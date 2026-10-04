"""Phase 2 RED tests - issue #1262 (Samantha case), group B, slice 4, finding F3.

Turns T6 to T9 of the diagnosed conversation. Source:
`documentation/plans/chatbot/PLAN-chatbot-samantha-slices-26sep.md` slice 4 and
`chatbot-samantha-slices-26sep-acceptance-criteria.md` AC-S4-1, AC-S4-2, AC-S4-3.

The bug (`turn/decide.py::_subject_reading`, read from the OUTSTANDING_KINDS arm at
`decide()`): an open outstanding offer (`pending.kind` in `OUTSTANDING_KINDS`,
`pending.payload["domain"]` the domain it was asked for) REFINES on any turn that names
an entity and no domain word of its own, with no comparison at all between the verdict's
own `domain_hint` and the offer's domain. A photo read as "X5: M210-GM" (T7) carries no
domain word (`domain_in_message: false`) but the parser DOES read it as an incoming
question (`domain_hint: "incoming"`) - the REFINE arm forces it back onto the open
order-domain offer instead (`turn/apply.py::_answer_outstanding`,
`focus.domains = [asked_for]`), and three photos in a row (T7, T8, T9) each re-arm the
same question and replay the same poisoned filters at `crm_outstanding_report`.

T6 ("got eta") is the other half: `domain_in_message: true`, `domain_hint: "incoming"`,
no entities - the message names a domain of its own but decide() files it as CARRY
`nothing_answered` (no entity, so none of the NEW_ASK rows fire) and
`_answer_pending`'s tail (`answer_pending_not_an_answer`) keeps the pending open exactly
as it was, unmodified. Owner ruling (hand pass 3, T6 half - PLAN "Owner rulings"): a turn
naming another domain closes the outstanding offer.

Both are read PURELY off the verdict's own fields (`domain_in_message`, `domain_hint`)
against the pending's own stored domain (`pending.payload["domain"]`) - never a text
match, never a hard-coded phrase (owner ruling 1 is about quantity, not this finding, but
the same "read the parser's own output" discipline applies).

Seam chosen (tester's own call, `documentation/plans/chatbot` convention, e.g.
`test_outstanding_lane.py`'s own "Tester's own choices" section): `decide()` directly for
the pure reading (AC-S4-1's first half), `apply()` for the state/domain effect every AC
needs, and `apply()` -> `turn_runtime.lane_parse_output(..., domain=spec.domain)` ->
`lanes.business.run_fetch` for AC-S4-1's tool-pick half - the same three-seam chain
`turn_runtime.py::_narrow_and_plan`'s own `runner()` uses per fetch spec in production
(`lane_out = lane_parse_output(verdict, focus=focus, domain=domain, policy=policy)`,
then `run_fetch` reads `domain = parse_output.get("domain_hint")` to pick the tool).
"""
from __future__ import annotations

from app.services.chatbot.turn.decide import CARRY, NEW_ASK, REFINE, decide
from app.services.chatbot.turn.pending import Pending
from app.services.chatbot.turn.state import Focus, Profile, State
from tests.chatbot._turn_helpers import build_policy, entity, verdict

OUTSTANDING_OFFER = Pending(
    kind="outstanding_scope",
    expects=None,
    options=[],
    team=None,
    payload={"domain": "order"},
    asked_at_turn=1,
)


def _state_with_offer(**focus_kwargs) -> State:
    focus = Focus(
        customers=focus_kwargs.pop(
            "customers", [{"raw": "Cheng Huat Sentul", "hint": "customer", "uuids": ["c1"]}]
        ),
        **focus_kwargs,
    )
    return State(focus=focus, pending=OUTSTANDING_OFFER, profile=Profile())


# --------------------------------------------------------------------------- #
# AC-S4-1 (T7): entities, no domain word, domain_hint names ANOTHER domain.
# --------------------------------------------------------------------------- #


def test_t7_incoming_verdict_under_outstanding_offer_is_not_a_refine() -> None:
    """`decide()` alone. Today: REFINE `refines_standing_subject`, because
    `_subject_reading`'s `false, entities` row never reads `domain_hint` at all.

    Fixed reading: NEW_ASK (the plan's own words - "else NEW_ASK"), because the
    verdict's own `domain_hint` ("incoming") disagrees with the offer's stored domain
    ("order")."""
    v = verdict(
        entities=[entity("M210-GM", hint="product")],
        domain_in_message=False,
        domain_hint="incoming",
    )
    decision = decide(v, Focus(), OUTSTANDING_OFFER)

    assert decision.kind != REFINE, (
        f"an outstanding offer (domain 'order') must not REFINE over a verdict whose "
        f"own domain_hint is 'incoming': got {decision.kind!r}/{decision.why!r}"
    )
    assert decision.kind == NEW_ASK, (
        f"a domain_hint mismatch under an open outstanding offer must read NEW_ASK: "
        f"got {decision.kind!r}/{decision.why!r}"
    )


