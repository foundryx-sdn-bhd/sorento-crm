"""RED tests, written before the coder's Phase 2, for Lane F of
`PLAN-order-sheet-oi-reports-22sep.md` (AC-F1..F7 in
`order-sheet-oi-reports-22sep-acceptance-criteria.md`).

STEP 1 MEASURE (captain's brief, 23 Sep 2026), confirmed on this branch before writing
these tests - three sizing paths, all seeded with on hand 702 (single-member: one
location; product-grain: same single location under `policy_type='reorder_level'`; pooled:
two locations, 326 + 376) against ONE confirmed ORDER_BACK row of 493 on a CLOSED, fully
delivered SO line (`qty_ordered = qty_delivered = 493`, `tests.test_reorder_plan_demand_
scope._project_so_delivered_line_with_order_back` - the owner's own measured shape,
CB4702/OI-000749/SO421985), an unscoped All run (`demand_class=None`) with the SO picked:

  (a) SINGLE-MEMBER (`_emit_cell`, default `reorder_point` policy, no forecast demand):
      `_compute_cell`'s `net = 702 - 493 = 209` (project already netted INSIDE `net` via
      `net_position`'s own `committed` term); `sizing_net = net - project_supply_reduction
      = 209`; `rop = 0` (no forecast) so `209 >= 0` does not trigger. The row emits
      `rec_type = 'covered'`, `recommended_qty = 0.0`, `rounded_qty = 493.0` (the DISPLAY-
      only "buy anyway" figure `_covered_rec` always carries, `reason_label = "702
      available in this pool covers 493 committed"`). NO BUY. Confirms the captain's
      reading: `_compute_cell` nets project need against on hand on a non-Project run.

  (b) PRODUCT-GRAIN (`_emit_product`, `policy_type='reorder_level'`, one product-wide
      level of 100, the SAME 702/493 seed): identical shape - `net = 209` (project folded
      into `net` per the one-formula ruling, `_emit_product`'s own "AC-R2" comment), `209
      >= level 100` does not trigger `reorder_level`'s gate, `rec_type = 'covered'`,
      `recommended_qty = 0.0`. NO BUY. Confirms the same reading for the product-grain
      path.

  (c) POOLED (`_emit_pool`, `pool_netting=true`, two members 326 + 376 = 702, the SAME
      493): `_emit_pool` never adopted the one-formula netting - it still computes
      `wh_inputs` off each cell's `retail_net` (project already stripped back OUT) and
      ADDS the raw `pool_project_need` (493, unclamped) on top unconditionally
      (`recommended = retail_recommended + pool_project_need`, `_emit_pool`'s own "AC-E05"
      comment). Measured result: `rec_type = 'buy'`, `rounded_qty = 493.0`, `reason_label
      = "project buy: 493 confirmed unplaced Buy in this pool"`. A BUY EXISTS, in full,
      REGARDLESS of the 702 on hand. This REFUTES the captain's reading for the pooled
      path specifically - `_emit_pool` already satisfies AC-F1/AC-F7's pooled case today.
      Its own test below is a GREEN regression guard, not a red test (same pattern as
      Lane D's `test_ac_d2_service_level_committed_already_scoped_on_all_run_bypassing_
      http`) - it still has to run, because a coder wiring the (a)/(b) fix through a
      shared helper could plausibly break the pool's existing correct behaviour.

Conflicting EXISTING pins (netting-on-All-runs pins this lane's fix will flip; the coder
edits these, citing this ruling - not the tester):

  - `tests/scm/test_reorder_one_formula.py::test_location_row_project_inside_net` (AC-3):
    10,000 on hand vs 200 confirmed project demand on a single-member/`_emit_cell` row,
    pinned `rec_type == 'covered'`, `'project buy' not in reason`, `net_position ==
    9_800.0` - THE SAME shape as measurement (a) above. Lane F flips this to a buy of 200.
  - `tests/scm/test_reorder_one_formula.py::test_no_level_product_row_buys_level_zero_gap`
    (AC-1): B2155-NL-BLUE, on hand 128 + PO 339 against project 493 + retail 170 + level 0,
    pinned `recommended_qty == 196.0` (the netted one-formula gap) and `'project buy' not
    in reason`. Lane F flips this to retail-alone-on-top-of-493-in-full (a different
    number and a "project buy" reason).
  - `tests/scm/test_reorder_per_product.py::test_the_buy_is_the_gap_from_the_net_up_to_
    the_level` (AC-R2): product-grain, on hand 10,860 + PO 860 - project 150 - retail 290
    against level 12,000, pinned `net == 11_280.0`, `recommended_qty == rounded_qty ==
    720.0` (the netted gap, project's 150 folded in). Lane F flips the sizing formula this
    depends on (retail sized on `retail_net`, project's 150 added in full on top), so 720
    is very unlikely to survive unchanged.
  - `tests/scm/test_reorder_per_product.py::test_a_level_sets_bypass_never_fires_when_
    stock_already_covers_the_confirmed_buy` (AC-5): product-grain, 600 on hand vs a 50
    confirmed Buy against a level of 500 (net 550 > level, no trigger), pinned `not
    _buys(rows)`, `rec_type == 'covered'`, `net == 550.0`. THE SAME shape as measurement
    (b) above. Lane F flips this to a buy of 50 in full.

Harness copied from `tests/scm/test_reorder_plan_admit_discontinued.py` (`_u`/`_code`/
`_recs_for`/`_buy_rows`, the `MARKER`/company-scoped-savepoint idiom),
`tests/scm/test_reorder_plan_project_only.py` (`_seed_pool_below_level`,
`_expected_pool_retail_buy`), `tests/scm/test_channel_read_model.py`
(`_core_line_for_run`, a plain committed retail SO line) and
`tests/test_reorder_plan_demand_scope.py` (`_project_so_with_lines`,
`_project_so_delivered_line_with_order_back`, `_add_reserve_claim`). Postgres only, via
`tests/scm/conftest.py::scm_app` - a live TestClient over a rolled-back SAVEPOINT. CI's
database starts empty: every test seeds its own full chain, nothing borrowed off the
shared local database.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import text

from app.models.project_so import ACK_ACKNOWLEDGED
from app.services.scm import reorder_engine as eng
from app.services.scm import demand
from app.services.scm import reorder_run_service as svc
from app.services.scm import summary_order_service as sos
from tests.scm.conftest import SORENTO_COMPANY_ID, requires_pg, scm_app  # noqa: F401
from tests.scm.test_channel_read_model import _core_line_for_run, _today
from tests.scm.test_m3_run import (
    _link,
    _mk_demand,
    _mk_movement,
    _mk_product,
    _mk_stock,
    _mk_supplier,
    _mk_warehouse,
)
from tests.scm.test_reorder_plan_project_only import (
    _expected_pool_retail_buy,
    _seed_pool_below_level,
)
from tests.test_reorder_plan_demand_scope import (
    _add_reserve_claim,
    _project_so_delivered_line_with_order_back,
    _project_so_with_lines,
)

pytestmark = requires_pg

MARKER = "ZZTOINF"


def _u() -> str:
    return str(uuid.uuid4())


def _code(stem: str) -> str:
    return f"{MARKER}-{stem}-{uuid.uuid4().hex[:8]}".upper()


def _recs_for(db, run_id: str, pid: str) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT rec_type, warehouse_id::text AS warehouse_id, rounded_qty, "
            "recommended_qty, triggered_reason AS reason_label, inputs "
            "FROM scm.reorder_recommendation WHERE run_id = :r AND product_id = :p"
        ),
        {"r": run_id, "p": pid},
    ).mappings().all()
    return [dict(r) for r in rows]


def _buy_rows(db, run_id: str, pid: str) -> list[dict]:
    return [r for r in _recs_for(db, run_id, pid) if r["rec_type"] == "buy"]


def _total_buy(db, run_id: str, pid: str) -> float:
    return sum(float(b["rounded_qty"]) for b in _buy_rows(db, run_id, pid))


def _seed_single_member(db, *, on_hand: float, demand_add: float = 0.0, lead: float = 30,
                        retail_committed: float = 0.0):
    """One location, default `reorder_point` policy, no reorder level set - the (a) shape
    of the module docstring's measurement.

    `retail_committed` (0 by default, matching AC-F1/AC-F3/AC-F4/AC-F6's OI-only fixtures,
    where the OI row itself gives G1 admission) adds a plain committed RETAIL SO line
    (`_core_line_for_run`) when a caller needs a non-pooled, non-project-demand product
    admitted and triggered on retail alone (AC-F2) - a non-pooled cell with zero committed
    demand of its own emits nothing at all (G1's location-grain gate, the same reason
    `test_reorder_plan_admit_discontinued.py` always pairs a single-member fixture with an
    OI row rather than leaving one bare).
    """
    wid = _mk_warehouse(db, _code("WH"))
    pid = _mk_product(db, _code("P"))
    _mk_stock(db, pid, wid, on_hand)
    _mk_movement(db, pid, wid, 1, days_ago=7)
    if demand_add:
        _mk_demand(db, pid, wid, demand_add)
    _link(db, pid, _mk_supplier(db, f"{MARKER} Supplier"), lead=lead, moq=1, mult=1)
    eng.ensure_reorder_policy_defaults(db)
    if retail_committed:
        _core_line_for_run(db, pid, wid, qty=retail_committed, demand_class="retail")
    db.flush()
    return {"pid": pid, "wid": wid}


def _seed_product_grain(db, *, on_hand: float, level: float, demand_add: float = 0.0,
                        lead: float = 30):
    """The SAME single location as `_seed_single_member`, under the PROD SHAPE
    (`policy_type='reorder_level'`, one product-wide level) - the (b) shape."""
    u = _seed_single_member(db, on_hand=on_hand, demand_add=demand_add, lead=lead)
    db.execute(
        text("UPDATE scm.reorder_policy SET policy_type = 'reorder_level' "
             "WHERE scope_type = 'global'")
    )
    db.execute(
        text(
            "INSERT INTO scm.reorder_level (id, product_id, warehouse_id, level, source, "
            "company_id, created_at) VALUES (:id, :p, NULL, :lvl, 'manual', :co, now())"
        ),
        {"id": _u(), "p": u["pid"], "lvl": level, "co": SORENTO_COMPANY_ID},
    )
    db.flush()
    return u


def _seed_pool(db, *, on_hand_root: float, on_hand_child: float, demand_add: float = 0.0,
              lead: float = 30):
    """Two pooled locations, `pool_netting=true` - the (c) shape."""
    root_wid = _mk_warehouse(db, _code("ROOT"))
    child_wid = _mk_warehouse(db, _code("CHILD"), pool_warehouse_id=root_wid)
    pid = _mk_product(db, _code("P"))
    _mk_stock(db, pid, root_wid, on_hand_root)
    _mk_stock(db, pid, child_wid, on_hand_child)
    _mk_movement(db, pid, root_wid, 1, days_ago=7)
    if demand_add:
        _mk_demand(db, pid, root_wid, demand_add)
    _link(db, pid, _mk_supplier(db, f"{MARKER} Supplier"), lead=lead, moq=1, mult=1)
    eng.ensure_reorder_policy_defaults(db)
    db.execute(
        text("UPDATE scm.reorder_policy SET pool_netting = true WHERE scope_type = 'global'")
    )
    db.flush()
    return {"pid": pid, "root_wid": root_wid, "child_wid": child_wid}


def _oi_order_back(db, *, pid, wid, qty="493"):
    """The owner's own measured shape: ONE project SO, ONE core line delivered IN FULL
    (`line_status='closed'`, nothing outstanding), an ORDER_BACK row of `qty` at the
    line's own location (`donor_warehouse_code=None` - `COALESCE(donor.id, sol.warehouse_
    id)` falls back to the line's own warehouse when `stock_location` names none).

    ``sibling_order=False``: the shared seed also plants an ORDER row of the same `qty`
    on the same line by default, which the 7.3 cap used to zero out on a delivered line.
    R1 (`PLAN-oi-order-rows-uncapped.md`, 23 Sep 2026) retires that cap, so with the
    sibling in the line would carry `2 * qty` and every "bought in full" figure below
    would double (986, not 493). The measured shape is ONE row, so only that row is
    seeded."""
    return _project_so_delivered_line_with_order_back(
        db, product_id=pid, warehouse_id=wid, donor_warehouse_code=None, qty=qty,
        sibling_order=False,
    )


def _seed_two_location_product_grain(db, *, level: float = 100.0):
    """Two INDEPENDENT (non-pooled) locations of one product under the PROD SHAPE
    (`policy_type='reorder_level'`) - `_emit_product` plans product-grain regardless of
    pooling (`_is_product_level_basis` is checked before the pool/single-member split), so
    no `pool_warehouse_id` link is needed between A and B. `level` set low (100, well below
    A's own 1,000 on hand) so RETAIL alone never triggers a buy even once B's small deficit
    joins the aggregate - the allocation regression this guards (review fix round 3) is
    about WHERE the confirmed project need lands, not how much is bought."""
    a_wid = _mk_warehouse(db, _code("A"))
    b_wid = _mk_warehouse(db, _code("B"))
    pid = _mk_product(db, _code("P"))
    _mk_stock(db, pid, a_wid, 1000)
    _mk_stock(db, pid, b_wid, 0)
    _mk_movement(db, pid, a_wid, 1, days_ago=7)
    _mk_movement(db, pid, b_wid, 1, days_ago=7)
    _mk_demand(db, pid, a_wid, 0.0)
    _mk_demand(db, pid, b_wid, 0.0)
    _link(db, pid, _mk_supplier(db, f"{MARKER} Supplier"), lead=30, moq=1, mult=1)
    eng.ensure_reorder_policy_defaults(db)
    db.execute(
        text("UPDATE scm.reorder_policy SET policy_type = 'reorder_level' "
             "WHERE scope_type = 'global'")
    )
    db.execute(
        text(
            "INSERT INTO scm.reorder_level (id, product_id, warehouse_id, level, source, "
            "company_id, created_at) VALUES (:id, :p, NULL, :lvl, 'manual', :co, now())"
        ),
        {"id": _u(), "p": pid, "lvl": level, "co": SORENTO_COMPANY_ID},
    )
    db.flush()
    return {"pid": pid, "a_wid": a_wid, "b_wid": b_wid}


def _allocation_for(db, run_id: str, pid: str) -> dict[str, float]:
    """``{warehouse_id: qty}`` off the ONE product-grain buy row's `allocation` column
    (`_allocation_lines`'s own frozen split) - the test only needs to compare which
    SIBLING the split favoured."""
    row = db.execute(
        text(
            "SELECT allocation FROM scm.reorder_recommendation "
            "WHERE run_id = :r AND product_id = :p AND rec_type = 'buy'"
        ),
        {"r": run_id, "p": pid},
    ).mappings().first()
    assert row is not None, "expected a buy row"
    return {a["warehouse_id"]: float(a["qty"]) for a in (row["allocation"] or [])}


def _horizon_end() -> date:
    """A year out: the seeded lines fall due relative to today (`_today() + 14`), so a
    fixed "Plan until" would drop them once today passes it less those 14 days."""
    return _today() + timedelta(days=365)


def _run_all(db, *, so_numbers: list[str]) -> str:
    created = svc.create_run(
        db, [], enqueue=False,
        plan_horizon_start=date(2026, 1, 1), plan_horizon_date=_horizon_end(),
        demand_class=None, so_numbers=so_numbers,
    )
    svc.run_reorder(created["run_id"], db=db)
    return created["run_id"]


def _run_project(db, *, so_numbers: list[str]) -> str:
    created = svc.create_run(
        db, [], enqueue=False,
        plan_horizon_start=date(2026, 1, 1), plan_horizon_date=_horizon_end(),
        demand_class="project", so_numbers=so_numbers,
    )
    svc.run_reorder(created["run_id"], db=db)
    return created["run_id"]


def _run_dealer(db, *, so_numbers: list[str] | None = None) -> str:
    created = svc.create_run(
        db, [], enqueue=False,
        plan_horizon_start=date(2026, 1, 1), plan_horizon_date=_horizon_end(),
        demand_class="retail", so_numbers=so_numbers or [],
    )
    svc.run_reorder(created["run_id"], db=db)
    return created["run_id"]


# =============================================================================
# AC-F1 / AC-F7 - All run, on hand > confirmed OI qty, retail trigger off: Suggested ==
# the OI owed qty, on every sizing path.
# =============================================================================

def test_ac_f1_single_member_all_run_bought_in_full_retail_trigger_off(scm_app):
    _, db, _, _ = scm_app
    u = _seed_single_member(db, on_hand=702)
    so = _oi_order_back(db, pid=u["pid"], wid=u["wid"], qty="493")

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    buys = _buy_rows(db, run_id, u["pid"])
    assert buys, (
        f"expected a visible buy for the confirmed OI need, not covered, got "
        f"{_recs_for(db, run_id, u['pid'])}"
    )
    assert _total_buy(db, run_id, u["pid"]) == 493.0


def test_ac_f1_product_grain_all_run_bought_in_full_retail_trigger_off(scm_app):
    _, db, _, _ = scm_app
    u = _seed_product_grain(db, on_hand=702, level=100)
    so = _oi_order_back(db, pid=u["pid"], wid=u["wid"], qty="493")

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    buys = _buy_rows(db, run_id, u["pid"])
    assert buys, (
        f"expected a visible buy for the confirmed OI need on the product-grain basis, "
        f"not covered, got {_recs_for(db, run_id, u['pid'])}"
    )
    assert _total_buy(db, run_id, u["pid"]) == 493.0


def test_ac_f7_pool_all_run_bought_in_full_retail_trigger_off(scm_app):
    """GREEN today (regression guard, not a red test - see module docstring, measurement
    (c)): `_emit_pool` already adds the raw project need on top unconditionally. Still
    required by AC-F7 ("pool ... path satisfies AC-F1") and worth running, because a coder
    wiring the single-member/product-grain fix through a shared helper could plausibly
    regress this already-correct path."""
    _, db, _, _ = scm_app
    u = _seed_pool(db, on_hand_root=326, on_hand_child=376)
    so = _oi_order_back(db, pid=u["pid"], wid=u["root_wid"], qty="493")

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    buys = _buy_rows(db, run_id, u["pid"])
    assert buys, f"expected a buy, got {_recs_for(db, run_id, u['pid'])}"
    assert _total_buy(db, run_id, u["pid"]) == 493.0


# =============================================================================
# AC-F2 - All run, retail trigger ON: Suggested == retail sizing + the full OI qty. Retail
# sizing computed from the SAME seed with the OI row removed.
#
# GREEN today (regression guard, not a red test): confirmed by running this exact fixture
# before writing the assertion. `_compute_cell`'s one-formula sizing is LINEAR in
# `committed` whenever the cell is triggered either way - `recommended = target - net +
# project_supply_reduction = target - on_hand + retail_committed + project_committed -
# reduction`, which is algebraically the SAME number as "retail-alone-triggered figure +
# project_committed - reduction" the instant retail alone already crosses its own trigger
# (no MOQ/order-multiple rounds the two halves differently here - both `moq=1`/`mult=1`).
# The bug AC-F1 pins only shows up when retail ALONE would NOT trigger (on hand comfortably
# covers retail demand on its own) - exactly AC-F1's fixture, not this one. Kept as its own
# test anyway (captain's own test list names it, and it is a genuine non-regression the
# coder's fix must not break, e.g. by rounding retail and project separately instead of
# once on the combined figure).
# =============================================================================

