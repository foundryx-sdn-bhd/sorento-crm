"""CHAT-COMPOSER S4 red tests (misses and promotions): one missing-file sentence on a miss (F4),
qualified misses keep today's sentence (OI-4), dates dd/mm/yyyy outside field lines (F5), and the
PX pins (promo dates and the EXPIRED marker already right on the live paths).

Goldens are literals captured from main code on 17 Oct (S3a landed, gate off).
"""
from __future__ import annotations

import re

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.lanes.business import answer as A
from tests.chatbot.test_composers_stock_xd import _set_gate

TEAM = "marketing product"
OFFER = f"Would you like me to escalate to {TEAM} team?"
REFER = "Please refer to your salesman."


class _Dealer:
    escalation_allowed = False
    tier = "dealer"


class _Staff:
    escalation_allowed = True
    tier = "office"


def _breakdown(*, types=("Product Photos",), profile=None, extra=None):
    """Product resolved and exact, attachment type resolved, nothing returned (cp0-7b068ed4da)."""
    names = ["Product Photos", "Technical Specifications"][: len(types)]
    ents = [{"raw": "SRTBF11850", "hint": "product", "current_message": True, "confident": True}] + [
        {"raw": t, "hint": "attachment_type", "current_message": True, "confident": True} for t in types
    ]
    parser = {
        "domain_hint": "product_attachment",
        "intent_hint": "check_product_attachment",
        "message_type": "business_query",
        "entities": ents,
        "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        **(extra or {}),
    }
    resolved = {
        "resolutions": [
            {"token": "srtbf11850", "matches": [{"entity_type": "product", "canonical_code": "SRTBF11850", "uuid": "p0"}]},
            *[
                {
                    "token": t,
                    "matches": [
                        {"entity_type": "attachment_type", "canonical_code": n, "uuid": f"t{i}", "display": {"type_name": n}}
                    ],
                }
                for i, (t, n) in enumerate(zip(types, names))
            ],
        ],
        "unresolved_tokens": [],
        "tokens": ["srtbf11850"],
        "intersection": [],
        "by_entity_type": {"product": ["SRTBF11850"], "attachment_type": names},
    }
    gate = {
        "gate_passed": True,
        "compatible_entities": [{"uuid": "p0", "entity_type": "product", "code": "SRTBF11850"}]
        + [{"uuid": f"t{i}", "entity_type": "attachment_type", "code": n} for i, n in enumerate(names)],
        "gate_debug": {"domain": "product_attachment"},
    }
    out = A.not_found_error_message(
        {"resolved": resolved, "gate": gate}, parser=parser, resolved=resolved, gate=gate, profile=profile
    )
    return out.get("escalate_message")


def _else_arm(*, types=("Product Photos",), codes=("SRT207DIY",), stored=("SRT207-DIY",), extra=None, profile=None):
    """The product matched only at spec tier, so the breakdown list is empty (else arm)."""
    parser = {
        "domain_hint": "product_attachment",
        "intent_hint": "check_product_attachment",
        "message_type": "business_query",
        "entities": [{"raw": c, "hint": "product", "current_message": True, "confident": True} for c in codes]
        + [{"raw": t, "hint": "attachment_type", "current_message": True, "confident": True} for t in types],
        "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        **(extra or {}),
    }
    resolved = {
        "resolutions": [
            {
                "token": c.lower(),
                "matches": [{"entity_type": "product", "canonical_code": s, "uuid": f"p{i}", "match_tier": "spec_search"}],
            }
            for i, (c, s) in enumerate(zip(codes, stored))
        ],
        "unresolved_tokens": [c.lower() for c in codes],
        "tokens": [c.lower() for c in codes],
        "intersection": [],
    }
    gate = {
        "gate_passed": True,
        "compatible_entities": [{"uuid": f"p{i}", "entity_type": "product", "code": s} for i, s in enumerate(stored)],
        "gate_debug": {"domain": "product_attachment"},
    }
    return A.not_found_error_message(
        {"resolved": resolved, "gate": gate},
        parser=parser,
        resolved=resolved,
        gate=gate,
        product_token_missed=True,
        profile=profile,
    ).get("escalate_message")


HEAD = "Here's what you want:\n• product: SRTBF11850\n• attachment type: Product Photos\n\n"


# --------------------------------------------------------------------------- #
# F4: the miss path
# --------------------------------------------------------------------------- #


def test_attachment_breakdown_miss_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _breakdown(profile=_Dealer()) == f"{HEAD}But no Product Photos matched these. {REFER}"


def test_attachment_breakdown_miss_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    assert _breakdown(profile=_Dealer()) == f"{HEAD}No Product Photos on record for SRTBF11850. {REFER}"
    assert _breakdown() == f"{HEAD}No Product Photos on record for SRTBF11850. {OFFER}"


def test_attachment_breakdown_two_types_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _breakdown(types=("Product Photos", "Technical Specifications"))
    assert out.endswith(f"No Product Photos or Technical Specifications on record for SRTBF11850. {OFFER}"), out


def test_attachment_else_arm_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _else_arm() == f"Could not find a Product Photos for product SRT207-DIY. {OFFER}"
    assert _else_arm(profile=_Staff()) == "Could not find a Product Photos for product SRT207-DIY."


def test_attachment_else_arm_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    assert _else_arm() == f"No Product Photos on record for SRT207-DIY. {OFFER}"
    assert _else_arm(profile=_Staff()) == "No Product Photos on record for SRT207-DIY."