def test_t7_incoming_parser_turn_never_picks_crm_outstanding_report() -> None:
    """`apply()` -> `lane_parse_output(domain=spec.domain)` -> `run_fetch`, the real
    per-fetch-spec chain `turn_runtime.py::_narrow_and_plan`'s `runner()` uses.

    Today: `apply()` forces `focus.domains = ["order"]` (the REFINE arm's
    `asked_for` carry), so `spec.domain == "order"` reaches `run_fetch` as
    `domain_hint`, and a product-shaped ask with the SO grant short-circuits into
    the outstanding scope question (`result["fetch"]["outstanding_report"] is True`,
    no MCP tool ever called - `_outstanding_scope_ask` fires before any tool pick).

    Fixed: `spec.domain == "incoming"`, `run_fetch` never enters the
    `domain == "order"` branch at all, and the REAL incoming tool
    (`crm_incoming_stock_list`, `policy_rows.py`'s own frozen seed) is what gets
    called - asserted on both the call log and the `outstanding_report` flag so a
    coder cannot satisfy this by merely suppressing the flag while still routing
    through the order branch.
    """
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.policy import default_policy

    state = _state_with_offer()
    v = verdict(
        entities=[entity("M210-GM", hint="product")],
        domain_in_message=False,
        domain_hint="incoming",
    )
    state2, plan = apply(state, v, build_policy())
    assert plan.fetch, f"apply() planned no fetch at all: {plan}"
    spec = plan.fetch[0]

    lane_out = turn_runtime.lane_parse_output(
        v, focus=state2.focus, domain=spec.domain, policy=default_policy()
    )
    # The real production shape (`entity_type`, not the internal Focus row's `hint`) -
    # `turn_runtime._entities_for(spec, compatible)` is what `runner()` feeds
    # `gate.compatible_entities` with; `compatible=[]` because no resolver ran in this
    # unit test (`resolved=None` on `apply()` above), so every spec entity falls
    # through its own CARRY half.
    entities_for_gate = turn_runtime._entities_for(spec, [])

    captured: list[tuple[str, dict]] = []

    def call(name: str, args: dict) -> dict:
        captured.append((name, args))
        return {"content": [{"type": "text", "text": "{}"}]}

    payload = {
        "gate": {"compatible_entities": entities_for_gate},
        "tier_gate": None,
        "ctx": {
            "parse": {"output": lane_out},
            "contact": {"id": "samantha-contact"},
            "access": {"attributes": ["sales_orders.outstanding"]},
        },
    }
    result = run_fetch(payload, services=FetchServices(mcp_call=call))

    called_tools = [name for name, _args in captured]
    assert "crm_outstanding_report" not in called_tools, (
        f"an incoming-domain verdict under an old order-domain offer must never call "
        f"crm_outstanding_report: {called_tools}"
    )
    assert result.get("fetch", {}).get("outstanding_report") is not True, (
        f"the fetch fragment must not be flagged as an outstanding report: {result.get('fetch')}"
    )


# --------------------------------------------------------------------------- #
# AC-S4-2 (T6): "got eta" - no entities, domain_in_message true, domain_hint incoming.
# --------------------------------------------------------------------------- #


def test_t6_domain_switch_drops_outstanding_offer() -> None:
    """Today: CARRY `nothing_answered` -> `_answer_pending`'s
    `answer_pending_not_an_answer` tail keeps the SAME pending, unmodified - the
    poisoned offer survives to be replayed by T7 to T9.

    Fixed (owner ruling, T6 half): a turn naming another domain of its own closes the
    offer. `state2.pending` is None and the turn is answered as an incoming ask over
    the carried focus (`state2.focus.domains == ["incoming"]` - already correct today
    off `apply()`'s own `domain_hint` fallback, kept here as the full AC).
    """
    from app.services.chatbot.turn.apply import apply

    state = _state_with_offer(products=[entity("SRTBF 11502", hint="product")])
    v = verdict(entities=[], domain_in_message=True, domain_hint="incoming")

    state2, plan = apply(state, v, build_policy())

    assert state2.pending is None, (
        f"'got eta' names another domain and must close the open outstanding offer: "
        f"pending is still {state2.pending!r}"
    )
    assert state2.focus.domains == ["incoming"], (
        f"the turn must be answered as an incoming ask: focus.domains is "
        f"{state2.focus.domains!r}"
    )


