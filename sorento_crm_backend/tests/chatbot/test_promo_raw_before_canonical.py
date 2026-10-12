"""The typed words win when they themselves name a registry value / class / type; the parser's
canonical_code is used only when the raw does not ground exactly. Fake names only."""
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
from tests.chatbot.test_promo_kind_subject import _promo
from tests.chatbot.test_promo_owner_round2 import LEVELS, _furniture_world, _mcp, _turn, _unresolved
from tests.chatbot.test_promo_subject_filter import _class, _pick_qf


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _qf(raw: str, canonical: str):
    return _parser_output(
        domain_hint="promotion", intent_hint="check_promotion",
        entities=[{"raw": raw, "hint": "category", "canonical_code": canonical, "current_message": True, "confident": True}],
    )


def test_raw_that_is_a_registry_value_wins_over_the_parsers_canonical_class(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    """(1) raw "basin cabinet" (furniture_type basin_cabinet), canonical "bathroom cabinet" (a
    synonym of the Bathroom Furniture class): menu "for basin cabinet", pick returns B not M."""
    _furniture_world(session_factory)  # basin / side / tall cabinet promos, all bathroom furniture
    _class(session_factory, "Bathroom Furniture", ("bathroom cabinet",))
    _mcp(monkeypatch, session_factory)
    services = _unresolved(LEVELS)
    qf = _qf("basin cabinet", "bathroom cabinet")
    menu = _turn(session_factory, monkeypatch, services, qf, "basin cabinet promo", "zzt-rbc-ask")
    assert menu.startswith("Which access level do you need for basin cabinet?"), menu
    assert "1. Office - has promotion" in menu.splitlines(), menu
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(1), "1", "zzt-rbc-pick")
    assert "ZZT PROMO BASIN_CABINET" in reply, reply
    assert "ZZT PROMO SIDE_CABINET" not in reply and "ZZT PROMO TALL_CABINET" not in reply, reply


def test_canonical_still_grounds_a_raw_that_does_not_ground_on_its_own(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    """(2) control: raw "seat cover" grounds to nothing, canonical "toilet seat" is a registry
    product_type value: the filter is the toilet seat one."""
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
    for value, name in (("toilet_seat", "ZZT PROMO TOILET SEAT"), ("bidet_spray", "ZZT PROMO BIDET SPRAY")):
        p = product(db, company_id=DEFAULT_COMPANY_ID)
        p.brand_id = brand.id
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values={"product_type": {"value": value}}))
        db.flush()
        _promo(db, name, std, p, levels=["sorento_office", "sorento_dealer"])
    db.commit()
    _mcp(monkeypatch, session_factory)
    services = _unresolved(LEVELS)
    menu = _turn(session_factory, monkeypatch, services, _qf("seat cover", "toilet seat"), "seat cover promo", "zzt-rbc-seat-ask")
    assert "1. Office - has promotion" in menu.splitlines(), menu
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(1), "1", "zzt-rbc-seat-pick")
    assert "ZZT PROMO TOILET SEAT" in reply and "ZZT PROMO BIDET SPRAY" not in reply, reply
