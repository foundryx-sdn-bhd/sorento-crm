"""CHAT-FIX F4 (CHAT-GROUNDING-TYPO), red tests. UAC: documentation/plans/chatbot/
chat-fix-grounding-typo-acceptance-criteria.md (AC-1..AC-6).

Owner ruling 16 Oct 2026: when the parser outputs a specification key + value the product
spec registry knows, that value binds, whatever the customer typed. This reverses the 28 Sep
"exact only" ruling for parser corrections. Only the registry's own words are said back.

Every test seeds its own chain (the registry seed and the class categories): no catalogue
row is borrowed.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_lane_require import _seed_registry
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category

UNDERMOUNT_MSG = "any undrmount basin have stock"
GUNMETAL_MSG = "any gunmetl basin have stock"


@pytest.fixture()
def vdb(session_factory):
    db = session_factory()
    for suffix in ("WC", "WB", "KS", "FT"):
        _class_category(db, suffix)
    _seed_registry(db)
    db.commit()
    yield db
    db.close()


def _e(raw: str, hint: str, **extra: Any) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": extra.pop("canonical_code", None), "current_message": True, "confident": True, **extra}


def _verdict(*entities: dict[str, Any]) -> dict[str, Any]:
    return {"entities": list(entities), "domain_hint": "inventory", "intent_hint": "check_stock"}


def _ground(db, message: str, *entities: dict[str, Any]) -> dict[str, Any]:
    from app.services.chatbot.head.grounding import ground

    verdict, _notes = ground(db, _verdict(*entities), message=message)
    return verdict


def _specs(entities) -> list[dict[str, Any]]:
    return [e for e in entities if e["hint"] == "specification"]


def _basin_typo_entity() -> dict[str, Any]:
    return _e("undrmount basin", "category", spec_key="mounting", spec_value="under_counter")


# --------------------------------------------------------------------------- #
# AC-1: a class entity carrying a valid spec                                    #
# --------------------------------------------------------------------------- #


def test_ac1_a_class_entity_with_a_valid_spec_grounds_to_the_class_and_the_spec(vdb):
    out = _ground(vdb, UNDERMOUNT_MSG, _basin_typo_entity())["entities"]
    assert any(e["hint"] == "category" and e["raw"] == "basin" for e in out), out
    specs = _specs(out)
    assert [(e["spec_key"], e["spec_value"]) for e in specs] == [("mounting", "under_counter")], out
    assert not [e for e in specs if not e["spec_key"]], out
    assert not [e for e in specs if e["spec_value"] is None], out


# --------------------------------------------------------------------------- #
# AC-2: a specification entity the parser put right                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,key,value,message,expected",
    [
        ("gunmetal", "finish", "gunmetal", GUNMETAL_MSG, "gunmetal"),
        ("gunmetl", "finish", "gunmetal", GUNMETAL_MSG, "gunmetal"),
        ("undermount", "mounting", "under_counter", UNDERMOUNT_MSG, "under_counter"),
        ("undrmount", "mounting", "under_counter", UNDERMOUNT_MSG, "under_counter"),
    ],
)
def test_ac2_a_registry_valid_specification_binds_whatever_was_typed(vdb, raw, key, value, message, expected):
    out = _ground(vdb, message, _e("basin", "category"), _e(raw, "specification", spec_key=key, spec_value=value))["entities"]
    specs = _specs(out)
    assert [(e["spec_key"], e["spec_value"]) for e in specs] == [(key, expected)], out


# --------------------------------------------------------------------------- #
# AC-3: an invalid parser value is unchanged                                    #
# --------------------------------------------------------------------------- #


def test_ac3_a_value_outside_the_keys_choices_is_an_unknown_value_said_as_typed(vdb):
    out = _ground(vdb, "any pnk basin", _e("basin", "category"), _e("pink", "specification", spec_key="finish", spec_value="pink"))["entities"]
    [spec] = _specs(out)
    assert (spec["spec_key"], spec["spec_value"], spec["raw"]) == ("finish", None, "pnk"), spec
    assert spec["spec_known"], spec


def test_ac3_a_value_outside_the_keys_choices_typed_right_is_said_as_typed(vdb):
    out = _ground(vdb, "any pink basin", _e("basin", "category"), _e("pink", "specification", spec_key="finish", spec_value="pink"))["entities"]
    [spec] = _specs(out)
    assert (spec["spec_key"], spec["spec_value"], spec["raw"]) == ("finish", None, "pink"), spec


def test_ac3_an_unknown_key_binds_nothing(vdb):
    out = _ground(vdb, "any zzqx basin", _e("basin", "category"), _e("zzqx", "specification", spec_key="zzt_not_a_key", spec_value="zzqx"))["entities"]
    assert not [e for e in _specs(out) if e["spec_value"] is not None], out
    assert not [e for e in _specs(out) if e["spec_key"] == "zzt_not_a_key"], out


# --------------------------------------------------------------------------- #
# AC-4: only the registry's words are said back                                 #
# --------------------------------------------------------------------------- #


def test_ac4_the_said_back_words_are_the_registrys_for_a_class_entity(vdb):
    out = _ground(vdb, UNDERMOUNT_MSG, _basin_typo_entity())["entities"]
    [spec] = _specs(out)
    assert spec["raw"] == "under counter" and spec["spec_words"] == "under counter", spec
    assert "undrmount" not in repr(out), out


@pytest.mark.parametrize("raw", ["gunmetal", "gunmetl"])
def test_ac4_the_said_back_words_are_the_registrys_for_a_specification(vdb, raw):
    out = _ground(vdb, GUNMETAL_MSG, _e("basin", "category"), _e(raw, "specification", spec_key="finish", spec_value="gunmetal"))["entities"]
    [spec] = _specs(out)
    assert spec["raw"] == "gunmetal" and spec["spec_words"] == "gunmetal", spec
    assert "gunmetl" not in repr(out), out


def test_ac4_the_set_header_names_the_registry_words_and_no_typo(vdb):
    from app.services.chatbot.lanes.business.answer import build_set_header
    from app.services.product_predicate_service import describe_set

    out = _ground(vdb, UNDERMOUNT_MSG, _basin_typo_entity())["entities"]
    [spec] = _specs(out)
    description = describe_set(vdb, brand=None, membership={"class": ["basin"], spec["spec_key"]: [spec["spec_value"]]})
    header = build_set_header(3, 3, "wash basins", {"stock": True}, description=description)
    assert "under counter" in header.lower(), header
    assert "undrmount" not in header.lower() and "Couldn't find" not in header, header


# --------------------------------------------------------------------------- #
# AC-5: the resolve body carries the filter and no unknown value                #
# --------------------------------------------------------------------------- #


def test_ac5_the_resolve_body_carries_mounting_with_class_basin_and_no_unknown_values(vdb):
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    verdict = {**_ground(vdb, UNDERMOUNT_MSG, _basin_typo_entity()), "message_type": "business_query", "match_mode": "and", "access_levels": []}
    ctx = {"text": {"message": {"message": {"text": UNDERMOUNT_MSG}}}, "contact": {"id": "1"}, "parse": {"output": verdict}}
    body = resolve_entity_body(ctx)
    specs = body.get("extracted_specs") or []
    assert [(s["key"], s["value"]) for s in specs] == [("mounting", "under_counter")], body
    assert not body.get("unknown_values"), body
    assert "basin" in (body.get("scope_terms") or []), body


# --------------------------------------------------------------------------- #
# AC-6: exact words and untouched turns                                         #
# --------------------------------------------------------------------------- #


def test_ac6_an_exact_word_descriptor_grounds_as_today(vdb):
    out = _ground(vdb, "any gunmetal basin have stock", _e("gunmetal basin", "category"))["entities"]
    assert any(e["hint"] == "category" and e["raw"] == "basin" for e in out), out
    assert [(e["spec_key"], e["spec_value"]) for e in _specs(out)] == [("finish", "gunmetal")], out


def test_ac6_a_spec_entity_with_its_word_in_the_message_grounds_as_today(vdb):
    out = _ground(vdb, "any gunmetal basin", _e("basin", "category"), _e("gunmetal", "specification", spec_key="finish", spec_value="gunmetal"))["entities"]
    assert [(e["spec_key"], e["spec_value"]) for e in _specs(out)] == [("finish", "gunmetal")], out


def test_ac6_a_verdict_with_no_descriptor_is_returned_unchanged(vdb):
    verdict = _verdict(_e("basin", "category"))
    out, notes = __import__("app.services.chatbot.head.grounding", fromlist=["ground"]).ground(vdb, verdict, message="any basin")
    assert out["entities"] == verdict["entities"], out
    assert not [e for e in out["entities"] if e["hint"] == "specification"], out


# --------------------------------------------------------------------------- #
# Guards (fix round on PR #1737): the class branch binds one unheard word only,   #
# invalid values and synonyms stay as they are                                    #
# --------------------------------------------------------------------------- #


def _shape(out) -> list[tuple[Any, ...]]:
    """The specification entities only: a class entity keeps the parser's own keys."""
    return [(e["hint"], e["raw"], e.get("spec_key"), e.get("spec_value")) for e in out["entities"] if e["hint"] == "specification"]


