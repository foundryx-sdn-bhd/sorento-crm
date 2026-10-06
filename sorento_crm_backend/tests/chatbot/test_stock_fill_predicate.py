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

Rules pinned here (owner ruling, 6 Oct 2026 17:40):

A. A fetch already scoped to a specific resolved product (a spec entity with a uuid)
   never takes a set predicate, on ANY turn: a task fill, a plain reuse, a fresh pick.
   A set predicate exists only for a set question with no specific product. A stray
   non-product entity (a stale brand / promotion / category row without uuid) may
   resolve on its own but never gates or widens the product lookup: the stock tool is
   called with the product's id alone, and the gate handed to the lane carries no
   `predicate`.
B. Never-stuck: a lane header is never sent with an empty body. A section that misses
   for its only product renders that product's miss line (`turn/compose.py`'s own "No
   stock found for X."); a section whose tool answered but whose lane rendered nothing
   says the `error` arm's own retry sentence, never a false "no stock".

On the blank test schema the carried SORENTO word resolves to nothing, so production's
own not-found composer also names it ("Couldn't find: SORENTO.") and re-renders the rows
through its own grammar, which carries no discontinued marker; in prod the same word is a
real brand and resolves. The replays therefore pin the product's own line and the gate,
not the whole reply text. (That a carried word nobody typed this turn is named at all,
and that the re-render drops the marker, are pre-existing behaviours of the unplaced-
token path, reported on the PR and not changed here.)

Every uuid here is synthetic; the codes are catalogue product codes (public repo).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes import business
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
BARE_HEADER = f"*stock* for {CODE}:"
SET_OPENER = "Here's what you want:"
STALE_EXTRA = {"promotion": [{"raw": "SORENTO", "hint": "promotion", "canonical_code": "SORENTO", "confident": True}]}


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


def _product_row(product_id: str) -> dict[str, Any]:
    return {
        "raw": CODE,
        "hint": "product",
        "canonical_code": CODE,
        "uuid": product_id,
        "current_message": False,
        "confident": True,
    }


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
    focus = Focus(products=[_product_row(product_id)], domains=["inventory"], tasks=(task,), extra=dict(STALE_EXTRA))
    return {"focus": focus_to_wire(focus), "open_question": None}


def _carried_product_state(product_id: str) -> dict[str, Any]:
    """The sibling state: the product carried on the focus with its uuid, the same stale
    uuid-less extra, and no open task at all."""
    focus = Focus(products=[_product_row(product_id)], domains=["inventory"], extra=dict(STALE_EXTRA))
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


def _assert_product_scoped(console: AvailConsole, product_id: str) -> None:
    """Rule A at the lane seam: the stock tool was called with the product's id alone, and
    every gate the lane received carried no set predicate and only that product."""
    assert console.stock_calls[-1]["product_ids"] == [product_id]
    assert console.fetch_gates, "the lane was never asked"
    for gate in console.fetch_gates:
        assert "predicate" not in gate, gate
        uuids = [e.get("uuid") for e in gate.get("compatible_entities") or [] if isinstance(e, dict)]
        assert uuids == [product_id], gate.get("compatible_entities")


def _assert_no_set_and_no_bare_header(text: str) -> None:
    assert BARE_HEADER not in text, text
    assert SET_OPENER not in text, text
    assert "No stock found" not in text, text
    assert f"\n\n{R}" not in text, text


@pytest.fixture
def fill_console(session_factory, monkeypatch, stub_access):
    """The dealer console with the product seeded. `fetch_gates` records every gate the
    lane (`lanes.business.run_fetch`) received on the way; the fetch itself is real."""
    gates: list[dict[str, Any]] = []
    real_run_fetch = business.run_fetch

    def recording_run_fetch(payload, **kwargs):
        gates.append(dict(payload.get("gate") or {}))
        return real_run_fetch(payload, **kwargs)

    monkeypatch.setattr(business, "run_fetch", recording_run_fetch)

    def make(**stock_facts: Stock) -> tuple[AvailConsole, str]:
        console = AvailConsole(
            session_factory, monkeypatch, stub_access, phone="+60100000912", **stock_facts
        )
        product_id = _seed_product(session_factory, console)
        console.fetch_gates = gates  # type: ignore[attr-defined]
        return console, product_id

    return make


