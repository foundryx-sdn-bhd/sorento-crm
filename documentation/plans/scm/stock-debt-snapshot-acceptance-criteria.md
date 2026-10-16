# UAC: Stock Debt snapshots (STOCK-DEBT-SNAPSHOT)

Plan: `PLAN-stock-debt-snapshot-15oct.md`. Card: `CARD-stock-debt-snapshot-15oct.md`. Mock:
`documentation/mockups/STOCK-DEBT-SNAPSHOT/index.html`. Owner answers 15 Oct: Q1 to Q5 all (a)
(decisions in v1; request reserve from the snapshot drill; freeze SO lines + OIs only; everyone
with Stock Debt access sees, saves and decides, creator deletes; keep until deleted).

All routes below sit under `/api/v1/project-sales/stock-debt/snapshots` and need
`projects.stock_debt.view`. Every WRITE uses `require_permission` (session only), never the
API-key variant, the same rule the export route follows (`app/api/v1/projects/stock_debt.py:212-226`).

## Save

- AC-1 `POST /snapshots` body `{name, filters}`; `filters` takes every field of
  `StockDebtExportIn` except `split` (`query, group, location, exclude_location, only_debt,
  date_from, date_to, supplier_ids, book, projected, show_aux`). 201 returns the header (AC-8
  shape).
- AC-2 `name` is trimmed, required, at most 120 characters; otherwise 422.
- AC-3 The stored rows equal `StockDebtService.list()` for the same filters, unpaged, in the same
  order: every `StockDebtRow` field, the `months` axis, `tba_month` and `totals` match the live
  list taken in the same transaction.
- AC-4 For every stored row, the stored lines equal the live drill's demand rows
  (`StockDebtService.cell()` `demand`, `stock_debt_service.py:653-685`) for every bucket of that
  row, each line also carrying `bucket` (`YYYY-MM`, `tba`, `undated` or `unlocated`) and
  `sales_order_line_id` (the core line id). Lines come from the SAME walk as the rows: for every
  month cell, `-sum(short_qty)` of its lines plus the cell's supply credit is the live drill's own
  identity; the test asserts lines equal `cell()` demand, field for field.
- AC-5 Each stored line carries `oi_rows`: every `order_inquiry_rows` row whose `so_line_id` is a
  `projects.sales_order_lines` row with `core_sales_order_line_id` = that line, at that moment:
  `{oi_id, inquiry_no, row_id, qty, delivery_date, stock_location, verb, state}`. A line with none
  stores `[]`.
- AC-6 The stored filters also carry the human labels: location codes (both lists) and supplier
  names, so a later rename does not change what the snapshot says it was taken under.
- AC-7 More than 5,000 rows (`MAX_LOW_STOCK_ROWS`, `low_stock_report_service.py:69`): 422
  "Narrow the filters first", nothing stored.

## Read

- AC-8 `GET /snapshots/{id}` returns `{id, name, filters, filters_label, months, tba_month,
  totals, row_count, decided_count, created_by_name, created_at, can_delete, prev_id, next_id}`.
  `prev_id`/`next_id` walk the company's snapshots by `created_at`. `can_delete` is true for the
  creator only.
- AC-9 `GET /snapshots?query=&page=&limit=` lists the company's snapshots, newest first, `query`
  matching the name (case-insensitive); each item has the AC-8 header fields except the axis and
  totals.
- AC-10 `GET /snapshots/{id}/rows?query=&decision=&page=&limit=` returns the stored rows in stored
  order, each a `StockDebtRow` plus `row_id, decision, decision_warehouse_id,
  decision_warehouse_code, decision_note, decided_by_name, decided_at`. `query` matches product
  code or name (case-insensitive). `decision` is one of `decided, undecided, reserve, buy,
  ignore`. Pagination `total` is the filtered count.
- AC-11 A snapshot is never recomputed: after the snapshot is taken, cancelling an SO, delivering
  a line or changing a line's quantity leaves AC-8 and AC-10 unchanged, field for field.