@pytest.mark.parametrize(
    "key,value",
    [("mounting", "flushmount"), ("zzt_not_a_key", "under_counter")],
    ids=["value-not-a-choice", "unknown-key"],
)
def test_guard_a_an_invalid_parser_spec_on_a_class_entity_leaves_the_unheard_word_unknown(vdb, key, value):
    message = "any undrmount chrome basin"
    entity = _e("undrmount chrome basin", "category", spec_key=key, spec_value=value)
    shape = _shape(_ground(vdb, message, entity))
    assert ("specification", "undrmount", "", None) in shape, shape
    assert not [s for s in shape if s[2] == "mounting"], shape


def test_guard_b_a_spec_already_heard_binds_once_and_the_extra_unheard_word_stays_unknown(vdb):
    message = "any pnk undermount basin"
    entity = _e("pnk undermount basin", "category", spec_key="mounting", spec_value="under_counter")
    shape = _shape(_ground(vdb, message, entity))
    assert [s for s in shape if s[2] == "mounting" and s[3] == "under_counter"] and len([s for s in shape if s[2] == "mounting"]) == 1, shape
    assert ("specification", "pnk", "", None) in shape, shape


def test_guard_c_two_unheard_words_and_one_parser_spec_guess_no_binding(vdb):
    message = "any undrmount pnk basin"
    entity = _e("undrmount pnk basin", "category", spec_key="mounting", spec_value="under_counter")
    shape = _shape(_ground(vdb, message, entity))
    assert not [s for s in shape if s[3] is not None], shape
    assert ("specification", "undrmount", "", None) in shape, shape
    assert ("specification", "pnk", "", None) in shape, shape


