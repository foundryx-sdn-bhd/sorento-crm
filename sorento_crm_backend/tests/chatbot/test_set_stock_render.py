"""SET-STOCK-RENDER (SSR-1 to SSR-14): a set stock answer reads like the product-code answer.

UAC `documentation/plans/chatbot/set-stock-render-acceptance-criteria.md`; behaviour card
`documentation/plans/chatbot/CARD-set-stock-render-16oct.md`.

Phase 2 red tests, written BEFORE the implementation. Every test runs `engine.run_turn` with
the real resolver, the real class vocabulary and brands table, the parser stubbed, and ONE
seam faked: the MCP call. The fake is faithful: it calls the REAL stock-balance route
function over the seeded rows (so the contact's stock-visibility policy, the company scope,
`hide_zero_locations` and any new route argument behave as in production) and runs the
payload through the REAL MCP presenter. Assertions read the reply text the user sees.
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.chatbot.set_reply import one_line_header
from tests.chatbot.test_attribute_asks_round2 import _brand, _product, _stock_verdict
from tests.chatbot.test_counted_set_no_paging import _link_to_default_company
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import (
    _s4_contact_id,
    _s4_envelope,
    _s4_real_resolve_entity,
    _s4_seed_contact,
    _s4_wire_engine,
    _seed_category_and_uom,
    _seed_registry,
    _stock_for,
    _warehouse,
)
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
INTRO = "Stock details found for the requested products."


# --------------------------------------------------------------------------- #
# The faithful stock tool                                                       #
# --------------------------------------------------------------------------- #


def _present(tool: str, payload: dict[str, Any]) -> str:
    mcp_root = str(Path(__file__).resolve().parents[3] / "sorento_crm_mcp")
    if mcp_root not in sys.path:
        sys.path.insert(0, mcp_root)
    from sorento_crm_mcp.presenters import present_response

    return present_response(tool, json.dumps(payload))


def _route_kwargs(fn, args: dict[str, Any]) -> dict[str, Any]:
    """Every parameter of the route function: the tool's arg when sent, else the route's own
    default (a bare call would leave `Query(...)` objects in place of defaults)."""
    out: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if name in ("current_user", "db"):
            continue
        default = param.default
        default = getattr(default, "default", default)
        value = args.get(name, default)
        if name in ("product_ids", "warehouse_ids", "entities") and isinstance(value, str):
            value = [value]
        if name == "requested_quantities" and isinstance(value, dict):
            value = json.dumps(value)
        out[name] = value
    return out


def _products_tool(db, args: dict[str, Any]) -> str:
    """The products tool's rows for `product_ids`: one block per product."""
    from app.models.product import Product

    ids = args.get("product_ids") or []
    ids = ids if isinstance(ids, list) else [ids]
    rows = db.query(Product).filter(Product.id.in_(ids)).order_by(Product.product_code).all() if ids else []
    items = [
        {
            "title": p.product_code,
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": p.product_code},
                {"key": "product_name", "label": "Product Name", "value": p.product_name},
            ],
            "flags": {},
        }
        for p in rows
    ]
    return json.dumps(
        {"result_type": "products", "intro": "Products found.", "items": items, "has_result": bool(items)}
    )


