"""PV2 R6 step 0: the live-parser CONTRACT test (PLAN-pv2-r6.md behaviour 0, "Step 0 seam").

Replays every recorded TRAIN conversation (`fixtures/r6_train_parses.json`: 135 conversations, the parser's own
outputs, masked) through the REAL engine turn: `engine.run_turn` with the contact on parser v2 (`_go_v2`), the
real `pv2.turn.run_turn_v2`, the real DbResolver + apply + answer, the real v1 lanes for a PV2 verdict, the real
register writer and the real `session_vars.pv2` carry. Only the edges are doubled:

* the parser: `pv2.call.parse_v2` pops the recorded parse of this turn (and records the MEMORY block it was
  handed); `pv2.call.resolve_v2_config` hands a config whose index keys are the ones the recordings use. A turn
  recorded with NO parse (`parse: null`) raises `ParserV2Error`, the failure path PV2 already has (the engine's
  failed `understood`, no state written); its reply is not judged.
* the MCP server: `MCPRuntimeClient.call_tool` answers from the seeded world through the real presenter
  (`sorento_crm_mcp.presenters.present_response`), so a lookup prints the world's rows and the register fills.
* access: `engine.check_access` (via `_run_turn`) and `head.access._granted_field_reveal_keys` grant everything,
  so no turn is refused for a missing grant; the asker differs only by staff tier vs a linked dealer.

Every turn is held to the invariants below; no expected reply is asserted. Violations are collected over the
whole replay and each invariant fails ONCE per asker with a compact summary.

  I1  the reask line ("Sorry, I didn't catch your choice") only when a question was open at the turn's start
  I2  no entity / which_held / domain / other question with zero options when the parse carries a named entity,
      a refers_to_held entity or a domain, or when the last answer printed rows
  I3  a pick answering a numbered question never re-prints that same question (kind + asked + labels)
  I4  every numbered line printed is an option of the stored pending question as MEMORY shows it next turn
      (or a row of LAST ANSWER ROWS there: the numbered rows a lookup prints are MEMORY's rows, not a question)
  I5  a product reference ("this product": kind product, refers_to_held, no key) is never offered a document
  I6  no reply text contains a UUID or an internal key
  C   an `unclear` turn keeps the customer, domain and period it parsed in held

Switch: env `PV2_CONTRACT_LIVE=1` skips the world seed and runs on the database `SORENTO_ENV_FILE` points at, as
it is (everything this test writes is still rolled back). Default (CI, lane): a blank schema seeded from
`fixtures/r6_train_world.json`. No LLM, no network.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sys
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot.pv2 import call as pv2_call_mod
from app.services.chatbot.pv2.schema import IndexKeys, validate
from app.services.chatbot.pv2.state import ClarifyAct, ConversationState, load_state
from app.services.company_scope import DEFAULT_COMPANY_ID

FIXTURES = Path(__file__).parent / "fixtures"
PARSES = json.loads((FIXTURES / "r6_train_parses.json").read_text(encoding="utf-8"))
WORLD = json.loads((FIXTURES / "r6_train_world.json").read_text(encoding="utf-8"))
LIVE = os.environ.get("PV2_CONTRACT_LIVE") == "1"

ASKERS = ("staff", "dealer")
INVARIANTS = ("I1", "I2", "I3", "I4", "I5", "I6", "C")
REASK_LINES = (
    "Sorry, I didn't catch your choice",
    "抱歉，我没看明白您的选择",
    "Maaf, saya kurang pasti pilihan anda",
)
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
NUMBERED_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(\S.*?)\s*$")
DOC_KINDS = frozenset({
    "delivery_order", "sales_order", "order", "order_number", "purchase_order", "customer_order",
    "customer_po_ref", "inbound_shipment", "quotation", "invoice", "packing_list", "spo",
})
ZERO_OPTION_ABOUTS = frozenset({"entity", "which_held", "domain", "other"})

ALL_GRANTS = [
    "delivery_orders.order_number", "delivery_orders.customer", "delivery_orders.branch",
    "delivery_orders.order_date", "delivery_orders.delivery_date", "delivery_orders.status",
    "delivery_orders.pickup_time", "delivery_orders.transporter", "delivery_orders.driver",
    "delivery_orders.lorry_plate", "delivery_orders.warehouse", "delivery_orders.products",
    "purchase_orders.supplier", "purchase_orders.placed", "purchase_orders.cost", "inventory.sellable",
    "sales_orders.outstanding", "sales_orders.sales_report", "chatbot.analytics",
]


# ============================================================================ the seeded world

#: The world file lists every customer / product / document PHRASE the parses carry, typos and references
#: included ("Brightwell", "DBrightwell tiles SDN BHD", "this product"). The rows those phrases name:
CUSTOMERS = [
    # One customer group with two ledger accounts: "Brightwell", "Brightwell tiles SDN BHD", "BRIGHTWELL TILES
    # SDN BHD" and "DBrightwell tiles SDN BHD" are the same company in the recordings.
    ("300-B001", "BRIGHTWELL TILES SDN BHD [A/C I]", 1),
    ("300-B002", "BRIGHTWELL TILES SDN BHD [A/C II]", 2),
    ("300-P001", "P & Q GLOBAL HARDWARE SDN BHD", None),
    ("300-Q001", "QZV TRADING SDN BHD", None),
    ("300-W001", "WXD HARDWARE ENTERPRISE", None),
]
CUSTOMER_OF_PHRASE = {
    "brightwell": ("300-B001", "300-B002"),
    "dbrightwell": ("300-B001", "300-B002"),
    "p & q": ("300-P001",),
    "qzv": ("300-Q001",),
    "wxd": ("300-W001",),
}
DEFAULT_DEALER_CUSTOMER = ("300-P001",)
#: Phrases in `product_codes` that are references or words, not codes.
NOT_CODES = {"NEW one", "NEW ones", "the NEW one", "the srtwc286 one", "the srtwc286 one i mean", "this product",
             "mou", "PP", "3001"}
WAREHOUSES = ("BRW", "KL")
#: Documents: (number, kind, customer code, line product codes). Kinds are what the parses tag them as.
DELIVERY_ORDERS = [
    ("202609-2012", "300-B001", ["SRTWB1096", "SRTWT7203"]),
    ("202609-3750", "300-P001", ["SRTWC286-SH", "SRTWC8608"]),
    ("202610-0147", "300-B002", ["CB1512ASS"]),
    ("202610-0151", "300-P001", ["SRTW5000", "SRTWB246", "SRTKS7061"]),
    ("PS202609-0469", "300-Q001", ["SRTWT5807"]),
    ("MFG6644-pp", "300-W001", ["SRTWT5827"]),
]
SALES_ORDERS = [("SO422057", "300-B001"), ("SO420420", "300-P001")]
PURCHASE_ORDERS = ["202606-S0105"]
BRANDS = {"SORENTO": "SORENTO", "MOCHA": "MOCHA"}
CLASSES = {"kitchen_sink": "kitchen sink", "tap": "tap", "water_closet": "water closet"}


def _world_codes() -> list[str]:
    seen: dict[str, str] = {}
    for code in WORLD["product_codes"]:
        if code in NOT_CODES:
            continue
        seen.setdefault(code.upper(), code.upper())
    for _n, _c, lines in DELIVERY_ORDERS:
        for code in lines:
            seen.setdefault(code.upper(), code.upper())
    return sorted(seen)


PRODUCT_CODES = _world_codes()


def _parse_values(key: str) -> set[str]:
    out: set[str] = set()
    for conv in PARSES:
        for turn in conv["turns"]:
            for ask in (turn["parse"] or {}).get("asks") or []:
                value = ask.get(key)
                for v in value if isinstance(value, list) else [value]:
                    if v:
                        out.add(v)
    return out


def _index_keys() -> IndexKeys:
    """The pinned index the recordings were parsed against, as far as they use it: every key they emit."""
    entity_keys: dict[str, set[str]] = {}
    kinds: set[str] = set()
    for conv in PARSES:
        for turn in conv["turns"]:
            for ask in (turn["parse"] or {}).get("asks") or []:
                for e in ask.get("entities") or []:
                    kinds.add(e["kind"])
                    if e.get("key"):
                        entity_keys.setdefault(e["kind"], set()).add(e["key"])
    return IndexKeys(
        domains=frozenset(_parse_values("domain")),
        entity_kinds=frozenset(kinds),
        entity_keys={k: frozenset(v) for k, v in entity_keys.items()},
        attachment_types=frozenset(_parse_values("attachment_type")),
        report_datasets=frozenset({"sales", "orders", "stock", "purchases"}),
        report_measures=frozenset({"amount", "qty", "count", "orders"}),
        report_dimensions=frozenset({"customer", "product", "brand", "month", "agent", "warehouse", "category"}),
        report_bases=frozenset({"delivered", "ordered"}),
        teams=frozenset({"CS", "SALES"}),
    )


INDEX_KEYS = _index_keys()


@dataclass
class World:
    product_ids: dict[str, str] = field(default_factory=dict)  # uuid -> code
    customer_ids: dict[str, str] = field(default_factory=dict)  # code -> uuid
    customer_names: dict[str, str] = field(default_factory=dict)  # uuid -> name
    order_ids: dict[str, str] = field(default_factory=dict)  # uuid -> DO number


def _seed_world(factory) -> World:
    from app.models.inventory import Warehouse
    from app.models.order import Customer, Order, OrderLine
    from app.models.procurement import PurchaseOrder
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
    from app.models.resources import AttachmentType
    from tests.chatbot.pv2._resolve_seed import create_pv2_tables

    world = World()
    db = factory()
    create_pv2_tables(db)
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZTU-r6", uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add(uom)
    cats = {}
    for key, label in CLASSES.items():
        cats[key] = ProductCategory(id=str(uuid.uuid4()), category_code=f"ZZTC-{key}"[:50],
                                    category_name=label.title(), class_label=label.title(), search_synonyms=[label],
                                    is_searchable=True, company_id=DEFAULT_COMPANY_ID)
    plain = ProductCategory(id=str(uuid.uuid4()), category_code="ZZTC-r6", category_name="ZZT R6",
                            is_searchable=True, company_id=DEFAULT_COMPANY_ID)
    db.add_all([*cats.values(), plain])
    brands = {}
    for key, name in BRANDS.items():
        brands[key] = Brand(id=str(uuid.uuid4()), brand_code=key, brand_name=name, is_active=True,
                            company_id=DEFAULT_COMPANY_ID)
    db.add_all(brands.values())
    for code in ("product_photos", "technical_specifications"):
        db.add(AttachmentType(id=str(uuid.uuid4()), code=code, type_name=code.replace("_", " ").title(),
                              allowed_extensions="pdf"))
    db.flush()

    def cat_of(code: str):
        if code.startswith(("SRTKS", "CKS", "SRTKT")):
            return cats["kitchen_sink"]
        if code.startswith(("SRTWT", "SRTBF", "SRTUB")):
            return cats["tap"]
        if code.startswith(("SRTWC", "WC")):
            return cats["water_closet"]
        return plain

    by_code: dict[str, str] = {}
    for code in PRODUCT_CODES:
        pid = str(uuid.uuid4())
        db.add(Product(id=pid, product_code=code, product_name=f"ZZT {code}", category_id=cat_of(code).id,
                       brand_id=brands["MOCHA" if code.startswith(("MBF", "CB", "CG", "CK")) else "SORENTO"].id,
                       base_uom_id=uom.id, list_price=1, is_active=True, company_id=DEFAULT_COMPANY_ID))
        world.product_ids[pid] = code
        by_code[code] = pid
    for code, name, level in CUSTOMERS:
        cid = str(uuid.uuid4())
        db.add(Customer(id=cid, customer_code=code, customer_name=name, is_active=True, account_level=level,
                        company_id=DEFAULT_COMPANY_ID))
        world.customer_ids[code] = cid
        world.customer_names[cid] = name
    whs = {}
    for code in WAREHOUSES:
        whs[code] = Warehouse(id=str(uuid.uuid4()), warehouse_code=code, warehouse_name=f"ZZT {code}",
                              company_id=DEFAULT_COMPANY_ID)
    db.add_all(whs.values())
    db.flush()
    for number, cust, lines in DELIVERY_ORDERS:
        oid = str(uuid.uuid4())
        world.order_ids[oid] = number
        db.add(Order(id=oid, order_number=number, customer_id=world.customer_ids[cust],
                     debtor_name=world.customer_names[world.customer_ids[cust]], warehouse="BRW",
                     company_id=DEFAULT_COMPANY_ID))
        db.flush()
        for seq, code in enumerate(lines, 1):
            db.add(OrderLine(id=str(uuid.uuid4()), line_sequence=seq, order_id=oid, product_id=by_code[code],
                             warehouse_id=whs["BRW"].id, quantity=seq + 1, company_id=DEFAULT_COMPANY_ID))
    for number in PURCHASE_ORDERS:
        db.add(PurchaseOrder(id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, po_number=number, status="active"))
    db.commit()
    from tests.chatbot.test_so_number_ask import _seed_so

    for number, cust in SALES_ORDERS:
        cid = world.customer_ids[cust]
        _seed_so(factory, number, customer_id=cid, lines=[(4, 1)], debtor_name=world.customer_names[cid])
    return world


def _live_world(factory) -> World:
    """PV2_CONTRACT_LIVE: the eval database as it is; only the ids the MCP double maps back to codes are read."""
    world = World()
    db = factory()
    for pid, code in db.execute(text("SELECT id, product_code FROM products")).all():
        world.product_ids[str(pid)] = code
    for cid, code, name in db.execute(text("SELECT id, customer_code, customer_name FROM customers")).all():
        world.customer_ids[str(code)] = str(cid)
        world.customer_names[str(cid)] = name
    return world


# ============================================================================ the MCP double (no network)

class WorldMcp:
    """`MCPRuntimeClient.call_tool` answering from the world, rendered by the real presenter."""

    def __init__(self, world: World) -> None:
        from tests.chatbot.pv2._register_envelopes import present_response

        self.world = world
        self.present = present_response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _codes(self, args: dict[str, Any]) -> list[str]:
        ids = args.get("product_ids") or []
        if isinstance(ids, str):
            ids = [s for s in re.split(r"[\s,\[\]\"]+", ids) if s]
        codes = [self.world.product_ids[i] for i in ids if i in self.world.product_ids]
        for key in ("product_code", "product_codes", "search", "query"):
            v = args.get(key)
            for s in v if isinstance(v, list) else [v]:
                if isinstance(s, str) and s.upper() in PRODUCT_CODES:
                    codes.append(s.upper())
        if codes or "product_ids" in args:
            return list(dict.fromkeys(codes))
        return sorted(self.world.product_ids.values())[:2]

    @staticmethod
    def _list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [s for s in re.split(r"[\s,\[\]\"]+", value) if s]
        return [str(v) for v in value or []]

    def _orders(self, args: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
        """The world's DOs the call names (`order_ids`), else its first three; a `customer_ids` filter holds."""
        rows = list(DELIVERY_ORDERS)
        if "order_ids" in args:
            wanted = {self.world.order_ids.get(i) for i in self._list(args.get("order_ids"))}
            rows = [r for r in rows if r[0] in wanted]
        else:
            rows = rows[:3]
        if args.get("customer_ids"):
            allowed = set(self._list(args.get("customer_ids")))
            rows = [r for r in rows if self.world.customer_ids.get(r[1]) in allowed]
        return rows

    def raw(self, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        cname = lambda code: self.world.customer_names.get(self.world.customer_ids.get(code, ""), code)  # noqa: E731
        if name in ("crm_order_management_orders_list", "crm_order_management_orders_by_product_list"):
            return {"data": [
                {"order_number": n, "debtor_name": cname(c), "order_date": "2026-09-10",
                 "actual_delivery_date": "2026-09-11", "order_status": "Delivered", "warehouse": "BRW",
                 "lines": [{"quantity": 2, "product": {"product_code": p}} for p in lines]}
                for n, c, lines in self._orders(args)]}
        if name == "crm_inventory_stock_balance_list":
            codes = self._codes(args)
            return {"data": [], "pagination": {"total": len(codes), "page": 1, "limit": 50}, "empty": False,
                    "stock_visibility": {"mode": "compact", "warehouse_codes": ["BRW", "KL"], "source": "contact"},
                    "stock_summary": [
                        {"product_id": next((k for k, v in self.world.product_ids.items() if v == c), None),
                         "product_code": c, "product_name": c, "total_on_hand": 40,
                         "locations": [{"warehouse_code": "BRW", "quantity_on_hand": 40}],
                         "flags": {"discontinued": False}} for c in codes],
                    "last_updated_at": "2026-10-14T18:00:00"}
        if name in ("crm_incoming_stock_list", "crm_incoming_stock_by_product"):
            codes = self._codes(args)
            return {"data": [{"shipment_number": "ZZTSHIP01", "shipping_container_number": "ZZTU0000001",
                              "estimated_arrival_date": "2026-11-02",
                              "lines": [{"product_code": c, "product_name": c, "remaining_incoming_quantity": 30,
                                         "warehouse_allocations": [{"warehouse_code": "BRW",
                                                                    "allocated_quantity": 30}]}
                                        for c in codes]}]}
        if name == "crm_procurement_po_placed_list":
            return {"data": [{"po_number": "202606-S0105", "product_code": c, "outstanding_qty": 20,
                              "expected_date": "2026-11-09", "supplier": "ZZT Supplies"} for c in self._codes(args)]}
        if name == "crm_master_product_attachments_list":
            return {"data": [
                {"product": {"product_code": c, "product_name": c},
                 "attachment": {"original_filename": f"{c}-drawing.pdf", "file_path": f"https://cdn.test/{c}.pdf",
                                "attachment_type": {"code": "technical_specifications",
                                                    "type_name": "Technical Specifications"}}}
                for c in self._codes(args)]}
        if name == "crm_master_products_list":
            return {"data": [{"product_code": c} for c in self._codes(args)]}
        return None

    def __call__(self, client, name: str, args: dict[str, Any]) -> str:
        self.calls.append((name, dict(args)))
        raw = self.raw(name, args)
        if raw is None:
            return json.dumps({"has_result": False, "items": []})
        try:
            return self.present(name, json.dumps(raw))
        except Exception:  # noqa: BLE001 - a presenter that cannot render this shape: the empty answer
            return json.dumps({"has_result": False, "items": []})


# ============================================================================ the replay


@dataclass
class TurnRecord:
    conv: str
    idx: int
    message: str
    parse: dict[str, Any] | None
    reply: str = ""
    memory: str | None = None
    state_in: ConversationState | None = None
    state_out: ConversationState | None = None
    actions: list[Any] = field(default_factory=list)
    error: str | None = None
    next_memory: str | None = None
    tools: list[str] = field(default_factory=list)


def _named(parse: dict[str, Any] | None) -> bool:
    for ask in (parse or {}).get("asks") or []:
        if ask.get("domain"):
            return True
        for e in ask.get("entities") or []:
            if e.get("refers_to_held") or e.get("phrase") or e.get("key") or e.get("doc_number"):
                return True
    return False


def _customer_phrases(conv: dict[str, Any]) -> list[str]:
    return [e["phrase"] for t in conv["turns"] for a in (t["parse"] or {}).get("asks") or []
            for e in a.get("entities") or [] if e["kind"] == "customer" and e.get("phrase")]


def _dealer_links(conv: dict[str, Any]) -> tuple[str, ...]:
    for phrase in _customer_phrases(conv):
        low = phrase.lower()
        for stem, codes in CUSTOMER_OF_PHRASE.items():
            if low.startswith(stem):
                return codes
    return DEFAULT_DEALER_CUSTOMER


@contextlib.contextmanager
def _connection():
    if LIVE:
        from app.database import engine

        connection = engine.connect()
        transaction = connection.begin()
    else:
        from tests import _pg_fixture
        from tests._pg_fixture import blank_schema_engine

        connection = blank_schema_engine().connect()
        transaction = connection.begin()
        name = _pg_fixture._BLANK["name"]
        connection.exec_driver_sql(
            f'SET LOCAL search_path TO "{name}", "{name}_scm", "{name}_dealer_kit", '
            f'"{name}_chatbot", "{name}_sales", "{name}_finance", "{name}_projects", "public"'
        )
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def _no_network(monkeypatch) -> None:
    """Belt and braces for "$0, no network": any connect to a non-loopback address fails the turn loudly."""
    real = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost") and not host.startswith("/"):
            raise AssertionError(f"r6 contract test: network connect to {address!r}")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)

    def no_llm(*a, **k):
        raise AssertionError("r6 contract test: an LLM call was attempted")

    from app.services.chatbot import llm_call

    monkeypatch.setattr(llm_call, "chat", no_llm)


