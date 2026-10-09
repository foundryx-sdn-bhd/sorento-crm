# CARD - claude.ai analytics connector (CLAUDE-CONNECTOR)

Status: Plan. This card waits for the owner's answers to section 9. Track: L (new public
service, OAuth, migration, new permissions, prod infra). The lane PR carries
`crew-lane: CLAUDE-CONNECTOR`.

Code facts are cited from `origin/main` 4ee6f643, paths relative to the repo root. This repo is
public, so the card holds no real names, numbers or message text.

## 0. Owner rulings so far (9 Oct 2026)

| # | Ruling | Effect on the build |
| --- | --- | --- |
| R1 | Org is on Claude Team. Build a claude.ai custom connector (remote MCP) for AUTHORIZED staff only. | OAuth per person. A static org bearer token is rejected, because every org member would get access. |
| R2 | No masking. Raw message text and names are allowed. Secrets and credentials are never allowed. | The views carry raw text. OTP sign-in codes count as credentials and are masked (section 5.3). |
| R3 | Audit log: agreed. | Every tool call writes an `audit_logs` EVENT row (section 6). |
| R4 | "Separate service" means a separate container and process, not a new domain. | Served at `https://<crm-domain>/analytics/mcp`. Host nginx sends `/analytics/` to the new container. A subdomain is kept only as an option (section 8.4). |
| R5 | Reuse the existing READ-ONLY tools from `sorento_crm_mcp`'s catalog, starting from the chatbot's `CHATBOT_READ_ONLY_TOOLS`. Call the backend AS THE SIGNED-IN USER, never with the shared `EXTERNAL_API_KEY`. Exclude every write tool. Add the new chat tools on top. | Section 4. |

## 1. What exists today (measured)

| Fact | Where |
| --- | --- |
| The existing MCP server has no auth. It calls the backend with one shared `X-API-Key` and includes write tools. | `sorento_crm_mcp/sorento_crm_mcp/server.py:1803-1816`, `http_client.py:56` |
| Catalog tools are compiled from a code template. The generated function reads `client` and `settings` from `ctx.request_context.lifespan_context`. | `sorento_crm_mcp/sorento_crm_mcp/server.py:1713-1791` |
| The template also calls `attach_suggested_escalation(..., api_key=settings.external_api_key)` on an empty result. That is a second backend call using the shared key. | `server.py:1774`, `escalation_hint.py:151-175` |
| The chatbot's read-only allow-list holds 43 tools. CI already pins it to "catalog GET, or `read_only=True`". | `sorento_crm_backend/app/services/chatbot/lanes/business/fetch.py:1370`, `tests/chatbot/test_tool_pool_is_read_only.py` |
| Staff tokens are opaque `user_sessions` rows (`secrets.token_urlsafe(48)`, stored plaintext). They are resolved on every request, so a revoke takes effect at once. | `app/services/user_session_service.py:53-130`, `app/dependencies.py:143,269` |
| `user_sessions.auth_method` has a CHECK constraint over `password, phone_otp, portal_link, impersonation, dev_login`. | `app/models/user_session.py:57-60` |
| The company-scope resolver also reads `UserSession.token`. A session row therefore gets the user's own company grants automatically. | `app/services/company_scope_resolver.py:79-110` |
| `superadmin` and `admin` pass every `require_permission` check (the result is cached for 30 s). | `app/services/user_service.py:1483,1538-1551` |
| Permission slugs are added in `permission_registry.py` and granted to roles by a migration. | `app/rbac/permission_registry.py:886`, `alembic/versions/sales_s1_reports_module.py:73-91` |
| Audit EVENT rows are written by `audit_service.record(...)`. `audit_logs` is append-only. | `app/services/audit_service.py:399`, `app/models/audit.py:32-105` |
| No OAuth server, no read-only DB role and no chat views exist. The only views are `scm.*`. | `alembic/versions/274_scm_m0_views_reg.py` |
| Chat tables: `chat_histories` (raw `message`, `type` incoming/outgoing, `sent_at` naive UTC, `contact_id` = respond.io id), `respond_contacts`, `chatbot.turns` (envelope/response JSONB), `conversation_frames`. None are company-scoped. Access to them is gated by `system.chat_history.view`. | `app/models/chat_history.py:21-79`, `access.py:230-345`, `chatbot_turn.py`, `conversation_frame.py`, `permission_registry.py:388` |
| `chat_histories.message` holds raw OTP sign-in codes. They are masked only at read time, by `mask_otp_text`. | `app/services/otp_redaction.py:1-35` |
| Prod compose and host nginx are not in the repo; they are edited on the server. Blue/green runs through `scripts/blue_green_deploy.sh`, and images are built by `deploy.yml` `build-images` with the matrix `[frontend, backend, mcp]`. | `deploy.yml:1542-1644`, `blue_green_deploy.sh:9-11,79-87` |

