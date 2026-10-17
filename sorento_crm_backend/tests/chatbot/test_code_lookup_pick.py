"""CHAT-CODE-LOOKUP slice A, U1.2 / U1.3: a parser pick is kept only when the candidate block
lists it. Code never picks: it only checks the parser's pick against what it listed."""
from __future__ import annotations

import pytest

import app.main  # noqa: F401  isort:skip
from app.services.chatbot.head import grounding
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _verdict(raw: str, canonical: str | None) -> dict:
    return {
        "domain_hint": "inventory", "intent_hint": "check_stock",
        "entities": [{"raw": raw, "hint": "product", "canonical_code": canonical, "current_message": True, "confident": True}],
    }


def _canonical(out: dict) -> str | None:
    return out["entities"][0]["canonical_code"]


def test_a_pick_on_the_listed_codes_keeps_its_canonical(db):
    out, _notes = grounding.ground(db, _verdict("ZZCM66631", "ZZMC66631"), listed_codes=frozenset({"zzmc66631"}))
    assert _canonical(out) == "ZZMC66631"


def test_a_listed_pick_is_matched_case_and_dash_folded(db):
    out, _ = grounding.ground(db, _verdict("ZZCM66631", "ZZ-MC66631"), listed_codes=frozenset({"zz-mc66631"}))
    assert _canonical(out) == "ZZ-MC66631"


def test_a_pick_not_listed_is_dropped_and_the_raw_kept_with_a_note(db):
    out, notes = grounding.ground(db, _verdict("ZZCM66631", "ZZQQ99999"), listed_codes=frozenset({"zzmc66631"}))
    assert _canonical(out) is None
    assert out["entities"][0]["raw"] == "ZZCM66631"
    assert notes


def test_a_family_or_extension_pick_is_kept(db):
    out, _ = grounding.ground(db, _verdict("zztwc286", "ZZTWC286-SH"), listed_codes=frozenset())
    assert _canonical(out) == "ZZTWC286-SH"


def test_an_empty_listed_set_still_drops_an_unrelated_pick(db):
    out, _ = grounding.ground(db, _verdict("ZZCM66631", "ZZMC66631"), listed_codes=frozenset())
    assert _canonical(out) is None


def test_without_listed_codes_the_output_is_exactly_today(db):
    verdict = _verdict("ZZCM66631", "ZZQQ99999")
    assert grounding.ground(db, verdict) == grounding.ground(db, verdict, listed_codes=None)
    out, _ = grounding.ground(db, verdict, listed_codes=None)
    assert _canonical(out) == "ZZQQ99999"
