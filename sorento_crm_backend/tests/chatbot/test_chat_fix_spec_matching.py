"""Chat fix PR-A red tests (AC-A3..A5, A8, A9): spec matching comes from the registry's data.
(AC-A1/A2, the parser's rendered spec vocabulary, moved to PR-A2 with the render: crew round 2.)

Plan documentation/plans/chatbot/PLAN-chat-fix-spec-16oct.md, UAC chat-fix-spec-acceptance-criteria.md.
No live LLM call: the parser is stubbed by handing the search the entities it would emit.
Postgres only (blank_session); every test seeds its own rows.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services import chatbot_prompt_vars
from app.services.product_class_signal import ClassVocabulary, backfill_category_signals
from app.services.product_predicate_service import resolve_product_set
from app.services.product_spec_derivation import derive_for_code
from app.services.product_spec_registry import seed_spec_registry
from app.services.product_spec_search import filter_specs
from tests._pg_fixture import blank_session

_REFS: dict = {}


@pytest.fixture
def db():
    with blank_session() as s:
        cats = {
            key: ProductCategory(id=str(uuid.uuid4()), category_code=code, category_name=code)
            for key, code in (("tub", "SRT-ZZTUB"), ("wc", "SRT-WC"), ("sh", "SRT-SH"))
        }
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZT-PCS", uom_name="Piece")
        s.add_all([*cats.values(), uom])
        s.flush()
        backfill_category_signals(s)
        seed_spec_registry(s)
        _REFS.update({k: v.id for k, v in cats.items()} | {"uom": uom.id})
        chatbot_prompt_vars.clear_cache()
        yield s
        chatbot_prompt_vars.clear_cache()


def _product(db, code, name, description, *, category="tub"):
    row = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=name,
        description=description,
        category_id=_REFS[category],
        base_uom_id=_REFS["uom"],
        list_price=Decimal("1.00"),
    )
    db.add(row)
    db.flush()
    derive_for_code(db, code)
    return row


def _values(db, product) -> dict:
    return db.query(ProductSpecifications).filter_by(product_id=product.id).one().values


def _codes(result) -> set[str]:
    return {c["product_code"] for c in result["candidates"]}


def _registry(db, key) -> ProductSpecRegistry:
    return db.query(ProductSpecRegistry).filter_by(spec_key=key).one()


# --------------------------------------------------------------------------- #
# AC-A3: no class word list in code; every word it held is served from data
# --------------------------------------------------------------------------- #
#: Frozen from `product_class_signal.CLASS_SYNONYMS` at origin/main ccb1ce9820 (word -> class).
FROZEN_CLASS_WORDS: dict[str, list[str]] = {
    "Kitchen Sink": ["kitchen sink", "sink", "sinki", "dapur"],
    "Tap": ["tap", "faucet", "mixer", "paip", "kepala paip", "water tap"],
    "Wash Basin": ["wash basin", "basin", "washbasin", "sink basin", "besen", "water basin"],
    "Water Closet": ["water closet", "wc", "toilet", "toilet bowl", "closet", "tandas"],
    "Shower": ["shower", "shower head", "rain shower", "hand shower", "pancuran"],
    "Bathroom Furniture": ["cabinet", "mirror", "vanity", "bathroom cabinet", "kabinet"],
    "Bathtub": ["bathtub", "bath tub", "tub"],
    "Jacuzzi": ["jacuzzi", "whirlpool", "spa bath"],
    "Bathtub and Jacuzzi": ["bathtub", "bath tub", "jacuzzi", "tub", "shower screen"],
    "Bathroom Accessory": [
        "bathroom accessory", "towel rail", "towel bar", "soap holder", "paper holder", "robe hook",
    ],
    "Cloth Hanger": ["cloth hanger", "clothes hanger", "hanger", "penyidai"],
    "Urinal": ["urinal", "urinary"],
    "Flexible Hose": ["flexible hose", "hose", "shower tube", "hos"],
}
_FROZEN_PAIRS = [(word, label) for label, words in FROZEN_CLASS_WORDS.items() for word in words]


def test_a3_class_synonyms_constant_is_gone():
    from app.services import product_class_signal

    assert not hasattr(product_class_signal, "CLASS_SYNONYMS"), "the class word list must live in the registry, not in code"


@pytest.mark.parametrize("word,label", _FROZEN_PAIRS, ids=[f"{w}->{l}" for w, l in _FROZEN_PAIRS])
def test_a3_every_word_the_code_list_held_is_served_from_data(db, word, label):
    """The test DB holds no categories at all, so the only data that can serve the word is the
    registry's `class` row as `seed_spec_registry` (the migration's mirror) ships it."""
    for row in db.query(ProductCategory).all():
        db.delete(row)
    db.flush()
    assert label in ClassVocabulary(db).resolve(word), f"{word!r} no longer resolves to {label!r}"


# --------------------------------------------------------------------------- #
# AC-A5: has_massage, a registry yes/no key read off the catalogue description
# --------------------------------------------------------------------------- #
def test_a5_has_massage_is_a_boolean_registry_key(db):
    assert _registry(db, "has_massage").data_type == "boolean"


# Fix round 1 F2 (lead): the jet words carry the flag, the bare class name JACUZZI does not.
@pytest.mark.parametrize("phrase", ["MASSAGE", "WHIRLPOOL", "AIR BUBBLE", "JETS"])
def test_a5_a_tub_description_naming_massage_carries_the_flag(db, phrase):
    tub = _product(db, f"ZZT-TUB-{phrase[:3]}", "ZZT SORENTO BATHTUB", f"ZZT SORENTO BATHTUB WITH {phrase}")
    assert _values(db, tub).get("has_massage", {}).get("value") is True


def test_a5_a_plain_tub_does_not_carry_the_flag(db):
    tub = _product(db, "ZZT-TUB-PLAIN", "ZZT SORENTO BATHTUB", "ZZT SORENTO BATHTUB 1700X800X600MM")
    assert _values(db, tub).get("has_massage", {}).get("value") is not True


def _jacuzzi_category(db) -> None:
    """A category coded like the catalogue's tub filing (`-JC`): its class is the broad
    "Bathtub and Jacuzzi", as on the live catalogue."""
    cat = ProductCategory(id=str(uuid.uuid4()), category_code="SRT-JC", category_name="SRT-JC")
    db.add(cat)
    db.flush()
    backfill_category_signals(db)
    _REFS["jc"] = cat.id


def test_f2_an_accessory_filed_with_the_tubs_never_carries_the_flag(db):
    """Fix round 1 F2 (live gate: SRTJC-PILLOW "JACUZZI PILLOW", SRTJC102-PARTS): the word
    JACUZZI in an accessory's name is not a massage jet."""
    _jacuzzi_category(db)
    pillow = _product(db, "ZZT-JC-PILLOW", "ZZT SORENTO JACUZZI PILLOW", "ZZT SORENTO JACUZZI PILLOW", category="jc")
    parts = _product(db, "ZZT-JC102-PARTS", "ZZT SORENTO JACUZZI PARTS", "ZZT SORENTO JACUZZI PARTS", category="jc")
    assert _values(db, pillow).get("has_massage", {}).get("value") is not True
    assert _values(db, parts).get("has_massage", {}).get("value") is not True


