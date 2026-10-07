"""STALE-EXTRA-CARRY (prod incident 6-7 Oct 2026, contact 550337684, prompt v47).

A photo of seat covers minted a `kind_pick` ("seat cover (promotion / attachment type /
form)") with `asked_at_turn=None`; sixteen hours later it was still the open question.
Beside it, `focus.extra.category` and `focus.extra.specification` both held "seat cover"
with no uuid (the specification with `spec_key ""`), and those rows rode into every reuse
fetch: after a correct "SRT320-CR x 30" answer, "Got ETA ?" reached the resolver with
"seat cover" as a scope term, the described set qualified nothing, and the reply printed
the seat-cover miss instead of the product's ETA line.

Two rules pinned here (plan `PLAN-stale-extra-carry-07oct.md`, UAC alongside):

1. A `kind_pick` minted by `turn/apply.py::apply()` carries the turn it was asked at
   (AC-SEC-1), so the EXISTING stamp-aware arm (`_stock_task_is_the_current_question`,
   issue #1352) can age it: a kind_pick asked at turn N with a stock task touched at
   N+1 is not the current question at N+2, and a bare number is the task's quantity
   (AC-SEC-2). No new ageing rule.
2. `turn_runtime.with_carried_entities(unsettled_only=True)` does not hand a uuid-less
   `category` / `product_type` row, or a `specification` row with no uuid and no
   `spec_key`, to the resolver when the focus holds a resolved product that is not a
   described set's member (AC-SEC-3). Those rows can only become `scope_terms`, never a
   `*_ids` param. A described set's class word, a grounded specification, every row when
   no product is resolved, and every other kind are handed over exactly as today
   (AC-SEC-4).

Every uuid here is synthetic; the codes are catalogue product codes (public repo).
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus, Profile, State, focus_to_wire

from tests.chatbot._avail_mode_console import AvailConsole, Stock, _seed
from tests.chatbot._r9_engine_console import reply
from tests.chatbot._turn_helpers import build_policy, entity, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

pytestmark = pytest.mark.usefixtures("stub_access")

CODE = "SRT320-CR"
PRODUCT_UUID = "7d1f2b3c-4e5a-4f6b-8c7d-9e0f1a2b3c4d"
STALE_WORD = "seat cover"
AMBIGUOUS = {"Sorento": {"customer": 1, "transporter": 1}}


def _product_row(product_id: str = PRODUCT_UUID, *, described_set: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {
        "raw": CODE,
        "hint": "product",
        "canonical_code": CODE,
        "uuid": product_id,
        "current_message": False,
        "confident": True,
    }
    if described_set:
        row["described_set"] = True
    return row


def _stale_extra() -> dict[str, list[dict[str, Any]]]:
    """The two rows the photo turn left on `focus.extra`: the class word tagged
    `category` and the same words tagged `specification`, neither with a uuid, the
    specification with no registry key (prod session state, 7 Oct 2026)."""
    return {
        "category": [{"raw": STALE_WORD, "hint": "category", "canonical_code": STALE_WORD, "confident": True}],
        "specification": [
            {
                "raw": STALE_WORD,
                "hint": "specification",
                "canonical_code": STALE_WORD,
                "spec_key": "",
                "confident": True,
            }
        ],
    }


def _hints(parse_output: dict[str, Any]) -> list[str]:
    return [str(e.get("hint")) for e in parse_output.get("entities") or [] if isinstance(e, dict)]


# --------------------------------------------------------------------------- #
# 1. The kind_pick carries the turn it was asked at
# --------------------------------------------------------------------------- #


def _mint_kind_pick(turn_no: int, *, tokens: dict[str, dict[str, int]] | None = None):
    """A turn at `turn_no` whose one ambiguous token arms a kind_pick (the shape
    `tests/chatbot/test_samantha_26sep_s8_kind_pick_siblings.py` pins)."""
    resolved = dict(tokens or AMBIGUOUS)
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=turn_no)
    v = verdict(domain_hint=None, entities=[entity(token, hint="customer") for token in resolved])
    return apply(state, v, build_policy(), resolved=resolved)


def test_a_kind_pick_minted_by_apply_carries_the_turn_it_was_asked_at():
    """AC-SEC-1: never None. The prod kind_pick carried `asked_at_turn: null` and so
    read as "asked this turn" on every later turn."""
    _state, plan = _mint_kind_pick(7)

    assert plan.ask is not None and plan.ask.kind == "kind_pick", plan.ask
    assert plan.ask.asked_at_turn == 7, plan.ask


def test_the_queued_sibling_pick_is_stamped_with_the_turn_that_asks_it():
    """AC-SEC-1, second half: the sibling pick queued on the first one's payload is
    asked on a LATER turn and carries that turn, not the first pick's."""
    state1, plan1 = _mint_kind_pick(7, tokens={**AMBIGUOUS, "Mocha": {"customer": 1, "transporter": 1}})
    assert plan1.ask is not None and plan1.ask.kind == "kind_pick"

    answering = State(focus=state1.focus, pending=plan1.ask, profile=state1.profile, turn_no=9)
    _state2, plan2 = apply(answering, verdict(reference_positions=[1]), build_policy())

    assert plan2.ask is not None and plan2.ask.kind == "kind_pick", plan2.ask
    assert "kind_pick_queue_advanced" in plan2.trace.rules_fired, plan2.trace.rules_fired
    assert plan2.ask.asked_at_turn == 9, plan2.ask