# --------------------------------------------------------------------------- #
# Guards named by the captain's test list - expected GREEN today and after the fix.
# --------------------------------------------------------------------------- #


def test_aside_keeps_outstanding_offer_open() -> None:
    """"ok thanks" - no domain, no entity. Owner hand pass 3's own rule
    (`answer_pending_not_an_answer`) is untouched by this slice: the offer stays open."""
    from app.services.chatbot.turn.apply import apply

    state = _state_with_offer()
    v = verdict(entities=[], domain_in_message=False, domain_hint=None)

    state2, plan = apply(state, v, build_policy())

    assert state2.pending is not None, (
        "an aside with no domain word must still leave the outstanding offer open"
    )
    assert plan.trace.decision == {"kind": CARRY, "why": "nothing_answered"}


def test_same_domain_refine_still_refines() -> None:
    """An entity, no domain word, `domain_hint` EQUAL to the offer's own domain
    ("order") still REFINES - the fix is a mismatch check, not a ban on refining."""
    v = verdict(
        entities=[entity("SRTWC1234", hint="product")],
        domain_in_message=False,
        domain_hint="order",
    )
    decision = decide(v, Focus(), OUTSTANDING_OFFER)

    assert decision.kind == REFINE, (
        f"a domain_hint equal to the offer's own domain must still REFINE: "
        f"got {decision.kind!r}/{decision.why!r}"
    )


def test_same_domain_null_domain_hint_still_refines() -> None:
    """The same guard with a NULL `domain_hint` (the ordinary case: most refining
    turns name no domain at all) - the mismatch check must not turn every refinement
    into a NEW_ASK by comparing None against the offer's domain."""
    v = verdict(
        entities=[entity("SRTWC1234", hint="product")],
        domain_in_message=False,
        domain_hint=None,
    )
    decision = decide(v, Focus(), OUTSTANDING_OFFER)

    assert decision.kind == REFINE, (
        f"a null domain_hint must still refine the open outstanding offer: "
        f"got {decision.kind!r}/{decision.why!r}"
    )


# --------------------------------------------------------------------------- #
# Fix lane round 2, S2 (reviewer pass at 4719a829): T5 - a product-list photo with
# per-line quantities and no domain word, sent while the outstanding offer is OPEN.
# Same shape as T7; slice 4 tells them apart only by the parser's own domain_hint, so
# the reading must be pinned for the two hints that keep narrowing (null, "order"),
# and the console case that grades the LIVE parser on it must exist.
# --------------------------------------------------------------------------- #


def _t5_verdict(domain_hint):
    return verdict(
        entities=[
            {**entity("SRTBF 11502", hint="product"), "quantity": 3},
            {**entity("SRTBF 11503", hint="product"), "quantity": 4},
        ],
        domain_in_message=False,
        domain_hint=domain_hint,
    )


def test_t5_photo_list_with_null_hint_refines_the_open_offer() -> None:
    decision = decide(_t5_verdict(None), Focus(), OUTSTANDING_OFFER)
    assert decision.kind == REFINE, (decision.kind, decision.why)


def test_t5_photo_list_with_order_hint_refines_the_open_offer() -> None:
    decision = decide(_t5_verdict("order"), Focus(), OUTSTANDING_OFFER)
    assert decision.kind == REFINE, (decision.kind, decision.why)


def test_console_cases_carry_t5_under_the_open_offer() -> None:
    """The live-parser half of S2: the Samantha console cases must send T5 AFTER an
    outstanding ask in the same case (so the offer is open), and grade the parser's
    own `domain_in_message` on it, so the prompt version is checked on T5 before its
    label moves."""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parent / "console_cases" / "2026-09-26-samantha.yaml"
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]
    t5 = [c for c in cases if str(c.get("name", "")).startswith("T5")]
    assert t5, [c.get("name") for c in cases]
    turns = t5[0]["turns"]
    assert "outstanding" in turns[0]["text"].lower(), turns[0]
    photo = turns[1]
    assert "\n" in photo["text"] and "outstanding" not in photo["text"].lower(), photo
    assert photo["expect"].get("parser", {}).get("domain_in_message") is False, photo
    assert "Incoming" in photo["expect"].get("reply_not_contains", []), photo