def _replay(asker: str, convs: list[dict[str, Any]]) -> list[list[TurnRecord]]:
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot.pv2 import turn as pv2_turn
    from app.services.chatbot import turn_runtime
    from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2
    from tests.chatbot.test_engine import CONTACT_ID
    from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact

    mp = pytest.MonkeyPatch()
    results: list[list[TurnRecord]] = []
    try:
        _no_network(mp)
        with _connection() as connection:
            opened: list[Any] = []

            def factory():
                from sqlalchemy.orm import Session

                s = Session(bind=connection, join_transaction_mode="create_savepoint")
                opened.append(s)
                return s

            world = _live_world(factory) if LIVE else _seed_world(factory)
            for s in opened:
                s.close()
            opened.clear()
            mcp = WorldMcp(world)
            mp.setattr(MCPRuntimeClient, "call_tool", lambda client, name, args: mcp(client, name, args))
            # `_run_turn` hands the fetch step its own `_capturing_mcp` double; it answers from the world too.
            import tests.chatbot.test_outstanding_lane as lane_tests

            mp.setattr(lane_tests, "_capturing_mcp", lambda response=None: (lambda name, args: mcp(None, name, args), []))
            mp.setattr("app.services.chatbot.head.access._granted_field_reveal_keys",
                       lambda db, contact_id, space_id: list(ALL_GRANTS))
            mp.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)

            current: dict[str, Any] = {}
            cfg = SimpleNamespace(keys=INDEX_KEYS, index_sha="r6-contract", prompt_version=1, version_id="v-r6",
                                  cache_key="pv2:r6-contract")
            fake_call = types.ModuleType("app.services.chatbot.pv2.call")
            fake_call.ParserV2Error = pv2_call_mod.ParserV2Error
            fake_call.resolve_v2_config = lambda db, override_version_id=None, **kw: cfg

            def parse_v2(config, *, history, memory_block, text):
                current["memory"] = memory_block
                raw = current.get("parse")
                if raw is None:
                    raise pv2_call_mod.ParserV2Error("r6 contract: recorded parse is null")
                output, dropped = validate(json.loads(json.dumps(raw)), INDEX_KEYS)
                return SimpleNamespace(output=output, usage={}, dropped=dropped)

            fake_call.parse_v2 = parse_v2
            import app.services.chatbot.pv2 as pv2_pkg

            mp.setitem(sys.modules, "app.services.chatbot.pv2.call", fake_call)
            mp.setattr(pv2_pkg, "call", fake_call, raising=False)

            real_apply = pv2_turn.apply_turn

            def watched_apply(state_in, out, **kw):
                state_out, actions = real_apply(state_in, out, **kw)
                current["state_in"] = state_in
                current["actions"] = list(actions)
                return state_out, actions

            mp.setattr(pv2_turn, "apply_turn", watched_apply)

            def saved_state() -> ConversationState | None:
                db = factory()
                row = db.execute(text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
                                 {"c": str(CONTACT_ID)}).first()
                db.close()
                raw = row.session_vars if row is not None else None
                raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
                pv2 = raw.get("pv2") if isinstance(raw, dict) else None
                if not pv2:
                    return None
                try:
                    from datetime import datetime, timezone

                    return load_state(pv2, datetime.now(timezone.utc))
                except Exception:  # noqa: BLE001
                    return None

            for conv in convs:
                savepoint = connection.begin_nested()
                records: list[TurnRecord] = []
                try:
                    _seed_contact(factory, variables={})
                    _go_v2(factory)
                    db = factory()
                    if asker == "staff":
                        db.execute(text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) "
                                        "WHERE respond_io_id = :c"), {"p": '{"tier": "office"}', "c": str(CONTACT_ID)})
                    else:
                        for i, code in enumerate(_dealer_links(conv)):
                            cid = world.customer_ids.get(code) if not LIVE else next(iter(world.customer_ids.values()))
                            db.execute(text(
                                "INSERT INTO respond_contact_customers (id, contact_id, customer_id, company_id, "
                                "created_at) SELECT gen_random_uuid(), id, :cust, :co, "
                                "TIMESTAMP '2026-01-01 00:00:00' + make_interval(secs => :i) "
                                "FROM respond_contacts WHERE respond_io_id = :c"),
                                {"cust": cid, "co": DEFAULT_COMPANY_ID, "c": str(CONTACT_ID), "i": i})
                    db.commit()
                    turns = [*conv["turns"], None]  # the trailing probe reads the MEMORY after the last turn
                    for idx, t in enumerate(turns):
                        current.clear()
                        current["parse"] = t["parse"] if t is not None else None
                        rec = TurnRecord(conv=conv["conv"], idx=idx, message=(t or {}).get("message") or "ok",
                                         parse=current["parse"])
                        n_calls = len(mcp.calls)
                        try:
                            result, _captured = _run_turn(
                                factory, mp, qf={}, text_body=rec.message,
                                msg_id=f"ZZT-r6-{uuid.uuid4().hex[:12]}", attributes=list(ALL_GRANTS),
                                real_resolver=True)
                            rec.reply = ((result.reply or {}).get("text") or "") if result is not None else ""
                        except Exception as exc:  # noqa: BLE001 - a crashed turn is a finding, not a harness stop
                            rec.error = f"{type(exc).__name__}: {exc}"[:200]
                        rec.tools = [f"{n} {sorted(a)}" for n, a in mcp.calls[n_calls:]]
                        rec.memory = current.get("memory")
                        rec.state_in = current.get("state_in")
                        rec.actions = current.get("actions") or []
                        rec.state_out = saved_state()
                        if records:
                            records[-1].next_memory = rec.memory
                        if t is not None:
                            records.append(rec)
                        for s in opened:
                            with contextlib.suppress(Exception):
                                s.close()
                        opened.clear()
                finally:
                    for s in opened:
                        with contextlib.suppress(Exception):
                            s.close()
                    opened.clear()
                    savepoint.rollback()
                results.append(records)
    finally:
        mp.undo()
    return results