def test_guard_d_a_non_registry_adjective_is_handled_as_before(vdb):
    message = "any cheap undermount basin"
    entity = _e("cheap undermount basin", "category", spec_key="mounting", spec_value="under_counter")
    shape = _shape(_ground(vdb, message, entity))
    assert [s for s in shape if s[2] == "mounting" and s[3] == "under_counter"], shape
    assert ("specification", "cheap", "", None) in shape, shape


def test_guard_e_a_boolean_spec_on_a_class_entity_is_said_with_the_keys_registry_phrase(vdb):
    entity = _e("rimles basin", "category", spec_key="is_rimless", spec_value=True)
    out = _ground(vdb, "any rimles basin", entity)["entities"]
    [spec] = [e for e in _specs(out) if e["spec_key"] == "is_rimless"]
    assert spec["spec_value"] is True, out
    assert spec["raw"] == "rimless", spec


def test_guard_e_a_numeric_spec_whose_number_is_in_the_message_takes_mains_path(vdb):
    entity = _e("600mm wdth basin", "category", spec_key="dim_width", spec_value=600)
    shape = _shape(_ground(vdb, "any 600mm wdth basin", entity))
    assert not [s for s in shape if s[2] == "dim_width" and s[3] is not None and s[1] != "600mm"], shape
    assert ("specification", "wdth", "", None) in shape, shape


