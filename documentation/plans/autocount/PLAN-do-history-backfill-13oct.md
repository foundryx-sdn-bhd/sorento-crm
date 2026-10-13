# PLAN: DO history backfill 2023-01 to June 2026, with "assumed delivered" tracking (DO-HISTORY)

Status: **plan, awaiting owner OK (no code yet)**. Track: full (one migration column, a backfill
script that writes prod data; no RBAC change, no new external ingest surface). PR #1633, branch
`claude/do-history-backfill-7e7enk` (the sandbox's designated branch; the brief named
`crew/do-history`). UAC: `do-history-backfill-13oct-acceptance-criteria.md`. Owner card:
`DO-HISTORY-card.md`.

Paths: `be/` = `sorento_crm_backend/`. Cited on `main` `64dfd86c`.

## 0. The ask

Owner, 13 Oct: "for DO we are reading from hapi from AutoCount; we should have DO data in Sorento
CRM that links to SO. I think now we only import DO since July; after this I need to import all
from AutoCount since 2023, but that will require all of them to be like fully delivered, because
I don't have the order tracking for them prior to July, so we need a plan to migrate all these."

## 1. Facts

### 1.1 What could not be measured here

This lane runs in a cloud sandbox with no access to the dev DB or a prod clone (the crew contract
forbids it). **Every count in this plan is either quoted from an earlier measured source (cited) or
is a query in section 6 for the crew to run read-only on the prod clone before the owner signs off.**
No count below is invented.

### 1.2 How a DO lands today

- Two doors, one ingest. The shared service pushes `POST /api/v1/external/ingest/delivery_orders`
  (contract 2.7, `PLAN-autocount-cross-repo-contract.md` section 13). The CRM "Pull from AutoCount"
  button builds a FoundryX snapshot and applies it (`be/app/services/autocount_pull_service.py`,
  `be/app/tasks/autocount_pull_tasks.py:868-889`). Both call
  `AutocountDocIngestService.ingest("delivery_orders", ...)`.
- Idempotent per `(company_id, source_book, doc_key)` (partial unique index, `be/app/models/order.py:500-505`;
  `_find`, `be/app/services/autocount_doc_ingest_service.py:721-745`). Lines match by `dtl_key`.
  An older `LastModified` is `unchanged` + `stale_ignored` (`_is_stale`, `:747-754`).
- A row with the same DocNo and `doc_key` NULL (an Order Tracking Excel row) is **adopted**: it keeps
  its id and every tracking column; old lines are matched by (product, warehouse, qty) and the
  unmatched ones are deleted (`:740-745`, `:1122-1135`, `:1310-1342`).
- A new DO gets `order_status_id` = the `new` status (`:831-840`, `:1111`). The ingest never writes
  `actual_delivery_date` or any tracking column (`order_field_ownership.py:43-54`, assert at `:1072`).
- The CRM has **no date floor**: the contract says the shared service backfills by DocDate from
  1 Jan 2023 (contract :549-553, :751-756; ruling Q6 in `PLAN-autocount-grn-do-ingest-29sep.md:46`).
  So "only since July" is where the shared service's DO job started, not a CRM limit.
- Books: `db1` = Sorento (ruling V9); Mocha is AutoCount `db2`, company code `MOCHA`
  (`PLAN-retail-sales-reports-26sep.md:567,1224`, contract :25).

### 1.3 How a DO line links to an SO line, and why the crew saw "0"

- `order_lines.from_dtl_key` is AutoCount's `FromDocDtlKey` **as sent**; the resolved link is
  `order_lines.sales_order_line_id` (`be/app/models/order.py:540-569`).
- AutoCount sends `FromDocDtlKey = 0` on every DO line (3,841 of 3,841 in the 01-03 Sep snapshot,
  3,718 with `FromDocType "SO"`; `PLAN-do-so-line-link-1oct.md:10-11`). Since #1409, `_link_key`
  stores 0 as NULL (`autocount_doc_ingest_service.py:190-193`, applied at `:507`).