# ============================================================================ the invariants


def _clarifies(rec: TurnRecord) -> list[ClarifyAct]:
    return [a for a in rec.actions if isinstance(a, ClarifyAct)]


def _memory_options(memory: str | None) -> tuple[dict[int, str], dict[int, str]]:
    """(pending option labels, LAST ANSWER ROWS labels) by number, read off a MEMORY block."""
    opts: dict[int, str] = {}
    rows: dict[int, str] = {}
    for line in (memory or "").splitlines():
        s = line.strip()
        if s.startswith("options:"):
            for part in s[len("options:"):].split(" | "):
                m = re.match(r"\s*(\d+)=(.*?) \((.*)\)\s*$", part)
                if m:
                    opts[int(m.group(1))] = m.group(3)
        elif s.startswith("LAST ANSWER ROWS:"):
            body = re.sub(r"\s\(\d+ of \d+\)$", "", s[len("LAST ANSWER ROWS:"):])
            for part in body.split(" | "):
                m = re.match(r"\s*(\d+)=(.*)$", part)
                if m:
                    # `n=<row kind> <label>` (a child adds ` (in <n>)`), or `n=<label>` for a report row.
                    rows[int(m.group(1))] = re.sub(r"\s\(in \d+\)$", "", m.group(2))
    return opts, rows


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def _matches(printed: str, label: str) -> bool:
    p, lb = _norm(printed), _norm(label)
    return bool(lb) and (lb in p or p in lb)