@pytest.mark.parametrize(
    "message,raw,key,value",
    [
        ("any 600mm pnk basin", "600mm pnk basin", "dim_width", 600),
        ("any undermount pnk basin", "pnk basin", "mounting", "under_counter"),
        ("any rimless pnk basin", "pnk basin", "is_rimless", True),
    ],
    ids=["number-in-message", "mounting-word-in-message", "boolean-word-in-message"],
)
def test_guard_g_a_spec_whose_words_are_in_the_message_never_replaces_another_typed_word(vdb, message, raw, key, value):
    entity = _e(raw, "category", spec_key=key, spec_value=value)
    shape = _shape(_ground(vdb, message, entity))
    assert ("specification", "pnk", "", None) in shape, shape


def test_guard_f_a_correctly_typed_synonym_keeps_its_own_words(vdb):
    out = _ground(vdb, "any undermount basin", _e("basin", "category"), _e("undermount", "specification", spec_key="mounting", spec_value="under_counter"))["entities"]
    [spec] = _specs(out)
    assert (spec["spec_key"], spec["spec_value"], spec["raw"]) == ("mounting", "under_counter", "undermount"), spec


def test_guard_f_a_correctly_typed_finish_synonym_keeps_its_own_words(vdb):
    out = _ground(vdb, "any gun metal basin", _e("basin", "category"), _e("gun metal", "specification", spec_key="finish", spec_value="gunmetal"))["entities"]
    [spec] = _specs(out)
    assert (spec["spec_key"], spec["spec_value"], spec["raw"]) == ("finish", "gunmetal", "gun metal"), spec


@pytest.mark.parametrize(
    "message,raw,key,value,word",
    [
        ("any double bowl pnk kitchen sink", "pnk kitchen sink", "bowl_count", 2, "pnk"),
        ("any 60cm pnk basin", "60cm pnk basin", "dim_width", 600, "pnk"),
        ("hi", "pnk basin", "mounting", "under_counter", "pnk"),
        ("", "undrmount basin", "mounting", "under_counter", "undrmount"),
        (None, "undrmount basin", "mounting", "under_counter", "undrmount"),
        ("any rimed basin", "rimed basin", "is_rimless", False, "rimed"),
    ],
    ids=["j-worded-number", "k-number-mismatch", "l-first-line-only", "m-empty-message", "m-no-message", "n-phraseless-false"],
)
def test_guard_jn_numbers_unseen_words_and_phraseless_values_take_mains_path(vdb, message, raw, key, value, word):
    entity = _e(raw, "category", spec_key=key, spec_value=value)
    shape = _shape(_ground(vdb, message, entity))
    assert ("specification", word, "", None) in shape, shape
    assert not [s for s in shape if s[2] == key], shape
    assert not [s for s in shape if s[1] in ("No", "Yes")], shape