- So "from_dtl_key = 0" is expected and is not the link. The link is made by the fallback
  `_do_so_line` (`:859-878`): exact DtlKey if one is ever sent, else **the one line of this product in
  the SO numbered `FromDocNo`**. Several lines of that product in that SO resolve to none, with
  `so_line_unresolved` (no position tie-break: SO `line_no` was NULL then, #1400).
- A line whose SO is not in the CRM yet is retried by the waiting-link fill at the end of each batch,
  bounded to the newest 500 lines (`MAX_WAITING_LINKS`, `:95`, `:1391-1416`).
- Why a line can be unlinked on dev, in order of likelihood (section 6 query Q-B splits them):
  1. the dev rows predate #1409 and were never re-pushed (stored `from_dtl_key = 0`; the waiting fill
     treats them as no key, `:1413`, but only the newest 500 per batch);
  2. the SO named by `FromDocNo` is not in the CRM in that company;
  3. the product appears on several lines of that SO (ambiguous, by design no guess);
  4. the line names no `FromDocNo` (the 29 Sep live payloads had no link fields at all,
     `PLAN-autocount-grn-do-ingest-29sep.md:52-56`, while the 1 Sep snapshot did).
- Measured before: a prod dry run had `sales_order_unresolved` on 435 of 948 DOs and
  `so_line_unresolved` on 946 lines (`PLAN-autocount-do-pull-crm-30sep.md:87-88`). No link-rate count
  after the fallback exists in the docs.
- Older AutoCount documents carry real DtlKeys: #1623 measured 46,828 Cancel SO lines from before 2026
  with `FromDocDtlKey`, 0 in 2026 (PR #1623 body). Whether pre-2026 **DO** lines do the same is not
  known (the AutoCount SQL login has no SELECT on DO / DODTL, same source); the backfill dry run
  reports it (`lines.linked` by key vs by product, section 3.4).

### 1.4 What "delivered" means for a DO today

- A DO is delivered only when `order_status` is `delivered` / `completed` **and**
  `actual_delivery_date` is set (`be/app/services/order_service.py:62-94`,
  `complaint_fulfilment_service.py:43-47`). Everything else is outstanding (`_outstanding_clause`).
- Both are Order Tracking columns: only the Excel tracking upload writes them, along with
  `delivery_days` / `kpi_warning` (`order_service.py:3190,3224`) and the per-DO delivery
  notifications (`:3356-3368`).
- So a backfilled DO with no tracking lands as `new`, no date: **outstanding**. That is the owner's
  "require all of them to be fully delivered".

### 1.5 Who would see 3.5 years of "outstanding" DOs

| Reader | Effect if backfilled DOs stay `new` | Cite |
| --- | --- | --- |
| Outstanding report, DO side | every pre-July DO line counted as pending qty | `be/app/services/outstanding_report_service.py:444-504` |
| DO list "outstanding" bucket, chatbot order tools | listed as outstanding | `order_service.py:108-122`; `chatbot/lanes/business/fetch.py:1099-1106` |
| Delivered DO list / chatbot "delivered between" | absent (filters `actual_delivery_date`) | `fetch.py:443-447` |
| Complaint fulfilment | not affected: links come from Remarks CS, which AutoCount never sends | `complaint_fulfilment_service.py` |

### 1.6 SO side: what the DO ingest does and does not touch

- The DO ingest writes **no** SO state: no `qty_delivered`, `line_status` or `status`. It only sets
  `orders.sales_order_id` and `order_lines.sales_order_line_id` (`autocount_doc_ingest_service.py:1050-1145`).
  SO delivered qty is AutoCount's `TransferedQty` from the SO feed (ruling Q11;
  `document_ingest_service.py:242-251`), and `line_status` is re-derived on every SO push:
  `closed` when delivered >= ordered (`:1828-1852`).