def _internal_keys() -> set[str]:
    keys = {k for k in INDEX_KEYS.attachment_types if "_" in k}
    keys |= {k for v in INDEX_KEYS.entity_keys.values() for k in v if "_" in k}
    keys |= {k for k in INDEX_KEYS.domains if "_" in k}
    keys |= {"which_held", "refers_to_held", "answers_bot_question", "new_topic", "refines_held", "switch_back",
             "small_talk", "clarify_domain", "clarify_entity", "clarify_which_held", "clarify_confirm",
             "clarify_did_you_mean", "clarify_report_measure", "clarify_report_period", "attachment_type",
             "product_class", "delivery_order", "sales_order", "purchase_order", "row_pick", "spec_attribute"}
    return keys


INTERNAL_KEYS = _internal_keys()
INTERNAL_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, INTERNAL_KEYS), key=len,
                                                                       reverse=True)) + r")(?![A-Za-z0-9_])")


def check(rec: TurnRecord) -> dict[str, str]:
    """{invariant: one-line reason} for every invariant this turn breaks."""
    bad: dict[str, str] = {}
    if rec.parse is None:
        return bad  # the parser-failure path: nothing PV2 said to judge
    if rec.error:
        return bad  # a crashed turn: `test_r6_contract_replay_ran_every_turn` reports it
    st_in = rec.state_in
    pending_in = st_in.pending if st_in is not None else None
    reply = rec.reply or ""
    asks = rec.parse.get("asks") or []
    # I1
    if any(line in reply for line in REASK_LINES) and pending_in is None:
        bad["I1"] = f"reask with no open question: {reply.splitlines()[0][:70]!r}"
    # I2
    from app.services.chatbot.pv2 import register

    shown = register.shown(st_in) if st_in is not None else []
    for act in _clarifies(rec):
        if act.about in ZERO_OPTION_ABOUTS and not act.pending.options and (_named(rec.parse) or shown):
            why = ", ".join(w for w, on in (("parse names it", _named(rec.parse)), ("rows shown", bool(shown)),
                                            ("not_found", act.not_found)) if on)
            bad["I2"] = f"about={act.about} kind={act.pending.kind} 0 options ({why}): {act.pending.asked[:50]!r}"
            break
    # I3
    picked = any(a.get("act") == "answers_bot_question" or a.get("row_pick") for a in asks)
    if pending_in is not None and pending_in.options and picked and rec.state_out is not None:
        out = rec.state_out.pending
        if out is not None and (out.kind, out.asked, [o.label for o in out.options]) == (
                pending_in.kind, pending_in.asked, [o.label for o in pending_in.options]):
            bad["I3"] = f"pick re-printed {out.kind} {out.asked[:50]!r}"
    # I4
    if rec.next_memory is not None:
        opts, rows = _memory_options(rec.next_memory)
        for line in reply.splitlines():
            m = NUMBERED_RE.match(line)
            if not m:
                continue
            n, printed = int(m.group(1)), m.group(2)
            if n in opts and _matches(printed, opts[n]):
                continue
            if not opts and n in rows and (_matches(printed, rows[n]) or _matches(printed, rows[n].partition(" ")[2])):
                continue
            have = f"option {n}={opts.get(n)!r}" if opts else (f"row {n}={rows.get(n)!r}" if rows else "no options")
            bad["I4"] = f"printed '{n}. {printed[:40]}' but MEMORY has {have[:60]}"
            break
    # I5
    product_ref = any(e.get("kind") == "product" and e.get("refers_to_held") and not e.get("key")
                      for a in asks for e in a.get("entities") or [])
    if product_ref:
        doc_labels = set()
        for st in (st_in, rec.state_out):
            if st is None:
                continue
            doc_labels |= {_norm(e.label) for e in st.register if e.kind in DOC_KINDS}
            doc_labels |= {_norm(r.label) for k, refs in st.held.refs.items() if k in DOC_KINDS for r in refs}
        doc_labels |= {_norm(n) for n, _c, _l in DELIVERY_ORDERS} | {_norm(n) for n, _c in SALES_ORDERS}
        for act in _clarifies(rec):
            for o in act.pending.options:
                kind = (o.payload or {}).get("kind") or (o.payload or {}).get("row_kind")
                if kind in DOC_KINDS or _norm(o.label) in doc_labels:
                    bad["I5"] = f"'this product' offered {kind or 'document'} {o.label!r}"
                    break
            if "I5" in bad:
                break
    # I6
    if UUID_RE.search(reply):
        bad["I6"] = f"uuid in reply: {UUID_RE.search(reply).group(0)[:13]}..."
    elif INTERNAL_RE.search(reply):
        bad["I6"] = f"internal key in reply: {INTERNAL_RE.search(reply).group(0)!r}"
    # C (brief rule c)
    if any(a.get("act") == "unclear" for a in asks) and rec.state_out is not None:
        held = rec.state_out.held
        lost = []
        for a in asks:
            if a.get("domain") and held.domain != a["domain"]:
                lost.append(f"domain {a['domain']}->{held.domain}")
            if a.get("period") and held.period is None:
                lost.append("period")
            if any(e.get("kind") == "customer" for e in a.get("entities") or []) and not held.refs.get("customer"):
                lost.append("customer")
        if lost:
            bad["C"] = "unclear turn dropped " + ", ".join(lost)
    return bad


