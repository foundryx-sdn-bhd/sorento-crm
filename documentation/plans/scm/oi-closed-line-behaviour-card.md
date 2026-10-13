# OI-CLOSED-LINE behaviour card v2 (owner's model, 11 Oct 2026)

Owner model: AutoCount delivered qty and the plan / OI are decoupled. The OI changes ONLY through CS decisions
confirmed in Fulfilment Planning; Confirm sends the purchasing (handover) email; AutoCount sync never touches OI
rows. BRW never needs refilling (a Use BRW / borrow decision raises no Buy).

Evidence DB: `sorento_clp_live` (prod copy to 2026-10-08, read-only). Caveat: other lanes' tests cancelled lines
of SO373923 in that copy (cancel reasons "asdf", "crew proof SO-CANCEL-HANDOVER"), so its `cancelled` statuses
are not prod truth; delivered qty, decisions and batch rows are.

## Is the decoupling how the code works today? Yes, with one hole

- Plan qty = `coalesce(qty_required, qty_ordered)`, never minus delivered: `plan_qty_of`,
  `project_supply_service.py:500-517` (14 Sep 2026 ruling in its docstring). Confirm's facts use it
  (`:8881`); drift check compares it (`:5509-5511`); the components must sum to it (`:6055-6061`).
- Delivered-based `_open_of` (`:486-498`) is read only for the hold cap, the board's "outstanding" column
  (`project_fulfilment_board_service.py:1610`) and staleness; it does not decide what is planned or bought.
- Confirm does not filter closed lines (`lines_of`, `:1226-1232`); the board admits closed-by-delivery undecided
  lines since 14 Sep (`project_fulfilment_board_service.py:733-739`).
- Buy residual = the decided `buy_qty` minus placed (`project_order_inquiry_service.py:1033`, `:1356-1376`);
  delivered never enters. A new decision with buy 0 cancels raised ORDER rows "Superseded by revision N" and
  queues each to the handover email (`:1349-1368`).
- AutoCount ingest writes `qty_delivered` / `line_status` only (`document_ingest_service.py:244`,
  `:1828-1851`); it touches OI rows only when a line is CANCELLED (`:1555-1609`, flips ack to `changed`).
- THE HOLE: batch apply drops any row whose core line is `closed` before confirm runs
  (`planning_change_service.py:4170-4192`, reason "the sales-order line is closed"; #1092 ruling R3
  "minimum only"). The decision never reaches confirm, refresh_for_decision or the email.

## Rules

1. A CS decision applies whether the SO line is open or closed. Delete the closed-line drop at
   `planning_change_service.py:4170-4192`; a `cancelled` line keeps its own path (`kind == "cancelled"`).
2. Confirm updates the OI rows exactly as today (buy 0 -> raised ORDER rows cancelled; placed > need ->
   CANCEL_BALANCE) and lists every changed row in that commit's purchasing email. No new email path.
3. AutoCount sync never changes OI rows for a delivery. No change to ingest.

## What the 153 stale rows mean under this model (raised ORDER rows on closed, fully delivered lines)

| Group | Rows / OIs | Meaning | Action |
|---|---|---|---|
| Order has no supply decision at all; all raised 2026-09-20 | 85 / 26 | never decided on the board | wait for CS; nothing automatic |
| Active decision says Buy > 0 for the line | 65 / 26-28 | CS decided Buy; OI matches the decision | correct under the model; CS re-decides if wanted |
| Decision dropped "line is closed" | 3 / 2 | the bug | fixed by rule 1; clean-up below |

Dropped decisions in the copy: 7 batch rows (SO373923 x6 on 6 Oct, SO406808 SRTSC07 on 28 Sep).
SO373923 batch built 02:49, AutoCount closed 5 lines 02:58:59, CS applied after: compositions were
Reserve BRW (SRT25-CR 72, SRTWT2200 72, SRTWT2203 8, SRTWT2216 6) and Borrow OB BRW-BB (CKS1050 8,
SRT1000-CR 72), all buy 0. Applied, each would have cancelled its raised ORDER row and listed it in the email.

## Edge cases

- Delivered line + Reserve decision: the hold cap reads `_open_of` = 0 (`project_supply_service.py:509-512`
  docstring), so no stock hold is written for delivered qty. But confirm's capacity recheck still reads LIVE free
  stock at the reserve bin (`:5681-5740`); the BRW units already left on the delivery, so the recheck may refuse
  ("<bin> has nothing free for this line now") or eat another order's stock. UNVERIFIED which; see Q1.
- Line closed after the batch was built (the complaint): applies normally once rule 1 lands.
- Line closed, then reopened (delivered qty lowered): plan qty unchanged, nothing to do.
- PO-linked rows: unchanged; a lower need raises CANCEL_BALANCE as today (`:1641-1660`).
- Cancelled lines: unchanged (Transfer to Cancel SO path, `project_supply_service.py:1234-1328`).

## Questions

1. Capacity recheck on a delivered line: (a) skip the free-stock recheck for the delivered part of a closed line
   (the goods are gone; the decision records where they came from), (b) keep the recheck (CS may get a refusal on
   exactly these lines). Rec (a): with (b) the SO373923 Use BRW decisions could still fail to apply. Red test
   decides whether (b) actually refuses.
2. CKSW015 (SO373923): ORDER `placed` 8 with no PO link and no actioner, plus CANCEL_BALANCE 8 "Placed 8, new
   need 0" from the 6 Oct borrow decision. (a) clean-up cancels the CANCEL_BALANCE only, (b) also cancels the
   unlinked placed ORDER. Rec (a): placed is purchasing's own mark; asking them is safer than erasing it.

## Clean-up for OI-2609-0724 (owner runs, after the fix deploys)

Preferred: CS re-decides the dropped lines on the board and clicks Confirm; with rule 1 the normal path cancels
the raised rows and sends the purchasing email. A script covers only what Confirm cannot reach:
`scripts/oneoff/oi_closed_line_cleanup.py --inquiry OI-2609-0724 [--apply]`, default dry run, one transaction:
1. List raised ORDER / ORDER_BACK rows of the OI whose line has a batch row superseded "the sales-order line is
   closed" or a cancelled core line, with item, qty, the dropped composition.
2. Cancel those rows, note "Decision of <date> dropped (line closed): <composition>; applied by clean-up",
   and queue them to one handover email (same `_record_handover` path, so purchasing is told).
3. CKSW015: per Q2.
4. Prints before/after; `--apply` commits, otherwise ROLLBACK. No link or stock changes.