## 2. Architecture

```
 claude.ai (Team org)                         staff browser
 egress 160.79.104.0/21                        (CRM login)
      |  Streamable HTTP + Bearer <conn token>     |  /analytics/oauth/authorize -> 302
      v                                             v
 +--------------------- host nginx  https://<crm-domain> ---------------------+
 | /analytics/mcp, /analytics/oauth/{register,token},  -> allow 160.79.104.0/21  |
 |   /.well-known/oauth-*/analytics...                    deny all; limit_req     |
 | /analytics/oauth/authorize                          -> open (user's browser)  |
 | /connect/claude  (consent page)                     -> frontend (as today)    |
 | /api/v1/*                                           -> backend  (as today)    |
 +----------------|-----------------------------------------------|-------------+
                  v                                               v
   sorento_analytics_mcp (NEW container, :8780)          frontend (Next.js)
   - OAuth endpoints (thin, forward to backend)          /connect/claude page:
   - auth gate before the MCP SDK (401 + WWW-Auth)       Allow / Deny as the
   - re-registers the allowed catalog tools              signed-in CRM user
   - new chat tools                                               |
   - holds NO DB credentials and NO EXTERNAL_API_KEY              |
                  |  Bearer <conn token> + X-Analytics-Connector-Key (internal)
                  v                                               v
   backend (FastAPI, existing)  <---------------------------------+
   - OAuth state: clients, pending requests/codes, grants (refresh tokens)
   - conn token = a user_sessions row, auth_method 'claude_connector', 1 h, non-rolling
   - guard for that auth_method: internal key, read-only methods, analytics.connector.use
   - normal RBAC + company scope of the signed-in user for every reused tool
   - chat datasets: second engine on role sorento_analytics_ro -> schema analytics_connector (views)
   - audit_logs EVENT per tool call; admin grant list + revoke
```

Why the backend holds the auth state while the container stays thin: users, sessions,
permissions, company scope and audit already live in the backend. The public-facing process then
holds nothing worth stealing (no DB password, no shared API key). It also means a single
`get_current_user` path decides access for both the reused tools and the new ones.

## 3. Auth flow (OAuth 2.1, per Anthropic's connector auth doc)

Discovery:
- An unauthenticated or invalid-token `POST /analytics/mcp` gets `401` with
  `WWW-Authenticate: Bearer resource_metadata="https://<crm-domain>/analytics/.well-known/oauth-protected-resource", scope="analytics.read"`.
  The 401 is sent BEFORE the MCP SDK runs, because a tool-level error would be wrapped in a 200 and
  sign-in would never start.
- Protected resource metadata (RFC 9728) is served at that URL and at
  `/.well-known/oauth-protected-resource/analytics/mcp`:
  `{resource: "https://<crm-domain>/analytics/mcp", authorization_servers: ["https://<crm-domain>/analytics"], scopes_supported: ["analytics.read"], bearer_methods_supported: ["header"]}`.
- Authorization server metadata (RFC 8414) is served at `/.well-known/oauth-authorization-server/analytics`.
  It is also served at `/analytics/.well-known/oauth-authorization-server` and at the OIDC path, for clients that append.
  It advertises: `issuer`, `authorization_endpoint`, `token_endpoint`, `registration_endpoint`,
  `response_types_supported: ["code"]`, `grant_types_supported: ["authorization_code","refresh_token"]`,
  `token_endpoint_auth_methods_supported: ["none"]`, `code_challenge_methods_supported: ["S256"]`,
  `scopes_supported: ["analytics.read"]`.

