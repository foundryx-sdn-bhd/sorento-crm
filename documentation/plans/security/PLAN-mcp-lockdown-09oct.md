# PLAN: MCP lockdown (bearer token on the CRM MCP server)

Status: Build (small fix track: ~250 changed lines, no migration; touches auth on the MCP
surface, so reviewer + security-reviewer both run). Lane MCP-LOCKDOWN, owner approval 9 Oct 2026.

## Problem

`sorento_crm_mcp` served Streamable HTTP with no auth (`FastMCP(...)` in `server.py`, no
token check, `enable_dns_rebinding_protection=False`), bound `0.0.0.0:8765`, and host nginx
publishes it as `http://72.62.195.20:8765/mcp` (`scripts/blue_green_deploy.sh:5`, deploy check
`.github/workflows/deploy.yml` "MCP via host nginx"). Every call reaches the backend as one
integration user through the shared `EXTERNAL_API_KEY` (`http_client.py`, `CRMClient`), and the
catalogue includes write tools (`record_actions.py`: `crm_order_cancel`,
`crm_purchase_request_approve` / `_reject`, `crm_complaint_close`; `ideation.py`:
`crm_ideation_turn`). Anyone who can reach the port can call them.

## Caller inventory

| Caller | Where | How it reaches MCP | Change |
| --- | --- | --- | --- |
| CRM backend, single client class | `sorento_crm_backend/app/services/ai_assistant_service.py` `MCPRuntimeClient` | POST `settings.ai_assistant_mcp_url` (`app/config.py`, default `http://localhost:8765/mcp`) | sends `Authorization: Bearer <settings.mcp_auth_token>` (this PR) |
| - in-app AI assistant | `ai_assistant_service.py` (5 constructions: tool list, agent loop, prefetch, write confirm) | via `MCPRuntimeClient` | covered by the class |
| - chatbot business lane fetch / probes | `app/services/chatbot/lanes/business/services.py` (`mcp_call`) | via `MCPRuntimeClient` | covered |
| - chatbot ideate lane | `app/services/chatbot/lanes/ideate.py` | via `MCPRuntimeClient` | covered |
| n8n (live, checked by crew 9 Oct, read-only) | 3 ACTIVE workflows: `sub-get-results` (`Fss5aAaXthJSWpZCgKiKR`), `sub-get-results TEST` (`rysSPgUssLDf6xJc`, called by live `sorento-consume-main` via `sub-fetch-results` / `sub-resolve-and-gate`), `sub-get-results CS-BUILD` (`t4QvrtrPnTwRU6br`) | nodes `MCP Client` (AI Agent tool) + `MCP Client1` (output-structurer) in each = 6 nodes, `http://72.62.195.20:8765/mcp`, no auth | owner adds a credential (below); repo exports live in the separate `sorento_crm_n8n` repo |
| Deploy post-check | `.github/workflows/deploy.yml` "MCP via host nginx" | `GET http://127.0.0.1:8765/mcp`, no token | 401 now accepted as "nginx reached MCP" |
| Container healthcheck | prod compose `x-mcp-base` (server-only file) | TCP connect to :8765 | unaffected |
| Not callers | `app/api/v1/system/mcp_tools.py`, `project_mcp_bootstrap.py`, catalogue seeders | import `sorento_crm_mcp.catalog` in process, no HTTP | none |

So the owner note "n8n now calls the turns endpoint, not MCP" holds for the main chat path, but
`sub-get-results` is still published and called (the S6a picker probes, see
`documentation/plans/chatbot/n8n-changes.md` S6b "Step 3 - unpublish"), so n8n must get the token
before enforcement.

## What ships

- `sorento_crm_mcp/sorento_crm_mcp/auth.py` `BearerAuthMiddleware`: `Authorization: Bearer
  <MCP_AUTH_TOKEN>` (scheme case-insensitive, `hmac.compare_digest`), else 401 with
  `WWW-Authenticate: Bearer`. `/healthz` is exempt and answers `ok`.
- `server.py` `create_http_app(settings)`: FastMCP's streamable HTTP app behind the gate.
  Refuses to build without a 32+ char token, so a missing env fails the container at start.
  `__main__.py` serves it with uvicorn (what `run_streamable_http_async` did).
- `MCP_AUTH_ENFORCE` (default `true`). `false` = rollout phase 1: unauthenticated calls pass
  and each is logged `mcp auth: missing|invalid bearer token from <ip> ...` (token never logged).
