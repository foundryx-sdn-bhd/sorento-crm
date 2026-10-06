"""STOCK-FILL-PREDICATE (owner report, 6 Oct 2026 13:20 MYT, prod turn 121, prompt v47).

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
resolver did, which is what let the set predicate through), so before the fix the two
replays answer `Couldn't find: SORENTO.` instead of the bare header - the same defect
one step earlier: the fill turn was made about the carried word, not about the task's
product. Either way the resolver must never be asked about it on that turn, which the
seam assertion on the recorded resolver bodies pins; the predicate half of the rule is
pinned at the tool runner's gate, where the set predicate is attached.

Rules pinned here:

A. A `stock_qty` task FILL turn fetches the task's product(s) only. No carried uuid-less
   extra is sent to the resolver with a require-stock predicate, and no set predicate
   reaches a task's own fetch.
B. Never-stuck (owner): a lane header is never sent with an empty body. A section that
   misses for its only product renders that product's miss line
   (`turn/compose.py`'s own "No stock found for X."); a section whose tool answered
   but whose lane rendered nothing says the `error` arm's own retry sentence, never a
   false "no stock".

Every uuid and code fixture here is synthetic (public repo).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes import business
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.plan import FetchSpec
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State, focus_to_wire

from tests.chatbot._avail_mode_console import AvailConsole, Stock, _seed
from tests.chatbot._r9_engine_console import reply
from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

pytestmark = pytest.mark.usefixtures("stub_access")

CODE = "SRTWC8383-RL-GY"
R = "Please refer to your salesman."
TICK, CROSS = "✅", "❌"
DISCONTINUED = "⚠️  *(PRODUCT DISCONTINUED)*"
BARE_HEADER = f"*stock* for {CODE}:"


# --------------------------------------------------------------------------- #
# A. The turn 121 state, replayed through the real engine
# --------------------------------------------------------------------------- #


def _seed_product(session_factory, console: AvailConsole) -> str:
    """The product the photo named, discontinued as it is on the catalogue. Returns its
    uuid."""
    from app.models.product import Product

    seeded = _seed(session_factory, [CODE])
    console.codes.update(seeded)
    console.uuid_of.update({code: pid for pid, code in seeded.items()})
    product_id = console.uuid_of[CODE]

    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    product = db.query(Product).filter(Product.id == product_id).one()
    product.is_discontinued = True
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


def _assert_no_carry_reached_the_resolver(bodies: list[dict[str, Any]]) -> None:
    """Rule A at the resolver seam: no body of the fill turn carries a require leg or
    the carried SORENTO token."""
    for body in bodies:
        assert "require" not in body, body
        assert "SORENTO" not in [str(t).upper() for t in body.get("tokens") or []], body


@pytest.fixture
def fill_console(session_factory, monkeypatch, stub_access):
    """The dealer console on the turn 120 state. `resolver_bodies` records every body the
    lane built for the shared resolver on the way."""
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
        product_id = _seed_product(session_factory, console)
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
    _assert_no_carry_reached_the_resolver(c.resolver_bodies)


def test_turn_121_fill_with_no_stock_and_no_incoming_says_so(fill_console):
    """Same state, the product with nothing on hand and nothing incoming: the cross line
    with its refer, never a bare header."""
    c, product_id = fill_console(**{CODE.replace("-", "_"): Stock(on_hand=0)})

    text = c.say("1", _turn_121_verdict())

    assert text == f"{CODE} x 1: {CROSS} No stock and no incoming. {R}", text
    assert BARE_HEADER not in text
    assert c.stock_calls[-1]["product_ids"] == [product_id]
    _assert_no_carry_reached_the_resolver(c.resolver_bodies)


# --------------------------------------------------------------------------- #
# A, the predicate half: a task's own fetch never carries the turn's set predicate
# --------------------------------------------------------------------------- #


def test_a_tasks_own_fetch_carries_no_set_predicate(session_factory, monkeypatch):
    """`turn_runtime.make_tool_runner`: the gate handed to `lanes.business.run_fetch`
    for a `filters["task"]` spec has no `predicate`, whatever the resolver derived this
    turn (a stray non-code word on the fill verdict is enough for `derive_require` to
    build one). A plain spec keeps it, as today."""
    gates: list[dict[str, Any]] = []

    def fake_run_fetch(payload, **kwargs):
        gates.append(payload["gate"])
        return {"fetch": {"has_result": True, "response": "stubbed", "answers": []}}

    monkeypatch.setattr(business, "run_fetch", fake_run_fetch)
    product_id = "11111111-1111-4111-8111-111111111111"
    row = {"uuid": product_id, "entity_type": "product", "canonical_code": CODE, "hint": "product", "raw": CODE}
    set_predicate = {"require": {"stock": True}, "qualifying_total": 2000, "breakdown": {"rows": []}}
    runner = turn_runtime.make_tool_runner(
        session_factory(),
        ctx={"parse": {"output": verdict(domain_hint="inventory", intent_hint="check_stock")}},
        verdict=verdict(domain_hint="inventory", intent_hint="check_stock"),
        focus=Focus(domains=["inventory"]),
        compatible_entities=[row],
        predicate=set_predicate,
        unplaced={},
        space_id=None,
        dry_run=True,
        turn_trace=type("Trace", (), {"add": lambda self, *a, **k: None})(),
    )

    runner(
        "inventory",
        FetchSpec(
            domain="inventory",
            entities=[row],
            filters={"task": "stock_qty", "requested_quantities": {product_id: 1}},
            date_window=None,
        ),
    )
    runner("inventory", FetchSpec(domain="inventory", entities=[row], filters={}, date_window=None))

    task_gate, plain_gate = gates
    assert "predicate" not in task_gate, task_gate
    assert plain_gate.get("predicate") == set_predicate


# --------------------------------------------------------------------------- #
# B. Never-stuck: a one-product inventory section with an empty lane_text
# --------------------------------------------------------------------------- #


def _policy() -> Policy:
    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    return Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _empty_section(code: str, *, tool_has_result: bool) -> dict[str, Any]:
    """`turn_runtime.envelope_of`'s shape for a fetch that rendered nothing: the lane's
    text stripped to "", no rows, the code in `miss`. `tool_has_result` is the tool's
    own verdict: False for a miss, True for the prod shape (a withheld set whose only
    line was the header `envelope_of` strips)."""
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
        "tool_has_result": tool_has_result,
        "unresolved": [],
        "unresolved_suggestions": {},
        "error": None,
        "lane_text": "",
    }


def _compose(env: dict[str, Any]) -> str:
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=121)
    return compose([env], state, _policy(), ctx=None).text


def test_a_one_product_section_with_empty_lane_text_never_composes_to_a_bare_header():
    text = _compose(_empty_section(CODE, tool_has_result=False))

    assert f"No stock found for {CODE}." in text, text
    assert BARE_HEADER not in text, text
    assert text.strip() != "", text


def test_a_section_the_tool_answered_but_the_lane_left_empty_never_says_no_stock():
    """The prod turn 121 envelope exactly (reviewer S1): the tool had a result, the
    withheld set left the lane with nothing under the header. Not a bare header, and
    not a false "No stock found" over a product with stock: the retry sentence."""
    text = _compose(_empty_section(CODE, tool_has_result=True))

    assert "No stock found" not in text, text
    assert text.strip() != BARE_HEADER, text
    assert text.startswith(f"{BARE_HEADER}\nI could not fetch stock just now, please try again."), text