def test_ac_f2_single_member_all_run_retail_trigger_on_adds_full_oi_qty_on_top(scm_app):
    _, db, _, _ = scm_app
    # demand_add=10/lead=30/on_hand=100 triggers the retail path on its own (rop = 10*30 +
    # 10*7 = 370 >> on_hand 100), so this is genuinely "retail trigger ON", not merely
    # "project pushed it over" - AC-F2 is retail PLUS project, not project alone.
    # `retail_committed=1` on BOTH twins alike: G1's location-grain gate withholds a
    # non-pooled cell with zero committed demand of its own even when the forecast alone
    # would trigger it (`_seed_single_member`'s own docstring) - a nominal 1-unit retail
    # commitment admits the cell without perturbing the retail-vs-with-OI comparison,
    # since both sides carry the identical 1 unit.
    with_oi = _seed_single_member(db, on_hand=100, demand_add=10.0, lead=30,
                                  retail_committed=1)
    so = _oi_order_back(db, pid=with_oi["pid"], wid=with_oi["wid"], qty="493")
    retail_only = _seed_single_member(db, on_hand=100, demand_add=10.0, lead=30,
                                      retail_committed=1)

    with_oi_run = _run_all(db, so_numbers=[so["so_number"]])
    retail_only_run = _run_all(db, so_numbers=[])

    with_oi_total = _total_buy(db, with_oi_run, with_oi["pid"])
    retail_only_recs = _recs_for(db, retail_only_run, retail_only["pid"])
    retail_only_total = _total_buy(db, retail_only_run, retail_only["pid"])
    assert retail_only_total > 0, (
        f"the retail-only twin must itself trigger a buy, or this is not a retail-trigger-"
        f"on scenario: {retail_only_recs}"
    )
    assert with_oi_total == retail_only_total + 493.0, (
        f"expected retail sizing ({retail_only_total}) + the full OI qty (493) = "
        f"{retail_only_total + 493.0}, got {with_oi_total}"
    )