Steps:
1. **Registration (DCR, RFC 7591).** `POST /analytics/oauth/register` (JSON) creates a public client.
   Every `redirect_uris` entry must be in the allowlist (exactly `https://claude.ai/api/mcp/auth_callback`
   in v1, Q4). Anything else gets `400 invalid_redirect_uri`. A rogue registration can therefore only
   ever send codes to claude.ai.
   - CIMD is not offered in v1: it would make our server fetch an arbitrary URL during `/authorize`.
     Claude falls back to DCR when CIMD is not advertised (Anthropic doc, "DCR and CIMD details").
2. **Authorize (user's browser).** `GET /analytics/oauth/authorize?response_type=code&client_id&redirect_uri&code_challenge&code_challenge_method=S256&state&scope&resource`.
   - The container hands it to the backend, which checks: client known, redirect exact, S256 present,
     `resource` equals the MCP URL.
   - The backend stores a pending request (10 min TTL), and the container answers
     `302 /connect/claude?request=<id>`.
   - Bad client or redirect: an error page and NO redirect, so this cannot be used as an open redirect.
3. **Consent page** `/connect/claude` (frontend, protected layout, so it asks for sign-in if needed).
   - It shows "Claude (claude.ai) wants read-only access to CRM data as <your name>", the redirect
     host, and the request's expiry. Two buttons: Allow and Deny.
   - **Allow** calls `POST /api/v1/analytics-connector/authorize/{request_id}/approve` with the staff
     session. The backend:
     - refuses when the user lacks `analytics.connector.use` (Q1);
     - refuses when the request comes through impersonation (an admin cannot consent for someone else);
     - mints a code: 32 random bytes, stored as a SHA-256 hash, single use, 60 s life;
     - returns `redirect_uri?code=..&state=..&iss=..`, which the page navigates to.
   - **Deny** returns `error=access_denied`.
4. **Token.** `POST /analytics/oauth/token` (form-urlencoded) with `grant_type=authorization_code`.
   The backend checks the code hash, the PKCE verifier (S256), the client, the redirect and the
   resource. It then creates:
   - a **grant** row: user, client, created, last used, revoked, absolute expiry 90 days, and the
     refresh token hash plus the previous refresh hash;
   - an **access token**, which is a `user_sessions` row with `auth_method='claude_connector'`,
     `rolling=false` and `expires_at = now + 1 h`.
   Response: `{access_token, token_type: "Bearer", expires_in: 3600, refresh_token, scope}`.
5. **Refresh.** `grant_type=refresh_token` rotates the refresh token, revokes the previous
   access-token session and mints a new one.
   - A REUSED old refresh token revokes the whole grant (OAuth 2.1 theft detection).
   - Every failure answers `invalid_grant`.
   - Grant expiry or revocation leads to a fresh sign-in.
6. **Every MCP request.**
   - The container first calls `GET /api/v1/analytics-connector/me` with the bearer. Any non-200
     becomes HTTP 401 plus `WWW-Authenticate`, so Claude refreshes or re-signs in.
   - Tool calls then go to the backend with the same bearer plus the internal key.
   - The backend guard (in `_resolve_session_to_user`, only for `auth_method='claude_connector'`):
     - (a) `X-Analytics-Connector-Key` must match `ANALYTICS_CONNECTOR_INTERNAL_KEY` (constant-time).
       A stolen connector token therefore cannot be replayed against the public `/api/v1` from
       outside the allowlisted path.
     - (b) The method must be GET or HEAD, or a POST on a short allowlist: `/api/v1/lookup/resolve`
       and the connector's own `query` and `audit` routes. Any write fails with 403 even if a write
       tool slipped in.
     - (c) The user must hold `analytics.connector.use`, re-checked on every request (Q1). A user who
       loses it, or is deactivated, fails the next call.
     - (d) `X-Impersonate-User-Id` is ignored.
   - After (a) to (d), normal RBAC and company scope apply as for that user's own browser session.
7. **Revoke.**
   - Admin "Claude connector access" page: list grants, Revoke. Revoke is a deferred action
     (5 s reversible, per `ADR-PRODUCT-STANDARDS`). It sets `grants.revoked_at` and revokes the
     grant's current session, so the very next call gets 401.
   - The user's own Current sessions screen also lists and revokes the connector session (existing
     screen, `auth_method` shown).
   - Disconnecting in claude.ai does not reach our server, so the grant just stops being used.
     Admin revoke is the server-side kill switch.

