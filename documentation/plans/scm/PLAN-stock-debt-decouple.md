# PLAN - Stock Debt fully decoupled from Fulfilment Planning pins (STOCK-DEBT-DECOUPLE)

Status: PARKED 14 Oct 2026 14:10 (owner scaled back: no decoupling, no pool change; the lane
switched to the CB6633-NL delivered-credit fix, see PLAN-own-delivered-first.md). Owner had
answered Q1 (a), Q2 (a), Q5 (a); Q3 and Q4 never answered. Kept as the record of the card.
UAC: `stock-debt-decouple-acceptance-criteria.md`. Domain: SCM, Stock Debt view
(`StockDebtService._assignments(view=True)`). The Fulfilment Planning board
(`assignments_for`, `view=False`) is untouched.

## The owner's words (14 Oct 2026)

"Stock Debt is FULLY DECOUPLED from Fulfilment Planning. It answers what do we owe vs the
stock we really have and have coming, by due date. Planning pins never change Stock Debt
cover." And, on the card: "Covered by can be by PO, on hand... PO ones are pinned by
AutoCount; on hand should be the only one we need to calculate."

This supersedes, for the Stock Debt VIEW only, R21 / AC-S2-2 / AC-S2-1b of
`PLAN-scm-borrow-ladder-v7-stock-debt.md` (lines 25, 51, 61) and
`scm-borrow-ladder-v7-stock-debt-acceptance-criteria.md` (lines 45-46). The board keeps them.

## What main does today (dd6ab9864)

`_assignments` (`app/services/scm/stock_debt_service.py:1026-1190`) builds holds and hands
them to `assign()` (`supply_assignment.py:600`), which spends every hold first at any date
(`:673-781`, stand-ins for out-of-span bins `:748-766`) and then walks on hand by due date
per ownership group (`:783-804`). SPO/PO events are `pin_only` in the view (R45,
`stock_debt_service.py:1078`), so a document reaches a line only through a hold.

| Hold source | Where | Origin |
| --- | --- | --- |
| Board decisions (`so_line_allocations`, Reserve own/brw/group_take, Borrow other_location/other_project) | `_holds` `:1508-1534` via `_hold_query` (`project_supply_service.py:9487`) | CRM planner |
| OI placements (`order_inquiry_links`), incl. received-SPO placement turned on-hand hold `:1626-1667` | `_holds` `:1536-1685` | CRM record; auto=True copies AutoCount (follow-book, `project_order_inquiry_service.py:3202`), auto=False is a manual link. Dev: 763 of 4,820 live placements on open SOs do not match the line's AutoCount ref |
| Landed goods | `_landed_holds` `:2051`, via `_po_received_by_so_line_ref` (`project_supply_service.py` ~3000) | AutoCount (PO `from_so_line_ref` + SPO receipt), but prefers OI links (`_landed_for_refs` `:349`) and nets CRM holds (`:2085`) |
| Book S/O | `_book_so_holds` `:2125`, `book_so_pins` `:171` | AutoCount `from_so_line_ref` (PO line, SPO row's OWN ref only), less live placements (`:2216-2249`) |

## The rules (view only)

- **D1 Document cover is AutoCount's link and nothing else (Q1 a).** A PO line covers the
  sales-order line its `from_so_line_ref` names. An SPO carries no SO link of its own in the
  owner's model: it covers the line its PO names (`SPOAllocation.from_po_line_ref` ->
  `PurchaseOrderLine.source_ref` -> `from_so_line_ref`, same product, same company). Dev:
  every one of the 10,624 SPO rows that does carry a `from_so_line_ref` agrees with its PO
  chain; the SPO's own ref is read only when it names no PO line (3 rows). Landed goods are
  the received quantity of SPO rows on that chain (R7's read, without the OI-link
  preference). Placements and decisions never pin and never reduce what the S/O link pins.
