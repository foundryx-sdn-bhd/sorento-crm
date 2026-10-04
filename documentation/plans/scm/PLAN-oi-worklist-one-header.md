# PLAN - Order inquiries: one header per SO, hide cancelled, Was/Now after a redirect, cascade skips used rows, raised-by per row

Status: in review
UAC: `oi-worklist-one-header-acceptance-criteria.md`
Branch: `feat/oi-worklist-one-header` from `origin/main`
Worktree: `../sorento_crm-oi-one-header`
Origin: SO314593 on prod, diagnosed 17 Sep 2026 (rev 1 10:11 by the batch apply, rev 2 11:25
"Reconfirmed by CS." after the network outage; 24 pcs of SPO-2026/09-0036 auto-linked onto
the released 182 row at 10:11:15; OI-000477 + OI-000734 on one order).

## Measured facts (origin/main, 17 Sep)

All paths are `sorento_crm_backend/` unless stated.

- `app/services/project_order_inquiry_service.py`
  - `auto_place_for_products` (about line 6206): the row query filters `state in (raised,
    partly_linked[, placed])`, `verb in _LINKABLE_VERBS`, `ack_state in linkable_ack`. No
    `redirected_to_pool` filter. `_redirect_row_if_received` (about 1331) leaves the released
    row `partly_linked` when its received link is short of its qty (182 vs 158), so the
    cascade reads the 24 as need. `link_now` goes through the same method.
  - `derive_for_book_change` (about 2355) mints a synthetic `SOAmendment`
    (`from_version_kind='planning_change_batch'`) and calls `_write`, which mints a NEW
    `OrderInquiry` with `amendment_id = amendment.id`. The docstring says this avoids the
    DB singleton on `amendment_id IS NULL` colliding with `confirm()` in the same apply.
    `ensure_inquiry` (about 2499) returns or mints the null-amendment header. Nothing reads
    `from_version_kind='planning_change_batch'` back for routing (grep: only the ESB
    envelope and the fulfilment_planning code string, unrelated).
  - `refresh_for_decision` (about 646): the fresh row is built at about line 946
    (`raised_row = OrderInquiryRow(...)`) with no `previous_qty` / `previous_delivery_date` /
    `note`. `_settle_row_in_place` (about 1130) returns False after
    `_redirect_row_if_received` returns True (about 1196), so the line falls through to the
    netting loop, where `redirected_to_pool` rows are skipped (about 888).
  - `_write` (about 2430) stamps `acknowledged_by = actor_user_id` on every derived row.
  - `project_order_inquiry_import_service.py` line 1480 stamps `acknowledged_by = self.actor`
    on every migrated row; line 1407 stamps the header `raised_by`.
- `app/services/order_inquiry_worklist_service.py`
  - `_RAISED_BY_ID = func.coalesce(SOSupplyDecision.confirmed_by, OrderInquiry.raised_by)`
    (line 445); `_RAISED_BY_NAME = User.name` joined on it; the `raised_by` filter (about
    847) and `raised_at` sort use the same expression. The header `raised_by` is re-stamped
    on every reconfirm (comment at 431).
  - `list(...)` takes `state: Optional[str]` (single value) and `kind`; `_NOT_OWED_STATES =
    (cancelled, actioned)` is applied only under `kind` (about 853). Facets return
    `by_state` over all rows.
- `sorento_crm_frontend/app/(protected)/project-sales/order-inquiries/components/`
  - `orderInquiryWorklistColumns.tsx` line 877: `accessorKey: 'inquiry_no'` column. The
    Qty cell already renders the (i) Was/Now off `previous_qty` (test at
    `orderInquiryWorklistColumns.test.tsx:529`).
  - `OrderInquiriesClient.tsx` line 576 builds params `{ ...filters, kind }`; the State
    filter sends `state=<value>`. Column preferences persist via the list-query column
    config (`listing_key`), so a default-hidden column is the DataGrid's initial
    `columnVisibility`, overridden by a saved preference.

## Design (simplest thing that works)

