"""CARRIED-MISS (owner report, 13 Oct 2026 07:33 MYT, prompt v52, prod main 958a57aae).

Contact "Sorento ACT" sent "I want to make complaint report". The parser read it right
(`portal_link` / `get_portal_link`, `entities: []`) and `crm_portal_link_get` returned the
link, but the reply sent was

    Couldn't find: "2024/02-1506" (order). Would you like me to escalate to customer service team?

`2024/02-1506` was a CARRIED focus token (`focus.extra.order`, `current_message: false`)
from an earlier turn. `turn_runtime.with_carried_entities` handed it to the resolver
because the message named nothing, the resolver could not place it, and
`resolve_gate.if3_miss` clause 2 ("unresolved > 0 and no compatible entities") called
that a miss for a domain whose gate had passed the empty ask.

Rule pinned here (plan `PLAN-carried-miss-13oct.md`): a token the current message did
not contain never turns an answer into "Couldn't find" when the domain cannot use a
token of that kind at all. A carried token of a kind the domain DOES look up (a product
under `incoming`) is still the subject of the ask, so its miss stands; a token the
current message typed that resolves to nothing is still a miss. No domain list: the
domain's own `ALLOWED` row (`gate_debug.allowed_lookup`) says which kinds it can use.

Every uuid here is synthetic.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.turn.state import Focus, focus_to_wire

from tests.chatbot._avail_mode_console import AvailConsole
from tests.chatbot._r9_engine_console import reply
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

pytestmark = pytest.mark.usefixtures("stub_access")

ORDER_TOKEN = "2024/02-1506"
LINK = "http://crm.example/portal/c/ABC?token=XYZ"
MISS_WORDS = ("couldn't find", "could not find", "escalate")


def _carried_order() -> dict[str, list[dict[str, Any]]]:
    """The row the earlier order turn left on `focus.extra`: the order number, no uuid."""
    return {"order": [{"raw": ORDER_TOKEN, "hint": "order", "canonical_code": ORDER_TOKEN, "confident": True}]}


def _state(extra: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    focus = Focus(domains=["order"], extra=extra)
    return {"focus": focus_to_wire(focus), "open_question": None}


def _portal_turn() -> dict[str, Any]:
    """The parser's reading of "I want to make complaint report" (prod turn)."""
    return reply(domain_hint="portal_link", intent_hint="get_portal_link", entities=[])


@pytest.fixture
def portal_console(session_factory, monkeypatch, stub_access):
    def make(phone: str) -> AvailConsole:
        console = AvailConsole(session_factory, monkeypatch, stub_access, phone=phone)
        inner = MCPRuntimeClient.call_tool

        def call_tool(client, name: str, args: dict[str, Any]) -> str:
            if name == "crm_portal_link_get":
                console.tool_calls.append((name, args))
                return json.dumps({"portal_url": LINK})
            return inner(client, name, args)

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", call_tool)
        return console

    return make


def _portal_calls(console: AvailConsole) -> list[dict[str, Any]]:
    return [args for name, args in console.tool_calls if name == "crm_portal_link_get"]


# --------------------------------------------------------------------------- #
# 1. The prod turn, replayed through the real engine
# --------------------------------------------------------------------------- #


def test_portal_link_with_a_carried_unresolved_order_sends_the_link(portal_console):
    """U1. Control first: the same turn over a clean focus is the link. Then the prod
    focus (the order number carried on `focus.extra.order`): the reply is the SAME link,
    no "Couldn't find", no escalation offer."""
    c = portal_console("+60100001613")
    c.state = _state({})
    expected = c.say("I want to make complaint report", _portal_turn())
    assert LINK in expected, expected
    assert len(_portal_calls(c)) == 1, c.tool_calls
    c.tool_calls.clear()

    c.state = _state(_carried_order())

    text = c.say("I want to make complaint report", _portal_turn())

    assert len(_portal_calls(c)) == 1, c.tool_calls
    assert text == expected, text
    assert ORDER_TOKEN not in text, text
    assert not any(w in text.lower() for w in MISS_WORDS), text


# --------------------------------------------------------------------------- #
# 2. The miss gate itself
# --------------------------------------------------------------------------- #


def _ctx(*, unresolved: list[str], domain: str, allowed: list[str] | None, gate_passed: bool = True) -> dict[str, Any]:
    gate_debug: dict[str, Any] = {"domain": domain}
    if allowed is not None:
        gate_debug["allowed_lookup"] = allowed
    return {
        "resolved": {"unresolved_tokens": unresolved},
        "gate": {"gate_passed": gate_passed, "compatible_entities": [], "gate_debug": gate_debug},
    }


def _entity(raw: str, hint: str, *, current: bool) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": raw, "current_message": current, "confident": True}


@pytest.mark.parametrize(
    ("domain", "allowed"),
    [
        ("portal_link", []),
        ("forms", ["form"]),
        # An unmapped domain (chat_analytics, help): no `allowed_lookup` at all.
        ("chat_analytics", None),
    ],
)
def test_a_carried_token_the_domain_cannot_use_is_no_miss(domain, allowed):
    parser = {"domain_hint": domain, "entities": [_entity(ORDER_TOKEN, "order", current=False)]}

    assert resolve_gate.if3_miss(_ctx(unresolved=[ORDER_TOKEN], domain=domain, allowed=allowed), parser=parser) is False


def test_a_typed_token_that_resolves_to_nothing_is_still_a_miss():
    """U2: the same order number, typed in THIS message, under the same domain."""
    parser = {"domain_hint": "portal_link", "entities": [_entity(ORDER_TOKEN, "order", current=True)]}

    assert resolve_gate.if3_miss(_ctx(unresolved=[ORDER_TOKEN], domain="portal_link", allowed=[]), parser=parser) is True


def test_a_token_both_typed_and_carried_is_a_typed_token():
    parser = {
        "domain_hint": "portal_link",
        "entities": [_entity(ORDER_TOKEN, "order", current=False), _entity(ORDER_TOKEN.lower(), "order", current=True)],
    }

    assert resolve_gate.if3_miss(_ctx(unresolved=[ORDER_TOKEN], domain="portal_link", allowed=[]), parser=parser) is True


def test_a_carried_token_of_a_kind_the_domain_looks_up_is_still_a_miss():
    """U3's guard: "incoming?" over a carried product word the resolver cannot place is
    asked ABOUT that word; a broad incoming list would answer a question nobody asked."""
    parser = {"domain_hint": "incoming", "entities": [_entity("SRTZZ999", "product", current=False)]}
    ctx = _ctx(unresolved=["SRTZZ999"], domain="incoming", allowed=["product", "inbound_shipment", "category", "brand"])

    assert resolve_gate.if3_miss(ctx, parser=parser) is True


def test_a_carried_token_beside_a_typed_unresolved_one_is_still_a_miss():
    parser = {
        "domain_hint": "portal_link",
        "entities": [_entity(ORDER_TOKEN, "order", current=False), _entity("SO999", "order", current=True)],
    }

    assert resolve_gate.if3_miss(_ctx(unresolved=[ORDER_TOKEN, "SO999"], domain="portal_link", allowed=[]), parser=parser) is True


def test_a_failed_gate_is_still_a_miss_whatever_was_carried():
    parser = {"domain_hint": "inventory", "entities": [_entity(ORDER_TOKEN, "order", current=False)]}
    ctx = _ctx(unresolved=[ORDER_TOKEN], domain="inventory", allowed=["product"], gate_passed=False)

    assert resolve_gate.if3_miss(ctx, parser=parser) is True