# =============================================================================
# AC-F3 - ORDER_BACK on a closed, fully delivered SO line: bought in full on an All run
# (RED - see AC-F1) and on a Project run (GREEN regression guard - R1a already buys the
# project-only branch in full; the point of this test is the "closed/delivered does not
# zero it" half, `_OWED_SQL`'s own ORDER_BACK-uncapped rule, surviving the All-run fix).
# =============================================================================

def test_ac_f3_order_back_on_closed_delivered_line_bought_in_full_on_all_run(scm_app):
    _, db, _, _ = scm_app
    u = _seed_single_member(db, on_hand=702)
    so = _oi_order_back(db, pid=u["pid"], wid=u["wid"], qty="493")

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    assert _total_buy(db, run_id, u["pid"]) == 493.0, (
        f"an ORDER_BACK row on a closed, fully delivered line must still buy 493 in full "
        f"on an All run, got {_recs_for(db, run_id, u['pid'])}"
    )


def test_ac_f3_order_back_on_closed_delivered_line_bought_in_full_on_project_run(scm_app):
    _, db, _, _ = scm_app
    u = _seed_single_member(db, on_hand=702)
    so = _oi_order_back(db, pid=u["pid"], wid=u["wid"], qty="493")

    run_id = _run_project(db, so_numbers=[so["so_number"]])

    assert _total_buy(db, run_id, u["pid"]) == 493.0, (
        f"a Project run already buys a closed/delivered ORDER_BACK row in full (R1a), "
        f"got {_recs_for(db, run_id, u['pid'])}"
    )