- `CRM_MCP_ALLOWED_HOSTS` (default empty = off): set it and FastMCP's DNS-rebinding / Host check
  turns on (421 for other hosts). Off by default because the Host value nginx forwards on prod is
  not in the repo; with the token in place a rebinding page cannot authenticate anyway.
- Backend `MCP_AUTH_TOKEN` setting, sent by `MCPRuntimeClient`. Blank = no header.

## Committed secrets

The scout's `docker-compose.yml:106,113` is the owner's untracked root copy: `docker-compose.yml`
is gitignored (`.gitignore:59`) and was removed from git in `b53c3584a` (13 May 2026), so main
carries no compose secret defaults and there is nothing to change to `${VAR:?}` in a tracked file.
But the deleted file is still in this public repo's HISTORY (`git show b53c3584a^:docker-compose.yml`)
with real defaults for, at least: `EXTERNAL_API_KEY`, `EXTERNAL_API_KEY_ACT_AS_USER_ID`,
`OUTLINE_API_TOKEN`, `POSTGRES_USER` / `POSTGRES_PASSWORD`, and others in that file. **They must
be rotated** (owner action, alongside the PII-SCRUB credential list); a history rewrite does not
un-leak them. On the server copy, replace `:-<value>` defaults with `:?` for every secret.

## Rollout (callers send the token before the server enforces)

1. Generate: `openssl rand -hex 32`. Prod `.env` (server, `/opt/sorento-crm2`): add
   `MCP_AUTH_TOKEN=<value>` and `MCP_AUTH_ENFORCE=false`. Prod compose (server-only file): add
   `MCP_AUTH_TOKEN: ${MCP_AUTH_TOKEN:?set MCP_AUTH_TOKEN}` and
   `MCP_AUTH_ENFORCE: ${MCP_AUTH_ENFORCE:-true}` to `x-mcp-env`, and
   `MCP_AUTH_TOKEN: ${MCP_AUTH_TOKEN:?set MCP_AUTH_TOKEN}` to the backend AND worker env blocks
   (whichever process runs a chatbot turn needs it). Do this BEFORE the release that carries this PR: the new MCP
   image will not start without the token.
2. Release (normal `deploy.yml` dispatch). Backend now sends the token; MCP accepts everything
   and logs the n8n calls as `missing`.
3. n8n (prod edit, owner go): create ONE credential, type **Bearer Auth** (`httpBearerAuth`),
   name `sorento-mcp-bearer`, Bearer Token = the value. In each of the 6 nodes (`MCP Client`,
   `MCP Client1` in the 3 workflows above) set Authentication = **Bearer Auth** and pick it; the
   node then sends `Authorization: Bearer <token>`. Node JSON:
   `"parameters": {..., "authentication": "bearerAuth"}`,
   `"credentials": {"httpBearerAuth": {"id": "<id>", "name": "sorento-mcp-bearer"}}`.
   If a node version offers only Header Auth: type **Header Auth** (`httpHeaderAuth`), Name
   `Authorization`, Value `Bearer <token>`, `"authentication": "headerAuth"`. Re-export the 3
   workflows into `sorento_crm_n8n/n8n-workflows-init/export/sub-get-results*/workflow.json`.
4. Verify: `docker compose logs mcp_<colour> | grep "mcp auth:"` shows no new lines through a
   day of chat traffic (or after running one probe turn).
5. Enforce: set `MCP_AUTH_ENFORCE=true` (or delete the line) and recreate the MCP container
   (`docker compose up -d --no-deps mcp_<active colour>`; no image change needed).
   Rollback = set it back to `false` and recreate.

## nginx (prod host, not in the repo)

Only the upstream file is written from the repo (`blue_green_deploy.sh` step 4); the `:8765`
server block is hand-edited on the host. Once n8n is the only external caller:
- Best: if n8n runs on the same host / docker network, point the 6 nodes at the internal URL and
  drop the public `listen 8765` block entirely (keep `127.0.0.1:8765` if the deploy post-check
  should still pass, or change that check to `127.0.0.1:<colour port>/healthz`).
- Otherwise restrict it: in the `:8765` server block add `allow <n8n egress IP>; deny all;`,
  and move it behind TLS (`listen 8765 ssl` with the existing cert) so the token is not sent in
  clear text over the internet.

## Tests

- MCP: `sorento_crm_mcp/tests/test_bearer_auth.py` (no token / wrong token / other scheme /
  suffix -> 401, right token -> tools list, `/healthz` open, no-token start refused, Host
  allow-list, enforce-off logs and passes).
- Backend: `sorento_crm_backend/tests/test_mcp_runtime_client_auth.py` (header sent / omitted).
