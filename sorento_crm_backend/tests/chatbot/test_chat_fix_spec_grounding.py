"""Chat fix PR-A red tests (AC-A6, A10): grounding says unknown spec words back, and a spec menu
offers only choices that lead to rows. (AC-A7, the specs-block publisher and the category name
rule, moved to PR-A2 with the parser vocabulary: crew round 2.)

Plan documentation/plans/chatbot/PLAN-chat-fix-spec-16oct.md. No live LLM call: the parse is a
hand-built dict. Postgres only (session_factory), every test seeds its own rows.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product import Brand
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_lane_require import _seed_registry
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_promo_kind_subject import _promo
from tests.chatbot.test_promo_subject_filter import _class
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category

def _e(raw: str, hint: str, **extra: Any) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": extra.pop("canonical_code", None), "current_message": True, "confident": True, **extra}


# --------------------------------------------------------------------------- #
# AC-A6: an unbindable spec word is said back on every domain
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("domain", ["master_products", "inventory", "order_management"])
def test_a6_an_unbindable_spec_word_is_kept_for_say_back_on_a_non_promotion_domain(session_factory, domain):
    from app.services.chatbot.head.grounding import ground

    db = session_factory()
    _class_category(db, "WC")
    _seed_registry(db)
    db.commit()
    verdict, _notes = ground(
        db,
        {
            "domain_hint": domain,
            "entities": [
                _e("water closet", "category"),
                _e("zztwonky", "specification", spec_key=None, spec_value=None),
            ],
        },
    )
    kept = [e for e in verdict["entities"] if e.get("hint") == "specification" and e.get("raw") == "zztwonky"]
    assert kept, f"the unbindable word was dropped, so the ask answers unfiltered: {verdict['entities']}"
    assert kept[0].get("spec_value") is None, kept


def test_a6_the_promotion_path_still_keeps_it(session_factory):
    """Guard: the shape the promotion path keeps today is the shape every domain now keeps."""
    from app.services.chatbot.head.grounding import ground

    db = session_factory()
    _class_category(db, "WC")
    _seed_registry(db)
    db.commit()
    verdict, _ = ground(
        db,
        {
            "domain_hint": "promotion",
            "entities": [_e("water closet", "category"), _e("zztwonky", "specification", spec_key=None, spec_value=None)],
        },
    )
    assert [e for e in verdict["entities"] if e.get("raw") == "zztwonky" and e.get("spec_value") is None]


# --------------------------------------------------------------------------- #
# AC-A10: a spec menu offers only choices that lead to rows for the asked domain
# --------------------------------------------------------------------------- #
def _cabinet_world(session_factory, promoted: tuple[str, ...]) -> None:
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Bathroom Furniture", ("cabinet",))
    db = session_factory()
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90, is_default=True)
    db.add(std)
    db.add(ContactAccessType(code="sorento_dealer", name="Sorento Dealer"))
    brand = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
    db.add(brand)
    db.add(
        ProductSpecRegistry(
            id=str(uuid.uuid4()), spec_key="furniture_type", label="Furniture type", data_type="enum",
            allowed_values=["basin_cabinet", "side_cabinet", "tall_cabinet"],
            synonyms={"basin_cabinet": ["basin cabinet"], "side_cabinet": ["side cabinet"], "tall_cabinet": ["tall cabinet"]},
        )
    )
    db.flush()
    for value in ("basin_cabinet", "side_cabinet", "tall_cabinet"):
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT{value[:4].upper()}8")
        p.brand_id = brand.id
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values={"class": {"value": "Bathroom Furniture"}, "furniture_type": {"value": value}}))
        db.flush()
        if value in promoted:
            _promo(db, f"ZZT PROMO {value.upper()}", std, p, levels=["sorento_dealer"])
    db.commit()


def _ask_back(session_factory):
    from app.services.chatbot import turn_runtime

    parse = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion",
        entities=[_e("cabinet", "category")],
    )
    return turn_runtime.promo_subject_ask_back(parse, session_factory())


def test_a10_the_menu_offers_only_the_cabinet_types_that_have_a_promotion(session_factory):
    _cabinet_world(session_factory, promoted=("basin_cabinet", "side_cabinet"))
    result = _ask_back(session_factory)
    assert result is not None, "two promoted types still need the which-question"
    _text, options = result
    assert sorted(o.lower() for o in options) == ["basin cabinet", "side cabinet"], options


def test_a10_with_no_choice_that_has_a_promotion_it_answers_at_class_level(session_factory):
    _cabinet_world(session_factory, promoted=())
    assert _ask_back(session_factory) is None, "no cabinet type has a promotion: no menu, the class-level answer"


def test_f4_a_choice_whose_only_promotion_is_at_another_level_is_not_offered(session_factory):
    """Fix round 1 F4 (AC-A10): the menu check reads promotions at the contact's own access
    levels, the way the promotions read does; tall cabinet's only promotion is at a level the
    contact lacks, so it is not offered."""
    from app.models.product import Product
    from app.services.chatbot import turn_runtime

    _cabinet_world(session_factory, promoted=("basin_cabinet", "side_cabinet"))
    db = session_factory()
    std = db.query(PromotionType).filter_by(type_code="standard").one()
    tall = db.query(Product).filter_by(product_code="ZZTTALL8").one()
    _promo(db, "ZZT PROMO TALL OTHER LEVEL", std, tall, levels=["zzt_other_level"])
    db.commit()
    parse = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion",
        entities=[_e("cabinet", "category")],
    )
    result = turn_runtime.promo_subject_ask_back(parse, session_factory(), access_levels=["Sorento Dealer"])
    assert result is not None
    _text, options = result
    assert sorted(o.lower() for o in options) == ["basin cabinet", "side cabinet"], options
