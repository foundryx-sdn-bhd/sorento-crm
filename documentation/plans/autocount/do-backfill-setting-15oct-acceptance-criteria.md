# UAC: DO-BACKFILL-SETTING

Plan: `PLAN-do-backfill-setting-15oct.md`. Tests: `tests/test_do_assume_delivered_setting.py`,
`RuntimeSettingsCards.test.tsx` ("AutoCount" block).

- **AC-DBS1** Settings > Integrations shows an AutoCount card with "Assume DOs delivered before"
  (date, empty = off) and the usual source badge (Saved in CRM / Server env / Not set).
- **AC-DBS2** A saved date wins over env; clearing it falls back to env; neither = off.
- **AC-DBS3** After a save, the next DO push (same process, no restart) uses the saved date: a DO
  dated before it lands delivered with the marker; after clearing, the next one lands new.
- **AC-DBS4** PUT accepts `YYYY-MM-DD` or null only, never a date after today (MYT); 422 names the
  field.
- **AC-DBS5** Only `user_management.settings.secrets` may read or change it (`.edit` alone: 403).
- **AC-DBS6** Each save writes one `RUNTIME_SETTINGS` audit row naming the user, old and new date.
- **AC-DBS7** When no order status with code `delivered` exists, the field shows a plain warning.
