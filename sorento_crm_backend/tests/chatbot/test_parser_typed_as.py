"""CHAT-FIX F5 (CHAT-TYPED-AS), red tests. UAC: documentation/plans/chatbot/
chat-fix-typed-as-acceptance-criteria.md (AC-1..AC-3, AC-5, AC-6; AC-4 is the F4 file staying
green, AC-7 is a gate run).

The parser names, per entity, the word of THIS message it read the entity from (`typed_as`).
W3 keeps a specification entity typed in this message, and grounding binds a spec to the
class raw's single typo by that word. Synthetic words only.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.chatbot.test_lane_require import _seed_registry
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category

MESSAGE = "i mean any stock for sorento undrmount basin"
TYPED_AS_DESCRIPTION = (
    "The word or words of THIS message's text that name this entity, copied character for character as typed "
    "(a misspelling stays misspelt); null when THIS message's text does not name it, whatever earlier turns said."
)
ENTITY_PROPERTIES_TODAY = [
    "raw", "hint", "canonical_code", "current_message", "confident", "hint_confident", "quantity", "spec_key", "spec_value", "account",
]


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
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True, **extra}


def _spec(raw="under counter", key="mounting", value="under_counter", *, typed_as=None, current=False) -> dict[str, Any]:
    return _e(raw, "specification", spec_key=key, spec_value=value, current_message=current, typed_as=typed_as)


def _verdict(*entities: dict[str, Any]) -> dict[str, Any]:
    return {"entities": list(entities), "domain_hint": "inventory", "intent_hint": "check_stock"}


def _ground(db, message, *entities):
    from app.services.chatbot.head.grounding import ground

    verdict, _notes = ground(db, _verdict(*entities), message=message)
    return verdict


def _specs(verdict) -> list[dict[str, Any]]:
    return [e for e in verdict["entities"] if e["hint"] == "specification"]


# --------------------------------------------------------------------------- #
# AC-1: the schema                                                              #
# --------------------------------------------------------------------------- #


def _entity_schema() -> dict[str, Any]:
    from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA

    return PARSE_OUTPUT_JSON_SCHEMA["properties"]["entities"]["items"]


def test_ac1_typed_as_is_the_last_entity_property_nullable_string_and_required():
    schema = _entity_schema()
    assert list(schema["properties"]) == [*ENTITY_PROPERTIES_TODAY, "typed_as"], list(schema["properties"])
    assert schema["required"] == [*ENTITY_PROPERTIES_TODAY, "typed_as"], schema["required"]
    assert schema["properties"]["typed_as"]["type"] == ["string", "null"], schema["properties"]["typed_as"]


def test_ac1_typed_as_carries_the_plans_description_word_for_word():
    assert _entity_schema()["properties"]["typed_as"].get("description") == TYPED_AS_DESCRIPTION


# --------------------------------------------------------------------------- #
# AC-2: W3 keeps a spec typed in this message                                    #
# --------------------------------------------------------------------------- #


def _w3(entities, **kwargs):
    from app.services.chatbot.turn_runtime import with_new_set_words

    return with_new_set_words({"entities": entities}, **kwargs)["entities"]


def test_ac2_a_carried_spec_typed_in_this_message_is_kept():
    spec = _spec(typed_as="undrmount")
    assert spec in _w3([_e("sorento", "brand"), _e("undrmount basin", "category"), spec], message=MESSAGE)


@pytest.mark.parametrize("typed_as", [None, "pnk"], ids=["null", "word-not-in-message"])
def test_ac2_a_carried_spec_not_typed_in_this_message_is_dropped(typed_as):
    spec = _spec(typed_as=typed_as)
    assert spec not in _w3([_e("undrmount basin", "category"), spec], message=MESSAGE)


def test_ac2_a_carried_non_spec_set_entity_is_dropped_as_today_whatever_typed_as_says():
    carried = {**_e("water closet", "category", current_message=False), "typed_as": "stock"}
    assert carried not in _w3([_e("undrmount basin", "category"), carried], message=MESSAGE)


def test_ac2_with_no_message_it_behaves_exactly_as_today():
    spec = _spec(typed_as="undrmount")
    entities = [_e("undrmount basin", "category"), spec]
    assert spec not in _w3(entities)


def test_ac2_an_explicit_none_message_is_the_same_as_no_message():
    entities = [_e("undrmount basin", "category"), _spec(typed_as="undrmount")]
    assert _w3(entities, message=None) == _w3(entities)


# --------------------------------------------------------------------------- #
# AC-3 and AC-6: grounding binds by typed_as; the composition                    #
# --------------------------------------------------------------------------- #


def _ac3_entities() -> list[dict[str, Any]]:
    return [_e("sorento", "brand"), _e("undrmount basin", "category"), _spec(typed_as="undrmount")]


def _assert_bound(out) -> None:
    assert any(e["hint"] == "category" and e["raw"] == "basin" for e in out["entities"]), out
    specs = [e for e in _specs(out) if e["spec_key"] == "mounting"]
    assert [(e["spec_value"], e["raw"]) for e in specs] == [("under_counter", "under counter")], out
    assert "undrmount" not in repr(out["entities"]), out


def test_ac3_grounding_binds_a_spec_by_the_word_the_parser_says_it_was_typed_as(vdb):
    _assert_bound(_ground(vdb, MESSAGE, *_ac3_entities()))


@pytest.mark.parametrize(
    "typed_as,second",
    [("pnk", None), ("zzword", None), ("undrmount", _spec("gunmetal", "finish", "gunmetal", typed_as="undrmount"))],
    ids=["another-word", "word-not-in-message", "two-specs-same-word"],
)
def test_ac5_a_typed_as_that_does_not_match_takes_f4s_path(vdb, typed_as, second):
    message = "any undrmount basin pnk"
    entities = [_e("undrmount basin", "category"), _spec(typed_as=typed_as), *([second] if second else [])]
    out = _ground(vdb, message, *entities)
    assert any(e["hint"] == "specification" and not e["spec_key"] and e["raw"] == "undrmount" and e["spec_value"] is None for e in out["entities"]), out


def test_ac6_the_composition_w3_then_grounding_keeps_and_binds_the_carried_spec(vdb):
    """No DB-free engine seam runs W3 and grounding with a stubbed parse, so this is the
    composition `engine.py` makes: `ground(with_new_set_words(v, message=m), message=m)`."""
    from app.services.chatbot.turn_runtime import with_new_set_words

    verdict = with_new_set_words(_verdict(*_ac3_entities()), message=MESSAGE)
    from app.services.chatbot.head.grounding import ground

    out, _notes = ground(vdb, verdict, message=MESSAGE)
    _assert_bound(out)
