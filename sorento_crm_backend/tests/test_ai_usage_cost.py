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
    uid = str(uuid.uuid4())
    db.execute(text("INSERT INTO users (id, email, name, status, is_trashed) VALUES (:i, :e, :n, 'ACTIVE', false)"),
               {"i": uid, "e": f"{uid}@zzt.test", "n": name})
    return uid


def _contact(db, name):
    cid = unique_code("rc")
    db.execute(text("INSERT INTO respond_contacts (id, phone_number, name, respond_io_id) VALUES (:i, :p, :n, :r)"),
               {"i": cid, "p": "+60100000009", "n": name, "r": f"rio-{uuid.uuid4().hex[:12]}"})
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
        text(f"SELECT {c.cost_usd} AS cost_usd, {c.priced} AS priced, {c.cached_price_missing} AS cpm "
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


def test_uc1_cost_sql_null_cached_tokens_counts_as_zero():
    with pg_session() as db:
        model = f"zzt-nullc-{unique_code('x', alpha=True)}"
        _price(db, model, cached=None)
        lid = _log(db, model=model, prompt=1000, cached=None, completion=10)
        row = _cost_row(db, lid)
        assert row.priced is True and Decimal(row.cost_usd) == Decimal("0.000795")


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