def _stock_tool(db, calls: list[dict[str, Any]]):
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    from app.api.v1.inventory.stock import get_stock_balance
    from app.models.base import set_company_scope
    from app.schemas.inventory import StockBalanceListResponse
    from app.services.company_scope import DEFAULT_COMPANY_ID

    def fake_call_tool(name: str, args: dict[str, Any]) -> str:
        calls.append({"name": name, "args": dict(args)})
        if name == "crm_master_products_list":
            return _products_tool(db, args)
        if name != "crm_inventory_stock_balance_list":
            return json.dumps({"items": [], "has_result": False})
        scope = getattr(db, "_ssr_scope", None) or frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        # The request-entry scope a contact call gets in production: its brand allow-list.
        from app.models.base import set_brand_scope
        from app.services.contact_brand_scope import contact_brand_scope

        if args.get("contact_id"):
            set_brand_scope(db, contact_brand_scope(db, str(args["contact_id"]), args.get("space_id")))
        result = get_stock_balance(
            **_route_kwargs(get_stock_balance, args), current_user={"id": str(uuid.uuid4())}, db=db
        )
        if isinstance(result, JSONResponse):
            body = json.loads(result.body)
        elif isinstance(result, dict) and result.get("alternatives"):
            body = jsonable_encoder(result)
        else:
            body = StockBalanceListResponse.model_validate(result).model_dump(mode="json")
            for extra in ("stock_summary", "stock_availability", "stock_visibility", "last_updated_at"):
                if isinstance(result, dict) and result.get(extra) is not None:
                    body[extra] = jsonable_encoder(result[extra])
        return _present(name, body)

    return fake_call_tool


# --------------------------------------------------------------------------- #
# World                                                                         #
# --------------------------------------------------------------------------- #


def _set_policy(session_factory, contact_id: str, *, mode: str, hide_zero: bool = False) -> None:
    from sqlalchemy import text as sa_text

    db = session_factory()
    internal = db.execute(
        sa_text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": contact_id}
    ).scalar()
    from app.models.access import StockVisibilityPolicy

    db.add(
        StockVisibilityPolicy(
            id=str(uuid.uuid4()), contact_id=internal, mode=mode, hide_zero_locations=hide_zero
        )
    )
    db.commit()


class World:
    """Wash basins in two brands, two locations. `on_hand` per product is (first location,
    second location); the second location holds 0 unless stated."""

    def __init__(self, session_factory):
        from app.models.product import Brand

        self.session_factory = session_factory
        self.db = session_factory()
        self.db.query(Brand).update({Brand.chatbot_weight: 0})
        _cat, self.uom_id = _seed_category_and_uom(self.db)
        self.category = _class_category(self.db, "WB")
        self.sorento = _brand(self.db, f"Sorento{uuid.uuid4().hex[:4]}")
        self.mocha = _brand(self.db, f"Mocha{uuid.uuid4().hex[:4]}")
        _seed_registry(self.db)
        self.wh1 = _warehouse(self.db)
        self.wh2 = _warehouse(self.db)
        self.products: list[Any] = []

    def basin(self, brand, prefix: str, first: int = 10, second: int | None = None, colour: str = "WHITE"):
        from app.models.product import Product
        from app.services.product_spec_derivation import derive_for_code
        from tests._pg_fixture import unique_code

        code = unique_code(f"{prefix}-Z", alpha=True)[:50]
        p = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"{prefix} basin",
            description=f"{code} {colour} WASH BASIN", category_id=self.category,
            base_uom_id=self.uom_id, brand_id=brand.id, list_price=10, is_active=True,
        )
        self.db.add(p)
        self.db.flush()
        derive_for_code(self.db, code)
        _stock_for(self.db, product_id=p.id, warehouse_id=self.wh1.id, on_hand=first)
        if second is not None:
            _stock_for(self.db, product_id=p.id, warehouse_id=self.wh2.id, on_hand=second)
        self.products.append(p)
        return p

    def commit(self):
        self.db.commit()

    @property
    def codes(self) -> set[str]:
        return {p.product_code for p in self.products}


