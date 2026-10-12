# PLAN: HQ warehouse as a project reserve

Status: Plan (behaviour card asked on PR #1597, waiting on owner). Track: standard (SCM
planner, Stock Debt, G12 lock; no migration expected).

## Owner ask (12 Oct)

HQ is a PROJECT RESERVE. CS put project stock at HQ first (no time to link it in AutoCount,
and BRW stock gets taken by dealers), then move it to the actual project locations. HQ stock
(on hand + incoming PO/SPO) belongs to projects in general, never to dealers.

## Audit of main 4ad65522f (verified)

- HQ is a pool only through the FK: `project_supply_service.py:2430-2454`
  (`_site_pool_warehouses` = every active warehouse some row's `pool_warehouse_id` names).
  Migration 311 seeds "a bare code points at itself" (`alembic/versions/311_scm_purchasing_base.py:350-365`),
  so HQ is its own pool; inactive HQ-ACC points at it too.
- Planner chain: own pool first, then the other pools by on hand (`project_supply_service.py:2496-2535`).
- Dealer share: `available_for_project` keeps `pool_share_pct` (default 50, `scm/priority.py:203`)
  of every pool for dealers (`scm/front_planning_engine.py:273-289`).
- `pools_net` nets every pool as one pile (`scm/group_netting.py:201-212`, pools read at :287),
  so HQ surplus offsets BRW's dealer backlog.
- Stock Debt: HQ supply events carry `is_pool` (`scm/stock_debt_service.py:1118,1136,1165,1272`),
  land in `POOL_GROUP` (`scm/supply_assignment.py:147-167`), and the walk draws only a line's own
  group pile (`supply_assignment.py:839-990`, R40). HQ covers dealer lines, never project lines.
- Chatbot stock: no segment test at all; only `is_active` and the stock-visibility
  include/exclude lists (`inventory_service.py:816-830,1226-1240,1255-1266`, `stock_visibility.py:364`).
- `segment` is the site-pool test everywhere else (`scm/pool_predicate.py:55-77`).
  `HQ.segment = project` on its own would:
  - drop HQ from reorder / low stock on hand, SPO and PO (`scm/reorder_run_service.py:1105,1148-1161`),
    BRW on hand (`summary_order_service.py:907`), site pool PO/incoming (`site_pool_supply.py:97,146`),
    PO book, SPO history, product economics, cover offers. Wanted.
  - put the G12 lock on HQ PO/SPO lines (`project_bin_lock.py:73,85,112`,
    `project_order_inquiry_service.py:7506,7593`) and make `supply_claim.py:128-135` claim
    plan-raised HQ PO lines for one inquiry. NOT wanted.
  - move HQ to "project bins" in the container grid split (`container_request_service.py:975,1066`)
    and HQ's last purchase cost into the `project` group (`reorder_run_service.py:1480`). Display only.

## Target rule

A **project reserve** = a warehouse that is some location's pool (`pool_warehouse_id` target)
AND has `segment = project`. One predicate, next to `pool_predicate.is_site_pool`.

1. Planner: a project reserve lends a project line its whole own net
   (`on hand - SO at HQ + SPO`, 0% dealer share), is NOT inside the dealer `pools_net`, and the
   dealer pools' share rule is unchanged.
2. Stock Debt: reserve on hand + incoming is its own pile; any project line that its own group
   cannot cover draws it (date order across groups); POOL_GROUP (dealer) lines never do.
   PR #1594 projection: reserve supply skips `dealer(M)` and goes to project months.
3. Reorder / low stock: covered by the segment test already (tests pin it). Chatbot: per Q4.
4. G12 lock and supply_claim skip a project reserve.

Data step `HQ.segment = project` ships WITH the release (owner, prod data).

## Behaviour card questions

See the `crew-ask` on PR #1597 (Q1 draw order, Q2 Stock Debt competition, Q3 dealer short,
Q4 chatbot, Q5 side effects).
