"""Fix round 8 on PR #833, F1 GROUNDING (owner retest of round 7, 27 Sep 2026).

The owner's words (verbatim): "when i ask "any gunmetal basin has incoming?" it is kinda
sus, then i follow up with cert also kinda sus, then when i ask about pink colour water
closet also knida sus, thicnkess also kinda sus, is it the parser is not boudned by what
the system has as a spec? like it doens't know colour is colour one meh, why it become
document type one, I am interseting to know what you have done to make this smarter (I
don't want you to make it smarter for this case only, it should be general)"

These tests run the grounding step (`app/services/chatbot/head/grounding.py`) over the
parser's entities exactly as the owner's turn traces read them, then over descriptor
phrases GENERATED from the specification registry itself (a colour, a finish, a mounting,
a trap, a thickness, a size, a material, each with a product type), so the rule is proven
for the registry and not for the owner's four words. The prompt half (the parser told the
registry) is pinned in `test_spec_grounding_prompt.py`.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_lane_require import _seed_registry
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category


@pytest.fixture()
def vdb(session_factory):
    """The registry seed, the four class categories the owner's messages name, and the
    two document types the traces mapped descriptors onto."""
    from app.models.resources import AttachmentType

    db = session_factory()
    for suffix in ("WC", "WB", "KS", "FT"):
        _class_category(db, suffix)
    _seed_registry(db)
    for name in ("Product Photos", "Technical Drawing"):
        if db.query(AttachmentType).filter(AttachmentType.type_name == name).first() is None:
            db.add(AttachmentType(id=str(uuid.uuid4()), type_name=name, allowed_extensions="pdf,jpg"))
    db.commit()
    yield db
    db.close()


def _e(raw: str, hint: str, **extra: Any) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": extra.pop("canonical_code", None), "current_message": True, "confident": True, **extra}


def _ground(db, *entities: dict[str, Any]) -> list[dict[str, Any]]:
    from app.services.chatbot.head.grounding import ground

    verdict, _notes = ground(db, {"entities": list(entities)})
    return verdict["entities"]


def _specs(entities) -> list[tuple[str, Any]]:
    return [(e["spec_key"], e["spec_value"]) for e in entities if e["hint"] == "specification"]


def _kinds(entities) -> list[tuple[str, str]]:
    return [(e["hint"], e["raw"]) for e in entities]


def _kept_as_document(out, document) -> None:
    """ATTACH-GROUNDING-TYPO option (c) (owner, 6 Oct 2026): a confident attachment_type
    whose code is a listed document type is the parser's, kept as filed, never re-read."""
    assert document in out, out
    assert not [e for e in out if e["hint"] == "specification"], out


# --------------------------------------------------------------------------- #
# The owner's four messages, as the traces read them                            #
# --------------------------------------------------------------------------- #


def test_item2_gunmetal_basin_is_a_basin_with_a_gunmetal_finish(vdb):
    """"any gunmetal basin has incoming?" -> [category "gunmetal basin"]."""
    out = _ground(vdb, _e("gunmetal basin", "category"))
    assert ("category", "basin") in _kinds(out), out
    assert _specs(out) == [("finish", "gunmetal")], out
    [spec] = [e for e in out if e["hint"] == "specification"]
    assert spec["raw"] == "gunmetal" and spec["spec_label"] == "Finish or colour", spec


def test_item3_f_trap_is_an_unknown_trap(vdb):
    """"any water closet f trap?". Read as the category's words, "f trap" is an unknown
    trap. Filed by the parser as a technical drawing, it is the parser's (option (c))."""
    document = _e("f trap", "attachment_type", canonical_code="technical drawing")
    _kept_as_document(_ground(vdb, _e("water closet", "category"), document), document)
    out = _ground(vdb, _e("water closet f trap", "category"))
    assert ("category", "water closet") in _kinds(out), out
    [spec] = [e for e in out if e["hint"] == "specification"]
    assert spec["spec_key"] == "trap_type" and spec["spec_value"] is None, spec
    assert spec["raw"] == "f trap", spec
    assert spec["spec_known"] == ["P trap", "S trap"], spec


def test_item4_pink_colour_is_an_unknown_finish_or_colour(vdb):
    """"any pink colour water closet?". Read as the category's words, pink is an unknown
    finish. Filed by the parser as a photo, it is the parser's (option (c))."""
    document = _e("pink colour", "attachment_type", canonical_code="photo")
    _kept_as_document(_ground(vdb, _e("water closet", "category"), document), document)
    out = _ground(vdb, _e("pink colour water closet", "category"))
    [spec] = [e for e in out if e["hint"] == "specification"]
    assert (spec["spec_key"], spec["spec_value"], spec["raw"]) == ("finish", None, "pink"), spec
    assert "Gunmetal" in spec["spec_known"] and "Chrome" in spec["spec_known"], spec