class Chat:
    def __init__(self, session_factory, monkeypatch, stub_parser, stub_access, world: World, *, mode=None, hide_zero=False):
        self.session_factory = session_factory
        self.stub_parser = stub_parser
        self.stub_access = stub_access
        self.calls: list[dict[str, Any]] = []
        self.contact_id = _s4_contact_id(f"ssr{uuid.uuid4().hex[:6]}")
        _s4_seed_contact(session_factory, contact_id=self.contact_id, session_vars={"variables": {}})
        _link_to_default_company(session_factory, self.contact_id)
        if mode:
            _set_policy(session_factory, self.contact_id, mode=mode, hide_zero=hide_zero)
        self.engine = _s4_wire_engine(
            session_factory,
            monkeypatch,
            resolve_entity=_s4_real_resolve_entity(world.db),
            fetch_mcp_call=_stock_tool(world.db, self.calls),
        )
        self.n = 0

    def say(self, text: str, verdict: dict[str, Any]) -> str:
        self.n += 1
        self.stub_parser(verdict)
        self.stub_access()
        turn = self.engine.run_turn(
            _s4_envelope(contact_id=self.contact_id, message_id=f"ZZT-{self.contact_id}-{self.n}", text=text),
            session_factory=self.session_factory,
        )
        assert turn.status == "done", turn.error
        return (turn.reply or {}).get("text") or ""


@pytest.fixture()
def world(session_factory):
    return World(session_factory)


def make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, **kw) -> Chat:
    return Chat(session_factory, monkeypatch, stub_parser, stub_access, world, **kw)


def _set_ask(extra=(), goal="any wash basin has stock"):
    entities = [{"raw": "wash basin", "hint": "product_type"}, *extra]
    return _stock_verdict(entities, goal)


def _code_ask(products, goal="stock for these"):
    entities = [
        {"raw": p.product_code, "hint": "product", "canonical_code": p.product_code} for p in products
    ]
    return _stock_verdict(entities, goal, match_mode="or")


def _rows(reply: str) -> str:
    """The reply without its opening line when that line is the code answer's intro or the
    set header: the rows (and footer) that follow, blank lines at the edges trimmed."""
    first, _, rest = reply.partition("\n")
    if first.startswith(INTRO) or first.startswith("Here's what you want"):
        return rest.strip("\n")
    return reply.strip("\n")


def _codes_shown(reply: str) -> list[str]:
    """Distinct product codes the rows name, in order (detailed/compact/availability)."""
    seen: list[str] = []
    for m in re.finditer(r"^(?:\d+\. )?\*Product Code:\* (\S+)|^\d+\. ([A-Z0-9-]+)$", reply, re.M):
        code = m.group(1) or m.group(2)
        if code not in seen:
            seen.append(code)
    return seen


def _header_count(reply: str) -> tuple[int, int | None]:
    m = re.match(r"Here's what you want: .*\(([\d,]+)(?:, showing 1 to (\d+))?\)", one_line_header(reply))
    assert m, reply
    return int(m.group(1).replace(",", "")), (int(m.group(2)) if m.group(2) else None)


def _clean(reply: str) -> None:
    """SSR-13."""
    assert not UUID_RE.search(reply), reply
    assert " · " not in reply, reply


def _on_hand_lines(reply: str) -> list[int]:
    return [int(v) for v in re.findall(r"\*Quantity On Hand:\* (-?\d+)", reply)]


# --------------------------------------------------------------------------- #
# R1 shape                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["detailed", "compact"])
def test_ssr_1_2_set_answer_is_the_code_answer_line_for_line(
    session_factory, monkeypatch, stub_parser, stub_access, world, mode
):
    """SSR-1 (detailed) and SSR-2 (compact): below the first line the set stock answer is the
    code answer for the same products, same contact policy. SSR-13/14 ride along."""
    srt = [world.basin(world.sorento, "ZZSA", 10, 4), world.basin(world.sorento, "ZZSB", 7, 3)]
    mch = [world.basin(world.mocha, "ZZMA", 5, 2)]
    world.commit()
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode=mode)

    code_reply = chat.say("stock for these codes", _code_ask(srt + mch))
    set_reply = chat.say("any wash basin has stock", _set_ask())

    assert set(_codes_shown(code_reply)) == world.codes, code_reply
    assert _rows(set_reply) == _rows(code_reply), (set_reply, code_reply)
    count, showing = _header_count(set_reply)
    assert showing is None and count == len(_codes_shown(set_reply)) == 3, set_reply  # SSR-14
    _clean(set_reply)


