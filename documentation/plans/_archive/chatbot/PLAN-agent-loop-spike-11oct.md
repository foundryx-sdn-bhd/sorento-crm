# PLAN: AGENT-LOOP-SPIKE (tool-calling agent engine, test turns only)

Status: superseded (11 Oct 2026): owner paused the free tool-calling agent (n8n history); lane re-briefed as a design lane, see ARCH-scalable-chat-11oct.html. No engine code was written. Track: M spike, lead pattern. No migration, no auth/RBAC change
(scope is REUSED, not changed). UAC: `agent-loop-spike-acceptance-criteria.md`.

## Why

CHATBOT-EVAL baseline (PR #1556): hidden 78.0% (strict 34.6%). The parser reads rewordings fine;
failures sit after understanding: record matching, context carry-over, rigid tool contracts.
Owner wants the architecture decided with numbers.

## Owner rulings (11 Oct 2026)

- Q1: same test endpoint (`POST /api/v1/external/chat/turn`), lane-only setting, TEST turns only.
- Q2: tools return ready-formatted fact blocks; the model writes short framing and places blocks by
  reference `{{block:N}}`; the engine inserts blocks verbatim. Model sees a block SUMMARY, not the body.
- Q3: test turns write nothing (portal link returns the link shape, no token row).
- Q4: the agent handles ALL topics, no hand-off to the old engine. Weakest topics first.
- Q5: switch bar >= 95% hidden pass across all topics, p95 <= 8 s, cost <= ~$0.010/turn (confirmed).
- Data: dev runs against `sorento_eval_base` (6 Oct prod snapshot, migrated to chat_topics_0002_audit)
  directly, read-only: no migrations or writes by the lane; missing columns go to crew as the exact error.

## Shape

Package `sorento_crm_backend/app/services/chatbot/agent/` (inside the chatbot package, so the
import boundary `tests/chatbot/test_import_boundary.py` is unchanged).

1. Hook: `engine._run_turn` (`app/services/chatbot/engine.py`, the `_run_stages(...)` call) runs
   `agent.run_agent_stages(...)` instead when `settings.chatbot_agent_engine` is true AND
   `dry_run` is true. Everything before it is unchanged and still applies: company + brand scope
   (`_scoped_factory`), AI-allowed gate, dedup, row insert, and the failure catch-all that closes
   the row. Setting: `CHATBOT_AGENT_ENGINE` env, default False, in `app/config.py` Settings.
2. Loop (`agent/loop.py`): OpenAI chat with tools via `chatbot/llm_call.chat` (rate limit and
   deadline handling reused). Provider/model/key from the chatbot's configured
   `ai_assistant_configs` row, resolved the same way the parser resolves it. Max 4 model rounds; the
   last round is called with no tools so it must answer. Parallel tool calls in one round allowed.
3. Tools (`agent/tools.py`): the model sees
   - `resolve(query, kinds)`: the reference resolver (`lanes/business/services.py` `_resolve_entity`
     -> `system/references.py` `resolve_reference_post`), returns candidates with codes and ids.
   - one function per MCP tool in `fetch.CHATBOT_READ_ONLY_TOOLS`, schema from the MCP catalogue via
     `ai_assistant_service._mcp_schema_to_openai_tool` (strips `contact_id`/`space_id`). For a
     customer-scoped contact, `customer_ids`/`customer_query` are removed from the schema of
     `fetch.CUSTOMER_SCOPED_TOOLS`.
   - `so_lookup(numbers)`: `so_status.lookup` / `lookup_po_refs` with the contact's `linked`.
   - `portal_link(submission_type)`: dry run returns the link shape without calling the token route.
   Execution: `services._mcp_call(db)` (read-only allow-list, brand scope, brand guard). Before
   the call, the engine injects `contact_id` + `space_id` and applies the customer-scope check
   (same rule as `fetch.py:1251-1270`, `ScopeViolation`). Per-domain reveal keys
   (`policy_rows` `reveal_key`, read via `head/access.check_access`) and the stock-allowed gate
   (`engine._stock_check_denied`) refuse a tool the contact may not use; the refusal is a tool
   result the model must relay, never a crash.
4. Blocks (`agent/blocks.py`): each tool result is rendered to a fact block with the existing
   renderer (`fetch.output_structurer`) where one exists, else a generic `Label: value` renderer.
   The model gets `block N: <tool>, <line count>, <short summary>` plus any candidate list it needs
   to ask a question. Final text `{{block:N}}` refs are replaced verbatim; unknown refs dropped and
   the unused blocks appended; empty final text = blocks in order.
5. Memory (`agent/memory.py`): `session_patch = {"agent": {"messages": [...last 6], "records":
   [...codes/ids/kinds found], "offered": [...numbered options]}}`. Read back from the harness's
   `previous_conversation_state` (dry run only). Nothing persisted (test turns write nothing).
6. Trace: `understood` stage facts carry summed `prompt_tokens`, `cached_tokens`,
   `completion_tokens`, `rounds`; `looked_up` stage `raw.envelopes` = one `{tool, args, error,
   lane_text}` per call, so `scripts/chatbot_eval/trace.parse_trace` reads tokens and tool errors.
   `branch_kind = "agent"`. Row closed with `_close_turn` (status done).
7. Never stuck: provider rate limit/timeout -> `llm_call.RATE_LIMITED_REPLY`; any other loop error
   -> a plain "please refer to your salesman" style reply (existing copy constant), row closed done
   with the error on the trace.
8. Style: blocks already follow WhatsApp style; the system prompt states the rules (one fact per
   line `Label: value`, no ` · ` joins, short, reply in the customer's language).
   No regex/keyword rules on customer text anywhere.

## Contract (pinned for tester and coder)

All in `app/services/chatbot/agent/`; dependencies injected so tests need no live LLM or MCP.

- `contact.py`: `@dataclass ContactCtx(contact_id: str, space_id: str | None, staff: bool,
  scope_customer_ids: list[str] | None, linked: tuple, reveal_keys: frozenset[str],
  stock_allowed: bool)`; `scope_customer_ids` is None for staff/unscoped contacts.
  `load_contact(db, contact_respond_id) -> ContactCtx` builds it from the existing seams.
- `blocks.py`: `class BlockStore`: `add(tool: str, text: str, summary: str) -> int` (1-based),
  `summaries() -> list[str]` (`"block N: <tool>, <k> lines, <summary>"`), `assemble(final_text: str) -> str`.
- `tools.py`: `@dataclass ToolOutcome(block: int | None, summary: str, error: str | None)`;
  `class ToolBox(contact: ContactCtx, *, blocks: BlockStore, mcp_call, resolve_call, so_lookup,
  catalog: dict[str, dict], dry_run: bool)`; `schemas() -> list[dict]` (OpenAI function tools);
  `execute(name: str, args: dict) -> ToolOutcome` (never raises; refusals and errors come back as
  `error`); `events: list[dict]` (`{tool, args, error, lane_text}` per call, for the trace).
  `catalog` = MCP `{name: {description, inputSchema}}` (from `MCPRuntimeClient.list_tools_with_schema`).
- `memory.py`: `@dataclass Memory(messages: list[dict], records: list[dict], offered: list[dict])`;
  `Memory.from_state(state: dict | None) -> Memory`; `to_patch() -> dict` (`{"agent": {...}}`);
  `remember_turn(user_text, reply_text, records, offered)` keeps the last 6 messages.
- `loop.py`: `@dataclass LoopResult(text: str, tokens: dict, rounds: int, error: str | None)`;
  `run_loop(*, chat, toolbox: ToolBox, system: str, messages: list[dict], max_rounds: int = 4)
  -> LoopResult`; `chat(messages, tools) -> ChatResult` (`app.services.llm_provider.ChatResult`);
  `tokens = {"prompt", "cached", "output"}` summed; `text` is ALREADY assembled (blocks inserted).
  Rate limit / timeout (`llm_call.RateLimited`) -> `text = RATE_LIMITED_REPLY`, `error` set.
- `__init__.py`: `run_agent_stages(envelope, *, session_factory, turn_trace, turn_id,
  contact_respond_id, contact_scope, dry_run, actions, stage, chat=None, mcp_call=None,
  resolve_call=None, so_lookup=None, catalog=None) -> TurnResult`; None = the real seam.
- Hook: `engine._agent_engine_on(dry_run: bool) -> bool` = `settings.chatbot_agent_engine and dry_run`.

## Slices

- S1 engine: hook + flag, loop, blocks, memory, trace, never-stuck, scope-checked tool execution,
  resolve + all read MCP tools + so_lookup + portal_link (dry shape). Red tests first (tester).
- S2 dev-set iteration on the 107 DEV cases, weakest topics first, prompt and tool-description
  tuning only (no keyword rules). Live calls <= 300/day.
- S3 report: dev score vs baseline per topic, cost/turn, p50/p95, recommendation; crew runs hidden gate.

## Not doing

No live-path change, no writes (orders, escalations, tickets, file links, portal tokens), no media,
no new tables, no UI, no n8n, no prompt registry entry (prompt lives in code for the spike).

## Triggers deferred

Prompt in the prompt registry only if the agent is adopted. Escalation hand-off only if adopted.
