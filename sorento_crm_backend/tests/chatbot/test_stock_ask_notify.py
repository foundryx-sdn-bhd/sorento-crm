"""Chatbot stock ask v2 S4 (PLAN-chatbot-stock-ask-v2-24sep.md "S4 - Agent notification +
integration_log"), AC-SA401 to AC-SA409.

Two halves:

* The ENGINE half runs real turns through `engine.run_turn` (the S3 round 9 console
  harness, `_r9_engine_console.EngineConsole`), with only the parser and the stock tool's
  database read stubbed, and a LIVE envelope (`is_test` false). The queue is the seam:
  `stock_ask_service.enqueue_job` is replaced by a recorder, so the test sees exactly which
  jobs a turn enqueued and when.
* The SEND half calls the job body (`stock_ask_service.notify_salesman`) directly against a
  seeded customer -> sales agent -> Respond contact chain. `send_text_or_template` runs for
  real; only its two network seams (`get_window_state`, the Respond client /
  `send_template_for_use_case`) are replaced, so the 24-hour window choice is the real one.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text

from app.models.integration import IntegrationLog
from app.models.order import Customer
from app.models.respond_template import TEMPLATE_DEFAULT_USE_CASES
from app.models.sales_agent import SalesAgent
from app.services import respond_messaging_service as messaging
from app.services import stock_ask_service
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import turn_runtime
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.turn import tail as turn_tail
from app.services.respond_template_service import PARAM_VARIABLES

from tests.chatbot._r9_engine_console import (
    _present_response,
    _seed_products,
    product,
    stock,
)
from tests.chatbot.test_engine import CONTACT_ID, _envelope
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _link_contact_company
from tests.chatbot.test_rearch_s3_roster_from_resolver import _seed_contact

TOO_BIG = "🚫 the quantity is more than what I can confirm here. Please refer to your salesman."
IN_STOCK = "✅ Please refer to your salesman."
NO_INCOMING = "❌ No stock and no incoming. Please refer to your salesman."

# One product per branch, so a single four-product ask exercises all four.
#
# Every code carries a digit (fix round 4, merge fallout with #833): the code-first rule
# (`references._code_matched`, `resolve_gate._names_a_typed_code`) reads only a
# code-shaped token WITH a digit as a typed code. The old digitless "ZZTSA-BIG" read as a
# described word, so the four-code ask became a counted "which of these has stock" set
# ("Couldn't find a ZZTSA-BIG ... with stock") and never reached the stock tool, the
# notify jobs or the asks rows. `test_the_fixture_codes_are_typed_codes` pins it.
BRANCH_OF = {
    "ZZTSA4-BIG": "too_big",
    "ZZTSA4-INS": "in_stock",
    "ZZTSA4-INC": "incoming",
    "ZZTSA4-NOI": "no_incoming",
}


# --------------------------------------------------------------------------------------- #
# The engine half
# --------------------------------------------------------------------------------------- #


class LiveDealer:
    """A dealer on WhatsApp: live turns through `engine.run_turn`, the stock tool stubbed to
    answer each code with the branch `BRANCH_OF` names."""

    def __init__(
        self,
        session_factory,
        monkeypatch,
        stub_access,
        *,
        notify: bool,
        with_customer: bool = True,
        agent_allowed: bool | None = None,
    ) -> None:
        self.session_factory = session_factory
        self._next: dict[str, Any] | None = None
        self.jobs: list[tuple[Any, tuple, dict]] = []
        _seed_contact(session_factory, phone=f"+6001{uuid.uuid4().int % 10**7:07d}")
        _link_contact_company(session_factory, company_id=SORENTO)
        db = session_factory()
        db.info["company_scope"] = frozenset({SORENTO})
        db.execute(
            text("UPDATE respond_contacts SET notify_salesman = :n WHERE respond_io_id = :c"),
            {"n": notify, "c": str(CONTACT_ID)},
        )
        self.contact_id = db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        self.customer_id = None
        self.agent_contact_id = None
        if with_customer:
            self.customer_id = str(uuid.uuid4())
            sales_agent_id = None
            if agent_allowed is not None:
                # Fix round 2 (AC-SA411): a sales agent whose Respond contact carries the
                # allowed-to-send flag (`respond_contacts.outbound_enabled`).
                self.agent_contact_id = str(uuid.uuid4())
                self.agent_respond_id = f"ZZT-agent-{uuid.uuid4().hex[:8]}"
                db.execute(
                    text(
                        "INSERT INTO respond_contacts (id, respond_io_id, phone_number, name, "
                        "session_vars, outbound_enabled) VALUES (:id, :rid, :phone, "
                        "'Agent Lim', CAST('{}' AS jsonb), :ok)"
                    ),
                    {
                        "id": self.agent_contact_id,
                        "rid": self.agent_respond_id,
                        "phone": f"+6003{uuid.uuid4().int % 10**7:07d}",
                        "ok": agent_allowed,
                    },
                )
                sales_agent_id = str(uuid.uuid4())
                db.add(
                    SalesAgent(
                        id=sales_agent_id,
                        sales_agent=f"ZZT{uuid.uuid4().hex[:6]}",
                        contact_id=self.agent_contact_id,
                        company_id=SORENTO,
                    )
                )
                db.flush()
            db.add(
                Customer(
                    id=self.customer_id,
                    customer_code=f"ZZT-C-{uuid.uuid4().hex[:6]}",
                    customer_name="Hock Lee Trading",
                    company_id=SORENTO,
                    sales_agent_id=sales_agent_id,
                )
            )
            db.flush()
            db.execute(
                text(
                    "INSERT INTO respond_contact_customers (id, contact_id, customer_id, "
                    "is_primary, source, company_id) "
                    "VALUES (gen_random_uuid(), :c, :cu, true, 'manual', :co)"
                ),
                {"c": self.contact_id, "cu": self.customer_id, "co": SORENTO},
            )
        db.commit()
        self.codes = _seed_products(session_factory, list(BRANCH_OF))
        stub_access()
        monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: True)
        present = _present_response()

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub",
                prompt_version=1,
                provider="openai",
                model="gpt-test",
                api_key="sk-test",
            )

        def fake_parse(config, user_block):
            return self._next

        def fake_call_tool(client, name: str, args: dict[str, Any]) -> str:
            if name != "crm_inventory_stock_balance_list":
                return json.dumps({"answers": []})
            wanted = args.get("requested_quantities") or {}
            if isinstance(wanted, str):
                wanted = json.loads(wanted)
            entries = []
            for pid in args.get("product_ids") or []:
                code = self.codes.get(pid)
                if code is None:
                    continue
                qty = wanted.get(pid)
                branch = BRANCH_OF[code] if qty is not None else None
                entries.append(
                    {
                        "product_id": pid,
                        "product_code": code,
                        "product_name": f"ZZT {code}",
                        "needs_quantity": qty is None,
                        "requested_qty": qty,
                        "branch": branch,
                        "cap_unset": False,
                        "category_name": "ZZT R9",
                        "eta": "19/10/2026" if branch == "incoming" else None,
                        "packing_list": None,
                    }
                )
            return present(
                name,
                json.dumps(
                    {
                        "data": [],
                        "pagination": {"total": 0, "page": 1, "limit": 50},
                        "empty": True,
                        "stock_visibility": {
                            "mode": "availability",
                            "warehouse_codes": None,
                            "source": "contact",
                        },
                        "stock_availability": entries,
                        "last_updated_at": "2026-09-28T09:00:00",
                    }
                ),
            )

        def record_job(func, *args, **kwargs):
            self.jobs.append((func, args, kwargs))

        from app.services.ai_assistant_service import MCPRuntimeClient

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", fake_parse)
        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
        monkeypatch.setattr(stock_ask_service, "enqueue_job", record_job)

    def say(
        self,
        message: str,
        v: dict[str, Any],
        *,
        is_test: bool = False,
        console: bool = False,
        **extra: Any,
    ):
        """`console=True` builds the envelope the chat console builds
        (`console_service._build_envelope`), so the test pins the marker the console sets
        rather than a hand-made copy of it. `extra` adds envelope fields (`ingress`,
        `test_run_id`, ...) to a non-console envelope."""
        self._next = v
        inbound = {
            "event_type": "message.received",
            "contact": {"id": CONTACT_ID},
            "message": {
                "messageId": f"ZZT-sa4-{uuid.uuid4().hex[:10]}",
                "contactId": CONTACT_ID,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": message},
            },
        }
        if console:
            from app.services.chatbot import console_service
            from app.services.chatbot.contracts import TurnRequest

            built = console_service._build_envelope(
                _envelope(message=inbound).model_dump(mode="json"),
                contact_respond_id=str(CONTACT_ID),
                message_text=message,
                run_id=f"ZZT-console-{uuid.uuid4().hex[:8]}",
                session_vars=None,
                prompt_version_id=None,
            )
            envelope = TurnRequest(envelope=built).envelope
        else:
            envelope = _envelope(is_test=is_test, message=inbound, **extra)
        return engine_mod.run_turn(envelope, session_factory=self.session_factory)

    def ask_all_four(self, *, is_test: bool = False, console: bool = False, **extra: Any):
        return self.say(
            "ZZTSA4-BIG 300, ZZTSA4-INS 50, ZZTSA4-INC 150, ZZTSA4-NOI 20",
            stock(
                product("ZZTSA4-BIG", 300),
                product("ZZTSA4-INS", 50),
                product("ZZTSA4-INC", 150),
                product("ZZTSA4-NOI", 20),
            ),
            is_test=is_test,
            console=console,
            **extra,
        )

    def uuid_of(self, code: str) -> str:
        return next(pid for pid, c in self.codes.items() if c == code)

    @property
    def notified(self) -> list[dict[str, Any]]:
        """The facts of every `notify_salesman` job enqueued, in order."""
        out = []
        for func, args, kwargs in self.jobs:
            if getattr(func, "__name__", "") == "notify_salesman":
                out.append(args[0])
        return out


def test_the_fixture_codes_are_typed_codes():
    """Fix round 4 pin: every fixture code is a typed CODE under #833's code-first rule,
    so the family ask is a forward ask with one figure line per product, never a counted
    set. A digitless rename would silently route every engine test here to the HAS miss."""
    from app.services.chatbot.lanes.business.gate import _is_a_described_word
    from app.services.chatbot.lanes.business.resolve_gate import _names_a_typed_code

    for code in BRANCH_OF:
        assert not _is_a_described_word(code), code
        assert _names_a_typed_code({"entities": [product(code, 1)]}), code


def test_ac_sa401_toggle_on_enqueues_one_job_per_b1_b2_b4_and_none_for_b3(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    out = dealer.ask_all_four()
    assert out.error is None, out.error
    reply = (out.reply or {}).get("text") or ""
    assert f"ZZTSA4-INC x 150: ❌ ETA 19/10/2026." in reply

    facts = dealer.notified
    assert sorted(f["branch"] for f in facts) == ["in_stock", "no_incoming", "too_big"]
    assert {f["product_code"] for f in facts} == {"ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"}
    assert {f["quantity"] for f in facts} == {300, 50, 20}
    for _func, _args, kwargs in dealer.jobs:
        assert kwargs.get("queue_name") == "respond_io"


def test_ac_sa401_toggle_off_enqueues_nothing(session_factory, monkeypatch, stub_access):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=False)
    out = dealer.ask_all_four()
    assert out.error is None, out.error
    assert dealer.notified == []


@pytest.mark.parametrize(
    "extra",
    [
        # A plain test envelope (n8n's own test mode, the clone's replay).
        {"is_test": True},
        {"test_run_id": "ZZT-clone-run"},
        # The Prompts screen's "Run a turn" (`chat.run_prompt_dry_run_turn`): ingress
        # console, but not the chat console, so no marker.
        {"is_test": True, "ingress": "console"},
        # The marker alone, off a caller that is not the console, is not enough.
        {"is_test": True, "console_origin": True},
    ],
    ids=["is_test", "test_run_id", "prompt_screen_run_a_turn", "marker_without_console_ingress"],
)
def test_ac_sa401_a_dry_run_that_is_not_the_chat_console_enqueues_nothing(
    session_factory, monkeypatch, stub_access, extra
):
    """Owner ruling 28 Sep 2026: only the CHAT CONSOLE's dry run notifies; every other
    dry run stays D14 (zero writes outside `chatbot.turns`)."""
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    out = dealer.ask_all_four(**extra)
    assert out.error is None, out.error
    assert out.is_test is True
    assert "ZZTSA4-BIG x 300" in ((out.reply or {}).get("text") or "")
    assert dealer.jobs == []


def test_ac_sa401_a_chat_console_turn_enqueues_the_real_notify_job(
    session_factory, monkeypatch, stub_access
):
    """Owner ruling 28 Sep 2026 ("it should work using chat console also"): a console turn
    that answers a stock ask enqueues the SAME `notify_salesman` job on `respond_io` a live
    turn does, one per B1 / B2 / B4, so the owner can hand test the salesperson message
    from the console. The turn itself is still a dry run."""
    from app.tasks import stock_ask_tasks

    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    out = dealer.ask_all_four(console=True)
    assert out.error is None, out.error
    assert out.is_test is True

    facts = dealer.notified
    assert sorted(f["branch"] for f in facts) == ["in_stock", "no_incoming", "too_big"]
    assert {f["product_code"] for f in facts} == {"ZZTSA4-BIG", "ZZTSA4-INS", "ZZTSA4-NOI"}
    for func, _args, kwargs in dealer.jobs:
        assert func is stock_ask_tasks.notify_salesman
        assert kwargs.get("queue_name") == "respond_io"


def test_ac_sa401_a_chat_console_turn_with_the_toggle_off_enqueues_nothing(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=False)
    out = dealer.ask_all_four(console=True)
    assert out.error is None, out.error
    assert dealer.notified == []


def test_ac_sa401_an_ask_still_owing_a_quantity_enqueues_nothing(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    out = dealer.say("check stock ZZTSA4-INS", stock(product("ZZTSA4-INS")))
    assert out.error is None, out.error
    assert dealer.notified == []


def test_ac_sa402_the_job_is_enqueued_after_the_turn_row_is_closed(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)
    statuses: list[str] = []

    def record_job(func, *args, **kwargs):
        from app.models.chatbot_turn import ChatbotTurn

        db = session_factory()
        row = db.query(ChatbotTurn.status).order_by(ChatbotTurn.created_at.desc()).first()
        statuses.append(row[0] if row else None)
        dealer.jobs.append((func, args, kwargs))

    monkeypatch.setattr(stock_ask_service, "enqueue_job", record_job)
    out = dealer.ask_all_four()
    assert out.error is None, out.error
    assert statuses and set(statuses) == {"done"}


def test_ac_sa402_a_turn_that_fails_before_the_write_enqueues_nothing(
    session_factory, monkeypatch, stub_access
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True)

    def boom(*a, **k):
        raise RuntimeError("the session write failed")

    monkeypatch.setattr(turn_tail, "persist", boom)
    out = dealer.ask_all_four()
    assert out.status == "failed"
    assert dealer.jobs == []


def _asks_and_logs(session_factory, contact_id: str) -> tuple[list[Any], list[IntegrationLog]]:
    from app.models.stock_ask import StockAsk

    db = session_factory()
    db.info["company_scope"] = None
    asks = db.query(StockAsk).filter(StockAsk.contact_id == contact_id).all()
    ids = [a.id for a in asks]
    logs = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "stock_asks", IntegrationLog.business_id.in_(ids))
        .all()
        if ids
        else []
    )
    return asks, logs


@pytest.mark.parametrize("console", [False, True], ids=["live", "console"])
def test_ac_sa411_agent_not_allowed_to_send_enqueues_nothing_but_writes_and_logs(
    session_factory, monkeypatch, stub_access, console
):
    """Fix round 2 (owner note on PR #1333, 28 Sep 2026): the salesperson notification
    respects the allowed-to-send flag on the salesperson's Respond contact
    (`respond_contacts.outbound_enabled`), live and console alike. Flag off: no job, the
    ask rows are still written (the Asks tab shows them), and each notified branch gets
    one integration log line saying why nothing went out."""
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True, agent_allowed=False)
    out = dealer.ask_all_four(console=console)
    assert out.error is None, out.error
    assert dealer.notified == []

    asks, logs = _asks_and_logs(session_factory, dealer.contact_id)
    # CUSTOMER-ASKS-REFER-ONLY: B3 `incoming` does not refer, so it is not a row.
    assert sorted(a.branch for a in asks) == ["in_stock", "no_incoming", "too_big"]
    assert {a.source for a in asks} == {"console" if console else "live"}
    notified = asks
    assert all(a.notified_agent is False for a in notified)
    assert {a.notify_skip_reason for a in notified} == {"contact_not_allowed_to_send"}
    assert len(logs) == 3
    for log in logs:
        assert log.status == "skipped"
        assert log.error_message == "not sent: contact not allowed to send"
        assert log.integration_channel == "respond_io"
        assert log.direction == "outbound"
        assert log.external_reference == dealer.agent_respond_id


@pytest.mark.parametrize("console", [False, True], ids=["live", "console"])
def test_ac_sa411_agent_allowed_to_send_enqueues_the_jobs(
    session_factory, monkeypatch, stub_access, console
):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=True, agent_allowed=True)
    out = dealer.ask_all_four(console=console)
    assert out.error is None, out.error
    facts = dealer.notified
    assert sorted(f["branch"] for f in facts) == ["in_stock", "no_incoming", "too_big"]
    asks, logs = _asks_and_logs(session_factory, dealer.contact_id)
    assert len(asks) == 3
    assert logs == []


# --------------------------------------------------------------------------------------- #
# The send half: `notify_salesman(db, facts)`
# --------------------------------------------------------------------------------------- #


@pytest.fixture
def db(session_factory):
    session = session_factory()
    session.info["company_scope"] = None
    yield session


def _contact(db, *, name: str, respond_io_id: str | None) -> str:
    cid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, name, session_vars) "
            "VALUES (:id, :rid, :phone, :name, CAST('{}' AS jsonb))"
        ),
        {
            "id": cid,
            "rid": respond_io_id,
            "phone": f"+6002{uuid.uuid4().int % 10**7:07d}",
            "name": name,
        },
    )
    return cid


def _chain(
    db,
    *,
    link_customer: bool = True,
    agent: bool = True,
    agent_contact: bool = True,
    agent_respond_id: str | None = "ZZT-agent-rid",
) -> dict[str, Any]:
    """dealer contact -> customer -> sales agent -> agent's Respond contact."""
    dealer_contact = _contact(db, name="Ah Seng", respond_io_id=f"ZZT-{uuid.uuid4().hex[:8]}")
    agent_contact_id = (
        _contact(db, name="Agent Lim", respond_io_id=agent_respond_id) if agent_contact else None
    )
    sales_agent = None
    if agent:
        sales_agent = SalesAgent(
            id=str(uuid.uuid4()),
            sales_agent=f"ZZT{uuid.uuid4().hex[:6]}",
            contact_id=agent_contact_id,
            company_id=SORENTO,
        )
        db.add(sales_agent)
        db.flush()
    customer = Customer(
        id=str(uuid.uuid4()),
        customer_code=f"ZZT-C-{uuid.uuid4().hex[:6]}",
        customer_name="Hock Lee Trading",
        company_id=SORENTO,
        sales_agent_id=sales_agent.id if sales_agent else None,
    )
    db.add(customer)
    db.flush()
    if link_customer:
        db.execute(
            text(
                "INSERT INTO respond_contact_customers (id, contact_id, customer_id, is_primary, "
                "source, company_id) VALUES (gen_random_uuid(), :c, :cu, true, 'manual', :co)"
            ),
            {"c": dealer_contact, "cu": customer.id, "co": SORENTO},
        )
    db.commit()
    return {
        "dealer_contact": dealer_contact,
        "customer": customer,
        "agent": sales_agent,
        "agent_contact": agent_contact_id,
    }


def _facts(chain: dict[str, Any], **over: Any) -> dict[str, Any]:
    base = {
        "turn_id": str(uuid.uuid4()),
        "contact_id": chain["dealer_contact"],
        "product_id": None,
        "product_code": "SRT5674",
        "product_name": "Wiper Blade 24in",
        "quantity": 50,
        "branch": "in_stock",
        "cap_unset": False,
        "category_name": "Wiper Blades",
        "asked_at": "2026-09-24T06:32:00+00:00",
    }
    base.update(over)
    return base


def _logs(db) -> list[IntegrationLog]:
    return (
        db.query(IntegrationLog)
        .filter(IntegrationLog.integration_channel == "respond_io")
        .order_by(IntegrationLog.created_at)
        .all()
    )


def _window(monkeypatch, *, open_: bool) -> None:
    monkeypatch.setattr(
        messaging,
        "get_window_state",
        lambda db, identifier, respond_contact_id=None: {
            "open": open_,
            "last_incoming_at": None,
            "checked_at": "2026-09-28T00:00:00Z",
        },
    )


class _FakeRespond:
    sent: list[tuple[str, str]] = []

    def send_message(self, identifier, text_):
        _FakeRespond.sent.append((identifier, text_))
        return {"messageId": 1}


@pytest.fixture
def respond(monkeypatch):
    from app.services import integration_service

    _FakeRespond.sent = []
    monkeypatch.setattr(integration_service, "RespondClient", _FakeRespond)
    return _FakeRespond


@pytest.mark.parametrize(
    "branch,cap_unset,expected",
    [
        ("in_stock", False, "in stock"),
        ("too_big", False, "too big"),
        ("too_big", True, "no cap set for Wiper Blades"),
        ("no_incoming", False, "no stock no incoming"),
    ],
)
def test_ac_sa403_outcome_phrase(branch, cap_unset, expected):
    assert (
        stock_ask_service.outcome_phrase(branch, cap_unset=cap_unset, category_name="Wiper Blades")
        == expected
    )


def test_ac_sa403_context_vars_and_the_default_wording(db, monkeypatch, respond):
    chain = _chain(db)
    _window(monkeypatch, open_=True)
    seen: dict[str, Any] = {}
    real = messaging.send_text_or_template

    def spy(db_, **kwargs):
        seen.update(kwargs)
        return real(db_, **kwargs)

    monkeypatch.setattr(stock_ask_service, "send_text_or_template", spy)
    out = stock_ask_service.notify_salesman(db, _facts(chain))

    assert out["status"] == "sent"
    assert seen["use_case"] == "stock_ask_salesman"
    assert seen["identifier"] == "ZZT-agent-rid"
    assert seen["respond_contact_id"] == chain["agent_contact"]
    ctx = seen["context_vars"]
    assert ctx["outcome"] == "in stock"
    assert ctx["customer_name"] == "Hock Lee Trading"
    assert ctx["contact_name"] == "Ah Seng"
    assert ctx["product"] == "SRT5674 - Wiper Blade 24in"
    assert ctx["quantity"] == "50"
    # 06:32 UTC is 14:32 on the Malaysia wall clock.
    assert ctx["asked_at"] == "24/09/2026 14:32"
    assert seen["text"] == (
        "Stock ask - Hock Lee Trading (contact: Ah Seng) asked about SRT5674 - Wiper Blade "
        "24in, qty 50, outcome: in stock. Asked at 24/09/2026 14:32."
    )
    for key in ("outcome", "customer_name", "product", "quantity", "asked_at"):
        assert key in PARAM_VARIABLES, key


@pytest.mark.parametrize(
    "chain_kwargs,reason",
    [
        ({"link_customer": False}, "no_customer"),
        ({"agent": False}, "no_sales_agent"),
        ({"agent_contact": False}, "agent_has_no_contact"),
        ({"agent_respond_id": None}, "agent_contact_has_no_respond_id"),
    ],
)
def test_ac_sa404_each_missing_link_is_a_skip_with_its_reason(
    db, monkeypatch, respond, caplog, chain_kwargs, reason
):
    chain = _chain(db, **chain_kwargs)
    _window(monkeypatch, open_=True)
    with caplog.at_level(logging.WARNING):
        out = stock_ask_service.notify_salesman(db, _facts(chain))
    assert out == {"status": "skipped", "reason": reason}
    assert respond.sent == []
    assert _logs(db) == []
    assert any(reason in r.getMessage() for r in caplog.records), caplog.text


def test_ac_sa405_window_open_sends_text_and_logs_one_success_row(db, monkeypatch, respond):
    chain = _chain(db)
    _window(monkeypatch, open_=True)
    facts = _facts(chain)
    out = stock_ask_service.notify_salesman(db, facts)

    assert out["status"] == "sent"
    assert out["sent_as"] == "text"
    assert len(respond.sent) == 1
    assert respond.sent[0][0] == "ZZT-agent-rid"
    logs = _logs(db)
    assert len(logs) == 1
    log = logs[0]
    assert (log.status, log.direction, log.external_reference) == (
        "success",
        "outbound",
        "ZZT-agent-rid",
    )
    assert log.business_table == "chatbot_turns"
    assert str(log.business_id) == facts["turn_id"]
    assert json.loads(log.request_payload)["message"]["text"] == respond.sent[0][1]


def test_ac_sa406_window_closed_with_a_mapped_template_sends_the_template(
    db, monkeypatch, respond
):
    chain = _chain(db)
    _window(monkeypatch, open_=False)

    def fake_template(db_, *, identifier, use_case, context_vars):
        assert use_case == "stock_ask_salesman"
        return {
            "response": {"messageId": 2},
            "template_name": "stock_ask_salesman_v1",
            "template_id": "tmpl-1",
            "params": [context_vars["customer_name"], context_vars["outcome"]],
            "request_payload": {"message": {"body_text": "{{1}} asked, {{2}}"}},
        }

    monkeypatch.setattr(messaging, "send_template_for_use_case", fake_template)
    out = stock_ask_service.notify_salesman(db, _facts(chain))

    assert out["sent_as"] == "template"
    assert respond.sent == []
    logs = _logs(db)
    assert len(logs) == 1
    payload = json.loads(logs[0].request_payload)
    assert logs[0].status == "success"
    assert payload["message"]["type"] == "whatsapp_template"
    assert payload["message"]["template_name"] == "stock_ask_salesman_v1"
    assert payload["message"]["parameters"] == ["Hock Lee Trading", "in stock"]


def test_ac_sa407_window_closed_and_no_template_logs_a_failed_row(db, monkeypatch, respond):
    chain = _chain(db)
    _window(monkeypatch, open_=False)
    out = stock_ask_service.notify_salesman(db, _facts(chain))

    assert out["status"] == "failed"
    assert respond.sent == []
    logs = _logs(db)
    assert len(logs) == 1
    assert logs[0].status == "failed"
    assert logs[0].error_message
    assert json.loads(logs[0].request_payload)["message"]["type"] == "whatsapp_template"


def test_ac_sa408_a_respond_401_logs_a_failed_row_with_status_and_body(db, monkeypatch):
    from app.services import integration_service

    chain = _chain(db)
    _window(monkeypatch, open_=True)

    class _Resp:
        status_code = 401
        text = '{"message":"Unauthorized"}'

    class _Err(Exception):
        response = _Resp()

    class _Failing:
        def send_message(self, identifier, text_):
            raise _Err("401 Client Error: Unauthorized")

    monkeypatch.setattr(integration_service, "RespondClient", _Failing)
    out = stock_ask_service.notify_salesman(db, _facts(chain))

    assert out["status"] == "failed"
    logs = _logs(db)
    assert len(logs) == 1
    assert logs[0].status == "failed"
    assert logs[0].status_code == 401
    assert "Unauthorized" in (logs[0].response_payload or "")


def test_ac_sa408_the_job_body_never_raises(db, monkeypatch):
    """The dealer's reply went out long before this runs; a broken send is a log row."""
    chain = _chain(db)

    def explode(*a, **k):
        raise RuntimeError("respond is down")

    monkeypatch.setattr(messaging, "get_window_state", explode)
    out = stock_ask_service.notify_salesman(db, _facts(chain))
    assert out["status"] == "failed"
    assert len(_logs(db)) == 1


def test_ac_sa409_the_use_case_is_registered_and_set_default_accepts_it(db):
    from app.models.respond_template import RespondChannel, RespondMessageTemplate
    from app.models.respond_workspace import RespondWorkspace
    from app.services import respond_template_service

    assert "stock_ask_salesman" in TEMPLATE_DEFAULT_USE_CASES
    workspace = RespondWorkspace(
        id=str(uuid.uuid4()),
        space_id=f"ZZT{uuid.uuid4().hex[:6]}",
        name="ZZT",
        api_key_ciphertext="not-encrypted-test",
    )
    db.add(workspace)
    db.flush()
    channel = RespondChannel(
        id=str(uuid.uuid4()), workspace_id=workspace.id, respond_channel_id=909090
    )
    db.add(channel)
    db.flush()
    template = RespondMessageTemplate(
        id=str(uuid.uuid4()),
        channel_id=channel.id,
        respond_template_id=77,
        name="stock_ask_salesman_v1",
        language_code="en",
        status="approved",
        components=[{"type": "body", "text": "x"}],
        body_text="{{1}} asked about {{2}}, qty {{3}}, outcome {{4}} at {{5}}",
        param_count=5,
    )
    db.add(template)
    db.flush()
    out = respond_template_service.set_default(
        db,
        "stock_ask_salesman",
        template_id=template.id,
        param_mapping={
            "1": "customer_name",
            "2": "product",
            "3": "quantity",
            "4": "outcome",
            "5": "asked_at",
        },
    )
    assert out["use_case"] == "stock_ask_salesman"
