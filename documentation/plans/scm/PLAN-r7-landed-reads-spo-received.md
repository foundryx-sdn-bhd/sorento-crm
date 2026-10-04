# PLAN: R7 own-arrival credit reads what LANDED on the SPO, not what the PO transferred

Status: in review, PR open (24 Sep 2026); #1362 rounds 4 and 5 on PR #1363 (29 Sep 2026); coder green (38 pass across the five s3 files), reviewer round 1 folded (B1 kill tests, S1 planning-engine sentence, S2 company equality on the batched join, N2 docstrings). Open ruling S3 below. Merge needs owner go. Track: small fix track, stretched - one seam, no migration, no auth change, but the R7 test fixture seeds the wrong document so four existing test files move with it. One coder writes tests + fix, one reviewer, no browser pass (no UI code changes; the refusal message is asserted in pytest). Lane `fix/r7-landed-reads-spo-received`, worktree `sorento_crm-r7-spo`. Follow-up to `PLAN-board-received-stock-own-arrival.md` (R7, PR #1092, merged as 2280975f9).

## The problem, measured (SO399639 line 58 / core line 2120, C-FHSS18, prod 24 Sep 2026)

Board Confirm refused with:

```
SO399639: 1 landed for this line on PO 202607-S0079; nothing to buy for it
Line 58, C-FHSS18: 1 landed for this line on PO 202607-S0079; nothing to buy for it
```

Prod rows behind it (owner ran `scratchpad/so399639-*.sql`, 24 Sep):

| Row | Value |
| --- | --- |
| `sales_order_lines` 2120 | `source_ref AED_SORENTO:44533747:44560139`, qty 7, delivered 0, open, required 2026-09-10, bin BRW-IB |
| `purchase_order_lines` on PO 202607-S0079 | `from_so_line_ref` = that ref, `source_ref AED_SORENTO:45346073:...`, qty_ordered 7, **qty_received 7**, updated 24 Sep 02:05 |
| `spo_allocations` SPO-2026/09-0091 line 403 | 7 pcs, BRW-IB, `quantity_received 0`, expected 2026-09-18, `from_po_number 202607-S0079`, `from_po_line_ref` = the PO line's `source_ref` |
| `stock` C-FHSS18 | BRW-IB 1, BRW-SMC 40 |

