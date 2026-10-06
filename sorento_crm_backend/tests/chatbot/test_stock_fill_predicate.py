"""STOCK-FILL-PREDICATE (owner report, 6 Oct 2026 13:20 MYT, prod turn 121 of contact
respond_io 404285551, prompt v47).

Turn 120 read a photo ("I read SRTWC8383-RL-GY and SORENTO from that photo. How many
units of SRTWC8383-RL-GY?") and left this state: the product on `focus.products` (with
its uuid), the brand word SORENTO mis-tagged `promotion` on `focus.extra` (no uuid), and
one open `stock_qty` task with one slot. Turn 121, the dealer typed "1": the parser
answered the open question (`open_question_answer.mode fill`, `entity_op reuse`, no
entities). The reply sent was

    *stock* for SRTWC8383-RL-GY:

    Please refer to your salesman.

Root cause (crew scout on origin/main 929e3bd23): the fill turn handed the CARRIED
uuid-less SORENTO row to the resolver (`turn_runtime.with_carried_entities`, unsettled
only), `resolve_gate.resolve_entity_body` added `require={"stock": true}` for
`check_stock` because the typed-code guard sees no product CODE in a promotion hint,
the resolver counted every product with stock (356 KB), the set predicate rode into the
inventory fetch, `output_structurer` withheld the rows of a set past `SET_LIST_MAX`
and printed only the set header, `envelope_of` stripped that header (not a counted
set), and the composer was left with a bare domain header over an empty body - which
`envelope_missed` read as a total miss, so the salesman line closed it.

On the blank test schema the resolver places nothing for the stray SORENTO token (prod's
resolver did, which is what let the set predicate through), so before the fix these
replays answer `Couldn't find: SORENTO.` instead of the bare header - the same defect
one step earlier: the fill turn was made about the carried word, not about the task's
product. Either way the resolver must never be asked about it on that turn, which the
seam assertion on the resolver bodies pins.

Two rules, each pinned here:

A. A `stock_qty` task FILL turn fetches the task's product(s) only. No carried uuid-less
   extra is sent to the resolver with a require-stock predicate, and no set predicate
   reaches the inventory fetch.
B. Never-stuck (owner): a lane header is never sent with an empty body. A section that
   misses for its only product renders that product's miss line
   (`turn/compose.py`'s own "No stock found for X.") instead of a bare header.

Every uuid and code fixture here is synthetic (public repo).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State, focus_to_wire

from tests.chatbot._avail_mode_console import AvailConsole, Stock, _seed
from tests.chatbot._r9_engine_console import reply
from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

pytestmark = pytest.mark.usefixtures("stub_access")

CODE = "SRTWC8383-RL-GY"
OTHER = "SRT5674"
R = "Please refer to your salesman."
TICK, CROSS = "✅", "❌"
DISCONTINUED = "⚠️  *(PRODUCT DISCONTINUED)*"
BARE_HEADER = f"*stock* for {CODE}:"


# --------------------------------------------------------------------------- #
# A. The turn 121 state, replayed through the real engine
# --------------------------------------------------------------------------- #


def _seed_catalogue_with_stock(session_factory, console: AvailConsole) -> str:
    """The product the photo named, plus the shape of the prod catalogue the set read
    counted: two products with stock on hand in an active warehouse, under two brands,
    so a require-stock predicate qualifies a set that `_set_breakdown` splits by brand
    (the prod set was every Sorento product with stock, broken down the same way).
    Returns the product's uuid."""
    from app.models.inventory import Stock as StockRow
    from app.models.inventory import Warehouse
    from app.models.product import Brand, Product

    seeded = _seed(session_factory, [CODE])
    console.codes.update(seeded)
    console.uuid_of.update({code: pid for pid, code in seeded.items()})
    product_id = console.uuid_of[CODE]

    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    product = db.query(Product).filter(Product.id == product_id).one()
    other = db.query(Product).filter(Product.id == console.uuid_of[OTHER]).one()
    company_id = product.company_id
    product.is_discontinued = True
    tag = uuid.uuid4().hex[:6]
    brands = [
        Brand(id=str(uuid.uuid4()), brand_code=f"ZZTB-{tag}-{i}", brand_name=f"ZZT brand {i}", company_id=company_id)
        for i in (1, 2)
    ]
    warehouse = Warehouse(
        id=str(uuid.uuid4()),
        warehouse_code=f"ZZTW-{tag}",
        warehouse_name="ZZT warehouse",
        is_active=True,
        company_id=company_id,
    )
    db.add_all([*brands, warehouse])
    db.flush()
    product.brand_id = brands[0].id
    other.brand_id = brands[1].id
    for row in (product, other):
        db.add(
            StockRow(
                id=str(uuid.uuid4()),
                product_id=row.id,
                warehouse_id=warehouse.id,
                quantity_on_hand=100,
                company_id=company_id,
            )
        )
    db.commit()
    return product_id


