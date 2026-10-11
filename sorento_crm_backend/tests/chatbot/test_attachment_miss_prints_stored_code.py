"""RED: the product_attachment miss reply prints the product's STORED code.

The parser's `raw` is what the customer typed ("SRT207DIY"); the resolver folds dashes away,
so the gate holds the product whose stored code is "SRT207-DIY". The not-found reply built
in the `product_attachment` branch of `answer.not_found_error_message` printed the raw
("...for product SRT207DIY"). Contract: when the gate's `compatible_entities` holds a
resolved product (uuid set) whose `code` equals the raw under the existing fold
(`answer._type_norm`), the reply prints the stored code; a raw with no resolved match keeps
printing the raw.

Inputs are the ones that reach that branch with a resolved product: the product's only
match is spec-tier under `product_token_missed=True`, so the breakdown list leaves it out and
the "Could not find ... for product <x>" sentence is built from the parser raw. Pure function,
no database (same style as test_rearch_r4_miss_engine.py::TestAC1702).
"""
from __future__ import annotations

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.lanes.business import answer as answer_mod


def _miss(raw: str, stored: str | None) -> str:
    parser = {
        "domain_hint": "product_attachment",
        "intent_hint": "check_product_attachment",
        "message_type": "business_query",
        "entities": [
            {"raw": raw, "hint": "product", "current_message": True, "confident": True},
            {"raw": "certificate", "hint": "attachment_type", "current_message": True, "confident": True},
        ],
        "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
    }
    matches = (
        [{"entity_type": "product", "canonical_code": stored, "uuid": "p-sc-1", "match_tier": "spec_search"}]
        if stored
        else []
    )
    resolved = {
        "resolutions": [{"token": raw.lower(), "matches": matches}],
        "unresolved_tokens": [raw.lower()],
        "tokens": [raw.lower()],
        "intersection": [],
    }
    gate = {
        "gate_passed": True,
        "compatible_entities": (
            [{"uuid": "p-sc-1", "entity_type": "product", "code": stored}] if stored else []
        ),
        "gate_debug": {"domain": "product_attachment"},
    }
    out = answer_mod.not_found_error_message(
        {"resolved": resolved, "gate": gate},
        parser=parser, resolved=resolved, gate=gate, product_token_missed=True,
    )
    return out.get("escalate_message") or ""


def test_reply_prints_the_stored_code_for_the_folded_raw():
    text = _miss("SRT207DIY", "SRT207-DIY")
    assert "SRT207-DIY" in text, text
    assert "SRT207DIY" not in text, f"the reply still prints the typed raw:\n{text}"


def test_a_raw_with_no_resolved_product_keeps_printing_the_raw():
    text = _miss("ZZNOSUCH9999", None)
    assert "ZZNOSUCH9999" in text, text
