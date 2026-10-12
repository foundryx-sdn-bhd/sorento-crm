"""SCM M4 Slice A - cash stage: ranking maths, greedy funding, run persistence + endpoints.

Split in two:

  * PURE maths (no DB) - ``cash_ranking`` graceful-degrade rank_score (AC-M4.1) +
    greedy skip-overflow funding allocation (AC-M4.2/4.4 + M4-D16). Every golden
    expected number is hand-authored here, derived INDEPENDENTLY of the engine.
  * DB-backed - the run's cash stage freezes rank_score/rank on the buy recs
    (AC-M4.1), the recommendations endpoint returns LIVE funding for a budget
    without persisting, and PUT /budget persists funding_status + the budget so a
    second read reflects it (AC-M4.3). Plus auth.

DB tests reuse the ``scm_app`` savepoint fixture + ``purchasing`` role (as
``test_m3_run`` / ``test_policy_config``); ``run_reorder`` is driven synchronously.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.scm import cash_ranking as cr
from app.services.scm import reorder_run_service as svc
from tests.scm.conftest import as_user, requires_pg, seed_user

pytestmark = requires_pg

# canonical M4-D1 default weights (urgency + margin dominant)
W = {"urgency": 0.40, "margin": 0.30, "abc": 0.15, "priority": 0.10, "committed": 0.05}


# ===========================================================================
# PURE - rank_score graceful degrade (AC-M4.1 core)
# ===========================================================================

def test_rank_score_graceful_degrade_drops_absent_factor():
    """A rec missing margin ranks by the REMAINING factors - the absent weight leaves
    BOTH the numerator and denominator (dropped, never zeroed). Hand-derived:
      with margin (v=0.4):    (.4·.8 + .3·.4 + .15·1.0) / (.4+.3+.15) = .59/.85 = 0.694118
      margin DROPPED:         (.4·.8 + .15·1.0)         / (.4+.15)    = .47/.55 = 0.854545
      margin ZEROED (wrong):  (.4·.8 + .3·0 + .15·1.0)  / (.4+.3+.15) = .47/.85 = 0.552941"""
    with_margin = [
        cr.Factor("urgency", 0.40, 0.8, True),
        cr.Factor("margin", 0.30, 0.4, True),
        cr.Factor("abc", 0.15, 1.0, True),
        cr.Factor("priority", 0.10, None, False),
        cr.Factor("committed", 0.05, None, False),
    ]
    dropped = [
        cr.Factor("urgency", 0.40, 0.8, True),
        cr.Factor("margin", 0.30, None, False),   # no cost → dropped
        cr.Factor("abc", 0.15, 1.0, True),
        cr.Factor("priority", 0.10, None, False),
        cr.Factor("committed", 0.05, None, False),
    ]
    assert cr.rank_score(with_margin) == pytest.approx(0.694118, abs=1e-5)
    assert cr.rank_score(dropped) == pytest.approx(0.854545, abs=1e-5)
    # graceful-degrade ≠ zeroing: dropping a LOW factor RAISES the score (denominator
    # shrinks); zeroing would LOWER it to 0.552941.
    assert cr.rank_score(dropped) != pytest.approx(0.552941, abs=1e-5)


def test_build_factors_drops_margin_when_uncosted():
    """build_factors marks margin present only when BOTH list_price AND unit_cost exist
    (M4-D14); abc/priority/committed absent when their inputs are missing."""
    costed = {f.key: f for f in cr.build_factors(
        W, days_of_cover=6, net_position=50, list_price=100, unit_cost=60)}
    uncosted = {f.key: f for f in cr.build_factors(
        W, days_of_cover=6, net_position=50, list_price=100, unit_cost=None)}
    assert costed["margin"].present is True
    assert costed["margin"].value == pytest.approx(0.4)
    assert uncosted["margin"].present is False and uncosted["margin"].value is None
    # a factor with no data is dropped, not zeroed
    assert costed["abc"].present is False and costed["priority"].present is False


def test_weight_swap_reorders_recommendations():
    """Swapping the urgency vs margin weight FLIPS the order of two recs - no code
    change, config-driven (AC-M4.1). P is urgency-heavy, Q is margin-heavy.
      urgency-dominant (.7/.3): P=.7·.9+.3·.2=.69 > Q=.7·.3+.3·.9=.48
      margin-dominant  (.3/.7): P=.3·.9+.7·.2=.41 < Q=.3·.3+.7·.9=.72"""
    # P: days_of_cover 6 → urgency 0.9; list 100/cost 80 → margin 0.2
    # Q: days_of_cover 42 → urgency 0.3; list 100/cost 10 → margin 0.9
    p_inputs = dict(days_of_cover=6, net_position=50, list_price=100, unit_cost=80)
    q_inputs = dict(days_of_cover=42, net_position=50, list_price=100, unit_cost=10)

    wa = {"urgency": 0.7, "margin": 0.3, "abc": 0.0, "priority": 0.0, "committed": 0.0}
    wb = {"urgency": 0.3, "margin": 0.7, "abc": 0.0, "priority": 0.0, "committed": 0.0}

    p_a = cr.rank_score(cr.build_factors(wa, **p_inputs))
    q_a = cr.rank_score(cr.build_factors(wa, **q_inputs))
    p_b = cr.rank_score(cr.build_factors(wb, **p_inputs))
    q_b = cr.rank_score(cr.build_factors(wb, **q_inputs))

    assert p_a == pytest.approx(0.69, abs=1e-6) and q_a == pytest.approx(0.48, abs=1e-6)
    assert p_b == pytest.approx(0.41, abs=1e-6) and q_b == pytest.approx(0.72, abs=1e-6)
    assert p_a > q_a          # urgency-dominant → P ranks above Q
    assert q_b > p_b          # margin-dominant  → Q ranks above P (order flipped)


def test_stockout_urgency_is_max():
    """A stockout (net ≤ 0 OR days_of_cover ≤ 0) pins urgency at 1.0 regardless of DoC."""
    neg_net = {f.key: f for f in cr.build_factors(W, days_of_cover=5, net_position=-10)}
    zero_doc = {f.key: f for f in cr.build_factors(W, days_of_cover=0, net_position=100)}
    assert neg_net["urgency"].value == 1.0 and neg_net["urgency"].present
    assert zero_doc["urgency"].value == 1.0
    # a healthy 60-day cover → 0 urgency (linear map floor)
    healthy = {f.key: f for f in cr.build_factors(W, days_of_cover=60, net_position=100)}
    assert healthy["urgency"].value == pytest.approx(0.0)


# ===========================================================================
# PURE - greedy funding allocation (AC-M4.2 / 4.4 / M4-D16)
# ===========================================================================

def _buys(*specs) -> list[cr.Buy]:
    """specs = (id, rank, cash_impact)."""
    return [cr.Buy(id=i, rank=rk, cash_impact=c) for (i, rk, c) in specs]


def test_greedy_skip_overflow_continues():
    """Greedy-by-rank funds whole items, SKIPS an overflowing one and CONTINUES to the
    next that fits (M4-D3). budget 12000; by rank: 6000 fund→rem6000, 5000 fund→rem1000,
    8000 overflow→deferred, 1000 fits→funded. Σ funded 12000 ≤ budget."""
    result = cr.allocate_funding(
        _buys(("a", 1, 6000), ("b", 2, 5000), ("c", 3, 8000), ("d", 4, 1000)),
        budget=12000)
    assert result.status_by_id == {"a": "funded", "b": "funded",
                                   "c": "deferred", "d": "funded"}
    assert result.funded_cash == 12000 and result.funded_cash <= 12000
    assert result.deferred_cash == 8000
    assert result.funded_count == 3 and result.deferred_count == 1


def test_uncosted_is_needs_cost_regardless_of_budget():
    """An uncosted buy (cash_impact None) is ALWAYS needs_cost and never draws from the
    budget - even a huge budget doesn't fund it (M4-D16)."""
    result = cr.allocate_funding(
        _buys(("a", 1, 5000), ("b", 2, None)), budget=100000)
    assert result.status_by_id == {"a": "funded", "b": "needs_cost"}
    assert result.needs_cost_count == 1
    assert result.funded_cash == 5000        # uncosted never counted against budget