# =============================================================================
# AC-F4 - a confirmed Reserve decision of 100 on the row reduces the bought qty by 100.
# =============================================================================

def test_ac_f4_confirmed_reserve_decision_reduces_bought_qty(scm_app):
    _, db, _, _ = scm_app
    u = _seed_single_member(db, on_hand=702)
    so = _project_so_with_lines(db, lines=[
        {"product_id": u["pid"], "warehouse_id": u["wid"], "qty": 493,
         "delivery_date": date(2026, 10, 1), "ack_state": ACK_ACKNOWLEDGED},
    ])
    _add_reserve_claim(db, so, product_id=u["pid"], warehouse_id=u["wid"], qty=100)

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    assert _total_buy(db, run_id, u["pid"]) == 393.0, (
        f"expected the confirmed project need (493) less the reserved 100 = 393, got "
        f"{_recs_for(db, run_id, u['pid'])}"
    )


# =============================================================================
# AC-F5 - retail-only products and Dealer runs size exactly as before, pinned exactly.
# =============================================================================

def test_ac_f5_retail_only_product_all_run_sizing_unchanged(scm_app):
    """No OI row anywhere - a product with only retail/leg-2 demand must size the SAME
    figure `_expected_pool_retail_buy` (the pure engine math, no OI-need-in-full change
    touches it) predicts, on an All run."""
    _, db, _, _ = scm_app
    u = _seed_pool_below_level(db, level=150, demand_add=5.0, lead=30)

    run_id = _run_all(db, so_numbers=[])

    total = _total_buy(db, run_id, u["pid"])
    expected = _expected_pool_retail_buy(demand_add=5.0, lead=30)
    assert total == expected, (
        f"a retail-only product's sizing must be unaffected, expected {expected}, "
        f"got {total}"
    )


