# Browser evidence - oi-worklist-one-header

Run date: 17 Sep 2026. Lane stack: FE `http://localhost:3080`, BE `http://localhost:8080`,
DB `sorento_oioh_stack` (15 Sep prod backup copy, already migrated - OI-000737 folded into
OI-000477 on SO314593). Coder HEAD at start of the walk: `d4a1bd681`; the coder kept pushing
review-round-2 commits during the walk (last seen `e080f68c4`), none touching the areas
exercised below (Filters popover, Columns menu, Was/Now tooltip).

Tool: `agent-browser@0.27.0`, session name `oi-one-header-tester` (isolated from the shared
daemon). Login via sidebar from `/`, never a deep URL.

## Data prep on the copy (`sorento_oioh_stack`)

1. Deleted the saved column preference for `person38@example.com` on listing key
   `projects.projects.view::order-inquiry-worklist` (table `user_list_column_configs`), so
   AC-OH-01/62 show the true default.
2. SO314593 / B2154-NL (`so_line_id 0ecc19dc-f2fc-45e5-ba0d-4ae4de4f2488`) sat post-confirm
   from a 16 Sep run (a 220 ORDER row, a DELAY row, the migrated 182 row already flagged
   `redirected_to_pool`). To replay the AC-OH-60 pre-state:
   - Did **not** run the UI "Reset planning" action - read `planning_reset_service.py` first:
     it deletes every `order_inquiry_row` for the WHOLE order (all 22 lines), which would have
     destroyed the very migrated row (`9e398088-a3bf-4525-9063-b2f9d42cade3`) the next step
     needs to still exist. Running it would have contradicted the brief's own follow-up SQL
     ("for the migrated row id 9e398088..."), so I went straight to the SQL instead.
   - SQL (transaction, both statements together):
     `UPDATE projects.order_inquiry_rows SET redirected_to_pool=false, state='partly_linked',
     note=regexp_replace(note, '; SPO-2026/01-0143 received.*$', '') WHERE id='9e398088-...'`
     and `DELETE FROM projects.order_inquiry_rows WHERE so_line_id='0ecc19dc-...' AND id <>
     '9e398088-...' AND (supply_decision_id IS NOT NULL OR verb='DELAY')` - removed the DELAY
     row (`bc4e015e-...`) and the 220 ORDER row (`4b717523-...`).
   - Verified with a SELECT: only the migrated row remains for that line, `redirected_to_pool
     = f`, note stripped, qty 182, `Taken by PO/SPO` 158, `Remaining` 24 - matches "one sheet
     row linked 158 to a received SPO" from the AC.
   - The project mirror line (`projects.sales_order_lines` id `0ecc19dc-...`) already showed
     the stale qty/date (182 / 01/06/2026) while the core line (`sales_order_lines` id
     `cb5753c9-...`) already showed 220 / 01/03/2027 - the "mirror stale on qty and date"
     precondition the AC asks for was already present in the copy, untouched by me.
3. Confirmed via API-key check that `EXTERNAL_API_KEY` has no seeded row on this copy (known
   gotcha, `project_prod_copy_db_lacks_local_test_api_key.md`) - not needed since all
   verification went through the browser session, not curl.

## AC-OH-60 - PASS, walked end to end after two repairs on the scratch copy

### Repair 1: the board-load crash (captain's call, `#985`, not fixed in this lane)

Walked: Supply Chain > Project Demand > Fulfilment Planning > searched SO314593 > selected
the row > "Plan SO314593". The planning board failed to load:

```
The planning board could not be loaded
(psycopg2.errors.InvalidParameterValue) cannot get array length of a scalar
[SQL: SELECT projects.so_supply_decisions.id ... FROM projects.so_supply_decisions
WHERE projects.so_supply_decisions.project_sales_order_id IN (%(project_sales_order_id_1_1)s::UUID) AND
projects.so_supply_decisions.state = %(state_1)s AND
projects.so_supply_decisions.undo_journal IS NOT NULL AND
jsonb_array_length(projects.so_supply_decisions.undo_journal) > %(jsonb_array_length_1)s AND
projects.so_supply_decisions.confirmed_at IS NOT NULL AND
projects.so_supply_decisions.company_id IN (%(company_id_1_1)s::UUID)]
```

Root cause: `so_supply_decisions.undo_journal` for this order's decision
(`ff18c368-008d-40e9-a1f1-2b6edf7ec2da`, revision 1) held the JSON literal `null`
(`undo_journal = 'null'::jsonb` true, `IS NULL` false), not SQL NULL - a `#985` defect
(`Core .values(undo_journal=None)` on a JSONB column stores the JSON literal). Captain's
call: fix the data on this copy, not the code, here.