def test_budget_zero_all_costed_deferred():
    """Budget 0 → every COSTED buy is deferred; uncosted still needs_cost."""
    result = cr.allocate_funding(
        _buys(("a", 1, 5000), ("b", 2, 3000), ("c", 3, None)), budget=0)
    assert result.status_by_id == {"a": "deferred", "b": "deferred", "c": "needs_cost"}
    assert result.funded_count == 0 and result.deferred_cash == 8000


def test_budget_ge_total_all_costed_funded():
    """Budget ≥ Σ costed → every costed buy funds; Σ funded ≤ budget."""
    result = cr.allocate_funding(
        _buys(("a", 1, 5000), ("b", 2, 3000), ("c", 3, None)), budget=9000)
    assert result.status_by_id == {"a": "funded", "b": "funded", "c": "needs_cost"}
    assert result.funded_cash == 8000 and result.funded_cash <= 9000
    assert result.deferred_count == 0


# ===========================================================================
# controlled DB fixture builders (all inside the savepoint) - mirror test_m3_run
# ===========================================================================

def _mk_product(db, code, list_price=100, cost_price=60):
    cat, uom = db.execute(text(
        "SELECT category_id, base_uom_id FROM products WHERE category_id IS NOT NULL LIMIT 1"
    )).fetchone()
    pid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO products (id, product_code, product_name, category_id, base_uom_id, "
        "list_price, cost_price, is_active, is_discontinued, currency, created_at, updated_at) "
        "VALUES (:id, :code, :name, :cat, :uom, :lp, :cp, true, false, 'MYR', now(), now())"
    ), {"id": pid, "code": code, "name": f"M4 {code}", "cat": cat, "uom": uom,
        "lp": list_price, "cp": cost_price})
    return pid


