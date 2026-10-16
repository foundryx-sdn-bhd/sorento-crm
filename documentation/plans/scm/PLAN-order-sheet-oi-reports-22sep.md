# PLAN: order sheet fixes, OI worksheet for a plan run, async OI export

Status: Lanes A-D BUILT (A + B merged; C + D re-land in #1144). Lane E (discontinued admission) + Lane F (OI need bought in full on All runs) IN PROGRESS 23 Sep, one PR #1148. Earlier: four PRs ready for review, awaiting owner go: #1134 (A), #1136 (B), #1138 (C, stacked on A), #1140 (D, stacked on A). Merge order A, C, D; B independent. Lane stack :3000/:8000 serves Lane D for hand-testing.
UAC: `order-sheet-oi-reports-22sep-acceptance-criteria.md`.

## What the owner asked (22 Sep 2026, order sheet screenshot + OI report sample)

1. Order sheet: BRW incoming qty and Last in qty must not print the SPO number.
2. Order sheet: Project / customer reads like the Project column on Order Inquiries.
3. Explain the numbers (Suggestion column, and why SRTWB248 buys 67 with 56 on hand).
4. New report: the OI lines behind a reorder plan, in the ORDER INQUIRY tabular format
   purchasing already uses (SO DATE / S/O NO / ITEM CODE / QTY / TOTAL QTY / DELIVERY DATE /
   PROJECT/CUSTOMER / SUPPLIER / PO NO), so they can tick every line against a PO in
   AutoCount.
5. Order sheet: one more column right of Supplier - last purchase cost. Supplier = last
   purchase supplier.
6. OI detail Export Excel: async, lands in My Downloads, tied to the OI so its download
   history is reachable from the OI.

## Measured (0921 prod copy, run 9e2254f6 of 21 Sep)

- Supplier on the sheet is ALREADY the last-purchase supplier: `_last_po_supplier_map`
  reads the newest non-cancelled PO line per product (issue_date desc). Nothing to change.
- The sheet's three project cells (Project qty, Delivery, Project / customer) come from
  `_project_inquiry_map`, which INNER JOINs `projects.so_supply_decisions` with
  `state = 'active'`. On the copy 12,261 of 12,763 live OI Buy rows have NO supply decision
  (form-raised / uploaded rows; only 501 went through the board). Those rows never reach
  the three cells. The engine (`demand.py` project select) reads BOTH legs - the confirmed
  leg with a decision AND the form leg with `supply_decision_id IS NULL` - so it buys for
  them. That is the whole SRTWB248 story: SO418869 (WATER CARE / EUGENIA), 67 units at
  BRW-IR, delivery 01/10/2026, OI row verb ORDER state raised, no decision. Engine: Buy 67.
  Sheet: Project qty 0, Delivery blank, Project / customer blank.
- Project / customer label: `_project_inquiry_map` prints `customer_name / projects.title`.
  Adopted AutoCount orders have `project_id` NULL by design, so the cell shows only the
  customer ("APEX CONNECTION SDN BHD (PROJECT)"). The OI worklist prints
  `project_customer_label(customer, coalesce(Project.title, SalesOrder.project_label))`
  (PR #1084), which is why it reads "APEX CONNECTION / DNC / TITAN RITZ / D NOVA".
- Suggestion text (`_suggestion_text`, channel runs): `Stock <BRW on hand> + PO <open PO
  qty> + Buy <Suggested qty>`. Stock and PO are the supply figures the engine counted, not
  amounts applied to the need; the three parts do not add up to Project qty and were never
  meant to. Suggested qty is the engine's buy inside the plan window, net of stock and PO.
- Async downloads already exist for the order sheet + low stock report
  (`user_downloads`, `source_entity_type = reorder_run`), for complaints
  (`generate_complaint_pdf`, `EntityDownloadsButton` "Download history" in the gear).
  The OI detail's Export Excel is the only sync one: `GET /order-inquiries/export?
  inquiry_id=` streams bytes and `saveBlobAs`.
- The ORDER INQUIRY tabular format the owner pasted IS `OrderInquiryWorklistService.
  export_xlsx` (EXPORT_HEADINGS, one sheet per delivery month, SUPPLIER then ITEM CODE).
  Superseded in part (owner, 16 Oct 2026, `PLAN-oi-excel-one-sheet-16oct.md`): the OI Excel is ONE sheet, never split by month.
  The new report is that writer over a different row set, not a new writer.
- Engine's last-purchase cost per product already exists: `reorder_engine.
  last_purchase_costs` (newest PO line with `unit_cost IS NOT NULL`, per supplier).

## Lane A - order sheet cells (small fix track candidate: one module, no migration)

Seam: `app/services/scm/summary_order_service.py` (+ `low_stock_report_service.py` shares
the cell builders, so it inherits A1/A2/A3 for free).

- A1 `_docs_text` for BRW incoming qty and `_last_in_text` drop the SPO number:
  `<container> - <qty>` per line, bare qty when the line names no container. BRW PO qty
  keeps its PO numbers (not asked). **R1 (owner, 22 Sep): container + qty.**
- A2 `_project_inquiry_map` label = `project_customer_label(customer_name,
  coalesce(pj.title, so.project_label))` imported from `project_order_inquiry_service`, so
  the sheet, the OI worklist and the OI detail spell one row the same way.
- A3 `_project_inquiry_map` reads the OI book directly, **scoped to the run** - owner
  rulings 22 Sep: "read from OI, don't care about supply decision", then "the project qty
  should be lesser", "I need the quantity here to make perfect sense". The
  `so_supply_decisions` join goes. The predicate is the ENGINE's own OI row predicate
  (captain ruling 23 Sep after review: the plan's first cut, "state <> cancelled, ack not
  rejected", admitted 2,006 placed + 5,713 actioned + 474 awaiting-ack + 12 redirected rows
  on the 0921 copy that the engine never buys for, so Project qty would have run ABOVE Buy
  instead of tallying): verb in (ORDER, ORDER_BACK), state in (raised, partly_linked),
  ack_state in (acknowledged, changed), not redirected to pool, owed qty (qty minus linked)
  > 0, printed qty = owed; product = the CORE line's product (`sol.product_id`, as the
  engine's confirmed leg reads it); SO in `run.so_numbers` when set, product in
  `run.product_ids` when set, delivery date inside `[plan_horizon_start,
  plan_horizon_date]` (undated included). Lane C prints the same rows.
  Result: Project qty = the rows the engine bought for, so on a Project run with buy-in-
  full Project qty == Buy, and Delivery / Project-customer list exactly those rows. One
  helper (`run_scope_oi_rows(db, run)` in `demand.py`) feeds both `write_rows` and Lane
  C. Freeze-time change; rerun to see it.
- A3b Suggestion text becomes one part per line - owner: "the + + should be separated
  into lines so it is easier to see": `Stock: 438` / `PO: 11` / `Buy: 67` on the XLSX
  (wrapped cell) AND the PDF (column 6 joins `_PDF_LIST_COLUMNS`). `Nothing` stays one
  word. SHEET ONLY: the plan grid's Decision pill is a one-line control and derives its
  own label on the FE (`lib/planEdits.ts`); it is untouched (captain, 23 Sep - the owner's
  ask was made on the sheet).
- A4 New column "Last cost" immediately right of Supplier, order sheet PDF + XLSX. Value =
  the newest non-cancelled PO line's `unit_cost` + currency for the product (same
  DISTINCT ON as the supplier lookup, extended to select cost/currency), joined at EXPORT
  time like Description/Category on the low stock report - no new frozen column, no
  migration. Printed `12.50 CNY`; blank when the line has no cost. **R2 (owner): one text
  cell; currency NEVER hard-coded** - `COALESCE(pol.currency, po.currency)`, the header's
  currency when the line carries none (AutoCount lines carry no currency; PR #1124 fixes
  the CNY fill and backfills the 66,720 mislabelled lines, so this column must not
  re-introduce the assumption). Low stock report does not get the column (not asked).
  Widths/PDF column maps shift by one (`_XLSX_COLUMN_WIDTHS`, `_PDF_LIST_COLUMNS`,
  `_PDF_NUM_COLUMNS`) - the tests pin them.

Tests: pytest on `_docs_text`/`_last_in_text` shapes, the label rule, the A3 predicate
(seed one form-leg row with no decision and assert it lands in Project qty), the new
column position on both formats.

## Lane B - OI detail Export Excel goes async and is tied to the OI

- B1 `POST /order-inquiries/{inquiry_id}/export` creates a `user_downloads` row
  (`kind = order_inquiry_xlsx`, `source_entity_type = order_inquiry`, `source_entity_id =
  inquiry id`, filename `<OI number>.xlsx`), enqueues `generate_order_inquiry_xlsx`
  (`export_tasks`, queue `imports`) which calls the existing `export_xlsx(inquiry_id=...)`
  and `mark_ready` with the bytes; enqueue failure marks the row failed (complaint
  pattern, verbatim). In-flight guard via `has_in_flight` like the order sheet (AC-21 of
  that plan): a second click while one is queued starts nothing.
- B1b **R4 (owner): the OI LIST page's Export Excel goes async too**, untied to any
  record: `POST /order-inquiries/export` with the same filter body the GET takes, kind
  `order_inquiry_worklist_xlsx`, no source entity, filename `order-inquiries-<ddmmyyyy>.xlsx`;
  same task with `inquiry_id` absent. The GET stays for one release (MCP/other callers),
  then retires.
- B2 OI detail: Export Excel toasts "Queued - see My Downloads"; the gear gets "Download
  history" opening `EntityDownloadsButton` for (`order_inquiry`, id), exactly the complaint
  detail's placement. The list page's Export Excel button toasts the same way (B1b).
- Worker restart needed (new task in `app/tasks/*`).

## Lane C - "Order inquiry worksheet" for a plan run

Owner's process (22 Sep): **this sheet comes FIRST.** Purchasing downloads the OI rows in
the run's scope, then the engine decides stock / buy / SPO per row, then the decision is
executed. So the row set is the run's SCOPE, not the engine's verdict. Reconciliation
against AutoCount POs is done offline for now.

- C1 Export menu on the plan view gains "OI worksheet Excel" beside Order sheet / Low stock;
  same `useExport*` hook shape, one pending flag for all four items; `source_entity_type
  = reorder_run`, `kind = oi_worksheet_xlsx`, filename `oi-worksheet-<ddmmyyyy>.xlsx`.
- C2 Row set = every live OI Buy row (verb ORDER/ORDER_BACK, state <> cancelled, ack not
  rejected, qty > 0) that satisfies the run's own Start Plan scope, read off the
  `reorder_run` row: `so_numbers` (when set), `product_ids` (when set), `demand_class`
  (Project = OI rows only; Dealer/All = the same OI predicate, there is no OI for retail),
  and delivery date within `[plan_horizon_start, plan_horizon_date]` (undated rows
  included, as the engine does). A raised-date filter, once PR #1123 stores one on the run,
  applies the same way - **owner: "raised at, sales order delivery, orders, products in
  Start Planning, so all should tally"**. A run's far-month rows are OUT. Products with no
  sheet row still appear if their OI row is in scope: the sheet is the input, not the
  output.
- C3 Writer = `OrderInquiryWorklistService.export_xlsx` with a `row_ids` filter fed by
  `run_scope_oi_rows` (delivery_from/to already exist; `so_numbers` and
  `product_ids` list filters are new). **Owner: no ACKNOWLEDGED column** - the worksheet
  writer takes a `columns` argument that stops at LOCATION; the list-page export keeps it.
- C4 Task `generate_oi_worksheet(download_id, run_id, user_id)` in `export_tasks`.
- C2 and A3 share `run_scope_oi_rows`; C branches from A's lane or waits for its merge.

Tests: pytest for C2's row set on a seeded run (one row in window, one outside, one from a
different SO, one for a product not in `product_ids`), the new list filters, the column
cut, the task's mark_ready. Vitest for the menu item + pending flag.

## Answers the owner asked for (kept here so the sheet's readers can find them)

The screenshot run was Demand: Project with selected sales orders, window 01/09 to 16/11.
In that mode Buy = the selected SOs' OI Buy rows due inside the window, bought in full
(owner ruling R1a, 22 Sep: stock and open PO are not netted against project need). Stock
and PO in the Suggestion text are only what is on hand at BRW and on open PO. Project qty
today counts OI rows that carry a board decision, every SO and every month, so it is 0 on
seven of the twelve rows and unrelated to Buy on the rest (A3 fixes it). Traced on the 21
Sep copy:

| Item | Buy | Source OI rows |
| --- | --- | --- |
| B2155-NL-BLUE | 961 | selected SOs' rows in window (OIB 700, Super Ceramic 156, Bathe Code 85, Mah Sing 15, ...) |
| CB2805A-DIY | 246 | SO402757 OIB / Myra Cove 246, 01/11 |
| CB2829-DIY | 128 | SO419517 OTM / Tat Lian 58 + SO418869 Water Care / Eugenia 70 |
| CB771 | 205 | SO418869 Water Care / Eugenia 205, 01/10, BRW-IR |
| CBT001-NL | 54 | SO420955 Kenwealth / Public Bank Bangi 54, 25/09 |
| MSP124 | 43 | SO420374 Bathe Code / Ban Hong 43, 02/11 |
| SRTWB1515 | 164 | SO402757 OIB / Myra Cove 164, 01/11 |
| SRTWB248 | 67 | SO418869 Water Care / Eugenia 67, 01/10, BRW-IR |
| SRTWB7237 | 82 | SO402757 OIB / Myra Cove 82, 01/11 |
| SRTWCX7405-RL-S-PJ | 164 | SO402757 OIB / Myra Cove 164, 01/11 |
| SRTWHBWP | 82 | SO402757 OIB / Myra Cove 82, 01/11 (9,922 on hand, bought in full per R1a) |
| SRTWT2207 | 67 | SO418869 Water Care / Eugenia 67, 01/10, BRW-IR |

Flagged to the owner: R1a means SRTWHBWP buys 82 against 9,922 on hand. Changing that is a
ruling on PR #1122, not this plan.

## Lane D - plan All with picked project SOs, retail via Dealer o/s

Owner, 22 Sep: "when we plan, I can only choose either project or retail, actually I want
to plan all, but for project I want to select specific SO which is linked to related OI,
the retail order contributes to dealer O/S qty." Then: "no, this should be 1 plan" - so it
is in scope here, on top of PR #1122 (branch from it, or from main once it merges).

Measured (main + #1122 worktree): `RunPlanningModal` sends `demand_class` only when the
buyer picks Project or Dealer and `so_numbers` only under Project; the Orders picker is
hidden under All. Backend honours `so_numbers` only when `demand_class == "project"`
(`_planning_rows` via `horizon_committed_select_sql(so_scoped=...)`,
`_apply_project_supply_reduction` line 1335). An All run therefore plans EVERY project
order in the window plus the retail leg. #1122 makes a Project run leg-1-only and
project-need-only.

- D1 Modal: the Orders picker shows under Demand = All as well as Project (same candidate
  query, same "everything in range ticked by default", same touched-by-hand rule). It is
  the existing `SearchableMultiSelect` already in the modal - owner: "use our existing
  multi select component, not a bloated picker", then "just one line, with extra as +x" -
  so the trigger uses the component's existing `renderTriggerLabel` to print ONE line:
  the first two SO numbers then `+x` (`SO418869, SO419517 +12`), no chip wall; the menu
  rows drop the two-line `renderOption` (description + "awaiting ack") for one line,
  `SO number - customer`, with the awaiting count as a small suffix only when non-zero.
  Wire: `so_numbers` may accompany an All run (`demand_class` absent). Dealer keeps no
  picker.
- D1b The From / To range is labelled "Project delivery range" and, on an All run WITH
  picked orders, windows the PROJECT legs only - owner: "this order range only is for
  project" (said of the picker). The retail book leg on such a run plans every open line.
  Captain ruling 23 Sep after review: the switch is `demand_class is None and so_numbers`
  (truthy), NOT "no demand class" - an unscoped ranged run with no picked orders (the
  chatbot's "plan for <from>..<to>", `tests/scm/test_reorder_window_start.py`) keeps
  today's windowed retail leg, and Dealer / Project runs keep today's reading too.
  `replan_run` derives the same switch from the stored run, so a re-run agrees.
- D2 Backend: `so_numbers` scopes the PROJECT legs of an All run - `horizon_committed_
  select_sql(demand_class=None, so_scoped=True)` narrows only the two OI legs; the SO-book
  retail leg and leg-2 admission (below level, moved in 180 d) are untouched, so the retail
  side still sizes from the level trigger with dealer outstanding inside `committed`.
  `_apply_project_supply_reduction` and `run_scope_oi_rows` (A3/C2) read `so_numbers`
  whenever set, not only under Project. Stamped on the run as today.
- D3 Sizing unchanged: `_emit_pool` / `_emit_cell` / `_emit_product` already add the
  project need on top of the retail sizing on an All run (`recommended =
  retail_recommended + pool_project_need`, buy-in-full R1a). Nothing new to size; the
  change is which project rows reach `committed`.
- D4 Sheet: Project qty / Delivery / Project-customer on an All run = the picked SOs' OI
  rows in window (A3, which keys off `so_numbers` regardless of class); Dealer o/s is the
  retail leg's own figure as today. The OI worksheet (C) prints the same picked rows.
- D5 A run with Demand = All and NO picked orders keeps today's behaviour (every project
  order in range) - the empty-list-means-nothing rule applies only when the picker was
  shown and everything unticked, same as Project.

Tests: pytest on `_planning_rows` for an All run with `so_numbers` (project row on an
un-picked SO absent from `committed`; retail-only product still admitted by leg 2 and
sized by level; picked SO's project need bought in full on top). Vitest: picker visible
under All, payload carries `so_numbers` with `demand_class` absent.

## Lane E - a discontinued product with a confirmed OI line enters the plan (owner ruling 23 Sep)

Measured 23 Sep (0921 copy): OI-001332 / prod OI-2609-0228, SO420946 line 16, SRTWC193 x 8 due
31/10/2026, raised, acknowledged, reconciled, no decision. The engine's own committed select
(`demand.horizon_committed_select_sql`) returns `project_confirmed_committed = 8` for it, yet the
plan of 23/09 10:25 (01/09 to 16/11, All) has no line: `_planning_rows`' admission WHERE
(`reorder_run_service.py` ~873) is `p.is_active = true AND p.is_discontinued = false AND
p.exclude_from_planning = false`, and SRTWC193 is `is_discontinued = true`. Owner: "we need to
admit discontinued product".

- E1 Admission: the `is_discontinued = false` predicate becomes `(p.is_discontinued = false OR
  EXISTS (SELECT 1 FROM cv_all c WHERE c.product_id = p.id AND c.project_confirmed_committed
  > 0))` - a discontinued product is admitted only by CONFIRMED project OI demand inside the
  run's scope (picked orders, window). `is_active` and `exclude_from_planning` stay hard.
  Leg 2 (below level, moved in 180 d) never admits a discontinued product.
- E2 Sizing: a product admitted this way is sized PROJECT-ONLY on every run kind (the
  `_project_only_cell` / `project_only` branches #1122 built), never a retail reorder-point
  or level top-up - it is discontinued, nobody restocks it for the shelf. `_planning_rows`
  stamps `discontinued_project_only = True` on its rows; `_emit_cell` / `_emit_pool` /
  `_emit_product` read `project_only = demand_class == "project" or row.discontinued_project_only`.
  A Dealer run has no project leg, so it never admits one. G10 (a product named in
  `product_ids` at Start Plan, buyer intent, normally exempt from the committed-demand
  gate) does not restore a discontinued product's retail sizing: `discontinued_project_only`
  sits in the same `or` as G10's `committed_gate_exempt` check and wins outright, because a
  discontinued product never gets its retail sizing back, named or not (review round 1, 23
  Sep). On an All run the discontinued product's project need is bought IN FULL, not netted
  against on hand (R1a applies here too) - a location holding 500 units with a confirmed
  row for 8 still buys 8.
- E3 The plan grid shows the row like any other; the sheet's Project qty and the OI worksheet
  already carry the line (`run_scope_oi_rows` has no discontinued filter).

Tests: pytest `_planning_rows` admits a discontinued product with a confirmed OI row in
scope, not one outside the window / on an un-picked SO / awaiting ack; not admitted on a
Dealer run; not admitted by leg 2 (below level, no OI); sizing = project need only on an All
run even when below level, on the pooled (`_emit_pool`) and PROD-shape product-grain
(`_emit_product`) bases too, and even when the product is named in `product_ids` (G10);
`test_reorder_plan_project_only.py` and `test_reorder_window_start.py` unchanged. No FE
change.

Branch: from `fix/order-sheet-cells` (= #1144's head, same `_planning_rows` region); PR base
retargeted to main by the captain right after #1144 merges and BEFORE the owner merges it.

## Lane F - confirmed OI need is bought in full on an All run too (owner ruling 23 Sep)

Measured 23 Sep (0921 copy, current code): OI-000749 CB4702 x 493 ORDER BACK, confirmed,
22/09/2026, SO421985. The engine's committed select returns 493 inside the 01/09 to 16/11
window, so the product is admitted; but on the All run (plan 23/09 10:51) the project need
is netted against 702 on hand (326 BRW + 376 BRW-IR) by the one-formula sizing, comes out
covered, and the row is hidden. On a Project run R1a buys the 493 in full. Owner: "should
apply the same for both".

- F1 On an All run, confirmed project OI need (`project_confirmed_committed`, the same
  figure Lane A's Project qty prints) is bought IN FULL on top of the retail sizing, never
  netted against on hand / open PO / SPO - R1a extended from Project runs to All runs. The
  retail leg keeps today's netting: retail is sized on `retail_net` (net with the project
  channel taken back out and confirmed project claims on stock removed), then the raw
  project need is added. `project_supply_reduction` (a confirmed Reserve / Borrow decision
  CS already recorded) still reduces the project need on EVERY path, including Project
  runs and the network aggregate - that IS the stock case CS chose, so R1a's "not netted
  against stock" carves it out rather than exempting it (fix round 3, captain ruling 23
  Sep: the original cut left a Project run's own `_project_only_cell` and `_emit_pool`'s
  project-only branch reading the raw figure, unreduced).
- F2 Applies in all three sizing paths (`_emit_cell` single member, `_emit_pool`,
  `_emit_product`) and to the network aggregate branch, which already added project need
  on top of a retail-only sizing before this lane (reviewer-confirmed, fix round 3) and
  now also applies the Reserve/Borrow reduction the same way. Dealer runs are untouched
  (no project leg). `_emit_product`'s ALLOCATION (where the buy is sited, not how much)
  must read the RETAIL aggregate's own per-location deficit plus each location's own
  project need, never the combined (project-folded-in) net's deficit - the latter reads a
  stock-covered OI location as un-short and sites the buy at an unrelated retail-short
  sibling instead (fix round 3, reviewer NOT READY finding).
- F3 The decision label and the Suggestion text keep reading "Stock: X / PO: Y / Buy: Z"
  where Buy now includes the full project need; the demand drill's Project figure equals
  the frozen `project_customers` sum (Lane A) and the OI worksheet's rows for that run.
- F4 Consequence stated to the owner: a product with 702 on hand and a confirmed row for
  493 buys 493 on an All run from now on, on every sizing path; purchasing pushes back
  through Request CS to reserve (#1120) when stock should cover it instead - and that
  Reserve then reduces the bought qty, wherever in the plan the row sizes.

Tests: pytest, All run: product with on hand > confirmed OI qty -> Suggested >= OI qty
(exactly the OI qty when retail trigger is off); ORDER BACK on a closed, delivered SO line
-> bought in full (owed uncapped rule kept); a confirmed Reserve decision on that row ->
reduced by the reserved qty, on every sizing path (single-member, pool, product-grain, a
Project run); the product-grain buy is allocated to the OI row's own location, not a
retail-short sibling; retail-only product unchanged; Dealer run unchanged;
`test_reorder_plan_project_only.py`, `test_reorder_plan_all_picked_orders.py`,
`test_reorder_window_start.py`, `test_reorder_one_formula*`, and
`test_channel_read_model.py::test_project_need_is_bought_in_full_on_top_of_the_stock_that_would_cover_it`
(was `::test_project_need_is_netted_once_against_the_stock_that_covers_it`, missed in the
first round and caught by the SCM CI shard) updated where they pinned the
netting on All runs (name each and cite this ruling). Lands in Lane E's worktree and PR
#1148 as its second slice (same functions), after Lane E's review fixes.

## Sequencing

A first (it changes the numbers purchasing reads), then B and C in parallel (independent
seams; B and C both touch `export_tasks.py` and `order_inquiry_worklist_service.py`, so
whichever merges second rebases). D after #1122 merges (it edits the same
`_planning_rows` branches); A3's helper is written so D needs no change to it.

## Rejected

- Freezing last cost onto `order_summary_row` (two columns + migration): the export-time
  join is the same shape the low stock report already uses for master data, and a cost that
  moved since the run is the cost purchasing pays today.
- A second XLSX writer for the OI worksheet: the sample the owner pasted is the worklist
  export's own layout, pixel for pixel.
- Making Project qty read the SO book instead: it would then disagree with the OI-only
  Project plan (PR #1122) the owner just ruled on.
- Scoping the OI worksheet to the engine's Buy verdict: the owner uses the sheet BEFORE the
  engine decides, so it must list the scope, not the answer.