def test_attachment_two_types_two_products_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _else_arm(
        types=("Product Photos", "Technical Specifications"),
        codes=("SRT207DIY", "SRT208DIY"),
        stored=("SRT207-DIY", "SRT208-DIY"),
    )
    lines = out.split("\n")
    assert lines[0] == "No Product Photos or Technical Specifications on record for SRT207-DIY."
    assert lines[1] == f"No Product Photos or Technical Specifications on record for SRT208-DIY. {OFFER}"
    assert len(lines) == 2


def test_attachment_miss_qualifiers_keep_today(monkeypatch):
    """OI-4: a filtered miss is not "nothing on record". Only the date format may differ."""
    window = {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}
    for kwargs in ({"extra": {"is_active": False}}, {"extra": window}):
        _set_gate(monkeypatch, False)
        off = _else_arm(**kwargs)
        _set_gate(monkeypatch, True)
        on = _else_arm(**kwargs)
        assert "on record" not in on, on
        assert on == off.replace("2026-09-01", "01/09/2026").replace("2026-09-30", "30/09/2026")
    _set_gate(monkeypatch, False)
    off = _breakdown(extra=window)
    _set_gate(monkeypatch, True)
    on = _breakdown(extra=window)
    assert "on record" not in on, on
    assert on == off.replace("2026-09-01", "01/09/2026").replace("2026-09-30", "30/09/2026")


@pytest.mark.parametrize("gate", [False, True])
def test_attachment_product_unfound_unchanged(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    parser = {
        "domain_hint": "product_attachment",
        "intent_hint": "check_product_attachment",
        "message_type": "business_query",
        "entities": [
            {"raw": "ZZNOSUCH9999", "hint": "product", "current_message": True, "confident": True},
            {"raw": "Product Photos", "hint": "attachment_type", "current_message": True, "confident": True},
        ],
        "routing": {"suggested_team": "marketing_product"},
    }
    resolved = {
        "resolutions": [{"token": "zznosuch9999", "matches": []}],
        "unresolved_tokens": ["zznosuch9999"],
        "tokens": ["zznosuch9999"],
        "intersection": [],
    }
    gate_ = {"gate_passed": True, "compatible_entities": [], "gate_debug": {"domain": "product_attachment"}}
    out = A.not_found_error_message(
        {"resolved": resolved, "gate": gate_}, parser=parser, resolved=resolved, gate=gate_, product_token_missed=True
    ).get("escalate_message")
    assert "Couldn't find" in out and "ZZNOSUCH9999" in out
    assert "on record" not in out


# --------------------------------------------------------------------------- #
# F5: dates outside field lines
# --------------------------------------------------------------------------- #


def test_miss_date_range(monkeypatch):
    window = {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}
    _set_gate(monkeypatch, False)
    assert _breakdown(extra=window) == f"{HEAD}But no Product Photos from 2026-09-01 to 2026-09-30 matched these. {OFFER}"
    _set_gate(monkeypatch, True)
    assert _breakdown(extra=window) == f"{HEAD}But no Product Photos from 01/09/2026 to 30/09/2026 matched these. {OFFER}"


# --------------------------------------------------------------------------- #
# PX1 / PX2: pins, right on the live paths gate on
# --------------------------------------------------------------------------- #


def _f(label, value):
    return {"label": label, "value": value}


def _promo_out(flags=None):
    from app.services.chatbot.lanes.business.fetch import output_structurer

    item = {"fields": [_f("Promotion", "CABANA"), _f("Start Date", "2026-09-25"), _f("End Date", "2026-12-25")], "flags": flags or {}}
    env = {"items": [item], "answers": [{"text": "x"}], "has_result": True}
    return output_structurer(env, {"tool": "crm_promotions_list", "semantic_input": {}})["response"]


def test_promo_dates_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _promo_out() == (
        "Here are the results.\n\n1. *Promotion:* CABANA\n*Start Date:* 2026-09-25\n*End Date:* 2026-12-25"
    )


def test_promo_dates_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    assert _promo_out() == (
        "Here are the results.\n\n1. *Promotion:* CABANA\n*Start Date:* 25/09/2026\n*End Date:* 25/12/2026"
    )


@pytest.mark.parametrize("gate", [False, True])
def test_promo_expired_marker_on_the_main_path(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    out = _promo_out({"expired": True})
    assert "⚠️  *(EXPIRED)*" in out and "PROMO EXPIRED" not in out


def test_promo_expired_on_the_cross_domain_row(monkeypatch):
    def block():
        item = {"fields": [{"key": "product_code", "label": "Product Code", "value": "SRTWC286"}], "flags": {"expired": True}}
        zs = {
            "active": True,
            "origin_domain": "incoming",
            "team": "warehouse",
            "missing": [{"code": "SRTWC286", "_n": "SRTWC286", "uuid": "u1"}],
        }
        return A.crossdomain_render({"items": [item], "has_result": True}, zeroset=zs, validator={})["_xdBlock"]["block"]

    _set_gate(monkeypatch, False)
    assert "⚠️  *(PROMO EXPIRED)*" in block()
    _set_gate(monkeypatch, True)
    on = block()
    assert "⚠️  *(EXPIRED)*" in on and "PROMO EXPIRED" not in on


# --------------------------------------------------------------------------- #
# Property
# --------------------------------------------------------------------------- #


def test_s4_output_clean(monkeypatch):
    _set_gate(monkeypatch, True)
    texts = [
        _breakdown(profile=_Dealer()),
        _breakdown(),
        _breakdown(types=("Product Photos", "Technical Specifications")),
        _else_arm(),
        _promo_out({"expired": True}),
    ]
    for t in texts:
        assert "has no " not in t and "matched these" not in t, t
        assert "PROMO EXPIRED" not in t
        assert not re.search(r"\d{4}-\d{2}-\d{2}", t), t
        assert " · " not in t
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", t)