- **D2 On hand is walked by due date per ownership group, as today.** No decision moves
  stock between groups or stands stock up outside the span. `book`/`group`/date filters
  keep their R14 meaning. HELD (Q3 open): a decision whose bin is a site pool keeps today's
  behaviour (it still pins) until the owner answers.
- **D3 A PO/SPO with no AutoCount link stays out of the walk**, free in its own month (R45
  kept, Q2 a).
- **D4 HELD (Q4 open): Planned info column.** Recommended: board decisions plus placements
  whose document is not in the line's Covered by. Not built until the owner answers.
- **D5 The pill keeps reading "pinned"** (Q5 a); it now means an AutoCount link or landed
  goods.
- **D6 Unchanged:** lendable / order back (STOCK-DEBT-LENDABLE, landed goods), R42/R43 PO
  dating and "assigned means fulfilled", R44 overdue off, the projection formula (it reads
  the walk, so pool borrows stop reaching project lines there too), export columns.

## Changes, file by file

Backend (`app/services/scm/stock_debt_service.py` unless named):
1. `_assignments`: when `view`, no placement and no decision from `_holds()` enters
   `assign()`, except (HELD, Q3) a decision whose bin is a site pool
   (`self.supply.site_pool_warehouses()`), which pins as today. `_landed_holds` and
   `_book_so_holds` see only what is left. The board branch is byte-identical.
2. `_landed_holds` (view): landed = AutoCount chain only. Add a keyword to
   `ProjectSupplyService._po_received_by_so_line_ref` (default = today's behaviour, so R7 and
   the board read the same) that skips `_oi_linked_landed_by_ref`.
3. `_book_so_holds` (view-only already): drop the `placed` subtraction; resolve an SPO
   event through its PO (`from_po_line_ref` -> PO `source_ref` -> `from_so_line_ref`, one
   batched read, product + company paired as `_po_received_by_so_line_ref` does), the SPO's
   own `from_so_line_ref` only when it names no PO line.
4. HELD (Q4): `cell()` `planned` per demand row, schema `StockDebtDemandLine.planned`.
5. Docstrings: `_holds`, `_assignments`, `supply_assignment` module rule 1 say the view no
   longer reads them.

Frontend (`app/(protected)/project-sales/stock-debt/`):
6. HELD (Q4): `planned` type + "Planned" column in `StockDebtCellDialog.tsx`. No FE change
   in R1-R3 (Q5 a keeps the label).

Docs: v7 plan + UAC get a "superseded for the view by STOCK-DEBT-DECOUPLE" note at the lines
above.

## Tests (tester writes them first, red)

New `tests/scm/test_stock_debt_decouple.py` (route level, private DB): one test per UAC
AC-1..AC-11. Existing view tests whose expectation the ruling flips are re-pointed in the
same red commit (list from the FE/BE map: `test_stock_debt_routes.py` :688, :821, :996,
:1161, :1574, :1634, :1699, :1799-1894; `test_stock_debt_lendable_routes.py` :428/:461/
:476; `test_stock_debt_po_supply_walk.py` :136; `test_stock_debt_pinned_po_fulfils.py`
:226; `test_stock_debt_projection_linked_only.py`; `test_stock_debt_projection_routes.py`
:171/:183/:353; `tests/test_project_supply_holds.py` :282;
`tests/test_order_inquiry_suggested_links_wiring.py` :143). A board-side assertion in any
of them stays as is. No vitest in R1-R3 (no FE change); Planned column vitest lands with Q4.

Board proof: the board/ladder suites (`tests/scm/test_ladder_v7_*`,
`tests/scm/test_so_transferable.py`, `tests/test_project_supply_holds.py` board cases,
fulfilment planning board suites) green unchanged.

## Risks

- Visible jump in shortfall: dev whole-book probe, 622 of 18,508 lines change status, dated
  shortfall +2.4% (770,679 -> 789,179). Expected by the ruling.