# ============================================================================ the tests

_CACHE: dict[str, dict[str, list[str]]] = {}


#: Debug knobs: R6_CONTRACT_CONVS=<n> replays the first n conversations only; R6_CONTRACT_DUMP=<dir> writes
#: every turn (message, reply, memory, violations) to <dir>/r6-<asker>.jsonl.
def _selected() -> list[dict[str, Any]]:
    n = int(os.environ.get("R6_CONTRACT_CONVS") or 0)
    return PARSES[:n] if n > 0 else PARSES


def _dump(asker: str, replays: list[list[TurnRecord]]) -> None:
    folder = os.environ.get("R6_CONTRACT_DUMP")
    if not folder:
        return
    path = Path(folder) / f"r6-{asker}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for records in replays:
            for rec in records:
                fh.write(json.dumps({
                    "conv": rec.conv, "idx": rec.idx, "message": rec.message, "reply": rec.reply,
                    "error": rec.error, "memory": rec.memory, "next_memory": rec.next_memory,
                    "pending_in": rec.state_in.pending.model_dump(mode="json") if rec.state_in and rec.state_in.pending else None,
                    "actions": [getattr(a, "type", type(a).__name__) for a in rec.actions],
                    "bad": check(rec), "tools": rec.tools,
                }, ensure_ascii=False, default=str) + "\n")


