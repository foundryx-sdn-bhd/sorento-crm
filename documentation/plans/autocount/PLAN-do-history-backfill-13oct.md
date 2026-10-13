# PLAN: DO history backfill from 2023-09-01, exact SO-line links, assumed delivery (DO-HISTORY)

Status: **plan v3; C1 (location narrowing) and C3 (backfill flag) built on top of slice 1
(marker column). C2 (assume delivered) is held on the cutover ask (section 8). The line-key source
for ambiguous lines is pending with crew.** Track: full
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

Crew answer to v2, 13 Oct:

- **Cutover = 2023-09-01** (the backfill start): `do_assume_delivered_before = 2023-09-01`;
  "everything from 2023-09-01 is tracked from real backfilled DOs".
- **"Exact" = one provable SO line.** The key link wins when present. Otherwise, when `FromDocNo`
  names the SO and that SO has exactly ONE line of the DO's product (after location narrowing, when
  the location is exact on both), the link is exact by construction and **stays**: that is how all
  29,040 of today's links were made. Several candidate lines (about 13,259 today) stay
  **unlinked**: no lowest-Seq, no qty-fit guess. API `FromDocDtlKey` is 0 on every DO line (verified
  live 8 Oct, and on 51,418 stored lines).
- A line-key source for the ambiguous case (AutoCount SQL DODTL via FXView, or a change to the API
  wrapper) is being checked by crew. It is pending, because the MacBook Air tunnel is down.

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
| K | crew (answered: no) | The API sends `FromDocDtlKey` 0 on every DO line. A key source for the ambiguous lines (DODTL via FXView, or an API wrapper change) is pending. | none |
| S1 | SS (its own lane) | Change the DO start floor in `doc_feed/constants.py:48` from 2023-01-01 to **2023-09-01**. | none |
| S2 | SS | Make sure db1 (Sorento) and db2 (Mocha) each have a DO feed config row. A missing row fails loudly for that book. | none |
| S3 | SS | Run the existing DocDate backfill for DO from 2023-09-01 to today (rules below the table). | S1, S2, K, C1-C3 deployed |
| C1 | CRM (this lane) | Provable links only: key, else a unique product match narrowed by location (section 3). | none |
| C2 | CRM | Assumed delivery when a pre-cutover DO is created or adopted (section 4). | the ask |
| C3 | CRM | Quiet backfill: no per-DO embedding events or audit rows on backfill pushes (section 5). | contract 2.9 agreed with the SS lane |

S3 run rules:
- One day per call, at most 1,000 records per batch, oldest first.
- Uses the SS's own cursor, so a stopped run resumes.
- Throttled: one call in flight per book, with a pause between days.
- Sorento first, then Mocha.
- First pass is on the prod clone with `?dry_run=true`, with counts aggregated by month.

The CRM never calls AutoCount. It only ingests what the SS pushes.

## 3. C1: provable SO-line links only (owner rule 3, crew definition)

The resolver for a DO line with `FromDocType` absent or `SO` (`_do_so_line`,
`autocount_doc_ingest_service.py:859-878`; the waiting fill uses the same resolver):

1. **Key.** `FromDocDtlKey` > 0 links to the SO line whose `source_ref` ends in that key, inside SO
   `FromDocNo` (`_so_line`, `:847-857`). If present, the key wins and nothing else is tried.
   Already built (test `test_exact_so_line_link`).
2. **Unique product.** Otherwise, the lines of the DO line's product inside SO `FromDocNo` in the
   anchor company. Exactly one: link. Already built (test
   `test_from_dtl_key_zero_links_by_doc_no_and_item`).
3. **Location narrowing (new).** Several candidates, the DO line has a warehouse, and **every**
   candidate has a warehouse: keep the candidates whose warehouse equals the DO line's. Exactly
   one: link. A candidate with no warehouse could be the real line, so it blocks narrowing.
4. **Otherwise unlinked**, with the existing `so_line_unresolved` warning. No position, Seq or
   quantity tie-break (test `test_from_dtl_key_zero_with_two_candidate_so_lines_warns`, plus a new
   one where the location matches several lines).

The prod-clone dry run counts how many of today's 13,259 ambiguous lines rule 3 resolves. The
rest wait for crew's key source (K).

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

**Contract (additive, no version bump).** An optional envelope flag `"backfill": true` on
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

A cutover of 2023-09-01 for `do_assume_delivered_before` assumes **nothing**, because no
backfilled DO is dated before its own start. Every DO from 2023-09-01 to July 2026 would therefore
land as `new` with no delivery date. At the 1.1 rates that is about 270,000 DOs, and they would show
as **outstanding** in the DO list, on the DO side of the outstanding report, and in the chatbot
(`order_service.py:62-94`, `outstanding_report_service.py:444-504`). Order Tracking for these DOs
does not exist (the owner's "I don't have the order tracking for them prior to July").

- (a) Keep the owner's "fully delivered" ask: `do_assume_delivered_before` = the first tracked
  month (2026-07-01, or 2026-08-01 for the feed start). The backfill still starts 2023-09-01, and
  every untracked DO before the date reads delivered with the "assumed" marker.
- (b) Cutover 2023-09-01 as stated: no DO is assumed delivered, and the owner accepts about
  270,000 outstanding DOs, or CS uploads tracking for them.

Recommend (a) with 2026-07-01, because it is the owner's "fully delivered" requirement from the
original ask. C2 is not built until this is answered.

## 9. Build slices (after K and the ask)

1. **Built**: the `tracking_assumed_at` column (migration `do_hist_0001_tracking_assumed`), system-owned,
   cleared by a tracking row.
2. **Built**: C1 location narrowing for several unique-product candidates; tests (key, unique match,
   ambiguous unlinked, location-narrowed link, location still ambiguous).
3. C2: assumed delivery in the ingest, behind the setting; tests.
4. **Built**: C3, the optional `backfill` envelope flag. Its tests are in `tests/test_do_history_backfill_flag.py`, and
   section 13.2 of `PLAN-autocount-cross-repo-contract.md` is updated for the SS lane. The flag is additive and
   optional, so the contract version string stays as is (avoids a clash with #1623's 2.8).
5. The #1623 gate (Q4 a), after #1623 merges.

Not built: SO history, and a UI badge for assumed rows (trigger: the owner asks to tell them apart).