`update projects.so_supply_decisions set undo_journal = NULL where jsonb_typeof(undo_journal)
= 'null'` - 1 row. Re-ran the board's own predicate in psql: 0 rows, no error. Board reloads
cleanly (`AC-OH-60-board-loads-after-undo-journal-fix.png`).

### Repair 2: the 16 Sep decision, removed entirely (captain's scope call)

B2154-NL still could not be walked - `so_supply_decisions` row `ff18c368` (16 Sep) still
listed it (and 7 other lines) as `Decided rev 1`, so Amend had nothing to save (suggested ==
decided already). Captain's call: remove that decision entirely so every line reads
undecided, then confirm B2154-NL alone.

Backed up first (`public.zz_bak_so314593_*`, kept on the scratch copy): `so_supply_decisions`
(1 row), the 7 `order_inquiry_rows` carrying `supply_decision_id = ff18c368` (8th was
B2154-NL's, already deleted in the earlier data-prep pass), the 8 `so_line_allocations` rows
(`decision_id = ff18c368`), the 1 `order_inquiry_links` row hanging off those rows, and the 4
DELAY rows raised `>= 2026-09-16 22:00` (the 16 Sep batch reactions; the 15 Sep migrated rows
are untouched). Then, in order:
1. `delete from projects.order_inquiry_rows where supply_decision_id in (select id from
   projects.so_supply_decisions where project_sales_order_id = '937b6647-...')` - 7 rows.
2. `delete from projects.so_line_allocations where decision_id in (...)` - 8 rows (`decision_id`
   found via `grep decision_id app/models/project_so.py` -> `so_line_allocations`, line 1431).
3. `delete from projects.so_supply_decisions where project_sales_order_id = '937b6647-...'` -
   1 row.
4. `delete from projects.order_inquiry_rows where order_inquiry_id = '4aa81b68-...' and verb
   in ('DELAY','ADVANCE') and created_at >= '2026-09-16 22:00:00'` - 4 rows.

Verified after: only the 6 migrated rows (15 Sep) remain on the order's header, none carrying
a `supply_decision_id`, B2154-NL's own row unchanged (182, `partly_linked`,
`redirected_to_pool=false`, note stripped).

### The walk

Reloaded "Plan SO314593": every line now reads `Decided 0` (screenshot
`board-all-undecided.png` in the run, not committed - the committed shots are the milestones
below). B2154-NL's row checkbox is still disabled (it is a "received"/redirect-eligible line,
excluded from bulk select regardless of decision state) - its expanded panel showed `DECISION
Amended to nothing / 220 short`, `Amend` enabled. Clicking **Amend**, toggling **Buy the whole
line** on (-> `DECISION Buy 220`) left **Save decision** still disabled; the missing piece was
the **"WHY THIS DIFFERS"** reason field - once filled, **Save decision** enabled and the save
went through ("Line 1 saved · 1 to confirm"). Ticked nothing else, clicked **Confirm (1)** ->
"Confirm 1 line across 1 order?" -> Confirm. Toast: "1 line confirmed · 0 transfers proposed ·
1 inquiry row" / "SO314593: confirmed as revision 2 (1 purchase row handed over)"
(`AC-OH-60-board-confirmed-revision2.png`). No console/network errors at any step.

### Verification against the worklist (Procurement > Supply Chain > Order Inquiries, `314593`)

| Assertion (from the brief) | Observed |
| --- | --- |
| One ORDER 220 row with (i) Was 182 | Present. New row `2acb7bfb-...`: qty 220, `previous_qty` 182, `previous_delivery_date` 2027-03-01. "Show what changed" dialog: "Was 182 / Now 220", "Was 01/03/2027 / Now 01/03/2027" (`AC-OH-60-wasnow-tooltip-was182-now220.png`) |
| note naming SPO-2026/01-0143 received | DB `note`: `"Replaces 182 used; SPO-2026/01-0143 received in full into BRW-IR"` - exact match to AC-OH-40's format |
| 182 row `used`/greyed | Migrated row `9e398088-...`: `redirected_to_pool=true`, note updated to `"...auto: autocount linkage; SPO-2026/01-0143 received in full, goods are BRW-IR stock, released at revision 2"`; worklist shows it greyed with a `used` badge |
| No DELAY row | Only 2 rows exist for `so_line_id 0ecc19dc-...` (182 used, 220 fresh) - no DELAY row raised, as expected (no planning change involved) |
| One order inquiry number | All 18 rows across all 9 lines on this order (including the 7 re-confirmed as revision 2 and their 5 now-`cancelled` predecessors) carry `inquiry_no = OI-000477` |
| Raised by = migrator on the 182 row | `acknowledged_by` unchanged: `9993276c-...` = Jayson Foundryx (the migrator) |
| Raised by = the confirming user on the 220 row | `acknowledged_by = 5994214c-...` = the test login; worklist "Raised by" column shows "Teh Jayson" |
| No new link on the used row | `order_inquiry_links` for `9e398088-...` unchanged: still the single original row (SPO-2026/01-0143, qty 158, `auto=true`, `linked_at` 15 Sep) - worklist chip unchanged, "Confirmed by Jayson Foundryx 16/09/2026" |
| Open SPO linked to the 220 row, or not linked at all | Not linked (`Not linked` in the worklist, no `order_inquiry_links` row for `2acb7bfb-...`). Tried "Actions > Link selected (1)" manually: `"0 linked, 1 after the link horizon"` - the row's required date (01/03/2027) sits past the page's default `link_up_to=2026-12-31` guard, so the manual linker declines it (expected behaviour, not a defect). The brief's own wording accepts this ("or not linked at all") |

