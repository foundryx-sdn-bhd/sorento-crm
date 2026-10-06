"""RESOLVE-SEMANTIC-FALLBACK, prod case 6 Oct 2026 21:14 MYT (dealer): "hi WC200-S got
stock ?" was answered "Here's what you want: product: IBWC-SC608-PP (+9 more). But no
inventory matched these." although the catalogue holds SRTWC200-S-UF-RL, which CONTAINS
the typed token.

Measured on this branch over a seeded family: the shared resolver DOES place the family
("WC200S" as a substring of ZZTWC200-S-UF / -UF-RL, both in OR mode and in the chatbot's
AND mode), then the gate (`lanes/business/gate.py`) threw it away: its "keep only
candidates whose code relates to a typed token" filter compared the FOLDED token the
resolver echoes (`resolve_gate._PRODUCT_FOLD` strips dashes and spaces: "wc200s") with the
candidate's code lower-cased only ("zztwc200-s-uf-rl"), so neither side contained the
other and the picker lost every row. The same lower-only compare decided AND-mode
exactness (`prod_exacts`), so a dashed exact code was never "exact" to its folded token.

One fold on both sides, the resolver's own (`turn.state.token_key`: casefold, dashes and
whitespace removed), the same fold `engine._exact_codes_only` and `turn_runtime` use.
Fake ZZT codes only.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.chatbot.lanes.business.gate import run_gate

FAMILY = ["ZZTWC200-S-UF", "ZZTWC200-S-UF-RL"]


def _row(code: str, tier: str) -> dict[str, Any]:
    return {"uuid": str(uuid.uuid4()), "entity_type": "product", "canonical_code": code, "match_tier": tier,
            "display": {"product_name": code}}


def _parser(raw: str = "WC200-S", domain: str = "product_attachment") -> dict[str, Any]:
    """`product_attachment` and `incoming` are the domains whose picker the GATE decides
    (`gate.REQUIRE_SPECIFIC_DOMAINS`); inventory's picker is `turn/narrow.py`'s, off the
    policy, and its gate passes the whole family through (`list_all`)."""
    return {"domain_hint": domain, "intent_hint": "check_product_attachment",
            "entities": [{"hint": "product", "raw": raw, "canonical_code": raw, "confident": True}]}


def _picker_codes(out: dict[str, Any]) -> list[str]:
    return sorted(c["label"] for o in out["specific_options"] for c in o["candidates"])


def test_or_mode_a_folded_token_keeps_the_dashed_family_the_resolver_placed():
    resolver = {
        "tokens": ["WC200S"],
        "resolutions": [{"token": "WC200S", "resolved": False, "ambiguous": True, "matches": [_row(c, "substring") for c in FAMILY]}],
        "unresolved_tokens": [],
    }
    out = run_gate(dict(resolver), parser=_parser(), resolver=resolver)

    assert out["gate_passed"] is False
    assert out["require_specific"] is True
    assert _picker_codes(out) == FAMILY, out["specific_options"]


def test_and_mode_a_folded_token_keeps_the_dashed_family_the_resolver_placed():
    """The chatbot sends a single product token in AND mode (`resolve_entity_body`); the
    route answers with an intersection, no per-token resolutions."""
    rows = [_row(c, "and") for c in FAMILY]
    resolver = {
        "match_mode": "and", "tokens": ["WC200S"], "intersection": rows,
        "by_entity_type": {"product": rows}, "empty": False, "unresolved_tokens": [],
    }
    out = run_gate(dict(resolver), parser=_parser(), resolver=resolver)

    assert out["gate_passed"] is False
    assert _picker_codes(out) == FAMILY, out["specific_options"]


def test_and_mode_a_dashed_exact_code_is_exact_to_its_folded_token():
    """"ZZTWC200-S-UF" typed, token "ZZTWC200SUF": the AND intersection holds the exact
    code and its variant; the exact one is the answer, no picker."""
    rows = [_row(c, "and") for c in FAMILY]
    resolver = {
        "match_mode": "and", "tokens": ["ZZTWC200SUF"], "intersection": rows,
        "by_entity_type": {"product": rows}, "empty": False, "unresolved_tokens": [],
    }
    out = run_gate(dict(resolver), parser=_parser("ZZTWC200-S-UF"), resolver=resolver)

    # The gate still asks product_attachment for its attachment_type (a different rule);
    # what this pins is that the exact code is recognised and no picker is built.
    assert out["require_specific"] is False, out["specific_options"]
    assert out["specific_options"] == []
    assert [e["code"] for e in out["compatible_entities"] if e["entity_type"] == "product"] == ["ZZTWC200-S-UF"]


def test_a_candidate_unrelated_to_every_typed_token_is_still_dropped():
    """The filter still does its job: a row whose code shares nothing with the token
    (descriptor noise) does not ride into the picker."""
    rows = [_row(c, "substring") for c in FAMILY] + [_row("IBWC-SC608-PP", "substring")]
    resolver = {
        "tokens": ["WC200S"],
        "resolutions": [{"token": "WC200S", "resolved": False, "ambiguous": True, "matches": rows}],
        "unresolved_tokens": [],
    }
    out = run_gate(dict(resolver), parser=_parser(), resolver=resolver)

    assert _picker_codes(out) == FAMILY, out["specific_options"]


class TestThroughTheEngine:
    def test_hi_wc200_s_got_stock_places_the_family_never_a_miss(self, session_factory, monkeypatch):
        """The real engine and the real resolver over the seeded family (the chatbot's own
        single-token AND body): the family is the turn's subject, never "could not find"
        and never a stranger's rows. Reproduced the prod reply shape before the gate fix
        only in the REQUIRE_SPECIFIC domains; on inventory the resolver and gate placed
        the family on this branch and on main alike, so a prod miss there is a visibility
        question (`chat_searchable_products()`), reported on PR #1511."""
        from app.services.company_scope import DEFAULT_COMPANY_ID
        from tests._mc_lookup_seed import product as seed_product
        from tests.chatbot.test_outstanding_lane import _qf, _run_turn, _seed_contact

        db = session_factory()
        for code in [*FAMILY, "IBWC-SC608-PP"]:
            seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        db.commit()
        _seed_contact(session_factory, variables={})

        result, _captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                domain_hint="inventory", intent_hint="check_stock", order_status=None,
                entities=[{"raw": "WC200-S", "hint": "product", "canonical_code": "WC200-S", "current_message": True, "confident": True}],
            ),
            text_body="hi WC200-S got stock ?",
            msg_id="ZZT-wc200-1",
            attributes=["inventory.stock"],
            mcp_response={"data": []},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        # Inventory lists the family (`list_all`), collapsed to the first code plus a count
        # in this harness's header; what matters is that the family the resolver placed is
        # what the turn is about, never a stranger's rows and never a miss.
        assert FAMILY[0] in reply, reply
        assert "IBWC" not in reply, reply
        assert "could not find" not in reply.lower() and "couldn't find" not in reply.lower(), reply
