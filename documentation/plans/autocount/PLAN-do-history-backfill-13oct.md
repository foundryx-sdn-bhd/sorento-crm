# PLAN: DO history backfill from 2023-09-01, exact SO-line links, assumed delivery (DO-HISTORY)

Status: **plan v5. Slice 1, C1, C2 and C3 are built on PR #1633 (merge-ready at `4a193e088`).
Owner rulings 14 Oct (section 0b): no line-key source, ever; several SO lines of the product link
to the SO + product group, with no warehouse narrowing (built on the follow-up PR, branch
`claude/do-history-group-links`, stacked on #1633); Q3 (a), no CRM SO write, the owner closes the
pre-July open lines in AutoCount from `SQL-pre-july-open-so-lines-14oct.sql`.** Track: full
(migration column, ingest behaviour change, external ingest surface flag). PR #1633, branch
`claude/do-history-backfill-7e7enk`. UAC: `do-history-backfill-13oct-acceptance-criteria.md`.
Owner card: `DO-HISTORY-card.md`.
Section 0b.3 gate: lane DO-BACKFILL-CRM, PR #1687, small fix track, built.

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

- (Superseded by the owner's answer, section 4.) Crew first said cutover = 2023-09-01 for
  `do_assume_delivered_before` as well.
- **"Exact" = one provable SO line.** The key link wins when present. Otherwise, when `FromDocNo`
  names the SO and that SO has exactly ONE line of the DO's product, the link is exact by
  construction and **stays** (the location narrowing first proposed here is superseded by the
  owner, 14 Oct, section 0b): that is how all
  29,040 of today's links were made. Several candidate lines (about 13,259 today) stay
  **unlinked**: no lowest-Seq, no qty-fit guess. API `FromDocDtlKey` is 0 on every DO line (verified
  live 8 Oct, and on 51,418 stored lines).
- A line-key source for the ambiguous case (AutoCount SQL DODTL via FXView, or a change to the API
  wrapper) is being checked by crew. It is pending, because the MacBook Air tunnel is down.

Crew answers that still stand:
- (Q2 (a), DocDate as the delivery date, is superseded by the owner's answer in section 4: the
  date stays empty.)
- Q4 (a): a #1623 cancel relink on a pre-cutover SO raises no planning change and no OI flag.

Q3 (the SO side): answered 14 Oct, section 0b.

## 0b. Owner rulings, 14 Oct

1. **No line-key source, ever.** The API sends `FromDocDtlKey` 0 on DO lines and the AutoCount
   admin will not grant DO / DODTL SQL access; nothing is planned around either. DO and Cancel SO
   link to the SO by document (`FromDocNo`) + product. One SO line of that product: the DO line
   links to it (as today). Several: the DO line links to the SO + product **group**, never a
   guessed line.
   - The group is already stored: every DO line keeps `from_doc_no` and `product_id`
     (`be/app/services/autocount_doc_ingest_service.py:1157-1161`), and #1623 already reads a
     group's DO qty by name for an unlinked line (`autocount_cancel_ingest_service.py:364-391`).
   - SO line quantities never come from DO links. The DO ingest writes no SO line column.
     `qty_delivered` is the SO push's line field (`document_ingest_service.py:244`), AutoCount's
     SODTL TransferedQty (`be/app/models/order.py:728-731`), so open qty (ordered less transferred)
     stays exact. `qty_cancelled` is derived from Cancel SO lines; an `undetermined` group is kept
     at SO + item level on no line (`autocount_cancel_matching.py:15-17`).
   - No screen lists DOs under an SO line or product today, exact or group: the SO detail's
     Delivery tab is SO-line totals only (`SalesOrderDetail.tsx:2057-2087`), and no API schema
     exposes `order_lines.sales_order_line_id`.
   - Crew answers (14 Oct): the DO line's warehouse no longer narrows several candidates to one
     (Q1 (a), section 3), because the warehouse does not prove the line: of 9,828 dev DO lines
     whose SO has one line of the product, 570 sit at a different warehouse than that line. The
     SO screen gains no DO history in this lane (Q3 (a): the owner wants a Deliveries section,
     Cancel SO lines included, as its own lane SO-DELIVERIES-VIEW, mock first). Sales achievement
     stays as is (Q4 (a)): group-linked DO qty counts on the SO date, like any unlinked line.
   - Owner Q2 (a), 14 Oct: a Cancel SO group is SO + item, whatever the location (Cancel PO / SPO
     keep it). Built on the follow-up PR as its own commits; `PLAN-autocount-cancel-crm-13oct.md`
     R2 step 4 carries the amendment. UAC AC-DH27..30a.
2. **Q3 (a).** No CRM write to the pre-July open SO lines. The owner closes them in AutoCount from
   the read-only list `SQL-pre-july-open-so-lines-14oct.sql` (the owner runs it on prod; dev:
   10,630 lines, 1,080,457 open qty, 64 of them from the pre-feed Excel upload). No existing screen gives that list: `/scm/sales-orders` is
   one row per SO and exports only the ticked rows of a page.
3. **Pre-cutover relink gate** (Q4 (a), #1623's `_run_cancel_relink_hook`,
   `be/app/api/v1/external/ingest.py:384`): its own follow-up lane, before the SS backfill runs.
   Built by lane DO-BACKFILL-CRM (PR #1687): a backfill batch (`"backfill": true`) skips the
   relink hook entirely, and also queues no embedding work (AC-DH44..46).
   Scope (review, 14 Oct): the gate covers the backfill batch only. A later plain SO push, an
   hourly DO push naming the same SO, or a Cancel SO push still relinks that SO and reads the
   backfilled DO qty, so the Q4 (a) wording above ("pre-cutover SO raises no planning change")
   holds for the backfill itself, not for those later pushes. Trigger for a wider gate: the
   first planning change or OI retire on a pre-cutover SO after the backfill.

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

### 1.3b Line-key sources (crew, FXView read-only, 13 Oct 21:20)

- FXView is DENIED SELECT on DO, DODTL and GRN, on both Sorento and Mocha (SQL error 229). It can
  read Consignment (+DTL, Return), Creditor, Debtor, Item*, Location, PO/PODTL, SO/SODTL, XP/XPDTL
  and XS/XSDTL.
- XSDTL (Cancel SO lines) `FromDocDtlKey` by year:

  | Year | Keyed / lines |
  | --- | --- |
  | 2023 | 7,898 / 7,898 |
  | 2024 | 5,420 / 6,544 |
  | 2025 | 0 / 7,553 |
  | 2026 | 0 / 5,510 |

  Every line is keyed until Oct 2024; Nov 2024 is 208 of 658; from Dec 2024 the key is always
  NULL, while `FromDocNo` is still filled. (So "about 25% empty" is not a fair summary: it is all,
  then nothing, with a one-month switch.)
- API DO lines: `FromDocDtlKey` is 0 always.
- Mocha's SQL connection needs pymssql `tds_version='7.0'`.
- Owner rule stands: exact links only. Line keys for ambiguous lines: none, ever (owner 14 Oct,
  section 0b); the previous wording follows for the record. How to get line keys for ambiguous lines was an owner
  decision, pending. Nothing in this lane depends on it.

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
| C1 | CRM (this lane) | Provable links only: key, else a unique product match; several candidates link the SO + product group (section 3, owner 14 Oct). | none |
| C2 | CRM (built) | Assumed delivery when a DO dated before 2026-07-01 is created or adopted (section 4). | owner answered |
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
3. **Several candidates: the SO + product group** (owner 14 Oct). The DO line keeps its
   `from_doc_no` + `product_id` and links to no single line, with the existing
   `so_line_unresolved` warning. No warehouse, position, Seq or quantity pick (tests
   `test_from_dtl_key_zero_with_two_candidate_so_lines_warns`, `tests/test_do_history_links.py`).
   The 13 Oct crew step that narrowed several candidates by the DO line's warehouse is removed
   (follow-up PR): the warehouse does not prove the line (section 0b).

## 4. C2: assumed delivery, inside the ingest (owner answer 13 Oct)

Owner: backfill from 2023-09-01; an untracked DO dated before **2026-07-01** gets status
**DELIVERED** with the `tracking_assumed_at` marker, and **`actual_delivery_date` stays empty**
("they don't have delivered date, but status is delivered"). This overrides crew's earlier Q2
answer (DocDate as the delivery date).

- **Setting.** `do_assume_delivered_before`: Settings > Integrations > AutoCount, "Assume DOs
  delivered before" (lane DO-BACKFILL-SETTING, `PLAN-do-backfill-setting-15oct.md`). It ships
  unset (off). For the backfill it is set to `2026-07-01`. The ingest reads it once per batch, so
  a save applies with no restart. Env `DO_ASSUME_DELIVERED_BEFORE` (`app/config.py`) is only the
  fallback while nothing is saved.
- **On create** of a DO with DocDate before the setting: status `delivered`, `tracking_assumed_at`
  = now. No delivery date, no `delivery_days`, no `kpi_warning`, no transporter.
- **On adoption** of an Order Tracking row with DocDate before the setting and **no tracking**
  (no `actual_delivery_date`, status NULL or `new`): the same two writes. An adopted row with
  any tracking keeps it.
- **On update** of an existing DO: never.
- **No `delivered` status configured**: nothing is assumed, and the ingest logs a warning.
- **The delivered rule accepts the marker.** `_delivered_clause` = delivered/completed status AND
  (a date OR the marker). `_outstanding_clause` is its null-safe negation
  (`be/app/services/order_service.py:82-112`). That one rule feeds the DO list bucket, the
  order summary, the outstanding report DO side and the chatbot order tools. A delivered
  status alone, or the marker under another status, is still outstanding.
- **Readers that stay date-based:**
  - "has a delivery date" filters, and "delivered between" lists, read `actual_delivery_date`
    literally, so assumed DOs show no date there.
  - Complaint fulfilment still needs a date (`complaint_fulfilment_service.py:86-89`). Backfilled
    DOs carry no Remarks CS, so no complaint links to them.
- **Later tracking.** An Overall Tracking row replaces the assumption and clears the marker
  (slice 1). A Master-sheet row without a Tracking row keeps the assumed delivery: the Master
  pass's "already delivered" check counts the marker like a date (`order_service.py`, review
  round 1 blocker).
- **Tool text.** The chatbot / MCP order-tool description and the orders route's `order_status`
  description say "delivered" includes an assumed delivery with no date.
- **Revert** (held for the owner):
  `UPDATE orders SET order_status_id = <new>, tracking_assumed_at = NULL WHERE tracking_assumed_at IS NOT NULL`.
- **Ownership.** The status and the marker are written outside `assert_autocount_writes`, on
  these two paths only.

## 5. C3: a quiet backfill

**Size.** The run covers about 35 months of DOs. At the rates in 1.1 (about 6,000 SRT and 1,800
MCH DOs a month, about 3.4 lines each) that is on the order of 270,000 DOs and 900,000 lines.
Through the push route that means 270,000 header audit rows and more than a million embedding
events.

**Contract (additive, no version bump).** An optional envelope flag `"backfill": true` on
`POST /external/ingest/delivery_orders`. With the flag, the route:
- runs the ingest under `suppress_embedding_events("order", "order_line")`
  (`embedding_change_listener.py:34`) and sets `session.info["skip_audit_entity_types"]` for
  `orders` (`audit_service.py:869-876`);
- in the batch's own transaction (so a batch never lands without it) writes ONE `IMPORT` audit
  row with the company, book, DocDate range, verdict counts and `items`: every created or
  updated DO's source ref, order id and outcome (security review B1: the one row still leads to
  every order it stands for);
- queues the written orders for embedding in one insert, as the Order Tracking import does
  (`order_service.py:3333-3348`), so the history reaches semantic search without one listener
  event per DO and line. Lines are not queued, the same as that import.

Without the flag nothing changes.

**SS side.** The SS sets the flag only on S3's backfill calls, never on its hourly poll.

**Nothing else to silence.** The DO push has no email or notification path today (1.2).

## 6. Effects

| Reader | Effect |
| --- | --- |
| SO-side outstanding, Stock Debt, Coverage, committed, reorder, fulfilment board | None. They read SO lines, and the DO ingest writes no SO state. |
| DO outstanding (DO list bucket, outstanding report DO side, chatbot) | Backfilled DOs before the cutover read as delivered, so they do not appear. |
| Delivered DO list (bucket), chatbot delivered bucket | Assumed DOs count as delivered. |
| "Delivered between" date lists, "has a delivery date" filters | Assumed DOs do not appear: they have no delivery date (owner). |
| Sales report delivered (`reports/datasets/delivery_order_lines.py`) | Gains AutoCount DOs from 2023-09-01, by `order_date`. |
| Sales achievement, delivered basis | Exact-linked DO qty counts by DO date; unlinked lines count on the SO date. Removing the fallback links (3.1) moves their qty back to the SO date until a key arrives. |
| #1623 cancel matching | The tie-break gains exact DO qty per SO line. Q4 (a) keeps pre-cutover SOs quiet. |
| Chatbot "delivered" sales basis | None. It reads SO `qty_delivered`. |

## 7. Prod run

1. Done: crew finding K = no key from the API (1.3b); exact links stay as section 3.
2. Merge and deploy CRM C1-C3 with `do_assume_delivered_before` unset. Merge SS S1 and S2.
3. On the prod clone: set the CRM date (Settings > Integrations > AutoCount) and run S3 with `?dry_run=true` for both books. Post the
   per-month counts (created / adopted / unchanged / failed / retryable; lines linked by key /
   unlinked) and re-run the section 1.1 queries.
4. The owner approves the numbers.
5. On prod: set the date (Settings > Integrations > AutoCount) and run S3 for real, off-hours
   MYT, Sorento then Mocha. Re-run the queries, then clear the date so no later push assumes
   anything (this holds while prod env `DO_ASSUME_DELIVERED_BEFORE` is unset; a set env value is
   the fallback a cleared date returns to).

## 8. Asks

Both earlier asks are answered (owner, 14 Oct, section 0b): no line-key source, and Q3 (a).
Answered 14 Oct (section 0b): no warehouse narrowing; no SO-screen DO history in this lane (lane
SO-DELIVERIES-VIEW); sales achievement unchanged.

## 9. Build slices

1. **Built**: the `tracking_assumed_at` column (migration `do_hist_0001_tracking_assumed`), system-owned,
   cleared by a tracking row.
2. **Built, then superseded** (owner 14 Oct, slice 6): C1 location narrowing for several
   unique-product candidates. Its tests now pin the group link instead.
3. **Built**: C2 assumed delivery in the ingest behind the setting, plus the marker-aware
   delivered rule; tests in `tests/test_do_history_assume_delivered.py`.
4. **Built**: C3, the optional `backfill` envelope flag. Its tests are in `tests/test_do_history_backfill_flag.py`, and
   section 13.2 of `PLAN-autocount-cross-repo-contract.md` is updated for the SS lane. The flag is additive and
   optional, so the contract version string stays as is (avoids a clash with #1623's 2.8).
5. The #1623 gate (Q4 a): its own follow-up lane, before the SS backfill runs (section 0b).
6. **Built (follow-up PR)**: group links, no warehouse narrowing (owner 14 Oct); tests in
   `tests/test_do_history_links.py`.

Trigger, not built: the waiting fill (`_fill_waiting_links`) re-tries every unlinked DO line, so
group-linked lines (permanent by the 14 Oct rule) share its 500-row window with lines still
waiting for their SO. Build a "group-linked" marker when a DO line waiting for a late SO is ever
seen staying unlinked after the SO arrived.

Not built: SO history, and a UI badge for assumed rows (trigger: the owner asks to tell them apart).