def test_ac_f5_dealer_run_identical_whether_or_not_a_confirmed_oi_row_exists(scm_app):
    """A Dealer run has no project leg at all (`demand_class='retail'` keeps only the
    book leg of `cv_all`'s UNION) - a confirmed OI row on the same below-level product must
    not move its Dealer-run recommendation at all. Pooled (`_seed_pool_below_level`), not
    single-member: a non-pooled cell needs committed demand of its own to emit anything at
    all (G1's location-grain gate, `_seed_single_member`'s own docstring), which a bare
    below-level retail fixture with no OI and no retail SO line does not carry - the pool
    basis sizes unconditionally of any one member's own committed figure instead."""
    _, db, _, _ = scm_app
    without_oi = _seed_pool_below_level(db, level=150, demand_add=5.0, lead=30)
    without_oi_run = _run_dealer(db)
    without_oi_total = _total_buy(db, without_oi_run, without_oi["pid"])

    with_oi = _seed_pool_below_level(db, level=150, demand_add=5.0, lead=30)
    so = _oi_order_back(db, pid=with_oi["pid"], wid=with_oi["root_wid"], qty="493")
    with_oi_run = _run_dealer(db, so_numbers=[so["so_number"]])
    with_oi_total = _total_buy(db, with_oi_run, with_oi["pid"])

    assert without_oi_total > 0, "the below-level twin must itself trigger a buy"
    assert with_oi_total == without_oi_total, (
        f"a Dealer run must be identical whether or not a confirmed OI row exists, "
        f"got {with_oi_total} vs baseline {without_oi_total}"
    )