### S1 - cascade skips used rows [BE]

One filter added to the row query in `auto_place_for_products`:
`OrderInquiryRow.redirected_to_pool.is_(False)`. Same seam serves Confirm's raise pass,
Link now, Auto link all and the PO confirm. No other change. AC-OH-10..12.

### S2 - raised-by per row [BE]

`_RAISED_BY_ID = coalesce(SOSupplyDecision.confirmed_by, OrderInquiryRow.acknowledged_by,
OrderInquiry.raised_by)`. Every row born since G4 is born acknowledged by its raiser
(confirm, `_write`, importer), so the middle term is the row's own person. The join to
`User`, the `raised_by` filter and the facet keep using the one expression. Update the
comment at line 431. AC-OH-20..23.

### S3 - one header per SO [BE + migration]

`derive_for_book_change` stops minting the synthetic `SOAmendment`; it calls
`ensure_inquiry(order, actor_user_id=actor)` and writes its rows there. `_write` gains an
optional `inquiry` argument: when given, it does not mint a header and does not touch its
`raised_by`. `derive_for_amendment` (OCN) is unchanged. The collision the old docstring
feared does not exist: `ensure_inquiry` returns the row `confirm()` wrote earlier in the
same apply, and both writers append rows to it.

Handover email: `_hand_to_purchasing(order, inquiry, n)` runs as today, once, for the
reused header.

