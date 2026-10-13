"""PROMO-SUBJECT-FILTER live defects on prompt v73. Fake names only; presenter-backed stub."""
from __future__ import annotations

import uuid

import pytest

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product import Brand
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_promo_kind_paths import _continue_services, _seed as _seed_kind_world
from tests.chatbot.test_promo_kind_subject import PROMO_TOOL, _promo, _qf
from tests.chatbot.test_promo_owner_round2 import LEVELS, _mcp, _subject_qf, _turn, _unresolved
from tests.chatbot.test_promo_subject_filter import _pick_qf


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_a_kind_and_brand_pick_keeps_the_brand_and_shows_the_ended_flyers(session_factory, monkeypatch):
    """(A) "any sorento a3 flyer?" then "2": the pick fetch still carries the kind AND the brand,
    so a live Mocha flyer cannot suppress Sorento's ended ones; the reply lists Sorento's flyer
    marked "(EXPIRED)" and never the Mocha one."""
    from datetime import date, timedelta

    from app.models.marketing import Promotion
    from app.services.chatbot.lanes.business.services import ResolveGateServices

    ids = _seed_kind_world(session_factory, sorento="expired_usable")
    db = session_factory()
    flyer = db.query(PromotionType).filter_by(type_code="a3_flyer").one()
    today = date.today()
    db.add(  # a live Mocha flyer with NO linked products
        Promotion(id=str(uuid.uuid4()), description="ZZT MOCHA A3 FLYER UNLINKED", start_date=today - timedelta(days=5),
                  end_date=today + timedelta(days=30), is_active=True, access_levels=["mocha_dealer"], promotion_type_id=flyer.id)
    )
    db.commit()
    captured = _mcp(monkeypatch, session_factory)

    def _resolve(body):  # the real resolver's brand match: no uuid, the live brand_name as canonical
        asked = list(body.get("tokens") or [])
        match = {"uuid": None, "entity_type": "brand", "canonical_code": "Sorento", "display": {"brand_name": "Sorento"}, "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked], "unresolved_tokens": []}

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in ("Sorento Dealer", "Sorento Office", "Mocha Dealer")],
        resolve_entity=_resolve, probe=lambda **_: None,
    )
    menu = _turn(session_factory, monkeypatch, services, _qf(kind="a3_flyer"), "any sorento a3 flyer?", "zzt-v73-ask")
    assert "2. Dealer - has promotion" in menu.splitlines(), menu
    n = len(captured)
    pick = _pick_qf(2)  # live shape: the pick turn's parser repeats the kind but names no brand
    pick["promotion_kind"] = "a3_flyer"
    pick["entities"] = []
    reply = _turn(session_factory, monkeypatch, services, pick, "2", "zzt-v73-pick")
    fetches = [a for name, a in captured[n:] if name == PROMO_TOOL]
    assert fetches, captured
    for a in fetches:
        assert a.get("promotion_types") == ["a3_flyer"], a
        assert a.get("brand_ids") == [ids["srt"]], a
    assert "ZZT SORENTO A3 FLYER ENDED" in reply and "(EXPIRED)" in reply, reply
    assert "ZZT MOCHA A3 FLYER" not in reply, reply


def test_the_prompt_tells_the_parser_to_put_the_catalogue_name_in_canonical_code():
    """(B1) PROMOTION SUBJECT: for "seat cover" the toilet seat type goes in the entity's
    canonical_code (the raw stays what the customer typed)."""
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    section = SEMANTIC_PARSER_PROMPT.split("== PROMOTION SUBJECT ==", 1)[1]
    section = section.split("━", 1)[0].split("\n\n", 1)[0]  # this section only, not the addenda after it
    flat = " ".join(section.split())
    assert "canonical_code" in flat and "seat cover" in flat and "toilet seat" in flat, flat
    # the exact instruction: the category entity's canonical_code is set to "toilet seat"
    assert '"canonical_code": "toilet seat"' in flat or 'canonical_code "toilet seat"' in flat or "canonical_code to \"toilet seat\"" in flat, flat


def test_a_category_entity_with_a_canonical_code_grounds_by_that_name(session_factory, monkeypatch):
    """(B2) raw "seat cover", canonical_code "toilet seat" (product_type registry value toilet_seat):
    stamped menu, the pick returns only the promotion linked to toilet seat products."""
    _seed_contact(session_factory, variables={})
    db = session_factory()
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90, is_default=True)
    db.add(std)
    db.add_all([ContactAccessType(code="sorento_office", name="Sorento Office"), ContactAccessType(code="sorento_dealer", name="Sorento Dealer")])
    brand = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
    db.add(brand)
    db.add(
        ProductSpecRegistry(
            id=str(uuid.uuid4()), spec_key="product_type", label="Product type", data_type="enum",
            allowed_values=["toilet_seat", "bidet_spray"],
            synonyms={"toilet_seat": ["toilet seat"], "bidet_spray": ["bidet spray"]},
        )
    )
    db.flush()
    levels = ["sorento_office", "sorento_dealer"]
    for value, name in (("toilet_seat", "ZZT PROMO TOILET SEAT"), ("bidet_spray", "ZZT PROMO BIDET SPRAY")):
        p = product(db, company_id=DEFAULT_COMPANY_ID)
        p.brand_id = brand.id
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values={"product_type": {"value": value}}))
        db.flush()
        _promo(db, name, std, p, levels=levels)
    db.commit()
    _mcp(monkeypatch, session_factory)
    services = _unresolved(LEVELS)
    qf = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion",
        entities=[{"raw": "seat cover", "hint": "category", "canonical_code": "toilet seat", "current_message": True, "confident": True}],
    )
    menu = _turn(session_factory, monkeypatch, services, qf, "seat cover promo", "zzt-v73-seat-ask")
    assert "1. Office - has promotion" in menu.splitlines(), menu
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(1), "1", "zzt-v73-seat-pick")
    assert "ZZT PROMO TOILET SEAT" in reply, reply
    assert "ZZT PROMO BIDET SPRAY" not in reply, reply