# =============================================================================
# AC-F6 - the sheet (write_rows -> project_customers) and the OI worksheet
# (demand.run_scope_oi_rows) both agree with the bought project qty on the run.
# =============================================================================

def test_ac_f6_write_rows_and_worksheet_scope_agree_with_the_bought_project_qty(scm_app):
    _, db, _, _ = scm_app
    u = _seed_single_member(db, on_hand=702)
    so = _oi_order_back(db, pid=u["pid"], wid=u["wid"], qty="493")

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    assert _total_buy(db, run_id, u["pid"]) == 493.0

    assert sos.write_rows(db, run_id) >= 1
    product_code = db.execute(
        text("SELECT product_code FROM products WHERE id = :id"), {"id": u["pid"]},
    ).scalar()
    report = sos.report(db, run_id=run_id)
    row = next((r for r in report["rows"] if r["product_code"] == product_code), None)
    assert row is not None, (
        f"expected the product's frozen row on the sheet, got "
        f"{[r['product_code'] for r in report['rows']]}"
    )
    customers = row["project_customers"]
    assert sum(c["qty"] for c in customers) == 493.0, customers

    scope_rows = demand.run_scope_oi_rows(
        db, [u["pid"]], so_numbers=[so["so_number"]],
        horizon_start=date(2026, 1, 1), horizon=date(2026, 12, 31),
    )
    assert any(float(r["qty"]) == 493.0 for r in scope_rows), (
        f"expected the OI worksheet's own row scope to carry the owed 493 too, got "
        f"{scope_rows}"
    )