Final worklist screenshot: `AC-OH-60-worklist-final-220-and-182-used.png` (both B2154-NL rows
visible: 220 with the (i) icon, 182 `used` and greyed, directly below).

**Verdict: PASS.** Every assertion in the brief is satisfied. Scratch-DB-only changes (backup
tables `public.zz_bak_so314593_*` left in place for anyone who wants to inspect or restore
them; not touched on any other environment).

## AC-OH-61 - PASS (was FAIL; the coder shipped the State field, commit `9dc1424f9`)

### First pass (superseded) - DEFECT: no State filter existed

Procurement > Supply Chain > Order Inquiries > searched `314593` > opened Filters. Read the
full field list off the DOM (`label` elements) with the popover open: Location, Agent, SO
month, PO number, SPO number, Linked, Confirmed, Supplier, Project, Raised by, Raised on.
**No "State" field.** The "Confirmed" field (options: Confirmed/Changed/Rejected) is `ack_state`,
not `state` - it has no Cancelled option. Grepped
`OrderInquiriesClient.tsx` for `state`/`Cancelled`: zero matches wired to a filter control (only
an unrelated `row.state !== 'cancelled'` guard on row selection). The backend already had the
capability (`order_inquiry_worklist_service.py` accepts a `state` query param and computes
`by_state[cancelled]` per AC-OH-51/52), but nothing in the FE read or wrote it.

Screenshot: `AC-OH-61-DEFECT-filters-popover-no-state-field.png` (full field list visible with
the popover open, no State).

Wrote the RED test-first contract for this in `OrderInquiriesClient.test.tsx`
(`describe('AC-OH-61: ...')`, 3 tests: label + option labels/counts off `by_state`, selecting
sends `state=` to the worklist call, a URL-seeded value round-trips and clearing drops the
param). Committed as `test(scm): red vitest for the worklist State filter (AC-OH-61)`
(`d8e3c9a9a`).

### Second pass - the coder shipped it (`9dc1424f9`), vitest green, re-walked in the browser

`npx vitest run OrderInquiriesClient.test.tsx -t "AC-OH-61"`: all 3 tests that were red now
pass; the full file (45 tests) is green, no regressions.

Browser walk (HMR on `:3080`, no restart): Procurement > Supply Chain > Order Inquiries >
searched `314593` > opened Filters > scrolled the popover to the **State** field, right after
Confirmed as the contract asked for - `AC-OH-61-filters-state-field-present.png`. Opened its
dropdown: `Raised (6)`, `Partly linked (1)`, `Actioned (1)`, `Cancelled (5)`, `Linked (5)` -
the same state labels `OrderInquiryStatePill`'s `STATE_LABEL` map uses elsewhere, each with
the live count for this SO (`AC-OH-61-state-dropdown-options.png`). Picked **Cancelled (5)**:
the list narrowed to exactly 5 rows (`1 - 5 of 5`), the `MAR 27` tab count dropped to `(5)`,
`Filters 1` badge appeared, and `network requests` confirmed both
`GET .../project-sales/order-inquiries?...&state=cancelled` and the summary call carrying
`state=cancelled` (`AC-OH-61-state-cancelled-5-rows.png`). Cleared it via the field's own "x"
(`Clear selection`): the URL's `state=cancelled` param disappeared, the list returned to
`1 - 13 of 13` with the same 12/1 month-tab split as before filtering
(`AC-OH-61-state-cleared-13-rows.png`). No console/network errors at any step.

**Verdict: PASS.** Selecting State = Cancelled shows the cancelled rows for the SO; clearing
it makes them vanish, exactly as the brief describes.

## AC-OH-70 - PASS (the popover scrolls; "last field" wording has drifted, not a defect)

