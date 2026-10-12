"""PROMO-SUBJECT-FILTER round 3b red tests: spec qualifiers on a promo ask (AC-36 to AC-38).

"wall hung water closet promo": the qualifier is a registry spec value (mounting wall_hung), the
parser emits it as a specification entity, and the promotions read (stamp and pick) narrows by it.
Real resolver route, route-backed MCP with the real presenter. Fake names only.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.main import app
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_promo_brand_category import _brand, _prod
from tests.chatbot.test_promo_kind_subject import PRODUCTS_TOOL, PROMO_TOOL, _promo
from tests.chatbot.test_promo_owner_round2 import _mcp
from tests.chatbot.test_promo_round3_detection import _go, _types, _wire
from tests.chatbot.test_promo_subject_filter import _class, _pick_qf


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _wc_world(session_factory, *, fm_level="sorento_dealer"):
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Water Closet")
    db = session_factory()
    std, _flyer = _types(db)
    srt = _brand(db, "Sorento")
    db.add_all(
        [
            ProductSpecRegistry(
                id=str(uuid.uuid4()), spec_key="mounting", label="Mounting", data_type="enum",
                allowed_values=["wall_hung", "floor_mounted"],
                synonyms={"wall_hung": ["wall hung", "wall mounted"], "floor_mounted": ["floor mounted"]},
            ),
            ProductSpecRegistry(
                id=str(uuid.uuid4()), spec_key="flush_type", label="Flush type", data_type="enum",
                allowed_values=["siphonic"], synonyms={"siphonic": ["siphonic"]},
            ),
        ]
    )
    db.flush()
    for code, mounting, promo, level in (
        ("ZZTWH1", "wall_hung", "ZZT WH PROMO", "sorento_dealer"),
        ("ZZTFM1", "floor_mounted", "ZZT FM PROMO", fm_level),
    ):
        p = _prod(db, srt, "Water Closet", code)
        spec = db.query(ProductSpecifications).filter_by(product_id=p.id).one()
        spec.values = {"class": {"value": "Water Closet"}, "mounting": {"value": mounting}}
        db.flush()
        _promo(db, promo, std, p, levels=[level])
    db.commit()


def _ents(*extra, brand=True):
    ents = []
    if brand:
        ents.append({"raw": "sorento", "hint": "brand", "canonical_code": "Sorento", "current_message": True, "confident": True})
    ents.append({"raw": "water closet", "hint": "category", "canonical_code": "Water Closet", "current_message": True, "confident": True})
    ents.extend(extra)
    return ents


def _spec(raw, key="mounting", value="wall hung"):
    return {"raw": raw, "hint": "specification", "spec_key": key, "spec_value": value, "canonical_code": None, "current_message": True, "confident": True}


def _qf(*extra):
    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=_ents(*extra))


# --- AC-36 -------------------------------------------------------------------------------------


def test_the_parser_context_carries_a_listed_spec_values_line(session_factory, monkeypatch):
    _wc_world(session_factory)
    _mcp(monkeypatch, session_factory)
    blocks: list[str] = []
    _run_turn(session_factory, monkeypatch, qf=_qf(), text_body="sorento water closet promo", msg_id="zzt-sq-ctx", resolve_services=_wire(session_factory), capture_user_block=blocks)
    lines = [
        l.lower() for l in "\n".join(blocks).splitlines()
        if not l.startswith(("Known brands:", "Promotion kinds:", "Product types:"))
    ]
    assert any("mounting" in l and "wall hung" in l and "floor mounted" in l and "siphonic" in l for l in lines), blocks


def test_the_promotion_subject_prompt_tells_the_parser_to_emit_listed_qualifiers_as_spec_entities():
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT as prompt

    section = prompt.split("== PROMOTION SUBJECT ==", 1)[1].split("\n== ")[0].split("\n━")[0].lower()
    for needed in ("specification", "spec_key", "spec_value", "listed"):
        assert needed in section, (needed, section)


# --- AC-37 -------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["wall hung", "walll hugggg"])
def test_a_qualified_promo_ask_narrows_stamp_and_pick_to_the_spec(session_factory, monkeypatch, raw):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    _wc_world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    menu = _go(session_factory, monkeypatch, _qf(_spec(raw)), f"sorento {raw} water closet promo", "zzt-sq-ask")
    assert menu.splitlines()[:4] == [
        "Which access level do you need for Sorento, water closet?",
        "1. Office - no promotion",
        "2. Dealer - has promotion",
        "3. End user - no promotion",
    ] or menu.startswith("Which access level"), menu
    probes = [a for n, a in captured if n == PROMO_TOOL]
    assert probes and all("wall" in json.dumps(a).lower() for a in probes), probes
    reply = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-sq-2")
    assert "ZZT WH PROMO" in reply and "ZZT FM PROMO" not in reply, reply
    picks = [a for n, a in captured if n == PRODUCTS_TOOL]  # owner Q4 option (a), 12 Oct
    assert picks and all("wall" in json.dumps(a).lower() for a in picks), picks


def test_the_stamp_follows_the_qualified_filter(session_factory, monkeypatch):
    """Only a floor mounted promo exists at Office: a wall hung ask stamps Office "no promotion"."""
    _wc_world(session_factory, fm_level="sorento_office")
    _mcp(monkeypatch, session_factory)
    menu = _go(session_factory, monkeypatch, _qf(_spec("wall hung")), "sorento wall hung water closet promo", "zzt-sq-stamp")
    lines = menu.splitlines()
    assert "1. Office - no promotion" in lines and "2. Dealer - has promotion" in lines, menu


# --- AC-38 -------------------------------------------------------------------------------------


@pytest.mark.parametrize("spec", [_spec("zztwonky", key=None, value=None), _spec("zztwonky", key="mounting", value="zzt wonky")])
def test_an_unplaceable_qualifier_asks_back_never_drops_or_widens(session_factory, monkeypatch, spec):
    _wc_world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    reply = _go(session_factory, monkeypatch, _qf(spec), "sorento zztwonky water closet promo", "zzt-sq-unl")
    assert reply.startswith('I could not match "zztwonky" to a product detail.'), reply
    assert "Which access level" not in reply, reply
    assert not [a for n, a in captured if n == PROMO_TOOL], captured


# --- guard -------------------------------------------------------------------------------------


def test_an_unqualified_water_closet_promo_still_returns_both(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    _wc_world(session_factory)
    _mcp(monkeypatch, session_factory)
    _go(session_factory, monkeypatch, _qf(), "sorento water closet promo", "zzt-sq-plain")
    reply = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-sq-plain-2")
    assert "ZZT WH PROMO" in reply and "ZZT FM PROMO" in reply, reply