# =============================================================================
# Review fix round 3 (reviewer NOT READY) - ALLOCATION: `_emit_product`'s split must not
# read the COMBINED (project-folded-in) net for its per-location deficit, or a confirmed
# OI row's own location (stock-covered, so its combined deficit reads ~0) loses the split
# to a merely retail-short sibling that carries none of the project demand at all. Two
# independent locations, product-grain: A holds huge stock and the confirmed OI row (50);
# B carries a small retail shortfall (5) and no project demand - the buy must land at A,
# not be diluted away to B.
# =============================================================================

def test_emit_product_allocates_the_project_buy_to_the_oi_rows_own_location(scm_app):
    _, db, _, _ = scm_app
    u = _seed_two_location_product_grain(db)
    _core_line_for_run(db, u["pid"], u["b_wid"], qty=5, demand_class="retail")
    so = _oi_order_back(db, pid=u["pid"], wid=u["a_wid"], qty="50")

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    assert _total_buy(db, run_id, u["pid"]) == 50.0, (
        f"expected the confirmed 50 alone (B's small retail deficit never triggers its "
        f"own buy), got {_recs_for(db, run_id, u['pid'])}"
    )
    allocation = _allocation_for(db, run_id, u["pid"])
    a_qty = allocation.get(u["a_wid"], 0.0)
    b_qty = allocation.get(u["b_wid"], 0.0)
    assert a_qty > b_qty, (
        f"expected the confirmed OI row's own location (A) to take the majority of the "
        f"split, not a merely retail-short sibling (B) with no project demand of its own "
        f"- reading the COMBINED (project-folded-in) net for the deficit sites the buy at "
        f"B instead: {allocation}"
    )
    assert a_qty >= 45.0, (
        f"A's own confirmed 50 must not be diluted away by B's small retail deficit: "
        f"{allocation}"
    )


# =============================================================================
# RULING (captain, 23 Sep 2026, fix round 3): a confirmed Reserve/Borrow claim
# (`project_supply_reduction`) reduces the bought project qty on EVERY sizing path, not
# only the single-member one AC-F4 already pins - R1a's "not netted against stock" carves
# out on-hand/SPO/PO, not a Reserve, which is CS's own explicit decision to use the stock.
# =============================================================================

