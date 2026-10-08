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

## Key source: FIELD_ENCRYPTION_KEY (crew review, 8 Oct)

`app/utils/field_encryption.py` used to derive its one Fernet key from SHA-256 of `JWT_SECRET`.
`JWT_SECRET` is on the owner's rotate list (leaked in public git history,
PII-SCRUB-CREDENTIALS.md item 3), so rotating it would have made every stored secret
unreadable. The helper already encrypted, before this lane:

- `respond_workspaces.api_key_ciphertext` (`app/models/respond_workspace.py:29`; written
  `app/services/respond_workspace_service.py:129,185`, read `:21`,
  `app/services/respond_template_service.py:191`, `app/services/integration_service.py:167`)
- `respond_workspaces.ideation_intake_api_key_ciphertext` (`app/models/respond_workspace.py:44`;
  written `respond_workspace_service.py:133,191`, read `:274`)
- `respond_workspaces.ideation_embed_signing_secret_ciphertext` (`app/models/respond_workspace.py:52`;
  written `respond_workspace_service.py:142,201`, read `:286`)
- `respond_workspaces.chatbot_retry_ingress_key_ciphertext` (`app/models/respond_workspace.py:67`;
  written `respond_workspace_service.py:76,146`, read `:298`)
- `integrations.credentials_json` (`app/models/integration.py:53`; written
  `app/services/integration_admin_service.py:221`, read `:233`)

Now: `FIELD_ENCRYPTION_KEY` is a comma-separated list of Fernet keys, current first, read by a
`MultiFernet`. The first key encrypts; every listed key decrypts; the JWT_SECRET-derived key is
appended as the last decrypt-only fallback so tokens written before the switch keep working
during rollout. Unset `FIELD_ENCRYPTION_KEY` (dev, CI) falls back to the JWT-derived key alone,
the old behaviour. A malformed entry raises naming its position, never its value.

Generate a key:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

## Design

One `TypeDecorator` (`EncryptedText`, impl `Text`, same helper) on the three columns. Bind encrypts, result
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

`scripts/reencrypt_field_secrets.py` covers every column above plus the three
`ai_assistant_configs` key columns. Per value: `current` (the current key opens it, left alone),
`old_key` (an older listed key or the JWT-derived key opens it, re-encrypted under the current
key), `plaintext` (encrypted for the `ai_assistant_configs` columns, which held plain text
before; reported and left alone elsewhere), `unreadable` (no configured key opens it; reported,
left alone, must be re-entered), `empty`. Dry run by default, counts only, never a value.
`--apply` refuses unless `FIELD_ENCRYPTION_KEY` is set, writes in one transaction, and each
UPDATE is a compare-and-set on the value it read. Re-running is a no-op.

## Rollout (deploy order)

Prod compose (`/opt/sorento-crm2/docker-compose.yml`) is edited by hand on the server.

1. **Env var.** Generate a key (above) and add `FIELD_ENCRYPTION_KEY=<key>` to the **backend and
   worker** services. Keep `JWT_SECRET` unchanged for now: it still opens every existing token.
2. **Deploy** this code. New saves are encrypted under `FIELD_ENCRYPTION_KEY`; existing tokens
   read through the JWT-derived fallback; legacy plaintext AI keys read as is.
3. **Dry run:** `cd sorento_crm_backend && python scripts/reencrypt_field_secrets.py` inside the
   backend container. Expect `old_key` counts on the Respond.io / integration columns and
   `plaintext` on the AI key columns; `unreadable` should be 0.
4. **Apply:** `python scripts/reencrypt_field_secrets.py --apply`, then the dry run again: every
   column should read `old_key=0`, AI columns `plaintext=0`. Prod run is the owner's call.
5. **Now JWT_SECRET rotation is safe** for stored secrets: nothing at rest depends on it. (Rotating
   it still signs everyone's short-lived link tokens anew, which is the point.)

Rotating `FIELD_ENCRYPTION_KEY` itself later: prepend the new key (`new,old`), deploy, run
`--apply` (moves `old_key` values onto `new`), then drop `old`.

## Tests (red first)

- `tests/test_ai_key_encrypt.py`: the AI key columns hold a token, not the key; the attribute
  reads the key; legacy plaintext reads as is; an undecryptable token reads as None; the config
  API never returns a key; format detection; the 422 log.
- `tests/test_field_encryption_key.py`: new writes use `FIELD_ENCRYPTION_KEY` and survive a
  `JWT_SECRET` rotation; first key encrypts, older listed keys decrypt; JWT-derived tokens
  decrypt during rollout; unset var keeps the old behaviour; malformed key fails loudly; the
  backfill re-encrypts every column onto the current key, after which rotating `JWT_SECRET`
  loses nothing; `--apply` refuses without the var; no value is ever printed.

## Follow-ups (not in this lane)

- **Remove the plaintext pass-through** in `EncryptedText.process_result_value` once
  `--apply` has run in prod (trigger: owner confirms the prod run printed `plaintext=0` on the AI columns).
- **Drop the JWT-derived decrypt fallback** once every env has run `--apply` with
  `unreadable=0` and `old_key=0` (trigger: owner confirms on prod).
- **Same plaintext pattern elsewhere:** `system_settings.smtp_password` (`app/models/user.py:409`);
  token columns that should be stored hashed: `user_sessions.token`
  (`app/models/user_session.py:36`), `portal_tokens.token` (`app/models/portal.py:30`), share-link
  tokens (`app/models/procurement.py:1212,1229`, `app/models/supplier_notice.py:126`,
  `app/models/projects.py:1184`).