def _mk_warehouse(db, code):
    wid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO warehouses (id, warehouse_code, warehouse_name, is_active, created_at, updated_at) "
        "VALUES (:id, :code, :name, true, now(), now())"
    ), {"id": wid, "code": code, "name": f"M4 WH {code}"})
    return wid


def _mk_stock(db, pid, wid, qty):
    db.execute(text(
        "INSERT INTO stock (id, product_id, warehouse_id, quantity_on_hand, "
        "synced_to_excel, created_at, updated_at) "
        "VALUES (:id, :p, :w, :q, false, now(), now())"
    ), {"id": str(uuid.uuid4()), "p": pid, "w": wid, "q": qty})


def _mk_demand(db, pid, wid, add, cv=0.2, sample=13):
    db.execute(text(
        "INSERT INTO scm.demand_stat (id, product_id, warehouse_id, avg_daily_demand, "
        "baseline_rate, demand_cv, sample_days, window_days, method, source_system, source_ref, created_at) "
        "VALUES (:id, :p, :w, :add, :add, :cv, :s, 90, 'mean', 'm4test', 't', now())"
    ), {"id": str(uuid.uuid4()), "p": pid, "w": wid, "add": add, "cv": cv, "s": sample})


def _mk_supplier(db, name):
    sid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO suppliers (id, supplier_code, supplier_name, is_active, created_at, updated_at) "
        "VALUES (:id, :code, :name, true, now(), now())"
    ), {"id": sid, "code": f"M4R-{sid[:8]}", "name": name})
    return sid


