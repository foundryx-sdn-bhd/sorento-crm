"""CHAT-CLOSED-LISTS SPEC-OVER-HELD (CHAT-STATE handoff, 17 Oct; TRAIN shapes, synthetic codes).

"How about p trap" after "ZZTWC286-SH x 10: no stock" answered every P-trap WC in the
catalogue; ZZTWC286-SH-P exists. Rule (data, no words): a message that names only a spec,
over a held product, narrows that product's family (the variant graph, `variant_of_id`) by
the spec, never the catalogue. Gated with the rest of the lane (the verdict's closed_lists).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401

ROWS = (
    # code, parent code, trap
    ("ZZTWC286-SH", None, "s_trap"),
    ("ZZTWC286-SH-P", "ZZTWC286-SH", "p_trap"),
    ("ZZTWC900-P", None, "p_trap"),
    ("ZZTWC901-P", None, "p_trap"),
)


def _seed(session_factory) -> dict[str, str]:
    db = session_factory()
    db.add(ProductSpecRegistry(
        id=str(uuid.uuid4()), spec_key="trap_type", label="Trap", data_type="enum",
        allowed_values=["s_trap", "p_trap"], synonyms={"p_trap": ["p trap"], "s_trap": ["s trap"]},
    ))
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("WC")[:50], category_name="ZZT WC", class_label="Water Closet",
        company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    ids: dict[str, str] = {}
    for code, parent, trap in ROWS:
        p = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name {code}", category_id=cat.id, base_uom_id=uom.id,
            list_price=1, company_id=DEFAULT_COMPANY_ID, variant_of_id=ids.get(parent) if parent else None,
        )
        db.add(p)
        db.flush()
        ids[code] = p.id
        db.add(ProductSpecifications(
            id=str(uuid.uuid4()), product_id=p.id, values={"class": {"value": "Water Closet"}, "trap_type": {"value": trap}},
        ))
    db.commit()
    return ids


def _message(text: str, n: int) -> dict[str, Any]:
    return {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": f"ZZT-soh-{n}-{uuid.uuid4().hex[:6]}", "contactId": CONTACT_ID, "channelId": "whatsapp",
            "traffic": "incoming", "message": {"type": "text", "text": text},
        },
    }


def _turns(session_factory, stub_access, monkeypatch, turns, *, gate: bool):
    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=gate,
        )

    queue = [verdict for _text, verdict in turns]
    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: queue.pop(0))
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        MCPRuntimeClient, "call_tool", lambda self, name, args: calls.append((name, args)) or json.dumps({"ok": True, "items": []})
    )
    stub_access()
    out = []
    for n, (text, _verdict) in enumerate(turns):
        calls.clear()
        out.append((engine_mod.run_turn(_envelope(message=_message(text, n)), session_factory=session_factory), list(calls)))
    return out


def _stock_ask(*entities: dict) -> dict:
    return _parser_output(intent_hint="check_stock", domain_hint="inventory", user_goal="stock", entities=list(entities))


HELD = {"raw": "ZZTWC286-SH", "hint": "product", "canonical_code": "ZZTWC286-SH", "current_message": True, "confident": True}
P_TRAP = {"raw": "p trap", "hint": "specification", "canonical_code": None, "spec_key": "trap_type", "spec_value": "p_trap",
          "current_message": True, "confident": True}


def _fetched(calls) -> list[str]:
    return sorted({pid for name, args in calls if name == "crm_inventory_stock_balance_list" for pid in args.get("product_ids") or []})


def test_a_spec_over_the_held_product_narrows_its_family_by_the_spec(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)

    (_r1, c1), (r2, c2) = _turns(
        session_factory, stub_access, monkeypatch, [("ZZTWC286-SH", _stock_ask(HELD)), ("how about p trap", _stock_ask(P_TRAP))],
        gate=True,
    )

    assert ids["ZZTWC286-SH"] in _fetched(c1)
    assert _fetched(c2) == [ids["ZZTWC286-SH-P"]], ((r2.reply or {}).get("text"), c2)


def test_gate_off_the_turn_is_unchanged(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)

    (_r1, _c1), (_r2, c2) = _turns(
        session_factory, stub_access, monkeypatch, [("ZZTWC286-SH", _stock_ask(HELD)), ("how about p trap", _stock_ask(P_TRAP))],
        gate=False,
    )

    assert _fetched(c2) != [ids["ZZTWC286-SH-P"]]


def _unit(session_factory, verdict, held_codes):
    from app.services.chatbot.head.held_family import narrow_held_family

    from app.models.base import set_company_scope

    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))  # the engine's sessions are always scoped
    held = [{"uuid": str(pid), "canonical_code": code} for pid, code in db.query(Product.id, Product.product_code)
            .filter(Product.product_code.in_(held_codes)).all()]
    return narrow_held_family(db, verdict, held)


def test_the_family_reaches_the_parent_of_a_held_variant(session_factory):
    """c05 shape: CB94SS-BL held, "I mean this got chrome colour": the chrome member is the
    parent CB94SS. Here: ZZTWC286-SH-P held, "s trap?" -> its parent ZZTWC286-SH."""
    _seed(session_factory)
    s_trap = {**P_TRAP, "raw": "s trap", "spec_value": "s_trap"}

    verdict, note = _unit(session_factory, {"entities": [s_trap]}, ["ZZTWC286-SH-P"])

    assert [e["canonical_code"] for e in verdict["entities"] if e["hint"] == "product"] == ["ZZTWC286-SH"]
    assert note["matched"] == ["ZZTWC286-SH"]


def test_no_member_with_the_spec_leaves_the_verdict_as_it_was(session_factory):
    _seed(session_factory)
    verdict = {"entities": [{**P_TRAP, "raw": "s trap", "spec_value": "s_trap"}]}

    assert _unit(session_factory, verdict, ["ZZTWC900-P"]) == (verdict, None)


def test_a_message_naming_its_own_product_is_not_narrowed(session_factory):
    _seed(session_factory)
    verdict = {"entities": [P_TRAP, {**HELD, "raw": "ZZTWC900-P", "canonical_code": "ZZTWC900-P"}]}

    assert _unit(session_factory, verdict, ["ZZTWC286-SH"]) == (verdict, None)