@pytest.mark.parametrize(
    "message,raw,key,value",
    [
        ("any pnk wall basin", "pnk wall basin", "mounting", "wall_hung"),
        ("any wall pnk basin", "pnk basin", "mounting", "wall_hung"),
        ("any pnk rose basin", "pnk rose basin", "finish", "rose_gold"),
        ("any pnk under basin", "pnk under basin", "mounting", "under_counter"),
        ("any pnk counter basin", "pnk counter basin", "mounting", "counter_top"),
        ("any yellow pnk basin", "pnk basin", "finish", "golden_yellow"),
    ],
    ids=["o1-wall", "o2-wall-elsewhere", "o3-rose", "o4-under", "o5-counter", "o6-gold"],
)
def test_guard_o_part_of_a_values_phrase_in_the_message_takes_mains_path(vdb, message, raw, key, value):
    entity = _e(raw, "category", spec_key=key, spec_value=value)
    shape = _shape(_ground(vdb, message, entity))
    assert ("specification", "pnk", "", None) in shape, shape
    assert not [s for s in shape if s[2] == key and s[3] == value], shape


def _seed_product_word(db, word: str) -> None:
    """A product whose name holds `word`, which no registry phrase does: the catalogue's own word."""
    from app.models.product import Product
    from tests._pg_fixture import unique_code
    from tests.chatbot.test_lane_require import _seed_category_and_uom

    category_id, uom_id = _seed_category_and_uom(db)
    code = unique_code("ZZF4")[:50]
    db.add(
        Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"{code} {word.upper()} BASIN", description=f"{word.upper()} BASIN",
            category_id=category_id, base_uom_id=uom_id, list_price=10, is_active=True,
        )
    )
    db.commit()


def test_guard_p_a_catalogue_word_the_registry_lacks_takes_mains_path(vdb):
    _seed_product_word(vdb, "zzinset")
    entity = _e("pnk zzinset basin", "category", spec_key="mounting", spec_value="under_counter")
    shape = _shape(_ground(vdb, "any pnk zzinset basin", entity))
    assert ("specification", "pnk", "", None) in shape, shape
    assert not [s for s in shape if s[2] == "mounting" and s[3] == "under_counter"], shape


def test_owner_shape_a_brand_and_an_undrmount_basin_binds_under_counter(vdb):
    """A behaviour test (passes on the fix, fails on main): the owner's recorded shape."""
    brand = _e("sorento", "brand")
    out = _ground(vdb, "i mean any stock for sorento undrmount basin", brand, _basin_typo_entity())["entities"]
    [spec] = [e for e in _specs(out) if e["spec_key"] == "mounting"]
    assert spec["spec_value"] == "under_counter" and spec["raw"] == "under counter", spec
    assert brand in out, out


# --------------------------------------------------------------------------- #
# Round 6: the split shape, a category entity plus a separate corrected spec      #
# --------------------------------------------------------------------------- #


def _spec_entity(raw: str, key: str, value: Any) -> dict[str, Any]:
    return _e(raw, "specification", spec_key=key, spec_value=value)


def test_q_the_split_shape_binds_the_corrected_spec_and_says_no_typo(vdb):
    """Behaviour test (fails on head and main): the parser's other common shape."""
    message = "i mean any stock for sorento undrmount basin"
    out = _ground(vdb, message, _e("sorento", "brand"), _e("undrmount basin", "category"), _spec_entity("under counter", "mounting", "under_counter"))["entities"]
    assert any(e["hint"] == "category" and e["raw"] == "basin" for e in out), out
    specs = _specs(out)
    assert [(e["spec_key"], e["spec_value"], e["raw"]) for e in specs] == [("mounting", "under_counter", "under counter")], out
    assert "undrmount" not in repr(out), out


def test_r_two_corrected_specs_are_ambiguous_and_the_word_is_said_back(vdb):
    out = _ground(
        vdb, "any undrmount basin", _e("undrmount basin", "category"),
        _spec_entity("under counter", "mounting", "under_counter"), _spec_entity("gunmetal", "finish", "gunmetal"),
    )["entities"]
    assert ("specification", "undrmount", "", None) in _shape({"entities": out}), out