def _link(db, pid, sid, *, lead=30, moq=100, mult=50, cost=60, primary=True):
    db.execute(text(
        "INSERT INTO product_suppliers (id, product_id, supplier_id, standard_lead_time_days, "
        "moq, order_multiple, unit_cost, currency, is_primary_supplier, created_at) "
        "VALUES (:id, :pid, :sid, :lead, :moq, :mult, :cost, 'MYR', :prim, now())"
    ), {"id": str(uuid.uuid4()), "pid": pid, "sid": sid, "lead": lead, "moq": moq,
        "mult": mult, "cost": cost, "prim": primary})


def _client(scm_app, role_slug):
    app, db, gcu, gcuak = scm_app
    uid = seed_user(db, role_slug)
    as_user(app, gcu, gcuak, uid)
    return app, db


def _seed_two_buys(db):
    """Two costed buys in one warehouse with DIFFERENT urgency (A more urgent than B)."""
    wid = _mk_warehouse(db, "M4W-CASH")
    a = _mk_product(db, "M4P-URGENT")
    b = _mk_product(db, "M4P-CALM")
    _mk_stock(db, a, wid, 5)      # doc ≈ 0.5 → very urgent
    _mk_stock(db, b, wid, 60)     # doc ≈ 6 → less urgent (still < ROP → buys)
    _mk_demand(db, a, wid, 10.0)
    _mk_demand(db, b, wid, 10.0)
    _link(db, a, _mk_supplier(db, "M4 Urgent Supplier"))
    _link(db, b, _mk_supplier(db, "M4 Calm Supplier"))
    db.flush()
    return wid, a, b


# ===========================================================================
# DB - run cash stage freezes rank_score + rank (AC-M4.1)
# ===========================================================================

def test_run_cash_stage_persists_rank_score_and_rank(scm_app):
    """After the run, every BUY rec carries a non-null frozen rank_score and a dense
    rank (a permutation 1..N); the more-urgent SKU ranks first."""
    _, db, _, _ = scm_app
    _, a, b = _seed_two_buys(db)

    created = svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    assert svc.run_reorder(created["run_id"], db=db)["status"] == "completed"

    rows = db.execute(text(
        "SELECT product_id, rank, rank_score FROM scm.reorder_recommendation "
        "WHERE run_id = :id AND rec_type = 'buy'"
    ), {"id": created["run_id"]}).mappings().all()
    assert len(rows) == 2
    assert all(r["rank_score"] is not None for r in rows), "rank_score frozen on every buy"
    ranks = sorted(int(r["rank"]) for r in rows)
    assert ranks == [1, 2], "dense 1..N ranking"
    by_pid = {str(r["product_id"]): r for r in rows}
    # A (urgent) outranks B (calm) → lower rank number + higher score
    assert int(by_pid[a]["rank"]) < int(by_pid[b]["rank"])
    assert float(by_pid[a]["rank_score"]) >= float(by_pid[b]["rank_score"])


# ===========================================================================
# DB - endpoint: live funding at view-time, no persistence (AC-M4.2/4.3)
# ===========================================================================

def test_get_recommendations_budget_is_live_and_non_mutating(scm_app):
    """GET ?budget=X returns live funding_status on buy rows WITHOUT persisting: a huge
    budget funds all, budget 0 defers all - and the stored funding_status stays null."""
    app, db = _client(scm_app, "purchasing")
    _, a, b = _seed_two_buys(db)
    created = svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    svc.run_reorder(created["run_id"], db=db)
    rid = created["run_id"]

    with TestClient(app) as c:
        big = c.get(f"/api/v1/scm/reorder-runs/{rid}/recommendations",
                    params={"type": "buy", "budget": 10_000_000, "limit": 500})
        assert big.status_code == 200, big.text
        buys = [r for r in big.json()["data"] if r["type"] == "buy"]
        assert buys and all(r["funding_status"] == "funded" for r in buys)
        # M4 fields exposed on buy rows
        for r in buys:
            assert r["rank"] is not None and r["rank_score"] is not None
            assert r["cash_impact"] is not None and r["unit_cost"] is not None
            assert isinstance(r["rank_factors"], list) and r["rank_factors"]
            assert r["days_to_stockout"] is not None

        zero = c.get(f"/api/v1/scm/reorder-runs/{rid}/recommendations",
                     params={"type": "buy", "budget": 0, "limit": 500})
        assert all(r["funding_status"] == "deferred"
                   for r in zero.json()["data"] if r["type"] == "buy")

    # the live view-time allocation never persisted - DB funding_status still null
    stored = db.execute(text(
        "SELECT funding_status FROM scm.reorder_recommendation "
        "WHERE run_id = :id AND rec_type = 'buy'"
    ), {"id": rid}).scalars().all()
    assert all(s is None for s in stored), "GET ?budget must not mutate funding_status"


