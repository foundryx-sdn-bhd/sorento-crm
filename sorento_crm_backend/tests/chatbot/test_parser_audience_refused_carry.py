"""PARSER-PER-AUDIENCE crew audit blocker: a subject refused by a grant gate must not carry
into the next ask's answer.

Live (3/3, prod parser v52, contact with all four gated reveals OFF): turn 1 "last receipt SPO
for <code>" is refused correctly; turn 2 "low stock report" printed the low stock refusal PLUS
"No stock for <code>" and an incoming block, though the product has stock. The refused subject
rode the carry into a stock miss answer. Two turns, same contact, parser output FORCED per turn
(verdict and entities only, no message-word rules).

No em or en dashes.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.chatbot.test_outstanding_lane import PRODUCT_CODE, _qf, _run_turn, _seed_contact
from tests.chatbot.test_parser_audience_leak_matrix import (
    LOW_STOCK_REFUSAL,
    L,
    PRODUCT_MATCH,
    _names,
)

STOCK_HIT = {
    "result_type": "stock",
    "intro": "Here is the stock.",
    "items": [
        {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": PRODUCT_CODE},
                {"key": "location", "label": "Location", "value": "KL-WH"},
                {"key": "available_qty", "label": "Available", "value": 168},
            ]
        }
    ],
    "has_result": True,
}

LOW_STOCK = {"all_categories": True, "categories": [], "brands": [], "suppliers": [], "group_by": None}
STOCKISH = ("stock", "incoming", "low_stock", "inventory")


def _low_stock_qf():
    return _qf(
        domain_hint="inventory", intent_hint="low_stock_report", entities=[], low_stock=LOW_STOCK
    )


def _two_turns(session_factory, monkeypatch, *, first_qf, attributes):
    _seed_contact(session_factory, variables={})
    out = []
    for qf, body in ((first_qf, f"ask about {PRODUCT_CODE}"), (_low_stock_qf(), "low stock report")):
        result, captured = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=body,
            msg_id=f"ZZT-pa-{uuid.uuid4().hex[:12]}", attributes=list(attributes),
            matches=PRODUCT_MATCH, mcp_response=json.dumps(STOCK_HIT),
        )
        out.append(((result.reply or {}).get("text") or "", captured))
    return out


def _assert_refusal_only(reply, captured):
    assert reply.strip().startswith(LOW_STOCK_REFUSAL), reply
    low = reply.lower()
    assert "no stock" not in low, reply
    assert PRODUCT_CODE.lower() not in low, reply
    assert "incoming" not in low and "eta" not in low.split(), reply
    stockish = [n for n in _names(captured) if any(k in n for k in STOCKISH)]
    assert stockish == [], captured


class TestRefusedSubjectDoesNotCarry:
    def test_r1_refused_spo_then_low_stock(self, session_factory, monkeypatch) -> None:
        (_r1, c1), (reply, captured) = _two_turns(
            session_factory, monkeypatch,
            first_qf=_qf(domain_hint="spo_allocation", intent_hint="check_spo"), attributes=[],
        )
        assert c1 == [], c1
        _assert_refusal_only(reply, captured)

    def test_r2_refused_cost_then_low_stock(self, session_factory, monkeypatch) -> None:
        (_r1, c1), (reply, captured) = _two_turns(
            session_factory, monkeypatch,
            first_qf=_qf(domain_hint="purchase_cost", intent_hint="check_po_cost"), attributes=[],
        )
        assert c1 == [], c1
        _assert_refusal_only(reply, captured)

    def test_r3_allowed_stock_then_refused_low_stock(self, session_factory, monkeypatch) -> None:
        (r1, c1), (reply, captured) = _two_turns(
            session_factory, monkeypatch,
            first_qf=_qf(domain_hint="inventory", intent_hint="check_stock"), attributes=[],
        )
        assert c1, "control: the allowed stock ask on turn 1 must reach a tool"
        _assert_refusal_only(reply, captured)

    def test_r4_granted_low_stock_still_reaches_the_flow(self, session_factory, monkeypatch) -> None:
        (_r1, _c1), (reply, captured) = _two_turns(
            session_factory, monkeypatch,
            first_qf=_qf(domain_hint="spo_allocation", intent_hint="check_spo"), attributes=[L],
        )
        assert LOW_STOCK_REFUSAL not in reply, reply
        assert "crm_low_stock_report" in _names(captured) or "categor" in reply.lower(), (reply, captured)