# --------------------------------------------------------------------------- #
# Fix lane round 3, B2 (reviewer pass round 2 at b219a730): AC-S4-2 and journey step
# 4 - "got eta" is answered as an incoming ask over the CARRIED focus. The
# `domain_switch` arm ran `_drop_question_subject`, which cleared the very products
# and customer the offer was about, so T6 called `crm_incoming_stock_list` with no
# product filter and replied "Could not find incoming.". The test above builds an
# offer with no filters, so it could not see this.
# --------------------------------------------------------------------------- #

_B2_CUSTOMER_UUID = "44444444-4444-4444-4444-444444444444"
_B2_WINDOW = {"mode": "range", "start": "2026-01-01", "end": "2026-09-26"}


def _offer_with_filters(kind: str) -> Pending:
    return Pending(
        kind=kind,
        expects=None,
        options=[],
        team=None,
        payload={
            "domain": "order",
            "filters": {
                "product_code": "SRTBF 11502",
                "product_codes": ["SRTBF 11502", "SRTBF 11503"],
                "customer_ids": [_B2_CUSTOMER_UUID],
                "scope": "both",
                "date_filter_start": _B2_WINDOW["start"],
                "date_filter_end": _B2_WINDOW["end"],
            },
        },
        asked_at_turn=1,
    )


def _settled_focus() -> Focus:
    """The focus as T5's refinement leaves it: the offer's own subject settled."""
    return Focus(
        products=[
            {"raw": c, "hint": "product", "canonical_code": c, "current_message": False}
            for c in ("SRTBF 11502", "SRTBF 11503")
        ],
        customers=[{"uuid": _B2_CUSTOMER_UUID, "hint": "customer", "current_message": False}],
        domains=["order"],
        document=["sales_order", "delivery_order"],
        status="outstanding",
        date_window=dict(_B2_WINDOW),
    )


def _assert_t6_keeps_the_subject(state2) -> None:
    assert state2.pending is None, state2.pending
    assert state2.focus.domains == ["incoming"], state2.focus.domains
    codes = [p.get("canonical_code") or p.get("raw") for p in state2.focus.products]
    assert codes == ["SRTBF 11502", "SRTBF 11503"], (
        f"'got eta' is asked about the products just sent - the domain switch must "
        f"keep them (AC-S4-2, journey step 4): {state2.focus.products!r}"
    )
    assert [c.get("uuid") for c in state2.focus.customers] == [_B2_CUSTOMER_UUID], (
        f"the customer is part of the standing subject too: {state2.focus.customers!r}"
    )
    # The dead question's own axes go with it.
    assert state2.focus.status is None, state2.focus.status
    assert state2.focus.document == [], state2.focus.document
    assert state2.focus.date_window is None, state2.focus.date_window


def test_b2_t6_domain_switch_keeps_the_carried_subject_on_the_scope_question() -> None:
    from app.services.chatbot.turn.apply import apply

    state = State(focus=_settled_focus(), pending=_offer_with_filters("outstanding_scope"), profile=Profile())
    v = verdict(entities=[], domain_in_message=True, domain_hint="incoming")
    state2, _plan = apply(state, v, build_policy())
    _assert_t6_keeps_the_subject(state2)


def test_b2_t6_domain_switch_keeps_the_carried_subject_on_the_detail_offer() -> None:
    from app.services.chatbot.turn.apply import apply

    state = State(focus=_settled_focus(), pending=_offer_with_filters("outstanding_detail"), profile=Profile())
    v = verdict(entities=[], domain_in_message=True, domain_hint="incoming")
    state2, _plan = apply(state, v, build_policy())
    _assert_t6_keeps_the_subject(state2)


def test_b2_t6_domain_switch_settles_the_offer_subject_when_focus_lost_it() -> None:
    """The reviewer's apply probe: the offer's filters carry the subject even when the
    focus it rode in on does not (an empty focus). The switch answers over the offer's
    own subject, never over nothing."""
    from app.services.chatbot.turn.apply import apply

    state = State(focus=Focus(), pending=_offer_with_filters("outstanding_scope"), profile=Profile())
    v = verdict(entities=[], domain_in_message=True, domain_hint="incoming")
    state2, _plan = apply(state, v, build_policy())
    _assert_t6_keeps_the_subject(state2)


