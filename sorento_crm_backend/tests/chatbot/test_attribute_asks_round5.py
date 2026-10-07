"""Reviewer pass on PR #833 round 4 at d6fa2b31 (26 Sep 2026): B1, S1 to S3, N1 and N2.
N3 is in the brands vitest, N4 in the MCP presenter tests.

Every test here failed on d6fa2b31e for the scenario the reviewer named; the replay of the
owner's eight exchanges (round 4) stays green beside them.
"""
from __future__ import annotations

import importlib

import pytest

from tests.chatbot.test_attribute_asks_round2 import _product
from tests.chatbot.test_attribute_asks_round3 import _ask
from tests.chatbot.test_attribute_asks_round4 import (  # noqa: F401 - fixtures used by name
    _product_ask,
    _rows,
    chat,
    world,
)
from tests.chatbot.test_attribute_asks_round3 import world as r3world  # noqa: F401 - round 4's world builds on it
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import _stock_for
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category, _incoming_for

# --------------------------------------------------------------------------- #
# B1: R6 fires only on a word in a value position, never on a product word      #
# --------------------------------------------------------------------------- #

#: The reviewer's six phrases: ordinary product words in front of a head word. Each was a
#: normal product search before round 4 and a dead end ("I don't know 'deck mounted' as a
#: mounting") at d6fa2b31.
REVIEWER_PHRASES = (
    "any deck mounted bath mixer",
    "which long spout basin tap has stock",
    "ceiling mounted shower",
    "rain shower ceiling mount",
    "any grease trap",
    "any click clack waste",
)


@pytest.mark.parametrize("phrase", REVIEWER_PHRASES)
def test_b1_a_plain_word_before_a_head_word_is_no_unknown_value(phrase):
    from app.services.product_spec_registry import seed_spec_registry
    from app.services.product_spec_search import unknown_spec_values
    from tests._pg_fixture import pg_session

    with pg_session() as db:
        seed_spec_registry(db)
        assert unknown_spec_values(db, phrase) == [], phrase


def test_b1_a_letter_or_a_misspelt_value_is_still_said_back():
    """The owner's "t trap" and a one-letter slip of a known value stay unknown values."""
    from app.services.product_spec_registry import seed_spec_registry
    from app.services.product_spec_search import unknown_spec_values
    from tests._pg_fixture import pg_session

    with pg_session() as db:
        seed_spec_registry(db)
        assert [u["said"] for u in unknown_spec_values(db, "any water clost t trap?")] == ["t trap"]
        assert [u["said"] for u in unknown_spec_values(db, "any wll mounted basin")] == ["wll mounted"]


def test_b1_a_phrase_that_names_a_product_is_searched(world):
    """A value-shaped phrase that is some product's own name is that product, not an
    unknown value."""
    from app.services.product_spec_search import unknown_spec_values

    db = world["db"]
    uom = world["srt_tubs"][0].base_uom_id
    _product(
        db,
        brand_id=world["sorento"].id,
        category_id=_class_category(db, "WC"),
        uom_id=uom,
        noun="WATER CLOSET",
        prefix="ZZR5X",
        name="Sorento X Trap Water Closet",
    )
    db.commit()
    assert unknown_spec_values(db, "any x trap water closet") == []


def test_b1_a_deck_mounted_bath_mixer_ask_is_searched(chat, world):
    """The reviewer's chat-level probe: the product ask reaches the search instead of
    "I don't know 'deck mounted' as a mounting".

    ROGUE-CONVO-REVIEW PR-1 (owner ruling Q2, 6 Oct 2026): the tier that places a product
    WORD with no code is the resolver's embedding tier, which this VM has no key for
    (`Tier-3 query embedding failed`), so here the phrase stays unplaced. Until that PR
    an unplaced product token in `master_products` ran the catalogue with no filter at
    all, and the dump happened to contain the mixer, so this test was green by the read
    it is now forbidden to produce (`gate.REFUSES_EMPTY_FETCH`, AC-RC-A5). The search
    outcome is one of two: the product listed when the search places it, or the miss
    that names the phrase AS TYPED when it does not; both are "searched", neither is the
    unknown-value dead end this test guards."""
    db = world["db"]
    mixer = _product(
        db,
        brand_id=world["sorento"].id,
        category_id=_class_category(db, "FT"),
        uom_id=world["srt_tubs"][0].base_uom_id,
        noun="DECK MOUNTED BATH MIXER",
        prefix="ZZR5M",
        name="Sorento Deck Mounted Bath Mixer",
    )
    _stock_for(db, product_id=mixer.id, warehouse_id=world["warehouse"].id)
    db.commit()
    text = chat.say("any deck mounted bath mixer", _product_ask("deck mounted bath mixer", "any deck mounted bath mixer"))
    assert "I don't know" not in text, text
    # Without an embedding key (this VM, CI) only the miss half can hold; with one the
    # Tier-3 hit places the mixer and the listed half does. Either is "searched".
    assert mixer.product_code in text or '"deck mounted bath mixer"' in text, text
    assert "That would search every product" not in text, text


def test_b1_a_long_spout_set_ask_is_counted(chat, world):
    """The set path: "long spout" is a product word, so the tap set is counted."""
    text = chat.say(
        "which long spout basin tap has stock",
        _ask("stock", "long spout basin tap", "which long spout basin tap has stock"),
    )
    assert "I don't know" not in text, text


# --------------------------------------------------------------------------- #
# S1: a near miss counts list values too                                        #
# --------------------------------------------------------------------------- #