- The DO push route runs no post-write hook (`be/app/api/v1/external/ingest.py:933-947`; the pull apply
  says the same, `autocount_pull_tasks.py:871`). No OI, planning-change, plan-exception, email or
  WhatsApp code runs on a DO write.
- Open demand everywhere reads the SO line, never the DO: `is_open_demand` = `line_status='open'`,
  not `covered`, `COALESCE(qty_required, qty_ordered) - qty_delivered > 0` (`be/app/services/scm/demand.py:171-182`),
  used by committed_v, Stock Debt, Coverage, reorder, outstanding SO side.
- **Conclusion: a DO backfill alone cannot make an old SO open demand, raise an OI row, a planning
  change or an email.** Old SOs that read open today do so because AutoCount's `TransferedQty` says
  so, and that is unchanged by this lane. The one exception is section 4.3 (#1623).
- Pre-existing, not caused by this lane: the fulfilment board admits closed project lines through
  `is_undecided_demand`, which has no delivery test (`demand.py:216-240`,
  `project_fulfilment_board_service.py:1544-1547`).

### 1.7 Side effects of the ingest itself

- Audit: `orders` is audited (`__audit_track__`, `order.py:402`); `order_lines` is skipped (`:513`).
  One audit row per backfilled DO header. Suppressible per session via
  `session.info["skip_audit_entity_types"]` (`audit_service.py:869-876`).
- Embeddings: the `Order` / `OrderLine` listener is registered in the **API process only**
  (`main.py:347-348`), not in the worker (`worker.py:223-248`). A push enqueues one event per DO and
  line; a pull apply (worker) enqueues none.

## 2. Design summary

Three steps, each with its own dry run and counts, run per book (Sorento `db1` first, then Mocha `db2`):

1. **Land the DOs** 2023-01-01 to 2026-06-30 through the existing ingest, month by month.
2. **Assume delivered** for pre-cutover DOs that have no tracking, marked so it is reversible.
3. **Report the link rate** and the SO-side numbers (no SO write).

## 3. Step 1: landing the DOs

### 3.1 Path (question Q5)

**(a) Recommended: a CRM script over the existing pull snapshot**, `be/scripts/do_history_backfill.py`:

- One FoundryX snapshot per calendar month (`fromDay` / `toDay`, `foundryx_autocount_client.py:166-179`),
  preview (= ingest dry run), then apply, through the same functions the pull button uses
  (`preview_autocount_pull` / `apply_autocount_pull`). Runs as a worker-context script, so no embedding
  storm (1.7) and every created / updated / adopted record lands as an `import_job_rows` outcome row,
  which is the revert list (section 5.2).
- A month over the 10,000-document cap (`autocount_pull_service.py:701`) splits into halves.
- **Resumable**: a checkpoint per `(book, month)` in the import job table (`import_jobs` row, job type
  `autocount_delivery_orders_apply` with the month in its scope). A re-run skips months whose apply
  job finished; a re-applied month is harmless (idempotent key + stale guard, 1.2).
- **Throttled**: one snapshot in flight at a time (SS answers 409 `BUILD_IN_FLIGHT` to a second scope
  anyway), a configurable pause between months (default 60 s), stop on the first failed build.
- Flags: `--book db1|db2 --from 2023-01 --to 2026-06 --dry-run|--apply --pause 60`.
- Needs no shared-service change **if** the DO snapshot scopes by DocDate and accepts 2023 dates.
  To verify on the SS side before build (crew relay): the snapshot uses `bydocdate`, not
  `byLastModified`, for `fromDay`/`toDay`, and has no floor.

(b) Alternative: the shared service's own backfill (contract: one DocDate day per call from 1 Jan
2023, pushed in batches of 1,000). No CRM code, but it runs in the API process (one embedding event
per DO and line), its dry run is `?dry_run=true` per batch with no aggregate, and the revert list has
to be rebuilt from `source_modified_at`. Pick (b) only if the snapshot cannot serve old dates.

