"""Crew browser pass on 9fba50f0 (TESTER-LOCAL, PR #1415): "Srtswt3001 / Srtswt3001-gm
stock" answered SRTSWT3001-GM only. SRTSWT3001 exists with 7 stock rows on the copy.

`resolve_entity_body` defaulted `match_mode` to "and" (resolve_gate.py), and the resolve
route returns a non-empty AND result untouched (references.py, `_resolve`'s AND arm). The
prefix probe for `srtswt3001` matches BOTH products and `srtswt3001gm` matches only the
-GM one, so the intersection is SRTSWT3001-GM alone: SRTSWT3001 vanished with no
unresolved token and no miss. Two product codes name two products; a product cannot be
both, so AND across product tokens only ever folds one code onto another's variant.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body


def _ctx(entities: list[dict[str, Any]], match_mode: str | None) -> dict[str, Any]:
    return {
        "text": {"message": {"message": {"text": "Srtswt3001 / Srtswt3001-gm stock"}}},
        "contact": {"id": "900000081"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_stock",
                "domain_hint": "inventory",
                "match_mode": match_mode,
                "entities": entities,
            }
        },
    }


def _product(raw: str) -> dict[str, Any]:
    return {"hint": "product", "raw": raw, "canonical_code": None}


def test_two_product_codes_resolve_or_whatever_the_parser_said():
    for mode in (None, "and"):
        body = resolve_entity_body(_ctx([_product("Srtswt3001"), _product("Srtswt3001-gm")], mode))
        assert body["match_mode"] == "or", mode


def test_one_product_code_keeps_the_parsers_mode():
    body = resolve_entity_body(_ctx([_product("Srtswt3001")], None))
    assert body["match_mode"] == "and"


def test_a_product_beside_another_kind_keeps_and():
    """"stock of SRTWT7445 for HANLIM" is a real AND: a product scoped by a customer."""
    entities = [_product("SRTWT7445"), {"hint": "customer", "raw": "HANLIM", "canonical_code": None}]
    body = resolve_entity_body(_ctx(entities, "and"))
    assert body["match_mode"] == "and"
