"""REFER-ONLY-FIXES (owner console, 3 Oct 2026, parser v43).

1. A contact who may not escalate is referred to their salesman ("Please refer to your
   salesman.") and must NEVER see the CS escalation picker or any staff list after it.
   Owner's turn: an order ask for SRTWC8605-FT with no match gave "But no order matched
   these. Please refer to your salesman." followed by five numbered staff names, under the
   scope header "Customer: all customers / Product / Dates: all dates".
2. An availability-only dealer: "stock srtwc286-sh" -> "How many units?" -> "5" gave
   "SRTWC286-SH x 5: ❌ No incoming. Please refer to your salesman." The real state is no
   stock AND no incoming, so the line says "❌ No stock and no incoming."

Staff names here are placeholders (public repo).

Plan: documentation/plans/chatbot/PLAN-refer-only-fixes-03oct.md.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.chatbot import escalation_control
from app.services.chatbot.tail import member_offer as member_mod
from app.services.chatbot.turn.pending import ask as pending_ask
from app.services.chatbot.turn.state import Profile
from app.services.chatbot.turn.task import REFER_TO_SALESMAN

from tests.chatbot._turn_helpers import do_window, entity, verdict
from tests.chatbot.test_engine import _envelope, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_escalation_agent_carry import _seed_contact, _seed_product, _stub_incoming_probe_empty
from tests.chatbot.test_escalation_control import _make_dealer

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")

#: Five placeholder CS members, the shape `team_roster_service.list_team_roster` returns.
STAFF = ["Staff One", "Staff Two", "Staff Three", "Staff Four", "Staff Five"]
NUMBERED_ROW = re.compile(r"^\s*\d+[.)]\s+\S", re.MULTILINE)


def _stub_cs_roster(monkeypatch) -> None:
    def stub_rosters(db, plan, ctx):
        return [
            {
                "body": [
                    {"user_id": f"u{i}", "respond_user_id": f"ru{i}", "name": name}
                    for i, name in enumerate(STAFF, start=1)
                ]
            }
            for _ in plan
        ]

    monkeypatch.setattr(member_mod, "fetch_rosters", stub_rosters)


def _assert_no_staff_list(text: str) -> None:
    for name in STAFF:
        assert name not in text, text
    assert not NUMBERED_ROW.search(text), text
    assert "route to" not in text.lower(), text
    assert "escalate" not in text.lower(), text
    assert "no preference" not in text.lower(), text


def _order_miss(session_factory, monkeypatch, stub_parser, stub_access, *, barred: bool):
    _seed_contact(session_factory, phone="+60000009401" if barred else "+60000009402")
    if barred:
        _make_dealer(session_factory)
    _seed_product(session_factory, code="SRTWC8605-FT")
    _stub_incoming_probe_empty(monkeypatch)
    _stub_cs_roster(monkeypatch)
    stub_parser(
        verdict(
            domain_hint="order",
            intent_hint="check_order",
            entities=[entity("SRTWC8605-FT", hint="product", confident=True)],
            routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
        )
    )
    stub_access()
    return engine_mod.run_turn(_envelope(), session_factory=session_factory)


# --------------------------------------------------------------------------- #
# Bug 1: no CS picker after the refer line
# --------------------------------------------------------------------------- #


class TestOrderMissForABarredContact:
    def test_the_owners_order_miss_shows_no_staff_list(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        result = _order_miss(session_factory, monkeypatch, stub_parser, stub_access, barred=True)
        text = (result.reply or {}).get("text") or ""
        assert text.endswith("But no order from 01/09/2026 to 30/09/2026 matched these. Reply with another month or dates (e.g. August, or 15 Sep to 10 Oct). " + REFER_TO_SALESMAN), text
        _assert_no_staff_list(text)
        assert (result.reply or {}).get("result_set") in (None, []), result.reply

    def test_the_refer_miss_keeps_its_scope_header(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Owner ruling (3 Oct 2026, crew-ask (b)): the scope header stays for everyone;
        only the staff list goes."""
        result = _order_miss(session_factory, monkeypatch, stub_parser, stub_access, barred=True)
        text = (result.reply or {}).get("text") or ""
        assert text == (
            "Customer: all customers\nProduct: SRTWC8605-FT\nDates: 01/09/2026 to 30/09/2026\n\n"
            "Here's what you want:\n• product: SRTWC8605-FT\n\n"
            f"But no order from 01/09/2026 to 30/09/2026 matched these. Reply with another month or dates (e.g. August, or 15 Sep to 10 Oct). {REFER_TO_SALESMAN}"
        ), text

    def test_an_allowed_contact_still_gets_the_picker_and_the_header(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        result = _order_miss(session_factory, monkeypatch, stub_parser, stub_access, barred=False)
        text = (result.reply or {}).get("text") or ""
        assert member_mod.PICKER_HEADER in text, text
        assert all(f"{i}. {name}" in text for i, name in enumerate(STAFF, start=1)), text
        assert text.startswith("Customer: all customers"), text


def _picker_text(head: str) -> str:
    rows = "\n".join(f"{i}. {name}" for i, name in enumerate(STAFF, start=1))
    return (
        f"{head} Would you like me to escalate to customer service team?\n\n"
        f"{member_mod.PICKER_HEADER}\n{rows}\n\n{member_mod.PICKER_CLOSE}"
    )


def _member_offer():
    return pending_ask(
        "member_offer",
        [
            {"position": i, "label": name, "entity_type": "member", "uuid": f"u{i}"}
            for i, name in enumerate(STAFF, start=1)
        ],
        team="customer_service",
    )


class TestTheBackstopTakesTheWholePicker:
    """`escalation_control.strip_text` is the backstop every barred reply passes
    (`engine._run_answer`, `run_tail`, the casual lane). Its picker rows went only when the
    question that carried them was still on the answer; an upstream site that had already
    dropped that question left the numbered staff names under the refer line."""

    def test_rows_go_even_when_the_question_was_already_dropped(self) -> None:
        text, question, offered = escalation_control.strip_text(
            _picker_text("But no order matched these."), None, Profile(escalation_allowed=False)
        )
        assert offered and question is None
        assert text == f"But no order matched these.\n\n{REFER_TO_SALESMAN}", text

    def test_rows_go_with_the_question(self) -> None:
        text, question, _offered = escalation_control.strip_text(
            _picker_text("But no order matched these."), _member_offer(), Profile(escalation_allowed=False)
        )
        assert question is None
        assert text == f"But no order matched these.\n\n{REFER_TO_SALESMAN}", text

    def test_a_multi_company_picker_goes_whole(self) -> None:
        rows = "*Sorento:*\n1. Staff One\n2. Staff Two\n*Mocha:*\n3. Staff Three"
        reply = (
            "But no order matched these. Would you like me to escalate to customer service team?\n\n"
            f"{member_mod.PICKER_HEADER}\n{rows}\n\n"
            f"{member_mod.PICKER_MULTI_CLOSE_PREFIX} (*Sorento* / *Mocha*) and we'll assign accordingly."
        )
        text, _q, _o = escalation_control.strip_text(reply, None, Profile(escalation_allowed=False))
        assert text == f"But no order matched these.\n\n{REFER_TO_SALESMAN}", text

    def test_a_product_roster_above_the_picker_stays(self) -> None:
        """A did-you-mean is a clarifying question, not an offer: its rows stay; only the
        roster of people under `ROSTER_HEADER` goes."""
        reply = (
            "Couldn't find SRT5764. Did you mean:\n1. SRT57-CR\n2. SRT5713\n\n"
            f"{member_mod.ROSTER_HEADER}\n3. Staff One\n4. Staff Two\n\n{member_mod.ROSTER_CLOSE}"
        )
        text, _q, _o = escalation_control.strip_text(reply, None, Profile(escalation_allowed=False))
        assert "1. SRT57-CR" in text and "2. SRT5713" in text, text
        assert "Staff" not in text, text
        assert text.endswith(REFER_TO_SALESMAN), text


class TestTheDealerStockStripTakesThePicker:
    """`engine._dealer_refers_to_salesman` (an availability-only dealer's stock or ETA
    reply) dropped a `member_offer` question and the offer sentence, but not the picker it
    hung off."""

    def test_the_picker_goes_with_its_question(self) -> None:
        from app.services.chatbot import dealer_stock

        text, question = dealer_stock.without_escalation(
            _picker_text("SRTWC8605-FT: No ETA"), _member_offer()
        )
        assert question is None
        assert text == f"SRTWC8605-FT: No ETA\n\n{REFER_TO_SALESMAN}", text

    def test_a_roster_keeps_its_products_and_loses_the_members_it_no_longer_shows(self) -> None:
        """Review SF2: a staff row a dealer can no longer see is never left pickable."""
        from app.services.chatbot import dealer_stock

        roster = pending_ask(
            "product_pick",
            [
                {"position": 1, "label": "SRT57-CR", "entity_type": "product", "uuid": "p1"},
                {"position": 2, "label": "SRT5713", "entity_type": "product", "uuid": "p2"},
                {"position": 3, "label": "Staff One", "entity_type": "member", "uuid": "u1"},
            ],
            team="customer_service",
            payload={"escalate_offered": True},
        )
        reply = (
            "Couldn't find SRT5764. Did you mean:\n1. SRT57-CR\n2. SRT5713\n\n"
            f"{member_mod.ROSTER_HEADER}\n3. Staff One\n\n{member_mod.ROSTER_CLOSE}"
        )
        text, question = dealer_stock.without_escalation(reply, roster)
        assert "Staff One" not in text and "1. SRT57-CR" in text, text
        assert [o["label"] for o in question.options] == ["SRT57-CR", "SRT5713"], question.options


# --------------------------------------------------------------------------- #
# Bug 2: an availability line says what is actually true
# --------------------------------------------------------------------------- #

from datetime import date  # noqa: E402

from tests.chatbot._avail_mode_console import AvailConsole, Stock  # noqa: E402
from tests.chatbot._r9_engine_console import product, reply, stock  # noqa: E402

TICK, CROSS = "✅", "❌"


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    def make(**stock_facts: Stock) -> AvailConsole:
        return AvailConsole(session_factory, monkeypatch, stub_access, phone="+60000009403", **stock_facts)

    return make


class TestAvailabilityWording:
    def test_the_owners_turns_no_stock_and_no_incoming(self, console) -> None:
        """Owner, 3 Oct 2026: "stock srtwc286-sh" -> "How many units?" -> "5"."""
        c = console(SRTWC286_SH=Stock(on_hand=0, x=100))
        assert c.say("stock srtwc286-sh", stock(product("SRTWC286-SH"))) == "How many units of SRTWC286-SH?"
        assert c.say("5", reply(demand_qty=5)) == (
            f"SRTWC286-SH x 5: {CROSS} No stock and no incoming. {REFER_TO_SALESMAN}"
        )

    def test_some_stock_short_of_the_ask_and_no_incoming_keeps_its_wording(self, console) -> None:
        c = console(SRTWC286_SH=Stock(on_hand=3, x=100))
        assert c.say("SRTWC286-SH x 5", stock(product("SRTWC286-SH", 5))) == (
            f"SRTWC286-SH x 5: {TICK} 3 available. {REFER_TO_SALESMAN}"
        )

    def test_no_stock_with_a_shipment_due_keeps_its_eta(self, console) -> None:
        c = console(SRTW2000=Stock(on_hand=0, x=200, eta=date(2026, 10, 19)))
        assert c.say("SRTW2000 x 150", stock(product("SRTW2000", 150))) == (
            f"SRTW2000 x 150: {CROSS} ETA 19/10/2026."
        )
