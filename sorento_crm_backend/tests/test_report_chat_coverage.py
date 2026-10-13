"""CA-4: era coverage lines, the default period and `no_reply` on the chat reports
(tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca4-backfill-09oct.md, B10 and Q4; UAC
AC-19 to AC-21. Synthetic facts and runs on a blank schema only.

The line for a text era reads
`<dates>: from message text (estimated), <p>% of <n> messages read.` where n is the era's
latest run's eligible count and p the share of those messages that have a fact; an era with no
run reads `<dates>: not loaded yet.` Only eras the period touches get a line. The CA-1 traces
sentence stays first.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tests._pg_fixture import blank_session

UTC = timezone.utc
MAY = {"kind": "custom", "from": "2026-05-01", "to": "2026-05-31"}
OCTOBER = {"kind": "custom", "from": "2026-10-01", "to": "2026-10-31"}


def _definition(key):
    import app.services.reports  # noqa: F401
    from app.services.reports import registry as reg

    return reg.get(key)


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.base import set_company_scope

        set_company_scope(session, None)
        yield session


def _history_fact(db, when: datetime, *, history_id: int | None = None, seq: int = 1, outcome="answered", **extra):
    from app.models.chat_facts import ChatAskFact

    row = ChatAskFact(
        id=str(uuid.uuid4()),
        source="history_backfill",
        turn_id=None,
        history_id=history_id if history_id is not None else uuid.uuid4().int % 10**12,
        ask_seq=seq,
        contact_respond_id="ZZT-cov",
        audience="dealer",
        contact_type="dealer",
        asked_at=when,
        channel="whatsapp",
        outcome=outcome,
        tools=[],
        is_media=False,
        phantom_dropped=False,
        **extra,
    )
    db.add(row)
    db.flush()
    return row


def _run_row(db, era: str, eligible: int, *, started: datetime):
    from app.models.chat_facts import ChatBackfillRun

    db.add(
        ChatBackfillRun(
            id=str(uuid.uuid4()),
            era=era,
            status="done",
            messages_eligible=eligible,
            started_at=started,
        )
    )
    db.flush()


def _run(db, key="chat_overview", params=None):
    from app.services.reports import engine

    return engine.run(db, _definition(key), params or {"period": MAY}, None)


def test_a_text_era_says_how_much_of_it_was_read(db):
    for day in range(1, 5):
        _history_fact(db, datetime(2026, 5, day, 3, tzinfo=UTC))
    _history_fact(db, datetime(2026, 5, 1, 4, tzinfo=UTC), history_id=777, seq=1)
    _history_fact(db, datetime(2026, 5, 1, 4, tzinfo=UTC), history_id=777, seq=2)  # same message
    _run_row(db, "A", 50, started=datetime(2026, 10, 1, tzinfo=UTC))
    _run_row(db, "A", 10, started=datetime(2026, 10, 2, tzinfo=UTC))  # the latest run counts
    note = _run(db).note
    assert note.startswith("From bot traces, 5 Sep 2026 on.")
    assert "17 Apr to 21 Jul: from message text (estimated), 50% of 10 messages read." in note


def test_an_era_with_no_run_is_not_loaded_yet(db):
    note = _run(db).note
    assert "17 Apr to 21 Jul: not loaded yet." in note


def test_only_the_eras_the_period_touches_get_a_line(db):
    note = _run(db, params={"period": {"kind": "custom", "from": "2026-07-01", "to": "2026-08-31"}}).note
    assert "17 Apr to 21 Jul" in note and "22 Jul to 18 Aug" in note and "19 Aug to 4 Sep" in note
    assert "5 to 22 Sep" not in note
    assert "17 Apr to 21 Jul" not in _run(db, params={"period": OCTOBER}).note
    assert "5 to 22 Sep" not in _run(db, params={"period": OCTOBER}).note


def test_the_untraced_september_slice_has_its_own_line(db):
    _history_fact(db, datetime(2026, 9, 8, 3, tzinfo=UTC))
    _run_row(db, "D", 4, started=datetime(2026, 10, 1, tzinfo=UTC))
    note = _run(db, params={"period": {"kind": "custom", "from": "2026-09-01", "to": "2026-09-30"}}).note
    assert "5 to 22 Sep, messages the bot never traced: from message text (estimated), 25% of 4 messages read." in note


def test_a_run_with_no_eligible_messages_reads_zero_not_a_division_error(db):
    _run_row(db, "A", 0, started=datetime(2026, 10, 1, tzinfo=UTC))
    assert "17 Apr to 21 Jul: from message text (estimated), 0% of 0 messages read." in _run(db).note


@pytest.mark.parametrize("key", ["chat_top_asks", "chat_bot_failures"])
def test_top_asks_and_bot_failures_carry_the_same_lines(db, key):
    _run_row(db, "A", 8, started=datetime(2026, 10, 1, tzinfo=UTC))
    _history_fact(db, datetime(2026, 5, 2, 3, tzinfo=UTC), topic_code=None)
    note = _run(db, key=key).note
    assert "Topics labelled:" in note
    assert "17 Apr to 21 Jul: from message text (estimated), 13% of 8 messages read." in note


def test_the_share_read_never_reads_over_100(db):
    for day in range(1, 4):
        _history_fact(db, datetime(2026, 5, day, 3, tzinfo=UTC))
    _run_row(db, "A", 2, started=datetime(2026, 10, 1, tzinfo=UTC))
    assert "17 Apr to 21 Jul: from message text (estimated), 100% of 2 messages read." in _run(db).note


def test_a_text_era_line_carries_the_reply_rules_once(db):
    """Crew rulings Q1 and Q3: the notes say how a text-era reply was found."""
    _run_row(db, "B", 5, started=datetime(2026, 10, 1, tzinfo=UTC))
    note = _run(db, params={"period": {"kind": "custom", "from": "2026-07-01", "to": "2026-08-31"}}).note
    assert note.count(chat_asks().TEXT_ERA_CAVEAT) == 1
    assert "24 hours" in chat_asks().TEXT_ERA_CAVEAT
    assert "no sender" in chat_asks().TEXT_ERA_CAVEAT
    assert chat_asks().TEXT_ERA_CAVEAT not in _run(db, params={"period": OCTOBER}).note


def test_no_caveat_while_no_text_era_is_loaded(db):
    note = _run(db, params={"period": {"kind": "custom", "from": "2026-07-01", "to": "2026-08-31"}}).note
    assert "not loaded yet" in note
    assert chat_asks().TEXT_ERA_CAVEAT not in note


def chat_asks():
    from app.services.reports.datasets import chat_asks as module

    return module


# --------------------------------------------------------------------------- default period


@pytest.mark.parametrize("key", ["chat_overview", "chat_top_asks", "chat_bot_failures"])
def test_the_default_period_opens_on_the_earliest_fact(db, key):
    # 19 May 17:00 UTC is 20 May in Kuala Lumpur.
    _history_fact(db, datetime(2026, 5, 19, 17, 0, tzinfo=UTC))
    param = next(p for p in _definition(key).params if p.key == "period")
    assert param.resolved_default(db)["from"] == "2026-05-20"


def test_with_no_fact_before_the_traces_the_default_period_opens_on_5_sep(db):
    param = next(p for p in _definition("chat_overview").params if p.key == "period")
    assert param.resolved_default(db)["from"] == "2026-09-05"
    assert param.resolved_default()["from"] == "2026-09-05"


def test_a_run_with_no_period_counts_the_backfilled_history(db):
    _history_fact(db, datetime(2026, 5, 2, 3, tzinfo=UTC))
    from app.services.reports import engine

    result = engine.run(db, _definition("chat_overview"), {}, None)
    assert Decimal(str(result.layouts.summary.grand_total["asks"])) == 1


def test_the_default_view_carries_the_earliest_fact(db):
    _history_fact(db, datetime(2026, 5, 2, 3, tzinfo=UTC))
    from app.services.reports import engine

    view = engine.view_config(_definition("chat_overview"), db)
    assert view.params["period"]["from"] == "2026-05-02"


# --------------------------------------------------------------------------- no_reply


def test_no_reply_is_an_outcome_in_words_counted_and_not_failed(db):
    _history_fact(db, datetime(2026, 5, 2, 3, tzinfo=UTC), outcome="no_reply")
    from app.services.reports.datasets import chat_asks

    assert ("no_reply", "No reply") in chat_asks.OUTCOME_LABELS
    assert "no_reply" in chat_asks.DEFAULT_OUTCOMES
    summary = _run(db).layouts.summary
    assert Decimal(str(summary.grand_total["asks"])) == 1
    assert Decimal(str(summary.grand_total["failed"])) == 0
    assert "No reply" in summary.row_totals


def test_the_report_meta_opens_on_the_earliest_fact(db, monkeypatch):
    """Review round 1: the screen opens on the meta's `default_view` and `params[].default`, so
    the route must hand the session to the default period too (AC-20)."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.models.user import User
    from app.services.user_service import UserPermissionService

    me = {"id": str(uuid.uuid4()), "email": "coverage@zzt.test", "name": "Coverage Caller"}
    db.add(User(id=me["id"], email=me["email"], name=me["name"], status="ACTIVE"))
    _history_fact(db, datetime(2026, 5, 2, 3, tzinfo=UTC))

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: me
    app.dependency_overrides[get_current_user_or_api_key] = lambda: me
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True)
    try:
        resp = TestClient(app).get("/api/v1/reports/chat_overview")
    finally:
        for dep in (get_db, get_current_user, get_current_user_or_api_key):
            app.dependency_overrides.pop(dep, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["default_view"]["params"]["period"]["from"] == "2026-05-02"
    period = next(p for p in body["params"] if p["key"] == "period")
    assert period["default"]["from"] == "2026-05-02"
