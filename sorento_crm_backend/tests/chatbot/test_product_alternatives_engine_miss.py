"""PRODUCT-ALTERNATIVES round B, engine-level miss replies (owner rule, 9 Oct 2026).

When the asked product is discontinued and has a stored alternative (`product_alternatives`
row, source `auto_new`), `run_fetch` calls the same tool a second time for the alternative.
If the OLD code's own call returned nothing, the fetch item carries `alternative_lines` and
the miss reply (`answer_bridge.answer_for`) must print them BEFORE any escalate offer:

* before the "Related products:" block when there is one,
* otherwise right before the sentence carrying the offer ("Would you like me to escalate"
  / "reply 'yes' to escalate to ..."),
* with no offer at all, closing the reply.

Whole turns through `engine.run_turn`; only the parser and the MCP tool call are stubbed,
the database is the real blank-schema Postgres from `tests/chatbot/conftest.py`.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import _envelope, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_escalation_agent_carry import _db, _seed_contact
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")

OLD = "ZZRVBF11821"
NEW = "ZZRVBF11821-NEW"


def _seed_products(session_factory, *, old_discontinued: bool, linked: bool = True) -> None:
    """OLD (optionally discontinued) and NEW, one category/uom, plus the `auto_new` link."""
    from app.models.product import Product, ProductAlternative, ProductCategory, UnitOfMeasure

    db = _db(session_factory)
    cat = ProductCategory(
        id=str(uuid.uuid4()),
        category_code="ZZTC-ALT",
        category_name="ZZT",
        class_label="zzt",
        search_synonyms=[],
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZTU-ALT", uom_name="ZZT uom")
    db.add_all([cat, uom])
    db.flush()
    ids: dict[str, str] = {}
    for code, discontinued in ((OLD, old_discontinued), (NEW, False)):
        pid = str(uuid.uuid4())
        ids[code] = pid
        db.add(
            Product(
                id=pid,
                product_code=code,
                product_name=f"ZZT {code}",
                category_id=cat.id,
                base_uom_id=uom.id,
                list_price=1,
                is_active=True,
                is_discontinued=discontinued,
            )
        )
    db.flush()
    if linked:
        db.add(
            ProductAlternative(
                id=str(uuid.uuid4()),
                product_id=ids[OLD],
                alternative_product_id=ids[NEW],
                source="auto_new",
            )
        )
    db.commit()


def _stub_mcp(monkeypatch, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every MCP tool call answers `payload`; every `(name, args)` is recorded."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        calls.append((name, dict(arguments)))
        return json.dumps(payload)

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
    return calls


def _reply(session_factory) -> str:
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    return (result.reply or {}).get("text", "")


def test_incoming_miss_prints_alternative_before_related_products_and_offer(
    session_factory, stub_parser, stub_access, monkeypatch
) -> None:
    _seed_contact(session_factory, phone="+60900000501")
    _seed_products(session_factory, old_discontinued=True)
    calls = _stub_mcp(monkeypatch, {"answers": [], "items": [], "has_result": False})
    stub_parser(
        verdict(
            domain_hint="incoming",
            intent_hint="check_incoming",
            entities=[entity(OLD, hint="product", confident=True)],
            routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
        )
    )
    stub_access()

    text = _reply(session_factory)

    line = f"Alternative: {NEW}: No incoming stock."
    assert line in text, f"calls={calls!r}\nreply:\n{text}"
    at = text.index(line)
    assert "escalate" in text, text
    assert at < text.index("escalate"), f"Alternative line must precede the offer:\n{text}"
    if "Related products:" in text:
        assert at < text.index("Related products:"), f"Alternative line must precede the roster:\n{text}"


def test_product_photo_miss_prints_alternative_before_escalate_offer(
    session_factory, stub_parser, stub_access, monkeypatch
) -> None:
    from app.models.resources import AttachmentType

    _seed_contact(session_factory, phone="+60900000502")
    _seed_products(session_factory, old_discontinued=True)
    db = _db(session_factory)
    db.add(
        AttachmentType(
            id=str(uuid.uuid4()),
            code=None,
            type_name="Product Photos",
            allowed_extensions="jpg,jpeg,png,webp,gif",
            max_file_size_mb=10,
        )
    )
    db.commit()
    calls = _stub_mcp(monkeypatch, {"items": [], "attachments": [], "has_result": False})
    stub_parser(
        verdict(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[
                entity(OLD, hint="product", confident=True),
                entity("Product Photos", hint="attachment_type", confident=True),
            ],
            routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        )
    )
    stub_access()

    text = _reply(session_factory)

    line = f"Alternative: {NEW}: no Product Photos."
    assert line in text, f"calls={calls!r}\nreply:\n{text}"
    assert "Would you like me to escalate" in text, text
    assert text.index(line) < text.index("Would you like me to escalate"), text


def test_miss_on_non_discontinued_product_names_no_alternative_and_calls_once(
    session_factory, stub_parser, stub_access, monkeypatch
) -> None:
    _seed_contact(session_factory, phone="+60900000503")
    _seed_products(session_factory, old_discontinued=False)
    calls = _stub_mcp(monkeypatch, {"answers": [], "items": [], "has_result": False})
    stub_parser(
        verdict(
            domain_hint="incoming",
            intent_hint="check_incoming",
            entities=[entity(OLD, hint="product", confident=True)],
            routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
        )
    )
    stub_access()

    text = _reply(session_factory)

    assert "Alternative" not in text, text
    # The sibling picker's own probe also reads the tool (old + sibling ids together, a
    # different seam); the alternative path would call it for the alternative ALONE.
    from app.models.product import Product

    new_id = _db(session_factory).query(Product).filter(Product.product_code == NEW).one().id
    alone = [c for c in calls if c[0] == "crm_incoming_stock_list" and c[1].get("product_ids") == [new_id]]
    assert not alone, [(c[0], c[1].get("product_ids")) for c in calls]
    first = [c for c in calls if c[0] == "crm_incoming_stock_list" and len(c[1].get("product_ids") or []) == 1]
    assert len(first) == 1, [(c[0], c[1].get("product_ids")) for c in calls]
