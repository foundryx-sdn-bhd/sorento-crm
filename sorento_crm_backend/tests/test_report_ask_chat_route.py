"""CA-3 RED tests: `GET /api/v1/order-management/report-ask?dataset=chat` (UAC-6 to 22).

Plan contract: documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md. The world
is `tests/_chat_report_seed.py` (7 counted asks, 5 turns, 2 failed turns, 3 contacts; cust_a owns
F1 and F2, cust_b owns F3, F3b and F4, the rest have no customer). Postgres only.

Pinned where the plan is silent (the coder follows, or asks the captain to change the test):
* error codes reuse the sales route's: `unknown_param`, `unknown_group_by`, `unknown_measure`;
* the dealer's refusal is `report_dimension_not_allowed` with the sales route's message;
* with no asks the body is a normal 200 with no rows and `message` "No asks in that period. Chat
  data starts 5 Sep 2026." (the `status` word is not pinned);
* `response_model` drops undeclared fields: every new field is asserted here.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests._chat_report_seed import NOT_LABELLED, add_unlabelled, seed_world
from tests._sales_report_do_seed import (  # noqa: F401  (client / db are fixtures)
    _as_contact,
    _contact,
    _link,
    client,
    db,
    seed_agent,
)

D = Decimal
ROUTE = "/api/v1/order-management/report-ask"
KEY = {"X-API-Key": "k"}
PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
GRANT = "chatbot.analytics"
DEALER_MESSAGE = "That breakdown is not available for your account."
NO_ASKS = "No asks in that period. Chat data starts 5 Sep 2026."


def _staff(db, keys=(GRANT,)):
    """A contact with no customer link holding exactly `keys` as reveal grants."""
    from app.services.contact_field_reveal_service import set_granted_keys

    contact = _contact(db, granted=False)
    set_granted_keys(db, contact.id, list(keys), actor_id=None)
    db.commit()
    return contact


def _ask(client, contact, headers=KEY, **params):
    q = {"dataset": "chat", **PERIOD, **_as_contact(contact), **params}
    return client.get(ROUTE, params={k: v for k, v in q.items() if v is not None}, headers=headers)


def _ok(client, contact, **params):
    resp = _ask(client, contact, **params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _code(resp):
    return resp.json().get("code")


def _rows(body):
    return {r["name"]: D(str(r["value"])) for r in body["rows"]}


# ======================================================================== grant


def test_the_grant_key_is_in_the_field_reveal_checklist():
    from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS

    assert (GRANT, "Chat analytics") in FIELD_REVEAL_KEYS


def test_no_grant_is_403_chat_analytics_not_enabled(client, db):
    seed_world(db)
    resp = _ask(client, _staff(db, keys=()), group_by="topic", top_n=5)
    assert resp.status_code == 403 and _code(resp) == "chat_analytics_not_enabled", resp.text


def test_the_sales_report_reveal_alone_is_not_enough(client, db):
    seed_world(db)
    resp = _ask(client, _staff(db, keys=("sales_orders.sales_report",)), group_by="topic", top_n=5)
    assert resp.status_code == 403 and _code(resp) == "chat_analytics_not_enabled", resp.text


def test_the_chat_grant_does_not_open_the_sales_dataset(client, db):
    resp = client.get(ROUTE, params={**PERIOD, **_as_contact(_staff(db)), "group_by": "product", "top_n": 3}, headers=KEY)
    assert resp.status_code == 403 and _code(resp) == "sales_report_not_enabled", resp.text


def test_an_unknown_contact_is_refused(client, db):
    resp = client.get(ROUTE, params={"dataset": "chat", **PERIOD, "contact_id": str(uuid.uuid4()),
                                     "space_id": "zzt-space", "group_by": "topic", "top_n": 3}, headers=KEY)
    assert resp.status_code == 403, resp.text


# ======================================================================== 422


def test_a_sales_only_param_with_the_chat_dataset_is_422_unknown_param(client, db):
    seed_world(db)
    resp = _ask(client, _staff(db), group_by="topic", top_n=5, sales_agent_ids=[str(uuid.uuid4())])
    assert resp.status_code == 422 and _code(resp) == "unknown_param", resp.text
    assert resp.json()["detail"] == "sales_agent_ids", "the dataset is known; the sales param is not"


def test_a_chat_only_param_without_the_dataset_is_422_unknown_param(client, db):
    resp = client.get(ROUTE, params={**PERIOD, **_as_contact(_staff(db)), "subjects": ["inventory"]}, headers=KEY)
    assert resp.status_code == 422 and _code(resp) == "unknown_param", resp.text
    assert resp.json()["detail"] == "subjects"


def test_an_unknown_dataset_is_422(client, db):
    resp = _ask(client, _staff(db), dataset="inventory", group_by="topic", top_n=5)
    assert resp.status_code == 422, resp.text
    assert resp.json().get("detail") != "dataset", "'dataset' is a declared param: the VALUE is what is refused"


@pytest.mark.parametrize("group_by", ["brand", "sales_agent", "category", "location", "channel", "nonsense"])
def test_a_sales_or_unknown_group_by_is_422(client, db, group_by):
    seed_world(db)
    resp = _ask(client, _staff(db), group_by=group_by, top_n=5)
    assert resp.status_code == 422 and _code(resp) == "unknown_group_by", resp.text


@pytest.mark.parametrize("measure", ["amount", "qty", "nonsense"])
def test_a_sales_or_unknown_measure_is_422(client, db, measure):
    seed_world(db)
    resp = _ask(client, _staff(db), measure=measure, group_by="topic", top_n=5)
    assert resp.status_code == 422 and _code(resp) == "unknown_measure", resp.text


def test_an_unknown_basis_is_422(client, db):
    seed_world(db)
    resp = _ask(client, _staff(db), basis="delivered", group_by="topic", top_n=5)
    assert resp.status_code == 422 and _code(resp) == "unknown_basis", resp.text


# ======================================================================== response shape


def test_ranking_by_topic_body(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), basis="asks", measure="asks", group_by="topic", top_n=2)
    assert body["status"] == "ok" and body["dataset"] == "chat", body
    assert body["basis"] == "asks" and body["measure"] == "asks" and body["unit"] == "asks", body
    assert body["group_by"] == "topic" and body["group_label"], body
    assert body["date_from"] == "2026-09-05", "a period opening before the traces is clipped to 5 Sep"
    assert body["date_to"] == "2026-09-30", body
    assert [(r["rank"], r["name"]) for r in body["rows"]][0] == (1, "Availability"), body["rows"]
    assert D(str(body["rows"][0]["value"])) == 4 and len(body["rows"]) == 2, body["rows"]
    assert body["more"] == 2 and body["total_count"] == 4, body
    assert D(str(body["total"]["value"])) == 7, body["total"]
    assert body["rows"][0].get("numerator") is None and body["total"].get("numerator") is None
    assert body["coverage"]["percent"] == 86 and body["coverage"]["taxonomy_version"] == 2, body["coverage"]
    assert body["coverage"]["line"] == (
        "Topics labelled: 86% of asks (taxonomy v2). From bot traces, 5 Sep 2026 on."
    ), body["coverage"]


def test_unlabelled_asks_rank_as_not_labelled_yet(client, db):
    seed_world(db)
    assert _rows(_ok(client, _staff(db), group_by="topic", top_n=10))[NOT_LABELLED] == 1


def test_by_product_names_are_the_product_codes(client, db):
    w = seed_world(db)
    body = _ok(client, _staff(db), group_by="product", top_n=10)
    assert _rows(body) == {w.p1.product_code: D(2), w.p2.product_code: D(1)}, body["rows"]


def test_failure_rate_rows_carry_numerator_and_denominator(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), basis="turns", measure="failure_rate", group_by="week")
    assert body["unit"] == "%" and body["basis"] == "turns" and body["measure"] == "failure_rate", body
    rows = {r["name"]: r for r in body["rows"]}
    assert set(rows) == {"2026-09-07", "2026-09-14"}, rows
    assert (rows["2026-09-07"]["numerator"], rows["2026-09-07"]["denominator"]) == (1, 3), rows
    assert float(rows["2026-09-07"]["value"]) == pytest.approx(33.3, abs=0.05), rows
    total = body["total"]
    assert (total["numerator"], total["denominator"]) == (2, 5), total
    assert float(total["value"]) == pytest.approx(40.0, abs=0.05), total


def test_trend_by_week_needs_no_top_n(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), group_by="week")
    assert _rows(body) == {"2026-09-07": D(4), "2026-09-14": D(2), "2026-09-21": D(1)}, body["rows"]
    assert body["more"] == 0, body


def test_contacts_total_is_distinct_not_the_sum_of_rows(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), measure="contacts", group_by="topic", top_n=10)
    assert body["unit"] == "contacts", body
    assert D(str(body["total"]["value"])) == 3, body["total"]
    assert _rows(body)["Availability"] == 2, body["rows"]


def test_failed_turns_unit_is_turns(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), basis="turns", measure="failed", group_by="topic", top_n=10)
    assert body["unit"] == "turns" and D(str(body["total"]["value"])) == 2, body


def test_a_total_has_no_rows(client, db):
    seed_world(db)
    body = _ok(client, _staff(db))
    assert body["rows"] == [] and body["group_by"] is None and D(str(body["total"]["value"])) == 7, body


def test_coverage_line_percent_under_80_is_reported_for_the_presenter(client, db):
    seed_world(db)
    add_unlabelled(db, 3)
    db.commit()
    cov = _ok(client, _staff(db), group_by="topic", top_n=10)["coverage"]
    assert cov["percent"] == 60 and cov["line"].startswith("Topics labelled: 60% of asks"), cov


def test_no_asks_in_the_period(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), group_by="topic", top_n=5, date_from="2026-11-01", date_to="2026-11-30")
    assert body["rows"] == [] and body["message"] == NO_ASKS, body


def test_the_period_before_the_traces_is_clipped(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), group_by="topic", top_n=5, date_from="2026-06-01", date_to="2026-09-30")
    assert body["date_from"] == "2026-09-05", body


def test_no_message_text_contact_or_phone_in_the_body(client, db):
    seed_world(db)
    resp = client.get(
        ROUTE, params={"dataset": "chat", **PERIOD, **_as_contact(_staff(db)), "group_by": "customer", "top_n": 10},
        headers=KEY,
    )
    assert resp.status_code == 200, resp.text
    raw = resp.text
    for forbidden in ("ZZT-c1", "contact_respond_id", "phone", "+6"):
        assert forbidden not in raw, forbidden


# ======================================================================== filters


def test_the_subject_filter_narrows_to_that_subject(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), group_by="topic", top_n=10, subjects=["inventory"])
    assert D(str(body["total"]["value"])) == 6 and "Order status" not in _rows(body), body


def test_the_customer_filter_narrows_and_is_echoed_by_name(client, db):
    w = seed_world(db)
    body = _ok(client, _staff(db), group_by="topic", top_n=10, customer_ids=[w.cust_b.id])
    assert D(str(body["total"]["value"])) == 3, body
    assert [f["values"] for f in body["filters"] if f["key"] == "customer"] == [[w.cust_b.customer_name]], body["filters"]


def test_the_product_filter_narrows_to_asks_that_named_it(client, db):
    w = seed_world(db)
    body = _ok(client, _staff(db), group_by="topic", top_n=10, product_ids=[w.p1.id])
    assert D(str(body["total"]["value"])) == 2, body


# ======================================================================== audiences


def _dealer(db, w, customer):
    from app.services.contact_field_reveal_service import set_granted_keys

    contact = _contact(db, granted=False)
    set_granted_keys(db, contact.id, [GRANT], actor_id=None)
    _link(db, contact, customer)
    db.commit()
    return contact


def test_a_dealer_sees_only_its_own_customers_asks(client, db):
    w = seed_world(db)
    body = _ok(client, _dealer(db, w, w.cust_a), group_by="topic", top_n=10)
    assert _rows(body) == {"Availability": D(2)} and D(str(body["total"]["value"])) == 2, body


def test_a_dealer_may_rank_by_topic_subject_product_and_month(client, db):
    w = seed_world(db)
    dealer = _dealer(db, w, w.cust_a)
    for group_by in ("topic", "subject", "product", "month"):
        body = _ok(client, dealer, group_by=group_by, top_n=10)
        assert body["group_by"] == group_by, body


@pytest.mark.parametrize("group_by", ["customer", "outcome", "failure_reason", "week", "day", "language"])
def test_a_dealer_may_not_rank_by_anything_else(client, db, group_by):
    w = seed_world(db)
    resp = _ask(client, _dealer(db, w, w.cust_a), group_by=group_by, top_n=10)
    assert resp.status_code == 403 and _code(resp) == "report_dimension_not_allowed", resp.text
    assert DEALER_MESSAGE in resp.text, resp.text


def test_a_dealer_naming_another_customer_is_refused(client, db):
    w = seed_world(db)
    resp = _ask(client, _dealer(db, w, w.cust_a), group_by="topic", top_n=10, customer_ids=[w.cust_b.id])
    assert resp.status_code == 403, resp.text


def test_a_salesman_sees_only_customers_whose_card_agent_is_theirs(client, db):
    from app.services.contact_field_reveal_service import set_granted_keys

    w = seed_world(db)
    contact = _contact(db, granted=False)
    set_granted_keys(db, contact.id, [GRANT], actor_id=None)
    agent = seed_agent(db, code="ZZT-CHAT-AG")
    agent.contact_id = contact.id
    w.cust_a.sales_agent_id = agent.id
    db.commit()
    body = _ok(client, contact, group_by="topic", top_n=10)
    assert _rows(body) == {"Availability": D(2)}, body["rows"]
    assert D(str(body["total"]["value"])) == 2, "asks with no customer are not theirs either"


def test_a_staff_contact_with_the_grant_and_no_agent_link_sees_everything(client, db):
    seed_world(db)
    body = _ok(client, _staff(db), group_by="topic", top_n=10)
    assert D(str(body["total"]["value"])) == 7, body


# ======================================================================== rate limit, sales unchanged


def test_a_rate_limited_chat_ask_answers_busy(client, db, monkeypatch):
    from app.services import rate_limit

    seed_world(db)
    monkeypatch.setattr(rate_limit, "hit", lambda *a, **k: SimpleNamespace(allowed=False))
    body = _ok(client, _staff(db), group_by="topic", top_n=3)
    assert body["status"] == "busy" and body["rows"] == [], body
    assert body["message"].startswith("Too many reports in the last "), body


def test_a_call_without_dataset_is_the_sales_report_unchanged(client, db):
    contact = _contact(db)  # holds sales_orders.sales_report
    db.commit()
    resp = client.get(ROUTE, params={**PERIOD, **_as_contact(contact), "group_by": "sales_agent", "top_n": 3}, headers=KEY)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["basis"] == "delivered" and body["basis_label"] == "delivered sales", body
    assert body.get("dataset") in (None, "sales") and not body.get("coverage"), body
    assert body["rows"] == [] and body["total"] == {"qty": 0, "amount": 0.0}, body
