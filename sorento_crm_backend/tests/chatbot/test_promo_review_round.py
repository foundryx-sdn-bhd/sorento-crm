"""PROMO-SUBJECT-FILTER review round red tests (MF1, SF-a, SF-b, NIT-a). Backend halves
(MF2, SF-c) are in tests/test_promotions_subject_filter.py and tests/test_promotion_kind_filter.py.
Fake names only.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.main import app
from app.models.marketing import PromotionType
from app.models.product import Brand, Product
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact, _session_of
from tests.chatbot.test_promo_kind_paths import _seed as _seed_paths, _mcp as _serving_mcp
from tests.chatbot.test_promo_kind_subject import (
    NAMES, PROMO_TOOL, TODAY, _promo, _qf, _route_backed_mcp, _seed_world,
)
from tests.chatbot.test_promo_kind_subject import _say as _say_world
from tests.chatbot.test_promo_subject_filter import HAS, _say


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_a_kind_code_not_in_promotion_types_behaves_as_no_kind(session_factory, monkeypatch):
    """MF1: the parser emits promotion_kind "flyer" (no such type code): same as no kind: the
    stamped menu over Sorento's promotions, never "No current promotion for Sorento."."""
    _seed_world(session_factory, sorento_live_flyer=True)
    captured = _route_backed_mcp(monkeypatch, session_factory)
    reply, _ = _say_world(session_factory, monkeypatch, _qf(kind="flyer"), "sroento flyer", "zzt-rr-mf1")
    assert "No current promotion" not in reply, reply
    assert reply.startswith("Which access level do you need for Sorento?"), reply
    assert " - has promotion" in reply, reply
    calls = [a for n, a in captured if n == PROMO_TOOL]
    assert calls and all(not a.get("promotion_types") for a in calls), captured


def test_post_pick_fetch_serves_the_expired_but_usable_flyer(session_factory, monkeypatch):
    """SF-a, updated to AC-22: Sorento's Dealer flyer is live, its Office flyer ended (expired-but-usable,
    still served by the type). The probe stamps Office "has promotion" and the pick hands it over."""
    ids = _seed_paths(session_factory, sorento="none")
    db = session_factory()
    flyer = db.query(PromotionType).filter_by(type_code="a3_flyer").one()
    prod = db.query(Product).filter(Product.brand_id == ids["srt"]).first()
    _promo(db, "ZZT SORENTO A3 FLYER LIVE", flyer, prod, levels=["sorento_dealer"])
    _promo(db, "ZZT SORENTO A3 FLYER ENDED OFFICE", flyer, prod, levels=["sorento_office"],
           start=TODAY - timedelta(days=60), end=TODAY - timedelta(days=10), active=False)
    db.commit()
    captured = _serving_mcp(monkeypatch, session_factory)
    reply, _ = _say_world(session_factory, monkeypatch, _qf(kind="a3_flyer"), "sroento a3 flyer", "zzt-rr-sfa-ask")
    assert "1. Office - has promotion" in reply and "2. Dealer - has promotion" in reply, reply
    from app.services.chatbot.lanes.business.services import ResolveGateServices  # noqa: F401
    from tests.chatbot.test_promo_kind_subject import _parser_output

    pick = _parser_output(domain_hint=None, intent_hint=None, entities=[], access_levels=[], reference_positions=[1])
    before = len(captured)
    pick_reply, _ = _say_world(session_factory, monkeypatch, pick, "1", "zzt-rr-sfa-pick")
    fetched = [a for n, a in captured[before:] if n == PROMO_TOOL]
    assert fetched, captured
    # AC-22: the ended flyer is served at Office (the double's rows render as "Here are the results.")
    assert "Here are the results" in pick_reply, pick_reply


def test_a_scope_refusal_on_the_bridge_probe_never_shows_an_unstamped_menu(session_factory, monkeypatch):
    """SF-b (AC-9): the bridge probe is refused by the customer scope: the refusal (or the
    could-not-check line), never a bare menu, and no tier pick is armed."""
    _seed_contact(session_factory, variables={})

    def _refuse(trigger, *, space_id=None):
        raise fetch_mod.ScopeViolation("probe asked outside the scope", dropped=[str(uuid.uuid4())])

    monkeypatch.setattr(fetch_mod, "entity_ids_transformer", _refuse)
    reply, captured, _ = _say(
        session_factory, monkeypatch, _qf(kind=None, brand=None), "any promo", "zzt-rr-sfb", lambda n, a: HAS,
    )
    assert "Which access level" not in reply, reply
    open_question = _session_of(session_factory).get("open_question")
    assert not open_question or open_question.get("kind") != "tier_pick", open_question


def test_menu_names_the_real_brand_name_and_binds_to_the_resolved_word(session_factory, monkeypatch):
    """NIT-a: an unresolved brand word comes first, the resolved one second: the real
    brand_name ("Sorento Bath", not .title() of the code) goes with the resolved word, and the
    unresolved word stays as typed."""
    _seed_contact(session_factory, variables={})
    db = session_factory()
    db.add(Brand(id=str(uuid.uuid4()), brand_code="ZZTSRB", brand_name="Sorento Bath", company_id=DEFAULT_COMPANY_ID))
    db.commit()
    qf = _qf(kind=None, brand="Sorento Bath", typed="sroento")
    qf["entities"].insert(0, {"raw": "xyzzy", "hint": "brand", "canonical_code": None, "current_message": True, "confident": True})
    reply, _, _ = _say(session_factory, monkeypatch, qf, "xyzzy sroento promo", "zzt-rr-nit", lambda n, a: HAS)
    first = reply.splitlines()[0]
    assert "xyzzy" in first and "Sorento Bath" in first, reply
    assert "sroento" not in first.lower(), reply