Token storage: codes and refresh tokens are stored as SHA-256 hashes. The access token is a
`user_sessions.token`, stored plaintext like every staff session today (`user_session_service.py:70`).
That follows the existing pattern, and the 1 h life bounds the exposure. Hashing all session tokens
is a separate repair for every session, out of scope here.

## 4. Tools

### 4.1 Reused catalog tools (R5): the mechanism

- The new container installs `sorento_crm_mcp` as a library (same repo; the Docker build context
  is the repo root) and imports its `CATALOG`, `merged_catalog` and `_compile_tool`.
- `ANALYTICS_REUSED_TOOLS` is a static frozenset in the new package: the allow-list below.
  - At startup, every name is looked up in the merged catalog and compiled with the same
    `_compile_tool`, so parameters, aliases, UUID checks and presenters are identical to the n8n
    server.
  - Each tool is registered with `readOnlyHint: true, destructiveHint: false`. Anthropic requires
    both annotations on every tool.
- **Per-user backend auth (on-behalf-of, no exchange step).** The OAuth access token already IS a
  backend session for the signed-in user (section 3, step 4).
  - The container's lifespan `client` is a `UserBearerClient` with the same `get/post/request`
    interface as `CRMClient`.
  - It reads the caller's bearer from a contextvar that the auth gate sets per HTTP request. It
    sends `Authorization: Bearer <that token>` plus the internal key, and NEVER `X-API-Key`.
  - The container has no `EXTERNAL_API_KEY` in its environment at all.
- **Neutralised side call.** The compiled template's `_attach_suggested_escalation` (a second
  backend call with the shared key) is replaced in each compiled function's namespace by a
  pass-through. A test asserts that no outgoing request carries `X-API-Key`.
- **Drift guards (tests).**
  - Every allowed name exists in the catalog, is `GET` or `read_only=True`, and is not `external`
    or `internal`.
  - The allow-list is a subset of `CHATBOT_READ_ONLY_TOOLS`. A backend test pins this, the same way
    `test_tool_pool_is_read_only.py` already does.
  - The backend method guard (section 3, step 6b) is the second, independent line.

### 4.2 Reused tool list (40 of the chatbot's 43)

| Domain | Tools |
| --- | --- |
| Master data | `crm_master_products_list`, `crm_master_brands_list`, `crm_master_product_categories_list`, `crm_master_units_of_measure_list`, `crm_master_product_attachments_list`, `crm_certificates_list`, `crm_master_customers_list`, `crm_lookup_resolve` (POST, `read_only`) |
| Stock / incoming | `crm_inventory_stock_balance_list`, `crm_inventory_warehouses_list`, `crm_low_stock_report`, `crm_incoming_stock_by_product`, `crm_incoming_stock_shipments`, `crm_incoming_stock_list` |
| Sales / orders | `crm_order_management_orders_list`, `crm_order_management_orders_by_product_list`, `crm_outstanding_report`, `crm_sales_report`, `crm_report_ask`, `crm_top_selling_report`, `crm_sales_analysis` |
| Procurement | `crm_procurement_po_placed_list`, `crm_procurement_spo_allocations_last_receipt_list`, `crm_procurement_po_last_cost_list` |
| Marketing / resources | `crm_marketing_promotions_list`, `crm_marketing_promotion_products_list`, `crm_marketing_promotion_attachments_list`, `crm_resource_attachments_list`, `crm_resource_attachments_catalogue`, `crm_resource_attachments_current_stock_list` |
| Projects | `crm_projects_list`, `crm_project_detail`, `crm_project_quotations_list`, `crm_project_forecast` |
| Complaints / forms / SLA | `crm_complaints_list`, `crm_complaint_analytics`, `crm_forms_management_forms_list`, `crm_sla_conversation_tracking_dashboard`, `crm_sla_conversation_tracking_list`, `crm_sla_conversation_event_logs_list` |

