# PLAN: SNAPSHOT-DATE-TZ - stock debt snapshot export filename dated in Malaysia time

Status: in progress (small fix track: S, no migration, no auth change; red-first + kill test + reviewer)

## Finding (verified)

- Release run 38821488694, job 116719033785 (self-hosted runner, 2026-10-15T23:14 UTC) failed
  `tests/scm/test_stock_debt_snapshots.py::test_export_route_queues_the_snapshot_download`:
  expected `stock-debt-snapshot-16102026.xlsx`, got `stock-debt-snapshot-15102026.xlsx`.
- The route formats the filename from the raw column
  (`app/api/v1/projects/stock_debt_snapshots.py:189`, `snapshot.created_at.strftime('%d%m%Y')`).
  `created_at` is naive UTC (`app/models/stock_debt_snapshot.py:38`, `server_default=func.now()`;
  the app engine pins the session to UTC, `app/database.py:10-18`).
- The test compares against `date.today()` (`tests/scm/test_stock_debt_snapshots.py:1122`), the
  runner's local date. The self-hosted runner is in MYT, the Postgres container in UTC, so the
  two disagree every day 00:00-08:00 MYT.
- Product impact: a snapshot exported 00:00-08:00 MYT carries yesterday's date in its filename.
  Every other date this system shows is Asia/Kuala_Lumpur (e.g. `app/models/project_so.py:883-885`).

## Fix

1. Route: format the date from `in_malaysia(snapshot.created_at)`
   (`app/services/pdf_render.py:39`, the shared "stored naive UTC -> Malaysia wall clock" helper,
   already reused off-PDF by `app/services/order_inquiry_reserve_service.py:111-116`).
2. AC-16 (`stock-debt-snapshot-acceptance-criteria.md:88-89`): "ddmmyyyy of created_at in
   Malaysia time".
3. Tests (red first):
   - New: pin `created_at` to `2026-10-15 17:30:00` (naive UTC, which is 01:30 on 16 Oct MYT) and
     assert `stock-debt-snapshot-16102026.xlsx`. Deterministic, independent of host and DB TZ.
   - Existing test: build the expected date from the stored row converted to Asia/Kuala_Lumpur,
     never `date.today()`.
   - Prove green under `TZ=UTC`, `TZ=Asia/Kuala_Lumpur`, `TZ=Pacific/Kiritimati`, each with a
     mismatched DB session zone (`PGTZ`).

## Sibling date.today() asserts in tests (listed, not fixed: not the DB created_at pattern)

- `tests/scm/test_stock_debt_export.py:107,367` vs product `date.today()`
  (`app/api/v1/projects/stock_debt.py:279`, `app/services/scm/stock_debt_service.py:2789`): same
  clock both sides, so not flaky; but the live export names its file by the HOST date, so on a UTC
  host it is a day behind 00:00-08:00 MYT. Follow-up candidate.
- `tests/test_board_undo_email.py:237` vs product `date.today()` in `_build_undo_context`
  (`app/services/project_order_inquiry_service.py:12721`): same clock, not flaky; but the undo
  email's "today" is the HOST date, while its sibling handover email uses `today_malaysia()`
  (`project_order_inquiry_service.py:12583`). Follow-up candidate.