# ===========================================================================
# DB - PUT /budget persists funding + budget; a later GET reflects it (AC-M4.3)
# ===========================================================================

def test_put_budget_persists_and_is_reflected(scm_app):
    """PUT /budget stamps funding_status on every buy + budget_amount on the run; a GET
    WITHOUT a budget then reflects the persisted split."""
    app, db = _client(scm_app, "purchasing")
    _, a, b = _seed_two_buys(db)
    created = svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    svc.run_reorder(created["run_id"], db=db)
    rid = created["run_id"]

    with TestClient(app) as c:
        put = c.put(f"/api/v1/scm/reorder-runs/{rid}/budget", json={"budget": 10_000_000})
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["run_id"] == rid and body["budget"] == 10_000_000
        assert body["funded_count"] == 2 and body["deferred_count"] == 0
        assert set(body) == {"run_id", "budget", "funded_count", "deferred_count",
                             "needs_cost_count", "funded_cash", "deferred_cash"}

        # a later read with NO budget reflects the persisted funding
        got = c.get(f"/api/v1/scm/reorder-runs/{rid}/recommendations",
                    params={"type": "buy", "limit": 500})
        assert all(r["funding_status"] == "funded"
                   for r in got.json()["data"] if r["type"] == "buy")

    run = db.execute(text(
        "SELECT budget_amount FROM scm.reorder_run WHERE id = :id"), {"id": rid}
    ).mappings().first()
    assert float(run["budget_amount"]) == 10_000_000


def test_put_budget_denied_without_reorder_run_permission(scm_app):
    """Auth: persisting a budget needs scm.reorder.run; a bare user is denied."""
    app, _ = _client(scm_app, None)
    with TestClient(app) as c:
        res = c.put(f"/api/v1/scm/reorder-runs/{uuid.uuid4()}/budget", json={"budget": 1000})
    assert res.status_code == 403


# ===========================================================================
# M7 - market-research priority factor (opt-in, bounded, qty-neutral)
# ===========================================================================

def test_market_value_symmetric_and_scaled():
    """AC-M7.2 - up→1.0, flat→0.5, down→0.0; unknown/None→absent; strength scales."""
    assert cr.market_value("up") == 1.0
    assert cr.market_value("flat") == 0.5
    assert cr.market_value("down") == 0.0
    assert cr.market_value(None) is None
    assert cr.market_value("sideways") is None  # unknown trend → dropped
    # strength scales toward the extreme
    assert cr.market_value("up", 0.0) == 0.5
    assert cr.market_value("up", 1.0) == 1.0
    assert cr.market_value("up", 0.5) == pytest.approx(0.75)
    assert cr.market_value("down", 1.0) == 0.0
    assert cr.market_value("down", 0.5) == pytest.approx(0.25)


