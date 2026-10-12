"""SO-PO-REF guard (crew, 12 Oct 2026): the parser intermittently copies a delivery order
number it ALSO emitted as an order entity into `po_refs`, minus its letters
("PS202609-0469" -> po_refs ["202609-0469"]). The turn then took the PO-ref arm and a
contact without the SO grant was told "Sales order figures are not enabled".

Rule: a `po_refs` string that is the same reference as a non-PO order/document entity of the
same message is not a PO ref. "Same" is the reference fold (case, separators, a leading
letters-only document prefix), never a word list. A PO ref the parser named as a PO kind,
or one no entity repeats, is kept.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_ref_probes_answer import DO_LIST, _seed_do
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, SO_NOT_ENABLED

DO_NO = "PS202609-0469"


def _qf(raw: str, hint: str, po_refs: list[str]) -> dict[str, Any]:
    return _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[{"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}],
        po_refs=po_refs,
    )


def _reply(session_factory, monkeypatch, qf, *, attributes) -> str:
    result, _ = _run_turn(
        session_factory,
        monkeypatch,
        qf=qf,
        text_body=f"{DO_NO} done deliver?",
        msg_id=f"ZZT-poguard-{uuid.uuid4().hex[:10]}",
        attributes=list(attributes),
        real_resolver=True,
        mcp_response=DO_LIST,
    )
    return (result.reply or {}).get("text") or ""


@pytest.mark.parametrize("po_ref", ["202609-0469", "2026090469", "ps202609-0469", "PS 202609 0469"])
def test_po_ref_copied_from_an_order_entity_is_not_a_po_ref(session_factory, monkeypatch, po_ref):
    _seed_contact(session_factory, variables={})
    _seed_do(session_factory, DO_NO, None)
    reply = _reply(session_factory, monkeypatch, _qf(DO_NO, "order", [po_ref]), attributes=())
    assert reply.strip() != SO_NOT_ENABLED, reply
    assert "not enabled" not in reply.lower(), reply


def test_unit_guard_keeps_po_kinds_and_unrepeated_refs():
    from app.services.chatbot.turn_runtime import _po_refs_not_order_entities

    ents = [
        {"raw": DO_NO, "hint": "order", "current_message": True},
        {"raw": "4500123", "hint": "purchase_order", "current_message": True},
        {"raw": "7781", "hint": "customer_po_ref", "current_message": True},
        {"raw": "SO2610-0127", "hint": "sales_order", "current_message": True},
    ]
    refs = ["202609-0469", "4500123", "7781", "2610-0127", "9900", "PS1"]
    assert _po_refs_not_order_entities(refs, ents) == ["4500123", "7781", "9900", "PS1"]


def test_unit_guard_ignores_earlier_turn_entities_and_digit_mismatch():
    from app.services.chatbot.turn_runtime import _po_refs_not_order_entities

    ents = [
        {"raw": DO_NO, "hint": "order", "current_message": False},
        {"raw": "PS202609-0470", "hint": "order", "current_message": True},
        {"raw": "A1B202609-0469", "hint": "order", "current_message": True},
    ]
    assert _po_refs_not_order_entities(["202609-0469"], ents) == ["202609-0469"]
