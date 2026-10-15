# PLAN: consignment pages get the SO planning buttons (CONSIGN-PLAN)

Status: Build - owner answered 16 Oct; red tests next. Track: standard (M), no migration, no auth change.
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
