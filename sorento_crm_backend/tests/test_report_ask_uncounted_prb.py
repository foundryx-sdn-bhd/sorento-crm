"""Phase 2 RED tests - chat fix PR-B S8 (AC-B10): a refine of a held report and an answer to the
bot's own question do not use up `GET /api/v1/order-management/report-ask`'s rate limit.

PINNED NAME (the tester's, flagged to the captain): the caller says so with the boolean query
param `refine=true`. Today the route answers an unknown param 422 `unknown_param`, which is the
red reason. The limiter is replaced by an in-memory counter with the real arithmetic
(`allowed` while count <= limit), so no Redis is needed; Postgres seeds its own chain.
"""
from __future__ import annotations

import pytest

from app.services import rate_limit
from tests._sales_report_do_seed import client, db  # noqa: F401  (fixtures)
from tests.test_report_ask_rate_limit_setting import _settings_row
from tests.test_report_ask_route import _ask, _full


@pytest.fixture
def counter(monkeypatch):
    seen: dict = {"n": 0}

    def _hit(bucket, ident, *, limit, window_seconds):
        seen["n"] += 1
        return rate_limit.RateResult(allowed=limit <= 0 or seen["n"] <= limit)

    monkeypatch.setattr(rate_limit, "hit", _hit)
    return seen


def test_b10_a_refine_does_not_count_and_is_never_busy(client, db, counter):
    _settings_row(db, report_ask_rate_limit=3, report_ask_rate_window_minutes=10)
    contact = _full(db)
    for _ in range(8):
        resp = _ask(client, contact, refine="true")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] != "busy", resp.json()
    assert counter["n"] == 0, f"{counter['n']} refines were counted"


def test_b10_fresh_asks_still_count_and_the_one_past_the_limit_is_refused(client, db, counter):
    _settings_row(db, report_ask_rate_limit=10, report_ask_rate_window_minutes=10)
    contact = _full(db)
    statuses = [_ask(client, contact).json()["status"] for _ in range(11)]
    assert statuses[:10] == ["ok"] * 10, statuses
    assert statuses[10] == "busy", statuses
    assert counter["n"] == 11


def test_b10_refines_between_fresh_asks_do_not_move_the_count(client, db, counter):
    _settings_row(db, report_ask_rate_limit=10, report_ask_rate_window_minutes=10)
    contact = _full(db)
    statuses = []
    for _ in range(11):
        statuses.append(_ask(client, contact).json()["status"])
        assert _ask(client, contact, refine="true").json()["status"] != "busy"
    assert statuses[:10] == ["ok"] * 10 and statuses[10] == "busy", statuses
    assert counter["n"] == 11


@pytest.mark.parametrize("value", ["false", "0"])
def test_b10_near_miss_refine_false_counts_like_a_fresh_ask(client, db, counter, value):
    _settings_row(db, report_ask_rate_limit=10, report_ask_rate_window_minutes=10)
    contact = _full(db)
    assert _ask(client, contact, refine=value).status_code == 200
    assert counter["n"] == 1
