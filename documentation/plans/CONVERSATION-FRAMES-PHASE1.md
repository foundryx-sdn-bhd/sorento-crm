# Conversation Frames - Phase 1 (backend + FE) - Implementation & Verification Handoff

> Status: **Phase 1 shipped, smoke-tested on localhost.** Phase 2 (n8n rewire)
> deferred to user. This doc is the handoff for the frontend Playwright agent
> to verify the new MCP Tool Routing column.

## 1. Context

n8n chatbot orchestration (`automate-sorento.foundryx.my` →
`sorento-consume-main`) stored ephemeral conversational state in n8n
`conversation-variables`, which holds only ~1 turn of history. Symptoms:

- User picks `access_levels` (e.g. `sorento_dealer`); on next topic-switch the
  preference is lost and the bot re-asks.
- `reference_to_previous_context=true` returns no usable structured state
  because raw chat history is text-only.
- AI Agent system prompt (~2,375 tokens) duplicates routing tables already
  encoded in DB (`agent_mcp_tools × AgentTeam`), and asks gpt-4.1-mini to
  chain a second tool call for escalation - which fails unreliably on small
  models.

Phase 1 solves the **state architecture** half:

- **`conversation_frames`** table = per-topic structured snapshot
  (domain, intent, entities, access_levels_used, tools_used, summary, etc.).
- Frame lifecycle: open on first turn of new topic → update on continuation →
  close on topic/role switch or session end → summary embedded via existing
  `embedding_queue` pipeline as `source_type='conversation_frame'`.
- Vector search (`/memory/frames/search`) for episodic recall when n8n flags
  `memory_search_required`.
- New `/system/mcp-routing` endpoint joins existing `agent_mcp_tools × AgentTeam
  × Team` to return tool → (team, agent, tier) chain.
- MCP server auto-attaches `suggested_escalation` to empty tool results so the
  AI Agent never needs a second tool call.

Phase 2 (n8n) is documented but **not yet wired** - production behavior
unchanged until user imports the new n8n flow nodes from
`/Users/tehjayson/Desktop/n8n/n8n_code_snippets/`.

## 2. Files added / modified

### Backend (sorento_crm_backend)

| File | Action | Purpose |
|------|--------|---------|
| `app/models/conversation_frame.py` | **new** | `ConversationFrame` model. UUID id, JSONB cols, ARRAY(Text) for access_levels_used/tools_used, 3 indexes. |
| `app/models/__init__.py` | edit | Register `ConversationFrame` import + `__all__`. |
| `alembic/versions/201_conversation_frames.py` | **new** | Migration chained `200_transporters_table → 201_conversation_frames`. |
| `app/services/frame_service.py` | **new** | `FrameService.{open_frame, update_frame, close_frame, search_frames, get_open_frame, get_by_id}`. Auto-enqueues embedding event on close. |
| `app/services/embedding_worker.py` | edit | `_canonical_for_source` extended with `conversation_frame` handler (uses `summary` as body_text, visibility_scope=`contact`). |
| `app/schemas/external/memory.py` | **new** | Pydantic request/response for the 5 frame endpoints. |
| `app/api/v1/external/memory.py` | **new** | `POST /frames/{open,update,close,search}` + `GET /frames/current`. Uses `get_external_api_user`. |
| `app/api/v1/external/__init__.py` | edit | Mount memory router at `/api/v1/external/memory`. |
| `app/services/mcp_routing_service.py` | **new** | `resolve_tool_routing(tool_name)` joining `agent_mcp_tools × AccessAgent × AgentTeam × Team`. `build_suggested_escalation(...)` shapes the hint payload. |
| `app/api/v1/system/mcp_routing.py` | **new** | `GET /api/v1/system/mcp-routing?tool_name=...` |
| `app/api/v1/system/__init__.py` | edit | Mount routing router. |

### MCP server (sorento_crm_mcp)

| File | Action | Purpose |
|------|--------|---------|
| `sorento_crm_mcp/escalation_hint.py` | **new** | `_is_empty_response`, `attach_suggested_escalation`. 5-min TTL cache. Calls backend `/system/mcp-routing`. |
| `sorento_crm_mcp/server.py` | edit | `_compile_tool` wraps every tool response through `_attach_suggested_escalation` before return. |

### Frontend (sorento_crm_frontend)