Data migration `oioh_0001_one_header_per_so` (Alembic, `< 32` chars): for every
`order_inquiries` row whose `so_amendments.from_version_kind = 'planning_change_batch'`,
find or mint the same order's `amendment_id IS NULL` header (copy `company_id`,
`project_sales_order_id`, `state`, `raised_by`, `raised_at` from the oldest moved header),
`UPDATE order_inquiry_rows SET order_inquiry_id = <target>`, delete the emptied header, then
delete the synthetic amendment. Row ids, links, claims, handover records untouched. Runs in
SQL, idempotent, downgrade is a documented no-op. Verified on `sorento_ai_automation_0915_1900`
before the PR (OI-000737 on SO314593 is the known case there - corrected from "SO314595"
above, confirmed against the copy's own `sales_orders.so_number`).

**Verified on real data, 17 Sep.** `sorento_ai_automation_0915_1900` was busy (another
lane's own `:8082` backend, `sorento_crm-oi-cascade-early`) so the copy was taken via
`pg_dump -Fc | pg_restore --no-owner --role=sorento_crm` into a new `sorento_oioh_stack`
(dump needs no exclusive lock on the source, unlike `createdb -T`) - 26 restore errors, all
`pg_stat_statements`/`vector` extension-creation permission denials and the three pgvector
embedding tables that follow from them, none of them order-inquiry tables. Row counts
matched the source exactly before touching anything:
`projects.order_inquiry_rows` 11886, `projects.order_inquiries` 721,
`projects.so_amendments` 7, `projects.order_inquiry_links` 6207, `users` 69.

Before `alembic upgrade head` (`sorento_oioh_stack`, starting at `undo_0002_seed_undone`):
2 `order_inquiries` headers whose amendment is `planning_change_batch` (OI-000737 on
SO314593, 5 rows; OI-000738 on SO314594, 3 rows - both amendments published 16 Sep), out
of 7 `planning_change_batch` amendments total (the other 5 predate this and never had a
header of their own - nothing for the migration to touch, since it walks from the header,
not the amendment). Both orders already had their own null-amendment header (OI-000477 on
SO314593 with 14 rows, OI-000539 on SO314594 with 13 rows).

**Re-verified after review round 1 rewrote the migration as plain SQL (`_fold`, no
`Session`/service import).** The run above predates that rewrite - it ran the old
`Session(bind=op.get_bind())` implementation. A second ephemeral copy,
`sorento_oioh_mig` (same method: `createdb -O sorento_crm sorento_oioh_mig && pg_dump
-Fc sorento_ai_automation_0915_1900 | pg_restore --no-owner --role=sorento_crm -d
sorento_oioh_mig`, `sorento_ai_automation_0915_1900` was free by then), confirmed the
SQL `_fold` produces the byte-identical result before it was dropped:

`alembic upgrade head` ran in 1.47s wall (`time`), logging `oioh_0001: folded 2
planning_change_batch header(s)` - same two headers, same counts before (2 batch
headers / 7 batch amendments / 11886 rows / 6207 links / 63200 claims / 0 tasks
anywhere in this data) as the first run.

After: 0 headers left on a `planning_change_batch` amendment; the amendment count dropped
7 -> 5 (only the two WITH a header got deleted, matching "delete the synthetic amendment"
only when its header actually existed to fold). OI-000737 and OI-000738 no longer exist.
OI-000477 now carries 19 rows (14 + 5), OI-000539 carries 16 (13 + 3) - `raised_by` and
`raised_at` on BOTH unchanged byte-for-byte against the pre-migration source (same
`9993276c-...` actor, same timestamps to the microsecond). Global row/link/claim counts
untouched: `order_inquiry_rows` still 11886, `order_inquiry_links` still 6207,
`scm.order_link_claim` still 63200. Task counts on OI-000477 and OI-000539 are 0 before
and 0 after - this copy carries no `projects.tasks` rows at all, so the round-2
task-collision logic (re-point vs. delete, `_fold`'s own `projects.tasks` step) ran its
query and found nothing to move, which the unit tests (`test_migration_folds_
planning_change_batch_headers_AC_OH_34`, `test_migration_repoints_task_when_target_
has_none_review_round_2`) are what actually exercise both branches. Spot-checked five
of OI-000477's links (SPO documents) - `row_id` unchanged, still pointing at the same
row, now under the new `order_inquiry_id`. `sorento_oioh_mig` dropped after this run;
`sorento_oioh_stack` (below) is the one left in place.

`sorento_oioh_stack` is left in place as the browser-verification DB (Phase 3). Its two
`email_outbox` rows in `pending` (both real prod addresses, `person34@example.com`,
subjects naming SO314595 - a different, unrelated SO on the same copy) were set to
`status = 'cancelled', cancel_reason = 'lane copy, never send'`. One further row was
already sitting in `sending` from 7 Sep (a `ticket_comment_mention` to a real Gmail
address, unrelated to this lane) - cancelled the same way as a precaution, since a stack
DB a browser pass will run a live backend against is exactly where a stuck `sending` row
could get picked up and actually sent; flagged to the captain as a judgment call beyond
the literal "pending" instruction, not silently done. AC-OH-30..35.

### S4 - Was/Now on the fresh row after a redirect [BE]

`refresh_for_decision` collects, per line, the rows `_redirect_row_if_received` flipped IN
THIS CALL (a local list filled where the settle declines, about line 1196; the redirect
method returns the fragment it wrote, or the caller reads `row.note`). When it builds
`raised_row`, if that list is non-empty: `previous_qty` = sum of their `qty`,
`previous_delivery_date` = the first one's `delivery_date`, `note` = `Replaces <qty> used;
<document> received <date|in full> into <location>` joined with `; ` per released row. Rows
released in an earlier decision are not re-stamped. The existing FE (i) then shows it.
AC-OH-40..43.

No DELAY row for that line (R4 revised): `refresh_for_decision` already returns
`settled_in_place` and `planning_change_service.apply` passes it to `_oi_demand_rows` to skip
the DELAY / ADVANCE reaction for a settled line. The redirected line ids join that same list.
One seam, no new flag. AC-OH-44..45.

### S5 - hide cancelled by default [BE]

In `list(...)` and the totals it feeds: when `state` is None, add
`OrderInquiryRow.state != INQUIRY_CANCELLED`. `state='cancelled'` still returns only
cancelled. Facets unchanged. Matrix, month strip and cards untouched. AC-OH-50..54.

### S7 - Filters popover scrolls [FE]

The Filters popover content in `OrderInquiriesClient.tsx` gets a viewport-bounded max height
and `overflow-y: auto` (the shared popover primitive's own prop if it has one; otherwise a
class on the content). No layout change. AC-OH-70. Phase 1.

### S8 - Worklist speed, measured first [BE]

Before touching code: boot the lane backend on `sorento_ai_automation_0915_1900`, replay the
page's load requests with the prod query string, record each request's time in this plan,
EXPLAIN ANALYZE the slowest. Likely candidates (unverified): the facets query (`by_state`,
`raised_by`, `locations`, `agents`, `kinds` over the full row set), the month strip's
`_RAISED_DAY` timezone cast per row, or `_quantity_flow_by_so_line` lateral joins. Fix the
measured hot spot only (an index, a narrowed facet, or one fewer round trip). AC-OH-80..81.

**Measured (17 Sep, `sorento_ai_automation_0915_1900`).** No local test API key exists on
this copy (the standing "prod-copy lane DB lacks local test API key" gotcha - inserting one
is owner-only) and guessing a real user's password is not something to attempt, so the two
requests `OrderInquiriesClient.tsx` actually fires on load (traced through the component:
`ack=all` clears the ack filter client-side and `link_up_to` is stored for the Auto-link-all
action only, never sent on a GET - the page's real load is `list_rows` + `summary`, no
matrix on the List view) were timed by calling `OrderInquiryWorklistService.list_rows` /
`.summary` directly against a session scoped to the Sorento company - same SQL, same
company scope the HTTP routes apply, minus the auth hop:

| Request (page 1, limit 25, sort `delivery_date` asc, no filters) | Before (cold / warm) |
| --- | --- |
| `list_rows` | 1408.8 ms / 493.8 ms |
| `summary` | 1532.0 ms / 1308.1 ms |

`summary`'s own facets, broken down (warm): `state_rows` 12.5 ms, `_by_month` 10.3 ms,
`_suppliers` 213.7 ms, `_projects` 8.8 ms, `_raised_by` 11.1 ms, `_locations` 60.1 ms,
`_agents` 11.3 ms, `_acks` 14.5 ms, `plan_link_horizon` 0.8 ms - and **`_kinds` 912.6 ms**,
by far the widest margin over everything else combined. `EXPLAIN (ANALYZE, BUFFERS)` on
`_kinds`'s own query named the hot spot: `_stage_rows` built `incoming`/`purchased`/`buy`
by calling `_incoming_qty()` (itself built from two correlated subqueries plus
`_derived_cover_qty()`, an EXISTS + a `spo_allocations` scalar subquery) TWICE - once for
the `incoming` column, once again inlined inside `_purchased_qty()`'s own
`qty - incoming_qty()` - and `_UNLINKED_QTY` added a third, independent `_linked_qty()`
subquery on top. Worse, restructuring the three into a plain (non-materialized) inner
subquery changed nothing: Postgres pulled the subquery up and re-evaluated
`_derived_cover_qty()`'s `ix_spo_allocations_spo_product_warehouse` scan (cost ~581,
~554 buffer reads) three times regardless (three `SubPlan`s, each `spo_allocations`/
`spo_allocations_1`/`spo_allocations_2`, ~296k-320k buffer hits apiece) - the fix had to
stop the planner from flattening it back, not just stop the Python code from asking twice.

**Fix:** `_stage_rows`'s inner query is now a `WITH ... AS MATERIALIZED` CTE
(`.cte().prefix_with("MATERIALIZED")`), an explicit optimizer fence Postgres 12+ honours -
each correlated subquery (SPO-linked, PO-linked, derived-cover, any-linked) computes
exactly ONCE per row, and `incoming`/`purchased`/`buy` are plain arithmetic over the
already-materialized columns. `EXPLAIN` after: one `CTE Scan`, one derived-cover
`SubPlan`, not three. `_incoming_qty()`/`_purchased_qty()` themselves are untouched - the
other caller (`kind=po`'s row-level filter, S4/S5 unaffected) never showed the
duplication, since it runs the formula once over a `WHERE`, not company-wide.

| Request | After (cold / warm, 3 runs) |
| --- | --- |
| `list_rows` | 620-962 ms / 476-742 ms |
| `summary` | 717-1073 ms / 675-870 ms |
| `_kinds` alone (warm) | 348-567 ms (was 912.6-1516.4 ms) |

Both requests now sit consistently under 1.5 s, cold or warm (AC-OH-81). `list_rows`'s own
cold-run number was already a cache-warming artifact rather than an algorithmic cost (its
warm number was fine before AND after) - not touched, since S8 fixes the MEASURED hot spot
only. Guard suites re-run clean: `test_order_inquiry_kinds.py`,
`test_order_inquiry_matrix.py`, `test_order_inquiry_derived_spo.py`,
`test_order_inquiry_bundles.py`, `test_order_inquiry_worklist.py` - 142 passed, 1
pre-existing failure unrelated to S8 (see the coder's report: an S5 side effect on a test
outside this lane's named scope, reported separately, not fixed here).

### S6 - Order inquiry column hidden by default [FE]

`orderInquiryWorklistColumns.tsx` / `OrderInquiriesClient.tsx`: initial `columnVisibility`
marks `inquiry_no` hidden; a saved column preference wins. AC-OH-01. Phase 1 (no backend).

### S9 - a local buy is not purchasing's job [BE]

Found via the owner's hand test on `sorento_oioh_stack`: SO314595's TPE-9204 line was
decided LOCAL (`origin == "local"` in `refresh_for_decision`'s `buy_lines`, set by
`buy_origin_by_product` in `project_supply_service.py`) but its migrated `raised` row
(no `supply_decision_id`) stayed untouched at the old qty, AND a DELAY row was written
for it - the old S3 (`PLAN-local-supplier-oi-routing.md`) rule left a local line's row
alone entirely, and `_retire_uncovered_rows` never reaches a row with no
`supply_decision_id` either (reads as the amendment path to it), so nothing in the
existing code ever cancels it once a line goes local.

R9: `refresh_for_decision`'s own `buy_lines` loop, at the SAME `origin == "local"` check,
now cancels the line's still-`raised` ORDER/ORDER_BACK rows ("Superseded by revision N"),
raises nothing fresh, and joins `settled_in_place` so the reaction pass raises no
DELAY/ADVANCE row either - one seam, the entry still stays in `buy_lines` for
`_retire_uncovered_rows`'s own `covered` set exactly as before. AC-OH-90..91.

## No-motion list

Nothing animates. No new component, no new state.

## Testing seams

- S1: pytest on `auto_place_for_products` with a seeded product, one released row with a
  short received link, one fresh raised row, one open SPO allocation.
- S2: pytest on `OrderInquiryWorklistService.list` with a migrated row and a re-stamped
  header.
- S3: pytest on `planning_change_service.apply` (existing fixtures) asserting one header;
  migration test on a scratch DB with a synthetic `planning_change_batch` header.
- S4: pytest on `refresh_for_decision` through `ProjectSupplyService.confirm` with a
  received link.
- S5: pytest on `list` with and without `state`.
- S6: vitest on the columns module / client initial visibility.
- E2E: agent-browser evidence run on the lane stack (AC-OH-60..63).

## Slices and order

Phase 1: S6, S7. Phase 2 (tester reds first, one coder): S1, S2, S5, S4 (+ R4), S3 (+ migration), S8 (measure, then fix).
Phase 3: reviewer (Opus) with kill tests on AC-OH-10, AC-OH-21, AC-OH-40; browser
verification; guide-writer. No security-reviewer (no auth, ingest, upload or scoping change).

## Prod follow-up (owner, after deploy)

1. Migration moves OI-000734 and siblings onto their SO headers on `alembic upgrade head`.
2. SO314593 B2154-NL: on the used 182 row, chip `unlink` the 24 of SPO-2026/09-0036, then
   Actions > Auto link all so the live 220 row takes it.

## Backlog

- OCN amendment headers per amendment (trigger: a project-authored SO shows two headers).
- Drop the order inquiry number entirely (trigger: unused in emails/sheets for a month).