At a 1280x700 viewport, opened Filters: the popover's own inner content div
(`max-h-[60vh] space-y-3 overflow-y-auto`, confirmed via computed style: `maxHeight: 420px`,
`overflowY: auto`, `scrollHeight: 758`) visually shows only Location..Linked at first paint
(screenshot `AC-OH-70-filters-700h-clipped-before-scroll.png`), because the box is genuinely
taller than 420px of content. Scrolling that specific container (not the page - the page-level
`scroll` command scrolls the window, which does not reach a nested `overflow-y-auto` div)
reveals Confirmed, Supplier, Project, Raised by, and the true last field, **Raised on** (a date
input), fully visible and interactive at the bottom of the popover (screenshot
`AC-OH-70-filters-700h-raised-on-reachable-after-scroll.png`). Nothing clips; the popover
scrolls independently of the page.

Note for the plan/UAC: AC-OH-70's own text says "the last field (Confirmed)" - that was true at
the point the S7 commit (`621fb4f25`) landed, but later commits (Excel-parity, PO/SPO pairing
work) added Supplier/Project/Raised by/Raised on after Confirmed, so Confirmed is no longer the
last field. The scroll mechanism still reaches whatever the actual last field is, so the AC's
*intent* (nothing clips, everything reachable) is satisfied - only the field name in the AC text
is stale.

**Verdict: PASS.** Flagging the stale field name as a docs nit, not a defect.

**Re-verified after State shipped (`9dc1424f9`):** at 375px, opened Filters, scrolled the same
`max-h-[60vh] overflow-y-auto` container - Agent through Confirmed visible first, scrolling
further reveals **State** cleanly between Confirmed and Supplier with nothing clipped or
overlapping the page underneath (`AC-OH-61-AC-OH-63-375-filters-state-visible.png`, also
covering AC-OH-63's 375px requirement for this popover). The new field did not break the
scroll contract.

## AC-OH-01 / AC-OH-62 - PASS

Columns menu on first load (no saved preference, per the data-prep step above): "Order
Inquiry" is unticked while every other column is ticked
(`AC-OH-01-AC-OH-62-columns-menu-order-inquiry-unticked-default.png`). Ticking it adds an
"Order inquiry" column showing `OI-000477` on every one of the 17 visible rows for SO314593
(`AC-OH-62-order-inquiry-column-shown-after-tick.png`) - also visual confirmation of R1/R5
(one header per SO: every row of this SO carries the same inquiry number). Unticked it again
afterwards per the brief's step 3 instruction.

**Verdict: PASS** for both ACs.

## AC-OH-02 - PASS

Clicked the (i) "Show what changed" affordance on the SRTWB245 row (qty 280, `previous_qty`
182 set, `redirected_to_pool` false - a different, unrelated line on the same SO that already
carries a Was/Now from the 16 Sep confirm). Dialog: "SRTWB245 / Changed / Changed 17/09/2026"
with a Qty row (Was 182, Now 280) and Date row (Was 01/03/2027, Now 01/03/2027) - the existing
Was/Now affordance, exactly as the AC describes (no new component).

Screenshot: `AC-OH-02-was-now-tooltip-srtwb245.png`.

**Verdict: PASS.**

## AC-OH-63 - PASS

1280px (viewport screenshot, not full-page - full-page capture produces a compositing
artifact where the sticky topbar double-paints over the page header, a screenshot-tool
quirk, not a real rendering bug): list usable, DataGrid horizontally scrollable within its
own container, nothing overlapping (`AC-OH-63-list-1280.png`).

375px: page reflows to a single column, summary cards wrap, Filters/Columns/Actions buttons
stack, the DataGrid keeps its own horizontal scroll for the wide table without clipping the
page (`AC-OH-63-list-375.png`).

Console/errors checked after every navigation and interaction in this run: no uncaught errors,
no unexpected console output beyond routine `[debug] JWT token extracted successfully` lines.

**Verdict: PASS.**

## Summary

| AC | Result |
| --- | --- |
| AC-OH-01 | PASS |
| AC-OH-02 | PASS |
| AC-OH-60 | PASS - walked end to end on the scratch copy after two captain-authorized data repairs (`#985` undo_journal fix; the 16 Sep decision removed and B2154-NL confirmed alone) |
| AC-OH-61 | PASS - was FAIL (no State filter in the FE, red vitest `d8e3c9a9a`); coder shipped it (`9dc1424f9`), vitest green, re-walked State = Cancelled / clear in the browser |
| AC-OH-62 | PASS |
| AC-OH-63 | PASS |
| AC-OH-70 | PASS (AC text names a stale field, not a defect); re-verified with the new State field in place, still scrolls cleanly |

Coder HEAD at end of walk: `342df5d2a` (`9dc1424f9` feat + its guide-note commit; none of the
commits since touched the fulfilment-planning board's Amend/Confirm flow or the undo-journal
query exercised in AC-OH-60).
