# PLAN: lock the MCP endpoint + default-deny company scope

Status: building (full track, size M: auth change on the MCP server, no migration, no UI change)
UAC: `sec-mcp-auth-acceptance-criteria.md`
Lane: SEC-MCP-AUTH (owner D7, 16 Oct). Supersedes the unmerged PR #1548 (MCP-LOCKDOWN), whose
bearer gate and env name `MCP_AUTH_TOKEN` this lane ports unchanged.

## Findings (verified at origin/main 3d837263)

### Who calls the MCP server

| Caller | How | File:line |
| --- | --- | --- |
| CRM backend: in-app AI assistant | `MCPRuntimeClient` at `settings.ai_assistant_mcp_url` | `sorento_crm_backend/app/services/ai_assistant_service.py:342`, `:758`, `:2064`, `:2334`, `:2451`, `:2978` |
| CRM backend: chatbot business lane (fetch + probes) | same client | `sorento_crm_backend/app/services/chatbot/lanes/business/services.py:272` |
| CRM backend: chatbot ideate lane | same client | `sorento_crm_backend/app/services/chatbot/lanes/ideate.py:127` |
| n8n `MCP Client` / `MCP Client1` nodes (sub-get-results, dormant since the chatbot moved into the CRM) | `http://<CRM_ORIGIN>:8765/mcp` | `documentation/runbooks/prod-edge-lockdown.md:81-84` |
| n8n heartbeat workflow (HA monitor) | httpRequest `tools/call crm_master_products_list` | `documentation/runbooks/n8n-heartbeat-workflow.json` (node "MCP call") |
| Release post-check | `curl http://127.0.0.1:8765/mcp` | `.github/workflows/deploy.yml:2116-2123`, `.github/workflows/rollback.yml:175-181` |

`MCPRuntimeClient` is the only MCP client class in the backend, so one header change there covers
every in-repo caller.

Exposure: host nginx listens on 8765 and proxies to the live colour's container on loopback
(`scripts/blue_green_deploy.sh:5`, `:12-13`, `:444`). The provider firewall limits 8765 to the
n8n host and the box itself (`documentation/runbooks/prod-edge-lockdown.md:192-236`). The prod
compose file is server-only (not in the repo).

### Auth today

None. `sorento_crm_mcp/sorento_crm_mcp/__main__.py:27` serves FastMCP's app directly; nothing reads
an inbound header. The MCP server then calls the CRM with the shared `EXTERNAL_API_KEY`
(`sorento_crm_mcp/sorento_crm_mcp/http_client.py:56`), so any caller that reaches 8765 acts with
that key, write tools included (`record_actions.py`, `ideation.py`).

### Cross-company reads

The backend scope service, `sorento_crm_backend/app/services/company_scope_resolver.py:244-252`
(`_resolve_api_key_scope`): an X-API-Key call with no `contact_id` + `space_id` resolves to `None`,
which means every company. With both present it resolves through `resolve_contact_company_scope`
(`:221-241`), which is fail-closed (an unknown contact gets an empty set, so 0 rows).

So every MCP tool that declares `contact_id` + `space_id` (38 of 53 in `merged_catalog(CATALOG)`,
pinned by `sorento_crm_mcp/tests/test_company_scope_params.py:20-61`) returns all companies' rows
when the caller leaves the pair blank or out. The staff AI assistant blanks them on purpose
(`ai_assistant_service.py:2455`, `:2705-2706`, `:3070-3071`). `crm_lookup_resolve` documents the
same fallback for n8n (`sorento_crm_mcp/sorento_crm_mcp/server.py:115-122`).

The product-attachment fan-out inside `crm_master_products_list` (`server.py:1064-1098`) calls a
second scoped route without the pair, so its child rows are unscoped even on a scoped call.

Tools without the pair (the 15 others) read global data (`GLOBAL_TOOLS` in the test above:
forms, SLA, complaint analytics, tool capabilities), or are writes or Outline reads. Two are
company-owned with no contact param: `crm_project_forecast` (a whole-company aggregate by design,
`test_company_scope_params.py:44`) and `crm_purchase_request_by_number`. They are listed as a
follow-up, not changed here: there is no contact to scope them by.

## Design

### 1. Service auth (port of #1548)