### 3.2 What the ingest does with an old DO (unchanged code)

- New DocKey: created with status `new`, lines linked per 1.3.
- DocNo already held by an Order Tracking row (`doc_key` NULL): adopted, tracking kept (1.2).
  Dry-run count `adopted_by_doc_no` per month.
- DocNo held by another DocKey: `failed` `errors.DocNo`, listed, nothing written.
- Unknown product / warehouse: `retryable`, listed. The dry run gives the item codes; the owner
  decides whether to bring retired products in first (`PLAN-hide-retired-everywhere.md`).

### 3.3 Waiting links after landing

The fill handles only the newest 500 lines per batch (1.3), so after the last month the script runs
one bounded relink pass over the backfilled lines (same `_do_so_line` resolver, 500 per commit) so
links do not depend on batch order. No new resolver.

### 3.4 Counts the dry run prints per (book, month)

documents: created / updated / unchanged / adopted / failed / retryable; lines: created / linked by
key / linked by product / unresolved (ambiguous vs SO missing vs no FromDocNo); distinct missing item
codes; distinct missing SO numbers.

## 4. Step 2: "assume delivered" for pre-cutover DOs

### 4.1 Rule

A DO is marked assumed-delivered when **all** hold:

- `doc_key IS NOT NULL` (AutoCount's), `is_cancelled = false`, `deleted_at IS NULL`;
- `order_date < cutover` (Q1, recommended `2026-07-01`);
- it has **no tracking**: `actual_delivery_date IS NULL` and status is NULL or `new`.

Writes: `order_status_id` = `delivered`, `actual_delivery_date` = `order_date` (Q2),
`tracking_assumed_at` = now(). Nothing else: no `delivery_days`, no `kpi_warning`, no transporter.

A pre-cutover DO that already has any tracking (an adopted Excel row) is left alone and counted.

### 4.2 Schema

One nullable column, `orders.tracking_assumed_at timestamptz` (migration `do_hist_0001`), registered
in `SYSTEM_ORDER_COLUMNS` (`order_field_ownership.py`; the registry test forces every column to have
an owner). It says "delivered by assumption, not by a tracking upload", and it is the revert key.
No table: one fact, one column. A later Order Tracking upload that sets real delivery fields on the
row clears it (one line in the tracking upsert), so the marker never lies.

### 4.3 Silence during the run

- Writes go through one bulk `UPDATE ... WHERE id IN (...)` per 1,000 ids, not the ORM, so no
  embedding, audit, complaint-fulfilment or delivery-notification code runs. One summary audit
  EVENT per run (counts, cutover, run id) instead of 100k row audits.
- Step 1's ingest already sends nothing (1.6, 1.7).
- **#1623 (AC-CANCEL-CRM, open draft) is the one path that reacts to DO links**: it relinks cancel
  docs when a DO naming the SO is pushed, and a changed match can cancel a line, retire its OI rows
  and raise a planning change for CS. Question Q4: recommended, after #1623 merges, its relink skips
  the planning-change raise and OI flag for SOs ordered before the cutover (the derived
  `qty_cancelled` number still updates). If #1623 is still unmerged when the backfill runs, nothing
  reacts (it is not on main).

### 4.4 SO side (Q3)

Recommended: **no SO write.** AutoCount's `TransferedQty` already decides `closed` on every SO push
(1.6), so a CRM force-close would be reopened by the next push of that SO. Instead the dry run
reports pre-cutover SO lines still open (query Q-D) by month and customer, so the owner can close
them in AutoCount (Cancel SO / transfer), which then flows through the SO feed with no CRM-side
special case.

## 5. Dry run, apply, revert

### 5.1 Order

1. Prod clone: `do_history_backfill.py --dry-run` for each book; post the per-month table (3.4)
   and section 6 counts as a `crew-note`. No real data committed, counts only.
2. Prod clone: `--apply`, then `do_history_assume_delivered.py --dry-run` then `--apply`; rerun
   section 6; check the outstanding report total and Stock Debt totals are unchanged on the SO side
   and that the DO side lost exactly the assumed-delivered pending qty.
3. Owner approves the numbers.
4. Prod apply window: off-hours MYT, worker running, the SS hourly DO poll left on (idempotent key +
   stale guard make overlap safe). Sorento first, Mocha after Sorento's numbers check.

### 5.2 Revert

- Step 2: `do_history_assume_delivered.py --revert --run <id>` sets status back to `new`,
  `actual_delivery_date` NULL and the marker NULL for rows with that marker and no later tracking.
  Exact, because the rule only touched rows that had no tracking.
- Step 1: delete the DOs the apply jobs **created** (outcome rows `created`); adopted rows keep their
  id and tracking and are not deleted (their lines were rewritten by AutoCount, which is the intended
  state either way). The script prints the count before deleting. DO deletion is a hard delete
  (D7), and a later SS push would recreate a deleted DO, so revert step 1 only with the SS DO job
  floored back to July.

## 6. Read-only count queries (prod clone, before sign-off)

```sql
-- Q-A: DOs in CRM by month and source (AutoCount vs Order Tracking Excel)
SELECT date_trunc('month', order_date)::date AS month, c.code,
       COUNT(*) FILTER (WHERE o.doc_key IS NOT NULL) AS autocount,
       COUNT(*) FILTER (WHERE o.doc_key IS NULL)     AS excel_only
FROM orders o JOIN companies c ON c.id = o.company_id
WHERE o.deleted_at IS NULL
GROUP BY 1, 2 ORDER BY 2, 1;

-- Q-B: AutoCount DO lines by link state, split by reason
SELECT date_trunc('month', o.order_date)::date AS month,
       COUNT(*) FILTER (WHERE ol.sales_order_line_id IS NOT NULL)                AS linked,
       COUNT(*) FILTER (WHERE ol.sales_order_line_id IS NULL AND ol.from_dtl_key = 0) AS stored_zero_pre_1409,
       COUNT(*) FILTER (WHERE ol.sales_order_line_id IS NULL AND ol.from_doc_no IS NULL) AS no_from_doc_no,
       COUNT(*) FILTER (WHERE ol.sales_order_line_id IS NULL AND ol.from_doc_no IS NOT NULL
                          AND NOT EXISTS (SELECT 1 FROM sales_orders so
                                          WHERE so.company_id = o.company_id
                                            AND so.so_number = ol.from_doc_no)) AS so_missing,
       COUNT(*) FILTER (WHERE ol.sales_order_line_id IS NULL AND ol.from_doc_no IS NOT NULL
                          AND (SELECT COUNT(*) FROM sales_order_lines sol
                               JOIN sales_orders so ON so.id = sol.sales_order_id
                               WHERE so.company_id = o.company_id AND so.so_number = ol.from_doc_no
                                 AND sol.product_id = ol.product_id) > 1) AS ambiguous
FROM order_lines ol JOIN orders o ON o.id = ol.order_id
WHERE o.doc_key IS NOT NULL AND o.deleted_at IS NULL
GROUP BY 1 ORDER BY 1;

-- Q-C: pre-cutover DOs by tracking state (what step 2 would touch)
SELECT (o.doc_key IS NOT NULL) AS autocount, s.status_code,
       (o.actual_delivery_date IS NOT NULL) AS has_date, COUNT(*)
FROM orders o LEFT JOIN order_statuses s ON s.id = o.order_status_id
WHERE o.order_date < DATE '2026-07-01' AND o.deleted_at IS NULL
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

-- Q-D: pre-cutover SO lines still open demand (owner closes these in AutoCount, section 4.4)
SELECT date_trunc('month', so.order_date)::date AS month, so.demand_class, COUNT(*) AS lines,
       SUM(GREATEST(COALESCE(sol.qty_required, sol.qty_ordered) - sol.qty_delivered, 0)) AS open_qty
FROM sales_order_lines sol JOIN sales_orders so ON so.id = sol.sales_order_id
WHERE so.order_date < DATE '2026-07-01' AND so.status = 'open' AND sol.line_status = 'open'
  AND sol.purchasing_status <> 'covered'
  AND COALESCE(sol.qty_required, sol.qty_ordered) - sol.qty_delivered > 0
GROUP BY 1, 2 ORDER BY 1, 2;

-- Q-E: earliest SO in CRM per company (can a 2023 DO find its SO at all?)
SELECT c.code, MIN(so.order_date), COUNT(*) FROM sales_orders so
JOIN companies c ON c.id = so.company_id GROUP BY 1;
```

Q-E decides how many backfilled lines can link at all: if the CRM holds no 2023 SOs, those DO
lines land unlinked (`so_missing`) and only a later SO history load would link them (out of scope;
trigger: the owner wants per-SO-line delivered history before the earliest SO in the CRM).

## 7. Effect on readers after both steps

| Reader | Effect |
| --- | --- |
| Outstanding report (SO side), Stock Debt, Coverage, committed, reorder, fulfilment board | None: they read SO lines (1.6). |
| Outstanding report (DO side) | Pre-cutover AutoCount DOs leave the pending total (assumed delivered). |
| Delivered DO list, chatbot "DOs delivered between X and Y" | Pre-cutover DOs appear on their DocDate. |
| Sales report delivered (`delivery_order_lines.py`, `sales_report_delivered.py`) | Gains 2023 to June 2026 AutoCount DOs by `order_date`; an adopted Excel DO switches to the per-line amount basis (`delivery_order_lines.py:145-159`). |
| Chatbot "delivered" sales basis | None: reads SO `qty_delivered` (`sales_report_service.py:204,540-550`). |
| Sales achievement, delivered basis | Linked DO qty moves from the SO's order date to the DO date (`sales/achievement_service.py:25-31,347-356`); per-line totals stay capped at `qty_ordered`, so a period's delivered achievement can shift between months, the all-time total does not. |
| #1623 cancel matching | Gains DO qty per SO line for the tie-break once lines link; Q4 gates the side effects. |

## 8. Questions (also posted as crew-ask)

1. **Q1 cutover**: (a) DocDate before 2026-07-01, (b) another date. Rec (a): "since July".
2. **Q2 delivery date on assumed rows**: (a) `actual_delivery_date = DocDate` with the marker,
   (b) leave NULL and widen the delivered predicate to the marker. Rec (a): one write, every
   existing delivered reader works unchanged; the marker tells it apart.
3. **Q3 SO side**: (a) no CRM SO write, report open pre-cutover SO lines for closing in AutoCount,
   (b) force-close them in the CRM. Rec (a): the next SO push re-derives `line_status` from
   `TransferedQty`, so (b) would not stick.
4. **Q4 #1623 side effects on old SOs**: (a) after #1623 merges, its DO-triggered relink skips the
   planning change and OI flag for SOs ordered before the cutover, (b) let them fire. Rec (a): no
   CS work raised for 2023 orders.
5. **Q5 path**: (a) CRM script over monthly pull snapshots, (b) shared-service DocDate backfill push.
   Rec (a): aggregate dry run, resumable per month, revert list, no embedding storm; needs the SS
   snapshot to scope by DocDate for old dates (to verify).

## 9. Build slices (after OK)

1. Migration `do_hist_0001` + ownership registry + tracking-upload clears the marker; tests.
2. `do_history_backfill.py` (dry run, apply, resume, split on cap, relink pass, per-month table); tests
   on a scratch Postgres with a fake FoundryX client.
3. `do_history_assume_delivered.py` (dry run, apply, revert, run audit event); tests.
4. (After #1623 merges, if Q4 a) the cutover gate in its relink; tests.

Not built: SO history load (trigger in section 6), a UI badge for assumed-delivered DOs (trigger: the
owner asks to tell them apart on screen).
