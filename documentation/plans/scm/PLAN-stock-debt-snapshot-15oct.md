# PLAN: Stock Debt snapshots (STOCK-DEBT-SNAPSHOT)

Status: built (PR #1705). Owner approved mock v2 (15 Oct). Track L. Review rounds closed four sets of findings:
security L1 to L4, reviewer blockers, the projects-schema move per ADR-0011, and a Park tickbox render loop
the browser pass found in the shared reserve dialog. Hand test pending on the crew copy.

## Discovery (main 01e275d4c)

- Board: `StockDebtService.list()` (`app/services/scm/stock_debt_service.py:349-508`). One
  `_assignments(...)` walk (`:385`) gives a `result` per product; rows are built at `:448-492`,
  totals `:494`. Not stored anywhere.
- Drill: `cell()` (`:547-866`) reruns the walk for one product and serialises each
  `result.lines` entry whose `bucket == month` at `:653-685`. `line.line.key` is the core
  `sales_order_lines.id` (`_demand`, `:1534`).
- SO line to OI row: `order_inquiry_rows.so_line_id` -> `projects.sales_order_lines.id`
  (`app/models/project_so.py:1011-1014`), whose `core_sales_order_line_id` is the core line.
- Reserve request: `POST /order-inquiries/{id}/reserve-requests` (`app/api/v1/projects/
  order_inquiries.py:1210-1246`, gate `projects.order_inquiries.acknowledge`, `:101`). Guards and
  helpers in `app/services/order_inquiry_reserve_service.py`: `_REQUESTABLE_VERBS` /
  `_REQUESTABLE_STATES` (`:65-66`), `_open_request_row_ids`, `_remaining` (used `:463-500`).
- FE: grid `StockDebtClient.tsx` (columns `:514-622`, tones `:94-98`, aux headers `:134-139`,
  month label `:111-128`, toolbar `:750-905`, Export as `primaryAction` `:897-905`). Drill
  `StockDebtCellDialog.tsx` (demand columns `:316-455`, `PanelDataGrid`). OI page
  `/project-sales/order-inquiries/[id]` with `?row=` (`OrderInquiryDetail.tsx:210-212`); dialog
  `order-inquiries/[id]/components/ReserveRequestDialog.tsx:60-73` (presentational, `onSend`
  injected); hooks `_shared/hooks/useReserveRowOptions.ts:90`,
  `_shared/hooks/useOrderInquiry.ts:199` (`useCreateOrderInquiryReserveRequest`); permission gate
  on the OI page `OrderInquiryDetail.tsx:142` (`useHasPermission`).
- Deferred delete precedent: `saved_view.delete` (`app/services/record_actions.py:2083-2107`),
  FE `components/common/DeferredActionButton.tsx`.
- Export pipeline: route `stock_debt.py:212-305`, task `generate_stock_debt_xlsx`
  (`app/tasks/export_tasks.py:1400`), renderer `_render_workbook` (`stock_debt_service.py:2677`).
- Dev measure (card): 550 rows, about 11,000 lines, about 5 MB JSON per snapshot of the owner's
  view; live list 5.7 s.

## Design

### Tables (one migration, `sds_0001_stock_debt_snapshots`, idempotent `IF NOT EXISTS`, in the `projects` schema per ADR-0011: Stock Debt is the `projects` module)

`stock_debt_snapshots` (CompanyScopedMixin, audited):
`id uuid pk, company_id, name varchar(120) not null, filters jsonb not null, months jsonb not null,
tba_month varchar(10), totals jsonb not null, row_count int not null, created_by varchar(100) fk
users set null, created_at timestamp default now()`. Index `(company_id, created_at desc)`.

`stock_debt_snapshot_rows` (CompanyScopedMixin, `__audit_skip__` with reason: bulk insert of up
to 5,000 rows per save; the decision fields carry their own who/when):
`id uuid pk, company_id, snapshot_id fk cascade not null, position int not null, product_id uuid
fk products set null, product_code varchar(180) not null, product_name text, row jsonb not null
(the StockDebtRow as served), lines jsonb not null (AC-4/AC-5 list), decision varchar(16) check in
('reserve','buy','ignore'), decision_warehouse_id uuid fk warehouses set null, decision_note text,
decided_by varchar(100) fk users set null, decided_at timestamp`. Indexes
`(snapshot_id, position)`, `(snapshot_id, decision)`.

Lines live on the row (JSONB) rather than a third table: the drill always reads one row's lines,
search is by product, and the largest dev row has 672 lines. Simplest thing that works.

### Backend

1. `StockDebtService` refactor, no behaviour change:
   - extract the body of `list()` up to `data_rows` into `_board(**filters) -> (data_rows,
     envelope_parts, assignments)`; `list()` pages it as today;
   - extract the demand-row dict of `cell()` (`:653-685`) into `_demand_row(line, order_ids)`;
     `cell()` calls it.
2. New `app/services/scm/stock_debt_snapshot_service.py`:
   - `create(name, filters, user_id)`: `_board(...)` once, cap check, one query for OI rows of
     every line's core id (AC-5), labels (AC-6), bulk insert rows with `position`;
   - `list`, `get` (prev/next), `rows(query, decision, page, limit)`, `lines(row_id, month)` with
     the live `now` read (AC-12) batched in two queries (SO status + open qty by core line id; OI
     rows + latest reserve request state by row id), `requestable` via the reserve service's own
     helpers;
   - `set_decision(...)` (AC-13), `delete(id, user_id)` (creator only, AC-15).
3. Routes `app/api/v1/projects/stock_debt_snapshots.py`, mounted with the stock debt router
   (literal `snapshots` segment, before any `{product_id}` path). Schemas in
   `app/schemas/stock_debt_snapshot.py`, every served field declared (response_model drops the
   rest).
4. `record_actions.py`: register `stock_debt_snapshot.delete` (WINDOW_DESTRUCTIVE, OWN_RECORD).
5. Export: `generate_stock_debt_snapshot_xlsx(download_id, user_id, snapshot_id, split, show_aux,
   company_id)` in `app/tasks/export_tasks.py`, renders the stored listing through
   `_render_workbook` with three extra columns. Worker restart needed after this lands (crew
   test copy restarts it).
6. Crew SQL for the shared dev DB at `crew/state/migrations/STOCK-DEBT-SNAPSHOT.sql` (the same
   DDL, idempotent).

### Frontend (`app/(protected)/project-sales/stock-debt/`)

1. Extract the grid's column builder from `StockDebtClient.tsx` into
   `lib/stockDebtColumns.tsx` (pure function: axis, tbaMonth, showAux, onCellPress, extra
   columns); the live board keeps identical output (existing vitest suites stay green).
2. Extract the drill's demand column defs from `StockDebtCellDialog.tsx` into
   `lib/stockDebtDemandColumns.tsx`; the live drill keeps identical output.
3. Live board: Snapshots link + `SaveSnapshotDialog` in the `primaryAction` fragment.
4. Shared with the OI side (owner, 15 Oct, mock v2 section 11), moved not copied:
   - `ReserveRequestDialog.tsx` + the row building at `OrderInquiryDetail.tsx:304-330` ->
     `project-sales/_shared/components/RequestReserveDialog.tsx` (props: `inquiryId`, live
     `OrderInquiryWorklistRow[]`, optional `defaultWarehouseId`, open state); the OI page and the
     snapshot drill both render it;
   - `rowRemaining` / `reserveIneligibleReason` (`OrderInquiryDetail.tsx:101-130`) ->
     `project-sales/_shared/lib/reserveEligibility.ts`;
   - the Actions items + `ParkedSendDialog` wiring (`OrderInquiryHeadersList.tsx:224-256,
     721-735`) -> `project-sales/_shared/hooks/useParkedSendActions.tsx`; the OI list and the
     snapshot page both use it.
   No backend change on the OI side: one parked queue, one send.
5. `snapshots/page.tsx` (list), `snapshots/[id]/page.tsx` (`SnapshotClient`), components
   `SnapshotDecisionPopover`, `SnapshotDrillDialog` (groups by OI, Now badges, reserve button
   opening the shared dialog fed by `useOrderInquiryHeaderLines(oi_id)`). Service `services/stockDebtSnapshotService.ts` via
   `lib/api-client` (`extractApiError`, `buildDataGridParams`), hooks
   `hooks/useStockDebtSnapshots.ts`.
6. Pages gated on `projects.stock_debt.view` like `stock-debt/page.tsx:15`. No sidebar change.

## Slices (one lane, one PR, one coder kept alive)

- S1 BE: migration, models, refactor (`_board`, `_demand_row`), service, routes, deferred delete,
  export task. Tester writes red pytest first (list below), `test(red):` commit, then coder.
- S2 FE: extractions, save dialog, list, snapshot page, decision popover, drill + reserve reuse.
  Tester writes red vitest first, `test(red):` commit, then coder.
- Phase 3: reviewer + security-reviewer (new routes, RBAC gate, company scoping) + browser pass
  1280/375 on the crew test copy, after the SQL is applied.

## Test list (tester, from the UAC; Postgres only, seed your own chain, CI DB is empty)

pytest `tests/scm/test_stock_debt_snapshots.py`:
1. AC-3/AC-4: seeded product with lines in two months and one TBA line: snapshot rows equal
   `list()` (all fields, order, axis, totals); each row's lines equal `cell()` demand for each
   bucket plus `bucket` and `sales_order_line_id`.
2. AC-5: a line with two OI rows, a line with none.
3. AC-11: after snapshot, cancel the SO, change a qty, add a new SO line: stored header, rows and
   lines unchanged.
4. AC-12: `now` on the same changes: `so_status=cancelled`, `open_qty=0`; OI row requestable
   true, then false after a reserve request is created; `reserve_state=requested`; OI row added
   after the snapshot appears with `after_snapshot`.
5. AC-7: cap (monkeypatch `MAX_LOW_STOCK_ROWS` to 1, two products): 422, no header written.
6. AC-2/AC-13: name blank / 121 chars 422; decision set, cleared, invalid value 422, warehouse not
   a site pool 422, warehouse with buy 422, note 2,001 chars 422.
7. AC-10: query, decision filters, paging total, stored order.
8. AC-8/AC-9: prev/next, can_delete per user, list newest first, query on name.
9. AC-15: delete by creator through the deferred action; another user 404; rows gone.
10. AC-17/AC-18: other company 404 on every route; no permission 403; API-key principal refused on
    POST/PUT/export.
11. AC-16: export task on a stored snapshot renders the stored figures (not live: change data
    first) with Decision/From/Note columns; in-flight 409.
12. Refactor guard: existing `tests/scm/test_stock_debt_*` route suites stay green (run touched).

vitest: `SaveSnapshotDialog.test.tsx` (default name, posts current filters, error toast),
`SnapshotClient.test.tsx` (Decision column second, footer from stored totals, search + decision
filter params, no projected switch), `SnapshotDecisionPopover.test.tsx` (From only for Reserve,
clear), `SnapshotDrillDialog.test.tsx` (grouping by OI, No OI last, line on two OIs in both, Now
badges, reserve button only with a requestable row AND permission, dialog rows = requestable rows
only, default location = decision pool, Park tickbox present and unticked), `SnapshotClient`
Actions menu (both send items with N from the reserve summary, greyed at 0, opening
`ParkedSendDialog`), `stockDebtColumns.test.tsx` (live board output unchanged), and the moved OI
suites re-pointed and green (`ReserveRequestDialog*`, `OrderInquiryDetail*`,
`OrderInquiryHeadersList*` send-parked tests).

## Open / risks

- Save time: one live read (about 6 s on dev for 550 rows) plus the OI query, synchronous in the
  request. If it passes 30 s on prod data, move it to the worker (trigger noted, not built).
- Another migration PR merge-ready at the same time forks main: say so in the PR body.