Nothing has physically landed. The PO line's `qty_received = 7` is the AutoCount TRANSFER of the
line onto the shipping order (owner, 24 Sep: "PO received 7 means it is converted to SPO, it
doesn't mean it is received"). R7 (`_own_arrival_credit_components`) reads
`purchase_order_lines.qty_received` as "landed", gets 7, caps it by on hand at BRW-IB (1) and
refuses the Buy the board itself proposed (the SPO is late against 2026-09-10, so the proposal
was Buy 7 - correct per the owner's ruling below).

Every PO in this business is received through an SPO (owner, 24 Sep: "all PO received via SPO for
order inquiries"). So `purchase_order_lines.qty_received` NEVER means landed; the physical
receipt is `spo_allocations.quantity_received`.

The SO372176 case that built R7 did not show this because there the PO lines and the SPO
allocations were BOTH fully received (parent plan, "The problem, measured", line 18), so the two
readings agreed.

## Rulings (owner, 24 Sep 2026)

- **R1** "Landed for this line" = `spo_allocations.quantity_received` on the SPO rows that carry
  the line's purchase-order line, resolved SO line -> PO line -> SPO row. PO `qty_received` is a
  transfer, never counted.
- **R2** A late own-SPO (arrival after the line's required date) still gets a Buy proposal ("buy
  is fine"). No change to `_group_water` / R32.
- **R3** The refusal names the SPO number (the document the goods landed on), not the PO.

## The chain in data (measured on the 0921 copy, 24 Sep)

`sales_order_lines.source_ref` == `purchase_order_lines.from_so_line_ref` (already how R7 finds
the PO line) and `purchase_order_lines.source_ref` == `spo_allocations.from_po_line_ref`
(817 of 817 open SPO rows with a ref resolve; `spo_allocations.po_line_id` is NULL on every one
of the 1,152 open rows, so it is not the key). Product and company filters stay exactly as R7
states them.

## The fix (one seam, two reads)

`app/services/project_supply_service.py`:

1. `_po_received_by_so_line_ref` (:2731) - the per-order read. Join
   `SPOAllocation` on `SPOAllocation.from_po_line_ref == PurchaseOrderLine.source_ref` (same
   product, same company filter as today), sum `SPOAllocation.quantity_received` per
   `from_so_line_ref`, and return the SPO's `spo_number` as the document. A PO line with no SPO
   row contributes zero. `qty_received` on the PO line is no longer read.
2. `_prefetch_own_arrival` (:2788) - the batched read of the same shape. Same join, same
   bucketing by (product, company).

Nothing else changes: `_own_arrival_credit_components`, the on-hand cap, tier 2's sibling spare
(`spare = received - min(received, sibling.qty_ordered)`, now off SPO received), the ledger,
`_check_line`'s refusal and `planning_change_service._refuse_buy_over_own_arrival` all read the
same tuple `(qty, document)` and keep working. The message template already says "on PO {x}";
change the noun to "on {x}" so an SPO number reads right (`:5517`, and the
`planning_change_service.py:2821` twin).

## Tests (pytest, Postgres, `tests/scm/`)

Fixture `tests/scm/_own_arrival_fixture.py` `po_line_bought_for` gains the SPO half: it writes the
PO line as today (a transfer, `qty_received = qty_ordered` to mirror what the book import
stamps) AND an `SPOAllocation` row with `from_po_line_ref = po_line.source_ref`,
`from_po_number = po.po_number`, `quantity_received` = the fixture's `qty_received` argument,
`line_status` / `receipt_status` derived from it. Existing tests in the four
`test_board_received_stock_s3_*.py` files keep their assertions; only the document name they
expect changes from the PO number to the SPO number where a test pins it.

New tests (one file, `test_board_received_stock_s3_spo_received.py`):

- **T1** PO line transferred (`qty_received = qty_ordered`), SPO row `quantity_received = 0`,
  on hand at own bin 1, Buy 7 posted -> Confirm succeeds, credit 0, no refusal (the SO399639
  shape).
- **T2** Same, SPO `quantity_received = 7`, on hand 7 -> refusal names the SPO number, credit 7.
- **T3** PO line with `qty_received = 7` and NO SPO row -> credit 0 (R1: PO received is never
  landed).
- **T4** Tier 2: a closed sibling's SPO row received 12 against ordered 10 -> spare 2 credited
  to the open line; the sibling's PO `qty_received` set to 99 does not change that.
- **T5** `_prefetch_own_arrival` and `_po_received_by_so_line_ref` return the same tuple for
  the same line (the batched and single reads agree).

## Open ruling (S3, reviewer 24 Sep)

Retired SPO rows (`retired_at` set, the old DocKey when AutoCount re-creates a shipping order)
keep their `quantity_received` and are counted by both R7 joins, the same way
`spo_supply.visible_line_clauses()` keeps them visible as real goods. On the 0921 copy 6
`from_po_line_ref` values carry both a retired and a live row with receipts, so those could
double-count. Built as A (count them, consistent with the board); owner may rule B (exclude
`retired_at IS NOT NULL` from R7 only).

## Follow-up: #1362, landed and free said apart (owner ruling 29 Sep 2026)

Owner, 29 Sep 2026, on SO382618 line SRT357 (100 landed on SPO-2026/06-0152 at BRW-BB, on
hand 261, trail read "40 landed for this line"): "i check 100 is for this line, why it says
40 ah?" then "please fix this". Ruling: the sentence states two facts apart. The first is what
landed for the line (tier 1 plus tier-2 spare, capped by the line's need). The second is how
much of it is still free at the bin and taken first. Example: "100 landed for this line on
SPO-2026/06-0152; 40 free at BRW-BB, taken first". When less is taken than is free (a pool
share covered part of the line first), the taken figure is said as a third number.
`front_planning_engine.own_arrival_reason` builds it for both the component reason and the
board trail (`_group_take_why`).

Measured cause of the 40 (reproduction, 29 Sep): the credit ledger is already per (product,
bin) (`compose_lines`, `_check_line`, the order-inquiry picker), so no other product is
involved. An EARLIER-due line of the SAME product at the same bin, with no PO of its own,
drew 221 of the 261 through the ordinary group take, and AC-S3-11 charges that draw to the
same ledger. Ruled 29 Sep 2026, built in round 5 below.

## Follow-up: #1362 items 2 and 3 (owner, 29 Sep 2026)

Item 2, shipment naming. `_po_received_by_so_line_ref` summed every SPO row landing the PO
line but named the first one found, so SO382618 line 400 read "on SPO-2026/06-0044" while its
order-inquiry rows link SPO-2026/06-0092 and SPO-2026/07-0019. Now (`_landed_for_refs`):
where the line's order-inquiry row links a specific SPO row, that link names the shipment and
its landed figure is tier 1, `min(link qty, allocation quantity_received)` per link, even when
the PO line's own receipts add to a different number. Otherwise the PO line's receipts stand
and every contributing shipment is named with its quantity: "100 landed for this line: 60 on
SPO-2026/06-0092, 40 on SPO-2026/07-0019". One helper, `front_planning_engine.landed_on`,
words the board sentence and both amend refusals.

Item 3, a stale decision on a fulfilled line. A named line whose plan quantity is 0 is skipped
by `confirm` (not checked, not guarded), counted as `lines_fulfilled_skipped`, and its saved
draft is deleted (cleared, not marked superseded: a draft has no history of its own, and on a
line with nothing open there is nothing left for it to decide). `_refuse_buy_over_own_arrival`
returns early for such a row. The cell drawer states "Fulfilled, N delivered, due <date>" in
place of the suggestion. The stock drawer lists the drawer's own fulfilled line at zero, so
"My line" finds it. There is no delivered-date column on `sales_order_lines`, so the date said
is the line's due date.

## Follow-up: #1362 round 4 (owner evidence, 29 Sep 2026)

Item 4, a tier-2 spare says whose purchase it was. SO382618's PO 202607-S0077 bought 200
B2154-NL for line 1648 (100 pieces) and landed all 200 on SPO-2026/09-0036. Line 1648 reads
"100 landed for this line on SPO-2026/09-0036"; line 2912 reads "100 spare from line 1648's
purchase (200 bought for 100) landed on SPO-2026/09-0036", never "landed for this line"
(`ProjectSupplyService.own_arrival_landed_text`). The board trail, the component reason, the
amend refusal and the confirm refusal all use it.

Item 5, AutoCount line numbers. Board contributions and confirm failing lines carry
`so_line_no` (`sales_order_lines.line_no`). The Line column, the drawer's line labels and the
confirm messages print it; "row N" only where AutoCount gave the line no number. `line_no`
stays the planning address (the draft key).

Item 6, a same-date unit member read the unit's first core line for tier 1, so a line with no
purchase of its own was credited its sibling's receipt (100 landed, 160 credited). Each member
now reads its own core line.

## Follow-up: #1362 round 5, landed goods stay with their line (owner ruling 29 Sep 2026)

Owner: "we cannot snatch, what's ordered against the SO should stay belonged to it, we cannot
simply say it belongs to another order, this is not the right process". Built in the ONE
assignment every reader shares (R21): `StockDebtService._landed_holds` pins what landed for a
line (tier 1, `min(landed, open)`, less the line's own decision and placement holds) to the
line's own bin floor, before the walk. Only a floor already in the read is pinned, and
`assign()` caps a pin at what the floor holds, so no stock is invented. The pin is marked
`landed`, and `_drawn_at_own_date` offers it to its own line only, never to another member
of the same planning unit. AC-S3-11 is amended accordingly (see the UAC). The Stock Debt view
reads the same assignment, so a line whose goods landed reads `pinned` there too.

Still true, with one addition (STOCK-DEBT-LENDABLE, owner 30 Sep 2026, option B,
`PLAN-stock-debt-lendable.md`): in the Stock Debt VIEW only, a landed pin whose line is due on
or after `today + lead + 14` (the board's own borrow-donor window) is lendable - nearer lines
draw it first and the far line reads `order_back` for what it lent, a planned re-buy at its own
date. Nothing is snatched: the far line keeps its claim, and the fulfilment board (where the
far line is a Borrow donor under the same window) is where the lend becomes a decision with
an order-back. The board and the ladder still pin; the view writes nothing.

## Follow-up: #1362 hand test, a Buy over landed goods is recorded, not refused (owner 29 Sep 2026)

Hand test: SO382618 confirm-all refused the whole order on one line (planning row 29, a saved
Buy over 100 that landed for it on SPO-2026/06-0131). Owner: "i think we are too restrictive
already", then "this good is on hand, and is covering the line, but, from fulfilment planning,
is kind of requesting it to be delayed while the link is intact, then only purchasing will do
the adjustment in the linkage". Built: the confirm recheck no longer refuses a Buy over landed
goods (it records a notice, returned as `landed_buy_notices` and written on the Buy's own order
inquiry row `note`, which a buyer must acknowledge anyway); the amend guard
`_refuse_buy_over_own_arrival` is removed; the board's Decide Buy no longer skips such a row
(AC-51). Any other per-line refusal at confirm-all holds that line back (`lines_held_back`,
decision kept) and confirms the rest; a planning-change batch is still applied whole. AC-S3-6,
AC-S3-15 and AC-51 are marked superseded in their UACs.

## Out of scope

- The board proposing Buy for a late own-SPO (R2 keeps it).
- `spo_allocations.po_line_id` being NULL everywhere.
- The parent plan's Status line (still "in review" though #1092 merged) - archived separately.
