# PLAN: split one SO line into delivery tranches in Order Inquiries (OI-LINE-SPLIT)

Status: Build (owner OK on mock v2, 16 Oct 23:47 "Ok proceed"). Track: full (L): migration, new
endpoint with RBAC, engine change. Lead pattern: tester red tests, one coder (Opus: tangled engine in
a 13k-line service), reviewer + security-reviewer, browser pass 1280/375. Stacked on #1748
(OI Excel one sheet; merged into this branch at c1f8f57d8).

Card: `oi-line-split-behaviour-card.md` (alongside, after discovery). UAC:
`oi-line-split-16oct-acceptance-criteria.md` (after the card is answered). Mock:
`documentation/mockups/OI-LINE-SPLIT/index.html`.

## Journey

Owner 16 Oct ~21:20: staff Excel shows one SO line split into several delivery tranches (SO417636
CSK14B: 417 @ 20/10/2026 split 14 @ 20/9/2026 + 236 @ 20/11/2026 + 167 @ 20/11/2029; 504 @
20/12/2026 split 37 @ 20/11/2026 + 467 @ 2/1/2027). AutoCount users reduce the line, change its
date and add new lines, so the CRM OI only sees net numbers and the split is lost. Staff need a
very interactive, flexible, drag-and-drop way to allocate one line's qty to several tranches (qty +
delivery date + remark), before Confirm or inside Confirm.

## Steps

