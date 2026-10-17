"""CHAT-CLOSED-LISTS HELD-FAMILY (CHAT-STATE handoff, 17 Oct; TRAIN shapes, synthetic codes).

"incoming srtwc286?" with SRTWC286-SH held asked a 10-variant "Which product do you mean?";
the gold is the held variant. Rule (data, no words): when the closed candidate list for a
typed family code holds exactly one product the conversation already holds, the token is
that product. Two held members, or none, keep today's picker.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401

FAMILY = ("ZZTWC286-SH", "ZZTWC286-NW", "ZZTWC286-P")  # no bare code, as SRTWC286


def _seed(session_factory) -> dict[str, str]:
    db = session_factory()
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT WC", company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    ids = {}
    for code in FAMILY:
        p = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name {code}", category_id=cat.id,
            base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
        )
        db.add(p)
        ids[code] = p.id
    db.flush()
    drawings = AttachmentType(
        id=str(uuid.uuid4()), type_name="Technical Specifications", description="Technical Specifications / Spec / Drawing by Marketing",
        allowed_extensions="pdf",
    )
    db.add(drawings)
    db.flush()
    for code in FAMILY:
        att = Attachment(
            id=str(uuid.uuid4()), original_filename=f"{code}_TD.pdf", stored_filename=uuid.uuid4().hex,
            file_path="https://cdn.test/td.pdf", attachment_type_id=drawings.id, is_deleted=False, company_id=DEFAULT_COMPANY_ID,
        )
        db.add(att)
        db.flush()
        db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=ids[code], attachment_id=att.id, company_id=DEFAULT_COMPANY_ID))
    db.commit()
    return ids


def _message(text: str, n: int) -> dict[str, Any]:
    return {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": f"ZZT-held-{n}-{uuid.uuid4().hex[:6]}", "contactId": CONTACT_ID, "channelId": "whatsapp",
            "traffic": "incoming", "message": {"type": "text", "text": text},
        },
    }


def _turns(session_factory, stub_access, monkeypatch, turns: list[tuple[str, dict]], *, gate: bool = True):
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
    results = []
    for n, (text, _verdict) in enumerate(turns):
        calls.clear()
        results.append((engine_mod.run_turn(_envelope(message=_message(text, n)), session_factory=session_factory), list(calls)))
    return results


def _product(raw: str, *, current: bool = True) -> dict:
    return {"raw": raw, "hint": "product", "canonical_code": raw, "current_message": current, "confident": True}


def _ask(intent: str, domain: str, *entities: dict) -> dict:
    return _parser_output(intent_hint=intent, domain_hint=domain, user_goal="ask", entities=list(entities))


def _product_ids(calls) -> list[list[str]]:
    return [sorted(args.get("product_ids") or []) for _name, args in calls if args.get("product_ids")]


@pytest.mark.parametrize(
    "held, follow_up, domain, intent",
    [
        ("ZZTWC286-SH", "incoming zztwc286?", "incoming", "check_incoming"),
        ("ZZTWC286-NW", "zztwc286 drawing", "product_attachment", "check_product_attachment"),
        ("ZZTWC286-SH", "zztwc286", "inventory", "check_stock"),
    ],
)
def test_a_typed_family_code_with_one_held_member_is_that_member(
    held, follow_up, domain, intent, session_factory, seeded, stub_access, monkeypatch
):
    ids = _seed(session_factory)
    first = _ask("check_stock", "inventory", _product(held))
    entities = [_product("zztwc286")]
    if domain == "product_attachment":
        entities.append({"raw": "drawing", "hint": "attachment_type", "canonical_code": "technical drawing",
                         "listed_item": "Technical Specifications", "current_message": True, "confident": True})
    second = _ask(intent, domain, *entities)

    (_r1, _c1), (r2, c2) = _turns(session_factory, stub_access, monkeypatch, [(held, first), (follow_up, second)])

    text = (r2.reply or {}).get("text") or ""
    assert "Which product do you mean" not in text, text
    # The domain's own fetch is for the held product. (The incoming ladder's cross-check
    # line still probes the typed token's family: `answer.py`'s requested set, unchanged.)
    tool = {
        "incoming": "crm_incoming_stock_list",
        "product_attachment": "crm_master_product_attachments_list",
        "inventory": "crm_inventory_stock_balance_list",
    }[domain]
    own = [sorted(args.get("product_ids") or []) for name, args in c2 if name == tool]
    assert own and own[0] == [ids[held]], c2


def test_no_held_member_keeps_the_picker(session_factory, seeded, stub_access, monkeypatch):
    _seed(session_factory)
    second = _ask("check_incoming", "incoming", _product("zztwc286"))

    [(r, _c)] = _turns(session_factory, stub_access, monkeypatch, [("incoming zztwc286?", second)])

    assert "Which product do you mean" in ((r.reply or {}).get("text") or "")


def test_an_exactly_typed_other_member_is_not_overridden_by_the_held_one(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)
    first = _ask("check_stock", "inventory", _product("ZZTWC286-NW"))
    second = _ask("check_stock", "inventory", _product("ZZTWC286-P"))

    (_r1, _c1), (_r2, c2) = _turns(session_factory, stub_access, monkeypatch, [("ZZTWC286-NW", first), ("ZZTWC286-P", second)])

    own = [sorted(a.get("product_ids") or []) for n, a in c2 if n == "crm_inventory_stock_balance_list"]
    assert own and ids["ZZTWC286-NW"] not in own[0] and ids["ZZTWC286-P"] in own[0], c2


def test_a_family_ask_is_not_narrowed_to_the_held_member(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)
    first = _ask("check_stock", "inventory", _product("ZZTWC286-SH"))
    second = {**_ask("check_stock", "inventory", _product("zztwc286")), "family": True, "broaden_to": "family"}

    (_r1, _c1), (_r2, c2) = _turns(session_factory, stub_access, monkeypatch, [("ZZTWC286-SH", first), ("all zztwc286", second)])

    own = [sorted(a.get("product_ids") or []) for n, a in c2 if n == "crm_inventory_stock_balance_list"]
    assert own and len(own[0]) == len(FAMILY), c2


def test_gate_off_the_held_member_does_not_settle_the_picker(session_factory, seeded, stub_access, monkeypatch):
    """Production keeps the picker PR #833 round 13 pinned ("65502 eta" after "SRTKS65502
    eta", `test_attribute_asks_round7`) until the owner moves the label."""
    _seed(session_factory)
    first = _ask("check_stock", "inventory", _product("ZZTWC286-SH"))
    second = _ask("check_incoming", "incoming", _product("zztwc286"))

    (_r1, _c1), (r2, _c2) = _turns(
        session_factory, stub_access, monkeypatch, [("ZZTWC286-SH", first), ("incoming zztwc286?", second)], gate=False
    )

    assert "Which product do you mean" in ((r2.reply or {}).get("text") or "")
