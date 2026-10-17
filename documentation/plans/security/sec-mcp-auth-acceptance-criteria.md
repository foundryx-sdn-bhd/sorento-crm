# UAC: SEC-MCP-AUTH

Plan: `PLAN-sec-mcp-auth-16oct.md`

## Auth

- **AUTH-1** A request to `/mcp` with no Authorization header, a wrong token, another scheme or
  the token plus a suffix gets HTTP 401 with `WWW-Authenticate: Bearer` (enforce mode).
- **AUTH-2** The right token gets the normal MCP answer (`tools/list` lists the catalog).
- **AUTH-3** `/healthz` answers 200 `ok` with no token.
- **AUTH-4** The server refuses to start when `MCP_AUTH_TOKEN` is unset, blank or under 32
  characters; the error text never contains the token.
- **AUTH-5** `permit` mode: no header passes and logs a warning without header values; a wrong
  token is still 401.
- **AUTH-6** The comparison is constant-time (`hmac.compare_digest`) and the token never appears
  in a log line.
- **AUTH-7** The backend `MCPRuntimeClient` sends `Authorization: Bearer <MCP_AUTH_TOKEN>` when the
  setting is set, and no Authorization header when it is blank.

## Scope

- **SCOPE-1** A scoped tool (declares `contact_id` + `space_id`) called with either blank or
  missing returns `error: scope_required` with `data: []`, and makes NO backend request.
- **SCOPE-2** A scoped tool with both set forwards them and returns the backend's answer unchanged
  (same result as today).
- **SCOPE-3** A global tool (no contact params) is unaffected.
- **SCOPE-4** `X-MCP-Scope: staff` on the request lets an unscoped call through as today; only the
  AI assistant's call sites set it, never the chatbot lanes.
- **SCOPE-5** The products-with-attachments fan-out forwards `contact_id` + `space_id` to the child
  call.
- **SCOPE-6** The check reads the catalog: every scoped tool in `merged_catalog(CATALOG)` is
  covered without a second list.

## Ops

- **OPS-1** The release post-check expects 401 for a wrong token (both modes) and fails on 406.
- **OPS-2** The heartbeat workflow sends the Bearer credential and calls a global tool, so default
  deny does not trip it.
