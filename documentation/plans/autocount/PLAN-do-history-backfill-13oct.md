# PLAN: DO history backfill from 2023-09-01, exact SO-line links, assumed delivery (DO-HISTORY)

Status: **plan v2 after OWNER DIRECTION 13 Oct. Waiting for crew's finding on whether AutoCount
API DO lines carry the SO line key (`FromDocDtlKey`) before any more code.** Track: full
(migration column, ingest behaviour change). Slice 1 (the `tracking_assumed_at` column) is built
and pushed at `cb05abdf`; nothing else is built. PR #1633, branch
`claude/do-history-backfill-7e7enk`. UAC: `do-history-backfill-13oct-acceptance-criteria.md`.
Owner card: `DO-HISTORY-card.md`.

Paths: `be/` = `sorento_crm_backend/`. CRM citations are on `main` `64dfd86c`. Shared service (SS)
citations come from crew's read of SS `main` `812dac2b`.

## 0. The ask and the owner's direction

Owner, 13 Oct: "for DO we are reading from hapi from AutoCount; we should have DO data in Sorento
CRM that links to SO. I think now we only import DO since July; after this I need to import all
from AutoCount since 2023, but that will require all of them to be like fully delivered, because
I don't have the order tracking for them prior to July, so we need a plan to migrate all these."

OWNER DIRECTION, 13 Oct (overrides crew's Q1 and Q5 answers):

1. The backfill runs from the shared service's **existing** AutoCount integration job (doc feed,
   DocDate push), **not** from a CRM script. The CRM-script design from plan v1 is dropped.
2. The backfill starts on **2023-09-01**, configured in the SS. Today `doc_feed/constants.py:48`
   says 2023-01-01.
3. **No flagging, no guessing**: every DO line links to its **exact** SO line. No fallback link is
   planned.

Crew answers that still stand:
- Q2 (a): an assumed row gets `actual_delivery_date` = DocDate plus the `tracking_assumed_at` marker.
- Q4 (a): a #1623 cancel relink on a pre-cutover SO raises no planning change and no OI flag.

Q3 (the SO side) is with the owner.

## 1. Facts

### 1.1 Prod-clone counts (crew, `sorento_prod_20261012`, read-only, 13 Oct)

- **DOs by month.**
  - AutoCount DOs start in Aug 2026: SRT 1,569 / 6,094 / 2,731 (Aug / Sep / Oct), MCH 227 / 1,796 / 1,022.
  - Before that there are only Order Tracking Excel rows: SRT Apr-Jul 2026 2,209 / 5,127 / 5,847 /
    6,947, MCH Jul 1,848, plus scattered late-2025 Excel rows.
- **Line links today.**
  - 45,157 AutoCount DO lines: 29,040 linked, 16,117 unlinked.
  - The unlinked split as 13,259 ambiguous, 1,642 product not on the SO, 1,213 with no `FromDocNo`,
    1 SO missing and 2 single-match lines left unlinked.
  - **0 of 45,157 carry a `FromDocDtlKey`.** Every one of the 29,040 links came from the
    `FromDocNo` + product fallback, not from a key.
- **Pre-July tracking.** 0 AutoCount DOs. Excel: 10,890 delivered with a date, 2,378 `new` without one.
- **Pre-July open SO lines.** 9,615 lines, about 991,615 qty, mostly project, the bulk dated 2025-08 to 2026-06.
- **Earliest SO.** SRT 2020-01-03, MCH **2023-09-01**. A 2023-09-01 start therefore falls inside
  the CRM's SO history for both books.

### 1.2 How a DO lands today (CRM)

- **Route.** The SS pushes to `POST /api/v1/external/ingest/delivery_orders` (contract 2.7), at
  most 1,000 records per call, `?dry_run=true` supported. The route runs
  `AutocountDocIngestService.ingest` (`be/app/services/autocount_doc_ingest_service.py`).
- **Idempotency.** Rows are keyed on `(company_id, source_book, doc_key)`
  (`be/app/models/order.py:500-505`, `_find` `:721-745`). A record with an older `LastModified` is
  `unchanged` (`_is_stale`, `:747-754`). Re-pushing a day the feed already sent is harmless.
- **Adoption.** An Order Tracking row with the same DocNo and no `doc_key` is adopted: it keeps
  its id and its tracking (`:740-745`, `:1122-1135`).
- **Status of a new DO.** It gets status `new` and no delivery date (`:831-840`, `:1111`), so it
  reads as **outstanding**. Delivered needs status `delivered`/`completed` AND an
  `actual_delivery_date` (`be/app/services/order_service.py:62-94`).
- **What does not run.** The push route has no post-write hook (`be/app/api/v1/external/ingest.py:933-947`),
  so no OI, planning, email or WhatsApp code runs on a DO write. The DO ingest writes no SO
  state (`:1050-1145`).