| File | Action | Purpose |
|------|--------|---------|
| `app/(protected)/system-management/mcp-tools/services/mcpAdminService.ts` | edit | New `getMcpToolRouting(toolName)` + `McpToolRoutingResult` types. |
| `app/(protected)/system-management/mcp-tools/hooks/useMcpAdmin.ts` | edit | New `useMcpToolRouting(toolName, enabled)` query hook. |
| `app/(protected)/system-management/mcp-tools/components/RoutingPreviewCell.tsx` | **new** | Popover-backed cell showing primary team + full tier chain. |
| `app/(protected)/system-management/mcp-tools/components/McpToolsList.tsx` | edit | New "Routing" column wired to `RoutingPreviewCell`; `colSpan` bumped 5→6. |

`tsc --noEmit` clean.

## 3. CRUD smoke results (localhost:8000, EXTERNAL_API_KEY=test)

Test contact: `900000008`, space `364817`, channel `whatsapp`.

| # | Endpoint | Status |
|---|---|---|
| 1 | `POST /api/v1/external/memory/frames/open` | 201 |
| 2 | `GET  /api/v1/external/memory/frames/current` | 200, returns open frame |
| 3 | `POST /api/v1/external/memory/frames/update` | 200, patch landed |
| 4 | `POST /api/v1/external/memory/frames/close` | 200, `embedding_queued: true` |
| 5 | `GET  /api/v1/external/memory/frames/current` (post-close) | 200, `frame: null` |
| 6 | `POST /api/v1/external/memory/frames/search` | 200, similarity 0.64 hit on closed frame |
| 7 | `GET  /api/v1/system/mcp-routing?tool_name=crm_marketing_promotions_list` | 200 |

### Bug fixed during smoke

Search query failed with `operator does not exist: uuid = character varying`
because `embedding_chunks.source_id` is `String(128)` but
`conversation_frames.id` is `UUID`. Fix in `frame_service.py`:

```python
from sqlalchemy import String, cast
...
.join(
    ConversationFrame,
    cast(ConversationFrame.id, String) == EmbeddingChunk.source_id,
)
```

Verified post-fix.

## 4. Routing-data caveat (config, not code)

In the current DB seed, the agent `general_enquiries` is linked to **every**
team (`customer_service`, `marketing_product`, `marketing_promotion`,
`purchasing`, `warehouse`, plus test variants `*_c`), all at tier 1. Therefore
`/system/mcp-routing` returns 10 rows for `crm_marketing_promotions_list` and
the `primary` is essentially arbitrary (DB row order). For escalation routing
to be semantically correct, either:

1. Re-config in FE: split `general_enquiries` into per-domain agents
   (`promotion_bot`, `order_bot`, etc.), each linked to one team only. **No
   code change required.** Configuration lives at
   `User Management → Access Agents`.
2. Add a tool-name-prefix override (e.g. `crm_marketing_promotion_*` →
   `marketing_promotion`) inside `mcp_routing_service.py`. Not recommended;
   duplicates state.

Option 1 is the chosen path.

## 5. **Frontend Playwright verification protocol** (this is the handoff)

> **Convention reminder** (project memory `feedback_playwright_via_sidebar`):
> always click through the sidebar starting from `/`. Never deep-link to
> `/system-management/mcp-tools` or any other route.

### 5.1 Setup

Required env (set in your shell or `.env.test`):

```
PORTAL_E2E_BASE_URL=http://localhost:3000
MCP_ROUTING_E2E_EMAIL=person38@example.com
MCP_ROUTING_E2E_PASSWORD=<set in .env.local>
# Optional - pin to a known-good tool name; default falls back to first row.
MCP_ROUTING_E2E_TOOL_NAME=crm_marketing_promotions_list
```

Backend must be running on `localhost:8000` with the migration applied
(`alembic upgrade head`).

### 5.2 Manual smoke (Playwright MCP, browser-driven)

1. `browser_navigate` → `http://localhost:3000/`
2. Log in (email + password from env).
3. Open the sidebar `System Management → MCP Tools`. **Do not deep-link.**
4. `browser_snapshot` and confirm new column **Routing** appears between
   "Linked agents" and "Status".
5. Pick any row (or `MCP_ROUTING_E2E_TOOL_NAME` if set). Click the cell text
   under the **Routing** column (either the team-name link or "View").