def test_market_factor_raises_up_over_down_and_drops_when_absent():
    """AC-M7.3/7.4 - up-trend ranks above down-trend; with no signal the market factor
    is dropped so the score is byte-identical to pre-M7 (a run without market signals)."""
    base = dict(days_of_cover=6, net_position=50, list_price=100, unit_cost=60, abc_class="A")
    up = cr.rank_score(cr.build_factors(cr.DEFAULT_WEIGHTS, **base,
                                        market_signal_value=cr.market_value("up")))
    down = cr.rank_score(cr.build_factors(cr.DEFAULT_WEIGHTS, **base,
                                          market_signal_value=cr.market_value("down")))
    none_ = cr.rank_score(cr.build_factors(cr.DEFAULT_WEIGHTS, **base, market_signal_value=None))
    assert up > down  # trending category floats up the funding queue

    # no signal → market dropped → identical to the pre-M7 factor set (W has no market key)
    assert none_ == pytest.approx(cr.rank_score(cr.build_factors(W, **base)))

    fmap = {f.key: f for f in cr.build_factors(cr.DEFAULT_WEIGHTS, **base,
                                               market_signal_value=cr.market_value("up"))}
    assert fmap["market"].present is True and fmap["market"].value == 1.0
    fmap_absent = {f.key: f for f in cr.build_factors(cr.DEFAULT_WEIGHTS, **base)}
    assert fmap_absent["market"].present is False and fmap_absent["market"].value is None


def test_run_include_market_shifts_rank_not_qty(scm_app):
    """AC-M7.7/7.8 - an up-trend signal on a buy's category raises its rank_score (and
    freezes the signal for explainability) while leaving the order quantity untouched;
    a run WITHOUT the flag carries no market factor at all."""
    _, db, _, _ = scm_app
    _, a, b = _seed_two_buys(db)
    cat_a = db.execute(
        text("SELECT category_id::text FROM products WHERE id = :p"), {"p": a}
    ).scalar()
    db.execute(
        text(
            "INSERT INTO scm.market_signal "
            "(id, category_ref, trend, summary, captured_at, source_system, created_at) "
            "VALUES (:id, :c, 'up', 'sage green trending', now(), 'test', now())"
        ),
        {"id": str(uuid.uuid4()), "c": cat_a},
    )
    db.flush()

    def _by_pid(run_id):
        return {
            str(x["product_id"]): x
            for x in db.execute(
                text(
                    "SELECT product_id, rank_score, rounded_qty, inputs "
                    "FROM scm.reorder_recommendation WHERE run_id = :r AND rec_type = 'buy'"
                ),
                {"r": run_id},
            ).mappings()
        }

    r0 = svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    assert svc.run_reorder(r0["run_id"], db=db)["status"] == "completed"
    base = _by_pid(r0["run_id"])

    r1 = svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False, include_market=True)
    assert svc.run_reorder(r1["run_id"], db=db)["status"] == "completed"
    mkt = _by_pid(r1["run_id"])

    # rank_score rose with the up-trend; the order quantity is byte-identical (AC-M7.8)
    assert float(mkt[a]["rank_score"]) > float(base[a]["rank_score"])
    assert mkt[a]["rounded_qty"] == base[a]["rounded_qty"]

    # the market factor is present + the signal frozen for "why this rank"
    a_factors = {f["key"]: f for f in mkt[a]["inputs"]["rank_factors"]}
    assert a_factors["market"]["present"] is True
    assert mkt[a]["inputs"]["market_factor"]["summary"] == "sage green trending"

    # the no-flag run carries NO present market factor (backward-compatible)
    base_factors = {f["key"]: f for f in base[a]["inputs"]["rank_factors"]}
    assert base_factors.get("market", {}).get("present") in (False, None)
    assert "market_factor" not in (base[a]["inputs"] or {})


def test_create_run_endpoint_accepts_include_market(scm_app):
    """AC-M7.9 - the create-run endpoint accepts the include_market flag and persists it."""
    app, db = _client(scm_app, "purchasing")
    _seed_two_buys(db)
    db.commit()
    with TestClient(app) as c:
        res = c.post(
            "/api/v1/scm/reorder-runs",
            json={"warehouse_codes": ["M4W-CASH"], "buy_scope": "warehouse", "include_market": True},
        )
        assert res.status_code == 202, res.text
        run_id = res.json()["run_id"]
    stored = db.execute(
        text("SELECT include_market FROM scm.reorder_run WHERE id = :r"), {"r": run_id}
    ).scalar()
    assert stored is True
