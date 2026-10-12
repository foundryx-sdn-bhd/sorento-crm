"""SCM M8 Slice E (E10 + E11) - cross-run history + market signal -> qty proposal.

Two features, both read-only against the recommendation table:

  * E10 ``query_past_plans`` - prior COMPLETED-run lines for the same SKU, its
    category siblings (category id-OR-code), and its ``variant_of_id`` neighbours;
    newest run first, bounded, empty on no history. Also wired into the plan-chat
    context so the LLM can answer "how did we handle X before".
  * E11 market-proposal - a market signal maps to the run's matching BUY recs and
    returns a per-line qty-uplift PROPOSAL (bounded +12%, recomputed cash delta,
    reason from the signal). Ambiguous matches are LISTED, not collapsed.

HARD GUARDRAIL (M8-E7): the proposal writes NOTHING to ``reorder_recommendation``
and never re-runs the engine - asserted byte-identical before/after the endpoint.

Fixtures reuse the ``scm_app`` savepoint + the M4 seed helpers + the M5 explainer
fake-provider pattern. Nothing here reaches the network (market search is stubbed).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.scm import MarketSignal
from app.services.scm import explainer_service
from app.services.scm import market_proposal_service as proposal_svc
from app.services.scm import market_research_service
from app.services.scm import reorder_engine
from app.services.scm import reorder_run_service as run_svc
from tests.scm.conftest import SORENTO_COMPANY_ID, requires_pg
from tests.scm.test_m4_cash import _client, _seed_two_buys
from tests.scm.test_m5_explainer import _install_provider, _seed_run, _user_block

pytestmark = requires_pg


def _answer_call(fake) -> dict:
    """The chat-ANSWER call of an `answer_run_chat` turn. Since M8-F16 that turn also runs the
    schema-forced action parse (`json_schema` kwarg) whenever the run has costed buys, and
    it runs first, so `fake.calls[0]` is the parse, not the answer."""
    answers = [c for c in fake.calls if not c["kwargs"].get("json_schema")]
    assert len(answers) == 1, [c["kwargs"] for c in fake.calls]
    return answers[0]


def _answer_block(fake) -> str:
    """The user message content of the chat-answer call (see `_answer_call`)."""
    return _answer_call(fake)["messages"][1]["content"]


# ===========================================================================
# direct-insert helpers (E10 history - precise control of category + variants)
# ===========================================================================

def _distinct_cat_uom(db, n=2):
    rows = db.execute(
        text(
            "SELECT DISTINCT category_id::text, base_uom_id::text FROM products "
            "WHERE category_id IS NOT NULL AND base_uom_id IS NOT NULL LIMIT :n"
        ),
        {"n": n},
    ).all()
    return [(r[0], r[1]) for r in rows]


def _mk_product_in(db, code, cat, uom, *, variant_of=None):
    pid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO products (id, product_code, product_name, category_id, base_uom_id, "
            "list_price, cost_price, is_active, is_discontinued, currency, variant_of_id, "
            "created_at, updated_at) "
            "VALUES (:id, :code, :name, :cat, :uom, 100, 60, true, false, 'MYR', :vof, "
            "now(), now())"
        ),
        {"id": pid, "code": code, "name": f"M8E {code}", "cat": cat, "uom": uom, "vof": variant_of},
    )
    return pid


def _mk_run(db, *, status="completed", created_at=None):
    rid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO scm.reorder_run (id, status, company_id, created_at) "
            "VALUES (:id, :st, :co, COALESCE(:ca, now()))"
        ),
        {"id": rid, "st": status, "ca": created_at, "co": SORENTO_COMPANY_ID},
    )
    return rid


def _mk_hist_rec(db, run_id, product_id, *, rounded_qty=100, funding_status="funded",
                 status="proposed", days_of_cover=5, override_reason=None):
    rid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO scm.reorder_recommendation "
            "(id, run_id, rec_type, product_id, rounded_qty, funding_status, status, "
            "days_of_cover, inputs, company_id, created_at) "
            "VALUES (:id, :run, 'buy', :p, :q, :fs, :st, :doc, cast(:inp as jsonb), :co, now())"
        ),
        {"id": rid, "run": run_id, "p": product_id, "q": rounded_qty, "fs": funding_status,
         "st": status, "doc": days_of_cover, "inp": json.dumps({"sku": "x"}),
         "co": SORENTO_COMPANY_ID},
    )
    if override_reason is not None:
        db.execute(
            text(
                "INSERT INTO scm.recommendation_override "
                "(id, recommendation_id, reason_text, action_applied, company_id, created_at) "
                "VALUES (:id, :rec, :reason, false, :co, now())"
            ),
            {"id": str(uuid.uuid4()), "rec": rid, "reason": override_reason,
             "co": SORENTO_COMPANY_ID},
        )
    return rid


# ===========================================================================
# E10 - query_past_plans: same SKU + category siblings across runs
# ===========================================================================

def test_past_plans_same_sku_and_category_siblings_newest_first(scm_app):
    _, db, _, _ = scm_app
    cats = _distinct_cat_uom(db, 1)
    assert cats, "prod-copy DB must have at least one category+uom"
    (cat1, uom1) = cats[0]
    a = _mk_product_in(db, f"M8E-A-{uuid.uuid4().hex[:6]}", cat1, uom1)
    b = _mk_product_in(db, f"M8E-B-{uuid.uuid4().hex[:6]}", cat1, uom1)  # same-category sibling
    a_code = db.execute(text("SELECT product_code FROM products WHERE id = :i"), {"i": a}).scalar()
    b_code = db.execute(text("SELECT product_code FROM products WHERE id = :i"), {"i": b}).scalar()

    old_run = _mk_run(db, created_at=datetime.utcnow() - timedelta(days=3))
    new_run = _mk_run(db, created_at=datetime.utcnow() - timedelta(days=1))
    _mk_hist_rec(db, old_run, a, rounded_qty=100, funding_status="funded")
    _mk_hist_rec(db, old_run, b, rounded_qty=50, funding_status="deferred",
                 status="adjusted", override_reason="bulk discount")
    _mk_hist_rec(db, new_run, a, rounded_qty=120, funding_status="funded")
    db.flush()

    lines = explainer_service.query_past_plans(db, product_code=a_code)
    codes = {ln["product_code"] for ln in lines}
    assert a_code in codes, "the same SKU's prior lines are returned"
    assert b_code in codes, "a same-category sibling's prior lines are returned"

    # newest run first (a's 120 line before a's 100 line)
    a_lines = [ln for ln in lines if ln["product_code"] == a_code]
    assert [ln["rounded_qty"] for ln in a_lines][:2] == [120.0, 100.0]

    # the override reason + decision status project through
    b_line = next(ln for ln in lines if ln["product_code"] == b_code)
    assert b_line["override_reason"] == "bulk discount"
    assert b_line["decision_status"] == "adjusted"
    assert b_line["funding_status"] == "deferred"


def test_past_plans_excludes_current_run(scm_app):
    _, db, _, _ = scm_app
    (cat1, uom1) = _distinct_cat_uom(db, 1)[0]
    a = _mk_product_in(db, f"M8E-EX-{uuid.uuid4().hex[:6]}", cat1, uom1)
    a_code = db.execute(text("SELECT product_code FROM products WHERE id = :i"), {"i": a}).scalar()
    old_run = _mk_run(db, created_at=datetime.utcnow() - timedelta(days=2))
    today_run = _mk_run(db, created_at=datetime.utcnow())
    _mk_hist_rec(db, old_run, a, rounded_qty=100)
    _mk_hist_rec(db, today_run, a, rounded_qty=999)  # marker for "current" run
    db.flush()

    lines = explainer_service.query_past_plans(db, product_code=a_code, exclude_run_id=today_run)
    qtys = [ln["rounded_qty"] for ln in lines if ln["product_code"] == a_code]
    assert 100.0 in qtys
    assert 999.0 not in qtys, "the excluded (current) run's line must not surface"


def test_past_plans_variant_neighbours_across_category(scm_app):
    """A variant child in a DIFFERENT category is still matched via variant_of_id."""
    _, db, _, _ = scm_app
    cats = _distinct_cat_uom(db, 2)
    if len(cats) < 2:
        pytest.skip("needs two distinct categories in the DB")
    (cat1, uom1), (cat2, uom2) = cats
    base = _mk_product_in(db, f"M8E-VA-{uuid.uuid4().hex[:6]}", cat1, uom1)
    variant = _mk_product_in(db, f"M8E-VV-{uuid.uuid4().hex[:6]}", cat2, uom2, variant_of=base)
    base_code = db.execute(text("SELECT product_code FROM products WHERE id = :i"), {"i": base}).scalar()
    v_code = db.execute(text("SELECT product_code FROM products WHERE id = :i"), {"i": variant}).scalar()
    run = _mk_run(db, created_at=datetime.utcnow() - timedelta(days=1))
    _mk_hist_rec(db, run, variant, rounded_qty=88)  # base itself has no rec
    db.flush()

    lines = explainer_service.query_past_plans(db, product_code=base_code)
    v_lines = [ln for ln in lines if ln["product_code"] == v_code]
    assert v_lines and v_lines[0]["rounded_qty"] == 88.0, "variant neighbour history is returned"


def test_past_plans_only_completed_runs(scm_app):
    _, db, _, _ = scm_app
    (cat1, uom1) = _distinct_cat_uom(db, 1)[0]
    a = _mk_product_in(db, f"M8E-RUN-{uuid.uuid4().hex[:6]}", cat1, uom1)
    a_code = db.execute(text("SELECT product_code FROM products WHERE id = :i"), {"i": a}).scalar()
    running = _mk_run(db, status="running", created_at=datetime.utcnow() - timedelta(days=1))
    _mk_hist_rec(db, running, a, rounded_qty=7777)  # marker on a non-completed run
    db.flush()

    lines = explainer_service.query_past_plans(db, product_code=a_code)
    assert all(ln["rounded_qty"] != 7777.0 for ln in lines), "non-completed runs are excluded"


def test_past_plans_empty_when_no_match(scm_app):
    _, db, _, _ = scm_app
    assert explainer_service.query_past_plans(db, product_code=f"NOPE-{uuid.uuid4().hex}") == []
    assert explainer_service.query_past_plans(db, category_ref=f"NOCAT-{uuid.uuid4().hex}") == []
    # nothing at all supplied → empty (no unbounded scan)
    assert explainer_service.query_past_plans(db) == []


def test_past_plans_bounded_by_limit(scm_app):
    _, db, _, _ = scm_app
    (cat1, uom1) = _distinct_cat_uom(db, 1)[0]
    a = _mk_product_in(db, f"M8E-LIM-{uuid.uuid4().hex[:6]}", cat1, uom1)
    a_code = db.execute(text("SELECT product_code FROM products WHERE id = :i"), {"i": a}).scalar()
    for i in range(5):
        r = _mk_run(db, created_at=datetime.utcnow() - timedelta(days=i + 1))
        _mk_hist_rec(db, r, a, rounded_qty=10 * (i + 1))
    db.flush()
    lines = explainer_service.query_past_plans(db, product_code=a_code, limit=2)
    assert len(lines) == 2, "the limit bounds the result set"


# ===========================================================================
# E10 - plan-chat context injection: past_plans block present when SKU mentioned
# ===========================================================================

def test_chat_injects_past_plans_when_sku_mentioned(scm_app, monkeypatch):
    _, db, _, _ = scm_app
    _seed_two_buys(db)
    prior = run_svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    assert run_svc.run_reorder(prior["run_id"], db=db)["status"] == "completed"
    today = run_svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    assert run_svc.run_reorder(today["run_id"], db=db)["status"] == "completed"

    fake = _install_provider(monkeypatch, "Last run we ordered the same qty.")
    explainer_service.answer_run_question(
        db, today["run_id"], "How did we handle M4P-URGENT before?"
    )
    block = _user_block(fake)
    assert '"past_plans"' in block, "a mentioned SKU must prefetch cross-run history"
    assert "M4P-URGENT" in block


def test_chat_no_past_plans_block_when_no_sku(scm_app, monkeypatch):
    _, db, _, _ = scm_app
    run_id = _seed_run(db)
    fake = _install_provider(monkeypatch, "x")
    explainer_service.answer_run_question(db, run_id, "Which buys are most urgent?")
    assert '"past_plans"' not in _user_block(fake)


def test_past_plans_endpoint_excludes_current_run(scm_app):
    app, db = _client(scm_app, "purchasing")
    (cat1, uom1) = _distinct_cat_uom(db, 1)[0]
    a = _mk_product_in(db, f"M8E-EP-{uuid.uuid4().hex[:6]}", cat1, uom1)
    a_code = db.execute(text("SELECT product_code FROM products WHERE id = :i"), {"i": a}).scalar()
    old_run = _mk_run(db, created_at=datetime.utcnow() - timedelta(days=2))
    today_run = _mk_run(db, created_at=datetime.utcnow())
    _mk_hist_rec(db, old_run, a, rounded_qty=100)
    _mk_hist_rec(db, today_run, a, rounded_qty=999)
    db.commit()

    with TestClient(app) as c:
        res = c.get(
            f"/api/v1/scm/reorder-runs/{today_run}/past-plans",
            params={"product_code": a_code},
        )
    assert res.status_code == 200, res.text
    qtys = [ln["rounded_qty"] for ln in res.json()["data"] if ln["product_code"] == a_code]
    assert 100.0 in qtys and 999.0 not in qtys


def test_past_plans_endpoint_denied_without_dashboard_view(scm_app):
    app, _ = _client(scm_app, None)
    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/reorder-runs/{uuid.uuid4()}/past-plans")
    assert res.status_code == 403


# ===========================================================================
# E11 - _proposed_qty pure rule (bounded +12%, never below original)
# ===========================================================================

def test_proposed_qty_uplift_and_multiple_rounding():
    # old 100, +12% = 112, floor moq 100, round UP to 50-multiple → 150
    assert proposal_svc._proposed_qty(100, moq=100, order_multiple=50, max_qty=None) == 150.0
    # no multiple → ceil to a whole unit, still strictly greater than old
    assert proposal_svc._proposed_qty(100, moq=None, order_multiple=None, max_qty=None) == 112.0
    # max cap clamps the uplift
    assert proposal_svc._proposed_qty(100, moq=None, order_multiple=None, max_qty=105) == 105.0
    # a tiny qty whose rounding collapses the uplift is nudged by one step
    assert proposal_svc._proposed_qty(1, moq=None, order_multiple=None, max_qty=None) > 1


# ===========================================================================
# E11 - proposal maps a signal to the run's matching BUY recs (service)
# ===========================================================================

def _seed_run_with_category(db):
    """A real completed run over two same-category buys; returns (run_id, category_code)."""
    _seed_two_buys(db)
    created = run_svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    assert run_svc.run_reorder(created["run_id"], db=db)["status"] == "completed"
    a_pid = db.execute(
        text(
            "SELECT product_id FROM scm.reorder_recommendation "
            "WHERE run_id = :r AND rec_type = 'buy' LIMIT 1"
        ),
        {"r": created["run_id"]},
    ).scalar()
    category = reorder_engine.load_category_code(db, a_pid)
    assert category
    return created["run_id"], category


def _mk_signal_row(db, category, *, summary="Resin prices climbing", trend="up"):
    sig = MarketSignal(
        category_ref=category,
        currency="MYR",
        trend=trend,
        summary=summary,
        captured_at=datetime.utcnow(),
        source_url="http://example.com/resin",
        source_system="m8etest",
    )
    db.add(sig)
    db.flush()
    return sig


def test_market_proposal_maps_signal_to_buys_and_lists_candidates(scm_app):
    _, db, _, _ = scm_app
    run_id, category = _seed_run_with_category(db)
    sig = _mk_signal_row(db, category)

    out = proposal_svc.build_market_proposal(db, run_id, signal_id=sig.id)
    assert out["signal_summary"] == "Resin prices climbing"
    assert out["source_url"] == "http://example.com/resin"
    # both same-category buys are listed (ambiguous match → candidates, not collapsed)
    assert len(out["lines"]) == 2, "every matching buy rec is a candidate line (M8-E6)"

    for ln in out["lines"]:
        assert ln["new_qty"] > ln["old_qty"], "a bounded uplift is proposed"
        if ln["unit_cost"] is not None:
            expected = round((ln["new_qty"] - ln["old_qty"]) * ln["unit_cost"], 2)
            assert ln["cash_impact_delta"] == expected
        assert "Resin prices climbing" in ln["reason"]
        assert "+12%" in ln["reason"]
        assert ln["rec_id"]


def test_market_proposal_no_signal_is_graceful_empty(scm_app, monkeypatch):
    """query path with no web-search key → search_adhoc finds nothing → empty proposal."""
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    monkeypatch.setattr(market_research_service, "_anthropic_api_key", lambda _db=None: None)

    out = proposal_svc.build_market_proposal(db, run_id, query="resin outlook 2026")
    assert out["lines"] == []
    assert out["signal_summary"] is None


def test_market_proposal_requires_signal_or_query(scm_app):
    from app.services.error_handler import AppException

    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    with pytest.raises(AppException) as ei:
        proposal_svc.build_market_proposal(db, run_id)
    assert ei.value.status_code == 422


def test_market_proposal_missing_run_404(scm_app):
    from app.services.error_handler import AppException

    _, db, _, _ = scm_app
    with pytest.raises(AppException) as ei:
        proposal_svc.build_market_proposal(db, str(uuid.uuid4()), signal_id=str(uuid.uuid4()))
    assert ei.value.status_code == 404


def test_market_proposal_missing_signal_404(scm_app):
    from app.services.error_handler import AppException

    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    with pytest.raises(AppException) as ei:
        proposal_svc.build_market_proposal(db, run_id, signal_id=str(uuid.uuid4()))
    assert ei.value.status_code == 404


# ===========================================================================
# E11 - GUARDRAIL (M8-E7): the proposal endpoint writes NO recommendation column
# ===========================================================================

_REC_COLS = (
    "rounded_qty", "recommended_qty", "reorder_point", "net_position", "cash_impact",
    "rank", "rank_score", "unit_cost", "days_of_cover", "forecast_daily_demand",
    "funding_status", "status", "explanation", "market_advisory",
)


def _run_rec_snapshot(db, run_id: str) -> list[dict]:
    cols = ", ".join(_REC_COLS)
    rows = db.execute(
        text(
            f"SELECT id::text AS id, {cols} FROM scm.reorder_recommendation "
            "WHERE run_id = :r ORDER BY id"
        ),
        {"r": run_id},
    ).mappings().all()
    return [{k: (None if v is None else str(v)) for k, v in r.items()} for r in rows]


def test_market_proposal_endpoint_writes_no_recommendation_column(scm_app):
    app, db = _client(scm_app, "purchasing")
    run_id, category = _seed_run_with_category(db)
    sig = _mk_signal_row(db, category)
    sig_id = sig.id
    db.commit()

    before = _run_rec_snapshot(db, run_id)
    with TestClient(app) as c:
        res = c.post(
            f"/api/v1/scm/reorder-runs/{run_id}/market-proposal",
            json={"signal_id": sig_id},
        )
    assert res.status_code == 200, res.text
    assert len(res.json()["lines"]) == 2

    # re-read from the DB - every recommendation column is byte-identical (proposal only)
    db.expire_all()
    after = _run_rec_snapshot(db, run_id)
    assert after == before, "the market proposal must not write any recommendation column"


def test_market_proposal_endpoint_denied_without_run_perm(scm_app):
    app, _ = _client(scm_app, None)  # authenticated, no scm.reorder.run
    with TestClient(app) as c:
        res = c.post(
            f"/api/v1/scm/reorder-runs/{uuid.uuid4()}/market-proposal",
            json={"signal_id": str(uuid.uuid4())},
        )
    assert res.status_code == 403


# ===========================================================================
# M8-F6 - unified assistant: ONE Ask input auto-routes a live market search and
# attaches a confirm-gated proposal to the SAME chat response when lines match.
# ===========================================================================

def _stub_live_search(monkeypatch, sig):
    """Stub the key-gated ad-hoc web search so ``build_market_proposal``'s query path
    resolves to an already-seeded signal (no network, no LLM extraction)."""
    def fake(db, query, category_ref=None, actor=None):
        return {
            "signals": [{"id": sig.id}],
            "run": {"id": str(uuid.uuid4()), "status": "completed",
                    "signal_count": 1, "error": None},
        }
    monkeypatch.setattr(market_research_service, "search_adhoc", fake)


def test_chat_market_intent_attaches_proposal_when_lines_match(scm_app, monkeypatch):
    _, db, _, _ = scm_app
    run_id, category = _seed_run_with_category(db)
    sig = _mk_signal_row(db, category)
    _stub_live_search(monkeypatch, sig)
    fake = _install_provider(monkeypatch, "Resin prices are trending up; I mapped that to your plan.")

    out = explainer_service.answer_run_chat(db, run_id, "what's the market trend for resin?")
    assert isinstance(out, dict)
    assert out["answer"], "the trend is answered conversationally"
    assert out["proposal"] is not None, "a mapped signal attaches a confirm-gated proposal"
    assert len(out["proposal"]["lines"]) == 2
    # the live reading was folded into the context the LLM reasoned over
    assert '"live_market_scan"' in _answer_block(fake)


def test_chat_market_intent_no_signal_gives_no_proposal(scm_app, monkeypatch):
    """Trend question, but no web-search key → search finds nothing → no proposal, and
    the context carries an 'unavailable' note so the LLM can say so gracefully."""
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    monkeypatch.setattr(market_research_service, "_anthropic_api_key", lambda _db=None: None)
    fake = _install_provider(monkeypatch, "I could not get a live market reading right now.")

    out = explainer_service.answer_run_chat(db, run_id, "any market trend I should know about?")
    assert out["proposal"] is None
    assert '"live_market_scan"' in _answer_block(fake)


def test_chat_plain_question_never_runs_market_search(scm_app, monkeypatch):
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        return {"signals": [], "run": {}}

    monkeypatch.setattr(market_research_service, "search_adhoc", boom)
    fake = _install_provider(monkeypatch, "The basin buy eats the most cash.")

    out = explainer_service.answer_run_chat(db, run_id, "which buys eat the most cash?")
    assert out["proposal"] is None
    assert called["n"] == 0, "a plain plan question must not trigger a live market search"
    assert '"live_market_scan"' not in _answer_block(fake)


def test_chat_market_path_writes_no_recommendation_column(scm_app, monkeypatch):
    """M8-E7 guardrail: the auto-routed market path writes NO recommendation column."""
    _, db, _, _ = scm_app
    run_id, category = _seed_run_with_category(db)
    sig = _mk_signal_row(db, category)
    _stub_live_search(monkeypatch, sig)
    _install_provider(monkeypatch, "trend answer")

    before = _run_rec_snapshot(db, run_id)
    out = explainer_service.answer_run_chat(db, run_id, "market trend for resin?")
    assert out["proposal"] is not None
    db.expire_all()
    after = _run_rec_snapshot(db, run_id)
    assert after == before, "the chat market path must not write any recommendation column"


def test_chat_endpoint_attaches_proposal_for_market_question(scm_app, monkeypatch):
    app, db = _client(scm_app, "purchasing")
    run_id, category = _seed_run_with_category(db)
    sig = _mk_signal_row(db, category)
    _stub_live_search(monkeypatch, sig)
    _install_provider(monkeypatch, "Resin is trending up; a proposal is shown below.")
    db.commit()

    with TestClient(app) as c:
        res = c.post(
            f"/api/v1/scm/reorder-runs/{run_id}/chat",
            json={"question": "what's the market trend for resin?"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answer"]
    assert body["proposal"] is not None
    assert len(body["proposal"]["lines"]) == 2


def test_chat_endpoint_plain_question_has_null_proposal(scm_app, monkeypatch):
    app, db = _client(scm_app, "purchasing")
    run_id, _cat = _seed_run_with_category(db)
    _install_provider(monkeypatch, "Here are the top risks.")
    db.commit()

    with TestClient(app) as c:
        res = c.post(
            f"/api/v1/scm/reorder-runs/{run_id}/chat",
            json={"question": "which buys are most urgent?"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answer"] == "Here are the top risks."
    assert body.get("proposal") is None


# ===========================================================================
# M8-F7 - past-plans for a no-SKU "previous / similar plans" question + prompt
# hygiene (no implementation internals leaked; business-language no-history line).
# ===========================================================================

def test_chat_injects_past_plans_for_no_sku_similar_question(scm_app, monkeypatch):
    _, db, _, _ = scm_app
    _seed_two_buys(db)
    prior = run_svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    assert run_svc.run_reorder(prior["run_id"], db=db)["status"] == "completed"
    today = run_svc.create_run(db, ["M4W-CASH"], "warehouse", product_codes=["M4P-URGENT", "M4P-CALM"], enqueue=False)
    assert run_svc.run_reorder(today["run_id"], db=db)["status"] == "completed"

    fake = _install_provider(monkeypatch, "Previously we ordered similar quantities.")
    out = explainer_service.answer_run_chat(
        db, today["run_id"], "tell me about the previous plan for similar plans"
    )
    block = _answer_block(fake)
    assert '"past_plans"' in block, "a no-SKU 'previous/similar plans' question must inject history"
    assert out["proposal"] is None, "a history question is not a market-trend ask"


def test_chat_no_past_plans_block_for_plain_no_sku_question(scm_app, monkeypatch):
    """A plain question with no SKU and no 'previous/similar' intent injects nothing."""
    _, db, _, _ = scm_app
    run_id = _seed_run(db)
    fake = _install_provider(monkeypatch, "ok")
    explainer_service.answer_run_chat(db, run_id, "which buys are most urgent?")
    assert '"past_plans"' not in _answer_block(fake)


def test_run_chat_system_prompt_is_hygienic():
    """The system prompt (M8-F7) forbids leaking implementation internals and pins the
    business-language no-history line."""
    sysmsg = explainer_service._RUN_CHAT_SYSTEM
    assert "I don't have prior plans for similar products yet." in sysmsg
    assert "business terms" in sysmsg
    # explicitly bans the internal vocabulary that leaked in the review
    for banned in ("JSON", "array", "object", "field", "context"):
        assert banned in sysmsg, f"the ban list must name {banned!r} as forbidden vocabulary"
    assert "NEVER mention" in sysmsg


def test_chat_uses_hygienic_system_prompt(scm_app, monkeypatch):
    """Contract: every chat turn is driven by the hygienic system prompt (the model's
    system message is byte-identical to _RUN_CHAT_SYSTEM)."""
    _, db, _, _ = scm_app
    run_id = _seed_run(db)
    fake = _install_provider(monkeypatch, "ok")
    explainer_service.answer_run_chat(db, run_id, "tell me about similar previous plans")
    sysmsg = _answer_call(fake)["messages"][0]
    assert sysmsg["role"] == "system"
    assert sysmsg["content"] == explainer_service._RUN_CHAT_SYSTEM


# ===========================================================================
# M8-F16 - assistant action pipeline: a natural-language plan INSTRUCTION becomes
# a STRUCTURED accept/reject/adjust proposal resolved to REAL rec ids. The LLM
# proposes which lines + which decision (schema-forced structured output); the
# human clicks Apply; NOTHING is written by the chat call (guardrail).
# ===========================================================================

class _SchemaAwareProvider:
    """Fake provider that returns the schema-forced action JSON on the structured
    action-parse call (``json_schema`` kwarg present) and prose otherwise."""

    def __init__(self, structured: str, prose: str):
        self._structured = structured
        self._prose = prose
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        content = self._structured if kwargs.get("json_schema") else self._prose
        return SimpleNamespace(
            content=content, prompt_tokens=0, completion_tokens=0,
            total_tokens=0, tool_calls=[], raw=None,
        )


def _install_action_provider(monkeypatch, *, structured, prose="Reviewing your instruction; Apply below."):
    fake = _SchemaAwareProvider(structured, prose)
    monkeypatch.setattr(
        explainer_service, "_provider_and_model", lambda db: (fake, "fake-model")
    )
    return fake


def _run_buy_skus(db, run_id) -> list[str]:
    rows = db.execute(
        text(
            "SELECT inputs->>'sku' AS sku FROM scm.reorder_recommendation "
            "WHERE run_id = :r AND rec_type = 'buy' ORDER BY sku"
        ),
        {"r": run_id},
    ).mappings().all()
    return [r["sku"] for r in rows if r["sku"]]


def test_chat_action_intent_accepts_named_rejects_the_rest(scm_app, monkeypatch):
    """'buy X only, the rest don't want' → accept X + reject every OTHER buy line, all
    resolved to real rec ids."""
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    skus = _run_buy_skus(db, run_id)
    assert len(skus) >= 2, "the seeded run needs at least two buys"
    keep = skus[0]

    structured = json.dumps({
        "summary": f"Buy {keep} only; reject the rest.",
        "lines": [{"ref": keep, "action": "accept", "new_qty": None, "reason": "customer wants it"}],
        "rest": {"action": "reject", "reason": "not wanted"},
        "unresolved": [],
    })
    fake = _install_action_provider(monkeypatch, structured=structured)

    out = explainer_service.answer_run_chat(db, run_id, f"buy {keep} only, the rest don't want")
    ap = out["action_proposal"]
    assert ap is not None, "an instruction must attach an action_proposal"
    by_sku = {ln["sku"]: ln for ln in ap["lines"]}
    assert by_sku[keep]["action"] == "accept"
    for other in skus[1:]:
        assert by_sku[other]["action"] == "reject", "the rest are rejected"
    # every proposed line resolved to a REAL recommendation id
    assert all(ln["rec_id"] for ln in ap["lines"])
    # a schema-forced structured parse actually ran
    assert any(c["kwargs"].get("json_schema") for c in fake.calls)


def test_chat_action_intent_adjust_carries_new_qty(scm_app, monkeypatch):
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    skus = _run_buy_skus(db, run_id)
    target = skus[0]
    structured = json.dumps({
        "summary": f"Bump {target}.",
        "lines": [{"ref": target, "action": "adjust", "new_qty": 684, "reason": "bump to MoQ"}],
        "rest": None,
        "unresolved": [],
    })
    _install_action_provider(monkeypatch, structured=structured)

    out = explainer_service.answer_run_chat(db, run_id, f"bump {target} to 684")
    ap = out["action_proposal"]
    line = next(ln for ln in ap["lines"] if ln["sku"] == target)
    assert line["action"] == "adjust"
    assert line["new_qty"] == 684
    assert line["current_qty"] is not None, "the frozen current qty is surfaced for the delta"


def test_chat_action_intent_unresolvable_ref_excluded_and_noted(scm_app, monkeypatch):
    """An unknown SKU is left OUT of the lines and named in the summary (never guessed)."""
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    keep = _run_buy_skus(db, run_id)[0]
    structured = json.dumps({
        "summary": "Accept the requested line.",
        "lines": [
            {"ref": keep, "action": "accept", "new_qty": None, "reason": "ok"},
            {"ref": "TOTALLY-UNKNOWN-SKU", "action": "reject", "new_qty": None, "reason": "x"},
        ],
        "rest": None,
        "unresolved": [],
    })
    _install_action_provider(monkeypatch, structured=structured)

    out = explainer_service.answer_run_chat(db, run_id, f"accept {keep}, drop the unknown one")
    ap = out["action_proposal"]
    skus_out = {ln["sku"] for ln in ap["lines"]}
    assert keep in skus_out
    assert "TOTALLY-UNKNOWN-SKU" not in skus_out, "an unresolved ref is not applied"
    assert "TOTALLY-UNKNOWN-SKU" in ap["summary"], "the unresolved ref is named for the user"
    assert len(ap["lines"]) == 1


def test_chat_action_intent_empty_parse_yields_no_proposal(scm_app, monkeypatch):
    """The instruction gate fired but nothing resolved (a question, or no lines) → None."""
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    structured = json.dumps({"summary": "", "lines": [], "rest": None, "unresolved": []})
    _install_action_provider(monkeypatch, structured=structured)
    out = explainer_service.answer_run_chat(db, run_id, "should I accept these or not?")
    assert out["action_proposal"] is None


def test_chat_plain_question_gets_no_action_proposal(scm_app, monkeypatch):
    """A plain analytical question gets no action card. M8-F16 made the schema-forced parse
    itself the gate (`answer_run_chat`: "we always run it and let an empty resolution be the
    real gate"), so the parse runs, resolves nothing, and the turn is answered in prose.
    This test used to assert the parse never ran, the keyword gate that same change removed."""
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    fake = _install_action_provider(monkeypatch, structured="{}", prose="Two buys take most of it.")
    out = explainer_service.answer_run_chat(db, run_id, "which buys eat the most cash?")
    assert out["action_proposal"] is None, "an empty parse is no proposal"
    assert any(c["kwargs"].get("json_schema") for c in fake.calls), "the parse is the gate"
    assert out["answer"] == "Two buys take most of it."
    assert _answer_call(fake)["messages"][0]["content"] == explainer_service._RUN_CHAT_SYSTEM


def test_chat_action_path_writes_no_recommendation_column(scm_app, monkeypatch):
    """M8-F16 guardrail: resolving an instruction into a proposal writes NOTHING."""
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    keep = _run_buy_skus(db, run_id)[0]
    structured = json.dumps({
        "summary": f"Buy {keep} only.",
        "lines": [{"ref": keep, "action": "accept", "new_qty": None, "reason": "wanted"}],
        "rest": {"action": "reject", "reason": "not wanted"},
        "unresolved": [],
    })
    _install_action_provider(monkeypatch, structured=structured)

    before = _run_rec_snapshot(db, run_id)
    out = explainer_service.answer_run_chat(db, run_id, f"buy {keep} only, reject the rest")
    assert out["action_proposal"] is not None
    db.expire_all()
    after = _run_rec_snapshot(db, run_id)
    assert after == before, "the chat action path must not write any recommendation column"


def test_chat_action_no_provider_degrades_to_none(scm_app, monkeypatch):
    _, db, _, _ = scm_app
    run_id, _cat = _seed_run_with_category(db)
    monkeypatch.setattr(explainer_service, "_provider_and_model", lambda db: (None, None))
    out = explainer_service.answer_run_chat(db, run_id, "buy the top 3 only, reject the rest")
    assert out["action_proposal"] is None, "no LLM configured degrades gracefully"
    assert out["answer"], "a grounded fallback answer is still returned"


def test_chat_endpoint_attaches_action_proposal(scm_app, monkeypatch):
    app, db = _client(scm_app, "purchasing")
    run_id, _cat = _seed_run_with_category(db)
    skus = _run_buy_skus(db, run_id)
    keep = skus[0]
    structured = json.dumps({
        "summary": f"Buy {keep} only; reject the rest.",
        "lines": [{"ref": keep, "action": "accept", "new_qty": None, "reason": "wanted"}],
        "rest": {"action": "reject", "reason": "not wanted"},
        "unresolved": [],
    })
    _install_action_provider(monkeypatch, structured=structured)
    db.commit()

    with TestClient(app) as c:
        res = c.post(
            f"/api/v1/scm/reorder-runs/{run_id}/chat",
            json={"question": f"buy {keep} only, reject the rest"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["action_proposal"] is not None
    actions = {ln["sku"]: ln["action"] for ln in body["action_proposal"]["lines"]}
    assert actions[keep] == "accept"
    assert any(a == "reject" for a in actions.values())
