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
