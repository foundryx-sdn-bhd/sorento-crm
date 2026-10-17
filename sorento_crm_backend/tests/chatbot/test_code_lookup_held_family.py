"""CHAT-CODE-LOOKUP slice C, R5 (U5.1, U5.2): held family member with code_lookup on.

Owner card c08: held SRTWC286-SH-NEW + "Srtwc286 drawing" -> the held member, no picker.
Synthetic family ZZTWC286-*; gate = closed_lists AND code_lookup.
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
from app.services.chatbot.turn import policy_rows
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401

HELD = "ZZTWC286-SH-NEW"
FAMILY = (HELD, "ZZTWC286-SH", "ZZTWC286-P", "ZZTWC286-NW", "ZZTWC286-GM", "ZZTWC286-BL")
PICKER = "Which product do you mean"


def _seed(session_factory) -> dict[str, str]:
    db = session_factory()
    cat = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT WC", company_id=DEFAULT_COMPANY_ID)
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    ids = {}
    for code in FAMILY:
        p = Product(id=str(uuid.uuid4()), product_code=code, product_name=f"Name {code}", category_id=cat.id,
                    base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID)
        db.add(p)
        ids[code] = p.id
    db.flush()
    drawings = AttachmentType(id=str(uuid.uuid4()), type_name="Technical Specifications",
                              description="Technical Specifications / Spec / Drawing by Marketing", allowed_extensions="pdf")
    db.add(drawings)
    db.flush()
    for code in FAMILY:
        att = Attachment(id=str(uuid.uuid4()), original_filename=f"{code}_TD.pdf", stored_filename=uuid.uuid4().hex,
                         file_path="https://cdn.test/td.pdf", attachment_type_id=drawings.id, is_deleted=False,
                         company_id=DEFAULT_COMPANY_ID)
        db.add(att)
        db.flush()
        db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=ids[code], attachment_id=att.id, company_id=DEFAULT_COMPANY_ID))
    db.commit()
    return ids


def _message(text: str, n: int) -> dict[str, Any]:
    return {
        "event_type": "message.received", "contact": {"id": CONTACT_ID},
        "message": {"messageId": f"ZZT-cl-{n}-{uuid.uuid4().hex[:6]}", "contactId": CONTACT_ID, "channelId": "whatsapp",
                    "traffic": "incoming", "message": {"type": "text", "text": text}},
    }


def _dev_db_policy(monkeypatch) -> None:
    """Fix round 1 (F5, lead sign-off for this fixture change only): the dev DB carries
    master_products narrowing {"product": "list_all"} (chatbot_domains.narrowing), while the
    frozen seed the blank schema falls back to has {}. Seed the dev DB's row."""
    rows = [
        {**row, "narrowing": {"product": "list_all"}} if row["name"] == "master_products" else row
        for row in policy_rows.DEFAULT_DOMAIN_ROWS
    ]
    monkeypatch.setattr(policy_rows, "DEFAULT_DOMAIN_ROWS", rows)


def _turns(session_factory, stub_access, monkeypatch, turns: list[tuple[str, dict]], *, code_lookup: bool = True):
    def fake_resolve_config(db, *, current_date, override_version_id=None):
        extra = {"code_lookup": True} if code_lookup else {}
        return parser_mod.ParserConfig(system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k",
                                       closed_lists=True, **extra)

    queue = [v for _t, v in turns]
    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: queue.pop(0))
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(MCPRuntimeClient, "call_tool",
                        lambda self, name, args: calls.append((name, args)) or json.dumps({"ok": True, "items": []}))
    stub_access()
    out = []
    for n, (text, _v) in enumerate(turns):
        calls.clear()
        out.append((engine_mod.run_turn(_envelope(message=_message(text, n)), session_factory=session_factory), list(calls)))
    return out


def _product(raw: str) -> dict:
    return {"raw": raw, "hint": "product", "canonical_code": raw, "current_message": True, "confident": True}


def _ask(intent: str, domain: str, *entities: dict) -> dict:
    return _parser_output(intent_hint=intent, domain_hint=domain, user_goal="ask", entities=list(entities))


DRAWING = {"raw": "drawing", "hint": "attachment_type", "canonical_code": "technical drawing",
           "listed_item": "Technical Specifications", "current_message": True, "confident": True}


@pytest.mark.parametrize(
    "label, follow_up, domain, intent, tool, extra",
    [
        ("a_product_info", "zztwc286 price", "master_products", "product_info", "crm_master_products_list", []),
        ("b_stock", "zztwc286 stock", "inventory", "check_stock", "crm_inventory_stock_balance_list", []),
        ("c_eta", "zztwc286 eta", "incoming", "check_incoming", "crm_incoming_stock_list", []),
        ("d_drawing", "zztwc286 drawing", "product_attachment", "check_product_attachment",
         "crm_master_product_attachments_list", [DRAWING]),
    ],
)
def test_u5_1_held_member_settles_the_family_prefix(
    label, follow_up, domain, intent, tool, extra, session_factory, seeded, stub_access, monkeypatch
):
    ids = _seed(session_factory)
    if domain == "master_products":
        _dev_db_policy(monkeypatch)
    first = _ask("check_stock", "inventory", _product(HELD))
    second = _ask(intent, domain, _product("zztwc286"), *extra)

    (_r1, _c1), (r2, c2) = _turns(session_factory, stub_access, monkeypatch, [(HELD, first), (follow_up, second)])

    text = (r2.reply or {}).get("text") or ""
    assert PICKER not in text, text
    own = [sorted(a.get("product_ids") or []) for n, a in c2 if n == tool]
    assert own and own[0] == [ids[HELD]], c2


def test_u5_2_no_held_member_stays_a_pick(session_factory, seeded, stub_access, monkeypatch):
    _seed(session_factory)
    second = _ask("check_incoming", "incoming", _product("zztwc286"))

    [(r, _c)] = _turns(session_factory, stub_access, monkeypatch, [("zztwc286 eta", second)])

    assert PICKER in ((r.reply or {}).get("text") or "")


@pytest.mark.parametrize("code_lookup", [True, False])
def test_f5_a_resource_attachment_ask_after_a_held_product_sends_no_product_ids(
    code_lookup, session_factory, seeded, stub_access, monkeypatch
):
    """Fix round 1 (F5): the held-member rule is for product-narrowed domains only. A domain
    with no product narrowing (resource_attachment: "send me the stock list") reads no held
    product, gate on or off."""
    _seed(session_factory)
    first = _ask("check_stock", "inventory", _product(HELD))
    second = _ask("get_resource_attachment", "resource_attachment")

    (_r1, _c1), (_r2, c2) = _turns(
        session_factory, stub_access, monkeypatch,
        [(HELD, first), ("send me the stock list", second)], code_lookup=code_lookup,
    )

    own = [a for n, a in c2 if n == "crm_resource_attachments_list"]
    assert not any(a.get("product_ids") for a in own), c2
