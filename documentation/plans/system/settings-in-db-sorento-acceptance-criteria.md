# UAC: SETTINGS-IN-DB-SORENTO

- AC-1 GET `/api/v1/user-management/settings/runtime` returns every field with `value`
  (non-secret) or `masked` (secret, `****` + last 4) and `source` (`db` | `env` | `unset`).
  The full secret never appears in any response.
- AC-2 PUT sets / clears / keeps (omitted) each field. A secret is stored encrypted: the raw
  column value is not the plaintext and `decrypt_secret` returns it.
- AC-3 Precedence: DB value wins over env; cleared DB value falls back to env; neither = unset.
- AC-4 GET and PUT need `user_management.settings.secrets`; a user with only
  `user_management.settings.edit` gets 403. The migration registers the slug and grants it to
  no role: admin and superadmin pass by bypass, so it is admin-only.
- AC-5 Each PUT that changes something writes exactly one `system_settings` audit row (the
  generic listener's duplicate is suppressed) naming the actor and the changed fields; secret
  fields show `set` / `cleared`, never the value. No other settings route echoes a stored secret.
- AC-6 No restart: the labeller tick, the connector token check, the consent flow and the
  Respond.io webhook read the effective value per call (a DB value saved after boot is honoured).
- AC-7 An unreadable ciphertext (key changed) reads as unset for the reader (env fallback) and
  the GET reports `unreadable: true`; nothing raises.
- AC-8 `connector_ss_callback_url` must be an https URL; anything else is a 422.
- AC-9 The Integrations settings tab shows the three cards; secrets are update-only inputs with
  the masked value and source shown; the Respond.io card shows the webhook URL and the header.