def test_b2_new_ask_naming_its_own_entity_still_drops_the_old_subject() -> None:
    """Guard: R17 is untouched for a message that names its OWN subject - the old
    question's product and customer still die with it (reviewer N2 of an earlier
    round)."""
    from app.services.chatbot.turn.apply import apply

    state = State(focus=_settled_focus(), pending=_offer_with_filters("outstanding_scope"), profile=Profile())
    v = verdict(
        entities=[entity("M210-GM", hint="product")], domain_in_message=True, domain_hint="incoming"
    )
    state2, _plan = apply(state, v, build_policy())
    codes = [p.get("canonical_code") or p.get("raw") for p in state2.focus.products]
    assert "SRTBF 11502" not in codes, state2.focus.products
    assert state2.focus.customers == [], state2.focus.customers


class TestB2EngineT6SendsTheCarriedProductsToIncoming:
    """Engine level: an outstanding ask over two products arms the scope question,
    then "got eta" calls the incoming tool with BOTH product ids."""

    P1 = ("SRTBF11502", "aaaaaaaa-1111-aaaa-1111-aaaaaaaaaaaa")
    P2 = ("SRTBF11503", "aaaaaaaa-2222-aaaa-2222-aaaaaaaaaaaa")

    def test_got_eta_calls_incoming_with_both_products(self, session_factory, monkeypatch) -> None:
        from tests.chatbot.test_outstanding_lane import _qf, _run_turn, _seed_contact, _session_of

        _seed_contact(session_factory, variables={})
        matches = {
            code: {"uuid": uid, "entity_type": "product", "canonical_code": code}
            for code, uid in (self.P1, self.P2)
        }
        qf1 = _qf(
            order_status="outstanding",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
                for code, _ in (self.P1, self.P2)
            ],
        )
        _result, captured1 = _run_turn(
            session_factory, monkeypatch,
            qf=qf1, text_body="outstanding SRTBF11502 SRTBF11503", msg_id="ZZT-b2-t6-1",
            attributes=["sales_orders.outstanding"], matches=matches,
        )
        assert captured1 == [], f"the bare outstanding ask arms the scope question first: {captured1}"
        assert (_session_of(session_factory).get("open_question") or {}).get("kind") == "outstanding_scope"

        # INTENT-FRAMES: the live parser reads "got eta" after an answer as the same subject
        # (`continuation: true`, `entity_op: reuse`; gpt-5.4-mini, 4 Oct 2026).
        qf2 = _qf(
            domain_hint="incoming", intent_hint="check_incoming", entities=[], domain_in_message=True,
            continuation=True, entity_op="reuse",
        )
        result, captured2 = _run_turn(
            session_factory, monkeypatch,
            qf=qf2, text_body="got eta", msg_id="ZZT-b2-t6-2",
            attributes=["sales_orders.outstanding"], matches=matches,
            mcp_response={"has_result": False, "items": []},
        )
        incoming = [(n, a) for n, a in captured2 if n == "crm_incoming_stock_list"]
        assert incoming, f"'got eta' must be answered as an incoming ask: {captured2} / {result.reply!r}"
        _, args = incoming[0]
        assert set(args.get("product_ids") or []) == {self.P1[1], self.P2[1]}, (
            f"'got eta' is asked about the products just sent, never the whole book: {args}"
        )


# --------------------------------------------------------------------------- #
# Fix lane round 3, N9: the T5 console case grades the parser's `domain_hint`, and
# slice 4 passes on two values (null or "order"). The harness grades by equality, so
# it gains one form, `{one_of: [...]}`, and the case uses it.
# --------------------------------------------------------------------------- #


def _console_check_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "chatbot_console_check.py"
    spec = importlib.util.spec_from_file_location("chatbot_console_check_n9", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_n9_grader_takes_one_of_for_a_parser_key() -> None:
    grade = _console_check_module()._grade_parser
    wanted = {"domain_hint": {"one_of": [None, "order"]}}
    assert grade(wanted, {"domain_hint": None}) == []
    assert grade(wanted, {"domain_hint": "order"}) == []
    failures = grade(wanted, {"domain_hint": "incoming"})
    assert failures and "incoming" in failures[0], failures
    # Plain equality is unchanged.
    assert grade({"domain_in_message": False}, {"domain_in_message": False}) == []
    assert grade({"domain_in_message": False}, {"domain_in_message": True})


def test_n9_t5_case_grades_domain_hint_null_or_order() -> None:
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parent / "console_cases" / "2026-09-26-samantha.yaml"
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]
    t5 = [c for c in cases if str(c.get("name", "")).startswith("T5")][0]
    wanted = t5["turns"][1]["expect"]["parser"]
    assert wanted.get("domain_hint") == {"one_of": [None, "order"]}, wanted
