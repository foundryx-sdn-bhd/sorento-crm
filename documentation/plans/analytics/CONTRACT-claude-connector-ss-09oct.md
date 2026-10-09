# CONTRACT - claude.ai connector: shared service <-> sorento (CLAUDE-CONNECTOR r3 / SS-CONNECTOR)

Status: sorento side BUILT against sections 0-8 (PR #1547, r3); SS side is SS-CONNECTOR (foundryx-shared-service #117).
Owner rulings 9 Oct 2026: the connector lives in foundryx-shared-service (SS); one connector per
product; identity = product login with a signed user assertion (the ideation embed pattern,
`sorento_crm_backend/app/services/ideation_embed_service.py:161`). All earlier owner rulings stand:
authorized personnel only (`analytics.connector.use`, no admin bypass), chat data also needs
`system.chat_history.view`, no phone numbers, raw text allowed, credentials never, every call audited.

## 0. Split

| SS owns | sorento owns |
| --- | --- |
| OAuth AS for claude.ai (DCR, PKCE, codes, refresh rotation + family revoke, grants, caps, purge), MCP Streamable HTTP endpoint + 401/WWW-Authenticate discovery, tool registration from the manifest, output cap (120 000 chars), fail-closed audit of every call, admin grant list + revoke, nginx allowlist 160.79.104.0/21 | Consent hop (login + Allow/Deny as the sorento user), signed user assertion, verification of SS-signed calls, per-call RBAC + company scope as that user, the read-only data API (chat datasets + reused read routes), read-only DB role + views, OTP masking, phone exclusion, its own audit row per call |

Removed from #1547 once SS covers them: sorento OAuth routes/tables, grants admin page, the
connector `user_sessions` access token, the internal key, `sorento_analytics_mcp/`.

## 1. Secrets (env only, never committed; one pair per product connection)

| Name | Holder | Signs |
| --- | --- | --- |
| `CONNECTOR_SS_TO_PRODUCT_SECRET` | both | SS -> sorento: consent requests (section 2) and API calls (section 3) |
| `CONNECTOR_PRODUCT_TO_SS_SECRET` | both | sorento -> SS: user assertions (section 2) |

Two secrets so a leak of one direction cannot forge the other. HS256 (the ideation pattern's
algorithm, `settings.jwt_algorithm`). `connection_id` names the product connection in every token.

## 2. Consent hop (browser)

1. claude.ai -> SS `/authorize` (SS validates client, PKCE, redirect). SS then redirects the
   browser to `https://<sorento>/connect/claude?req=<consent_request JWT>`.
   - `consent_request` claims: `typ="connector_consent_request"`, `iss="foundryx-ss"`,
     `aud="sorento-connector-consent"`, `connection_id`, `request_id` (SS's pending authorize id),
     `client_name` ("Claude"), `redirect_host` ("claude.ai"), `return_url` (SS callback, must equal
     sorento's configured `CONNECTOR_SS_CALLBACK_URL` exactly), `iat`, `exp` (<= 10 min), `jti`.
2. sorento page (protected layout: not signed in -> sign-in -> back with `req` intact) calls
   `POST /api/v1/connector/consent/inspect {req}` -> `{client_name, redirect_host, expires_at, user_name, allowed}`
   or an error (section 5). `allowed=false` -> no-permission view (Deny only).
3. Allow -> `POST /api/v1/connector/consent/approve {req}` with the staff session. sorento checks the
   signature/aud/exp/`return_url`, `jti` single use, the user holds `analytics.connector.use` (no admin
   bypass), the request is not under impersonation. It returns
   `{return_url, assertion}`; the page **form-POSTs** `request_id` + `assertion` to `return_url`
   (not a query string, so the assertion stays out of URLs and logs).
   - `assertion` claims: `typ="connector_assertion"`, `iss="sorento"`, `aud="foundryx-connector"`,
     `connection_id`, `request_id` (binds to that one consent), `sub` (sorento user id), `name`
     (display name, never the email), `email`, `iat`, `exp` (120 s), `jti`.
4. Deny -> `POST .../consent/deny {req}` -> `{return_url}`; page form-POSTs `request_id` + `error=access_denied`.
5. SS verifies the assertion, finishes the OAuth code flow, and stores the grant with
   `product_user_id = sub` and `consented_at`.

## 3. Tool calls (server to server)

Every SS -> sorento request carries `Authorization: Connector <call JWT>`:
- claims: `typ="connector_call"`, `iss="foundryx-ss"`, `aud="sorento-connector-api"`, `connection_id`,
  `sub` (the grant's sorento user id), `grant_id`, `consented_at`, `tool`, `req_hash`, `iat`, `exp` (60 s), `jti`.
- `req_hash` = base64url(SHA-256 of `METHOD\nPATH\nCANONICAL_QUERY\nSHA256(body)`), so a token cannot
  be replayed onto another route, query or body. sorento keeps `jti` for 120 s (redis) and refuses a repeat.
- sorento then: user exists, active, holds `analytics.connector.use` (no admin bypass), chat tools also
  `system.chat_history.view`; `consented_at` is after the user's last sign-out-everywhere / password
  change (else `reconsent_required`; sorento has no such timestamp today, verified: `app/models/user.py`
  holds no password_changed_at / session epoch, so sorento adds `users.connector_revoked_at`, set by
  `revoke_all_for_user`, `app/services/user_session_service.py:151`); method + path match the manifest's exact route list; then the
  route runs AS that user with normal RBAC + company scope. No `user_sessions` row, no impersonation.

Manifest: `GET /api/v1/connector/manifest` (signed, `sub` may be empty) returns
`{version, tools: [{name, title, description, input_schema, annotations: {readOnlyHint: true, destructiveHint: false},
http: {method, path_template, query_params: [..], path_params: [..]}}]}`.
- 34 reused read tools (card 4.2 after the security round) + 5 chat tools (`/api/v1/connector/datasets`,
  `/datasets/{key}`, `/query` (POST, read-only), `/messages/search`, `/conversation`).
- SS registers exactly these, percent-encodes path params (`safe=""`), never adds other routes; sorento's
  exact-route guard is the second line. A `version` change tells SS to re-read the manifest.
- Responses are the routes' own JSON; SS applies the 120 000-char cap and the `truncated` marker.

## 4. Audit

- SS: one row per MCP call (user, tool, args capped 4 KB, status, ms, rows, chars), fail-closed: no
  data to Claude if its audit write fails. Grant lifecycle rows. Admin list + revoke.
- sorento: one `audit_logs` EVENT `connector.tool_call` per API call it serves (actor = the user,
  `auth_method="connector"`, `source="connector"`, `new_values={tool, grant_id, status, ms}`), written
  inside the request so it cannot be skipped.

## 5. Error shape (sorento -> SS and to the consent page)

HTTP status + `{"error": {"code": "<code>", "message": "<plain words>"}}`.

| Status | code | SS action |
| --- | --- | --- |
| 401 | `invalid_signature`, `token_expired`, `replayed`, `wrong_audience` | treat as SS bug/config; retry once with a fresh token, then alert |
| 403 | `connector_access_removed`, `user_inactive`, `reconsent_required` | revoke the grant; Claude's next call gets 401 + re-sign-in |
| 403 | `chat_history_not_permitted`, `route_not_allowed`, `permission_required` | tool error to Claude; grant stays |
| 403 | `param_not_allowed` (section 9.2) | tool error to Claude; grant stays |
| 413 | `payload_too_large` (body over 1 MB, section 9.4) | tool error to Claude; grant stays |
| 404 / 422 | `not_found`, `validation_error` | tool error |
| 429 / 5xx | `rate_limited`, `upstream_error` | tool error, retry later |
Consent endpoints add `request_expired`, `request_used`, `impersonation_not_allowed`, `bad_request`.

## 6. Questions for SS-CONNECTOR (sorento's recommendation first)

1. Call signing: per-call JWT with `req_hash` (rec) vs a static bearer per connection + IP allowlist. Rec JWT: replay- and route-bound, no shared static credential.
2. Consent return: browser form-POST of the assertion to SS (rec) vs redirect with `?assertion=`. Rec POST: keeps the assertion out of URLs, referrers and logs.
3. Revocation on sign-out-everywhere: sorento refuses with `reconsent_required` via the `consented_at` claim (rec, no callback) vs sorento calling an SS revoke endpoint. Rec claim check: no new inbound surface on SS; SS revokes on the 403.
4. Manifest: sorento-hosted manifest (rec) vs SS hard-coding the tool list per product. Rec manifest: the product owns its tools and route allowlist in one place.
5. Who keeps the RO DB role + views: sorento (rec; the data lives in sorento's DB, SS never gets DB credentials).

## 7. Resolutions while writing sorento's red tests (9 Oct 2026, relayed on SS #117)

1. `req_hash` = base64url (no padding) of SHA-256 over `METHOD\nPATH\nCANONICAL_QUERY\nhex(sha256(body))`.
   PATH = the raw request path, still percent-encoded, without the query. CANONICAL_QUERY = the pairs in
   the order they are sent (NOT sorted; changed 9 Oct after security review, so duplicate keys cannot be
   reordered under one hash), each part encoded with `quote(safe="-_.~")`, joined by `&` (empty string when none).
   An empty body hashes as `sha256(b"")`.
2. `consented_at`, `iat`, `exp` are integer unix seconds. A call token whose `exp - iat` exceeds 60 s is
   refused; 5 s clock skew is tolerated on `iat`/`exp`.
3. 401 `invalid_signature` covers: bad signature, `req_hash` mismatch, wrong `typ`/`iss`/`connection_id`,
   a missing claim, an over-long token. 401 `wrong_audience` for a wrong `aud`. An RBAC refusal on a
   reused route is 403 `permission_required` (SS keeps the grant).
4. Allowed non-GET calls are exactly the manifest's POSTs: `/api/v1/connector/query` and
   `/api/v1/lookup/resolve` (`crm_lookup_resolve`, a read-only POST).
5. `path_template` is the real route path including any trailing slash (e.g. `/api/v1/master-data/brands/`);
   SS must call it verbatim, because a 307 redirect would change PATH and break `req_hash`.
6. Consent: `bad_request`, `request_expired`, `request_used` are 400; `connector_access_removed`,
   `impersonation_not_allowed` are 403. Inspect does not consume the `jti`; approve and deny do; a refused
   approve does not burn the request. inspect/approve/deny all return `request_id`; inspect's `expires_at`
   is ISO 8601 UTC.
7. `/api/v1/connector/*` data routes accept only `Authorization: Connector`; a staff Bearer session gets 401.
8. `revoke_all_for_user` stamps `users.connector_revoked_at` on every call, including "log out other devices".

## 8. Notes from sorento's build (9 Oct 2026)

1. Manifest `input_schema` lists every argument; for a POST tool, the arguments that are neither
   `path_params` nor `query_params` are the JSON body (`chat_query`; `crm_lookup_resolve` takes
   `set_key`, `raw`, `locale`). Reused tools take every argument as a string.
2. The reused tools drop the n8n chatbot's `contact_id` / `space_id` and `resolve_signed_urls`: a
   call already acts as the staff user, and presigned URLs are credentials.
3. Before a call is verified, refusals use the codes above. A call refused by sorento's own checks
   writes no audit row; a call that reached the route writes one with its status (`ok` or the HTTP
   status).
4. A sorento-side outage of the replay cache (redis) or an unconfigured connection answers 503
   `upstream_error`.

## 9. Review-round tightening (9 Oct 2026, relayed on SS #117)

1. CANONICAL_QUERY keeps the sent order (section 7.1 amended).
2. A call may only send the query keys listed in that tool's manifest `query_params`; any other key is
   403 `param_not_allowed`. `claims.tool` must name the tool whose manifest (method, path_template) is
   being called, else 403 `route_not_allowed` (the manifest route itself is called with `tool=""`).
3. The manifest no longer mentions `resolve_signed_urls`, `contact_id` or `space_id`.
4. A request body over 1 MB is refused 413 `payload_too_large` before parsing; the token is verified before the body is read.
5. sorento's audit row records the HTTP status the caller actually received.
