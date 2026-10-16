# PLAN: consignment pages get the SO planning buttons (CONSIGN-PLAN)

Status: PR ready - reviewer READY (round 3), browser pass PASS; waiting on CI label and owner hand test (PR #1712). Track: standard (M), no migration, no auth change.
UAC: `consign-plan-acceptance-criteria.md` (alongside).

## Owner request (15 Oct, verbatim)

"Our consignment page don't have a plan like sales order, right? Our consignment page supposed to
look exactly like sales order page in terms of the buttons also. Like plan selected, we need that
also. So for consignment, most of it, the location is CON consignment. So the user need to select
it to say borrow using the decide and borrow button."

## Behaviour card

See `CARD-consign-plan-15oct.md` (alongside). Filed as a crew-ask on PR #1712, 15 Oct.

## Owner answers (16 Oct, 00:10)

"in the plan, the user will choose the item and then click Decide, then Borrow, and then set the
borrow location." Q1: the existing Borrow picker, CON among the donor choices, no Buy, nothing to
purchasing, no stock move. Q2 (a). Q3 (a). Q4/Q5 (a) (crew). Keep it doc-type agnostic where
cheap: the Sample Request flow will borrow through the same path.

## Design

- `app/services/scm/planning_predicate.py`: one doc-type set, `PLANNED_AT_OWN_BIN_DOC_TYPES =
  frozenset({"consignment"})`, and `outside_fulfilment_planning(warehouse, doc_type=None)` returns
  False for a doc type in the set. Both callers pass the order's doc_type: the board's
  `_demand_rows` (`project_fulfilment_board_service.py:1635`) and the supply service's
  `unplannable_reason` (`project_supply_service.py:9313`). Sample Request adds its doc type to the
  set and nothing else.
- CON stays flagged off, so `_drawable_free_stock` and every sales order line keep ignoring it
  (UAC-04). The consignment line is offered its own bin by the existing BORROW-OWN-LOC row
  (`project_supply_service._borrow_candidates`), which is how CON reaches the Borrow picker.
- `scm/demand.plan_qty()` and `project_supply_service.plan_qty_of` subtract `qty_returned`
  (0 on every sales order line), so returns net generically.
- FE: `ConsignmentsList` gets row selection, `buildPlanActions` and an Actions menu (Reset
  planning, Refresh). The reset dialog moves out of `SalesOrdersGrid` into one shared component so
  there is still exactly one importer of the retired dialog. `SalesOrderDetail` drops the two
  consignment exclusions (Plan button, planned chip).

## Test list (red first)

Backend `tests/scm/test_consign_plan.py`: UAC-01..09. Frontend: `ConsignmentsList.test.tsx`
(UAC-10, 11), `SalesOrderDetail.consignment.test.tsx` (UAC-13; the old "never planned"
assertions flip).

## Browser evidence run (cloud sandbox, 16 Oct, head df97c383)

agent-browser 0.27.0, headless, against the lane's own stack (uvicorn + `next dev`, bootstrapped
sandbox DB, every module enabled). Seed: masked copy of CG-202606-062's shape, `CG-SBX/06-062`
(dealer 300-X096, project class, closed, 3 lines at CON: SAMPLE-SBX 2, CB-SBX-2516 4 with 1
returned, WT-SBX-165 3), `CG-SBX/08-072` (1 line, fully returned), `SO-SBX-0001` (open sales
order at BRW-BB). CON holds 6 of each product, flagged out of fulfilment planning.

1. `/` > sidebar Project Sales Admin > Consignment: row ticks, Plan selected (0) disabled,
   Actions. Tick CG-SBX/06-062: Plan selected (1); Actions = Reset planning (1), Refresh (no
   Upload, no Add).
2. Plan selected (1) opens `/project-sales/fulfilment-planning?orders=CG-SBX%2F06-062`. Three
   lines, none "Outside fulfilment planning"; planned total 8 (2 + (4 - 1) + 3: returns netted).
   The ladder's own suggestion is Buy 8 (CON is never drawn).
3. Tick the 3 lines > Decide > Borrow: dialog "Decide 3 lines: Borrow", Location offers BRW-BB and
   CON. CON + reason > Save 3 lines: Borrow decided 8, Buy 0 (3 draft PUTs 200).
4. Confirm (3): `POST .../adopt` 200 (unadopted consignment adopted at confirm), `confirm-all` 200,
   "CG-SBX/06-062: confirmed as revision 1 (0 purchase rows handed over)". DB:
   `projects.order_inquiry_rows` 0, one active `so_supply_decisions` revision, CON stock unchanged.
