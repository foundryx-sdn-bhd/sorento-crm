# PLAN: split one SO line into delivery tranches in Order Inquiries (OI-LINE-SPLIT)

Status: Hand test v4 (head 569f23b1b: reviewer READY, kill-proofs posted, browser 1280/375 PASS + re-check PASS, ci labelled; hand test filed laneboard/scripts/1749.md; owner OK on mock v4 17 Oct 14:10, Q1 drag yes, Q2-Q4 (a), Q5 (a) no box; after the 17 Oct hand-test FAIL of the v2 grid). See "Build v4" at the bottom; UAC section A/F rewritten. Before that: PR #1749 head c3a4b4acd: CI green, reviewer READY, security ready. Track: full (L): migration, new
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

- Pre-pass `_apply_split_links` before `_apply_pure_splits` in `refresh_for_decision`: for each
  ACTING split whose sits-on line differs from its own line, both lines in this Confirm, own line's
  AutoCount qty went down and the target's went up: move that qty with its placement links and ack
  to the target's row via `_split_row` (merge branch; split qty, not the target's Buy; latest
  first; pure-split guards). Moved qty is recorded per origin and target in `SPLIT_MOVED_KEY`
  (session; popped only on the outermost rollback and after each order's apply).
- `split_state` per split line: `acting` (pending change from == stored base, or no pending change
  and open qty/date == stored base: Confirm applies it), `applied` (no pending change and open qty
  == own-line plan: the normal state after the Confirm that applied it; stored base kept, resend
  prints the plan), `stale` (anything else: current base, "AutoCount changed", engine ignores,
  resend prints the current row, a PUT re-bases). A line ADDED in the pending change has base 0.
- Sent email (`explained_by_splits`): the origin prints honoured split rows next to the netting's
  own CANCEL; a target excludes moved-in qty (a fully moved-in added line tells nothing); a cancel
  split is told once. `_oi_demand_rows` needs no change: a moved origin always settles in place
  (fa01e5f84 removed an unreachable subtraction).
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

## Build v4 (owner OK 17 Oct 14:10)

Frontend rebuild of the split screen only. Backend split model, totals, email and Excel stay; one
additive GET field (`so_date`, the SO date for the SO DATE column, AC-A12) is the only backend change.
Track: full (lane is L). security-reviewer: not needed for this round (no auth/RBAC/ingest change;
the one GET field rides the existing VIEW gate), said in the PR.

Files (PS = sorento_crm_frontend/app/(protected)/project-sales/, FP = PS/fulfilment-planning/):
- New `FP/components/LineSplitSheet.tsx` (the table, AC-A2..A12, F) inside the existing
  `FP/components/LineSplitDialog.tsx` shell (load, Save, close guard, history/undo, DndContext with
  the existing sensors :675-683, `SPLIT_AUTOSCROLL`, `DragOverlay` :835-895).
- Gone: `LineSplitGrid.tsx`, `LineSplitZoom.tsx`, `LineSplitMoveBox.tsx`, `LineSplitPhone.tsx` and
  the tests that only cover them; `lineSplitParts.tsx` keeps `ChangePill`, `SitsOnSelect`
  (relabelled per AC-A7), `QtyInput` where they still fit.
- Model `FP/lib/lineSplitModel.ts` (pure, vitest): `addSplit` fills what is left and sits on New line
  (AC-A5); `placeRest(lines, lineId, target)` for the chip (AC-A9); `targetDateOf(line)` = nowDate ??
  baseDate; `setSitsOn` onto another line takes its date (AC-A7); `sortLines` (AC-A2); `readsRemain`
  (only split equal to base on own line and date, any remark: AC-A4). Drops reuse `moveSplit` (:164)
  with qty = all.
- Backend: `so_date` on the GET payload (schema + service; pytest asserts it, response_model lesson).

DOM contract (tests and the coder both follow it; `<no>` = the AutoCount line no the UI shows,
`<n>` = 1-based split position in the line):
- table `aria-label="Split delivery sheet"`; one `<tbody data-testid="split-line-<no>">` per line.
- REMAIN cells: buttons `Split line <no> quantity`, `Split line <no> date`; REMAIN-line grip `Drag line <no>`.
- split row: inputs `Quantity, line <no> split <n>`, `Date, line <no> split <n>`, select
  `AutoCount line, line <no> split <n>`, `Remark, line <no> split <n>`; buttons
  `Drag split <n> of line <no>` (grip, first cell of the row), `Remove split <n> of line <no>`.
- balance cell `data-testid="balance-<no>"` text `0`, `<N> left`, `<N> over`; chip button
  `Drag the <N> left of line <no>`; add button `Add a split to line <no>`.
- while dragging: `data-testid="drop-new-date"`, `data-testid="drop-cancel"` rows; a faded line block
  has `data-dimmed="true"`; the hovered one `data-over="true"`.
- status line `role="status"`; Save button name `Save`; toast text `Moved <N> to <target>` with Undo.

Slices (red-first each: `test(red):` commit of test files only, then the fix commit, then a kill note):
1. Model deltas (vitest, `FP/lib/lineSplitModel*.test.ts`).
2. Backend `so_date` (pytest, `tests/scm/test_line_splits_api.py`).
3. Sheet without drag: AC-A1..A7, A10 (non-drag keys), A11, A12, B1, B5, F (vitest on the dialog).
4. Drag: grip leftmost, chip, REMAIN-line grip, targets, fade, drop rows, overlay, AC-A9 drops,
   toast Undo, keyboard drag (vitest, keyboard sensor as `LineSplitDialog.dnd.test.tsx`).
Then reviewer, browser pass on crew's copy (1280 + 375, SO417636 owner case end to end), new
hand-test script, `crew report --handtest`.

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
- 17 Oct checkpoint: crew audits closed (AutoCount line labels, target/source move guards, resend
  netting, drop under pointer + thin autoscroll, keyboard focus, CI test-setup fix). Browser rows on
  #1749 crew-notes. SO424910 reseeded 3 times (restored to original checksums); every test-Confirm
  email/delivery/notification cancelled by id (UTC timestamps: never compare with now()). After the
  owner's step 14 crew cancels that Confirm's email by id. After merge: drop sorento_oils_throwaway,
  gc worktree, archive plan/UAC/card; #1748 merges first, then merge main + single alembic head.
- 17 Oct hand test FAIL (owner, verbatim in the brief): "split delivery too hard to use, supposed to be 1
  page only view all products, and refer to the excel, when i split i can split vertically not
  horizontally". Mock v3 (`documentation/mockups/OI-LINE-SPLIT/index.html`): one full-screen page, one
  table of every SO line with the Excel's columns (LINE, SO DATE, S/O NO, ITEM CODE, LOCATION, QTY,
  DELIVERY DATE | CHANGE QUANTITY, CHANGE DATE, AUTOCOUNT LINE, REMARK, BALANCE); a split's rows stack
  under its line with the line's cells merged (rowSpan, as `scm/components/SupplierSheet.tsx:134`);
  unsplit lines read REMAIN; typing in REMAIN or "+ Split" splits; balance per line, Save waits for 0.
  FE only: LineSplitGrid / LineSplitZoom / LineSplitMoveBox / LineSplitPhone give way to one
  LineSplitSheet in the same dialog; backend, model, email and Excel stay. Model deltas: `addSplit`
  fills the new row with what is left; lines sort by item, location, delivery date. Open Q1-Q4 in the
  mock section 6 (drag none vs grip; AutoCount line column visible; new row = what is left; SO DATE and
  S/O NO columns kept). Dev DB facts (read-only): SO417636 = 292 lines, 29 products, one location
  BRW-BB; CSK14B AutoCount lines 32, 480 (old 417), 608 (old 192), 752 (old 504), 4768 (751 @ 01/01/2030).
- 17 Oct 13:50 owner on v3: "After i split ady can i like drag and drop the rows to allocate ... some qty to
  14 then remaining qty go to other date, somewhere like 20/11" = Q1 drag YES. Mock v4: a grip on every
  split row (and on a REMAIN line for the whole line) plus a draggable "N left" chip in BALANCE; drop on
  another line of the same item and location = that line's date (AutoCount's current one inside Confirm)
  and AutoCount line; "New date" and "Cancel" drop rows open under the item while dragging; other items
  fade. One <tbody> per line = one droppable. Reuses the built @dnd-kit wiring (LineSplitDialog.tsx:675-683
  sensors, :835-895 DndContext + SPLIT_AUTOSCROLL + DragOverlay; useDraggable/useDroppable as
  LineSplitGrid.tsx:177/:251) and moveSplit (lineSplitModel.ts:164). Q2-Q4 taken as recommended (a).
  New Q5: a drop moves the whole row/chip with no "how many" box (rec) vs keep LineSplitMoveBox.
