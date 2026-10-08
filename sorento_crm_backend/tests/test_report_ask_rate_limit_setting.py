"""The report engine's rate limit is a System Setting, not a constant (lane
REPORT-RATE-LIMIT-CONFIG, owner 8 Oct 2026: "make it configurable for me").

`GET /api/v1/order-management/report-ask` caps reports per contact per window. Both numbers
live on `system_settings` (`report_ask_rate_limit`, `report_ask_rate_window_minutes`), are
edited on Settings -> Chatbot media, and are read per request so a change applies on the next
report with no restart. Defaults 10 per 10 minutes; a limit of 0 means no limit; the busy reply
names the configured window.

Postgres only; every row is seeded here and rolled back (CI's database has none).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from pydantic import ValidationError

from app.api.v1.user_management.settings import (
    SystemSettingUpdate,
    _update_general_settings_impl,
    get_settings,
)
from app.models.user import SystemSetting
from app.services import rate_limit
from tests._sales_report_do_seed import client, db  # noqa: F401  (fixtures)
from tests.test_report_ask_route import _ask, _full


def _settings_row(db, **values):
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting(id=str(uuid.uuid4()), name="ZZT Settings")
        db.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    db.commit()
    return row


@pytest.fixture
def hits(monkeypatch):
    """Record every rate limiter call; refuse whenever a limit is set (the counter's own
    arithmetic is `rate_limit.hit`'s, tested with the limiter, not here)."""
    calls: list[dict] = []

    def _hit(bucket, ident, *, limit, window_seconds):
        calls.append({"bucket": bucket, "limit": limit, "window_seconds": window_seconds})
        return rate_limit.RateResult(allowed=limit <= 0)

    monkeypatch.setattr(rate_limit, "hit", _hit)
    return calls


# ------------------------------------------------------------------ the route


def test_the_route_reads_the_limit_and_window_from_system_settings(client, db, hits):
    _settings_row(db, report_ask_rate_limit=2, report_ask_rate_window_minutes=3)
    contact = _full(db)

    body = _ask(client, contact).json()

    assert hits[-1] == {"bucket": "report_ask", "limit": 2, "window_seconds": 180}
    assert body["status"] == "busy"
    assert body["message"] == "Too many reports in the last 3 minutes."


def test_a_changed_setting_applies_on_the_next_request_without_a_restart(client, db, hits):
    _settings_row(db, report_ask_rate_limit=2, report_ask_rate_window_minutes=3)
    contact = _full(db)
    _ask(client, contact)

    _settings_row(db, report_ask_rate_limit=5, report_ask_rate_window_minutes=1)
    body = _ask(client, contact).json()

    assert hits[-1]["limit"] == 5
    assert hits[-1]["window_seconds"] == 60
    assert body["message"] == "Too many reports in the last 1 minute."


def test_a_limit_of_zero_means_no_limit(client, db, hits):
    _settings_row(db, report_ask_rate_limit=0, report_ask_rate_window_minutes=10)
    contact = _full(db)

    resp = _ask(client, contact)

    assert resp.status_code == 200, resp.text
    assert hits[-1]["limit"] == 0
    assert resp.json()["status"] != "busy"


def test_the_defaults_are_ten_reports_per_ten_minutes():
    row = SystemSetting(id=str(uuid.uuid4()), name="ZZT Settings")
    columns = SystemSetting.__table__.c
    assert columns["report_ask_rate_limit"].server_default.arg == "10"
    assert columns["report_ask_rate_window_minutes"].server_default.arg == "10"
    assert columns["report_ask_rate_limit"].default.arg == 10
    assert columns["report_ask_rate_window_minutes"].default.arg == 10
    assert row is not None


# ------------------------------------------------------------- the settings API


@pytest.mark.parametrize("value", [0, 1, 10, 1000])
def test_the_limit_accepts_zero_to_a_thousand(value):
    assert SystemSettingUpdate(report_ask_rate_limit=value).report_ask_rate_limit == value


@pytest.mark.parametrize("value", [-1, 1001])
def test_the_limit_refuses_outside_zero_to_a_thousand(value):
    with pytest.raises(ValidationError):
        SystemSettingUpdate(report_ask_rate_limit=value)


@pytest.mark.parametrize("value", [1, 10, 1440])
def test_the_window_accepts_one_minute_to_a_day(value):
    assert (
        SystemSettingUpdate(report_ask_rate_window_minutes=value).report_ask_rate_window_minutes
        == value
    )


@pytest.mark.parametrize("value", [0, -5, 1441])
def test_the_window_refuses_outside_one_minute_to_a_day(value):
    with pytest.raises(ValidationError):
        SystemSettingUpdate(report_ask_rate_window_minutes=value)


def test_both_round_trip_through_the_update_and_the_get_builders(db):
    _settings_row(db)

    _update_general_settings_impl(
        SystemSettingUpdate(report_ask_rate_limit=4, report_ask_rate_window_minutes=30), db
    )
    payload = asyncio.run(get_settings(current_user={"id": "zzt"}, db=db))

    assert payload["settings"]["report_ask_rate_limit"] == 4
    assert payload["settings"]["report_ask_rate_window_minutes"] == 30