Left out of the 43, with reasons (Q5):

| Tool | Why it is left out |
| --- | --- |
| `crm_portal_link_get` | `POST /api/v1/external/portal-tokens/` MINTS a customer portal login link, which is a credential (R2). |
| `user_guides_read` | `external=True`: it calls Outline with the server's Outline key, not the backend as the user. |
| `crm_system_tool_capabilities_summary` | Chatbot meta (which tools the bot has); no analytic value. |

All 6 write tools are excluded by construction: `crm_order_cancel`, `crm_purchase_request_approve`,
`crm_purchase_request_reject`, `crm_complaint_close`, `crm_it_support_ticket_create` and
`crm_ideation_turn` are not in the allow-list, and the backend refuses their methods anyway.

### 4.3 New chat tools (backend `/api/v1/analytics-connector/*`, gated per Q2)

| Tool | Input | Output |
| --- | --- | --- |
| `chat_list_datasets` | none | Each dataset: key, description, row count, first/last date. |
| `chat_describe_dataset` | `dataset` | Columns (key, type, description, filterable, groupable, measurable) and 3 sample rows. |
| `chat_query` | `dataset`, `columns[]`, `filters[]` (`{column, op, value}`; op in eq, ne, in, not_in, gt, gte, lt, lte, contains, is_null, not_null), `group_by[]` (a column, or a date bucket `sent_at:hour\|day\|week\|month\|dow\|hour_of_day`), `measures[]` (`{fn: count\|count_distinct\|sum\|avg\|min\|max, column}`), `period {from, to}`, `order_by[]`, `limit` (default 200, max 1000), `offset` | `{columns[], rows[][], row_count, truncated, next_offset}`, in a compact array form (not one dict per row). |
| `chat_search_messages` | `text` (contains, case-insensitive), `contact`, `customer`, `direction`, `date_from`, `date_to`, `limit` (max 200), `offset` | Message rows with contact and customer names, newest first. |
| `chat_get_conversation` | `contact` (id, respond.io id or name; several matches return a candidate list), `date_from`, `date_to`, `limit` (max 500), `offset` | One contact's thread, oldest first, with `incoming`/`outgoing` and bot turn status inline. |

