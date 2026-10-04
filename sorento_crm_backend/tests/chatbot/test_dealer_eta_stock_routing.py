"""Owner hand test, 28 Sep 2026 (PR #1329): a dealer's stock ask gets the availability
answer, and a dealer's incoming reply is the deduped ETA list plus the salesperson line.
UAC AC-EO13 (dealer half), AC-EO15, AC-EO16
(`documentation/plans/chatbot/chatbot-eta-offset-per-contact-28sep-acceptance-criteria.md`).

Owner, verbatim: "stock is stock, incoming is incoming, no such thing as incoming stock"
and "it should just list deduped ETAs, and say please refer to sales person".

The product printed twice because two still-incoming lines share one ETA and the
container and quantities that told them apart were withheld from the contact. The stock
vs incoming routing half of the hand test ("stoick X", "check stock X" after an incoming
turn) is NOT pinned here: the owner ruled the routing patch out (28 Sep, #1352) and the
strip round removed it; the fix comes from the picker design.

Harness: the REAL engine, the REAL stock and incoming routes (through TestClient, the
MCP tool call being the one seam doubled), the REAL MCP presenter. Only the parser
verdict is stubbed: each turn's verdict is the reading the live parser gave or could give.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401 - first app import
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access  # noqa: F401
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _link_contact_company
from tests.chatbot.test_rearch_s3_roster_from_resolver import _seed_contact

CODE = "SRTWC286-SH-NEW"
STOCK_TOOL = "crm_inventory_stock_balance_list"
INCOMING_TOOL = "crm_incoming_stock_list"
#: The shipments' real ETA; the category offset is 5 days, so the contact reads 8 Sep.
REAL_ETA = date(2026, 9, 3)
TOLD_ETA = "2026-09-08"
LATER_REAL_ETA = date(2026, 9, 15)
LATER_TOLD_ETA = "2026-09-20"


def dealer_date(iso: str) -> str:
    """A date as the dealer view tells it (AVAIL-MODE-REPLIES: dd/mm/yyyy); staff keep ISO."""
    return date.fromisoformat(iso).strftime("%d/%m/%Y")
SALESPERSON = "ZZT Sean Lim"


def _mcp():
    root = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
    if str(root) not in sys.path:
        sys.path.append(str(root))
    from sorento_crm_mcp.catalog import CATALOG
    from sorento_crm_mcp.presenters import present_response

    return {t.name: t for t in CATALOG}, present_response


def _seed(sf, *, dealer: bool, salesperson: bool, later_shipment: bool = False, shipments: bool = True) -> None:
    from app.models.access import RespondContact, RespondContactCustomer, StockVisibilityPolicy
    from app.models.resources import Attachment
    from app.models.order import Customer
    from app.models.procurement import InboundShipment, InboundShipmentLine
    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from app.models.sales_agent import SalesAgent

    _seed_contact(sf, phone="+60111222333")
    _link_contact_company(sf, company_id=SORENTO)
    db = sf()
    set_company_scope(db, frozenset({SORENTO}))
    contact = (
        db.query(RespondContact).filter(RespondContact.respond_io_id == str(CONTACT_ID)).one()
    )
    if dealer:
        db.add(
            StockVisibilityPolicy(id=str(uuid.uuid4()), contact_id=contact.id, mode="availability")
        )
    if salesperson:
        agent = SalesAgent(
            id=str(uuid.uuid4()), sales_agent=f"ZZT{uuid.uuid4().hex[:5]}", person_label=SALESPERSON
        )
        db.add(agent)
        db.flush()
        cust = Customer(
            id=str(uuid.uuid4()),
            customer_code=f"ZZT{uuid.uuid4().hex[:6]}",
            customer_name="ZZT Dealer Sdn Bhd",
            company_id=SORENTO,
            sales_agent_id=agent.id,
        )
        db.add(cust)
        db.flush()
        db.add(
            RespondContactCustomer(
                contact_id=contact.id, customer_id=cust.id, company_id=SORENTO, is_primary=True
            )
        )
    cat = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=f"ZZTC-{uuid.uuid4().hex[:5]}",
        category_name="ZZT ETA",
        class_label="wc286",
        search_synonyms=[],
        chatbot_eta_offset_days=5,
        chatbot_max_qty=200,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=f"ZZTU-{uuid.uuid4().hex[:5]}", uom_name="u")
    db.add_all([cat, uom])
    db.flush()
    pid = str(uuid.uuid4())
    db.add(
        Product(
            id=pid,
            product_code=CODE,
            product_name="ZZT WC",
            category_id=cat.id,
            base_uom_id=uom.id,
            list_price=1,
            company_id=SORENTO,
        )
    )
    db.flush()
    etas = [REAL_ETA, REAL_ETA] + ([LATER_REAL_ETA] if later_shipment else [])
    if not shipments:
        etas = []  # REFER-SALESMAN: a product with nothing incoming, for the miss reply
    # Listed out of order on purpose: the dealer reply sorts.
    for n, eta in enumerate(reversed(etas)):
        # A packing list on each, so the stock ask's own `incoming` branch (R5: the
        # earliest still-incoming shipment WITH a packing list) can answer too.
        aid = str(uuid.uuid4())
        db.add(
            Attachment(
                id=aid,
                original_filename="packing-list.pdf",
                stored_filename=f"{aid}.pdf",
                file_path=f"/attachments/{aid}.pdf",
                mime_type="application/pdf",
            )
        )
        db.flush()
        shipment = InboundShipment(
            attachment_id=aid,
            id=str(uuid.uuid4()),
            shipment_number=f"ZZT-SHP-{uuid.uuid4().hex[:6]}-{n}",
            shipment_date=date(2026, 8, 1),
            estimated_arrival_date=eta,
            shipment_status="in_transit",
            company_id=SORENTO,
            shipping_container_number=f"ZZTCONT{n}",
        )
        db.add(shipment)
        db.flush()
        db.add(
            InboundShipmentLine(
                id=str(uuid.uuid4()),
                shipment_id=shipment.id,
                product_id=pid,
                quantity_shipped=10 + n,
                quantity_received=0,
                line_status="in_transit",
                company_id=SORENTO,
            )
        )
    db.commit()


class Console:
    """One contact on the Chatbot Console, turn by turn, through `engine.run_turn`.
    `live=True` (REFER-SALESMAN) sends the envelope a dealer on WhatsApp sends (`is_test`
    false), so the tail writes its `stock_asks` rows."""

    def __init__(self, session_factory, monkeypatch, stub_access, *, live: bool = False) -> None:
        self.sf = session_factory
        self.live = live
        self.state: dict[str, Any] | None = None
        self.tools: list[str] = []
        self._next: dict[str, Any] | None = None
        stub_access()
        specs, present = _mcp()

        def _override_db():
            s = session_factory()
            set_company_scope(s, frozenset({SORENTO}))
            yield s

        principal = {"id": str(uuid.uuid4()), "email": "zzt-dealer-eta@test.com"}
        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

        async def _scope():
            return frozenset({SORENTO})

        app.dependency_overrides[apply_company_scope] = _scope
        monkeypatch.setattr(
            UserPermissionService, "check_user_has_permission", lambda self, u, s: True
        )
        client = TestClient(app)

        def fake_call_tool(_self, name: str, args: dict[str, Any]) -> str:
            self.tools.append(name)
            params = {
                k: (json.dumps(v) if isinstance(v, dict) else v)
                for k, v in args.items()
                if v is not None and not k.startswith("_")
            }
            res = client.get(specs[name].path, params=params)
            assert res.status_code == 200, res.text
            return present(name, res.text)

        from app.services.ai_assistant_service import MCPRuntimeClient

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub",
                prompt_version=1,
                provider="openai",
                model="gpt-test",
                api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", lambda config, block: self._next)

    def say(self, text: str, v: dict[str, Any]) -> str:
        self._next = v
        self.tools = []
        out = engine_mod.run_turn(
            _envelope(
                is_test=not self.live,
                previous_conversation_state=self.state,
                message={
                    "event_type": "message.received",
                    "contact": {"id": CONTACT_ID},
                    "message": {
                        "messageId": f"ZZT-eta-{uuid.uuid4().hex[:10]}",
                        "contactId": CONTACT_ID,
                        "channelId": "whatsapp",
                        "traffic": "incoming",
                        "message": {"type": "text", "text": text},
                    },
                },
            ),
            session_factory=self.sf,
        )
        assert out.error is None, out.error
        self.state = out.session_patch
        return (out.reply or {}).get("text") or ""


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    try:
        yield lambda **seed: (_seed(session_factory, **seed), Console(session_factory, monkeypatch, stub_access))[1]
    finally:
        app.dependency_overrides.clear()


def _v(domain: str | None, intent: str | None = None, **extra: Any) -> dict[str, Any]:
    return verdict(
        domain_hint=domain,
        intent_hint=intent,
        entities=[entity(CODE, hint="product", quantity=extra.pop("quantity", None))],
        **extra,
    )


INCOMING = _v("incoming", "check_incoming")


def _dealer_reply(etas: list[str]) -> str:
    # REFER-SALESMAN (30 Sep 2026): the one refer sentence, with or without a salesperson
    # on the customer; the name is never printed.
    return f"{CODE}: \u2705 ETA {', '.join(dealer_date(e) for e in etas)}\n\nPlease refer to your salesman."


# --------------------------------------------------------- the dealer stock ask


def test_a_dealer_stock_ask_gets_the_availability_answer_not_figures(console):
    """What the availability-only policy allows on a stock ask the parser read as stock:
    the quantity question, then one sentence per product. No quantity of ours, no
    location, never the incoming reply. Routing a stock word the parser did NOT read as
    stock is left to the picker design on #1352 (the strip round took the word rule out)."""
    c = console(dealer=True, salesperson=True)
    c.say(f"incoming {CODE}", INCOMING)
    reply = c.say(f"check stock {CODE}", _v("inventory", "check_stock"))
    assert c.tools == [STOCK_TOOL], c.tools
    assert reply == f"How many units of {CODE}?"


