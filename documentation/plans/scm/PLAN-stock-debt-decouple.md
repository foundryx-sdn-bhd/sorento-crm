# PLAN - Stock Debt fully decoupled from Fulfilment Planning pins (STOCK-DEBT-DECOUPLE)

Status: building R1-R3 (backend), 14 Oct 2026. Owner answers on the card: Q1 (a), Q2 (a),
Q5 (a, keep "pinned"). Q3 (pool) and Q4 (Planned column) still open: pool decisions keep
today's behaviour and no Planned column is built until they land. Feature track, size M (read model + one drill
column + one pill label; no write, no migration, no RBAC change, no new ingest surface).
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
