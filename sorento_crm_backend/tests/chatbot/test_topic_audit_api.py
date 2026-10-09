"""CA-2 topic audit API (tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, build contract,
"Topic audit (new table, routes, page)"; UAC U17-U23.

Routes in `app/api/v1/system/chatbot.py`, all needing BOTH `system.chat_history.view` and
`chatbot.analytics.view` (403 otherwise, each slug alone is 403):
  GET   /system/chatbot/topic-audit?version=&show=to_review|reviewed|all&subject=
  POST  /system/chatbot/topic-audit/sample {version} -> 201 {drawn}; 409 when drawn already
  PATCH /system/chatbot/topic-audit/{id} {verdict, corrected_topic?, products_verdict?}

Until the routes are mounted every call answers 404 (or 405), which is the right red.
Postgres only; every test seeds its own taxonomy, facts, turns and user. Fake text only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  registers every model
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.services.user_service import UserPermissionService

from tests.chatbot._chat_facts_helpers import (
    answered_trace,
    envelope_with_text,
    seed_catalogue,
    seed_contact,
    seed_turn,
)

BASE = "/api/v1/system/chatbot/topic-audit"
VIEW = "system.chat_history.view"
ANALYTICS = "chatbot.analytics.view"
UTC = timezone.utc
VERSION = 2

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Auditor", "email": "zzt-auditor@zzt.test"}


# --------------------------------------------------------------------------- fixtures


@pytest.fixture()
def db(session_factory):
    return session_factory()


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.update({VIEW, ANALYTICS})
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in _GRANTS)
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def client(db, session_factory):
    from app.models.user import User

    _ACTOR["id"] = str(uuid.uuid4())
    seeder = session_factory()
    seeder.add(User(id=_ACTOR["id"], email=_ACTOR["email"], name=_ACTOR["name"], status="ACTIVE"))
    seeder.commit()

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- seeding


SUBJECTS = (("inventory", "Stock"), ("order", "Orders"), ("general", "General chat"))
LEAVES = (
    ("inventory.availability", "inventory", "Availability"),
    ("inventory.other", "inventory", "Other stock"),
    ("order.status", "order", "Order status"),
    ("order.other", "order", "Other orders"),
    ("general.noise", "general", "Noise"),
)


@pytest.fixture()
def taxonomy(session_factory):
    from app.models.chat_facts import ChatTopic

    db = session_factory()
    for order, (code, label) in enumerate(SUBJECTS):
        db.add(ChatTopic(code=code, label=label, definition=label, version=1, sort_order=order))
    db.flush()
    for order, (code, parent, label) in enumerate(LEAVES):
        db.add(ChatTopic(code=code, parent_code=parent, label=label, definition=label, version=VERSION, sort_order=order))
    db.commit()


def _fact(session_factory, *, subject: str = "inventory", leaf: str | None = None, version: int | None = VERSION,
          confidence: float = 0.9, minutes: int = 0, turn_id: str | None = None, labelled: bool = True) -> str:
    from app.models.chat_facts import ChatAskFact

    leaf = leaf or {"inventory": "inventory.availability", "order": "order.status", "general": "general.noise"}[subject]
    db = session_factory()
    fact_id = str(uuid.uuid4())
    db.add(
        ChatAskFact(
            id=fact_id, source="trace", turn_id=turn_id or str(uuid.uuid4()), ask_seq=1,
            contact_respond_id=f"ZZT-audit-{fact_id[:8]}", audience="dealer", contact_type="dealer",
            asked_at=datetime(2026, 9, 10, 3, 0, tzinfo=UTC) + timedelta(minutes=minutes), channel="whatsapp",
            domain=None if subject == "general" else subject, outcome="answered", tools=[],
            topic_code=leaf if labelled else None, taxonomy_version=version if labelled else None,
            label_confidence=confidence if labelled else None, language="en",
            is_media=False, phantom_dropped=False,
        )
    )
    db.commit()
    return fact_id


def _bulk(session_factory, subject: str, count: int, *, version: int | None = VERSION, base_conf: float = 0.5,
          leaf: str | None = None) -> list[str]:
    from app.models.chat_facts import ChatAskFact

    db = session_factory()
    leaf = leaf or {"inventory": "inventory.availability", "order": "order.status", "general": "general.noise"}[subject]
    ids = []
    for i in range(count):
        fact_id = str(uuid.uuid4())
        ids.append(fact_id)
        db.add(
            ChatAskFact(
                id=fact_id, source="trace", turn_id=str(uuid.uuid4()), ask_seq=1,
                contact_respond_id=f"ZZT-bulk-{fact_id[:8]}", audience="dealer", contact_type="dealer",
                asked_at=datetime(2026, 9, 10, 3, 0, tzinfo=UTC) + timedelta(minutes=i), channel="whatsapp",
                domain=None if subject == "general" else subject, outcome="answered", tools=[],
                topic_code=leaf, taxonomy_version=version, label_confidence=base_conf + (i % 40) / 100.0,
                language="en", is_media=False, phantom_dropped=False,
            )
        )
    db.commit()
    return ids


def _draw(client, version: int = VERSION):
    return client.post(f"{BASE}/sample", json={"version": version})


def _rows(client, **params) -> list[dict]:
    params.setdefault("show", "all")
    resp = client.get(BASE, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["rows"]


def _drawn_one(client, session_factory, **fact_kwargs) -> tuple[str, dict]:
    fact_id = _fact(session_factory, **fact_kwargs)
    assert _draw(client).status_code == 201
    row = next(r for r in _rows(client))
    return fact_id, row


# --------------------------------------------------------------------------- permissions


@pytest.mark.parametrize("granted", [set(), {VIEW}, {ANALYTICS}])
def test_every_route_is_403_without_both_slugs(client, taxonomy, granted):
    _GRANTS.clear()
    _GRANTS.update(granted)
    assert client.get(BASE).status_code == 403
    assert client.post(f"{BASE}/sample", json={"version": VERSION}).status_code == 403
    assert client.patch(f"{BASE}/{uuid.uuid4()}", json={"verdict": "right"}).status_code == 403


def test_both_slugs_open_the_screen(client, taxonomy):
    assert client.get(BASE).status_code == 200


# --------------------------------------------------------------------------- empty state


def test_a_version_with_no_sample_returns_the_empty_shape(client, taxonomy):
    body = client.get(BASE).json()

    assert body["version"] == VERSION, "defaults to the current taxonomy version"
    assert VERSION in body["versions"]
    assert body["sample_size"] == 0
    assert body["reviewed"] == 0
    assert body["rows"] == []
    assert body["rates"] == {"subject": None, "topic": None, "product": None}
    assert body["targets"] == {"subject": 0.9, "topic": 0.8, "product": 0.95}


def test_an_explicit_version_with_no_sample_is_also_empty(client, taxonomy, session_factory):
    _fact(session_factory)
    assert _draw(client).status_code == 201

    other = client.get(BASE, params={"version": 1}).json()

    assert other["version"] == 1 and other["sample_size"] == 0 and other["rows"] == []


# --------------------------------------------------------------------------- the draw


def test_the_draw_takes_300_with_at_least_10_per_subject_with_asks(client, taxonomy, session_factory):
    _bulk(session_factory, "inventory", 400)
    _bulk(session_factory, "order", 20)
    _bulk(session_factory, "general", 5)

    resp = _draw(client)

    assert resp.status_code == 201, resp.text
    assert resp.json() == {"drawn": 300}
    body = client.get(BASE, params={"show": "all"}).json()
    assert body["sample_size"] == 300 and len(body["rows"]) == 300
    per_subject = {}
    for row in body["rows"]:
        per_subject[row["subject"]] = per_subject.get(row["subject"], 0) + 1
    assert per_subject["general"] == 5, "a subject with fewer than 10 asks gives all of them"
    assert per_subject["order"] >= 10
    assert per_subject["inventory"] >= 10
    assert per_subject["inventory"] > per_subject["order"], "the rest follows volume"
    assert len({r["id"] for r in body["rows"]}) == 300


def test_the_draw_favours_the_lowest_confidence_for_half_the_rest(client, taxonomy, session_factory):
    low = _fact(session_factory, subject="inventory", confidence=0.01)
    _bulk(session_factory, "inventory", 450, base_conf=0.5)

    assert _draw(client).status_code == 201

    drawn_ids = {r["id"] for r in _rows(client)}
    confidences = sorted(r["confidence"] for r in _rows(client))
    assert len(drawn_ids) == 300
    assert min(confidences) == pytest.approx(0.01), "the lowest-confidence ask is always drawn"
    # 121 seeded asks sit below 0.60, so a lowest-first half reaches 0.62 at index 149; a random
    # draw of 300 from 451 would sit near the 0.70 median.
    assert confidences[149] < 0.65, "half the draw comes from the low end of the confidence range"
    assert low  # the seeded fact


def test_fewer_than_300_labelled_asks_draws_them_all(client, taxonomy, session_factory):
    _bulk(session_factory, "inventory", 4)
    _bulk(session_factory, "order", 12)
    _bulk(session_factory, "general", 20)

    assert _draw(client).json() == {"drawn": 36}
    assert client.get(BASE).json()["sample_size"] == 36


def test_only_labelled_asks_of_the_chosen_version_are_drawn(client, taxonomy, session_factory):
    mine = [_fact(session_factory, minutes=i) for i in range(3)]
    _fact(session_factory, version=1, minutes=10)  # labelled under an older version
    _fact(session_factory, labelled=False, minutes=11)  # not labelled at all
    _fact(session_factory, version=3, minutes=12)  # a newer version

    assert _draw(client).json() == {"drawn": 3}

    assert {r["id"] for r in _rows(client)} and len(_rows(client)) == 3
    assert mine


def test_a_second_draw_for_the_same_version_is_refused(client, taxonomy, session_factory):
    _fact(session_factory)
    assert _draw(client).status_code == 201

    again = _draw(client)

    assert again.status_code == 409, again.text
    assert client.get(BASE).json()["sample_size"] == 1


def test_another_version_can_have_its_own_sample(client, taxonomy, session_factory):
    _fact(session_factory, version=VERSION)
    _fact(session_factory, version=1)
    assert _draw(client, VERSION).status_code == 201

    assert _draw(client, 1).status_code == 201


def test_the_draw_needs_a_version(client, taxonomy):
    assert client.post(f"{BASE}/sample", json={}).status_code == 422


# --------------------------------------------------------------------------- the rows


def test_a_row_carries_the_contract_fields_with_labels_and_no_uuid_but_its_id(client, taxonomy, session_factory):
    fact_id, row = _drawn_one(client, session_factory, subject="inventory", confidence=0.62)

    assert set(row) >= {
        "id", "asked_at", "message", "subject", "subject_label", "topic", "topic_label", "confidence",
        "products", "verdict", "corrected_topic", "corrected_topic_label", "products_verdict",
        "reviewed_by_name",
    }
    assert row["id"] != fact_id, "the audit row has its own id"
    assert row["subject"] == "inventory" and row["subject_label"] == "Stock"
    assert row["topic"] == "inventory.availability" and row["topic_label"] == "Availability"
    assert row["confidence"] == pytest.approx(0.62, abs=1e-3)
    assert row["products"] == []
    assert row["verdict"] is None and row["corrected_topic"] is None and row["products_verdict"] is None
    assert row["reviewed_by_name"] is None


def test_the_message_is_read_from_the_turn_envelope_at_request_time(client, taxonomy, session_factory):
    from app.models.chatbot_turn import ChatbotTurn

    respond_id = "ZZT-audit-msg"
    seed_contact(session_factory, respond_id)
    turn_id = seed_turn(session_factory, respond_id, records=answered_trace())
    session = session_factory()
    session.query(ChatbotTurn).filter_by(id=turn_id).update(
        {"envelope": envelope_with_text("ZZT do you have the grey tile in stock")}
    )
    session.commit()
    _fact(session_factory, turn_id=turn_id)
    assert _draw(client).status_code == 201

    assert _rows(client)[0]["message"] == "ZZT do you have the grey tile in stock"


def test_no_audit_column_stores_the_message(client, taxonomy, session_factory):
    from sqlalchemy import inspect

    from tests import _pg_fixture

    _fact(session_factory)
    assert _draw(client).status_code == 201
    columns = {c["name"] for c in inspect(session_factory().connection()).get_columns(
        "chat_label_audits", schema=f"{_pg_fixture._BLANK['name']}_chatbot")}
    assert columns and not {c for c in columns if "message" in c or "text" in c}


def test_a_row_lists_the_product_codes_the_ask_named(client, taxonomy, session_factory):
    from app.models.chat_facts import ChatAskItem

    catalogue = seed_catalogue(session_factory, product_codes=("ZZTAUD-A",))
    fact_id = _fact(session_factory)
    db = session_factory()
    db.add(ChatAskItem(id=str(uuid.uuid4()), ask_id=fact_id, kind="product",
                       ref_id=catalogue["products"]["ZZTAUD-A"], role="named", resolved_by="parser"))
    db.commit()
    assert _draw(client).status_code == 201

    assert _rows(client)[0]["products"] == ["ZZTAUD-A"]


# --------------------------------------------------------------------------- verdicts


def _patch(client, row_id: str, **body):
    return client.patch(f"{BASE}/{row_id}", json=body)


def test_right_is_recorded_with_the_reviewer_and_returns_the_row(client, taxonomy, session_factory):
    _fact_id, row = _drawn_one(client, session_factory)

    resp = _patch(client, row["id"], verdict="right")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == row["id"] and body["verdict"] == "right"
    assert body["reviewed_by_name"] == "ZZT Auditor"
    assert body["corrected_topic"] is None


def test_wrong_topic_takes_a_leaf_under_the_asks_subject(client, taxonomy, session_factory):
    _fact_id, row = _drawn_one(client, session_factory, subject="inventory")

    resp = _patch(client, row["id"], verdict="wrong_topic", corrected_topic="inventory.other")

    assert resp.status_code == 200, resp.text
    assert resp.json()["verdict"] == "wrong_topic"
    assert resp.json()["corrected_topic"] == "inventory.other"
    assert resp.json()["corrected_topic_label"] == "Other stock"


def test_wrong_topic_outside_the_subject_or_without_a_leaf_is_422(client, taxonomy, session_factory):
    _fact_id, row = _drawn_one(client, session_factory, subject="inventory")

    assert _patch(client, row["id"], verdict="wrong_topic", corrected_topic="order.status").status_code == 422
    assert _patch(client, row["id"], verdict="wrong_topic").status_code == 422
    assert _patch(client, row["id"], verdict="wrong_topic", corrected_topic="zzt.nowhere").status_code == 422
    assert _rows(client)[0]["verdict"] is None, "a refused verdict changes nothing"


def test_wrong_subject_takes_any_leaf(client, taxonomy, session_factory):
    _fact_id, row = _drawn_one(client, session_factory, subject="inventory")

    resp = _patch(client, row["id"], verdict="wrong_subject", corrected_topic="order.status")

    assert resp.status_code == 200, resp.text
    assert resp.json()["verdict"] == "wrong_subject"
    assert resp.json()["corrected_topic_label"] == "Order status"
    assert _patch(client, row["id"], verdict="wrong_subject").status_code == 422


def test_an_unknown_verdict_or_products_verdict_is_422(client, taxonomy, session_factory):
    _fact_id, row = _drawn_one(client, session_factory)

    assert _patch(client, row["id"], verdict="maybe").status_code == 422
    assert _patch(client, row["id"], verdict="right", products_verdict="maybe").status_code == 422
    assert _patch(client, row["id"]).status_code == 422


def test_an_unknown_row_is_404(client, taxonomy, session_factory):
    _fact(session_factory)
    assert _draw(client).status_code == 201
    assert _patch(client, str(uuid.uuid4()), verdict="right").status_code == 404


def test_the_products_verdict_is_recorded_beside_the_verdict(client, taxonomy, session_factory):
    _fact_id, row = _drawn_one(client, session_factory)

    resp = _patch(client, row["id"], verdict="right", products_verdict="wrong")

    assert resp.status_code == 200, resp.text
    assert resp.json()["products_verdict"] == "wrong"
    assert _rows(client)[0]["products_verdict"] == "wrong"


def test_a_verdict_can_be_changed(client, taxonomy, session_factory):
    _fact_id, row = _drawn_one(client, session_factory)
    assert _patch(client, row["id"], verdict="right").status_code == 200

    resp = _patch(client, row["id"], verdict="wrong_topic", corrected_topic="inventory.other")

    assert resp.json()["verdict"] == "wrong_topic"
    assert resp.json()["corrected_topic"] == "inventory.other"


# --------------------------------------------------------------------------- rates and filters


def _judge(client, session_factory):
    """Four rows: right, right, wrong_topic, wrong_subject; two product verdicts (right, wrong)."""
    for i in range(4):
        _fact(session_factory, minutes=i)
    assert _draw(client).status_code == 201
    ids = [r["id"] for r in _rows(client)]
    assert _patch(client, ids[0], verdict="right", products_verdict="right").status_code == 200
    assert _patch(client, ids[1], verdict="right", products_verdict="wrong").status_code == 200
    assert _patch(client, ids[2], verdict="wrong_topic", corrected_topic="inventory.other").status_code == 200
    assert _patch(client, ids[3], verdict="wrong_subject", corrected_topic="order.status").status_code == 200
    return ids


def test_the_rates_follow_the_contract(client, taxonomy, session_factory):
    _judge(client, session_factory)

    body = client.get(BASE).json()

    assert body["sample_size"] == 4 and body["reviewed"] == 4
    assert body["rates"]["subject"] == pytest.approx(0.75), "(right + wrong_topic) / judged"
    assert body["rates"]["topic"] == pytest.approx(0.5), "right / judged"
    assert body["rates"]["product"] == pytest.approx(0.5), "right / rows with a products verdict"


def test_the_rates_are_null_while_nothing_is_judged(client, taxonomy, session_factory):
    _fact(session_factory)
    assert _draw(client).status_code == 201

    body = client.get(BASE).json()

    assert body["sample_size"] == 1 and body["reviewed"] == 0
    assert body["rates"] == {"subject": None, "topic": None, "product": None}


def test_the_product_rate_is_null_when_no_row_has_a_products_verdict(client, taxonomy, session_factory):
    _fact_id, row = _drawn_one(client, session_factory)
    assert _patch(client, row["id"], verdict="right").status_code == 200

    rates = client.get(BASE).json()["rates"]

    assert rates["topic"] == pytest.approx(1.0) and rates["product"] is None


def test_show_to_review_lists_only_rows_without_a_verdict(client, taxonomy, session_factory):
    for i in range(3):
        _fact(session_factory, minutes=i)
    assert _draw(client).status_code == 201
    first = _rows(client)[0]["id"]
    assert _patch(client, first, verdict="right").status_code == 200

    assert {r["id"] for r in _rows(client, show="to_review")} == {r["id"] for r in _rows(client) if r["id"] != first}
    assert [r["id"] for r in _rows(client, show="reviewed")] == [first]
    assert len(_rows(client, show="all")) == 3


def test_the_subject_filter_narrows_the_rows(client, taxonomy, session_factory):
    _bulk(session_factory, "inventory", 3)
    _bulk(session_factory, "order", 2)
    assert _draw(client).status_code == 201

    assert {r["subject"] for r in _rows(client, subject="order")} == {"order"}
    assert len(_rows(client, subject="order")) == 2
    assert len(_rows(client, subject="inventory")) == 3
    assert len(_rows(client)) == 5


def test_the_rates_cover_the_whole_sample_not_the_filtered_rows(client, taxonomy, session_factory):
    _judge(client, session_factory)

    body = client.get(BASE, params={"show": "to_review"}).json()

    assert body["rows"] == []
    assert body["sample_size"] == 4 and body["reviewed"] == 4
    assert body["rates"]["subject"] == pytest.approx(0.75)