def test_ssr_3_dealer_availability_set_lists_codes_and_asks_how_many(world):
    """SSR-3: an availability contact, no quantity given: header, the numbered codes, then
    "How many units do you need?", and no stock number anywhere. Driven through the
    resolve + fetch lane (the card's path, fetch.py `stock_ask_render`): the engine's own
    stock-ask task answers a dealer's quantity question before this text on main ("How many
    units for each?"), which is outside this UAC."""
    from sqlalchemy import text as sa_text

    from app.models.access import RespondContact, StockVisibilityPolicy
    from tests.chatbot.test_lane_require import _run_has_lane
    from tests.chatbot.test_engine import _parser_output

    products = [world.basin(world.sorento, "ZZDA", 10), world.basin(world.mocha, "ZZDB", 6)]
    contact = RespondContact(
        id=str(uuid.uuid4()), phone_number=f"+60{uuid.uuid4().int % 10**9:09d}", name="ZZT SSR dealer"
    )
    world.db.add(contact)
    world.db.flush()
    world.db.add(StockVisibilityPolicy(id=str(uuid.uuid4()), contact_id=contact.id, mode="availability"))
    world.commit()
    ctx = {
        "text": {"message": {"message": {"text": "any wash basin has stock"}}},
        "contact": {"id": contact.id},
        "parse": {"output": _set_ask()},
    }

    out, fragment = _run_has_lane(world.db, ctx, fake_call_tool=_stock_tool(world.db, []))

    assert out.get("_exit_kind") == "continue", out.get("gate_reason")
    reply = (fragment.get("fetch") or {}).get("response") or ""
    lines = [ln for ln in reply.split("\n") if ln.strip()]
    assert lines[0].startswith("Here's what you want: wash basins with stock (2)"), reply
    assert lines[-1] == "How many units do you need?", reply
    numbered = lines[1:-1]
    assert sorted(re.fullmatch(r"\d+\. (\S+)", ln).group(1) for ln in numbered) == sorted(
        p.product_code for p in products
    ), reply
    assert not re.search(r"Quantity|Total|On Hand|Outstanding", reply), reply
    assert not re.search(r"\b(10|6)\b", "\n".join(numbered)), reply
    _clean(reply)