def test_item5_thickness_1_2_mm_is_a_thickness_and_kitchen_sink_a_kitchen_sink(vdb):
    """"any kitchen sink with thickness 1.2 mm" -> [category "kitchen sink",
    attachment_type "thickness 1.2 mm" canonical "technical drawing"]."""
    document = _e("thickness 1.2 mm", "attachment_type", canonical_code="technical drawing")
    _kept_as_document(_ground(vdb, _e("kitchen sink", "category"), document), document)
    out = _ground(vdb, _e("kitchen sink thickness 1.2 mm", "category"))
    assert ("category", "kitchen sink") in _kinds(out), out
    [spec] = [e for e in out if e["hint"] == "specification"]
    assert (spec["spec_key"], spec["spec_value"], spec["spec_unit"]) == ("thickness", 1.2, "mm"), spec


def test_item5_misspelt_kitchne_and_thicnkess_bind_nothing(vdb):
    """Fix round 10 on PR #833 (owner, 28 Sep 2026: "for #833 yeah exact only"). Round 8
    read "kitchne sink" as a kitchen sink and "thicnkess" as thickness, one typo apart
    (`_near`, retired). Neither is the catalogue's word: nothing binds, never a document."""
    document = _e("thicnkess 1.2 mm", "attachment_type", canonical_code="technical drawing")
    kept = _ground(vdb, _e("kitchne sink", "category"), document)
    assert document in kept and not [s for s in _specs(kept) if s[1] is not None], kept
    out = _ground(vdb, _e("kitchne sink thicnkess 1.2 mm", "category"))
    assert ("category", "kitchen sink") not in _kinds(out), out
    assert not [s for s in _specs(out) if s[1] is not None], out


def test_undermount_basin_is_an_under_counter_basin(vdb):
    out = _ground(vdb, _e("undermount basin", "category"))
    assert ("category", "basin") in _kinds(out), out
    assert _specs(out) == [("mounting", "under_counter")], out


def test_a_parser_specification_is_checked_against_the_registry(vdb):
    ok = _ground(vdb, _e("gun metal", "specification", spec_key="finish", spec_value="Gunmetal"))
    assert _specs(ok) == [("finish", "gunmetal")], ok
    unknown = _ground(vdb, _e("pink", "specification", spec_key="finish", spec_value="pink"))
    assert _specs(unknown) == [("finish", None)], unknown
    # A key the registry does not have is never passed through: the raw words are grounded.
    wrong_key = _ground(vdb, _e("gunmetal", "specification", spec_key="colour_code", spec_value="gm"))
    assert _specs(wrong_key) == [("finish", "gunmetal")], wrong_key


@pytest.mark.parametrize(
    "raw,code",
    [
        ("cert", "certificate"),
        ("certificate", "certificate"),
        ("technical drawing", "technical drawing"),
        ("photo", "photo"),
        ("photos", "photo"),
        ("sijil", "certificate"),
        ("PPS cert", "certificate"),
    ],
)
def test_a_listed_document_code_stays_a_document_type(vdb, raw, code):
    entity = _e(raw, "attachment_type", canonical_code=code)
    out = _ground(vdb, _e("basin", "category"), entity)
    assert out == [_e("basin", "category"), entity], out


@pytest.mark.parametrize("raw", ["water closet", "basin", "wash basin", "kitchen tap", "close coupled water closet"])
def test_a_class_word_with_no_descriptor_is_left_byte_identical(vdb, raw):
    entity = _e(raw, "category")
    assert _ground(vdb, entity) == [entity]


def test_a_registry_phrase_spanning_the_class_word_keeps_the_class(vdb):
    """"pillar tap" is a mounting word that ends in the class noun: the tap stays the
    product type and pillar is its mounting."""
    out = _ground(vdb, _e("pillar tap", "category"))
    assert ("category", "tap") in _kinds(out) and _specs(out) == [("mounting", "pillar_mounted")], out


def test_a_descriptor_no_key_holds(vdb):
    """Filed by the parser as a photo, "zebra" is the parser's (option (c)); read as the
    category's words it is a word nothing matched, with no key."""
    document = _e("zebra", "attachment_type", canonical_code="photo")
    _kept_as_document(_ground(vdb, _e("water closet", "category"), document), document)
    unlisted = _ground(vdb, _e("water closet", "category"), _e("zebra", "attachment_type", canonical_code="zebra"))
    assert unlisted == [_e("water closet", "category")], unlisted
    out = _ground(vdb, _e("water closet zebra", "category"))
    [spec] = [e for e in out if e["hint"] == "specification"]
    assert spec["spec_key"] == "" and spec["spec_value"] is None and spec["raw"] == "zebra", spec


# --------------------------------------------------------------------------- #
# Generated from the registry: every descriptor kind, glued to a product type    #
# --------------------------------------------------------------------------- #


