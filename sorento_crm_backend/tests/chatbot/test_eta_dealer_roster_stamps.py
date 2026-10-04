"""PR #1329 fix round 2 (owner hand test on :3101 at e5ee59dc, 29 Sep 2026 11:53 MYT,
contact 900000008, a dealer, prompt v50).

Turn 562961e4 "eta srtwc286": the ten-row roster stamped EVERY row "no incoming", while
turn ca1eb616 "all" fetched ETAs for SRTWC286-SH-NEW, -NEW-200 and -NEW-P. The dealer view
(`eta_policy.dealer_view`, presented by the MCP's `_incoming_dealer`) prints each product
as ONE title, "<code>: ETA <dates>" (one line since AVAIL-MODE-REPLIES), with no field, and `pickers.annotate_incoming`
read the whole title as the code, so nothing matched.

Turn a3d0d2c7 "5" (SRTWC286-SH-NEW-150, no incoming): the dealer was told "No incoming,
no stock and nothing on order" and offered the purchasing team. The ETA policy says a
dealer sees the ETA only and is referred to their salesperson.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import pickers

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access, stub_parser  # noqa: F401 (fixtures)
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _link_contact_company
from tests.chatbot.test_rearch_s3_roster_from_resolver import (
    _seed_contact,
    _seed_products,
)

# The owner's roster, in the order turn 562961e4 printed it.
ROSTER = [
    "SRTWC286-SH",
    "SRTWC286-SH-150",
    "SRTWC286-SH-200",
    "SRTWC286-SH-NEW",
    "SRTWC286-SH-NEW-150",
    "SRTWC286-SH-NEW-200",
    "SRTWC286-SH-NEW-P",
    "SRTWC286-SH-P",
    "SRTWC286-SH-PP",
    "SRTWC286-SH-UF",
]
# What turn ca1eb616 "all" fetched for the same ten ids.
ETAS = {
    "SRTWC286-SH-NEW": ["2026-09-08"],
    "SRTWC286-SH-NEW-200": ["2026-09-08", "2026-09-20"],
    "SRTWC286-SH-NEW-P": ["2026-09-08", "2026-09-20"],
}


def dealer_item(code: str, etas: list[str]) -> dict[str, Any]:
    """One line of the MCP presenter's dealer envelope, byte for byte
    (`sorento_crm_mcp/presenters.py::_incoming_dealer`, pinned by
    `sorento_crm_mcp/tests/test_presenters.py::test_dealer_view_is_one_line_per_product_and_the_salesperson`)."""
    return {"title": f"{code}: \u2705 ETA {', '.join(etas)}", "fields": [], "flags": {"dealer_view": True}}


def dealer_envelope(etas: dict[str, list[str]]) -> dict[str, Any]:
    items = [dealer_item(code, dates) for code, dates in etas.items()]
    envelope: dict[str, Any] = {
        "result_type": "incoming_dealer",
        "intro": "",
        "items": items,
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": bool(items),
    }
    if items:
        envelope["closing"] = "Please refer to your salesman."
    return envelope


# --------------------------------------------------------------------------- #
# The roster stamps (turn 562961e4)
# --------------------------------------------------------------------------- #


def test_the_incoming_annotator_reads_the_code_off_a_dealer_line():
    gate = {
        "gate_clarification": "\n".join(f"{i}. {c}" for i, c in enumerate(ROSTER, 1)),
        "compatible_entities": [{"code": c, "entity_type": "product"} for c in ROSTER],
    }
    out = pickers.annotate_incoming(gate, probe=dealer_envelope(ETAS))
    # AVAIL-MODE-REPLIES (crew, 3 Oct 2026, cloud live-parser pass at 2ff7f5e9): a dealer
    # is told nothing about a variant's incoming before picking it, so the dealer roster
    # is no longer stamped at all (it read "has incoming" on every line once the dealer
    # view listed "CODE: No ETA" rows too). Supersedes this test's 29 Sep stamp.
    assert out["incoming_by_code"] == {}
    assert out["escalate_message"] == gate["gate_clarification"]
    assert "incoming" not in out["escalate_message"]


def test_a_non_dealer_title_is_still_the_whole_code():
    gate = {"compatible_entities": [{"code": "A-1"}, {"code": "A-2"}]}
    out = pickers.annotate_incoming(gate, probe={"answers": [{"title": "A-1"}]})
    assert out["incoming_by_code"] == {"A-1": True, "A-2": False}


def test_the_dealer_roster_lists_every_code_and_stamps_none(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """The owner's turn 562961e4 through `engine.run_turn`: ten SRTWC286 products, the
    incoming probe answered in the dealer's envelope, three of them with ETAs."""
    _seed_contact(session_factory, phone="+60000000437")
    _link_contact_company(session_factory, company_id=SORENTO)
    _seed_products(session_factory, ROSTER)

    from app.services.ai_assistant_service import MCPRuntimeClient

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "crm_incoming_stock_list":
            return json.dumps(dealer_envelope(ETAS))
        return json.dumps({"answers": []})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
    stub_parser(
        verdict(
            domain_hint="incoming",
            intent_hint="check_incoming",
            entities=[entity("srtwc286", hint="product", confident=False)],
        )
    )
    stub_access()

    from app.services.chatbot import engine as engine_mod

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    text = (result.reply or {}).get("text", "")
    # AVAIL-MODE-REPLIES (crew, 3 Oct 2026): every code listed, none stamped.
    for code in ROSTER:
        assert code in text, f"{code!r} must be listed: {text!r}"
    assert "has incoming" not in text and "no incoming" not in text, text


# --------------------------------------------------------------------------- #
# The "5" reply (turn a3d0d2c7)
# --------------------------------------------------------------------------- #


def test_an_empty_dealer_incoming_reply_runs_no_stock_or_po_ladder():
    """No line carries the flag on an empty reply; the envelope's `result_type` does."""
    out = answer_mod.crossdomain_zeroset(
        dealer_envelope({}),
        parser={"domain_hint": "incoming", "message_type": "business_query"},
        resolved={},
        session_block=None,
    )
    assert out["_xd"] == {"active": False, "why": "dealer_view"}


def test_a_dealer_incoming_ask_is_named_for_the_bridge_and_the_offer_strip():
    from app.services.chatbot import engine as engine_mod

    dealer = SimpleNamespace(
        profile=SimpleNamespace(stock_availability_only=True), focus=SimpleNamespace(domains=[])
    )
    staff = SimpleNamespace(
        profile=SimpleNamespace(stock_availability_only=False), focus=SimpleNamespace(domains=[])
    )
    incoming = SimpleNamespace(domains=["incoming"])
    assert engine_mod._dealer_incoming_ask(dealer, incoming) is True
    assert engine_mod._dealer_incoming_ask(staff, incoming) is False
    assert engine_mod._dealer_stock_ask(dealer, incoming) is False
    assert engine_mod._dealer_stock_ask(dealer, SimpleNamespace(domains=["inventory"])) is True


def test_a_dealer_incoming_miss_is_referred_not_offered_purchasing():
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.turn import compose as turn_compose

    answer = turn_compose.Answer(
        text=(
            "product: SRTWC286-SH-NEW-150\n\nBut no incoming matched these. "
            "Would you like me to escalate to purchasing team?"
        )
    )
    out = engine_mod._dealer_refers_to_salesman(answer)
    assert "escalate" not in out.text.lower()
    assert "purchasing" not in out.text.lower()
    assert out.text.endswith("Please refer to your salesman.")
