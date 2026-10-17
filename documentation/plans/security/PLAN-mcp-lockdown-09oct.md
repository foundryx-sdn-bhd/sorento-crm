# PLAN: MCP lockdown (bearer token on the CRM MCP server)

Status: Review (small fix track: ~200 changed lines, no migration; touches auth on the MCP
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
| n8n (checked by crew 9 Oct, read-only; dormant) | 3 ACTIVE workflows: `sub-get-results` (`Fss5aAaXthJSWpZCgKiKR`), `sub-get-results TEST` (`rysSPgUssLDf6xJc`, called by live `sorento-consume-main` via `sub-fetch-results` / `sub-resolve-and-gate`), `sub-get-results CS-BUILD` (`t4QvrtrPnTwRU6br`) | nodes `MCP Client` (AI Agent tool) + `MCP Client1` (output-structurer) in each = 6 nodes, `http://72.62.195.20:8765/mcp`, no auth | owner adds a credential (below); repo exports live in the separate `sorento_crm_n8n` repo |
| Deploy post-check | `.github/workflows/deploy.yml` "MCP via host nginx" | `GET http://127.0.0.1:8765/mcp`, no token | 401 now accepted as "nginx reached MCP" |
| Container healthcheck | prod compose `x-mcp-base` (server-only file) | TCP connect to :8765 | unaffected |
| Not callers | `app/api/v1/system/mcp_tools.py`, `project_mcp_bootstrap.py`, catalogue seeders | import `sorento_crm_mcp.catalog` in process, no HTTP | none |

The owner note "n8n now calls the turns endpoint, not MCP" holds for the live path. The 3
`sub-get-results` workflows are still ACTIVE (`documentation/plans/chatbot/n8n-changes.md` S6b
"Step 3 - unpublish" kept them for the S6a picker probes) but have not run in 7 days, so they are
a dormant backup that gets the token after the deploy (Rollout below).

## What ships

- `sorento_crm_mcp/sorento_crm_mcp/auth.py` `BearerAuthMiddleware`: `Authorization: Bearer
  <MCP_AUTH_TOKEN>` (scheme case-insensitive, `hmac.compare_digest`), else 401 with
  `WWW-Authenticate: Bearer`. `/healthz` is exempt and answers `ok`.
- `server.py` `create_http_app(settings)`: FastMCP's streamable HTTP app behind the gate.
  Refuses to build without a 32+ char token, so a missing env fails the container at start.
  `__main__.py` serves it with uvicorn (what `run_streamable_http_async` did).
- `CRM_MCP_ALLOWED_HOSTS` (default empty = off): set it and FastMCP's DNS-rebinding / Host check
  turns on (421 for other hosts). Off by default because the Host value nginx forwards on prod is
  not in the repo; with the token in place a rebinding page cannot authenticate anyway.
- Backend `MCP_AUTH_TOKEN` setting, sent by `MCPRuntimeClient`. Blank = no header.

## Committed secrets

`docker-compose.yml` is gitignored (`.gitignore:59`) and was removed from git in `b53c3584a`
(13 May 2026), so main carries no compose secret defaults and there is no tracked file to switch
to `${VAR:?}`. The scout's `docker-compose.yml:106,113` is the owner's untracked root copy.

The deleted file is still in this public repo's HISTORY, and every revision of it counts. Names
with non-empty secret defaults in `b53c3584a^` (security review, 9 Oct; values never copied):

- Database: `POSTGRES_PASSWORD` (also a literal in the `pgbackups` service), `DB_PASSWORD`
  (pgbouncer), `DATABASE_URL` / `DIRECT_URL` (password embedded; two of them point at the public
  IP on :5432).
- Auth: `JWT_SECRET` (signs dealer-kit / SLA-summary / ticket-draft link tokens, which can be
  forged with it), `NEXTAUTH_SECRET`.
- Storage: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` /
  `R2_SECRET_ACCESS_KEY`, `CLOUDFRONT_KEY_PAIR_ID`.
- Third parties: `OPENAI_API_KEY`, `RESPOND_API_KEY`, `RESPOND_SPACE_ID`, `N8N_WEBHOOK_URL`
  (path may carry a secret), `OUTLINE_API_TOKEN`.
- Integration: `EXTERNAL_API_KEY`, `EXTERNAL_API_KEY_ACT_AS_USER_ID`.

Older revisions carry further distinct values (9 for `DATABASE_URL`, 4 for `POSTGRES_PASSWORD`,
3 AWS key pairs, 2 each for `JWT_SECRET` and `EXTERNAL_API_KEY`): any still live is leaked too.

Owner actions: **rotate every name above** (a history rewrite does not un-leak them); confirm
Postgres :5432 is NOT reachable on the public IP; on the server compose replace each secret
`:-<value>` default with `:?`; confirm `sorento_crm_backend/.env.browse` (tracked) holds dev-only
values. This list is the in-repo record (`PLAN-pii-scrub.md:50` keeps the rest outside the repo).

## Rollout (enforced at release; owner rulings 9 Oct)

Crew checked live n8n run history (~21 days): the 3 `sub-get-results` workflows had no runs in
the last 7 days (only 2 manual TEST runs on 27 Sep), and the live path (`sorento-consume-main S7`
+ `sorento-main`) never calls them. So the token is enforced from the release; the owner keeps
the 3 workflows active as a backup and gives their MCP nodes the token after the deploy.

**Before dispatching the release (server, `/opt/sorento-crm2`):**
1. `openssl rand -hex 32` -> prod `.env`: `MCP_AUTH_TOKEN=<value>`.
2. Prod compose (server-only file): add `MCP_AUTH_TOKEN: ${MCP_AUTH_TOKEN:?set MCP_AUTH_TOKEN}`
   to `x-mcp-env` AND to the env block of every service built from the backend image
   (`backend_blue`, `backend_green`, `worker`, `worker_fast`): whichever process runs a chat
   turn calls MCP. The new MCP image refuses to start without it, so a missing value fails the
   deploy at the health wait and the old colour keeps serving.

**Release** (normal `deploy.yml` dispatch). Backend sends the token; MCP answers 401 to anything
else. The deploy post-check accepts 401 from `GET /mcp`.

**After the deploy, owner, n8n (the 6 MCP nodes):**
- Credentials -> New -> **Bearer Auth** (type `httpBearerAuth`), name `sorento-mcp-bearer`,
  field **Bearer Token** = the `MCP_AUTH_TOKEN` value. It sends header
  `Authorization: Bearer <token>`.
- Open each of `sub-get-results` (`Fss5aAaXthJSWpZCgKiKR`), `sub-get-results TEST`
  (`rysSPgUssLDf6xJc`), `sub-get-results CS-BUILD` (`t4QvrtrPnTwRU6br`); in BOTH `MCP Client`
  and `MCP Client1`: Authentication = **Bearer Auth**, Credential = `sorento-mcp-bearer`, and (once the nginx
  step below is done) URL = `https://fe-sorento.foundryx.my:8765/mcp`; save.
  (If a node version lists only Header Auth: type **Header Auth** / `httpHeaderAuth`, Name
  `Authorization`, Value `Bearer <token>`, with the space.)
- Until then those 3 workflows (and anything that calls them by hand: `sorento-consume-main`,
  its TEST / PROMO-PICKER copies, `sub-fetch-results`, `sub-resolve-and-gate`, the inactive
  `sorento-consume-main copy` `oo7LnsedPyKB9bWM`) get 401 from MCP if run.
- Optional: re-export the 3 into `sorento_crm_n8n/n8n-workflows-init/export/sub-get-results*/`.

**Rollback:** the token check has no switch; rolling back to an image tag before this PR
REMOVES the auth and re-opens the write tools on the public port. Pair any such rollback with the
nginx change below (drop or `deny all` the `:8765` block) until the gated image is back. The
deploy post-check now fails on anything but 401 from an unauthenticated `GET /mcp`, so an
ungated MCP behind nginx shows up red.

**Rotate:** the leaked values listed under "Committed secrets".

## nginx (prod host, not in the repo): owner step after the deploy

Owner ruling 9 Oct (Q1 b): n8n runs on a DIFFERENT server and the `sub-get-results` backups stay,
so the public `:8765` listener stays but only n8n's server may reach it, over TLS, so the bearer
token never crosses the internet in clear. Only the upstream file is written from the repo
(`blue_green_deploy.sh` step 4, `upstream sorento_mcp`); the `:8765` server block is hand-edited.

1. **n8n's IP.** On the CRM host: `dig +short automate-sorento.foundryx.my`. If that answers a
   proxy/CDN address rather than the n8n server itself, use the n8n server's egress IP instead
   (on the n8n server: `curl -s https://ifconfig.me`). Call it `N8N_IP`. It must be static.
2. **Cert: reuse the CRM's existing one, no new DNS.** A certificate validates the hostname, not
   the port, so the `fe-sorento.foundryx.my` cert already serves `https://fe-sorento.foundryx.my:8765`.
   Find its paths: `sudo nginx -T | grep -A2 "server_name fe-sorento.foundryx.my" ; sudo nginx -T | grep ssl_certificate`.
   (certbot renewals keep working; nginx picks up the renewed file on its next reload.)
3. **Replace the `:8765` server block** with (paths from step 2):

```nginx
server {
    listen 8765 ssl;
    server_name fe-sorento.foundryx.my;
    ssl_certificate     /etc/letsencrypt/live/fe-sorento.foundryx.my/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/fe-sorento.foundryx.my/privkey.pem;

    allow N8N_IP;      # from step 1
    allow 127.0.0.1;   # deploy.yml post-check
    deny  all;

    location /mcp {
        proxy_pass http://sorento_mcp;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_buffering off;        # streamable HTTP may answer as SSE
        proxy_read_timeout 120s;
    }
    location / { return 404; }
}
```

   Then `sudo nginx -t && sudo nginx -s reload`. Never add `$http_authorization` to a
   `log_format`.
4. **Check** (from the CRM host): `curl -s -o /dev/null -w "%{http_code}\n" https://fe-sorento.foundryx.my:8765/mcp`
   gives `403` (the host's public IP is not in the allow list) and
   `curl -sk -o /dev/null -w "%{http_code}\n" https://127.0.0.1:8765/mcp` gives `401`. From
   your laptop the port gives `403` or times out. The deploy post-check tries https then http,
   so it passes before and after this step.
5. **n8n**: in the 6 MCP nodes (Rollout above) change the URL to
   `https://fe-sorento.foundryx.my:8765/mcp` at the same time as adding the Bearer Auth
   credential.

## Tests

- MCP: `sorento_crm_mcp/tests/test_bearer_auth.py` (no token / wrong token / other scheme /
  suffix -> 401, right token -> tools list, `/healthz` open, no-token start refused, Host
  allow-list on and off).
- Backend: `sorento_crm_backend/tests/test_mcp_runtime_client_auth.py` (header sent / omitted).