def test_ssr_3_engine_dealer_set_answer_equals_the_dealer_code_answer(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-3 (unified rule): an availability (dealer) contact asking the set, no quantity,
    gets what a dealer asking the same codes by code gets, below any first line (the engine
    quantity task and numbered codes, no stock number)."""
    products = [world.basin(world.sorento, "ZZEA", 10), world.basin(world.mocha, "ZZEB", 6)]
    world.commit()
    code_chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="availability")
    code_reply = code_chat.say("stock for these codes", _code_ask(products))
    set_chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="availability")
    set_reply = set_chat.say("any wash basin has stock", _set_ask())

    def below_header(reply: str) -> str:
        first, _, rest = reply.partition("\n")
        return rest.strip("\n") if first.startswith("Here's what you want") else reply.strip("\n")

    assert below_header(set_reply) == below_header(code_reply), (set_reply, code_reply)
    for p in products:
        assert p.product_code in set_reply, set_reply
    assert not re.search(r"Quantity|Total|On Hand|\b(10|6)\b", set_reply), set_reply
    _clean(set_reply)


def test_ssr_4_first_line_is_the_set_header_not_the_code_intro(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-4: "Here's what you want: wash basins with stock (N)" opens the reply; the code
    answer's "Stock details found for the requested products." is not printed."""
    world.basin(world.sorento, "ZZHA", 3, 1)
    world.basin(world.sorento, "ZZHB", 2, 1)
    world.commit()
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")

    reply = chat.say("any wash basin has stock", _set_ask())

    assert reply.split("\n", 1)[0] == "Here's what you want: wash basins with stock (2)", reply
    assert INTRO not in reply, reply
    _clean(reply)


# --------------------------------------------------------------------------- #
# R2 no breakdown                                                               #
# --------------------------------------------------------------------------- #


def _fifty_two(world: World, *, colours: bool = False):
    """30 Sorento + 22 Mocha basins with stock. All white (the brand splits the set), or with
    `colours` a mix of black and white (the colour splits one brand's set)."""
    for i in range(30):
        world.basin(world.sorento, f"ZZSO{i:02d}", 5, colour="BLACK" if colours and i % 3 == 0 else "WHITE")
    for i in range(22):
        world.basin(world.mocha, f"ZZMO{i:02d}", 5, colour="BLACK" if colours and i % 2 == 0 else "WHITE")
    world.commit()


def test_ssr_5_over_fifty_stock_set_lists_the_first_fifty_with_no_breakdown(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-5: 52 qualifying products (two brands, so a brand breakdown WOULD be built), stock
    leg: no "• " line, the rows of the first 50 products, header "(52, showing 1 to 50)"."""
    _fifty_two(world)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")

    reply = chat.say("any wash basin has stock", _set_ask())

    assert not [ln for ln in reply.split("\n") if ln.startswith("• ")], reply
    assert one_line_header(reply) == "Here's what you want: wash basins with stock (52, showing 1 to 50)", reply
    assert len(_codes_shown(reply)) == 50, reply
    assert set(_codes_shown(reply)) <= world.codes, reply
    _clean(reply)


def test_ssr_6_over_fifty_product_ask_lists_the_first_fifty_with_no_breakdown(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-6: more than 50 qualifying products on another leg (a product ask): no breakdown
    line; the first 50 products' rows under the same header form."""
    from tests.chatbot.test_engine import _parser_output

    _fifty_two(world)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    verdict = _parser_output(
        intent_hint="check_product",
        domain_hint="master_products",
        match_mode="or",
        user_goal="show me wash basins",
        requested_attributes=["price"],
        entities=[
            {"raw": "wash basin", "hint": "product_type", "canonical_code": None, "current_message": True, "confident": True},
            {"raw": "white", "hint": "specification", "spec_key": "finish", "spec_value": "White", "canonical_code": None, "current_message": True, "confident": True},
        ],
    )

    reply = chat.say("show me white wash basins", verdict)

    assert not [ln for ln in reply.split("\n") if ln.startswith("• ")], reply
    count, showing = _header_count(reply)
    assert (count, showing) == (52, 50), reply
    assert len(re.findall(r"\*Product Code:\* (\S+)", reply)) == 50, reply
    _clean(reply)


def test_ssr_7_misspelt_word_keeps_the_miss_shape_with_no_breakdown_and_no_rows(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-7: "sorento undrmount basin" with stock. A word the set reader cannot use keeps
    today's miss shape minus the breakdown: the typed header (no count), no rows,
    `Couldn't find: "undrmount".` and the escalate offer. Never a different set."""
    _fifty_two(world, colours=True)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    brand = world.sorento.brand_name.lower()
    verdict = _stock_verdict(
        [{"raw": brand, "hint": "brand"}, {"raw": "undrmount basin", "hint": "product_type"}],
        f"any stock for {brand} undrmount basin",
    )

    reply = chat.say(f"any stock for {brand} undrmount basin", verdict)

    assert not [ln for ln in reply.split("\n") if ln.startswith("• ")], reply
    brand_name = world.sorento.brand_name
    brand_name = brand_name.title() if brand_name.isupper() else brand_name
    assert reply.split("\n", 1)[0] == f"Here's what you want: {brand_name} undrmount wash basins", reply
    assert _codes_shown(reply) == [], reply
    assert "*Product Code:*" not in reply, reply
    assert 'Couldn\'t find: "undrmount".' in reply, reply
    assert "escalate" in reply, reply
    _clean(reply)


def test_ssr_8_nothing_matched_keeps_todays_text(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-8: a set nothing qualifies for (every basin at 0 on hand) keeps today's "nothing
    matched" reply: the found-summary bullet, "But no stock matched these.", the team
    question. It is not turned into a list of rows."""
    p = world.basin(world.sorento, "ZZNA", 0)
    world.commit()
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")

    reply = chat.say("any wash basin has stock", _set_ask())

    assert reply.startswith(f"Here's what you want: wash basins (1)\n• {p.product_code}\n\nBut no stock matched these."), reply
    assert "*Product Code:*" not in reply, reply


# --------------------------------------------------------------------------- #
# R3 with stock, R4 two companies                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["detailed", "compact"])
def test_ssr_9_with_stock_prints_no_empty_location(
    session_factory, monkeypatch, stub_parser, stub_access, world, mode
):
    """SSR-9: a set asked with stock prints no location with on hand 0, even when the
    contact's policy shows empty locations (hide_zero off)."""
    world.basin(world.sorento, "ZZZA", 8, 0)
    world.basin(world.sorento, "ZZZB", 0, 5)
    world.commit()
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode=mode, hide_zero=False)

    reply = chat.say("any wash basin has stock", _set_ask())

    assert one_line_header(reply) == "Here's what you want: wash basins with stock (2)", reply
    if mode == "detailed":
        assert sorted(_on_hand_lines(reply)) == [5, 8], reply
    else:
        location_values = re.findall(r"^\*[^*:]+:\* (-?\d+)(?: \(O/S: \d+\))?$", reply, re.M)
        assert "0" not in location_values, reply
        for block in re.split(r"\n\n", reply):
            for line in block.split("\n"):
                if re.fullmatch(r"\*(?!Total|Product Code)[^*]+:\* 0( \(O/S: \d+\))?", line):
                    raise AssertionError(("empty location line", line, reply))
    _clean(reply)


def test_ssr_10_code_answer_still_prints_empty_locations(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-10: the code answer for the same product, same contact, still prints its empty
    location when the policy shows them."""
    p = world.basin(world.sorento, "ZZYA", 8, 0)
    world.commit()
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed", hide_zero=False)

    reply = chat.say("stock for ZZYA", _code_ask([p]))

    assert sorted(_on_hand_lines(reply)) == [0, 8], reply


def _add_mocha_twin(world: World, twin, *, brand_id, on_hand: int = 9):
    """The same product code as `twin` in the Mocha company, with its own warehouse and stock.
    Call AFTER every Sorento-company row is seeded (a two-company scope refuses an unstamped
    insert). Returns the Mocha-company product."""
    from app.models.base import set_company_scope
    from app.models.company import Company
    from app.models.inventory import Stock, Warehouse
    from app.models.product import Product
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from app.services.product_spec_derivation import derive_for_code
    from tests._mc_lookup_seed import MOCHA_ID

    db = world.db
    if not db.query(Company).filter(Company.id == MOCHA_ID).first():
        db.add(Company(id=MOCHA_ID, name="Mocha", code=f"MCH{uuid.uuid4().hex[:5]}"))
        db.flush()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    wh = Warehouse(
        id=str(uuid.uuid4()), warehouse_code=f"ZZMW{uuid.uuid4().hex[:4]}", warehouse_name="ZZT Mocha WH",
        is_active=True, company_id=MOCHA_ID,
    )
    db.add(wh)
    db.flush()
    row = Product(
        id=str(uuid.uuid4()), product_code=twin.product_code, product_name=twin.product_name,
        description=twin.description, category_id=twin.category_id, base_uom_id=twin.base_uom_id,
        brand_id=brand_id, list_price=10, is_active=True, company_id=MOCHA_ID,
    )
    db.add(row)
    db.flush()
    derive_for_code(db, twin.product_code)
    db.add(
        Stock(
            id=str(uuid.uuid4()), product_id=row.id, warehouse_id=wh.id,
            quantity_on_hand=on_hand, quantity_reserved=0, company_id=MOCHA_ID,
        )
    )
    db.flush()
    world.commit()
    db._ssr_scope = frozenset({DEFAULT_COMPANY_ID, MOCHA_ID})
    return row


def _dual_chat(session_factory, monkeypatch, stub_parser, stub_access, world, *, brand_scope=None):
    """A detailed-policy contact linked to both companies (and, when given, scoped to brands)."""
    from sqlalchemy import text as sa_text

    from tests._mc_lookup_seed import MOCHA_ID

    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    s = session_factory()
    internal = s.execute(
        sa_text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": chat.contact_id}
    ).scalar()
    s.execute(
        sa_text("INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) VALUES (gen_random_uuid(), :r, :c)"),
        {"r": internal, "c": MOCHA_ID},
    )
    if brand_scope:
        s.execute(
            sa_text("UPDATE respond_contacts SET brand_ids = CAST(:b AS uuid[]) WHERE id = :r"),
            {"b": "{" + ",".join(brand_scope) + "}", "r": internal},
        )
    s.commit()
    return chat


def test_ssr_11_same_code_in_two_companies_lists_both_rows_counts_once(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-11: one product code in two companies, both qualifying: both companies' rows are
    listed (as the code answer does) and the header counts the code once."""
    twin = world.basin(world.sorento, "ZZTW", 6)
    world.basin(world.sorento, "ZZTX", 4)
    _add_mocha_twin(world, twin, brand_id=twin.brand_id)
    chat = _dual_chat(session_factory, monkeypatch, stub_parser, stub_access, world)

    reply = chat.say("any wash basin has stock", _set_ask())

    twin_blocks = [ln for ln in reply.split("\n") if ln.endswith(f"*Product Code:* {twin.product_code}")]
    assert len(twin_blocks) == 2, reply
    assert one_line_header(reply) == "Here's what you want: wash basins with stock (2)", reply
    assert sorted(_on_hand_lines(reply)) == [4, 6, 9], reply
    _clean(reply)


def _twin_world(world: World, *, other_brand_code: bool = False):
    """Codes A (twin in the Mocha company), B, C, D, all Sorento brand, in code order; with
    `other_brand_code` one more code of the Mocha brand."""
    if other_brand_code:
        world.basin(world.mocha, "ZZB9", 3)
    a = world.basin(world.sorento, "ZZA0", 6)
    rest = [world.basin(world.sorento, f"ZZA{i}", 4) for i in (1, 2, 3)]
    _add_mocha_twin(world, a, brand_id=a.brand_id)
    return a, rest


def test_ssr_11b_next_page_after_a_twin_starts_at_the_next_code(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-11 + SSR-14: with a list limit of 3, the first page holds A, its twin A' and B
    ("(4, showing 1 to 3)"); the next page starts at the next CODE: no code repeated or
    skipped across the pages."""
    from app.services.chatbot.lanes.business import answer as answer_mod
    from tests.chatbot.test_attribute_asks_round3 import _bare

    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 3)
    _twin_world(world)
    chat = _dual_chat(session_factory, monkeypatch, stub_parser, stub_access, world)

    first = chat.say("any wash basin has stock", _set_ask())
    page = chat.say("3", _bare(top_n=3))

    assert one_line_header(first).endswith("(4, showing 1 to 3)"), first
    shown_first, shown_page = _codes_shown(first), _codes_shown(page)
    assert len(shown_first) == 3, first
    assert not set(shown_first) & set(shown_page), ("page repeats a code", shown_first, shown_page)
    assert set(shown_first) | set(shown_page) == world.codes, (shown_first, shown_page)


def test_ssr_11b_brand_follow_up_over_a_set_with_twins_lists_every_code_of_the_brand(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-11 + SSR-12: the brand word after a set whose codes include a twin lists every
    code of that brand (the twin's rows included), none dropped. A list limit of 4 equal to
    the brand's code count: a cut that counts rows instead of codes would drop one."""
    from app.services.chatbot.lanes.business import answer as answer_mod

    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 4)
    a, rest = _twin_world(world, other_brand_code=True)
    chat = _dual_chat(session_factory, monkeypatch, stub_parser, stub_access, world)
    chat.say("any wash basin has stock", _set_ask())
    brand = world.sorento.brand_name.lower()

    reply = chat.say(
        brand, _stock_verdict([{"raw": brand, "hint": "brand"}], f"{brand} wash basins with stock", entity_op="reuse")
    )

    assert set(_codes_shown(reply)) == {a.product_code, *(p.product_code for p in rest)}, reply
    assert len([ln for ln in reply.split("\n") if ln.endswith(f"*Product Code:* {a.product_code}")]) == 2, reply
    assert one_line_header(reply).endswith("(4)"), reply


def test_ssr_11c_a_brand_scoped_contact_sees_only_the_in_scope_company_row(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-11 scope guard: a dual-company contact scoped to the Sorento brand. The code exists
    as a Mocha-company row (no brand) and a Sorento-company row branded Mocha (out of scope):
    the set answer shows ONE block, the Mocha-company row, never two."""
    out_of_scope = world.basin(world.mocha, "ZZSC", 6)  # Sorento-company row, brand MOCHA
    _add_mocha_twin(world, out_of_scope, brand_id=None, on_hand=9)
    chat = _dual_chat(
        session_factory, monkeypatch, stub_parser, stub_access, world, brand_scope=[world.sorento.id]
    )

    reply = chat.say("any wash basin has stock", _set_ask())

    blocks = [ln for ln in reply.split("\n") if ln.endswith(f"*Product Code:* {out_of_scope.product_code}")]
    assert len(blocks) == 1, reply
    assert _on_hand_lines(reply) == [9], reply
    _clean(reply)


def test_ssr_14_every_continuation_page_says_its_range_including_the_last(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-14: a page after the first says "(<total>, showing X to Y)", the last page too; a
    bare "(total)" only when the whole set is on one page."""
    from app.services.chatbot.lanes.business import answer as answer_mod
    from tests.chatbot.test_attribute_asks_round3 import _bare

    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 3)
    for i in range(4):
        world.basin(world.sorento, f"ZZP{i}", 5)
    world.commit()
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")

    first = chat.say("any wash basin has stock", _set_ask())
    last = chat.say("3", _bare(top_n=3))

    assert one_line_header(first) == "Here's what you want: wash basins with stock (4, showing 1 to 3)", first
    assert one_line_header(last) == "Here's what you want: wash basins with stock (4, showing 4 to 4)", last


# --------------------------------------------------------------------------- #
# R5 brand follow-up                                                            #
# --------------------------------------------------------------------------- #


def test_ssr_12_brand_word_after_a_listed_set_answers_the_narrowed_set_like_the_code_answer(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    """SSR-12: after a set stock answer listed in full, the brand word answers the set
    narrowed to that brand in the SSR-1 shape, the header names the brand, no breakdown."""
    srt = [world.basin(world.sorento, "ZZFA", 9, 3), world.basin(world.sorento, "ZZFB", 4, 2)]
    world.basin(world.mocha, "ZZFC", 7, 1)
    world.commit()
    code_chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    code_reply = code_chat.say("stock for the sorento ones", _code_ask(srt))
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    first = chat.say("any wash basin has stock", _set_ask())
    assert one_line_header(first) == "Here's what you want: wash basins with stock (3)", first
    brand = world.sorento.brand_name.lower()

    second = chat.say(
        brand,
        _stock_verdict(
            [{"raw": brand, "hint": "brand"}], f"{brand} wash basins with stock", entity_op="reuse"
        ),
    )

    sorento = world.sorento.brand_name
    sorento = sorento.title() if sorento.isupper() else sorento
    assert one_line_header(second) == f"Here's what you want: {sorento} wash basins with stock (2)", second
    assert not [ln for ln in second.split("\n") if ln.startswith("• ")], second
    assert _rows(second) == _rows(code_reply), (second, code_reply)
    _clean(second)