6. `browser_snapshot` the popover. Verify:
 - Popover header shows `Escalation chain for <tool_name>` with the tool
     name in monospace.
 - At least one row inside, each formatted as `T<tier> <team_name> → <agent_name>`.
 - Footer: "First row = primary, sent as `suggested_escalation` on empty results."
7. Network-tab style check: confirm a `GET /api/system/mcp-routing?tool_name=...`
   fires when the popover opens. Use `browser_network_requests` after step 5.
   It should return a `RoutingResultOut` JSON with `entries: [...]` and
   `primary: {...} | null`.
8. Close popover; open a different row; confirm the popover refetches and
   shows that row's chain (no stale data).

### 5.3 Edge-case checks

- **Orphan tool**: pick a row whose "Linked agents" cell shows "Unassigned"
  (amber). Open Routing popover - should display "No teams linked.
  Configure via …".
- **No primary**: in DB, temporarily set `AccessAgent.is_active = false` for
  the agent that owns the chosen tool, refresh page, open popover - should
  show "No teams linked" (no crash).
- **Tooltip rerender**: open popover, close, reopen the SAME row - should
  show data immediately from React-Query cache (`staleTime: 5min`).

### 5.4 Suggested spec file path

If the Playwright agent prefers a checked-in spec rather than ad-hoc steps:
write to `sorento_crm_frontend/e2e/mcp-tool-routing.spec.ts`. Mirror the
sidebar-navigation pattern from `contact-access-types.spec.ts`.

### 5.5 Pass criteria

- New column visible & labeled "Routing".
- Popover opens on click, fetches `/api/system/mcp-routing`, renders chain.
- Orphan tools degrade gracefully (no error).
- Backend route returns 200 with the documented shape.

## 6. n8n Phase 2 (handoff to chatbot author, not Playwright agent)

Code/prompt snippets are in
`/Users/tehjayson/Desktop/n8n/n8n_code_snippets/`:

- `README.md` - wiring instructions, HTTP-node configs.
- `reformulator-system-prompt.md` - new reformulator prompt with 3 added booleans
  (`memory_search_required`, `role_switch_detected`, `frame_should_close`).
- `ai-agent-system-prompt.md` - slimmed AI Agent prompt (~50% reduction);
  new user-template binding `<active_frame>`, `<semantic_parse>`, `<recalled_frame>`.
- `compile-current-state.js` - rewritten Code node deciding
  `frameAction ∈ {open, update, close_and_open, rehydrate}`.
- `output_exchange.js` - unchanged; included for completeness.

Architecture diagram lives in the user's chat transcript and at
`/Users/tehjayson/Desktop/n8n/PLAN_conversation_frames.md`.

## 7. Manual curl reference

```bash
API=http://localhost:8000
KEY=test
CONTACT=900000008
SPACE=364817

# Open
curl -sS -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -X POST "$API/api/v1/external/memory/frames/open" \
  -d '{"contact_id":"'$CONTACT'","space_id":"'$SPACE'","channel":"whatsapp",
       "domain":"promotion","intent":"lookup","access_levels_used":["sorento_dealer"]}'

# Current
curl -sS -H "X-API-Key: $KEY" \
  "$API/api/v1/external/memory/frames/current?contact_id=$CONTACT&space_id=$SPACE&channel=whatsapp"

# Update  (replace FRAME_ID with id from /current)
curl -sS -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -X POST "$API/api/v1/external/memory/frames/update" \
  -d '{"frame_id":"<FRAME_ID>","patch":{"tools_used":["crm_marketing_promotions_list"]}}'

# Close
curl -sS -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -X POST "$API/api/v1/external/memory/frames/close" \
  -d '{"frame_id":"<FRAME_ID>","reason":"topic_switch","summary":"<summary text>"}'

# Search
curl -sS -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -X POST "$API/api/v1/external/memory/frames/search" \
  -d '{"contact_id":"'$CONTACT'","space_id":"'$SPACE'","query_text":"promotion for dealer","k":3}'

# Routing
curl -sS -H "X-API-Key: $KEY" \
  "$API/api/v1/system/mcp-routing?tool_name=crm_marketing_promotions_list"
```

## 8. Out of scope for this phase

- Profile tier (`contact_attributes`): durable per-contact preferences. Future
  ticket.
- Frame purge cron (e.g. drop `closed_at < now() - interval '90 days'`). Defer
  to ops.
- 1-second `Schedule Trigger` → webhook migration. Orthogonal.
- Group-chat frames (per-contact assumption baked in).