- **What does run.** The route runs in the API process, so every push writes:
  - one audit row per DO header (`orders` is `__audit_track__`, `order.py:402`);
  - embedding events for every DO and every line (`embedding_change_listener.py:163-203`,
    registered at `main.py:347-348`).

### 1.3 Line links today, measured against the owner's rule

- **Exact path (built).** A `FromDocDtlKey` > 0 matches the `sales_order_lines` row whose
  `source_ref` ends in `:{FromDocDtlKey}` (the SO feed writes `{database}:{DocKey}:{DtlKey}`),
  inside the SO numbered `FromDocNo` (`_so_line`, `autocount_doc_ingest_service.py:847-857`;
  contract :696).
- **Fallback path (built by #1409).** With no key, the DO line takes the one line of the same
  product in SO `FromDocNo` (`_do_so_line`, `:859-878`). The waiting fill retries it (`:1391-1416`).
- **What AutoCount sends.** `FromDocDtlKey = 0` on every line (1.1: 0 of 45,157), and `_link_key`
  stores that as NULL (`:190-193`). So **every link in the CRM today is a fallback link**, and
  owner rule 3 does not accept any of them.

### 1.4 What the SS does (crew read of SS main 812dac2b)

- The DO doc feed reads AutoCount by DocDate (`doc_feed/snapshot.py:143-165` ->
  `vendor.day_by_doc_date`, `/deliveryorderbydocdate`, `vendor.py:58-60`).
- The start floor is `doc_feed/constants.py:48` (2023-01-01). The pull gateway enforces it as an
  error (`pull_gateway_service.py:189-190`).
- Each book needs its own DO feed config row (`pull_gateway_service.py:65-68`). Mocha's row is
  not confirmed.

## 2. Who does what

| # | Repo / lane | Work | Depends on |
| --- | --- | --- | --- |
| K | crew (in progress) | Finding: does the AutoCount API send a real `FromDocDtlKey` on DO lines (any endpoint, any date range)? | none |
| S1 | SS (its own lane) | Change the DO start floor in `doc_feed/constants.py:48` from 2023-01-01 to **2023-09-01**. | none |
| S2 | SS | Make sure db1 (Sorento) and db2 (Mocha) each have a DO feed config row. A missing row fails loudly for that book. | none |
| S3 | SS | Run the existing DocDate backfill for DO from 2023-09-01 to today (rules below the table). | S1, S2, K, C1-C3 deployed |
| C1 | CRM (this lane) | Exact links only (section 3). | K = yes |
| C2 | CRM | Assumed delivery when a pre-cutover DO is created or adopted (section 4). | the ask |
| C3 | CRM | Quiet backfill: no per-DO embedding events or audit rows on backfill pushes (section 5). | contract 2.9 agreed with the SS lane |

S3 run rules:
- One day per call, at most 1,000 records per batch, oldest first.
- Uses the SS's own cursor, so a stopped run resumes.
- Throttled: one call in flight per book, with a pause between days.
- Sorento first, then Mocha.
- First pass is on the prod clone with `?dry_run=true`, with counts aggregated by month.

The CRM never calls AutoCount. It only ingests what the SS pushes.

## 3. C1: exact SO-line links only (owner rule 3)

### 3.1 If K = yes (the API sends the key)

- **Key only.** `_do_so_line` links **only** by `FromDocDtlKey`, through the existing exact path
  (1.3). The `FromDocNo` + product fallback is removed for DO lines, in the ingest and in the
  waiting fill.
- **Key with no SO line yet.** The line stays unlinked, and the waiting fill retries it by exact
  key when the SO arrives. The `so_line_unresolved` warning for ambiguity goes away, because a key
  names exactly one line.
- **Existing fallback links (the 29,040 in 1.1)** are replaced on the next push of each DO:
  - a pushed key overwrites the link;
  - a pushed line with no key is cleared, so no link the CRM holds is a guess.
  S3 runs through today, so every live DO is re-linked by key in the same pass. The prod-clone
  dry run counts links before and after.
- **SS mapping.** The SS sends the key it finds. If the key comes from a different endpoint or
  field name, S3 maps it onto `FromDocDtlKey` in the 2.7 record shape, so the CRM contract does
  not change.
- **Readers.** Sales achievement by DO date (`be/app/services/sales/achievement_service.py:25-31,347-356`)
  and #1623's tie-break get exact links only. A DO line with no link counts on the SO's order
  date, as it did before any DO was linked.

### 3.2 If K = no

The AutoCount API then has no exact source for the line link, and this lane cannot meet rule 3.

- The backfill can still land the DOs with **header** links (`RefDocNo` / `FromDocNo` to the SO,
  exact by number) and **no** line links.
- The fallback is still removed, so nothing is guessed.
- A line key would need another source, such as the AutoCount DB. The SQL login has no SELECT on
  DO / DODTL today (per #1623).
- That is a new owner decision, not planned here.

## 4. C2: assumed delivery, inside the ingest

The ingest marks DOs as it lands them. A separate script is not needed, and the ingest is the
only CRM code a DocDate push passes through.

- **Setting.** A new CRM setting `do_assume_delivered_before` (a date). It ships unset, which means off.
- **On create** of a DO with a DocDate before that date, write three columns and nothing else:
  - status `delivered`;
  - `actual_delivery_date` = DocDate at 00:00 (the column is a DateTime; the tracking upload stores
    a date-only row the same way, `order_service.py:3163-3166`);
  - `tracking_assumed_at` = now.
  No `delivery_days`, no `kpi_warning`, no transporter.
- **On adoption** of an Excel row with a DocDate before that date and **no tracking** (no
  `actual_delivery_date`, status NULL or `new`): the same three writes. An adopted row that has
  any tracking keeps it.
- **On update** of an existing DO: never. Only the first landing assumes.
- **Later tracking.** An Overall Tracking row for the DO replaces the assumption and clears the
  marker (slice 1, built in the tracking upsert in `order_service.py`).
- **Revert.** One SQL statement on the marker: status back to `new`, date and marker set to NULL,
  only on rows where the marker is set. The card shows it; running it is the owner's call.
- **Ownership.** All three columns sit outside the AutoCount-owned set (`tracking_assumed_at` is
  system-owned; the other two are Order Tracking's), so this path writes them outside
  `assert_autocount_writes`. A test pins that no other ingest path writes them.

## 5. C3: a quiet backfill

**Size.** The run covers about 35 months of DOs. At the rates in 1.1 (about 6,000 SRT and 1,800
MCH DOs a month, about 3.4 lines each) that is on the order of 270,000 DOs and 900,000 lines.
Through the push route that means 270,000 header audit rows and more than a million embedding
events.

**Contract 2.9 (additive).** An optional envelope flag `"backfill": true` on
`POST /external/ingest/delivery_orders`. With the flag, the route:
- runs the ingest under `suppress_embedding_events("order", "order_line")`
  (`embedding_change_listener.py:34`, as the tracking import does at `order_service.py:3328`);
- sets `session.info["skip_audit_entity_types"]` for `order` (`audit_service.py:869-876`);
- writes one summary audit event per batch (counts, book, day).

Without the flag nothing changes.

**SS side.** The SS sets the flag only on S3's backfill calls, never on its hourly poll.

**Nothing else to silence.** The DO push has no email or notification path today (1.2).

## 6. Effects

| Reader | Effect |
| --- | --- |
| SO-side outstanding, Stock Debt, Coverage, committed, reorder, fulfilment board | None. They read SO lines, and the DO ingest writes no SO state. |
| DO outstanding (DO list bucket, outstanding report DO side, chatbot) | Backfilled DOs before the cutover read as delivered, so they do not appear. |
| Delivered DO list, chatbot "delivered between" | Backfilled DOs appear on their DocDate. |
| Sales report delivered (`reports/datasets/delivery_order_lines.py`) | Gains AutoCount DOs from 2023-09-01, by `order_date`. |
| Sales achievement, delivered basis | Exact-linked DO qty counts by DO date; unlinked lines count on the SO date. Removing the fallback links (3.1) moves their qty back to the SO date until a key arrives. |
| #1623 cancel matching | The tie-break gains exact DO qty per SO line. Q4 (a) keeps pre-cutover SOs quiet. |
| Chatbot "delivered" sales basis | None. It reads SO `qty_delivered`. |

## 7. Prod run

1. Crew finding K. If K = no, the owner decides on 3.2 before anything else.
2. Merge and deploy CRM C1-C3 with `do_assume_delivered_before` unset. Merge SS S1 and S2.
3. On the prod clone: set the CRM date and run S3 with `?dry_run=true` for both books. Post the
   per-month counts (created / adopted / unchanged / failed / retryable; lines linked by key /
   unlinked) and re-run the section 1.1 queries.
4. The owner approves the numbers.
5. On prod: set the date and run S3 for real, off-hours MYT, Sorento then Mocha. Re-run the
   queries, then unset the date so no later push assumes anything.

## 8. The one ask

Cutover for "assume delivered" (`do_assume_delivered_before`):
- (a) **2026-07-01**, your "prior to July". Apr-Jul 2026 rows that are tracked keep their tracking.
- (b) **2026-08-01**, where the AutoCount feed starts, so every backfilled DO without tracking is
  assumed delivered.

Recommend (a): it is your own boundary, and an untracked July DO stays visible to CS as outstanding.

## 9. Build slices (after K and the ask)

1. Built: the `tracking_assumed_at` column (migration `do_hist_0001_tracking_assumed`), system-owned,
   cleared by a tracking row.
2. C1: exact-only links (if K = yes); tests.
3. C2: assumed delivery in the ingest, behind the setting; tests.
4. C3: the contract 2.9 `backfill` flag; tests; section 13 of the contract doc updated for the SS lane.
5. The #1623 gate (Q4 a), after #1623 merges.

Not built: SO history, and a UI badge for assumed rows (trigger: the owner asks to tell them apart).