- No raw SQL from the model in v1. `chat_query` is a whitelist builder in SQLAlchemy Core: columns
  and ops come from the dataset catalog, values are bound parameters, and the `contains` pattern
  escapes `%`, `_` and `\`.
- `chat_search_messages` and `chat_get_conversation` are thin wrappers over the same builder.
- CA-1 facts (`chat_ask_facts` and friends, PR #1546) become two more views and catalog entries
  once that lane merges. That is a follow-up commit, not this lane's blocker.

## 5. Data access

### 5.1 Read-only role and engine

- The migration creates schema `analytics_connector` holding VIEWS only (section 5.2).
- If role `sorento_analytics_ro` exists, it runs:
  `GRANT USAGE ON SCHEMA analytics_connector`, `GRANT SELECT ON ALL TABLES IN SCHEMA analytics_connector`,
  `ALTER ROLE ... SET statement_timeout = '10s'` and `SET default_transaction_read_only = on`.
  It never grants anything on `public`, `chatbot` or other schemas. The views run with the owner's
  rights, so the role reads base rows only through the views.
- The role itself is created by the owner on prod, once, because its password is a secret that
  must not be in a public repo:
  `CREATE ROLE sorento_analytics_ro LOGIN PASSWORD '<secret>' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;`
  It is then re-granted by re-running the migration's grant block (it ships as `scripts/analytics_ro_grants.sql`).
- The backend gets a second engine from `ANALYTICS_RO_DATABASE_URL`: `pool_size=2`, `max_overflow=3`,
  `options=-c statement_timeout=10000 -c default_transaction_read_only=on -c timezone=utc`.
  - Every connector query also runs `SET TRANSACTION READ ONLY` and `SET LOCAL statement_timeout`
    (defence in depth).
  - When the URL is unset (dev, CI), it falls back to the main engine with those same transaction
    settings. A test proves the role cannot write and cannot read `public.chat_histories` directly.
- Row cap: 1000 rows per call. Output cap: 120,000 characters, under claude.ai's ~150,000-character
  tool result limit. A capped answer says `truncated: true` and gives `next_offset`.

### 5.2 Views (schema `analytics_connector`; times as Malaysia wall clock, `Asia/Kuala_Lumpur`)

| View | Rows from | Columns (secrets and blobs excluded) |
| --- | --- | --- |
| `messages` | `chat_histories` LEFT JOIN `respond_contacts` (`respond_io_id`), primary customer, companies | `id, sent_at, direction, channel, contact_id, contact_name, customer_code, customer_name, companies, text` (OTP-masked), `reply_to_text` (OTP-masked), `chatbot_turn_id, delivery_status`. **Not included:** `phone_number` (Q3), `result`, `state_trace`. |
| `contacts` | `respond_contacts` + primary customer + companies + message counts | `id, respond_io_id, name, first_name, last_name, customer_code, customer_name, companies, created_at, chatbot_allowed, chatbot_stock_allowed, outbound_enabled, escalation_allowed, chatbot_memory_level, chatbot_profile` (tier, language, facts), `first_message_at, last_message_at, message_count`. **Not included:** `phone_number` (Q3), `portal_slug` (it forms a portal URL), `session_vars` (internal bot state). |
| `bot_turns` | `chatbot.turns` WHERE NOT `is_test` | `id, contact_id, contact_name, created_at, finished_at, latency_ms, status, stage, branch_kind, error, attempt, inbound_text` (envelope message text, OTP-masked), `reply_text` (`response.reply.text`). **Not included:** `envelope` (holds the full respond.io contact record), `trace` (parser raw output + session vars), `response` blob. |
| `episodes` | `conversation_frames` WHERE NOT `is_test` | `id, contact_id, contact_name, domain, intent, status, close_reason, started_at, last_activity_at, closed_at, turn_count, tools_used, summary, last_user_message` (OTP-masked), `last_assistant_summary`. |

### 5.3 OTP masking

`mask_otp_text`'s regex (`otp_redaction.py:30`) is ported to a SQL `regexp_replace` in the view
definitions. A test feeds the same fixtures through both and asserts identical output, so the two
cannot drift.

This is NOT a departure from R2. An OTP is a live credential, and R2 excludes credentials.

Relation to `DESIGN-chat-analytics-09oct.md` (PR #1545): that design's step-3 views (`ai_nocost`)
carry no message text. This connector uses its own schema and role, so the text-free `ai_nocost`
plan is unaffected.

### 5.4 Payload sizes (estimates)

Basis: 53,895 messages, 17 Apr to 6 Oct, about 3,000 a week. Measure on the prod copy at hand-test.

| Call | Size |
| --- | --- |
| One `messages` row in array form | ~250-400 chars (assuming ~120 chars of text on average) |
| `chat_search_messages`, 200 rows | ~50-80 KB |
| `chat_get_conversation`, one busy contact for a month (~300 msgs) | ~90-120 KB, which hits the cap and pages |
| `chat_query`, grouped (e.g. messages per week x direction, 26 weeks) | ~2 KB |
| Whole history raw | ~16-20 MB, i.e. ~150+ capped calls. So the tool descriptions steer Claude to `group_by` aggregates first and raw text for samples. |
| Reused tools | Same payloads as the n8n server's presenters (`view=render`); the 120 KB cap is applied after them. |

## 6. Audit

- One `audit_logs` EVENT per tool call (R3), written by `audit_service.record(...)` with:
  `event='analytics_connector.tool_call'`, `actor_type='user'`, `user_id` = the signed-in user,
  `auth_method='claude_connector'`, `session_id`, `source='mcp'`, and `new_values = {tool, args, status, ms, rows, chars}`.
- The new chat tools write their own row inside the backend request, so it cannot be skipped.
  Reused tools are logged by the container through `POST /api/v1/analytics-connector/audit`
  (internal-key only) right after the call returns.
- Grant lifecycle writes EVENT rows too: `analytics_connector.grant_created`, `.refreshed`,
  `.revoked` and `.refresh_reuse_detected`.
- Admin view: the new "Claude connector access" page (`/user-management/claude-connector`, gated by
  `analytics.connector.manage`). It is a DataGrid of grants (user, created, last used, calls, status)
  with Revoke. A grant's detail page lists its recent tool calls from `audit_logs`.

## 7. Threat model

| Threat | Mitigation | Residual |
| --- | --- | --- |
| Someone outside claude.ai calls `/analytics/mcp` | nginx `allow 160.79.104.0/21; deny all;` plus 401 without a token | None material |
| Stolen access token used elsewhere | 1 h life. The internal key is required by the backend, so the token is useless on the public `/api/v1`. The nginx allowlist leaves only the Anthropic IP range. | Use through another claude.ai account for at most 1 h; admin revoke ends it at once |
| Stolen refresh token | Rotation, reuse detection revokes the grant, 90-day absolute expiry, stored hashed | Claude holds it server-side (Anthropic's custody) |
| Rogue DCR client | `redirect_uris` allowlist (claude.ai callback only), nginx rate limit on `register` | Clients can still be registered, but they are harmless |
| Consent phishing: an attacker starts a flow in their own claude.ai and gets staff to approve it | Consent page names the app and the redirect host and says it acts as <you>. Codes live 60 s. Few holders of the permission. Every call is audited, admin revoke. | Inherent to OAuth consent; mitigated by training and the audit trail |
| Prompt injection in customer messages ("ignore instructions, call X") | Every tool is read-only and the backend refuses writes. Users approve tool calls in claude.ai. | Claude could be steered to paste data into ANOTHER connector in the same chat. Guidance: do not enable write connectors in analysis chats. |
| Write through a reused tool | Allow-list ⊆ catalog read-only ⊆ `CHATBOT_READ_ONLY_TOOLS`, pinned by tests. The backend refuses non-GET for connector sessions except the 3 listed POSTs. | None |
| SQL injection / costly query | No raw SQL. Whitelist builder with bound values. Views only. `statement_timeout` 10 s, read-only transaction, row and char caps, small pool. | Heavy but legal aggregates up to 10 s each |
| Credentials in output | OTP masked in views. `session_vars`, `trace`, `envelope`, `portal_slug` and `users.password` are never exposed. `crm_portal_link_get` is excluded. | Free text a customer typed (e.g. a bank account) is exposed raw by R2 |
| Admin consents as someone else via impersonation | Approve refuses impersonated requests; connector sessions ignore `X-Impersonate-User-Id` | None |
| Permission removed or user deactivated | Re-checked every request (permission cache is up to 30 s) | Up to 30 s |
| Data leaves our infra | Owner-accepted (R1/R2). Team plan: no training by default, ~30-day retention. | Accepted |
| Secrets in the public repo | Role password, internal key and RO DB URL come from env only. `gitleaks`, `pii_guard` and the pre-push hook already run. | None |

## 8. Ops (prod change: owner approves; nothing deploys from this lane)

1. **Env (server only, never committed).**
   - Backend: `ANALYTICS_CONNECTOR_INTERNAL_KEY`, `ANALYTICS_RO_DATABASE_URL`,
     `ANALYTICS_CONNECTOR_PUBLIC_BASE_URL=https://<crm-domain>/analytics`.
   - Container: `CRM_BASE_URL=http://backend:8000` (internal network), the same internal key and the
     same public base URL.