- AC-12 `GET /snapshots/{id}/rows/{row_id}/lines?month=` returns the stored lines of that row
  (one bucket when `month` is given, every bucket otherwise), sorted by due date then SO number,
  each with the frozen fields of AC-4/AC-5 plus a live `now` read at request time:
  - `now.so_status`: the sales order's current `status` (`open`, `closed`, `cancelled`);
  - `now.open_qty`: the line's current open demand (`demand_qty()`), 0 when the SO is not open;
  - every frozen OI row gains `now_state` (the row's current `state`, or `missing` when the row is
    gone), `reserve_state` (the latest reserve request on that row: `requested`, `parked`,
    `reserved`, `cancelled`, or null), `remaining` and `requestable`;
  - `requestable` is true exactly when the existing reserve request would accept the row: verb
    ORDER / ORDER BACK, state raised / partly linked, no open request, remaining > 0
    (`order_inquiry_reserve_service.py:65-66, 475-500`; reuse its helpers, do not re-derive);
  - OI rows linked to the line NOW that were not frozen are appended with `after_snapshot: true`.

## Decide

- AC-13 `PUT /snapshots/{id}/rows/{row_id}/decision` body `{decision, warehouse_id, note}`.
  `decision` is `reserve`, `buy`, `ignore` or null (null clears all three fields and who/when).
  `warehouse_id` is accepted only with `reserve` and must be a site pool
  (`ProjectSupplyService.site_pool_warehouses()`), else 422; it is optional with `reserve`.
  `note` at most 2,000 characters. Stores `decided_by` = caller, `decided_at` = now. Returns the
  AC-10 row.
- AC-14 Any holder of `projects.stock_debt.view` may decide on any snapshot of the company.

## Delete

- AC-15 Delete runs through the deferred-action registry as `stock_debt_snapshot.delete`
  (`record_actions.py`, same shape as `saved_view.delete` at `:2096`): destructive window,
  creator only (another user gets 404), hard delete of the header and its rows.

## Export

- AC-16 `POST /snapshots/{id}/export` body `{split, show_aux}` queues the workbook through My
  Downloads (kind `stock_debt_snapshot_xlsx`, `imports` queue), one in flight per user per kind
  (409 otherwise). The worker renders today's workbook (`_render_workbook`,
  `stock_debt_service.py:2677`) from the STORED rows, axis and totals, never a live read, with
  three columns appended: Decision, From, Note. Filename `stock-debt-snapshot-<ddmmyyyy of
  created_at in Malaysia time>.xlsx` (stored naive UTC, so 17:30 UTC on 15 Oct names 16102026).

## Scope and security

- AC-17 Both tables are company-scoped (`CompanyScopedMixin`): another company's snapshot or row
  answers 404 on every route.
- AC-18 Without `projects.stock_debt.view`: 403 on every route. Every route is session-only (security
  review L2): an API-key principal is refused on reads and writes alike.
- AC-18b `oi_rows` (frozen and live) are served only when the caller also holds
  `projects.projects.view`, the OI pages' own read gate (security review L1); otherwise `[]`.
- AC-18c Each decision PUT (set or clear) writes exactly one audit entry; a refused PUT writes none
  (security review L3). `filters.query` is at most 200 characters; `location`, `exclude_location`
  and `supplier_ids` are UUIDs (`supplier_ids` also takes "none"); malformed values are 422 (L4,
  reviewer NIT).
- AC-18d Both tables live in the `projects` schema (ADR-0011).
- AC-19 The OI reserve request and its batch send are unchanged on the backend: the snapshot page
  calls the existing `POST /order-inquiries/{id}/reserve-requests` (with `park`), and the
  existing parked preview / send routes (`order_inquiries.py:1337-1370`, `park_send.py`), each
  with its own gate. There is one parked queue.

## Frontend

- AC-F1 Stock Debt toolbar: "Snapshots" (opens `/project-sales/stock-debt/snapshots`) and "Save
  snapshot" beside Export (`primaryAction`, `data-grid-list-toolbar.tsx:609-611`). Save snapshot
  opens a modal with Name (default `Stock Debt <dd/mm/yyyy>`) and a read-only line naming the
  product count and the filters; Save posts the board's current filters (the same object Export
  sends); success toasts "Snapshot saved" with an Open action; an error toasts the extracted
  message.