5. Sidebar > Consignment > CG-SBX/06-062: header "At customer", "Consignment", "Planned", Plan
   button linking `?orders=CG-SBX%2F06-062`.
6. 375px: list has no page-level horizontal scroll (scrollWidth 375); the grid scrolls inside its
   card. Detail renders.
7. UAC-09: tick CG-SBX/06-062 > Actions > Reset planning (1) > Reset planning: `POST
   .../reset-planning` 200, decision removed, order reads 0 of 3 planned.
8. API: board for `CG-SBX/08-072,SO-SBX-0001` admits only the sales order line (fully returned CG
   has nothing to plan); its Borrow candidates are BRW-BB only, never CON.
9. Fix round 1 (head ff146a7f, B1): after the Reset in step 7, Plan selected (1) again. The
   board's own suggestion is now Borrow 8, Buy 0. `Save line N as suggested` on the 3 lines, then
   Confirm (3): "CG-SBX/06-062: confirmed as revision 1 (0 purchase rows handed over)";
   `projects.order_inquiry_rows` 0.

## Review decisions

- Round 1 B1: a consignment line at an off bin (CON) is proposed Borrow at that bin for the whole
  plan qty, never Buy, so Approve all writes nothing to purchasing (`compose_lines`).
- Round 2 B2: that proposal and the no-order-back skip apply only when the line's own bin is
  outside fulfilment planning; a consignment line at a planned bin runs the ladder like a sales
  order line (card R6).
- Not built (lead, round 2 S6): capping the CON proposal at CON's free qty. BORROW-OWN-LOC already
  confirms what is free at the own bin and records the rest short, and the owner ruled no Buy for
  consignments. Trigger to revisit: a planner reports a consignment marked planned while CON's
  on-hand for that product is below the consignment's open qty.
- Not built (round 2 N9): plan moves lost if `_finish_batch`'s savepoint rolls back. Rare; the
  next planning change or a manual re-plan covers it.

Browser evidence, fix round 2 (head b1abbae7): after an API reset of CG-SBX/06-062, the board's
List view reads `Borrow 2 (CON)` / `Borrow 3 (CON)` per line (summary Borrow suggested 8, Buy 0);
the Grid cell breakdown for SAMPLE-SBX shows the one-step reason "Consignment: the goods are at
CON." and the stock table lists CON as `Own location` (on hand 6, taken 2) beside the BB group.

## Crew audit round (16 Oct): CON inactive on dev

Crew: on the shared dev DB CON is `is_active = false` and `fulfilment_planning = false`; the
round-2 helper only fired for an ACTIVE bin, so dev would have proposed Buy. Crew decision: a
consignment line at its own bin is planned there whatever that bin's active / planning flags, as
long as the bin is not IN fulfilment planning (a consignment at a planned bin still runs the
ladder, R6); CON stays invisible to every sales order. `planning_predicate.planned_at_own_bin` =
doc type in the set, warehouse present, `not in_fulfilment_planning(warehouse)` (87836e6a).

CON holding 0 of the product: the board still proposes Borrow <plan qty> at CON; confirm decides
the line with no Buy, no Order Inquiry row, no stock-short notice and no hold. The owner rule (no
Buy, nothing to purchasing) holds.

Browser evidence (head 87836e6a, seed mirroring dev: CON inactive and off, CG-SBX/06-062 with two
fully returned lines and one line WT-SBX-165 qty 1 with CON holding 0, CG-SBX/08-072 fully
returned). Before the fix (4caef32e) the same seed proposed `Buy 1`.

| # | Check | Result |
|---|---|---|
| 1 | Consignment list: ticks, Plan selected (0) disabled, Actions | PASS |
| 2 | CG-SBX/08-072 > Plan selected: "No lines to plan on these sales orders" | PASS |
| 3 | CG-SBX/06-062 > board: one row (WT-SBX-165), `Borrow 1 (CON)`, Buy suggested 0 | PASS |
| 4 | Decide > Borrow: "Decide 1 lines: Borrow", Location offers BRW-BB and CON | PASS |
| 5 | CON + reason > Save 1 line > Confirm (1): "confirmed as revision 1 (0 purchase rows handed over)" | PASS |
| 6 | DB: 0 `order_inquiry_rows`, 1 active decision, CON on-hand still 0 | PASS |
| 7 | Detail: At customer, Consignment, Planned, Plan button | PASS |
| 8 | Sales order SO-SBX-0001 Decide > Borrow: Location offers BRW-BB only (no CON) | PASS |
| 9 | Reset planning (1) on the consignment: 0 of 1 planned | PASS |

The board's empty-state hint now reads "Every line is cancelled, fully returned or marked no
purchase needed." (a fully returned consignment lands there).