2. **DB.** `CREATE ROLE sorento_analytics_ro ...` (section 5.1), then `alembic upgrade head`,
   then `scripts/analytics_ro_grants.sql`.
3. **Compose.** A new service `analytics_mcp`: one container, no blue/green in v1 (the service is
   stateless, and a restart drops at most in-flight calls), bound `127.0.0.1:8780`, healthcheck
   `GET /analytics/healthz`. The image is built from the repo root.
   `deploy.yml` gains the new image in its build matrix and validate jobs; this lane adds the CI
   validate job, and the release wiring is a separate owner-approved step.
4. **Host nginx** (owner applies; the snippet ships as
   `documentation/plans/analytics/nginx-analytics-connector.conf`):
   - `limit_req_zone $binary_remote_addr zone=analytics:10m rate=10r/s;`
   - `location /analytics/` and the two exact `/.well-known/...analytics...` locations ->
     `127.0.0.1:8780`, with `allow 160.79.104.0/21; deny all; limit_req zone=analytics burst=40;`.
     Streamable HTTP needs `proxy_buffering off; proxy_read_timeout 300s;`.
   - `location = /analytics/oauth/authorize` -> same upstream, no IP allowlist, because the staff
     member's browser hits it.
   - Option (not recommended): a subdomain `analytics.<crm-domain>` with its own server block. It
     gives cleaner well-known paths but costs DNS and a certificate (R4 says same domain).