- AC-F2 Snapshots list page: `PageHeader` with back to Stock Debt; DataGrid (fixed layout,
  resizable, `rowHref` to the snapshot) with Name, Filters (truncate + title), Products, Decided,
  Created by, Created at; Delete only where `can_delete`, as the deferred-action countdown (no
  confirm). Empty state: heading "No snapshots yet" + hint, no button.
- AC-F3 Snapshot page `/project-sales/stock-debt/snapshots/[id]`: `PageHeader` (name + "Snapshot"
  badge, back to the list, `RecordNavigation` prev/next), read-only meta under it (taken at, by,
  book, location, delivery dates, only in debt, products, decided). Toolbar: product search,
  clearable Decision select (Decided, Undecided, Reserve, Buy, Ignore), Export popover (same split
  choices as Stock Debt). Grid: Stock Debt's own columns from the stored axis (aux columns when
  the snapshot stored `show_aux`), Decision as the second column, stored totals in the footer,
  server paging. No live filters, no projected switch.
- AC-F4 Decision cell: "Decide" when empty, else a badge (Reserve + pool code, Buy, Ignore); it
  opens a popover with Reserve / Buy / Ignore, From (clearable `SearchableSelect` of site pools
  with their available stock, from `useReserveRowOptions`; shown for Reserve only), Note,
  "who, when", Save and Clear decision.
- AC-F5 Pressing a month cell opens the snapshot drill for that bucket; pressing the product opens
  it for every bucket. Title `<code> · <month label | all months>`, subtitle "As frozen <taken
  at>"; a summary strip (total, BRW on hand, lines, short, decision). Lines are grouped by OI (a
  line on two OIs shows in both), lines with no OI in a "No OI" group last. Columns: the live
  drill's demand columns (`StockDebtCellDialog.tsx:316-455`) plus "OI row" (frozen qty, links to
  `/project-sales/order-inquiries/<oi_id>?row=<row_id>`) and "Now" (badges from AC-12). The OI
  number in the group header links to the OI page.
- AC-F6 An OI group shows "Request CS to reserve" when at least one of its rows for this product
  is `requestable` and the user holds the OI acknowledge permission. It opens the ONE shared
  request component, `project-sales/_shared/components/RequestReserveDialog.tsx` (owner 15 Oct:
  "share the same component from these two perspectives"), moved from
  `order-inquiries/[id]/components/ReserveRequestDialog.tsx` together with the row building now
  inline at `OrderInquiryDetail.tsx:304-330`. Its rows are the OI's live rows
  (`useOrderInquiryHeaderLines(oi_id)`, the OI page's own query) narrowed to this product's frozen
  rows that `reserveEligibility.ts` (moved from `OrderInquiryDetail.tsx:101-130`) accepts; the
  default location is the decision's pool when offered, else the component's default. "Park
  this first" behaves exactly as on the OI page (unticked on open). It writes through
  `useCreateOrderInquiryReserveRequest(oi_id)` (same toast); on success the drill's lines query
  is invalidated so Now shows Reserve parked / Reserve requested.
- AC-F6b The OI page uses the same moved files with no visible change: its existing vitest suites
  (`ReserveRequestDialog*.test.tsx`, `OrderInquiryDetail*.test.tsx`) move or re-point and stay
  green.
- AC-F6c The snapshot page toolbar has the same "Actions" menu as the Order inquiries list:
  "Send all parked requests to CS (N)" (acknowledge) and "Send all parked reserves to purchasing
  (N)" (reserve), N = the presser's own parked items wherever they were parked, greyed at 0 with
  today's reason, opening `components/common/ParkedSendDialog.tsx`. Both pages get them from one
  shared hook, `project-sales/_shared/hooks/useParkedSendActions.tsx`, extracted from
  `OrderInquiryHeadersList.tsx:224-256, 721-735`. A request parked from the snapshot is counted
  and sent from the Order inquiries list too, and vice versa.
- AC-F7 Usable without clipping at 375 and 1280: the grid scrolls sideways with Product pinned;
  the drill's tables scroll on their own; dialogs fit the viewport.
- AC-F8 No UUIDs on screen; no explanatory text inside the UI.
