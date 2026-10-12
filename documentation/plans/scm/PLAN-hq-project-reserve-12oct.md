# PLAN: HQ warehouse as a project reserve

Status: Review READY (reviewer round 3, 12 Oct); waiting on CI and owner hand test. Track: standard (Stock Debt
walk, planner pool set, G12 lock; no migration).

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

## Rule (owner answers, 12 Oct 2026)

A **project reserve** = a warehouse that is some location's pool (`pool_warehouse_id` target)
AND has `segment = project` (`pool_predicate.is_project_reserve`).

1. **Fulfilment Planning never sees it** (Q1 final: "taking HQ is NOT a planning step"). It
   is out of `ProjectSupplyService._site_pool_warehouses` (pool chain, drawable stock, pool
   net) and out of `group_netting.netting_for_products`' pools. The ladder is unchanged,
   Use BRW and the 50% dealer share included. Stock gets from HQ to a project by the CS
   transfer in AutoCount.
2. **Stock Debt** (Q2: "same principle and code as Covered by today"): the walk reads HQ as
   one more pile (`supply_assignment.RESERVE_GROUP`). A project line draws its own group's
   pile, then HQ; an HQ arrival clears open project shortfalls earliest required date
   first. Book=Project and Book=All include HQ; Book=Retail does not. A book S/O on an HQ
   PO may pin it to a project line (`may_draw`). R45 still holds: an HQ PO/SPO covers a
   line on the page only through a link.
3. **Dealers** (Q3 = a): a dealer (pool) line never draws HQ.
4. **Chatbot** (Q4): no change; visibility policies stay as they are.
5. **Side effects** (Q5 = a): G12 count, PO filter, Order Inquiry lock and `supply_claim`
   skip a reserve. Reorder / low stock / BRW on hand drop HQ through the existing segment
   test (pinned by a test). Container grid shows HQ under project bins and HQ's last cost
   moves to the `project` group: accepted display effects.

#1594 (STOCK-DEBT-PROJECTION) coordinates: its `pool_supply(M)` should leave HQ supply out of
`dealer(M)` and give it to project months. Not touched here (that lane is unmerged).

Open, left as today: an SO line booked AT HQ itself reads as a dealer (pool) line.

## Release post-steps

HQ's prod `segment` is NULL since 12 Oct 23:05 (owner cleared it). The code in this PR is
inert while it is NULL (`is_project_reserve(NULL)` is false), so the release can go first and
the data step right after it, never before.

1. Owner, prod data, WITH the release. **Prod has TWO `HQ` warehouses**: Sorento's (company
   SRT, id `50aa5369...`) and Mocha's (company MCH, id `ab5d1150...`, also a self-pool,
   `segment = dealer`). Target the SRT one by company, never by code alone. Mocha's HQ stays
   as it is until the owner rules on it.
   ```sql
   SELECT w.id, c.code, w.warehouse_code, w.segment, w.pool_warehouse_id, w.is_active
     FROM warehouses w JOIN companies c ON c.id = w.company_id
    WHERE w.warehouse_code = 'HQ';                       -- expect 2 rows: SRT and MCH
   UPDATE warehouses SET segment = 'project', updated_at = now()
    WHERE warehouse_code = 'HQ'
      AND company_id = (SELECT id FROM companies WHERE code = 'SRT');  -- expect 1 row
   ```
2. What moves at that step: reorder / low stock / BRW on hand / PO book / SPO history /
   product economics / cover offers drop HQ; G12 and supply_claim skip it; the planner stops
   offering it; Stock Debt lets project lines use it; HQ's last purchase cost moves from the
   `unattributed` bucket to `project`; the container grid lists HQ under project bins.
   Retail-only usage and the sales trajectory do not move (NULL already reads as not-dealer
   there).

## Interim (HQ NULL, until the data step)

NULL reads as `dealer` in `pool_predicate` (`COALESCE(segment, 'dealer')`), so HQ is a site
pool everywhere that predicate runs: reorder / low stock on hand, SPO and PO
(`reorder_run_service.py:1105,1148-1161`), BRW on hand (`summary_order_service.py:885-907`,
`active_site_pool_sql` plus `counts_as_available`), cover offers (`cover_service.py:120,192`),
product economics. Planner and Stock Debt are as on main (HQ a pool by FK: 50% share, in the
pool net, dealer-only cover). No G12 lock (`segment = 'project'` only). Three readers differ
from `dealer`: retail-only usage (`reorder_level_service.py:90,159`, `warehouse_segment =
'dealer'`) excludes HQ deliveries; the sales trajectory (`trajectory_service.py:135-151`,
`COALESCE(segment, 'project')`) counts HQ in the project series; last purchase cost
(`reorder_run_service.py:1480`) files HQ purchases as `unattributed`.

## Tests

`sorento_crm_backend/tests/scm/test_hq_project_reserve.py`.
