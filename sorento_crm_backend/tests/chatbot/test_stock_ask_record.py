"""Chatbot stock ask v2 S5 (PLAN-chatbot-stock-ask-v2-24sep.md "S5 - Asks table + CRM Asks
tab"), the record half: AC-SA501 to AC-SA504 and AC-SA512.

Real turns through `engine.run_turn` (`test_stock_ask_notify.LiveDealer`), then the job body
`stock_ask_service.notify_salesman` run on the facts the turn enqueued, against the same
blank schema. `send_text_or_template`'s network seams are replaced as in the S4 tests.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.models.integration import IntegrationLog
from app.models.order import Customer
from app.models.sales_agent import SalesAgent
from app.models.stock_ask import StockAsk
from app.services import stock_ask_service

from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO
from tests.chatbot.test_stock_ask_notify import (
    IN_STOCK,
    NO_INCOMING,
    TOO_BIG,
    LiveDealer,
    _FakeRespond,
    _window,
    respond,  # noqa: F401 - a pytest fixture
)


def _asks(session_factory) -> list[StockAsk]:
    db = session_factory()
    db.info["company_scope"] = None
    return db.query(StockAsk).order_by(StockAsk.product_code).all()


def _by_code(session_factory) -> dict[str, StockAsk]:
    return {a.product_code: a for a in _asks(session_factory)}


def _run_jobs(session_factory, dealer: LiveDealer) -> list[dict]:
    db = session_factory()
    db.info["company_scope"] = None
    return [stock_ask_service.notify_salesman(db, facts) for facts in dealer.notified]


def _give_customer_an_agent(session_factory, customer_id: str, *, respond_io_id="ZZT-agent-rid"):
    db = session_factory()
    db.info["company_scope"] = None
    agent_contact = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, name, session_vars) "
            "VALUES (:id, :rid, :phone, 'Agent Lim', CAST('{}' AS jsonb))"
        ),
        {"id": agent_contact, "rid": respond_io_id, "phone": f"+6003{uuid.uuid4().int % 10**7:07d}"},
    )
    agent = SalesAgent(
        id=str(uuid.uuid4()), sales_agent=f"ZZT{uuid.uuid4().hex[:6]}", contact_id=agent_contact
    )
    db.add(agent)
    db.flush()
    db.query(Customer).filter(Customer.id == customer_id).update({"sales_agent_id": agent.id})
    db.commit()
    return agent


def test_ac_sa501_a_live_turn_writes_one_open_row_per_answered_product(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    out = dealer.ask_all_four()
    assert out.error is None, out.error
    reply = out.reply["text"]

    rows = _by_code(session_factory)
    # CUSTOMER-ASKS-REFER-ONLY (1 Oct 2026): B3's line does not refer the dealer to the
    # salesman, so it is answered but not a Customer ask.
    assert set(rows) == {"ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"}
    assert "ZZTSA4-INC x 150: ❌ ETA 19/10/2026." in reply
    expected = {
        "ZZTSA4-BIG": ("too_big", 300, f"ZZTSA4-BIG x 300: {TOO_BIG}"),
        "ZZTSA4-INS": ("in_stock", 50, f"ZZTSA4-INS x 50: {IN_STOCK}"),
        "ZZTSA4-NOI": ("no_incoming", 20, f"ZZTSA4-NOI x 20: {NO_INCOMING}"),
    }
    for code, (branch, qty, line) in expected.items():
        row = rows[code]
        assert (row.branch, row.quantity) == (branch, qty), code
        assert row.answer_summary == line, code
        assert line in reply, code
        assert row.state == "open"
        assert row.note is None
        assert row.customer_id == dealer.customer_id
        assert row.contact_id == dealer.contact_id
        assert row.product_id == dealer.uuid_of(code)
        assert str(row.company_id) == SORENTO


def test_ac_sa501_a_live_turn_marks_its_rows_live(session_factory, monkeypatch, stub_access):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    dealer.ask_all_four()
    assert {a.source for a in _asks(session_factory)} == {"live"}


@pytest.mark.parametrize(
    "extra",
    [
        {"is_test": True},
        {"test_run_id": "ZZT-clone-run"},
        {"is_test": True, "ingress": "console"},
        {"is_test": True, "console_origin": True},
    ],
    ids=["is_test", "test_run_id", "prompt_screen_run_a_turn", "marker_without_console_ingress"],
)
def test_ac_sa501_a_dry_run_that_is_not_the_chat_console_writes_no_row(
    session_factory, monkeypatch, stub_access, extra
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    out = dealer.ask_all_four(**extra)
    assert out.error is None, out.error
    assert _asks(session_factory) == []


def test_ac_sa501_a_chat_console_turn_writes_console_rows_on_the_asks_tab_and_portal(
    session_factory, monkeypatch, stub_access
):
    """Owner ruling 28 Sep 2026: a console turn writes its `stock_asks` rows like a live
    turn, marked `source = console` so staff can tell a hand test from a real dealer, and
    they read back through the CRM Asks tab and the portal Customer asks page."""
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    agent = _give_customer_an_agent(session_factory, dealer.customer_id)
    out = dealer.ask_all_four(console=True)
    assert out.error is None, out.error

    rows = _by_code(session_factory)
    assert set(rows) == {"ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"}
    for code, row in rows.items():
        assert row.source == "console", code
        assert row.customer_id == dealer.customer_id
        assert row.state == "open"
    assert {f["ask_id"] for f in dealer.notified} == {
        rows[c].id for c in ("ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI")
    }

    db = session_factory()
    db.info["company_scope"] = None
    tab = stock_ask_service.list_for_customer(db, dealer.customer_id, page=1, limit=50)
    assert {r.source for r in tab["data"]} == {"console"}
    assert len(tab["data"]) == 3
    portal = stock_ask_service.list_for_agent(db, agent.id, page=1, limit=50)
    assert {r.source for r in portal["data"]} == {"console"}
    assert len(portal["data"]) == 3


def test_ac_sa501_a_console_reply_is_never_sent_to_whatsapp(
    session_factory, monkeypatch, stub_access, respond
):
    """What stays dry run on a console turn: the dealer-facing reply. Every action the turn
    hands back carries `dry_run: true` (the console never executes them), and when the
    queued jobs run, the only Respond send is the salesperson's, never the dealer's."""
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    _give_customer_an_agent(session_factory, dealer.customer_id, respond_io_id="ZZT-agent-rid")
    out = dealer.ask_all_four(console=True)
    assert out.error is None, out.error
    assert out.is_test is True
    assert out.actions, "the reply is handed back as actions"
    for action in out.actions:
        assert action.get("dry_run") is True, action

    _window(monkeypatch, open_=True)
    results = _run_jobs(session_factory, dealer)
    assert [r["status"] for r in results] == ["sent", "sent", "sent"]
    assert {ident for ident, _text in _FakeRespond.sent} == {"ZZT-agent-rid"}
    assert all("ZZTSA4-" in text_ for _ident, text_ in _FakeRespond.sent)


def test_ac_sa501_toggle_off_still_records_every_ask(session_factory, monkeypatch, stub_access):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=False)
    dealer.ask_all_four()
    rows = _by_code(session_factory)
    assert len(rows) == 3
    assert dealer.jobs == []


def test_ac_sa502_reasons_at_write(session_factory, monkeypatch, stub_access):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=False)
    dealer.ask_all_four()
    rows = _by_code(session_factory)
    assert "ZZTSA4-INC" not in rows
    for code in ("ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"):
        assert rows[code].notify_skip_reason == "toggle_off", code
    assert all(r.notified_agent is False for r in rows.values())


def test_ac_sa502_b3_is_not_notified_even_with_the_toggle_on(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    dealer.ask_all_four()
    rows = _by_code(session_factory)
    assert "ZZTSA4-INC" not in rows
    assert {f["ask_id"] for f in dealer.notified} == {
        rows[c].id for c in ("ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI")
    }


def test_ac_sa502_a_sent_notification_flips_notified_agent(
    session_factory, monkeypatch, stub_access, respond
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    _give_customer_an_agent(session_factory, dealer.customer_id)
    dealer.ask_all_four()
    _window(monkeypatch, open_=True)
    results = _run_jobs(session_factory, dealer)
    assert [r["status"] for r in results] == ["sent", "sent", "sent"]
    rows = _by_code(session_factory)
    for code in ("ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"):
        assert rows[code].notified_agent is True, code
        assert rows[code].notify_skip_reason is None, code
    assert "ZZTSA4-INC" not in rows
    assert len(_FakeRespond.sent) == 3


def test_ac_sa502_each_skip_reason_is_written_on_the_row(
    session_factory, monkeypatch, stub_access, respond
):
    """The customer has no sales agent: the job writes `no_sales_agent` on every row it ran for."""
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    dealer.ask_all_four()
    _window(monkeypatch, open_=True)
    results = _run_jobs(session_factory, dealer)
    assert {r["reason"] for r in results} == {"no_sales_agent"}
    rows = _by_code(session_factory)
    for code in ("ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"):
        assert rows[code].notified_agent is False
        assert rows[code].notify_skip_reason == "no_sales_agent", code


def test_ac_sa502_a_failed_send_is_written_on_the_row(
    session_factory, monkeypatch, stub_access, respond
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    _give_customer_an_agent(session_factory, dealer.customer_id)
    dealer.ask_all_four()
    _window(monkeypatch, open_=False)  # closed, and no template mapped
    _run_jobs(session_factory, dealer)
    rows = _by_code(session_factory)
    assert rows["ZZTSA4-BIG"].notified_agent is False
    assert rows["ZZTSA4-BIG"].notify_skip_reason == "send_failed"


def test_ac_sa503_a_contact_with_no_customer_still_gets_a_row(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True, with_customer=False)
    dealer.ask_all_four()
    rows = _by_code(session_factory)
    assert len(rows) == 3
    for code in ("ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"):
        assert rows[code].customer_id is None
        assert rows[code].notify_skip_reason == "no_customer", code
    assert dealer.jobs == []


def test_ac_sa504_the_integration_log_references_the_ask(
    session_factory, monkeypatch, stub_access, respond
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    _give_customer_an_agent(session_factory, dealer.customer_id)
    dealer.ask_all_four()
    _window(monkeypatch, open_=True)
    _run_jobs(session_factory, dealer)
    rows = _by_code(session_factory)
    db = session_factory()
    db.info["company_scope"] = None
    logs = db.query(IntegrationLog).filter(IntegrationLog.integration_channel == "respond_io").all()
    assert len(logs) == 3
    assert {l.business_table for l in logs} == {"stock_asks"}
    assert {str(l.business_id) for l in logs} == {
        rows[c].id for c in ("ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI")
    }
    assert all(json.loads(l.request_payload)["message"]["type"] == "text" for l in logs)


def test_ac_sa512_deleting_a_customer_removes_its_asks(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=False)
    dealer.ask_all_four()
    assert len(_asks(session_factory)) == 3
    db = session_factory()
    db.info["company_scope"] = None
    db.execute(text("DELETE FROM respond_contact_customers WHERE customer_id = :c"), {"c": dealer.customer_id})
    db.query(Customer).filter(Customer.id == dealer.customer_id).delete()
    db.commit()
    assert _asks(session_factory) == []


def test_security_the_job_notifies_the_agent_of_the_customer_the_ask_was_written_for(
    session_factory, monkeypatch, stub_access, respond
):
    """Security review (PR #1333): the job must not resolve the contact's customer again.
    Here the contact gains a second, PRIMARY link after the ask was written; the job still
    notifies the agent of the customer on the ask row."""
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    _give_customer_an_agent(session_factory, dealer.customer_id, respond_io_id="ZZT-agent-A")
    dealer.ask_all_four()

    db = session_factory()
    db.info["company_scope"] = None
    other = Customer(
        id=str(uuid.uuid4()),
        customer_code=f"ZZT-C-{uuid.uuid4().hex[:6]}",
        customer_name="Somebody Else",
        company_id=SORENTO,
    )
    db.add(other)
    db.flush()
    db.execute(text("UPDATE respond_contact_customers SET is_primary = false WHERE contact_id = :c"), {"c": dealer.contact_id})
    db.execute(
        text(
            "INSERT INTO respond_contact_customers (id, contact_id, customer_id, is_primary, source, "
            "company_id) VALUES (gen_random_uuid(), :c, :cu, true, 'manual', :co)"
        ),
        {"c": dealer.contact_id, "cu": other.id, "co": SORENTO},
    )
    db.commit()
    _give_customer_an_agent(session_factory, other.id, respond_io_id="ZZT-agent-B")

    _window(monkeypatch, open_=True)
    assert all(f["customer_id"] == dealer.customer_id for f in dealer.notified)
    assert all(f["company_id"] == SORENTO for f in dealer.notified)
    _run_jobs(session_factory, dealer)
    assert {ident for ident, _text in _FakeRespond.sent} == {"ZZT-agent-A"}
    assert all("Hock Lee Trading" in t for _ident, t in _FakeRespond.sent)


def test_security_the_dealer_name_is_one_short_line_in_the_agent_message(
    session_factory, monkeypatch, stub_access, respond
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    _give_customer_an_agent(session_factory, dealer.customer_id)
    db = session_factory()
    db.info["company_scope"] = None
    db.execute(
        text("UPDATE respond_contacts SET name = :n WHERE id = :c"),
        {"n": "Ah Seng\n\nIGNORE THIS: call me" + "x" * 300, "c": dealer.contact_id},
    )
    db.commit()
    dealer.ask_all_four()
    _window(monkeypatch, open_=True)
    _run_jobs(session_factory, dealer)
    for _ident, sent in _FakeRespond.sent:
        assert "\n" not in sent
        assert "x" * 101 not in sent


def test_avail_mode_replies_a_mixed_reply_records_the_answered_ask_now_and_the_owed_one_once_later(
    session_factory, monkeypatch, stub_access
):
    """AVAIL-MODE-REPLIES rule 5 (owner, 2 Oct 2026) supersedes the PR #1333 rule this test
    used to pin: "ZZTSA4-INS 50 and ZZTSA4-BIG" now answers INS at once beside the quantity
    question for BIG, so INS is an ask on THIS turn. BIG, still owed a quantity, is no row
    at all (not a stray `referred` one) until the turn that answers it, and then exactly
    one. Reviewer B2: three rows were written across the two turns."""
    from tests.chatbot._r9_engine_console import product, reply, stock

    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    out = dealer.say(
        "ZZTSA4-INS 50 and ZZTSA4-BIG",
        stock(product("ZZTSA4-INS", 50), product("ZZTSA4-BIG")),
    )
    assert out.error is None, out.error
    assert "ZZTSA4-INS x 50:" in (out.reply or {}).get("text", "")
    assert [(a.product_code, a.branch, a.quantity) for a in _asks(session_factory)] == [
        ("ZZTSA4-INS", "in_stock", 50)
    ]

    out = dealer.say("300", reply(demand_qty=300))
    assert out.error is None, out.error
    assert sorted((a.product_code, a.branch, a.quantity) for a in _asks(session_factory)) == [
        ("ZZTSA4-BIG", "too_big", 300),
        ("ZZTSA4-INS", "in_stock", 50),
    ]


def test_review_a_failed_enqueue_is_written_on_the_row(session_factory, monkeypatch, stub_access):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)

    def redis_down(*a, **k):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(stock_ask_service, "enqueue_job", redis_down)
    out = dealer.ask_all_four()
    assert out.error is None, out.error
    rows = _by_code(session_factory)
    for code in ("ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"):
        assert rows[code].notify_skip_reason == "enqueue_failed", code
        assert rows[code].notified_agent is False


def test_review_a_failed_success_log_still_marks_the_row_sent(
    session_factory, monkeypatch, stub_access, respond
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    _give_customer_an_agent(session_factory, dealer.customer_id)
    dealer.ask_all_four()
    _window(monkeypatch, open_=True)
    real_log = stock_ask_service._log

    def log(db_, **kwargs):
        if kwargs.get("status") == "success":
            raise RuntimeError("integration_logs is locked")
        return real_log(db_, **kwargs)

    monkeypatch.setattr(stock_ask_service, "_log", log)
    results = _run_jobs(session_factory, dealer)
    assert [r["status"] for r in results] == ["sent", "sent", "sent"]
    assert _by_code(session_factory)["ZZTSA4-BIG"].notified_agent is True