- `_po_received_by_so_line_ref` is shared with R7 on the board: the new keyword defaults to
  today's behaviour; a board test pins it.
- #1658 (STOCK-DEBT-STALE-PIN) edits `_holds`, `cell()` and the dialog. This lane bases on
  main and rebases after #1658 merges; its cap stays for the board.

## Appendix: the behaviour card as filed (14 Oct 2026)

STOCK-DEBT-DECOUPLE behaviour card (size M). Owner model: incoming cover only from AutoCount SO<->PO/SPO links; on hand is the only thing Stock Debt walks (due date, per group); CRM planning pins/borrows = info only. Board path (view=False, assignments_for stock_debt_service.py:997) untouched.

TODAY, what covers a line in the view (_assignments stock_debt_service.py:1026-1190):
1. Board decisions so_line_allocations (_holds :1508-1534 via _hold_query project_supply_service.py:9487). CRM planner writes (confirm -> _write_allocations project_supply_service.py:8640; planning_change_service.py:3487). Dev: active non-order pins 176 (own 26, brw pool 110, other_location 29, group_take 11). = CRM.
2. OI placements order_inquiry_links (_holds :1536-1685). Written by CRM (_write_link project_order_inquiry_service.py:9184). auto=True = follow-book, copies AutoCount refs (follow_book_for_rows :3202, from_so_line_ref + PO->SPO chain); auto=False = manual Link dialog / SPO conversion / borrow. Since 25 Sep (3ca359cf6, #1220) the CRM FIFO cascade writes suggestions only. Dev, live placements on open SOs: 4,057 match the line's AutoCount ref, 763 do not (752 linked before 25 Sep by the retired cascade). = MIXED.
3. Received-SPO placement -> on-hand hold (:1626-1667). Same placements as 2.
4. _landed_holds (:2051): PO.from_so_line_ref + SPO receipt = AutoCount, but _landed_for_refs prefers OI links (project_supply_service.py:349, :3070) and nets CRM holds (:2085). = mostly AutoCount.
5. _book_so_holds (:2125): PO/SPO from_so_line_ref, written only by AutoCount ingest (document_ingest_service.py:1366, shipping_order_ingest_service.py:466; no CRM writer), less live placements (:2216-2249). Reads the SPO's OWN ref only, not SPO->PO chain. = AutoCount, CRM-netted.
6. Due-date walk over on hand per group (supply_assignment.py:783-804); SPO/PO are pin_only, never walked (R45, supply_assignment.py:785, stock_debt_service.py:1078).

PROPOSED (view only):
R1 Document cover = AutoCount ref only: PO.from_so_line_ref, SPO.from_so_line_ref, or SPO.from_po_line_ref -> PO.from_so_line_ref; landed goods = same refs + SPO receipt. Placements and decisions never pin and never net the S/O.
R2 On hand: due-date walk per group as today. No pool->project or group->group move from a pin.
R3 PO/SPO with no AutoCount link stays out of the walk, free in its own month (R45 kept).
R4 Decisions + placements shown in a new "Planned" info column in the drill Demand table.
R5 Board unchanged; proof = board/ladder suites green.

EXAMPLES (dev sorento_cagent_stack read-only; real view walk run twice, today vs CRM holds removed):
E1 Borrow across groups, SRTSH1040: SO402757 BRW-IB due 1 Sep 350. Today pinned on 6 borrows of BRW-BB on hand. After: covered by its own AutoCount-linked PO 202607-S0067 lines 77+65 (200+150). BB stock stays in BB: SO385493 BB Jan short 49 -> covered; SO383872 BB 75/84/84/42 short -> covered. Planned: "Borrow BRW-BB 350".
E2 Pool borrow, TPE-9204: SO402757 BRW-IB Sep 305 and Jan 200 pinned on BRW pool -> short 305 / 200; SO400418 BB Dec 4 -> short 4. Pool lines get the BRW stock: SO413738 BRW Jun 2027 short 202 -> covered.
E3 Thin/stale pin (#1658 shape), CSH2073: SO419595 IB 30 Sep 8, pool pin 8 on BRW holding 5. Today short 3 (pinned 5). After short 8, BRW 5 free in pool. Dev has no zero-stock own pin (CB6633 pins are all source 'order', never read).
E4 Out-of-span stand-in, SRTSH1040 Group=IB: SO402757 Sep 350 today stood up on BRW-BB outside the IB span (supply_assignment.py:748-766). After: its AutoCount PO lines. Stand-ins leave the view (only decisions/placements create them).
E5 Received-SPO placement, SRTSH1040 BB: SO394056 111, SO396347 110, SO396350 234. Today pinned via placement on received SPO; after pinned via landed AutoCount goods, same qty. 3,260 of 3,698 received-SPO placements on dev chain to the line's AutoCount ref.
E6 In-transit SPO, CB310-CR-L: SO398090 BB 3 lines x 175 on SPO-2026/09-0034 (pending; the SPO has no own S/O ref, its PO has). Today pinned via placement. Without R1's chain read -> short 175 x 3; with it, still covered.
E7 PO with no S/O link, TPE-9204: PO-2026/05-0022 line 1 has no from_so_line_ref but carries 534 placed (SO419376, SO314594, SO314595, SO419595; linked 17-18 Sep by the retired cascade). After: SO419376 BB 76 short, SO314594 IR 280 short; the PO shows 6,450 free in its own month.

Whole dev book (approx: CRM holds dropped, chain not yet added): 18,508 lines, 622 change status: pinned->short 268, pinned->covered 114, short->pinned 116 (S/O no longer netted by placements), covered->pinned 51, short->covered 39, covered->short 34. Dated shortfall 770,679 -> 789,179 (+2.4%).

SCREEN: list grid shows month balances only; numbers move, no new column (StockDebtClient.tsx:504-547). Drill: Status, Covered by, Assigned, Short, Free move; one new "Planned" column (StockDebtCellDialog.tsx:426-501). Export columns unchanged (stock_debt_service.py:2484-2489), numbers move. Projection: pool reaches a project line only through an AutoCount link now (:1832-1836). Lendable / order back unchanged (landed goods). No mock (one column on an existing table). #1658 cap stays for the board; I base on main and rebase after it merges.

QUESTIONS
Q1 Source of AutoCount links: (a) read AutoCount's refs directly (PO ref, SPO ref, SPO->PO chain) and ignore placements for cover; (b) count auto (follow-book) placements as AutoCount's record. Rec (a): the auto flag cannot tell the 763 non-matching placements (E7) apart; cost is one chain read in _book_so_holds so E6 stays covered.
Q2 PO/SPO with no S/O link (E7): (a) out of the walk, free in its own month (R45 kept); (b) walk it by due date as incoming. Rec (a): "on hand should be the only one we need to calculate", and R45.
Q3 Pool (BRW/MWH) reserves for project lines (E2; 110 active brw pins on dev): (a) info only like any borrow, line reads short; (b) keep confirmed pool reserves as cover. Rec (a): a pool is its own pile (POOL_GROUP, supply_assignment.py:149) and a pool pin is a planning pin.
Q4 "Planned" content: (a) board decisions only ("Reserve BRW-IB 54", "Borrow BRW-BB 350"); (b) decisions + placements that differ from the line's Covered by ("OI PO-2026/05-0022 76"); (c) decisions + every placement. Rec (b): a placement that matches AutoCount already shows in Covered by; a mismatching one is what the planner must chase.
Q5 Status pill for AutoCount-linked cover (today bare "pinned", StockDebtCellDialog.tsx:499): (a) keep "pinned"; (b) relabel "linked". Rec (b): on this page "pinned" would read as a planning pin, which no longer counts.