def test_ac_f4_confirmed_reserve_decision_reduces_bought_qty_on_the_pool_path(scm_app):
    """The reviewer's own probe: 702 on hand (326 + 376), 493 confirmed, a 100-unit
    Reserve -> 393, not the 493 `_emit_pool` bought before this round."""
    _, db, _, _ = scm_app
    u = _seed_pool(db, on_hand_root=326, on_hand_child=376)
    so = _project_so_with_lines(db, lines=[
        {"product_id": u["pid"], "warehouse_id": u["root_wid"], "qty": 493,
         "delivery_date": date(2026, 10, 1), "ack_state": ACK_ACKNOWLEDGED},
    ])
    _add_reserve_claim(db, so, product_id=u["pid"], warehouse_id=u["root_wid"], qty=100)

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    assert _total_buy(db, run_id, u["pid"]) == 393.0, (
        f"expected the confirmed project need (493) less the reserved 100 = 393 on the "
        f"pool path too, got {_recs_for(db, run_id, u['pid'])}"
    )


def test_ac_f4_confirmed_reserve_decision_reduces_bought_qty_on_the_product_grain_path(scm_app):
    _, db, _, _ = scm_app
    u = _seed_product_grain(db, on_hand=702, level=100)
    so = _project_so_with_lines(db, lines=[
        {"product_id": u["pid"], "warehouse_id": u["wid"], "qty": 493,
         "delivery_date": date(2026, 10, 1), "ack_state": ACK_ACKNOWLEDGED},
    ])
    _add_reserve_claim(db, so, product_id=u["pid"], warehouse_id=u["wid"], qty=100)

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    assert _total_buy(db, run_id, u["pid"]) == 393.0, (
        f"expected the confirmed project need (493) less the reserved 100 = 393 on the "
        f"product-grain path too, got {_recs_for(db, run_id, u['pid'])}"
    )


def test_ac_f4_confirmed_reserve_decision_reduces_bought_qty_on_a_project_run(scm_app):
    """A Reserve reduces the bought qty on a PROJECT run too - `_project_only_cell`'s own
    "read raw" rule (R1a) carves out on-hand/SPO/PO netting, not a Reserve."""
    _, db, _, _ = scm_app
    u = _seed_single_member(db, on_hand=702)
    so = _project_so_with_lines(db, lines=[
        {"product_id": u["pid"], "warehouse_id": u["wid"], "qty": 493,
         "delivery_date": date(2026, 10, 1), "ack_state": ACK_ACKNOWLEDGED},
    ])
    _add_reserve_claim(db, so, product_id=u["pid"], warehouse_id=u["wid"], qty=100)

    run_id = _run_project(db, so_numbers=[so["so_number"]])

    assert _total_buy(db, run_id, u["pid"]) == 393.0, (
        f"expected the confirmed project need (493) less the reserved 100 = 393 on a "
        f"Project run too, got {_recs_for(db, run_id, u['pid'])}"
    )


# =============================================================================
# Review round 3 nit (reviewer, 23 Sep 2026, READY) - `retail_net` must CLAMP the
# reduction to the row's own project need, not subtract an oversized Reserve unclamped.
# =============================================================================

def test_retail_net_clamps_an_oversized_reserve_to_the_rows_own_need(scm_app):
    """A Reserve of 100 against a confirmed need of just 20: `retail_net` must land back
    on exactly `net` (80 = 100 on hand - 20 committed) - the reserve fully absorbs the row's
    own 20-unit need and Retail is unaffected - not `net - 80` (the unclamped reading,
    which read Retail as 80 units worse off than the no-project baseline for no reason).
    The project part is unaffected by the clamp (`max(need - reduction, 0)` already floors
    at 0): the reserve fully covers the confirmed 20, so nothing is bought at all.
    """
    _, db, _, _ = scm_app
    u = _seed_single_member(db, on_hand=100)
    so = _project_so_with_lines(db, lines=[
        {"product_id": u["pid"], "warehouse_id": u["wid"], "qty": 20,
         "delivery_date": date(2026, 10, 1), "ack_state": ACK_ACKNOWLEDGED},
    ])
    _add_reserve_claim(db, so, product_id=u["pid"], warehouse_id=u["wid"], qty=100)

    run_id = _run_all(db, so_numbers=[so["so_number"]])

    recs = _recs_for(db, run_id, u["pid"])
    assert len(recs) == 1, recs
    row = recs[0]
    assert float(row["inputs"]["retail_net"]) == 80.0, (
        f"expected retail_net to land back on `net` (80), not net - 80 (0) from an "
        f"unclamped reduction: {row}"
    )
    assert row["rec_type"] == "covered", (
        f"expected no buy - the reserve fully covers the confirmed need: {row}"
    )
