# PLAN: "Assume DOs delivered before" moves from env into System Settings (DO-BACKFILL-SETTING)

Status: **built, PR open.** Track: S (crew size S; carries one additive migration column, so not
the repo small-fix track). Plan created: 2026-10-15T10:10+08:00. UAC:
`do-backfill-setting-15oct-acceptance-criteria.md`. Parent: `PLAN-do-history-backfill-13oct.md`.

## Ruling

Owner, 15 Oct 2026: "can this be a frontend config, I really don't want to set things in env and
need to restart the prod services". The DO-HISTORY cutover today is env
`DO_ASSUME_DELIVERED_BEFORE`, read once per process (`app/config.py`), on 4 prod services.

## Seam (reuse, nothing new)

- The mechanism already exists: `app/services/runtime_settings.py` (SETTINGS-IN-DB-SORENTO). A
  column on the `system_settings` singleton, NULL falls back to the env var of the same name, the
  screen is Settings > Integrations (`RuntimeSettingsCards.tsx`), the endpoint is
  `GET|PUT /api/v1/user-management/settings/runtime` (slug `user_management.settings.secrets`,
  admin only), and every save writes one `RUNTIME_SETTINGS` audit row.
- Add `do_assume_delivered_before` (DATE) to it: one migration column (`dobs_0001`), a
  `DATE_FIELDS` set (ISO text in describe / update / audit, a `date` from `get`), ISO-only + not
  after today (MYT) validation on PUT.
- `AutocountDocIngestService._assume_delivered_before` reads it through `runtime_settings.get` on
  the batch's own session, once per batch: a save applies to the next push or pull apply in every
  process, with no restart.
- The env var stays, as the fallback while nothing is saved (the runtime_settings contract).
- GET adds `delivered_order_status_exists` (the ingest's own `delivered` lookup), and the
  AutoCount card warns when it is false.
- Review fix: the shared `DatePicker` (`components/ui/date-picker.tsx`) accepted a half-typed
  `2026-07-0` as 30 Jun and rolled `31/02` into March; a typed date now parses only when it is
  that real day, and ISO needs two-digit month and day.