5. **claude.ai.** The Organization Owner adds a custom connector with URL
   `https://<crm-domain>/analytics/mcp` (Settings > Connectors). Each authorized staff member then
   clicks Connect and signs in with their CRM account.

## 9. Questions (with recommendations)

| # | Question | Options | Recommendation |
| --- | --- | --- | --- |
| Q1 | Do admins/superadmins get `analytics.connector.use` automatically, as they do for every other permission? | (a) No. Explicit grant only, admins included; the check skips the admin bypass for this one slug. (b) Yes, the normal bypass. | **(a)**: raw chat text of every customer is the most sensitive data in the CRM, and the ruling says "only authorized personnel". |
| Q2 | Should the chat tools also require the existing `system.chat_history.view`? | (a) Yes, connector permission AND `system.chat_history.view`. (b) The connector permission alone. | **(a)**: it is the same rule the CRM's Chat History screen applies, so nobody reads more in claude.ai than in the CRM. |
| Q3 | Phone numbers in the chat datasets? | (a) Excluded: contacts are identified by name and customer. (b) Included. | **(a)**: no analysis question needs them, and they are the direct identifier. Raw text, which may itself contain numbers, stays raw per R2. |
| Q4 | Which Claude apps may connect? | (a) claude.ai web, Desktop and mobile only (the one fixed callback). (b) Also Claude Code (loopback redirect on any port). | **(a)**: a loopback callback can be claimed by any local process; add Claude Code later if someone asks. |
| Q5 | The 3 tools left out of the chatbot set (portal link, user guides, tool capabilities): agree? | (a) Leave all three out. (b) Include `user_guides_read` (needs the Outline key in the new container). | **(a)**: the portal link is a credential; user guides would put a second server secret in the public-facing container. |

Defaults applied unless the owner says otherwise:
- access token 1 h, refresh 30 days rotating, grant 90 days absolute;
- 1000 rows or 120 KB per call;
- 10 s statement timeout;
- nginx 10 req/s per IP, burst 40.

## 10. Build slices (one lane, one PR)

| Slice | Content |
| --- | --- |
| S1 Backend | Migration (`acc_0001_...`): permissions `analytics.connector.use` / `.manage` (granted to no role; Q1), the `claude_connector` auth_method, tables `analytics_oauth_clients` / `analytics_oauth_requests` / `analytics_connector_grants`, schema + views, conditional grants. OAuth service + routes, session guard, RO engine, dataset catalog + query builder, chat routes, audit. Pytest written red-first. |
| S2 Container | `sorento_analytics_mcp/`: well-known docs, register/token/authorize forwarders, auth gate, `UserBearerClient`, reused tool registration with the escalation call neutralised, chat tools, output cap, audit post, Dockerfile, pytest, CI validate job. |
| S3 Frontend | `/connect/claude` consent page; `/user-management/claude-connector` grants list + detail + deferred Revoke; nav entry gated by `.manage`. Vitest. |
| S4 Verify | Local end-to-end: backend + container + FE in this sandbox, the full OAuth dance driven by a scripted client (DCR, PKCE, token, refresh, reuse detection, revoke) and MCP calls. Then reviewer + security-reviewer, and a hand-test script for a real claude.ai connection (needs a public URL, so it runs on prod after the owner's deploy, or through a tunnel from the owner's machine). |

Estimated size: ~2,500-3,500 changed lines including tests.
