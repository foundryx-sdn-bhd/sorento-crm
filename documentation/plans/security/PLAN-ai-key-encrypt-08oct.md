# PLAN: encrypt AI assistant provider keys at rest (AI-KEY-ENCRYPT)

Status: built, in review (small fix track: no migration, no auth/RBAC change, no new ingest surface; security review ran because it touches stored secrets)

## Finding

`ai_assistant_configs.api_key_ciphertext`, `anthropic_api_key_ciphertext` and
`gemini_api_key_ciphertext` (`app/models/ai_assistant.py:35,38,41`) hold the provider keys in
plain text despite the name. The only writer, `AIAssistantConfigService.update`
(`app/services/ai_assistant_service.py:457-463`), assigns the stripped key directly, and every
reader uses the attribute as the key:

- `app/services/ideation_turn_service.py:87`
- `app/services/ideation_extractor.py:335`
- `app/services/ticket_intake_service.py:262`
- `app/services/scm/explainer_service.py:165`
- `app/services/scm/market_research_service.py:67-68`
- `app/services/llm_provider.py:159-176` (`resolve_api_key`, used by the chat assistant,
  extraction, media, translation and the chatbot lanes)
- `app/services/ai_assistant_service.py:475-477` (masking for the settings API)

## Existing mechanism

`app/utils/field_encryption.py` (`encrypt_secret` / `decrypt_secret`, Fernet keyed by
SHA-256 of `JWT_SECRET`) already encrypts `respond_workspaces.api_key_ciphertext` and the other
`respond_workspaces.*_ciphertext` columns (`app/services/respond_workspace_service.py:76,129-145,185-201`).
This lane reuses it, so there is **no new env var**: `JWT_SECRET` is already required and already
must stay stable for the Respond.io keys to decrypt.

## Design

One `TypeDecorator` (`EncryptedText`, impl `Text`) on the three columns. Bind encrypts, result
decrypts. Every existing reader keeps getting the plain key from the attribute, so no call site
changes and a future reader cannot forget to decrypt. No schema change, no migration.

Legacy rows: a value is treated as ciphertext only when it has the Fernet token format (urlsafe
base64 that decodes to a version byte `0x80` plus the fixed-size header/HMAC). Anything else is
legacy plaintext and is returned as is. A Fernet-shaped value that fails to decrypt (wrong
`JWT_SECRET`) is logged without the value and read as "no key configured".

API: `GET/PUT /api/v1/system/ai-assistant/config` returns only `*_masked` (`****` + last 4);
the schema (`app/schemas/ai_assistant.py:27-41`) has no raw-key field. Unchanged.

## Other leaks of the same keys (found in security review, fixed here)

- The global 422 handler (`app/main.py`, `validation_exception_handler`) logged `exc.errors()`,
  whose `input` is the whole request body on a missing-field error, so a malformed settings PUT
  wrote the plain key to the app log. `input` is now dropped before logging.
- `api_call_logs` redaction (`app/services/api_call_log_service.py`) matched exact key names
  only, so `anthropic_api_key` / `gemini_api_key` in the settings PUT body were stored. It now
  also matches the suffixes `_api_key`, `_password`, `_secret`, `_token`.

## Backfill

`scripts/encrypt_ai_assistant_keys.py`: dry run by default (counts per column of plaintext /
encrypted / empty, never values), `--apply` encrypts plaintext values in one transaction.
Idempotent; each UPDATE is a compare-and-set on the value it read, so a key saved meanwhile is never overwritten.

## Rollout

1. Confirm `JWT_SECRET` in the target env is the one it will keep (it already is for Respond.io).
2. Deploy the code. Reads tolerate plaintext, new saves write ciphertext.
3. Run `python scripts/encrypt_ai_assistant_keys.py` (dry run), then `--apply`. Prod run is the owner's call.

## Tests (red first)

`tests/test_ai_key_encrypt.py`: saved key column holds a Fernet token, not the key; the ORM
attribute reads back the key; legacy plaintext rows read as is; the config API never returns
the key; backfill dry run changes nothing, `--apply` encrypts, re-run is a no-op.

## Follow-ups (not in this lane)

- **Remove the plaintext pass-through** in `EncryptedText.process_result_value` once
  `--apply` has run in prod (trigger: owner confirms the prod run printed `plaintext=0`).
- **Owner decision: dedicated `FIELD_ENCRYPTION_KEY`.** Every `field_encryption` user
  (respond_workspaces, integrations.credentials_json, now ai_assistant_configs) derives its key
  from `JWT_SECRET`: rotating it silently unsets every stored secret, and anyone holding it
  (including the FE if `NEXTAUTH_SECRET` shares the value) can decrypt them. A `MultiFernet`
  ([new key, JWT-derived]) migration fixes both, in its own lane.
- **Same plaintext pattern elsewhere:** `system_settings.smtp_password` (`app/models/user.py:409`);
  token columns that should be stored hashed: `user_sessions.token`
  (`app/models/user_session.py:36`), `portal_tokens.token` (`app/models/portal.py:30`), share-link
  tokens (`app/models/procurement.py:1212,1229`, `app/models/supplier_notice.py:126`,
  `app/models/projects.py:1184`).
