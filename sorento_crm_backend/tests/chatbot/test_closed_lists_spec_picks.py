"""CHAT-CLOSED-LISTS S5: under the gate the parser's spec and type picks bind as picked.

Card R2/R5: code checks a pick against the registry and never re-reads the customer's words
to fix it. Under the CLOSED LISTS gate (a) a class / product type the parser picked from the
candidates binds as that type, with no word split of the raw (card c27: "kitchan tap" read
as kitchen_tap, never "Couldn't find: kitchan"); (b) a spec value off the registry is said
back as unknown (as today); (c) the category-word domain move
(word lists over the goal sentence) does not run. Gate off, every case keeps today's reading.
"""
from __future__ import annotations

import uuid

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.product_spec import ProductSpecRegistry
from app.services.chatbot.head import grounding
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        for spec_key, label, values, synonyms in (
            ("product_type", "Product type", ["kitchen_tap", "basin_tap"], {}),
            ("finish", "Finish", ["chrome", "black"], {"black": ["matte black"]}),
            ("spout_type", "Spout", ["double_flexible", "fixed"], {}),
        ):
            session.add(ProductSpecRegistry(
                id=str(uuid.uuid4()), spec_key=spec_key, label=label, data_type="enum",
                allowed_values=values, synonyms=synonyms,
            ))
        session.flush()
        yield session


def _ground(db, verdict: dict, *, closed: bool, message: str = "") -> list[dict]:
    out, _ = grounding.ground(db, verdict, message=message, closed_lists=closed)
    return [e for e in out.get("entities") or [] if isinstance(e, dict)]


def _ask(*entities: dict, domain: str = "master_products") -> dict:
    return {"intent_hint": "check_product", "domain_hint": domain, "entities": list(entities), "user_goal": ""}


def _type_pick(raw: str, canonical: str | None) -> dict:
    return {"raw": raw, "hint": "category", "canonical_code": canonical, "current_message": True, "confident": True}


def _spec_pick(raw: str, key: str, value) -> dict:
    return {"raw": raw, "hint": "specification", "canonical_code": None, "spec_key": key, "spec_value": value,
            "current_message": True, "confident": True}


MESSAGE_C27 = "any double flexible kitchan tap in chrome color"


def test_c27_shape_a_listed_type_pick_binds_as_that_type_with_no_unknown_word(db):
    entities = _ground(db, _ask(_type_pick("kitchan tap", "kitchen_tap")), closed=True, message=MESSAGE_C27)

    assert [(e["hint"], e["raw"]) for e in entities] == [("category", "Kitchen tap")]


def test_control_gate_off_the_typo_type_word_is_split_as_today(db):
    entities = _ground(db, _ask(_type_pick("kitchan tap", "kitchen_tap")), closed=False, message=MESSAGE_C27)

    assert [(e["hint"], e["raw"]) for e in entities] != [("category", "Kitchen tap")]


def test_an_unlisted_type_pick_is_left_to_todays_reading(db):
    gated = _ground(db, _ask(_type_pick("vanity", "vanity_unit")), closed=True, message="vanity")
    plain = _ground(db, _ask(_type_pick("vanity", "vanity_unit")), closed=False, message="vanity")

    assert gated == plain


def test_a_spec_value_off_the_registry_is_said_back_never_read_off_the_raw(db):
    [entity] = _ground(db, _ask(_spec_pick("chrome", "finish", "shiny")), closed=True, message="any chrome tap")

    assert (entity["spec_key"], entity["spec_value"], entity["raw"]) == ("finish", None, "chrome")
    assert entity["spec_known"] == ["Black", "Chrome"]


def test_a_registry_value_binds_and_a_typo_is_said_in_the_registrys_words(db):
    """The owner's F4 ruling (16 Oct 2026) stands under the gate: a registry-valid pick binds
    whatever was typed, and is said in the registry's words when the typed words hold one
    the vocabulary does not know."""
    [entity] = _ground(db, _ask(_spec_pick("matt blck", "finish", "black")), closed=True, message="matt blck basin tap")

    assert (entity["spec_value"], entity["raw"]) == ("black", "black")


def test_the_category_word_domain_move_does_not_run_under_the_gate(db):
    verdict = _ask(_type_pick("kitchen tap", "kitchen_tap"), domain="product_attachment")
    out, _ = grounding.ground(db, verdict, message="kitchen tap", closed_lists=True)
    assert out["domain_hint"] == "product_attachment"

    plain, _ = grounding.ground(db, verdict, message="kitchen tap", closed_lists=False)
    assert plain["domain_hint"] == "master_products"