def _stock_task(turn_no: int) -> task_mod.Task:
    return task_mod.Task(
        kind="stock_qty",
        domain="inventory",
        status=task_mod.OPEN,
        opened_at_turn=turn_no,
        touched_at_turn=turn_no,
        slots=(task_mod.Slot(key=PRODUCT_UUID, label=CODE, value=None),),
    )


def _bare_number_turn(pending: Any, turn_no: int) -> tuple[dict[str, Any], Any]:
    """A bare "5" at `turn_no` over the open stock task (touched at turn_no - 1) with
    `pending` still stored underneath. Returns the verdict (mutated by the rule) and the
    plan."""
    focus = Focus(products=[_product_row()], domains=["inventory"], tasks=(_stock_task(turn_no - 1),))
    state = State(focus=focus, pending=pending, profile=Profile(), turn_no=turn_no)
    v = verdict(reference_positions=[5])
    _state_out, plan = apply(state, v, build_policy())
    return v, plan


def test_a_kind_pick_with_an_int_stamp_ages_under_the_existing_rule():
    """The control (green before this lane): a kind_pick stamped at turn 7, a stock task
    touched at 8, a bare number at 9 is the task's quantity, issue #1352's own arm."""
    _state, plan = _mint_kind_pick(7)
    stamped = replace(plan.ask, asked_at_turn=7)

    v, plan2 = _bare_number_turn(stamped, 9)

    assert "bare_number_is_the_quantity" in plan2.trace.rules_fired, plan2.trace.rules_fired
    assert v["demand_qty"] == 5 and v["reference_positions"] == [], v


def test_a_kind_pick_minted_by_apply_is_not_the_current_question_once_a_stock_task_was_touched_after_it():
    """AC-SEC-2: the kind_pick `apply()` itself minted at turn 7 (not one the test
    stamped by hand) ages exactly like the control: the bare "5" at turn 9 is the stock
    task's quantity, never a pick off the 16-hour-old kind menu."""
    _state, plan = _mint_kind_pick(7)
    assert plan.ask is not None and plan.ask.kind == "kind_pick"

    v, plan2 = _bare_number_turn(plan.ask, 9)

    assert "bare_number_is_the_quantity" in plan2.trace.rules_fired, plan2.trace.rules_fired
    assert v["demand_qty"] == 5 and v["reference_positions"] == [], v


# --------------------------------------------------------------------------- #
# 2. Uuid-less class words stay off a product-scoped fetch
# --------------------------------------------------------------------------- #