def _registry_phrases(db, key: str) -> list[tuple[str, str]]:
    """(customer phrase, stored value) for every choice of `key`, off the registry the
    way staff see it (`merged_synonyms`), one phrase per choice."""
    from app.models.product_spec import ProductSpecRegistry
    from app.services.product_spec_registry import merged_synonyms

    row = db.query(ProductSpecRegistry).filter_by(spec_key=key).one()
    return [(phrases[0], value) for value, phrases in sorted(merged_synonyms(row).items()) if value != "_self" and phrases]


#: The product type each descriptor kind is asked about, and the class word it keeps.
_TYPES = {"finish": "basin", "mounting": "wash basin", "trap_type": "water closet", "material": "kitchen sink"}


@pytest.mark.parametrize("key", sorted(_TYPES))
@pytest.mark.parametrize("order", ["before", "after"])
def test_every_registry_choice_glued_to_a_product_type_grounds(vdb, key, order):
    """For every choice of finish, mounting, trap and material the registry holds: "<its
    first word> <product type>" (and the other way round) leaves the product type as the
    category and grounds the choice to its key and value."""
    type_word = _TYPES[key]
    phrases = _registry_phrases(vdb, key)
    assert len(phrases) >= 2, phrases
    for phrase, value in phrases:
        raw = f"{phrase} {type_word}" if order == "before" else f"{type_word} {phrase}"
        out = _ground(vdb, _e(raw, "category"))
        assert ("category", type_word) in _kinds(out), (raw, out)
        assert (key, value) in _specs(out), (raw, out)


@pytest.mark.parametrize(
    "raw,type_word,key,value",
    [
        ("thickness 1.2 mm", "kitchen sink", "thickness", 1.2),
        ("1.0mm thick", "kitchen sink", "thickness", 1),
        ("thickness 0.8 mm", "kitchen sink", "thickness", 0.8),
        ("600mm long", "basin", "dim_length", 600),
        ("width 450 mm", "basin", "dim_width", 450),
        ("height 800mm", "water closet", "dim_height", 800),
        ("diameter 420mm", "basin", "diameter", 420),
        ("double bowl", "kitchen sink", "bowl_count", 2),
    ],
)
def test_a_number_with_its_key_word_grounds_to_that_measurement(vdb, raw, type_word, key, value):
    for glued in (f"{type_word} {raw}", f"{raw} {type_word}"):
        out = _ground(vdb, _e(glued, "category"))
        assert ("category", type_word) in _kinds(out), (glued, out)
        assert (key, value) in _specs(out), (glued, out)
        # Filed by the parser as a technical drawing, the words are the parser's (option (c)).
        document = _e(raw, "attachment_type", canonical_code="technical drawing")
        _kept_as_document(_ground(vdb, _e(type_word, "category"), document), document)


@pytest.mark.parametrize(
    "said,key,known",
    [
        ("purple colour", "finish", "Gunmetal"),
        ("colour teal", "finish", "Chrome"),
        ("t trap", "trap_type", "P trap"),
        ("q trap", "trap_type", "S trap"),
        ("wood material", "material", "Ceramic"),
    ],
)
def test_a_word_beside_a_keys_own_word_that_is_no_choice_is_unknown(vdb, said, key, known):
    out = _ground(vdb, _e(f"water closet {said}", "category"))
    [spec] = [e for e in out if e["hint"] == "specification"]
    assert spec["spec_key"] == key and spec["spec_value"] is None, spec
    assert known in spec["spec_known"], spec


@pytest.mark.parametrize("raw", ["deck mounted tap", "long spout basin tap", "grease trap"])
def test_an_ordinary_product_word_before_a_head_word_is_not_an_unknown_value(vdb, raw):
    """Round 5 B1 kept: "deck mounted", "grease trap" are product words, never a value the
    registry is told it does not know. Fix round 10 on PR #833 ("for #833 yeah exact
    only"): a word no product and no registry entry holds ("deck" here, where no product
    is deck mounted) is said back as a word nothing matched, with no key."""
    out = _ground(vdb, _e(raw, "category"))
    assert all(
        e.get("spec_value") is not None or not e.get("spec_key") for e in out if e["hint"] == "specification"
    ), out


def test_a_typo_of_a_choice_binds_nothing(vdb):
    """Fix round 10 on PR #833 ("for #833 yeah exact only"): "gunmetl" is not the
    registry's "gunmetal". Round 8 grounded it to the choice; now it is said back as
    typed, and only the exact spelling (any case, a plural aside) binds."""
    out = _ground(vdb, _e("gunmetl basin", "category"))
    assert ("finish", "gunmetal") not in _specs(out), out
    assert any(e["hint"] == "specification" and e["raw"] == "gunmetl" and e["spec_value"] is None for e in out), out
    assert _specs(_ground(vdb, _e("GUNMETAL basins", "category"))) == [("finish", "gunmetal")]
