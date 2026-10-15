"""Chat fix PR-A red tests (AC-A6, A7, A10): grounding says unknown spec words back, the publisher
puts the live `{{specs}}` block and the category name rule into a new parser version, and a spec
menu offers only choices that lead to rows.

Plan documentation/plans/chatbot/PLAN-chat-fix-spec-16oct.md. No live LLM call: the parse is a
hand-built dict. Postgres only (session_factory), every test seeds its own rows.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from app.models.access import ContactAccessType
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.models.marketing import PromotionType
from app.models.product import Brand
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_lane_require import _seed_registry
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_promo_kind_subject import _promo
from tests.chatbot.test_promo_subject_filter import _class
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category

KEY = "chatbot_semantic_parser"


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
# AC-A7 (with A1/A2): the publisher builds the new parser version
# --------------------------------------------------------------------------- #
STATIC_SPEC_BLOCK = (
    'Specification dim_length ("Length"): a number in mm. Words: length, long.\n'
    'Specification dim_width ("Width"): a number in mm. Words: width, wide.\n'
    "Product types (the category entity keeps these, never a specification): wash basin.\n"
)
SOURCE = (
    "ZZT parser text, every line must survive.\n"
    "== MESSAGE TYPE ==\n"
    "Brand rule: a brand is one of the listed brands {{domains}}.\n"
    "Another owner line.\n"
    f"{BLOCKS_BEGIN}\n"
    "Domain inventory: stock.\n"
    f"{STATIC_SPEC_BLOCK}"
    f"{BLOCKS_END}\n"
    "Tail line.\n"
)


def _publish_source(db, version: int = 52):
    row = AIPromptVersion(
        id=str(uuid.uuid4()), name=KEY, version=version, type="text", template=SOURCE,
        variables=["domains"], config_json={}, commit_message="zzt source",
    )
    db.add(row)
    db.flush()
    db.add(AIPromptLabel(id=str(uuid.uuid4()), name=KEY, label="production", version_id=row.id))
    db.flush()
    return row


def test_a7_publish_specs_block_swaps_the_static_block_and_adds_the_category_rule(session_factory):
    from app.services import chatbot_prompt_vars as pv

    db = session_factory()
    _seed_registry(db)
    source = _publish_source(db)
    version = pv.publish_specs_block(db, from_version=52)
    assert version == 53, "a new version is published off the named source"
    new = db.query(AIPromptVersion).filter_by(name=KEY, version=version).one()
    old_lines, new_lines = SOURCE.split("\n"), new.template.split("\n")
    removed = [ln for ln in old_lines if ln not in new_lines]
    added = [ln for ln in new_lines if ln not in old_lines]
    assert all(ln.startswith("Specification ") or ln.startswith("Product types (the category entity keeps") for ln in removed), removed
    assert "{{specs}}" in added and new.template.count("{{specs}}") == 1, new.template
    rule = [ln for ln in added if ln != "{{specs}}" and ln.strip()]
    assert len(rule) == 1 and re.search(r"categor", rule[0], re.I) and re.search(r"listed|one of", rule[0], re.I), (
        f"exactly one added category name rule line expected, got {rule}"
    )
    # every other line is byte-identical and keeps its order
    kept_old = [ln for ln in old_lines if ln in new_lines]
    kept_new = [ln for ln in new_lines if ln in old_lines]
    assert kept_old == kept_new
    assert "specs" in (new.variables or [])
    label = db.query(AIPromptLabel).filter_by(name=KEY, label="production").one()
    assert label.version_id == source.id, "the production label never moves"


def test_a7_the_published_version_renders_the_live_registry_vocabulary(session_factory):
    """AC-A1: the new version takes its spec vocabulary from the registry at render time."""
    from app.services import ai_prompt_registry
    from app.services import chatbot_prompt_vars as pv

    from app.services.chatbot_parser_prompt import specification_lines

    db = session_factory()
    _seed_registry(db)
    product_type = db.query(ProductSpecRegistry).filter_by(spec_key="product_type").one()
    product_type.user_synonyms = {**(product_type.user_synonyms or {}), "grab_bar": ["zztgrabrail"]}
    db.flush()
    _publish_source(db)
    version = pv.publish_specs_block(db, from_version=52)
    row = db.query(AIPromptVersion).filter_by(name=KEY, version=version).one()
    pv.clear_cache()
    rendered, _ = ai_prompt_registry.render(db, KEY, current_date="TODAY", override_version_id=row.id)
    live = specification_lines(db)
    # (a) the block is the live registry's, exactly as `specification_lines` renders it now
    assert "\n".join(live) in rendered, rendered[-1500:]
    # (b) a word seeded into the registry reaches the render; the static block's stale line
    # (one the live registry does not produce) does not
    assert "zztgrabrail" in rendered, rendered[-1500:]
    stale = [ln for ln in STATIC_SPEC_BLOCK.splitlines() if ln not in live]
    assert stale and not [ln for ln in stale if ln in rendered.splitlines()], stale


def test_a7_publishing_twice_publishes_nothing_the_second_time(session_factory):
    from app.services import chatbot_prompt_vars as pv

    db = session_factory()
    _seed_registry(db)
    _publish_source(db)
    assert pv.publish_specs_block(db, from_version=52) == 53
    assert pv.publish_specs_block(db, from_version=52) is None


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
