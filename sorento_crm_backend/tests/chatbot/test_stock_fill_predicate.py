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

Rules pinned here (owner rulings, 6 Oct 2026 17:40 and 17:50):

A. A fetch already scoped to a specific resolved product (a spec entity with a uuid)
   never takes a set predicate, on ANY turn: a task fill, a plain reuse, a fresh pick.
   A set predicate exists only for a set question with no specific product. At the
   runner's gate "specific product" is told apart from "the set's members" by whether
   the spec's products ARE the population the resolver placed this turn: a set question
   (and a follow-up over a carried set) re-resolves its members onto the spec; a carried
   subject or a task slot is not something the resolver placed this turn. A stray
   non-product entity carried without a uuid (brand / promotion / category /
   product_type row) may resolve on its own, and may even describe a set of its own,
   but never gates or widens the product lookup: the stock tool is called with the
   product's id alone and the gate the lane receives carries no `predicate`. Nor is
   that carried word said back as a miss: `unplaced_tokens` names the tokens THIS
   message typed.
B. Never-stuck, and no claim without a lookup (the #1501 principle): a lane header is
   never sent with an empty body. A section whose tool ran for its only product and
   found nothing renders the existing miss line ("No stock found for X."); a section
   whose tool answered but whose lane rendered nothing, or for which no tool ran at
   all, says the `error` arm's own retry sentence, never a false "no stock".

Every uuid here is synthetic; the codes are catalogue product codes (public repo).
"""
from __future__ import annotations

import uuid
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
DISCONTINUED = "⚠️  *(PRODUCT DISCONTINUED)*"
BARE_HEADER = f"*stock* for {CODE}:"
SET_OPENER = "Here's what you want:"
STALE_WORD = "SORENTO"
GOT_STOCK = f"{CODE} x 1: {TICK} Got stock.\n{DISCONTINUED}"
NO_STOCK = f"{CODE} x 1: {CROSS} No stock and no incoming. {R}"
HOW_MANY = f"How many units of {CODE}?"


def _stale_extra(hint: str, word: str = STALE_WORD) -> dict[str, list[dict[str, Any]]]:
    """The brand word the photo read, mis-tagged `hint`, carried on `focus.extra` with
    no uuid (prod: `promotion`)."""
    return {hint: [{"raw": word, "hint": hint, "canonical_code": word, "confident": True}]}


# --------------------------------------------------------------------------- #
# A. The turn 121 state, replayed through the real engine
# --------------------------------------------------------------------------- #


def _seed_product(session_factory, console: AvailConsole, *, brand_word: str | None = None) -> str:
    """The product the photo named, discontinued as it is on the catalogue. With
    `brand_word`, an active Brand row of that name is seeded and the product (and one
    other) put under it, so a carried word of that name BINDS as a brand and describes a
    set of its own (reviewer round 5's shape). Returns the product's uuid."""
    from app.models.product import Brand, Product

    seeded = _seed(session_factory, [CODE])
    console.codes.update(seeded)
    console.uuid_of.update({code: pid for pid, code in seeded.items()})
    product_id = console.uuid_of[CODE]

    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    product = db.query(Product).filter(Product.id == product_id).one()
    product.is_discontinued = True
    if brand_word:
        brand = Brand(
            id=str(uuid.uuid4()), brand_code=brand_word, brand_name=brand_word, company_id=product.company_id, is_active=True
        )
        db.add(brand)
        db.flush()
        product.brand_id = brand.id
        other = db.query(Product).filter(Product.id == console.uuid_of["SRT5674"]).one()
        other.brand_id = brand.id
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


def _turn_120_state(product_id: str, *, hint: str = "promotion") -> dict[str, Any]:
    """The state turn 120 left: the product (settled, with its uuid), the brand word
    SORENTO mis-tagged `hint` on `focus.extra` with no uuid, one open stock_qty task
    with one empty slot."""
    task = task_mod.Task(
        kind="stock_qty",
        domain="inventory",
        status=task_mod.OPEN,
        opened_at_turn=120,
        touched_at_turn=120,
        slots=(task_mod.Slot(key=product_id, label=CODE, value=None),),
    )
    focus = Focus(products=[_product_row(product_id)], domains=["inventory"], tasks=(task,), extra=_stale_extra(hint))
    return {"focus": focus_to_wire(focus), "open_question": None}


def _carried_product_state(product_id: str, *, hint: str = "promotion") -> dict[str, Any]:
    """The sibling state: the product carried on the focus with its uuid, the same stale
    uuid-less extra, and no open task at all."""
    focus = Focus(products=[_product_row(product_id)], domains=["inventory"], extra=_stale_extra(hint))
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

    def make(brand_word: str | None = None, **stock_facts: Stock) -> tuple[AvailConsole, str]:
        console = AvailConsole(
            session_factory, monkeypatch, stub_access, phone="+60100000912", **stock_facts
        )
        product_id = _seed_product(session_factory, console, brand_word=brand_word)
        console.fetch_gates = gates  # type: ignore[attr-defined]
        return console, product_id

    return make


IN_STOCK = {CODE.replace("-", "_"): Stock(on_hand=100, discontinued=True)}


def test_turn_121_fill_answers_the_products_availability_line(fill_console):
    """The owner's turn 121, product in stock and discontinued: the availability line
    and the marker and nothing else. No bare "*stock* for" header, no set, no salesman
    line, no sentence about the carried word."""
    c, product_id = fill_console(**IN_STOCK)
    c.state = _turn_120_state(product_id)

    text = c.say("1", _turn_121_verdict())

    assert text == GOT_STOCK, text
    _assert_product_scoped(c, product_id)


def test_turn_121_fill_with_no_stock_and_no_incoming_says_so(fill_console):
    """Same state, the product with nothing on hand and nothing incoming: the cross line
    with its refer, never a bare header."""
    c, product_id = fill_console(**{CODE.replace("-", "_"): Stock(on_hand=0)})
    c.state = _turn_120_state(product_id)

    text = c.say("1", _turn_121_verdict())

    assert text == NO_STOCK, text
    _assert_product_scoped(c, product_id)


@pytest.mark.parametrize("hint", ["promotion", "brand", "category", "product_type"])
def test_a_stale_word_that_binds_as_a_brand_never_gates_the_fill(fill_console, hint):
    """Reviewer round 5: the stale word tagged `category` or `product_type` becomes a
    scope term, BINDS to an active brand of that name and describes a set of its own
    (the brand's products with stock), the carried product among them. That set is not
    the fetch's subject: the dealer's "1" still gets the product's own line, never the
    scope-needed refusal, never a set, and the word is not said back."""
    c, product_id = fill_console(brand_word=STALE_WORD, **IN_STOCK)
    c.state = _turn_120_state(product_id, hint=hint)

    text = c.say("1", _turn_121_verdict())

    assert text == GOT_STOCK, text
    _assert_product_scoped(c, product_id)


def test_a_plain_reuse_turn_over_a_carried_product_and_a_stale_extra_stays_product_scoped(fill_console):
    """The sibling case (owner, 6 Oct 2026 17:40): no open task. The product is carried
    on the focus with its uuid beside the stale uuid-less SORENTO row, and the dealer
    sends a plain stock check (`entity_op reuse`, no entities). The reply is about the
    product (the quantity question, then its availability line), never a catalogue set."""
    c, product_id = fill_console(**IN_STOCK)
    c.state = _carried_product_state(product_id)

    first = c.say("check stock", reply(entity_op="reuse"))

    assert first == HOW_MANY, first
    _assert_product_scoped(c, product_id)

    second = c.say("1", _turn_121_verdict())

    assert second == GOT_STOCK, second
    _assert_product_scoped(c, product_id)


def test_a_plain_reuse_turn_with_a_binding_stale_category_word_stays_product_scoped(fill_console):
    """The sibling case with reviewer round 5's shape: the stale word tagged `category`
    binds as a brand."""
    c, product_id = fill_console(brand_word=STALE_WORD, **IN_STOCK)
    c.state = _carried_product_state(product_id, hint="category")

    first = c.say("check stock", reply(entity_op="reuse"))

    assert first == HOW_MANY, first
    _assert_product_scoped(c, product_id)


# --------------------------------------------------------------------------- #
# A at the runner's gate: the one place a predicate is attached to a fetch
# --------------------------------------------------------------------------- #


def _runner(session_factory, *, compatible: list[dict[str, Any]], predicate: dict[str, Any]):
    """`make_tool_runner` in the production shape: the runner's gate STARTS as the
    resolver's own gate dict, which already carries the predicate the engine read off it
    (reviewer round 2), and `compatible` is what the resolver placed this turn."""
    return turn_runtime.make_tool_runner(
        session_factory(),
        ctx={"parse": {"output": verdict(domain_hint="inventory", intent_hint="check_stock")}},
        verdict=verdict(domain_hint="inventory", intent_hint="check_stock"),
        focus=Focus(domains=["inventory"]),
        compatible_entities=compatible,
        predicate=predicate,
        resolver_gate={"gate_passed": True, "predicate": predicate},
        unplaced={},
        space_id=None,
        dry_run=True,
        turn_trace=type("Trace", (), {"add": lambda self, *a, **k: None})(),
    )


def test_a_fetch_scoped_to_a_resolved_product_carries_no_set_predicate(session_factory, monkeypatch):
    """`turn_runtime.make_tool_runner`: a spec whose product (a real uuid) is NOT the set
    the resolver placed this turn hands `lanes.business.run_fetch` a gate with no
    `predicate`, task filter or not, whether the predicate describes a set or not. A set
    question's spec IS the resolver's population, so it keeps its predicate; so does a
    spec that names only a class word."""
    gates: list[dict[str, Any]] = []

    def fake_run_fetch(payload, **kwargs):
        gates.append(payload["gate"])
        return {"fetch": {"has_result": True, "response": "stubbed", "answers": []}}

    monkeypatch.setattr(business, "run_fetch", fake_run_fetch)
    product_id = "11111111-1111-4111-8111-111111111111"
    row = {"uuid": product_id, "entity_type": "product", "canonical_code": CODE, "hint": "product", "raw": CODE}
    other = {**row, "uuid": "22222222-2222-4222-8222-222222222222", "canonical_code": "ZZT-OTHER", "raw": "ZZT-OTHER"}
    class_word = {"hint": "category", "raw": "basin", "canonical_code": "basin", "current_message": True}
    bare_leg = {"require": {"stock": True}, "qualifying_total": 2000, "breakdown": {"rows": []}}
    described = {**bare_leg, "description": [{"key": "brand", "label": "Brand", "value": "Zzt brand"}]}
    task_spec = FetchSpec(
        domain="inventory",
        entities=[row],
        filters={"task": "stock_qty", "requested_quantities": {product_id: 1}},
        date_window=None,
    )
    carried_spec = FetchSpec(domain="inventory", entities=[row], filters={}, date_window=None)

    # The turn 121 shape: the resolver saw only the stray word and placed no product.
    runner = _runner(session_factory, compatible=[], predicate=bare_leg)
    runner("inventory", task_spec)
    runner("inventory", carried_spec)
    # Reviewer round 5: the stray word (tagged category) bound to a brand and DESCRIBED a
    # set whose population the resolver placed, the carried product among it. The
    # spec's product is not that set, so the predicate still goes.
    runner = _runner(session_factory, compatible=[row, other], predicate=described)
    runner("inventory", task_spec)
    runner("inventory", carried_spec)
    # A set question ("which basins have stock"): the spec IS the resolver's population.
    runner = _runner(session_factory, compatible=[row, other], predicate=described)
    runner("inventory", FetchSpec(domain="inventory", entities=[row, other], filters={}, date_window=None))
    # A spec that names only a class word has no product to be scoped to.
    runner = _runner(session_factory, compatible=[], predicate=bare_leg)
    runner("inventory", FetchSpec(domain="inventory", entities=[class_word], filters={}, date_window=None))

    task_gate, carried_gate, task_described_gate, carried_described_gate, set_gate, class_gate = gates
    for gate in (task_gate, carried_gate, task_described_gate, carried_described_gate):
        assert "predicate" not in gate, gate
    assert set_gate.get("predicate") == described
    assert class_gate.get("predicate") == bare_leg


def test_unplaced_tokens_name_only_what_this_message_typed():
    """`turn_runtime.unplaced_tokens`: a carried row (`current_message: False`) the engine
    handed to the resolver is not a token this message named, so its miss is not said
    back; a typed token's miss still is."""
    resolved = {"unresolved_tokens": ["sorento", "wc2867"]}
    entities = [
        {"raw": "SORENTO", "hint": "promotion", "current_message": False},
        {"raw": "wc2867", "hint": "product", "current_message": True},
    ]
    assert turn_runtime.unplaced_tokens(entities, resolved) == {"wc2867": "wc2867"}


# --------------------------------------------------------------------------- #
# B. Never-stuck: a one-product inventory section with an empty lane_text
# --------------------------------------------------------------------------- #


def _policy() -> Policy:
    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    return Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _empty_section(code: str, *, tool_has_result: bool, tool_ran: bool = True) -> dict[str, Any]:
    """`turn_runtime.envelope_of`'s shape for a fetch that rendered nothing: the lane's
    text stripped to "", no rows, the code in `miss`. `tool_has_result` is the tool's
    own verdict: False for a miss, True for the prod shape (a withheld set whose only
    line was the header `envelope_of` strips). `tool_ran` is whether a tool was called
    FOR this code at all (owner, 6 Oct 17:50: no claim without a lookup)."""
    return {
        "tool_ran": tool_ran,
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
    """The stock tool ran for the code and came back empty: the existing miss line."""
    text = _compose(_empty_section(CODE, tool_has_result=False, tool_ran=True))

    assert f"No stock found for {CODE}." in text, text
    assert BARE_HEADER not in text, text
    assert text.strip() != "", text


def test_a_section_no_tool_ran_for_never_claims_no_stock():
    """Owner, 6 Oct 17:50 (the #1501 principle): no tool ran for this code (a set
    predicate's refusal, a gate refusal), so "No stock found" would be a claim nothing
    looked up. The existing retry wording under the header, never a stock claim."""
    text = _compose(_empty_section(CODE, tool_has_result=False, tool_ran=False))

    assert "No stock found" not in text, text
    assert text.strip() != BARE_HEADER, text
    assert text.startswith(f"{BARE_HEADER}\nI could not fetch stock just now, please try again."), text


def test_a_section_the_tool_answered_but_the_lane_left_empty_never_says_no_stock():
    """The prod turn 121 envelope exactly (reviewer round 1 S1): the tool had a result,
    the withheld set left the lane with nothing under the header. Not a bare header, and
    not a false "No stock found" over a product with stock: the retry sentence."""
    text = _compose(_empty_section(CODE, tool_has_result=True))

    assert "No stock found" not in text, text
    assert text.strip() != BARE_HEADER, text
    assert text.startswith(f"{BARE_HEADER}\nI could not fetch stock just now, please try again."), text


def test_envelope_of_says_whether_a_tool_ran_for_the_codes():
    """`turn_runtime.envelope_of`: `tool_ran` is True only for a fragment a real tool
    call produced (`fetch.tool` stamped by `fetch_result`) that was about these codes
    (no `outcome: not_found`)."""
    spec = FetchSpec(domain="inventory", entities=[], filters={}, date_window=None)
    row = {"entity_type": "product", "uuid": "11111111-1111-4111-8111-111111111111", "code": CODE}
    empty_from_tool = {"fetch": {"has_result": False, "response": "", "answers": [], "tool": {"name": "crm_inventory_stock_balance_list"}}}
    refused_before_any_tool = {"fetch": {"has_result": False, "response": "No matching results found."}, "outcome": "not_found"}
    tool_ran_but_not_for_the_code = {"fetch": {"has_result": False, "response": "", "tool": {"name": "x"}}, "outcome": "not_found"}

    assert turn_runtime.envelope_of(empty_from_tool, spec, [row])["tool_ran"] is True
    assert turn_runtime.envelope_of(refused_before_any_tool, spec, [row])["tool_ran"] is False
    assert turn_runtime.envelope_of(tool_ran_but_not_for_the_code, spec, [row])["tool_ran"] is False