def _reuse_turn() -> dict[str, Any]:
    """The parser's reading of "Got ETA ?": no entities, `entity_op reuse`."""
    return reply(entity_op="reuse", domain_hint="incoming", intent_hint="check_incoming")


def test_the_incident_focus_hands_no_class_word_to_the_resolver():
    """AC-SEC-3: the carried product (uuid, not a set member) beside the uuid-less
    "seat cover" category and the key-less "seat cover" specification: the resolver is
    asked about nothing (the product is settled, the words can build no `*_ids`)."""
    focus = Focus(products=[_product_row()], domains=["inventory"], extra=_stale_extra())

    out = turn_runtime.with_carried_entities(_reuse_turn(), focus, unsettled_only=True)

    assert _hints(out) == [], out.get("entities")


def test_a_uuid_less_product_type_word_stays_off_a_product_scoped_fetch_too():
    """AC-SEC-3: `product_type` is the other hint `resolve_gate` turns into a scope
    term."""
    extra = {"product_type": [{"raw": STALE_WORD, "hint": "product_type", "canonical_code": STALE_WORD, "confident": True}]}
    focus = Focus(products=[_product_row()], domains=["inventory"], extra=extra)

    out = turn_runtime.with_carried_entities(_reuse_turn(), focus, unsettled_only=True)

    assert _hints(out) == [], out.get("entities")


def test_a_described_sets_class_word_is_still_handed_over():
    """AC-SEC-4: the set's members are marked `described_set` (#1502), and the class
    word beside them is what re-describes the set on "cert?" after "which basins have
    incoming?"."""
    members = [_product_row(str(uuid.uuid4()), described_set=True) for _ in range(3)]
    focus = Focus(products=members, domains=["incoming"], extra=_stale_extra())

    out = turn_runtime.with_carried_entities(_reuse_turn(), focus, unsettled_only=True)

    assert sorted(_hints(out)) == ["category", "specification"], out.get("entities")


def test_a_grounded_specification_is_still_handed_over_beside_a_resolved_product():
    """AC-SEC-4: a specification with its registry key keeps its carry (fix round 8 on
    PR #833), whatever sits on `focus.products`."""
    grounded = {
        "specification": [
            {
                "raw": "gunmetal",
                "hint": "specification",
                "canonical_code": "gunmetal",
                "spec_key": "finish",
                "spec_value": "gunmetal",
                "confident": True,
            }
        ]
    }
    focus = Focus(products=[_product_row()], domains=["inventory"], extra=grounded)

    out = turn_runtime.with_carried_entities(_reuse_turn(), focus, unsettled_only=True)

    assert _hints(out) == ["specification"], out.get("entities")
    assert out["entities"][0]["spec_key"] == "finish"


def test_every_uuid_less_row_is_handed_over_when_no_product_is_resolved():
    """AC-SEC-4: with nothing settled, the carried words ARE the subject (contract 33,
    35) and the resolver is asked about all of them."""
    focus = Focus(products=[], domains=["inventory"], extra=_stale_extra())

    out = turn_runtime.with_carried_entities(_reuse_turn(), focus, unsettled_only=True)

    assert sorted(_hints(out)) == ["category", "specification"], out.get("entities")


def test_an_unresolved_product_code_beside_a_class_word_keeps_the_class_word():
    """AC-SEC-4: a carried product with NO uuid is not a resolved product; both rows go
    to the resolver as today."""
    unresolved = {k: v for k, v in _product_row().items() if k != "uuid"}
    focus = Focus(products=[unresolved], domains=["inventory"], extra=_stale_extra())

    out = turn_runtime.with_carried_entities(_reuse_turn(), focus, unsettled_only=True)

    assert sorted(_hints(out)) == ["category", "product", "specification"], out.get("entities")


