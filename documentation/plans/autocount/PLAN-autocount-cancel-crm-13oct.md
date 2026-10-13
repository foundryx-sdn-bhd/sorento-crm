# PLAN: AutoCount Cancel SO / Cancel PO in the CRM (AC-CANCEL-CRM)

Status: Plan (behaviour card filed to owner as one crew-ask, awaiting answers). Track: full (migration).
Lane: AC-CANCEL-CRM. Sibling: AC-CANCEL-PULL (the shared service that reads the cancel docs; payload agreed via crew).

## Goal

The CRM accepts Cancel SO and Cancel PO documents from the shared service (ingest contract 2.8) and derives
cancelled quantity per SO / PO line on every sync, so cancelled quantity stops reading as delivered, received
or incoming.

## Owner rulings (13 Oct, closed)

- Cancelled qty is DERIVED on every sync: sum of cancel-doc lines for that SO/PO line. No events, no flags.
  Deleting the cancel doc in AutoCount lowers it. OI rows already retired stay retired.
- Partial cancel lowers planned qty; a line is "cancelled" only when fully cancelled.
- SRT first; Mocha later by configuration, same design.
- AutoCount is truth; no name / fuzzy matching. Cancel lines carry no line key since ~Nov 2024: match by
  SO/PO no + item (+ location + qty); ambiguous = manual review, never a guess (2026: 854 of 5,365 XS lines).
- Never stuck UI; every failure ends in a clear state.

## Measured (code on main fe6c08d9)

- SO `qty_delivered` IS AutoCount's SODTL TransferedQty, copied straight from the SO push
  (`document_ingest_service.py:244`, `:1291-1329`, setattr `:1550`). Nothing derives it from DO lines
  (DO ingest writes no SO quantity). PO `qty_received` is the same, from the PO push (`:276`, `:285`).
- Line status at push: header cancelled -> cancelled, delivered >= ordered -> closed, else open
  (`document_ingest_service.py:1828-1850`). A fully Cancel-SO'd line therefore lands `closed`, delivered = ordered.
- Owed / outstanding is `ordered - delivered` (`project_supply_service.py:225-247`, `:487-499`; `demand.py:168-174`;
  `committed_v`). Because TransferedQty = delivered + cancelled, outstanding is ALREADY right (cancelled qty is not
  owed). What is wrong is (1) the delivered figure people read, (2) the planned qty: `plan_qty_of` /
  `demand.plan_qty` = `coalesce(qty_required, qty_ordered)` ignores delivered by the 14 Sep ruling, so a cancelled
  unit is still planned and Confirm refuses the line (SO373923, `PLAN-so-cancel-line.md`), (3) the status: closed
  instead of cancelled.
- Same on PO: incoming = `qty_ordered - qty_received` (`project_supply_service._po_rows:9772`) is already right;
  "received" is overstated.
- SPO (`spo_allocations`, the shipping order): receipt is `max(GRN receipt, stated_received)` where stated_received is
  AutoCount TransferedQty (`procurement_service.py:1189-1213`, `shipping_order_ingest_service.py:989-1014`).
  Whether a Cancel PO can name an SPO is being checked with AC-CANCEL-PULL / dev data.
- Existing cancel plumbing reused: `SalesOrderLine.cancelled_at/_by_id/cancel_reason` (#1524, board cancel),
  `retire_rows_for_cancelled_lines` (`project_order_inquiry_service.py:4498`, #1530: OI rows cancelled, links
  removed, handover email "Transferred to Cancel SO"), `flag_rows_for_cancelled_lines` (`:563`), the planning-change
  diff around an SO push (`document_ingest_service.py:1236-1280` -> `planning_change_service`), #1599's
  undecided-change guard.
- DO / GRN ingest pattern to copy: `autocount_doc_ingest_service.py` (vendor PascalCase record with `Details`,
  envelope `book`, idempotency (company, book, DocKey), LastModified stale guard, `unchanged` verdict, waiting-link
  retry at batch end, `/deletions` with `doc_keys` + DocDate range marks vanished, never deletes).

## Behaviour card (filed as ONE crew-ask)

See the PR comment `crew-ask` for the questions. Rules proposed:

R1. Two new push entities, `cancel_sales_orders` and `cancel_purchase_orders`, same wire shape and envelope as
    `delivery_orders` / `goods_receive_notes`. Stored as one header + lines table pair with a `kind`.
R2. Each cancel line is matched to ONE target line, or to none with a reason, at ingest and again at the end of
    every batch (waiting links): exact `FromDocDtlKey` when present; else the lines of SO/PO `FromDocNo` with the
    same item; one candidate = match; several = narrow by Location; still several = narrow by qty (see Q2); still
    several = `needs_review` with the candidate lines listed. Unknown SO/PO = `waiting` (retried, never failed).
R3. `qty_cancelled` on the target line = sum of matched lines of non-vanished, non-cancelled cancel docs, recomputed
    for every touched target on every batch (and on a deletions sweep). Capped at ordered.
R4. Delivered shown = TransferedQty - cancelled. Planned = coalesce(required, ordered) - cancelled. Outstanding
    unchanged (already ordered - TransferedQty).
R5. Fully cancelled (cancelled >= ordered) -> line status `cancelled`, OI rows retired by the #1530 path. Partial
    -> planned qty drops and an AutoCount planning change `qty_down` is raised for CS like an SO push edit.
R6. Cancel doc deleted / cancelled in AutoCount -> cancelled qty drops on the next sync; a line that was fully
    cancelled reopens (open/closed by its quantities); OI rows already retired stay retired, the board shows the
    line to plan again.
R7. Every outcome is visible: `needs_review` and `waiting` lines are listed with their reason; nothing spins.

## Test list (red first)

Filled after the owner's answers.