def _violations(asker: str) -> dict[str, list[str]]:
    if asker not in _CACHE:
        replays = _replay(asker, _selected())
        _dump(asker, replays)
        found: dict[str, list[str]] = {k: [] for k in INVARIANTS}
        for records in replays:
            for rec in records:
                for inv, why in check(rec).items():
                    found[inv].append(f"({rec.conv}, {rec.idx}, {why})")
        found["_turns"] = [str(sum(len(r) for r in replays))]
        found["_crashed"] = [f"({r.conv}, {r.idx}, {r.error})" for records in replays for r in records if r.error]
        found["_judged"] = [str(sum(1 for records in replays for r in records if r.parse is not None and not r.error))]
        _CACHE[asker] = found
    return _CACHE[asker]


@pytest.mark.parametrize("invariant", INVARIANTS)
@pytest.mark.parametrize("asker", ASKERS)
def test_r6_contract_invariant_holds_on_every_recorded_turn(asker, invariant):
    found = _violations(asker)
    hits = found[invariant]
    assert not hits, (
        f"{invariant} ({asker}): {len(hits)} of {found['_turns'][0]} turns; first 5:\n  " + "\n  ".join(hits[:5])
    )


@pytest.mark.parametrize("asker", ASKERS)
def test_r6_contract_replay_ran_every_turn(asker):  # guard: the harness itself, passes today
    """Every recorded turn ran through the engine without raising, and the replay judged turns at all, so a red
    invariant above is the engine's behaviour and never a harness fault."""
    found = _violations(asker)
    assert not found["_crashed"], f"{len(found['_crashed'])} turns raised; first 5:\n  " + "\n  ".join(found["_crashed"][:5])
    assert int(found["_judged"][0]) > 0, "no turn was judged"