@pytest.mark.parametrize("hint", ["brand", "promotion", "attachment_type", "customer"])
def test_every_other_uuid_less_kind_is_handed_over_beside_a_resolved_product(hint):
    """AC-SEC-4: #1502's own shape (a brand word mis-tagged `promotion` beside the
    product) is untouched: the word may resolve on its own; what it never does is gate
    the product lookup, and that rule lives at the runner's gate, not here."""
    extra = {hint: [{"raw": "SORENTO", "hint": hint, "canonical_code": "SORENTO", "confident": True}]}
    focus = Focus(products=[_product_row()], domains=["inventory"], extra=extra)

    out = turn_runtime.with_carried_entities(_reuse_turn(), focus, unsettled_only=True)

    assert _hints(out) == [hint], out.get("entities")


def test_a_turn_that_names_its_own_entities_is_untouched():
    """AC-SEC-4: the carry is the EMPTY case only."""
    own = reply(entities=[entity("SRT5674", hint="product")])
    focus = Focus(products=[_product_row()], domains=["inventory"], extra=_stale_extra())

    out = turn_runtime.with_carried_entities(own, focus, unsettled_only=True)

    assert out == own


# --------------------------------------------------------------------------- #
# 3. The incident, replayed through the real engine
# --------------------------------------------------------------------------- #


def _seed_product(session_factory, console: AvailConsole) -> str:
    seeded = _seed(session_factory, [CODE])
    console.codes.update(seeded)
    console.uuid_of.update({code: pid for pid, code in seeded.items()})
    return console.uuid_of[CODE]


def _carried_state(product_id: str, *, extra: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The state "srt320-cr x30" left: the product settled with its uuid under the
    inventory domain, the photo turn's extras beside it, no open question."""
    focus = Focus(products=[_product_row(product_id)], domains=["inventory"], extra=extra)
    return {"focus": focus_to_wire(focus), "open_question": None}


@pytest.fixture
def eta_console(session_factory, monkeypatch, stub_access):
    """The dealer console with SRT320-CR seeded and a shipment due, recording every
    resolver body `resolve_gate.resolve_entity_body` built on the way."""
    bodies: list[dict[str, Any]] = []
    real_body = resolve_gate.resolve_entity_body

    def recording_body(ctx, **kwargs):
        body = real_body(ctx, **kwargs)
        bodies.append(body)
        return body

    monkeypatch.setattr(resolve_gate, "resolve_entity_body", recording_body)

    def make(phone: str) -> tuple[AvailConsole, str]:
        console = AvailConsole(
            session_factory,
            monkeypatch,
            stub_access,
            phone=phone,
            **{CODE.replace("-", "_"): Stock(on_hand=30, eta=date(2026, 10, 20))},
        )
        product_id = _seed_product(session_factory, console)
        console.resolver_bodies = bodies  # type: ignore[attr-defined]
        return console, product_id

    return make


def _incoming_calls(console: AvailConsole) -> list[list[str] | None]:
    return [
        args.get("product_ids")
        for name, args in console.tool_calls
        if name in ("crm_incoming_stock_by_product", "crm_incoming_stock_list")
    ]


def test_got_eta_after_the_product_answer_is_the_products_eta_line(eta_console):
    """AC-SEC-5. The control turn first: the same "Got ETA ?" over a clean carry (no
    extras) is what the dealer must read. Then the incident carry on the same console:
    the reply is that same ETA line, no resolver body carries `scope_terms`, the
    incoming tool is called with the product's id alone, and "seat cover" is not said
    back."""
    c, product_id = eta_console("+60100000913")
    c.state = _carried_state(product_id, extra={})
    expected = c.say("Got ETA ?", _reuse_turn())
    assert CODE in expected and "20/10/2026" in expected, expected
    assert _incoming_calls(c) == [[product_id]], c.tool_calls
    c.resolver_bodies.clear()
    c.tool_calls.clear()

    c.state = _carried_state(product_id, extra=_stale_extra())

    text = c.say("Got ETA ?", _reuse_turn())

    assert text == expected, text
    assert STALE_WORD not in text.lower(), text
    assert _incoming_calls(c) == [[product_id]], c.tool_calls
    assert [b for b in c.resolver_bodies if b.get("scope_terms")] == [], c.resolver_bodies
