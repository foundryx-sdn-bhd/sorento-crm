# UAC: DO history backfill (DO-HISTORY)

Plan: `PLAN-do-history-backfill-13oct.md`. Status: draft, awaiting owner OK. Assumes the
recommended answers to Q1-Q5; an answer the other way rewrites the matching criteria.

## Landing (step 1)

- **AC-DH01** The backfill dry run for a (book, month) writes nothing (row counts of `orders`,
  `order_lines` unchanged) and prints created / updated / unchanged / adopted / failed / retryable
  documents and linked-by-key / linked-by-product / unresolved lines.
- **AC-DH02** Applying the same month twice leaves the second run all `unchanged` (no duplicate DO,
  same ids).
- **AC-DH03** A run stopped after month N resumes at month N+1 without re-applying months 1..N.
- **AC-DH04** A month over 10,000 documents is split and both halves land.
- **AC-DH05** A pre-cutover DO whose DocNo matches an Order Tracking row is adopted: same id,
  tracking columns unchanged.
- **AC-DH06** After the relink pass, a backfilled DO line whose SO (FromDocNo) has exactly one line
  of its product is linked, whatever the month order of landing.
- **AC-DH07** The backfill writes no SO line `qty_delivered`, `line_status` or SO `status`, raises
  no OI row, planning change, plan exception, email or WhatsApp.

## Assume delivered (step 2)

- **AC-DH10** A pre-cutover AutoCount DO with no tracking becomes `delivered`, `actual_delivery_date`
  = its DocDate, `tracking_assumed_at` set; it leaves the DO outstanding bucket and the DO side of
  the outstanding report.
- **AC-DH11** A pre-cutover DO with any tracking (a date, or a status other than `new`), a cancelled
  DO, a deleted DO, an Excel-only DO (`doc_key` NULL) and any DO dated on or after the cutover are
  untouched.
- **AC-DH12** Step 2's dry run prints the count it would mark and writes nothing.
- **AC-DH13** Step 2 writes no row audit, no embedding event and no delivery notification; it
  writes one summary audit event per run.
- **AC-DH14** Revert of a run restores `new`, NULL date and NULL marker on exactly the rows it marked
  that have no later tracking.
- **AC-DH15** An Order Tracking upload that sets real delivery fields on an assumed row clears
  `tracking_assumed_at`.
- **AC-DH16** `tracking_assumed_at` is in `SYSTEM_ORDER_COLUMNS`; the ingest never writes it.

## Readers

- **AC-DH20** SO-side outstanding, Stock Debt, Coverage and committed totals are identical before
  and after both steps on the same data.
- **AC-DH21** (Q4 a, after #1623 merges) A DO-triggered cancel relink on an SO ordered before the
  cutover raises no planning change and flags no OI row; `qty_cancelled` still updates.