- `sorento_crm_mcp/sorento_crm_mcp/auth.py`: ASGI gate. `Authorization: Bearer <MCP_AUTH_TOKEN>`,
  `hmac.compare_digest`; anything else gets 401 with `WWW-Authenticate: Bearer`. `/healthz` is open
  so a container healthcheck needs no secret. Websockets are refused. The token is never logged.
- `MCP_AUTH_MODE`: `enforce` (default) or `permit`. In `permit`, a request with NO Authorization
  header passes and logs one warning line (method, path, no header values); a wrong token is
  still 401. It exists for one release so callers can move over without an outage, then the owner
  removes it.
- Fail closed: `create_http_app` refuses to start without a token of 32+ characters, in both
  modes.
- Backend: `MCPRuntimeClient` sends `Authorization: Bearer <settings.mcp_auth_token>` when set
  (`MCP_AUTH_TOKEN`, same value).

### 2. Default-deny scope

One check, `require_contact_scope(spec, query, body, request_headers)` in
`sorento_crm_mcp/sorento_crm_mcp/scope.py`, called at the top of `_execute_tool_request_with_body`,
the one function every catalog tool's call goes through (GET and POST). A tool is scoped when its
spec declares both `contact_id` and `space_id`, read from the catalog itself rather than a second
list. A scoped call with either value blank gets
`{"error": "scope_required", "data": [], "total": 0, ...}` and no backend request is made. The
contact to company resolution stays where it is (`resolve_contact_company_scope`); MCP only
refuses to ask without an identity.

The staff AI assistant opts out explicitly: `MCPRuntimeClient(..., staff=True)` sends
`X-MCP-Scope: staff`, set only at the AI assistant's own call sites. The chatbot lanes never set
it. This keeps today's assistant results (no behaviour change, owner question posted on the PR);
scoping the assistant to the staff user's active company is the follow-up. The header is honoured
only on an authenticated request; any caller holding the token already holds the key that reads
every company, so the opt-out grants nothing new.

The products fan-out forwards the caller's `contact_id` + `space_id` to its child call.

### 3. Callers that must send the token

- backend_blue, backend_green, worker, worker_fast: `MCP_AUTH_TOKEN` (env).
- mcp_blue, mcp_green: `MCP_AUTH_TOKEN`, and `MCP_AUTH_MODE=permit` for the first release.
- n8n: the heartbeat workflow and the 6 dormant `MCP Client` nodes, Bearer credential (owner step).
- Release post-check: sends a deliberately wrong token and expects 401, which proves the gate is
  there in both modes (an ungated image answers 406).

## Rollout (owner runs every prod step)

1. Before dispatching the release: `openssl rand -hex 32`; put it in the prod `.env` as
   `MCP_AUTH_TOKEN`. In the server compose add `MCP_AUTH_TOKEN: ${MCP_AUTH_TOKEN:?}` to the MCP env
   (`x-mcp-env`) and to backend_blue, backend_green, worker, worker_fast; add `MCP_AUTH_MODE: permit`
   to the MCP env only. If the MCP container healthcheck calls `/mcp`, point it at `/healthz`.
2. Dispatch the release. During the colour swap the old backend sends no token; `permit` lets it
   through, so the chatbot never sees a 401.
3. n8n: credential Bearer Auth `sorento-mcp-bearer` with the token; attach it to the heartbeat
   "MCP call" node and to the 6 `MCP Client` nodes (re-import the heartbeat JSON from this PR, it
   also switches to a global tool, see UAC).
4. After a day with no `unauthenticated MCP request allowed (permit mode)` line in the MCP logs
   (`docker compose logs mcp_<colour> | grep 'permit mode'`): remove `MCP_AUTH_MODE` from the
   compose and recreate the MCP container (env-only, no release). Enforce is now on.

Rollback: an image from before this PR removes the gate. The firewall rule on 8765 stays the
outer guard either way.

## Follow-ups (not this lane)

- Scope the staff AI assistant to the staff user's active company, then drop `X-MCP-Scope`.
- `crm_project_forecast`, `crm_purchase_request_by_number`: company-owned, no contact param.
- Per-caller tokens (n8n vs CRM) if a caller ever needs revoking alone.