def test_f2_a_jacuzzi_is_a_tub_with_massage_jets(db):
    """A product whose own class is Jacuzzi carries the flag: the registry's class meaning
    is "bathtub with massage jets" (owner ruling 16 Oct)."""
    _jacuzzi_category(db)
    jacuzzi = _product(db, "ZZT-JC-TUB", "ZZT SORENTO JACUZZI", "ZZT SORENTO JACUZZI 1700X800X650MM", category="jc")
    assert _values(db, jacuzzi).get("has_massage", {}).get("value") is True


def test_a5_a_water_closet_or_shower_set_naming_those_words_never_carries_it(db):
    wc = _product(db, "ZZT-WC-JET", "ZZT SORENTO WATER CLOSET", "ZZT SORENTO WATER CLOSET WITH WHIRLPOOL FLUSH AIR BUBBLE", category="wc")
    shower = _product(db, "ZZT-SH-JET", "ZZT SORENTO SHOWER SET", "ZZT SORENTO SHOWER SET WITH MASSAGE JACUZZI STYLE", category="sh")
    assert _registry(db, "has_massage") is not None
    assert _values(db, wc).get("has_massage", {}).get("value") is not True
    assert _values(db, shower).get("has_massage", {}).get("value") is not True


# --------------------------------------------------------------------------- #
# AC-A4: bathtub is not jacuzzi; "bathtub with massage jet" is
# --------------------------------------------------------------------------- #
def _tub_world(db):
    return {
        "plain": _product(db, "ZZT-T-PLAIN", "ZZT SORENTO BATHTUB", "ZZT SORENTO BATHTUB 1700X800X600MM"),
        "jetted": _product(db, "ZZT-T-JETTED", "ZZT SORENTO MASSAGE BATHTUB", "ZZT SORENTO BATHTUB WITH MASSAGE JETS"),
        "jac1": _product(db, "ZZT-J-ONE", "ZZT SORENTO WHIRLPOOL JACUZZI", "ZZT SORENTO JACUZZI WITH MASSAGE JETS"),
        "jac2": _product(db, "ZZT-J-TWO", "ZZT SORENTO BUBBLE JACUZZI", "ZZT SORENTO JACUZZI WITH AIR BUBBLE"),
    }


def test_a4_a_plain_bathtub_ask_returns_bathtubs_and_no_jacuzzi_class_product(db):
    world = _tub_world(db)
    out = resolve_product_set(db, require={}, scope_terms=["bathtub"])
    codes = _codes(out)
    assert world["plain"].product_code in codes, out
    assert not codes & {world["jac1"].product_code, world["jac2"].product_code}, f"a Jacuzzi-class product answered 'bathtub': {codes}"