def _turn_120_state(product_id: str) -> dict[str, Any]:
    """The state turn 120 left: the product (settled, with its uuid), the brand word
    SORENTO mis-tagged `promotion` on `focus.extra` with no uuid, one open stock_qty
    task with one empty slot."""
    task = task_mod.Task(
        kind="stock_qty",
        domain="inventory",
        status=task_mod.OPEN,
        opened_at_turn=120,
        touched_at_turn=120,
        slots=(task_mod.Slot(key=product_id, label=CODE, value=None),),
    )
    focus = Focus(
        products=[
            {
                "raw": CODE,
                "hint": "product",
                "canonical_code": CODE,
                "uuid": product_id,
                "current_message": False,
                "confident": True,
            }
        ],
        domains=["inventory"],
        tasks=(task,),
        extra={
            "promotion": [
                {"raw": "SORENTO", "hint": "promotion", "canonical_code": "SORENTO", "confident": True}
            ]
        },
    )
    return {"focus": focus_to_wire(focus), "open_question": None}


def _turn_121_verdict() -> dict[str, Any]:
    """The parser's reading of "1": no entities, `entity_op reuse`, `check_stock` under
    `inventory`, the open question answered in `fill` mode for line 1."""
    return reply(
        entity_op="reuse",
        open_question_answer={
            "mode": "fill",
            "picked": [1],
            "items": [{"position": 1, "code": CODE, "qty": 1}],
            "qty_for_all": None,
        },
    )


@pytest.fixture
def fill_console(session_factory, monkeypatch, stub_access):
    """The dealer console on the turn 120 state. `resolver_bodies` records every body the
    lane built for the shared resolver on the way; `SET_LIST_MAX` is lowered so the
    two-product seeded set is past it exactly as the prod catalogue was past 50."""
    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 1)
    bodies: list[dict[str, Any]] = []
    real_body = resolve_gate.resolve_entity_body

    def recording_body(ctx, **kwargs):
        body = real_body(ctx, **kwargs)
        bodies.append(body)
        return body

    monkeypatch.setattr(resolve_gate, "resolve_entity_body", recording_body)

    def make(**stock_facts: Stock) -> tuple[AvailConsole, str]:
        console = AvailConsole(
            session_factory, monkeypatch, stub_access, phone="+60100000912", **stock_facts
        )
        product_id = _seed_catalogue_with_stock(session_factory, console)
        console.state = _turn_120_state(product_id)
        console.resolver_bodies = bodies  # type: ignore[attr-defined]
        return console, product_id

    return make


def test_turn_121_fill_answers_the_products_availability_line(fill_console):
    """The owner's turn 121, product in stock and discontinued: the availability line
    and the marker, no bare "*stock* for" header, no salesman line."""
    c, product_id = fill_console(**{CODE.replace("-", "_"): Stock(on_hand=100, discontinued=True)})

    text = c.say("1", _turn_121_verdict())

    assert text == f"{CODE} x 1: {TICK} Got stock.\n{DISCONTINUED}", text
    assert BARE_HEADER not in text
    assert R not in text
    # The task's own product, and only it, reached the stock tool with its quantity.
    assert c.stock_calls[-1]["product_ids"] == [product_id]
    # Rule A at the resolver seam: no carried uuid-less extra was sent with a
    # require-stock predicate on the fill turn.
    for body in c.resolver_bodies:
        assert "require" not in body, body
        assert "SORENTO" not in [str(t).upper() for t in body.get("tokens") or []], body


def test_turn_121_fill_with_no_stock_and_no_incoming_says_so(fill_console):
    """Same state, the product with nothing on hand and nothing incoming: the cross line
    with its refer, never a bare header."""
    c, _product_id = fill_console(**{CODE.replace("-", "_"): Stock(on_hand=0)})

    text = c.say("1", _turn_121_verdict())

    assert text == f"{CODE} x 1: {CROSS} No stock and no incoming. {R}", text
    assert BARE_HEADER not in text


# --------------------------------------------------------------------------- #
# B. Never-stuck: a one-product inventory section with an empty lane_text
# --------------------------------------------------------------------------- #


def _policy() -> Policy:
    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    return Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _empty_section(code: str) -> dict[str, Any]:
    """`turn_runtime.envelope_of`'s shape for a fetch that rendered nothing: the lane's
    text stripped to "", no rows, the code in `miss`."""
    return {
        "domain": "inventory",
        "denied": False,
        "entities": [code],
        "product_codes": [code],
        "attachment_types": [],
        "figures": [],
        "files": [],
        "miss": [code],
        "has_result": False,
        "tool_has_result": False,
        "unresolved": [],
        "unresolved_suggestions": {},
        "error": None,
        "lane_text": "",
    }


def test_a_one_product_section_with_empty_lane_text_never_composes_to_a_bare_header():
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=121)

    text = compose([_empty_section(CODE)], state, _policy(), ctx=None).text

    assert f"No stock found for {CODE}." in text, text
    assert BARE_HEADER not in text, text
    assert text.strip() != "", text
