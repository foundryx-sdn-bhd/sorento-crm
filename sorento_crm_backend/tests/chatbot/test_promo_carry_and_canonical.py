"""Reviewer should-fixes: (A) a pending tier pick's promo_filter is not merged into a new ask that
states its own level; (B2) the parser's canonical_code is not a class name for an entity whose raw
grounds on its own, nor for a product the grounding reclassified. Fake names only."""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product import Brand, Product
from app.services.chatbot.head import grounding
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn
from tests.chatbot.test_promo_kind_paths import _seed as _seed_kind_world
from tests.chatbot.test_promo_kind_subject import PROMO_TOOL, _promo, _qf
from tests.chatbot.test_promo_owner_round2 import _mcp
from tests.chatbot.test_promo_subject_filter import _class

ALL_LEVELS = ["Sorento Office", "Sorento Dealer", "Cabana Office", "Cabana Dealer"]


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_a_new_ask_with_its_own_level_does_not_inherit_the_open_menus_kind_and_brand(session_factory, monkeypatch):
    """(A) menu open for "sroento a3 flyer"; then "cabana promo for dealer" (brand Cabana, level
    dealer, no kind): the fetch carries neither a3_flyer nor Sorento's brand id."""
    ids = _seed_kind_world(session_factory, sorento="none")
    db = session_factory()
    cabana = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="Cabana", company_id=DEFAULT_COMPANY_ID)
    db.add(cabana)
    db.add_all([ContactAccessType(code="cabana_dealer", name="Cabana Dealer"), ContactAccessType(code="cabana_office", name="Cabana Office")])
    std = db.query(PromotionType).filter_by(type_code="standard").one()
    flyer = db.query(PromotionType).filter_by(type_code="a3_flyer").one()
    db.flush()
    cprod = product(db, company_id=DEFAULT_COMPANY_ID)
    cprod.brand_id = cabana.id
    sprod = db.query(Product).filter(Product.brand_id == ids["srt"]).first()
    db.flush()
    _promo(db, "ZZT CABANA PROMO DEALER", std, cprod, levels=["cabana_dealer"])
    _promo(db, "ZZT SORENTO A3 FLYER LIVE", flyer, sprod, levels=["sorento_dealer", "sorento_office"])
    db.commit()
    brand_ids = {"sorento": ids["srt"], "cabana": str(cabana.id)}

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        hits, rest = [], []
        for raw in asked:
            uid = brand_ids.get(raw.strip().lower())
            (hits if uid else rest).append((raw, uid))
        return {
            "tokens": asked,
            "resolutions": [
                {"raw": r, "token": r, "matches": [{"uuid": u, "entity_type": "brand", "canonical_code": r.title(), "match_tier": "exact"}]}
                for r, u in hits
            ],
            "unresolved_tokens": [r for r, _ in rest],
        }

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in ALL_LEVELS], resolve_entity=_resolve, probe=lambda **_: None
    )
    captured = _mcp(monkeypatch, session_factory)

    def turn(qf, text, msg_id):
        _run_turn(session_factory, monkeypatch, qf=qf, text_body=text, msg_id=msg_id, resolve_services=services)

    turn(_qf(kind="a3_flyer", brand="Sorento", typed="sorento"), "sorento a3 flyer", "zzt-cc-menu")
    n = len(captured)
    second = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion", access_levels=["dealer"],
        entities=[{"raw": "cabana", "hint": "brand", "canonical_code": "Cabana", "current_message": True, "confident": True}],
    )
    turn(second, "cabana promo for dealer", "zzt-cc-new")
    fetches = [a for name, a in captured[n:] if name == PROMO_TOOL]
    assert fetches, captured
    for a in fetches:
        assert not a.get("promotion_types"), a
        assert ids["srt"] not in (a.get("brand_ids") or []), a


# --------------------------------------------------------------------------- #
# (B2) canonical_code never replaces a raw that grounds, nor a reclassified product
# --------------------------------------------------------------------------- #


def _ground(db, entity):
    verdict = {"entities": [entity], "domain_hint": "inventory", "intent_hint": "check_stock"}
    out, _notes = grounding.ground(db, verdict, message=entity["raw"])
    return [{k: e.get(k) for k in ("raw", "hint", "spec_key", "spec_value")} for e in out["entities"]]


def _world(session_factory):
    _class(session_factory, "Tap", ("water tap",))
    _class(session_factory, "Wash Basin", ("basin",))
    return session_factory()


@pytest.mark.parametrize("hint", ["category", "product"])
def test_a_guessed_canonical_code_never_replaces_a_raw_that_grounds_on_its_own(session_factory, hint):
    """(B2) "water tap basin" grounds (as one unknown product type) from its raw. A canonical_code
    "GMB-XYZ" the parser guessed beside it (hint category, or a product the grounding reclassifies
    to category) must change nothing."""
    db = _world(session_factory)
    entity = {"raw": "water tap basin", "hint": hint, "canonical_code": None, "current_message": True, "confident": True}
    baseline = _ground(db, entity)
    guessed = _ground(db, {**entity, "canonical_code": "GMB-XYZ"})
    assert baseline and baseline[0]["spec_key"] == "product_type", baseline
    assert guessed == baseline, (guessed, baseline)