def _dual_finish(db, product, finishes: list[str]) -> None:
    from app.services.product_spec_write import apply_spec_values

    apply_spec_values(
        db,
        product.product_code,
        [{"spec_key": "finish", "op": "set", "value": finishes}],
        actor={"id": None, "name": "round 5 test"},
        commit=False,
    )


def test_s1_a_set_whose_only_other_finish_is_a_dual_finish_says_it(chat, world):
    """The reviewer's scenario: every other-finish product carries two finishes, which the
    membership clause matches by containment, so "no water closets have incoming stock in
    any finish" was false."""
    db = world["db"]
    wc = _product(
        db,
        brand_id=world["sorento"].id,
        category_id=_class_category(db, "WC"),
        uom_id=world["srt_tubs"][0].base_uom_id,
        noun="WATER CLOSET",
        prefix="ZZR5D",
        name="Sorento Dual Finish WC",
    )
    _dual_finish(db, wc, ["rose_gold", "black"])
    _incoming_for(db, product_id=wc.id)
    db.commit()
    text = chat.say(
        "any gunmetal water closet has incoming",
        _ask("incoming", "gunmetal water closet", "any gunmetal water closet has incoming"),
    )
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: no explanations, the one reply structure): the other finishes are the
    # breakdown lines; the dual-finish product is listed under each of its finishes.
    assert text == (
        "Here's what you want: gunmetal water closets with incoming stock\n"
        "• Black water closets: 1\n"
        "• Rose gold water closets: 1\n\n"
        "Couldn't find: gunmetal (finish or colour). Would you like me to escalate to purchasing team?"
    ), text


def test_s1_a_dual_finish_product_is_counted_once_beside_the_scalar_ones(chat, world):
    db = world["db"]
    tub = _product(
        db,
        brand_id=world["sorento"].id,
        category_id=_class_category(db, "BT"),
        uom_id=world["srt_tubs"][0].base_uom_id,
        noun="BATHTUB",
        prefix="ZZR5B",
        name="Sorento Dual Finish Tub",
    )
    _dual_finish(db, tub, ["rose_gold", "black"])
    _incoming_for(db, product_id=tub.id)
    db.commit()
    text = chat.say(
        "any gunmetal bathtub has incoming",
        _ask("incoming", "gunmetal bathtub", "any gunmetal bathtub has incoming"),
    )
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: no explanations, the one reply structure): one line per other finish.
    assert "• White bathtubs: 2\n• Black bathtubs: 1\n• Rose gold bathtubs: 1\n" in text, text


# --------------------------------------------------------------------------- #
# S2: one display helper for spec values                                        #
# --------------------------------------------------------------------------- #


def test_s2_the_dealer_kit_tag_reads_values_through_the_registry_helper(monkeypatch):
    """The tag and the chatbot share one helper and one acronym list; the tag keeps its
    title case."""
    from app.services import product_spec_registry
    from app.services.dealer_kit import tag_data_service

    monkeypatch.setattr(product_spec_registry, "SPEC_ACRONYMS", product_spec_registry.SPEC_ACRONYMS | {"wc"})
    assert tag_data_service._spec_display_value("wc_seat") == "WC Seat"
    assert product_spec_registry.display_spec_value("wc_seat") == "WC seat"
    monkeypatch.undo()
    assert product_spec_registry.display_spec_value("stainless_steel") == "Stainless steel"
    assert product_spec_registry.display_spec_value("stainless_steel", title_case=True) == "Stainless Steel"
    assert tag_data_service._spec_display_value("stainless_steel") == "Stainless Steel"
    assert tag_data_service._spec_display_value("pvc_pipe") == "PVC Pipe"


# --------------------------------------------------------------------------- #
# S3: the lane's migration tests leave the xdist pool                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "module",
    ["tests.test_migration_bcw_0001_brand_chatbot_weight", "tests.test_migration_bcd_0001_brand_chatbot_default"],
)
def test_s3_the_brand_migration_tests_are_serial_ddl(module):
    marks = getattr(importlib.import_module(module), "pytestmark", [])
    marks = marks if isinstance(marks, list) else [marks]
    assert any(m.name == "serial_ddl" for m in marks), module


# --------------------------------------------------------------------------- #
# N1: a list value's display_value                                              #
# --------------------------------------------------------------------------- #


def test_n1_a_list_value_reads_as_its_values_in_plain_words(world):
    from app.services.product_service import ProductService

    db = world["db"]
    tap = world["taps"][1]
    _dual_finish(db, tap, ["rose_gold", "black"])
    db.flush()
    specs = ProductService(db).spec_list_for_products([tap.id])[str(tap.id)]
    [finish] = [s for s in specs if s["key"] == "finish"]
    assert finish["display_value"] in ("Rose gold / Black", "Black / Rose gold"), finish


# --------------------------------------------------------------------------- #
# N2: an acronym value keeps its capitals in the miss sentence                  #
# --------------------------------------------------------------------------- #


def test_n2_an_acronym_value_is_not_lower_cased():
    from app.services.chatbot.lanes.business.answer import near_miss_sentence

    near = {"class_labels": ["Wash basin"], "label": "Material", "value": "PVC", "other_total": 0}
    text = near_miss_sentence(near, {"incoming": True})
    assert text.startswith("No PVC wash basins with incoming stock"), text
    near = {**near, "value": "Gunmetal"}
    assert near_miss_sentence(near, {"incoming": True}).startswith("No gunmetal wash basins"), near