# ------------------------------------------------------ the dealer reply (items 2 and 3)


def test_dealer_incoming_reply_is_the_deduped_eta_list_and_the_salesperson(console):
    c = console(dealer=True, salesperson=True)
    reply = c.say(f"incoming {CODE}", INCOMING)
    assert c.tools == [INCOMING_TOOL]
    assert reply == _dealer_reply([TOLD_ETA])


def test_dealer_incoming_reply_sorts_distinct_etas(console):
    c = console(dealer=True, salesperson=True, later_shipment=True)
    reply = c.say(f"incoming {CODE}", INCOMING)
    assert reply == _dealer_reply([TOLD_ETA, LATER_TOLD_ETA])


def test_dealer_with_no_salesperson_is_still_referred(console):
    c = console(dealer=True, salesperson=False)
    reply = c.say(f"incoming {CODE}", INCOMING)
    assert reply == _dealer_reply([TOLD_ETA])


def test_dealer_reply_never_names_the_salesperson(console):
    """AC-RS02: the customer's sales agent is on file, and still no name is printed."""
    c = console(dealer=True, salesperson=True)
    reply = c.say(f"incoming {CODE}", INCOMING)
    assert SALESPERSON not in reply
    assert "salesperson" not in reply
    assert reply.endswith("\n\nPlease refer to your salesman.")


def test_dealer_reply_carries_no_allocation_quantity_or_container(console):
    c = console(dealer=True, salesperson=True)
    reply = c.say(f"incoming {CODE}", INCOMING)
    for word in ("Allocation", "Quantity", "Container", "ZZTCONT", "PENDING", "1."):
        assert word not in reply, word
    assert reply.count(CODE) == 1


def test_staff_incoming_reply_is_unchanged(console):
    c = console(dealer=False, salesperson=True)
    reply = c.say(f"incoming {CODE}", INCOMING)
    assert reply.startswith("Here is the incoming stock I found.\n\n1. ")
    assert "*Container:* ZZTCONT0" in reply and "*Container:* ZZTCONT1" in reply
    assert "*Incoming Quantity:*" in reply
    assert f"*ETA:* {TOLD_ETA}" in reply
    assert "salesperson" not in reply