def test_a4_massage_jet_bathtub_as_the_has_massage_flag_returns_only_jetted_tubs(db):
    """The parse of "Any bathtub with massage jet?" as the registry-fed parser emits it: the yes/no key."""
    world = _tub_world(db)
    out = resolve_product_set(db, require={}, specs=[{"key": "has_massage", "value": True}])
    assert _codes(out) == {world[k].product_code for k in ("jetted", "jac1", "jac2")}, out


def test_a4_massage_jet_bathtub_as_the_jacuzzi_class_returns_only_jacuzzi_class_tubs(db):
    """The same ask parsed to the Jacuzzi class word (the registry says jacuzzi means a tub with jets)."""
    world = _tub_world(db)
    out = resolve_product_set(db, require={}, scope_terms=["jacuzzi"])
    assert _codes(out) == {world["jac1"].product_code, world["jac2"].product_code}, out


# --------------------------------------------------------------------------- #
# AC-A8: name-matched ids and the spec clause combine with AND
# --------------------------------------------------------------------------- #
def test_a8_name_matched_ids_are_intersected_with_the_spec_clause(db):
    p_trap = _product(db, "ZZT-WC-P", "ZZT SORENTO WATER CLOSET", "ZZT SORENTO CERAMIC WATER CLOSET P-TRAP", category="wc")
    s_trap = _product(db, "ZZT-WC-S", "ZZT SORENTO WATER CLOSET", "ZZT SORENTO CERAMIC WATER CLOSET S-TRAP", category="wc")
    out = resolve_product_set(
        db,
        require={},
        specs=[{"key": "trap_type", "value": "p_trap"}],
        product_ids=[p_trap.id, s_trap.id],
    )
    assert _codes(out) == {"ZZT-WC-P"}, out
    assert out["qualifying_total"] == 1, out


# --------------------------------------------------------------------------- #
# AC-A9: a yes/no spec value filters
# --------------------------------------------------------------------------- #
def test_a9_a_boolean_spec_value_produces_a_filter_clause(db):
    found = filter_specs(db, specs=[{"key": "is_smart", "value": True}])
    assert found["clause"] is not None, "is_smart=true was dropped, so the ask answered unfiltered"


def test_a9_a_boolean_spec_value_narrows_the_set(db):
    smart = _product(db, "ZZT-WC-SMART", "ZZT SORENTO WATER CLOSET", "ZZT SORENTO INTELLIGENT WATER CLOSET", category="wc")
    _product(db, "ZZT-WC-PLAIN", "ZZT SORENTO WATER CLOSET", "ZZT SORENTO CERAMIC WATER CLOSET", category="wc")
    out = resolve_product_set(db, require={}, specs=[{"key": "is_smart", "value": True}], scope_terms=["water closet"])
    assert _codes(out) == {smart.product_code}, out


# --------------------------------------------------------------------------- #
# Fix round 1 F1 (AC-A4, W11): a yes/no spec asked with a class reaches the class
# family the spec's own scope holds; a plain class ask does not move
# --------------------------------------------------------------------------- #
def test_f1_a_bathtub_ask_with_the_massage_flag_reaches_every_jetted_tub(db):
    """The parse the live gate saw for "Any bathtub with massage jet?": category bathtub +
    has_massage. The Jacuzzi class (most jetted tubs) is in the answer."""
    from app.services.chatbot.lanes.business.answer import set_noun_for

    world = _tub_world(db)
    out = resolve_product_set(db, require={}, specs=[{"key": "has_massage", "value": True}], scope_terms=["bathtub"])
    assert _codes(out) == {world[k].product_code for k in ("jetted", "jac1", "jac2")}, out
    # the header names the asked class, never "massage jets products"
    assert set_noun_for(out["class_labels"]) == "bathtubs", out["class_labels"]


def test_f1_a_flag_never_widens_to_a_class_the_asked_one_shares_no_word_with(db):
    """is_rimless is scoped to Water Closet, Urinal and Squatting Pan; "rimless water
    closet" is still water closets only."""
    wc = _product(db, "ZZT-WC-RIM", "ZZT SORENTO RIMLESS WATER CLOSET", "ZZT SORENTO RIMLESS WATER CLOSET", category="wc")
    urinal = _product(db, "ZZT-UR-RIM", "ZZT SORENTO RIMLESS URINAL", "ZZT SORENTO RIMLESS URINAL", category="wc")
    assert _values(db, urinal).get("is_rimless", {}).get("value") is True
    out = resolve_product_set(db, require={}, specs=[{"key": "is_rimless", "value": True}], scope_terms=["water closet"])
    assert _codes(out) == {wc.product_code}, out


def test_r2_a_key_holding_a_yes_and_the_word_true_never_crashes_the_membership(db):
    """Reviewer R2 on PR-A: one key can arrive as the boolean and as its word (a grounded
    `True` beside a scope term's "true"); the membership echo sorts them as text."""
    found = filter_specs(db, specs=[{"key": "is_smart", "value": True}, {"key": "is_smart", "value": "true"}])
    assert found["membership"]["is_smart"], found