@pytest.mark.parametrize(
    "message,spec",
    [
        ("any pnk basin under counter", _spec_entity("under counter", "mounting", "under_counter")),
        ("any undrmount basin", _spec_entity("under countr", "mounting", "flushmount")),
        ("any undrmount basin", _spec_entity("600 mm", "dim_width", 600)),
    ],
    ids=["s-spec-words-in-message", "t-invalid-value", "u-numeric"],
)
def test_stu_a_spec_entity_that_is_not_a_correction_leaves_the_typed_word_said_back(vdb, message, spec):
    raw = "pnk basin" if "pnk" in message else "undrmount basin"
    word = "pnk" if "pnk" in message else "undrmount"
    out = _ground(vdb, message, _e(raw, "category"), spec)
    assert ("specification", word, "", None) in _shape(out), out


# --------------------------------------------------------------------------- #
# Round 7: a spec W3 dropped (a carried entity) can account for the class typo     #
# --------------------------------------------------------------------------- #


def _carried_under_counter() -> dict[str, Any]:
    return {**_spec_entity("under counter", "mounting", "under_counter"), "current_message": False}


def _ground_carried(db, message, entities, carried):
    from app.services.chatbot.head.grounding import ground

    verdict, _notes = ground(db, _verdict(*entities), message=message, carried_specs=carried)
    return verdict


def test_v_a_dropped_carried_spec_accounts_for_the_one_typed_word_and_binds(vdb):
    """Behaviour test: the third parse shape of the owner's probe."""
    message = "i mean any stock for sorento undrmount basin"
    out = _ground_carried(vdb, message, [_e("sorento", "brand"), _e("undrmount basin", "category")], [_carried_under_counter()])["entities"]
    assert any(e["hint"] == "category" and e["raw"] == "basin" for e in out), out
    assert [(e["spec_key"], e["spec_value"], e["raw"]) for e in _specs(out) if e["spec_key"] == "mounting"] == [("mounting", "under_counter", "under counter")], out
    assert "undrmount" not in repr(out), out


def test_w_a_carried_spec_that_accounts_for_nothing_changes_nothing(vdb):
    entities = [_e("basin", "category"), _e("pink", "specification", spec_key="finish", spec_value=None)]
    message = "any pnk basin"
    baseline = _ground(vdb, message, *entities)
    carried = _ground_carried(vdb, message, entities, [_carried_under_counter()])
    assert carried == baseline, (carried, baseline)
    assert not [e for e in carried["entities"] if e.get("spec_key") == "mounting"], carried


def test_x_no_unheard_word_means_the_dropped_spec_is_not_added(vdb):
    entities = [_e("basin", "category")]
    baseline = _ground(vdb, "any basin", *entities)
    carried = _ground_carried(vdb, "any basin", entities, [_carried_under_counter()])
    assert carried == baseline, (carried, baseline)


def test_y_two_unheard_words_take_mains_path_whatever_was_carried(vdb):
    entities = [_e("undrmount pnk basin", "category")]
    message = "any undrmount pnk basin"
    baseline = _ground(vdb, message, *entities)
    carried = _ground_carried(vdb, message, entities, [_carried_under_counter()])
    assert carried == baseline, (carried, baseline)
    shape = _shape(carried)
    assert ("specification", "undrmount", "", None) in shape and ("specification", "pnk", "", None) in shape, shape


def test_z_dropped_set_specs_is_exactly_what_w3_removed(vdb):
    from app.services.chatbot.turn_runtime import dropped_set_specs, with_new_set_words

    carried = _carried_under_counter()
    before = {"entities": [_e("sorento", "brand"), _e("undrmount basin", "category"), carried]}
    after = with_new_set_words(before)
    assert carried not in after["entities"], after
    assert dropped_set_specs(before, after) == [carried]
    untouched = {"entities": [_e("undrmount basin", "category"), _e("under counter", "specification", spec_key="mounting", spec_value="under_counter")]}
    assert dropped_set_specs(untouched, with_new_set_words(untouched)) == []
