"""CA-2 taxonomy: the "Bot failures" report (tester-first RED).

Plan: PLAN-chat-analytics-ca2-taxonomy-09oct.md, build contract, last bullet of "Top asks";
UAC U25. Key `chat_bot_failures`: the Top asks datasets, outcome default = the failed outcomes
(technical, cannot_do, missed), pivot rows `failure_reason`, columns `topic`, measure `asks`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tests._pg_fixture import blank_session

UTC = timezone.utc
KEY = "chat_bot_failures"
PERMISSION = "chatbot.analytics.view"
PERIOD = {"kind": "custom", "from": "2026-09-01", "to": "2026-10-31"}


def _uid() -> str:
    return str(uuid.uuid4())


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def _definition():
    import app.services.reports  # noqa: F401
    from app.services.reports import registry as reg

    definition = reg.get(KEY)
    assert definition is not None, "the chat_bot_failures report is not registered by app.services.reports"
    return definition


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.base import set_company_scope

        set_company_scope(session, None)
        yield session


def _seed(db):
    from app.models.chat_facts import ChatAskFact, ChatTopic

    db.add(ChatTopic(code="inventory", label="Stock", definition="Stock", version=1))
    db.flush()
    db.add(ChatTopic(code="inventory.availability", parent_code="inventory", label="Availability",
                     definition="Availability", version=2))
    db.flush()

    def fact(outcome, day, reason=None, topic=None):
        db.add(ChatAskFact(
            id=_uid(), source="trace", turn_id=_uid(), ask_seq=1, contact_respond_id=f"ZZT-bf-{_uid()[:8]}",
            audience="dealer", contact_type="dealer", asked_at=datetime(2026, 9, day, 3, 0, tzinfo=UTC),
            channel="whatsapp", outcome=outcome, failure_reason=reason, is_media=False, phantom_dropped=False,
            domain="inventory", topic_code=topic, taxonomy_version=2 if topic else None, tools=[],
        ))

    fact("technical", 8, "technical", "inventory.availability")
    fact("technical", 9, "technical")
    fact("cannot_do", 10, "cannot_do", "inventory.availability")
    fact("missed", 11, "missed")
    fact("answered", 12)
    fact("clarified", 13)
    db.flush()


def _run(db, params=None, view=None):
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine

    merged = {"period": PERIOD}
    merged.update(params or {})
    return engine.run(db, _definition(), merged, ReportViewConfig.model_validate(view) if view else None)


def test_the_definition_is_registered_with_its_contract(db):
    definition = _definition()
    assert definition.key == KEY
    assert definition.permission == PERMISSION
    assert definition.module_key == "chatbot"
    assert definition.dataset.scope == "none"


def test_the_outcome_filter_defaults_to_the_failed_outcomes(db):
    param = next(p for p in _definition().params if p.key == "outcome")
    assert set(param.default) == {"technical", "cannot_do", "missed"}
    assert set(_definition().default_view["params"]["outcome"]) == {"technical", "cannot_do", "missed"}


def test_the_default_view_is_failure_reason_by_topic(db):
    assert _definition().default_view["pivot"] == {"rows": "failure_reason", "cols": "topic", "measures": ["asks"]}


def test_only_failed_outcomes_are_counted_by_reason_and_topic(db):
    _seed(db)

    summary = _run(db).layouts.summary

    labels = summary.row_value_labels or {}
    by_reason = {labels.get(v, v): _dec(t["asks"]) for v, t in summary.row_totals.items()}
    assert by_reason == {"Technical failure": 2, "Cannot do": 1, "Missed": 1}
    assert _dec(summary.grand_total["asks"]) == 4
    col_labels = summary.col_dim.value_labels or {}
    printed_cols = {col_labels.get(v, v) for v in summary.col_dim.values}
    assert printed_cols == {"Availability", "(not labelled yet)"}


def test_choosing_other_outcomes_brings_them_back(db):
    _seed(db)

    summary = _run(db, {"outcome": ["answered", "clarified"]}).layouts.summary

    assert _dec(summary.grand_total["asks"]) == 2


def test_the_catalog_lists_the_report_for_a_holder(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.models.user import User
    from app.services.user_service import UserPermissionService

    me = {"id": _uid(), "email": "bot-failures@zzt.test", "name": "Failures Caller"}
    db.add(User(id=me["id"], email=me["email"], name=me["name"], status="ACTIVE"))
    db.flush()
    allow = {PERMISSION}
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_current_user] = lambda: me
    app.dependency_overrides[get_current_user_or_api_key] = lambda: me
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow)
    try:
        client = TestClient(app)
        listed = client.get("/api/v1/reports").json()["reports"]
        assert next((r for r in listed if r["key"] == KEY), None) == {
            "key": KEY, "title": "Bot failures", "permission": PERMISSION,
        }
        allow.clear()
        assert client.get(f"/api/v1/reports/{KEY}").status_code == 403
    finally:
        for dep in (get_db, get_current_user, get_current_user_or_api_key):
            app.dependency_overrides.pop(dep, None)
