# PLAN: prod env switches move to DB settings (SETTINGS-IN-DB-SORENTO)

Status: Review (Phase 3: security fixes H1/M1/M2/L2/L3 in; M3 owner ruling (a)).
Track: full lane (new permission + secrets at rest + external ingest auth source).

Owner ask (10 Oct 11:20): "for these prod steps, can we make it UI configurable and store in DB,
I am reluctant to set .env variables".

## Behaviour card

### Fields

Priority (owner correction, 10 Oct): all fields are full priority, the Respond.io webhook secret
included (with the webhook URL and the expected signature header shown in the UI).


| Setting (env name) | Added by | DB column on `system_settings` | Secret | UI home (existing component) |
| --- | --- | --- | --- | --- |
| `RESPOND_WEBHOOK_SECRET` | #1402 | `respond_webhook_secret_ciphertext` | yes | Settings > Integrations, new card "Respond.io webhook" (`Card` + `Form` + `Input type=password`, the SMTP password pattern) |
| `CHAT_TOPIC_LABELLER_ENABLED` | #1551 | `chat_topic_labeller_enabled` (nullable bool) | no | Settings > Integrations, card "Chat analytics" (`Switch`) |
| `CONNECTOR_SS_TO_PRODUCT_SECRET` | #1547 | `connector_ss_to_product_secret_ciphertext` | yes | Settings > Integrations, new card "Claude connector" |
| `CONNECTOR_PRODUCT_TO_SS_SECRET` | #1547 | `connector_product_to_ss_secret_ciphertext` | yes | same card |
| `CONNECTOR_CONNECTION_ID` | #1547 | `connector_connection_id` | no | same card (`Input`) |
| `CONNECTOR_SS_CALLBACK_URL` | #1547 | `connector_ss_callback_url` | no | same card (`Input`, must be https) |
| `ANALYTICS_RO_DATABASE_URL` (optional) | #1547 | `analytics_ro_database_url_ciphertext` | yes (DSN holds a password) | same card |

UI page: `sorento_crm_frontend/app/(protected)/user-management/settings/integrations/page.tsx`
(the tab that already holds the n8n webhook URLs). Only rows of existing form components, so no
Lavish mock.

The `analytics.connector.use` role grant is already UI: the permission is a `user_permissions`
row (`alembic/versions/acc_0001_connector.py`) and is granted on User management > Roles like
every other slug. Nothing to build.

### Precedence: DB wins, env is the fallback

Effective value = DB value when the DB holds one; otherwise the env value; otherwise unset.
The API reports `source: db | env | unset` per field so the screen says where the live value
comes from. Saving a value overrides env; clearing it falls back to env.

### Secrets

- Encrypted at rest with the existing helper `app/utils/field_encryption.py`
  (`encrypt_secret` / `decrypt_secret`). PR #1537 (AI-KEY-ENCRYPT) keeps that API and swaps the
  key for `FIELD_ENCRYPTION_KEY`, so this lane needs nothing from #1537 to merge first. When both
  have merged, the four new `*_ciphertext` columns (`connector_ss_to_product_secret_ciphertext`,
  `connector_product_to_ss_secret_ciphertext`, `analytics_ro_database_url_ciphertext`,
  `respond_webhook_secret_ciphertext`) MUST join #1537's re-encrypt script list, or they become
  unreadable at the key switch (fail closed: webhook and connector answer 503 until re-entered).
- Implication, stated plainly: ONE master key must stay in env. Today that is `JWT_SECRET`
  (the helper derives its key from it); after #1537 it is `FIELD_ENCRYPTION_KEY`. If that key
  changes without a re-encrypt, the saved secrets cannot be decrypted: they read as unset
  WITHOUT the env fallback (fail closed, security review M2: the env value may be the secret
  the saved one replaced), the screen says "saved value unreadable, enter it again", and nothing
  crashes. A failed `system_settings` read fails closed for secrets the same way.
- Once a value is saved in the CRM, remove its env var from the prod env: env stays only as
  the fallback for a value never saved, and a stale env secret becomes live again if the saved
  one is cleared.
- Never returned in full: the API returns `****` + last 4 (values of 8 chars or fewer show
  `****`). Inputs are update-only and never prefilled.
- Every change audited: one `system_settings` audit row per save naming the actor and the
  fields changed. Non-secret fields carry old and new value; secret fields carry only
  `set` / `cleared`, never the value.

### Permission

New slug `user_management.settings.secrets` ("Manage integration secrets"), registered and
granted to NO role: `admin` and `superadmin` already pass every slug by bypass
(`UserPermissionService.check_user_has_permission`, `app/services/user_service.py:1551`), so an
ungranted slug is exactly admin-only, and a role can still be granted it on User management >
Roles if the owner ever wants to. Gates both the GET and the PUT of the new
endpoint. Reason: `user_management.settings.edit` is also held by `warehouse_manager` and the
three integration (API key) roles, and the webhook secret decides who may write chat history.

### Owner ruling M3 (10 Oct): admins hold the connector secrets

Security review M3: with the two connector HMAC secrets editable on screen, an admin who sets
them can sign connector calls as any user holding `analytics.connector.use`, and the connector's
own audit row names that user, not the admin. Owner ruling (a): accepted. Admins may edit both
secrets in Settings. Mitigations that stand: the save writes a `RUNTIME_SETTINGS` audit row
naming the admin and `secrets_set`, and changing the secret breaks the live shared-service
connection, so the change is visible.

### Effect without restart

