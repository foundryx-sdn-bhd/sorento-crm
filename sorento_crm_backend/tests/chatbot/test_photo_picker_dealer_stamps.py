"""PHOTO-PICKER (owner, 5 Oct 2026; crew answer (a)): the photo picker lists every matching
code, and its " - has/no Product Photos" stamp follows the incoming / ETA picker's rule.

That rule (`pickers.annotate_incoming`, `pickers.py:92-100`): a dealer - a contact on the
"Availability only" stock policy, `Profile.stock_availability_only` - is told nothing
about a variant before picking it, so the roster goes out bare. Every other contact and
staff keep the stamps. One switch covers every attachment type and all three surfaces
`build_suggest_offer` stamps: the require-specific picker, the multi-token did-you-mean
and the single-token code mode.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.lanes.business.answer import build_suggest_offer
from app.services.chatbot.turn.state import Profile

DEALER = Profile(tier="dealer", stock_availability_only=True)
CONTACT = Profile(tier="dealer")
STAFF = Profile(tier="office")

ROSTER = (
    "product_attachment search needs to be more specific. Multiple matches found. Please choose:\n"
    "1. SRTWC286-SH-200\n"
    "2. SRTWC286-SH-P\n"
    "3. SRTWC286-SH"
)

PHOTO_PROBE = {
    "dym_probe_meta": {"ok": True, "noun": "Product Photos", "probed": ["srtwc286-sh-200", "srtwc286-sh-p", "srtwc286-sh"]},
    "dym_available_codes": ["srtwc286-sh"],
}

PARSER = {
    "domain_hint": "product_attachment",
    "entities": [{"raw": "srtwc286", "hint": "product"}, {"raw": "photo", "hint": "attachment_type"}],
}


def _match(code: str, uuid: str) -> dict[str, Any]:
    return {
        "entity_type": "product",
        "canonical_code": code,
        "uuid": uuid,
        "match_tier": "trgm",
        "similarity": 0.5,
        "display": {"product_name": code},
    }


def _require_specific(profile: Profile) -> str:
    out = build_suggest_offer(
        {"escalate_message": ROSTER},
        parser=PARSER,
        resolved={},
        gate={"require_specific": True},
        dym_annotate=PHOTO_PROBE,
        profile=profile,
    )
    return out["escalate_message"]


def _code_mode(profile: Profile) -> str:
    resolved = {
        "resolutions": [
            {
                "token": "srtwc286x",
                "resolved": False,
                "matches": [],
                "alternatives": [
                    _match("SRTWC286-SH-P", "11111111-1111-4111-8111-111111111111"),
                    _match("SRTWC286-SH", "22222222-2222-4222-8222-222222222222"),
                ],
            }
        ],
        "unresolved_tokens": ["srtwc286x"],
    }
    out = build_suggest_offer(
        {},
        parser={"domain_hint": "product_attachment", "entities": [{"raw": "srtwc286x", "hint": "product"}]},
        resolved=resolved,
        gate={},
        dym_annotate={
            "dym_probe_meta": {"ok": True, "noun": "Product Photos", "probed": ["srtwc286-sh-p", "srtwc286-sh"]},
            "dym_available_codes": ["srtwc286-sh"],
        },
        profile=profile,
    )
    return out["suggest_response"]


def _multi_token(profile: Profile) -> str:
    resolved = {
        "resolutions": [
            {"token": "srtwc286x", "resolved": False, "matches": [], "alternatives": [_match("SRTWC286-SH", "22222222-2222-4222-8222-222222222222")]},
            {"token": "srtwc282x", "resolved": False, "matches": [], "alternatives": [_match("SRTWC282", "33333333-3333-4333-8333-333333333333")]},
        ],
        "unresolved_tokens": ["srtwc286x", "srtwc282x"],
    }
    out = build_suggest_offer(
        {},
        parser={
            "domain_hint": "product_attachment",
            "entities": [{"raw": "srtwc286x", "hint": "product"}, {"raw": "srtwc282x", "hint": "product"}],
        },
        resolved=resolved,
        gate={},
        dym_annotate={
            "dym_probe_meta": {"ok": True, "noun": "Product Photos", "probed": ["srtwc286-sh", "srtwc282"]},
            "dym_available_codes": ["srtwc286-sh"],
        },
        profile=profile,
    )
    return out["suggest_response"]


SURFACES = {"require_specific": _require_specific, "code_mode": _code_mode, "multi_token": _multi_token}


@pytest.mark.parametrize("surface", list(SURFACES))
def test_a_dealer_gets_the_bare_list(surface: str) -> None:
    text = SURFACES[surface](DEALER)
    assert "Product Photos" not in text, f"{surface}: a dealer's picker carried a photo stamp:\n{text}"
    assert "SRTWC286-SH" in text, text  # still listed


@pytest.mark.parametrize("profile", [CONTACT, STAFF], ids=["contact", "staff"])
@pytest.mark.parametrize("surface", list(SURFACES))
def test_contacts_and_staff_keep_the_stamps(surface: str, profile: Profile) -> None:
    text = SURFACES[surface](profile)
    assert "has Product Photos" in text and "no Product Photos" in text, text


def test_the_dealer_roster_is_every_code_in_the_gates_own_order() -> None:
    assert _require_specific(DEALER) == ROSTER


def test_the_dealer_code_mode_keeps_the_resolvers_order() -> None:
    """No has-first sort for a dealer: with nothing printed about photos, a sort by them
    would leak the same fact the stamp would have stated."""
    text = _code_mode(DEALER)
    assert text.startswith('Couldn\'t find "srtwc286x" (product). Did you mean SRTWC286-SH-P, or SRTWC286-SH?'), text


def _several_type_probe() -> dict[str, Any]:
    rows = [
        {"code": "SRTWC286-SH-200", "uuid": "44444444-4444-4444-8444-444444444444", "company": "Sorento"},
        {"code": "SRTWC286-SH-P", "uuid": "11111111-1111-4111-8111-111111111111", "company": "Sorento"},
        {"code": "SRTWC286-SH", "uuid": "22222222-2222-4222-8222-222222222222", "company": "Sorento"},
    ]
    return {
        "dym_probe_meta": {"ok": True, "key_mode": "uuid", "probed": [r["uuid"] for r in rows]},
        "dym_probe_row_keys": rows,
        "dym_available_codes": [rows[2]["uuid"]],
        "dym_has_by_type": [
            {"type": "Product Photos", "has": [rows[2]["uuid"]]},
            {"type": "Certification", "has": [rows[1]["uuid"]]},
        ],
    }


@pytest.mark.parametrize("profile", [DEALER, CONTACT], ids=["dealer", "contact"])
def test_a_several_type_ask_follows_the_same_switch(profile: Profile) -> None:
    """ATTACHMENT-MULTI R2's per-type stamps ("- has Product Photos, no Certification")."""
    out = build_suggest_offer(
        {"escalate_message": ROSTER},
        parser=PARSER,
        resolved={},
        gate={"require_specific": True},
        dym_annotate=_several_type_probe(),
        profile=profile,
    )
    if profile is DEALER:
        assert out["escalate_message"] == ROSTER
    else:
        assert "3. SRTWC286-SH - has Product Photos, no Certification" in out["escalate_message"], out["escalate_message"]
