# UAC - OI-RESERVE-RECEIVED

Plan: `PLAN-oi-reserve-received.md`. Card: `oi-reserve-received-card.md` (crew answers 15 Oct:
Q1 (a), Q2 (a), Q3 yes, Q4 (a), Q5 texts as written).

Terms:

- **Received link.** A PO/SPO link whose target the existing "received" test calls fully
  received. This is the test behind the worklist's received pill (`links_for_rows`,
  project_order_inquiry_service.py:6624-6653). For a PO line: closed, or qty_received >=
  qty_ordered. For an SPO allocation: not open incoming (`spo_supply.open_incoming_clauses`).
- **Open link.** Any other PO/SPO link.
- **Requestable.** row.qty - open links - reserve links - bundled_qty.
- **Plain remaining.** Today's `_remaining`: row.qty - every link - bundled_qty.

## Request

- **AC-RR-1.** A live ORDER / ORDER BACK row in state raised, partly_linked or placed can be
  requested when requestable > 0. Example: placed, 1260 on a received SPO, requestable 1260.
- **AC-RR-2.** A row whose only links are open (incoming, or a partly received PO line) has
  requestable 0. The request is refused with 409 `reserve_request_nothing_remaining`.
- **AC-RR-3.** qty_requested must be > 0 and <= requestable. Otherwise 422
  `reserve_request_qty_out_of_range`, and the message names requestable.
- **AC-RR-4.** Unchanged: actioned, cancelled and redirected rows, other verbs, and rows that
  already have an open request are refused exactly as today.
- **AC-RR-5.** The OI detail row payload carries `reserve_requestable_qty`, a number, so the FE
  never re-derives "received".

## Commit (CS answers)

- **AC-RR-10.** cap = min(qty_requested, requestable at commit). A higher qty gives 422
  `reserve_qty_out_of_range`. A reason is still required below the cap (existing rule).
- **AC-RR-11.** Release mode applies when the CS qty is > 0 AND qty_requested > plain remaining
  at commit (the request reaches into the received part). In release mode EVERY received link on
  the row is deleted; open links stay.
- **AC-RR-12.** Each released link writes one row to `projects.order_inquiry_released_links`:
  company, OI row, reserve request row, the same target (po_line_id or spo_allocation_id),
  document, qty, released_by, released_at.
- **AC-RR-13.** Each released link stamps the row note:
  `Released {document} (received, stock used by other orders) for CS reserve {qty} @ {loc}`.
  `{qty}` is the CS qty and `{loc}` the reserve warehouse code. The note shows in the Lines
  History "Why" column.
- **AC-RR-14.** The reserve link is written as today (`Reserved @ {loc}`, CS qty). Row state and
  bundle are re-derived as today (`refresh_link_state`).
- **AC-RR-15.** Full accept example: 1260 received, CS 1260 @ BRW. Result: no SPO link,
  reserve 1260, state placed, plain remaining 0, one released row of qty 1260.
- **AC-RR-16.** Partial example: 1092 received, CS 435 @ BRW with a reason. Result: no SPO
  link, reserve 435, state partly_linked, plain remaining 657, one released row of qty 1092.
- **AC-RR-17.** Decline: CS 0 with a reason. The received link stays, no released row is
  written, the state is unchanged and the reserve event is written as today.
- **AC-RR-18.** Not reaching: row 200, received link 150, unlinked 50, request 50, CS 50. The
  received link stays, no released row, reserve 50, plain remaining 0.
- **AC-RR-19.** Mixed: 158 received + 24 open, request 158, CS 158. The 158 link is released,
  the 24 open link stays, reserve 158, plain remaining 0.
- **AC-RR-20.** Bundle: qty 420, bundled 266, 154 received. Requestable is 154. CS 26 releases
  the 154 link and reserves 26. Bundle is then re-derived (no assertion on its new value beyond
  `refresh_link_state`'s own rule).
- **AC-RR-21.** Amending a reserve after a release (down, even to 0) never restores or recreates
  the released link. The released row stays.
- **AC-RR-22.** Reserved email: for a released row the context row's `reason` starts with
  `Released {doc}[, {doc2}...] (received).`. If CS gave a reason it follows after one space.
  Template text is unchanged.

## Never re-linked (owner rule 3)

- **AC-RR-30.** `_linked_by_target` adds released qty to each target's tally, per PO line and
  per SPO allocation. The row's own sums are unchanged: Taken, `_remaining`, `refresh_link_state`,
  worklist, demand and stock debt never read the released table.
- **AC-RR-31.** Book step (`follow_book_for_rows`, and through it Link now, Auto link, Confirm,
  PO confirm and the PO/SPO ingest hooks). The case: an SPO allocation whose `from_so_line_ref`
  names the row's SO line, after a release. The book step does not re-link that target to the
  row, even when the row has a shortfall.
- **AC-RR-32.** Same target, a DIFFERENT SO line whose book names that target: the book step
  does not link the released qty to it either (capacity stays used up).
- **AC-RR-33.** D3 displacement never strips another SO line's link off a target to re-link a
  row onto a target it released.
- **AC-RR-34.** `relink_to_matching_lines` counts released qty as claimed on the PO line, so a
  released received PO line is not chosen as a move target.
- **AC-RR-35.** A fresh row with NO release whose book names a received line still links through
  the book step as today (sheet importer behaviour unchanged).

## Screen (OI Lines tab, "Request CS to reserve")

- **AC-RR-40.** A selected placed row with `reserve_requestable_qty` > 0 enables the action. The
  dialog defaults to and caps at requestable (1260 in the SRT6638 example).
- **AC-RR-41.** When requestable <= 0 and the row has any PO/SPO link, the tooltip reads
  `{item}: on a PO/SPO not received yet, nothing left to request`.
- **AC-RR-42.** Other tooltips are unchanged: `{item}: not open for a reserve request` for
  actioned/cancelled, `{item}: has nothing left to request` when requestable <= 0 with no
  PO/SPO link, and the open-request text.
- **AC-RR-43.** After a full accept the row shows no SPO document, the pill reads Reserved 1260,
  and Remaining is 0. After a partial accept, Remaining is the shortfall (657).
- **AC-RR-44.** Usable at 1280 and 375.

## Migration

- **AC-RR-50.** One additive alembic revision (id <= 32 chars) chained onto main's head. It is
  `IF NOT EXISTS` safe, creates `projects.order_inquiry_released_links` with UUID id and indexes
  on po_line_id, spo_allocation_id and row_id, and has no backfill. `alembic heads` = 1.
  `tests/test_schema_uuid_id_principle.py` is green.