Every reader goes through one resolver (`app/services/runtime_settings.py`) that reads the row
per call (webhook per request on the request's session, labeller per nightly tick, connector per
request). Readers with no session (the connector preflight, which runs before any signature
check) share a 5 second snapshot, cleared on save, so a flood of junk connector headers costs no
database read each (security review H1); another worker process sees a save within 5 seconds. The read-only
analytics engine is cached per DSN, so a changed DSN builds a new engine on the next call.

### Endpoint

`GET /api/v1/user-management/settings/runtime` and `PUT` the same path. Body fields are the
setting names; a secret field omitted = keep, `""` = clear, a string = set.

### Three real examples

1. Respond.io webhook on prod. Integrations tab, card "Respond.io webhook": status "Not set:
   the webhook answers 503", URL `https://<this CRM host>/api/v1/public/respond/webhook` with a
   copy button, "Signature header: x-respond-signature (HMAC-SHA256 of the raw body); or custom
   header X-Respond-Webhook-Secret". Admin pastes the secret, Save: the field shows `****9f3c`,
   source "Saved in CRM". The next Respond.io delivery verifies, no restart.
2. Nightly labeller. "Chat analytics" card shows "Nightly topic labeller: Off (default)". Admin
   switches it on, Save. The 02:15 tick that night enqueues the job. Audit shows
   `chat_topic_labeller_enabled: null -> true` by the admin.
3. Connector already in env. Prod env holds the four `CONNECTOR_*` values: each row shows
   `****ab12` (or the id / URL in full for non-secrets) with source "Server env". Admin saves a
   new connection id: source becomes "Saved in CRM" and the connector uses it on the next call.
   Clearing it returns to the env value.

### Other env-only switches from the 9-10 Oct releases

- `RATE_LIMIT_FILE_LINK_MAX` / `RATE_LIMIT_FILE_LINK_WINDOW_SECONDS` (#1541): have safe
  defaults (60 per 60 s); nothing to set on prod. Not moved.
- Worker `analytics` queue (#1551): drained by the default worker and by `WORKER_ROLE=batch`;
  prod compose sets neither `WORKER_QUEUES` nor anything to change. Nothing to set.
- `FIELD_ENCRYPTION_KEY` (#1537, not merged): the master key; must stay env by design.
- `N8N_CRM_WEBHOOK_SECRET`, `EXTERNAL_API_KEY*`: older than 9 Oct, out of scope.

### Questions (answered (a) on all five, crew 10 Oct)

1. Precedence: DB wins, env fallback (rec) / env wins.
2. Permission: new admin-only slug (rec) / reuse `user_management.settings.edit`.
3. Encryption: existing helper now, #1537 key swap later (rec) / wait for #1537.
4. `ANALYTICS_RO_DATABASE_URL`: move it too, encrypted (rec) / leave env-only.
5. Labeller switch home: Integrations tab "Chat analytics" card (rec, one screen for all
   prod steps) / Chatbot tab "Switches" card.

## Contract (Phase 2)

`app/services/runtime_settings.py`

- `FIELDS`: the seven setting names, in screen order: `chat_topic_labeller_enabled`,
  `connector_ss_to_product_secret`, `connector_product_to_ss_secret`, `connector_connection_id`,
  `connector_ss_callback_url`, `analytics_ro_database_url`, `respond_webhook_secret`. Each name is
  also the `app.config.settings` attribute holding its env fallback.
- `SECRET_FIELDS`: `connector_ss_to_product_secret`, `connector_product_to_ss_secret`,
  `analytics_ro_database_url`, `respond_webhook_secret`. Stored in `<name>_ciphertext`.
  Non-secret fields are stored in a column of the same name.
- `get(name, db=None)`: effective value. DB value if non-null (secret: decrypted; an unreadable
  ciphertext counts as null), else `getattr(settings, name)`, else None (labeller: False).
  `db=None` opens and closes its own `SessionLocal()` (module attribute `SessionLocal`, so tests
  can monkeypatch it).
- `describe(db)`: `{name: {"source": "db"|"env"|"unset", "secret": bool, "value": <non-secret
  effective value or None for secrets>, "masked": <secret: "****" + last 4 of the effective
  value, "****" when 8 chars or fewer, None when unset>, "unreadable": bool}}`.
- `update(db, payload, actor_user_id)`: payload keys = field names (unknown keys refused by the
  schema). Omitted = keep. Secret / string: `None` or `""` (after strip) = clear, else set.
  Bool: `None` = clear (back to env), True/False = set. Commits. Writes ONE audit row
  (`log_audit(db, "system_settings", <row id>, "UPDATE", event="RUNTIME_SETTINGS",
  new_values={"changes": {<non-secret name>: {"old": .., "new": ..}}, "secrets_set": [names],
  "secrets_cleared": [names]}, user_id=actor)`) when anything changed, none otherwise. Secret
  names travel as list VALUES because the audit redactor drops keys ending in `_secret`.
  Creates the singleton row if none exists.

API: `GET` / `PUT /api/v1/user-management/settings/runtime`, both
`require_permission("user_management.settings.secrets")`. Response = `{"fields": describe(db),
"respond_webhook_path": "/api/v1/public/respond/webhook", "respond_signature_headers":
["x-respond-signature", "x-webhook-signature"], "respond_shared_secret_header":
"X-Respond-Webhook-Secret"}`. PUT returns the same shape. `connector_ss_callback_url` must start
with `https://` (422 otherwise).

Readers switched to `runtime_settings.get`: `respond_webhook.respond_message_webhook` (request
db), `task_scheduler._chat_topic_labeller_tick`, `analytics_connector.access.verify_token`,
`analytics_connector.consent._configured` / `verify_request` / `approve`,
`analytics_connector.datasets._ro_engine` (cached per DSN).