1. Discovery with file:line (OI line mapping, AutoCount pull, Confirm, OI email/Excel, grid
   components, OI Lines page from #1727).
2. Behaviour card as ONE crew ask.
3. Lavish mock on SO417636 (business data only).
4. After owner OK: UAC, tickets, tester red tests, coder, reviewer, browser pass, hand-test script.

## Discovery (summary; full citations in the card)

- One OI row per SO line (owner rule, `project_so.py:1138-1145`); tranches sit under the row.
- AutoCount edits arrive per DtlKey; reduce + add new lines = unrelated qty_down / added / qty_up
  change rows (`planning_change_service.py:800-810`); pure split only on same date
  (`project_order_inquiry_service.py:5308`).
- Confirm parts must sum to open qty (`project_supply_service.py:6404`); no date per part.
- Email one row per saved line (`project_order_inquiry_service.py:4280-4295`); Excel one row per OI
  row, month sheets, no remark (`order_inquiry_worklist_service.py:195-222, 3353-3376`).
- FE reuse: Sheet, `SupplyBar` (`_shared/components/SupplyBar.tsx:42`), `DataGridTableDndRows`
  (`components/ui/data-grid-table-dnd-rows.tsx:183`), Kanban/Sortable (`components/ui/sortable.tsx`),
  `ContextMenu`, `DatePicker`, `Badge`, `Kbd`; quotation lines grid pattern
  (`[projectId]/components/QuotationLinesGrid.tsx`).

## Build design (final)

### Storage: one table, migration `ols_0001_so_line_splits` (down_revision = current head)

`projects.so_line_splits` (CompanyScopedMixin, uuid `id`): `project_sales_order_id` (FK
projects.sales_orders CASCADE), `so_line_id` = own/origin line (FK projects.sales_order_lines
CASCADE), `seq` int, `qty` numeric(15,4) > 0, `delivery_date` date NULL (NULL iff `is_cancel`),
`is_cancel` bool, `sits_on_line_id` (FK projects.sales_order_lines SET NULL; NULL = new line not in
AutoCount yet; = own line for a cancel), `remark` text, `base_qty` numeric(15,4), `base_date` date
NULL (the base the split was made against, same on every row of one origin), `created_by` /
`updated_by` (users SET NULL), `created_at` / `updated_at`. Index (project_sales_order_id),
(so_line_id). A line with no rows = not split. A sits-on line that is cancelled reads as NULL.

### API (router `BE/app/api/v1/projects/line_splits.py`, included in `projects/__init__.py`)

- `GET /api/v1/project-sales/sales-orders/{pso_id}/line-splits` (VIEW `projects.projects.view`, api
  key allowed) -> `{pso_id, so_number, customer, project, lines: [{line_id, line_no, item_code,
  location, base_qty, base_date, now_qty, now_date, pending_change, delivered_qty, splits: [{id,
  qty, delivery_date, is_cancel, sits_on_line_id, remark}]}]}`. Lines = the SO's open mirror lines
  ordered by item_code, location, line_no. base = stored base when split; else the pending change's
  `from` (qty, required_date) when a pending planning-change row exists for the line; else the
  line's open qty and date. now = current mirror qty / delivery_date.
- `PUT` same path (EDIT `projects.projects.edit` + `_assert_can_act_on`) body `{lines: [{line_id,
  splits: [...]}]}`: replaces the splits of every named line in one transaction; a line with an
  empty list or one split equal to its base on its own line and date with no remark = unsplit
  (rows deleted). 422 (AppException, message names the line) when: sum != base, qty <= 0,
  non-cancel without date, sits_on line not on this SO or different item/location, line not on this
  SO. Returns the GET payload. Server owns base (client never sends it).
- Company scoping by `CompanyScopedMixin`; SO loaded via `ProjectSupplyService(db).get_order`.

### Purchasing outputs

- Email (`record_saved_lines` / `_queue_saved_line`, `project_order_inquiry_service.py:4280-4560`;
  resend `oi_handover_resend.py`): a split origin line becomes one email line per split (LOCATION,
  ITEM CODE, QTY = base qty and DELIVERY = base date via `was`, NEW QTY = split qty, NEW DATE =
  split date, REMARK = change word + remark); inside Confirm the origin's "left to tell" adds a line.
  Uses the existing template (REMARK column since oihr_0008): no template migration.
- OI Excel (`export_xlsx`, one sheet): REMARK appended after the existing headings (the worksheet
  `[:10]` slice is unchanged). An OI row whose line has splits sitting on it expands into one row
  per split sitting on that line (QTY = split qty, DELIVERY DATE = split date, REMARK = change word,
  "from line N" when another line's, + remark) only when those splits add up to the row's printed
  QTY; otherwise the row prints as today with the split plan in REMARK. TOTAL QTY stays right.
- OI Lines tab (`serialize_rows`, :6372/:6433): `splits` of the row's line (origin view) for the
  read-only expand.

### Engine (Confirm honours staff links)

- Pre-pass `_apply_split_links` before `_apply_pure_splits` in `refresh_for_decision`
  (`project_order_inquiry_service.py:1323`): for each stored split whose sits-on line differs from
  its own line, both lines in this Confirm, own line's AutoCount qty went down: move that qty with
  its placement links and ack from the own line's live ORDER row to the sits-on line's row via
  `_split_row` (merge branch when the target already has a row; split qty instead of the target's
  Buy; latest split first). Same guards as the pure split (single live ORDER, not redirected, no
  reserve link). Does not add lines to handled/settled sets: netting tells only what is left.
- `_oi_demand_rows` (`planning_change_service.py:3889-3892`): drop = from - to - linked_out.
- No stored splits = today's behaviour exactly.

### FE (PS = sorento_crm_frontend/app/(protected)/project-sales/)

- `PS/_shared/types/lineSplit.types.ts`, `PS/_shared/services/lineSplitService.ts` (lib/api-client,
  extractApiError), `PS/_shared/hooks/useLineSplits.ts` (query + save mutation; toast; invalidate
  board + OI queries).
- Pure model `PS/fulfilment-planning/lib/lineSplitModel.ts`: moveSplit, splitInTwo, backToOwn,
  cancelSplit, gaps, plan/left per line, isSplit (all vitest-able).
- `PS/fulfilment-planning/components/LineSplitDialog.tsx` (full-screen Dialog): grid grouped by
  item + location, @dnd-kit DndContext (Pointer + Touch + Keyboard sensors as
  `resource-management/attachment-directories/components/AttachmentDirectoriesView.tsx`),
  useDraggable cards, useDroppable cells, DragOverlay, move popover, ContextMenu, zoom pane (SupplyBar
  style bar with dividers, table), footer gaps + Save; phone (< md) one line at a time.
- Entry: "Split delivery…" in the line action menu (`BoardDecideControl.tsx` dropdown) and a footer
  button in the `BoardChangeTable.tsx` lightbox; an SO group header button if the list view has one.
- OI Lines read-only expand: `orderInquiryHeaderLinesColumns.tsx` expandedContent.

### Slices (commit per behaviour; red tests first)

1. BE storage + API (tests: API shape, base rules, validation 422s, unsplit, RBAC 403, company
   scope, sits-on cancelled -> NULL; `tests/test_schema_uuid_id_principle.py`,
   `tests/test_list_pagination_limit.py`).
2. BE purchasing outputs (email lines, Excel expansion + REMARK, Lines serialize).
3. BE engine (SO402757 shape: 51 -> 2 with 49 linked to line 18; no CANCEL 49, links moved, no
   ORDER for 49; no splits = unchanged).
4. FE phase 1 against a mock service, then vitest red (model + dialog: Save blocked on gap,
   keyboard move across lines records sits_on, failed save keeps values) and wiring.

## Open

- Owner answers 16 Oct ~22:05: Q1 (a), Q2 (b) splits add up EXACTLY to the line qty (no balance,
  gap shown live, Save blocked), Q3 (a) staff-picked link, Q4 yes = (b) drag qty between SO lines of
  the same SO (each split keeps its own line and records the line it sits on), Q5 (a). New owner
  rule: the OI Excel is never split by month (separate PR #1748, crew ruled TOTAL QTY (a)).
- Owner Lavish notes on v1 (22:30): split at header level on planning (all SO lines, drag and drop
  across lines); never say "tranche", say "split"; real drag and drop (reuse @dnd-kit).
- Mock v2 filed: whole-SO split view (rows = SO lines grouped by item + location, columns = dates,
  New date, Cancel; a New line row), zoom pane = the single-line view. Design draft above still
  holds; the balance mode column is dropped (Q2 b) and a split may be a cancel.
- Dev DB (15 Oct prod copy, read-only check 16 Oct): SO417636 now present. CSK14B BRW-BB lines (Seq =
  `line_no`): 263 301 @ 20/09/2026 closed; 560 14 @ 20/09/2026 (the old 417 @ 20/10/2026 line);
  640 632 @ 20/11/2026 (old 192); 720 649 @ 02/01/2027 (old 504 @ 20/12/2026); 4816 751 @
  01/01/2030 cancelled. CSK11A: 768 335 @ 02/01/2027, 4817 313 @ 20/11/2026, 544 154 closed. Use it
  for the hand test once the build starts.
