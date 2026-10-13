"""MODEL-PRICES: cost on the usage endpoints and the shared cost SQL (UC-1, UC-2, UC-7, TZ-2).

Plan: documentation/plans/analytics/model-prices-acceptance-criteria.md. Runs on Postgres
(`pg_session`, rolled back). Every row uses a unique model name and a 2020 window, so the shared
tables' existing rows never reach an assertion.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests._ai_usage_client import VIEW, api_client
from tests._pg_fixture import pg_session, unique_code

BASE = "/api/v1/system/ai-assistant/usage"
DAY = datetime(2020, 1, 1, 12, 0, 0)  # naive UTC
WIN = {"from": "2020-01-01T00:00:00", "to": "2020-01-03T00:00:00"}


def _price(db, model, inp="0.75", cached="0.075", out="4.50"):
    db.execute(
        text("INSERT INTO llm_model_prices (model, input_usd_per_1m, cached_input_usd_per_1m, output_usd_per_1m) "
             "VALUES (:m, :i, :c, :o)"),
        {"m": model, "i": inp, "c": cached, "o": out},
    )


def _log(db, *, model, prompt, cached, completion, provider="openai", feature="chatbot_parser",
         user_id=None, contact_id=None, at=DAY):
    lid = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO ai_assistant_usage_logs (id, user_id, contact_id, feature, provider, model, prompt_tokens, "
             "cached_tokens, completion_tokens, total_tokens, tool_calls_count, response_time_ms, was_answered, created_at) "
             "VALUES (:id, :u, :c, :f, :p, :m, :pt, :ct, :co, :tt, 0, 10, true, :at)"),
        {"id": lid, "u": user_id, "c": contact_id, "f": feature, "p": provider, "m": model, "pt": prompt,
         "ct": cached, "co": completion, "tt": prompt + completion, "at": at},
    )
    return lid


def _user(db, name):
    from app.models.user import User

    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@zzt.test", name=name, status="ACTIVE"))
    db.flush()
    return uid


def _contact(db, name):
    cid = unique_code("rc")
    phone = "+6010" + str(uuid.uuid4().int)[:8]
    db.execute(text("INSERT INTO respond_contacts (id, phone_number, name, respond_io_id) VALUES (:i, :p, :n, :r)"),
               {"i": cid, "p": phone, "n": name, "r": f"rio-{uuid.uuid4().hex[:12]}"})
    return cid


def _world(db):
    """Real-example rows: gpt-5.4-mini price shape under a unique name, plus an unpriced model."""
    tag = unique_code("uc", alpha=True)
    priced, unpriced = f"zzt-gpt-{tag}", f"zzt-gemini-{tag}"
    _price(db, priced)
    ids = {
        "cached": _log(db, model=priced, prompt=35805, cached=35072, completion=366),
        "plain": _log(db, model=priced, prompt=35726, cached=0, completion=363),
        "small": _log(db, model=priced, prompt=5064, cached=0, completion=38),
        "unpriced": _log(db, model=unpriced, prompt=3005, cached=0, completion=690, provider="gemini"),
    }
    return tag, priced, unpriced, ids


def _recent(client, **extra):
    res = client.get(f"{BASE}/recent-queries", params={**WIN, "limit": 200, **extra})
    assert res.status_code == 200, res.text
    return res.json()


# ---- UC-1 / UC-2: the shared cost SQL ---------------------------------------------------------

def _cost_row(db, lid):
    from app.services.analytics_connector.prices import cost_sql

    c = cost_sql("u", "p")
    row = db.execute(
        text(f"SELECT {c.cost_usd} AS cost_usd, {c.priced} AS priced, {c.cached_price_missing} AS cpm, {c.cache_unknown} AS cache_unknown "
             "FROM ai_assistant_usage_logs u LEFT JOIN llm_model_prices p ON p.model = u.model WHERE u.id = :id"),
        {"id": lid},
    ).one()
    return row


def test_uc1_cost_sql_matches_real_examples():
    with pg_session() as db:
        _, _, _, ids = _world(db)
        assert Decimal(_cost_row(db, ids["cached"]).cost_usd) == Decimal("0.00482715")
        assert Decimal(_cost_row(db, ids["plain"]).cost_usd) == Decimal("0.028428")
        assert Decimal(_cost_row(db, ids["small"]).cost_usd) == Decimal("0.003969")
        assert _cost_row(db, ids["cached"]).priced is True


def test_uc2_cost_sql_unpriced_is_null_never_zero():
    with pg_session() as db:
        _, _, _, ids = _world(db)
        row = _cost_row(db, ids["unpriced"])
        assert row.cost_usd is None and row.priced is False and row.cpm is False


def test_uc2_cost_sql_cached_tokens_without_cached_price_is_null_and_flagged():
    with pg_session() as db:
        model = f"zzt-nocache-{unique_code('x', alpha=True)}"
        _price(db, model, cached=None)
        with_cache = _log(db, model=model, prompt=1000, cached=500, completion=10)
        no_cache = _log(db, model=model, prompt=1000, cached=0, completion=10)
        row = _cost_row(db, with_cache)
        assert row.cost_usd is None and row.priced is False and row.cpm is True
        ok = _cost_row(db, no_cache)
        assert ok.priced is True and ok.cpm is False
        assert Decimal(ok.cost_usd) == Decimal("0.000795")  # (1000*0.75 + 10*4.5)/1e6


@pytest.mark.parametrize("provider", ["anthropic", "  Anthropic "])
def test_uc1_cost_sql_anthropic_uncached_is_full_prompt(provider):
    with pg_session() as db:
        model = f"zzt-claude-{unique_code('x', alpha=True)}"
        _price(db, model)
        lid = _log(db, model=model, prompt=1000, cached=400, completion=100, provider=provider)
        # uncached 1000 (not 600): (1000*0.75 + 400*0.075 + 100*4.5)/1e6
        assert Decimal(_cost_row(db, lid).cost_usd) == Decimal("0.00123")


def test_cu1_null_cached_tokens_on_a_priced_model_is_cache_unknown():
    with pg_session() as db:
        model = f"zzt-nullc-{unique_code('x', alpha=True)}"
        _price(db, model, cached=None)
        unknown = _cost_row(db, _log(db, model=model, prompt=1000, cached=None, completion=10))
        assert unknown.cost_usd is None and unknown.priced is False
        assert unknown.cpm is False and unknown.cache_unknown is True
        # A recorded 0 still prices normally.
        known = _cost_row(db, _log(db, model=model, prompt=1000, cached=0, completion=10))
        assert known.priced is True and known.cache_unknown is False
        assert Decimal(known.cost_usd) == Decimal("0.000795")


def test_cu1_unpriced_model_with_null_cached_tokens_is_unpriced_not_cache_unknown():
    with pg_session() as db:
        model = f"zzt-noprice-{unique_code('x', alpha=True)}"
        row = _cost_row(db, _log(db, model=model, prompt=1000, cached=None, completion=10))
        assert row.cost_usd is None and row.priced is False
        assert row.cache_unknown is False and row.cpm is False


# `serial_ddl`: re-creates the SHARED analytics_connector views; in the xdist pool it deadlocks
# against another worker doing the same (CI PR gate run 38426480075).
@pytest.mark.serial_ddl
def test_uc1_connector_view_cost_equals_recent_queries_cost():
    from app.services.analytics_connector.views import ensure_cost_views, ensure_views

    with pg_session() as db:
        ensure_views(db)
        ensure_cost_views(db)
        _, priced, _, ids = _world(db)
        db.flush()
        view = {
            r.id: r.cost_usd
            for r in db.execute(text("SELECT id, cost_usd FROM analytics_connector.llm_calls WHERE id = ANY(:ids)"),
                                {"ids": list(ids.values())})
        }
        with api_client(db, [VIEW]) as client:
            items = _recent(client)
        by_model = [i for i in items if i["model"] == priced]
        assert len(by_model) == 3
        api_costs = sorted(Decimal(str(i["cost_usd"])) for i in by_model)
        view_costs = sorted(Decimal(view[ids[k]]) for k in ("cached", "plain", "small"))
        assert api_costs == view_costs


# ---- recent-queries ---------------------------------------------------------------------------

def test_recent_queries_carry_model_cost_priced_flags():
    with pg_session() as db:
        _, priced, unpriced, _ = _world(db)
        model_nc = f"zzt-nc-{unique_code('x', alpha=True)}"
        _price(db, model_nc, cached=None)
        _log(db, model=model_nc, prompt=1000, cached=500, completion=10)
        with api_client(db, [VIEW]) as client:
            items = _recent(client)
        p = sorted((i for i in items if i["model"] == priced), key=lambda i: i["tokens"])
        costs = sorted(round(i["cost_usd"], 8) for i in p)
        assert costs == [0.003969, 0.00482715, 0.028428]
        assert all(i["priced"] is True and i["cached_price_missing"] is False for i in p)
        u = [i for i in items if i["model"] == unpriced]
        assert len(u) == 1 and u[0]["cost_usd"] is None and u[0]["priced"] is False
        assert u[0]["cached_price_missing"] is False
        n = [i for i in items if i["model"] == model_nc]
        assert len(n) == 1 and n[0]["cost_usd"] is None and n[0]["priced"] is False
        assert n[0]["cached_price_missing"] is True


def test_uc7_price_edit_changes_past_call_cost():
    with pg_session() as db:
        _, priced, _, _ = _world(db)
        db.execute(text("UPDATE llm_model_prices SET output_usd_per_1m = 9 WHERE model = :m"), {"m": priced})
        with api_client(db, [VIEW]) as client:
            items = _recent(client)
        small = [i for i in items if i["model"] == priced and i["tokens"] == 5064 + 38]
        assert len(small) == 1
        assert small[0]["cost_usd"] == pytest.approx((5064 * 0.75 + 38 * 9) / 1e6)


# ---- summary / by-day / top-users / top-contacts ----------------------------------------------

def test_summary_cost_and_unpriced_calls():
    with pg_session() as db:
        tag, *_ = _world(db)
        with api_client(db, [VIEW]) as client:
            res = client.get(f"{BASE}/summary", params={**WIN, "feature": "chatbot_parser"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total_messages"] >= 4
        assert body["unpriced_calls"] == 1
        assert body["cost_usd"] == pytest.approx(0.00482715 + 0.028428 + 0.003969)


def test_summary_with_no_priced_calls_is_zero_cost_and_all_unpriced():
    with pg_session() as db:
        m = f"zzt-none-{unique_code('x', alpha=True)}"
        _log(db, model=m, prompt=100, cached=0, completion=1, feature="zzt_only_feature")
        _log(db, model=m, prompt=100, cached=0, completion=1, feature="zzt_only_feature")
        with api_client(db, [VIEW]) as client:
            body = client.get(f"{BASE}/summary", params={**WIN, "feature": "zzt_only_feature"}).json()
        assert body["total_messages"] == body["unpriced_calls"] == 2
        assert body["cost_usd"] == 0


def test_by_day_uses_malaysia_day_and_carries_cost():
    with pg_session() as db:
        m = f"zzt-day-{unique_code('x', alpha=True)}"
        _price(db, m, cached=None)
        # 17:00 UTC on 13 Oct is 01:00 on 14 Oct in Kuala Lumpur; 15:59 UTC is still the 13th.
        _log(db, model=m, prompt=1000, cached=0, completion=10, feature="zzt_day", at=datetime(2026, 10, 13, 17, 0, 0))
        _log(db, model=m, prompt=1000, cached=0, completion=10, feature="zzt_day", at=datetime(2026, 10, 13, 15, 59, 0))
        _log(db, model=f"{m}-unpriced", prompt=1, cached=0, completion=1, feature="zzt_day",
             at=datetime(2026, 10, 13, 17, 30, 0))
        with api_client(db, [VIEW]) as client:
            res = client.get(f"{BASE}/by-day", params={"from": "2026-10-13T00:00:00", "to": "2026-10-14T00:00:00",
                                                       "feature": "zzt_day"})
        assert res.status_code == 200, res.text
        days = {d["date"]: d for d in res.json()}
        assert set(days) == {"2026-10-13", "2026-10-14"}
        assert days["2026-10-13"]["messages"] == 1 and days["2026-10-13"]["unpriced_calls"] == 0
        assert days["2026-10-13"]["cost_usd"] == pytest.approx(0.000795)
        assert days["2026-10-14"]["messages"] == 2 and days["2026-10-14"]["unpriced_calls"] == 1
        assert days["2026-10-14"]["cost_usd"] == pytest.approx(0.000795)


def test_top_users_and_top_contacts_carry_cost():
    with pg_session() as db:
        m = f"zzt-top-{unique_code('x', alpha=True)}"
        _price(db, m, cached=None)
        u1, u2 = _user(db, "ZZT Priced User"), _user(db, "ZZT Unpriced User")
        c1, c2 = _contact(db, "ZZT Priced Contact"), _contact(db, "ZZT Unpriced Contact")
        _log(db, model=m, prompt=1000, cached=0, completion=10, user_id=u1, contact_id=c1, feature="zzt_top")
        _log(db, model=m, prompt=1000, cached=0, completion=10, user_id=u1, contact_id=c1, feature="zzt_top")
        _log(db, model=f"{m}-x", prompt=1000, cached=0, completion=10, user_id=u1, contact_id=c1, feature="zzt_top")
        _log(db, model=f"{m}-x", prompt=10, cached=0, completion=1, user_id=u2, contact_id=c2, feature="zzt_top")
        with api_client(db, [VIEW]) as client:
            users = client.get(f"{BASE}/top-users", params={**WIN, "feature": "zzt_top"})
            contacts = client.get(f"{BASE}/top-contacts", params={**WIN, "feature": "zzt_top"})
        assert users.status_code == contacts.status_code == 200
        by_user = {i["user_id"]: i for i in users.json()}
        assert by_user[u1]["cost_usd"] == pytest.approx(0.00159) and by_user[u1]["unpriced_calls"] == 1
        assert by_user[u2]["unpriced_calls"] == by_user[u2]["messages"] == 1
        assert by_user[u2]["cost_usd"] == 0
        by_contact = {i["contact_id"]: i for i in contacts.json()}
        assert by_contact[c1]["cost_usd"] == pytest.approx(0.00159) and by_contact[c1]["unpriced_calls"] == 1
        assert by_contact[c2]["unpriced_calls"] == 1


# ---- CU-5 / CU-4: unknown cache use on the endpoints -------------------------------------------

def _cu_world(db, feature):
    tag = unique_code("cu", alpha=True)
    priced, nocache = f"zzt-p-{tag}", f"zzt-nc-{tag}"
    _price(db, priced)
    _price(db, nocache, cached=None)
    kw = dict(feature=feature)
    return priced, nocache, kw


def test_cu5_summary_counts_unknown_cache_calls_apart_from_unpriced():
    with pg_session() as db:
        priced, nocache, kw = _cu_world(db, "zzt_cu_sum")
        _log(db, model=priced, prompt=1000, cached=0, completion=10, **kw)            # priced
        _log(db, model=priced, prompt=1000, cached=None, completion=10, **kw)         # cache unknown
        _log(db, model=priced, prompt=1000, cached=None, completion=10, **kw)         # cache unknown
        _log(db, model=f"{priced}-none", prompt=1000, cached=None, completion=10, **kw)  # no price
        _log(db, model=nocache, prompt=1000, cached=500, completion=10, **kw)         # cached price missing
        with api_client(db, [VIEW]) as client:
            body = client.get(f"{BASE}/summary", params={**WIN, "feature": "zzt_cu_sum"}).json()
        assert body["total_messages"] == 5
        assert body["unknown_cache_calls"] == 2
        assert body["unpriced_calls"] == 2
        assert body["cost_usd"] == pytest.approx((1000 * 0.75 + 10 * 4.5) / 1e6)


def test_cu5_by_day_top_users_top_contacts_carry_unknown_cache_calls():
    with pg_session() as db:
        priced, _, kw = _cu_world(db, "zzt_cu_top")
        uid, cid = _user(db, "ZZT CU User"), _contact(db, "ZZT CU Contact")
        for cached in (0, None, None):
            _log(db, model=priced, prompt=1000, cached=cached, completion=10, user_id=uid, contact_id=cid, **kw)
        with api_client(db, [VIEW]) as client:
            days = client.get(f"{BASE}/by-day", params={**WIN, "feature": "zzt_cu_top"}).json()
            users = client.get(f"{BASE}/top-users", params={**WIN, "feature": "zzt_cu_top"}).json()
            contacts = client.get(f"{BASE}/top-contacts", params={**WIN, "feature": "zzt_cu_top"}).json()
        assert [d["unknown_cache_calls"] for d in days] == [2]
        assert days[0]["unpriced_calls"] == 0 and days[0]["cost_usd"] == pytest.approx(0.000795)
        u = next(i for i in users if i["user_id"] == uid)
        assert u["unknown_cache_calls"] == 2 and u["unpriced_calls"] == 0
        assert u["cost_usd"] == pytest.approx(0.000795)
        c = next(i for i in contacts if i["contact_id"] == cid)
        assert c["unknown_cache_calls"] == 2 and c["unpriced_calls"] == 0


def test_cu4_recent_queries_flag_cache_unknown_calls():
    with pg_session() as db:
        priced, _, kw = _cu_world(db, "zzt_cu_rec")
        _log(db, model=priced, prompt=1000, cached=None, completion=10, **kw)
        _log(db, model=priced, prompt=1001, cached=0, completion=10, **kw)
        with api_client(db, [VIEW]) as client:
            items = _recent(client, feature="zzt_cu_rec")
        by_prompt = {i["tokens"]: i for i in items}
        unknown, known = by_prompt[1010], by_prompt[1011]
        assert unknown["cache_unknown"] is True and unknown["cost_usd"] is None
        assert unknown["priced"] is False and unknown["cached_price_missing"] is False
        assert known["cache_unknown"] is False and known["priced"] is True
