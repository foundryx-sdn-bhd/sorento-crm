"""CA-2 review round 1: a split ask (the labeller wrote ask_seq 1 and 2 for one turn) counts
once on the turn measures. Chat overview and Bot failures count `ask_seq = 1`; Top asks counts
every ask (one per topic). Plan: PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Review round 1".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tests._pg_fixture import blank_session

PERIOD = {"kind": "custom", "from": "2026-09-01", "to": "2026-09-30"}


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.base import set_company_scope

        set_company_scope(session, None)
        yield session


def _seed_split_failed_turn(db):
    from app.models.chat_facts import ChatAskFact, ChatTopic

    db.add(ChatTopic(code="inventory", label="Stock", definition="Stock", version=1))
    db.add(ChatTopic(code="order", label="Orders", definition="Orders", version=1))
    db.flush()
    for code, parent, label in (
        ("inventory.availability", "inventory", "Availability"),
        ("order.status", "order", "Order status"),
    ):
        db.add(ChatTopic(code=code, parent_code=parent, label=label, definition=label, version=2))
    db.flush()
    turn = _uid()
    contact = f"ZZT-split-{_uid()[:8]}"
    for seq, domain, topic in ((1, "inventory", "inventory.availability"), (2, "order", "order.status")):
        db.add(ChatAskFact(
            id=_uid(), source="trace", turn_id=turn, ask_seq=seq, contact_respond_id=contact,
            audience="dealer", contact_type="dealer", asked_at=datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc),
            channel="whatsapp", outcome="technical", failure_reason="technical", is_media=False,
            phantom_dropped=False, domain=domain, topic_code=topic, taxonomy_version=2, tools=[],
        ))
    db.flush()


def _run(db, key, params=None, view=None):
    import app.services.reports  # noqa: F401
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine
    from app.services.reports import registry as reg

    merged = {"period": PERIOD}
    merged.update(params or {})
    return engine.run(db, reg.get(key), merged, ReportViewConfig.model_validate(view) if view else None)


def _dec(v) -> Decimal:
    return Decimal(str(v or 0))


def test_chat_overview_counts_a_split_turn_once(db):
    _seed_split_failed_turn(db)

    summary = _run(db, "chat_overview").layouts.summary

    assert _dec(summary.grand_total["asks"]) == 1
    assert _dec(summary.grand_total["failed"]) == 1


def test_bot_failures_counts_a_split_turn_once(db):
    _seed_split_failed_turn(db)

    summary = _run(db, "chat_bot_failures").layouts.summary

    assert _dec(summary.grand_total["asks"]) == 1


def test_top_asks_still_counts_every_ask(db):
    _seed_split_failed_turn(db)

    summary = _run(db, "chat_top_asks", {"outcome": ["technical"]}).layouts.summary

    assert _dec(summary.grand_total["asks"]) == 2
    labels = summary.row_value_labels or {}
    assert {labels.get(v, v) for v in summary.row_values} == {"Availability", "Order status"}