def test_turn_121_fill_answers_the_products_availability_line(fill_console):
    """The owner's turn 121, product in stock and discontinued: the availability line
    and the marker, no bare "*stock* for" header, no set, no salesman line."""
    c, product_id = fill_console(**{CODE.replace("-", "_"): Stock(on_hand=100, discontinued=True)})
    c.state = _turn_120_state(product_id)

    text = c.say("1", _turn_121_verdict())

    assert text.split("\n")[0] == f"{CODE} x 1: {TICK} Got stock.", text
    _assert_no_set_and_no_bare_header(text)
    _assert_product_scoped(c, product_id)


def test_turn_121_fill_with_no_stock_and_no_incoming_says_so(fill_console):
    """Same state, the product with nothing on hand and nothing incoming: the cross line
    with its refer, never a bare header."""
    c, product_id = fill_console(**{CODE.replace("-", "_"): Stock(on_hand=0)})
    c.state = _turn_120_state(product_id)

    text = c.say("1", _turn_121_verdict())

    assert text.split("\n")[0] == f"{CODE} x 1: {CROSS} No stock and no incoming. {R}", text
    assert BARE_HEADER not in text and SET_OPENER not in text, text
    _assert_product_scoped(c, product_id)


def test_a_plain_reuse_turn_over_a_carried_product_and_a_stale_extra_stays_product_scoped(fill_console):
    """The sibling case (owner, 6 Oct 2026 17:40): no open task. The product is carried
    on the focus with its uuid beside the stale uuid-less SORENTO row, and the dealer
    sends a plain stock check (`entity_op reuse`, no entities). The reply is about the
    product (the quantity question, then its availability line), never a catalogue set."""
    c, product_id = fill_console(**{CODE.replace("-", "_"): Stock(on_hand=100, discontinued=True)})
    c.state = _carried_product_state(product_id)

    first = c.say("check stock", reply(entity_op="reuse"))

    assert f"How many units of {CODE}?" in first.split("\n"), first
    _assert_no_set_and_no_bare_header(first)
    _assert_product_scoped(c, product_id)

    second = c.say("1", _turn_121_verdict())

    assert second.split("\n")[0] == f"{CODE} x 1: {TICK} Got stock.", second
    _assert_no_set_and_no_bare_header(second)
    _assert_product_scoped(c, product_id)


# --------------------------------------------------------------------------- #
# A at the runner's gate: the one place a predicate is attached to a fetch
# --------------------------------------------------------------------------- #


def test_a_fetch_scoped_to_a_resolved_product_carries_no_set_predicate(session_factory, monkeypatch):
    """`turn_runtime.make_tool_runner`: a spec whose entity is a product with a real uuid
    hands `lanes.business.run_fetch` a gate with no `predicate`, whatever the resolver
    derived this turn, task filter or not. A spec that names only a class word (a set
    question) keeps it.

    Production shape: the runner's gate STARTS as the resolver's own gate dict, which
    already carries the predicate the engine read off it, so the arm must remove the key,
    not merely refrain from adding it."""
    gates: list[dict[str, Any]] = []

    def fake_run_fetch(payload, **kwargs):
        gates.append(payload["gate"])
        return {"fetch": {"has_result": True, "response": "stubbed", "answers": []}}

    monkeypatch.setattr(business, "run_fetch", fake_run_fetch)
    product_id = "11111111-1111-4111-8111-111111111111"
    row = {"uuid": product_id, "entity_type": "product", "canonical_code": CODE, "hint": "product", "raw": CODE}
    class_word = {"hint": "category", "raw": "basin", "canonical_code": "basin", "current_message": True}
    set_predicate = {"require": {"stock": True}, "qualifying_total": 2000, "breakdown": {"rows": []}}
    runner = turn_runtime.make_tool_runner(
        session_factory(),
        ctx={"parse": {"output": verdict(domain_hint="inventory", intent_hint="check_stock")}},
        verdict=verdict(domain_hint="inventory", intent_hint="check_stock"),
        focus=Focus(domains=["inventory"]),
        compatible_entities=[row],
        predicate=set_predicate,
        resolver_gate={"gate_passed": True, "predicate": set_predicate},
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
    runner("inventory", FetchSpec(domain="inventory", entities=[class_word], filters={}, date_window=None))

    task_gate, carried_gate, set_gate = gates
    assert "predicate" not in task_gate, task_gate
    assert "predicate" not in carried_gate, carried_gate
    assert set_gate.get("predicate") == set_predicate


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
