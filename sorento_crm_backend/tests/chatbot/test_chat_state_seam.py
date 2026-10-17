"""CHAT-STATE S4 (red): the engine seam, the carry skips, the trace log and R14.

UAC C5, C6, C9, D1, D2; card R2, R6, R9, R14, example 5; plan test list S4 items 1-7.

Driven the way the repo's other $0 engine tests drive a turn: a dry-run envelope whose
`mock_reformulator_output` is the parser's emission (no LLM, parser bypassed), the
previous turn's `session_patch` handed back as `previous_conversation_state`, the MCP tool
call stubbed so the tool name and args of every turn are on record. The state path is on
for a bypassed emission, so an emission that carries `state_changes` reaches the seam.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import turn_runtime
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.turn.state import Focus, State, focus_to_wire

from tests.chatbot._r9_engine_console import OWNER_FAMILY, _seed_products
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access  # noqa: F401
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _link_contact_company
from tests.chatbot.test_rearch_s3_roster_from_resolver import _seed_contact

STOCK = "crm_inventory_stock_balance_list"
INCOMING_TOOLS = ("crm_incoming_stock_list", "crm_incoming_stock_by_product", "crm_incoming_stock_shipments")
EXTRA_CODES = ["SRT1", "SRT391-GM"]
CUSTOMER_UUID = str(uuid.uuid4())


def _op(op: str, axis: str | None = None, position: int | None = None) -> dict[str, Any]:
    return {"op": op, "axis": axis, "position": position}


def _product(code: str) -> dict[str, Any]:
    return entity(code, "product")


class Seam:
    """One contact, turn by turn, through `engine.run_turn` with the parser bypassed."""

    def __init__(self, session_factory, monkeypatch, stub_access) -> None:
        self.session_factory = session_factory
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.turn_calls: list[list[tuple[str, dict[str, Any]]]] = []
        self.applies: list[list[Any]] = []
        self.traces: list[Any] = []
        self.results: list[Any] = []
        self.state: dict[str, Any] | None = None
        _seed_contact(session_factory, phone="+60000000019")
        _link_contact_company(session_factory, company_id=SORENTO)
        self.codes = _seed_products(session_factory, OWNER_FAMILY + EXTRA_CODES)
        self.uuid_of = {code: pid for pid, code in self.codes.items()}
        stub_access()
        monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: True)

        def fake_call_tool(client, name: str, args: dict[str, Any]) -> str:
            self.tool_calls.append((name, args))
            return json.dumps({"data": [], "pagination": {"total": 0, "page": 1, "limit": 50}, "answers": []})

        from app.services.ai_assistant_service import MCPRuntimeClient

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test"
            )

        def never_parse(config, user_block):  # the emission is bypassed: a real call is a harness bug
            raise AssertionError("the parser was called on a bypassed turn")

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", never_parse)
        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
        real_apply = engine_mod.turn_apply

        def traced_apply(*args: Any, **kwargs: Any):
            state_out, plan = real_apply(*args, **kwargs)
            self.traces.append(plan.trace)
            self.applies.append(state_out)
            return state_out, plan

        monkeypatch.setattr(engine_mod, "turn_apply", traced_apply)

    def say(self, message: str, v: dict[str, Any], *, previous: dict[str, Any] | None = None) -> Any:
        start_calls, start_applies, start_traces = len(self.tool_calls), len(self.applies), len(self.traces)
        out = engine_mod.run_turn(
            _envelope(
                is_test=True,
                mock_reformulator_output=v,
                previous_conversation_state=previous if previous is not None else self.state,
                message={
                    "event_type": "message.received",
                    "contact": {"id": CONTACT_ID},
                    "message": {
                        "messageId": f"ZZT-s4-{uuid.uuid4().hex[:10]}",
                        "contactId": CONTACT_ID,
                        "channelId": "whatsapp",
                        "traffic": "incoming",
                        "message": {"type": "text", "text": message},
                    },
                },
            ),
            session_factory=self.session_factory,
        )
        assert out.error is None, out.error
        self.state = out.session_patch
        self.turn_calls.append(self.tool_calls[start_calls:])
        self.last_applies = self.applies[start_applies:]
        self.last_traces = self.traces[start_traces:]
        self.results.append(out)
        return out

    @property
    def rules_fired(self) -> list[str]:
        return list(self.last_traces[-1].rules_fired) if self.last_traces else []

    def trace_rows(self, out: Any) -> list[dict[str, Any]]:
        row = self.session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == out.turn_id).first()
        return list(row.trace or [])

    def memory(self, out: Any) -> dict[str, Any]:
        events = [r for r in self.trace_rows(out) if r.get("kind") == "memory"]
        assert events, "no memory trace event"
        return events[-1]

    def stock_ids(self, calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
        ids: list[str] = []
        for name, args in calls:
            if name == STOCK:
                ids += list(args.get("product_ids") or [])
        return ids


@pytest.fixture()
def seam(session_factory, monkeypatch, stub_access):
    return Seam(session_factory, monkeypatch, stub_access)


def _emit(**overrides: Any) -> dict[str, Any]:
    """`verdict` plus None for every key the schema declares that the helper leaves out, so
    the harness emission passes `parser.assert_emission` like a real model answer."""
    out = verdict(**overrides)
    for key in parser_mod.DECLARED_KEYS:
        out.setdefault(key, None)
    return out


def _stock(*codes: str, **extra: Any) -> dict[str, Any]:
    return _emit(
        domain_hint="inventory", intent_hint="check_stock",
        entities=[_product(c) for c in codes], **extra,
    )




def _held_customer_state(*, window: dict[str, str] | None = None) -> dict[str, Any]:
    row = {
        "raw": "Customer X", "name": "Customer X", "canonical_code": "C0001",
        "hint": "customer", "uuid": CUSTOMER_UUID, "current_message": False,
    }
    return {
        "focus": focus_to_wire(Focus(customers=[row], domains=["order"], date_window=window)),
        "open_question": None,
    }


def _roster_state(seam: Seam, codes: list[str]) -> dict[str, Any]:
    from app.services.chatbot.turn import pending as pending_mod

    options = [
        {
            "position": i, "label": code, "code": code, "name": None, "entity_type": "product",
            "uuid": seam.uuid_of[code], "uuids": [seam.uuid_of[code]], "payload": {},
        }
        for i, code in enumerate(codes, 1)
    ]
    pend = pending_mod.ask("product_pick", options, asked_at_turn=2, payload={"domain": "inventory"})
    return {"focus": focus_to_wire(Focus(domains=["inventory"])), "open_question": pending_mod.to_wire(pend)}



def _mem_state(seam: Seam, out: Any) -> dict[str, Any]:
    state = seam.memory(out).get("state")
    assert isinstance(state, dict), f"the memory trace event carries no `state` log: {sorted(seam.memory(out))}"
    return state


def _held_srt1(seam: Seam) -> dict[str, Any]:
    """Turn 1: a stock ask for SRT1, left on the plain (no state_changes) path; returns the
    session it leaves behind."""
    seam.say("stock SRT1", _stock("SRT1"))
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of["SRT1"]]
    return seam.state


def _no_reset(seam: Seam) -> None:
    bad = [r for r in seam.rules_fired if r.startswith("new_ask_drops_") or "reset" in r]
    assert bad == [], bad


# --------------------------------------------------------------------------- #
# S4 item 1 (C6): an emission without state_changes runs exactly as on main
# --------------------------------------------------------------------------- #


def test_an_emission_without_state_changes_runs_as_on_main_and_records_no_list(seam):
    """C6. Rules, tool args and the stored focus are what main measured at 66bc3b6cf; the
    only new thing is the `state` log on the memory event (D1), which records no change and
    no list."""
    _held_srt1(seam)
    assert seam.rules_fired == ["replace_same_axis", "domains_from_asks", "focus_settles_product"]

    out = seam.say(
        "eta", _emit(domain_hint="inventory", intent_hint="check_stock")
    )
    assert seam.rules_fired == ["reuse_alive", "domains_from_asks"]
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of["SRT1"]]
    assert seam.state["focus"].get("last_list") is None
    assert "state" not in seam.memory(out), "a mock emission without state_changes is not on the state path"


# --------------------------------------------------------------------------- #
# S4 item 2 (C5, R2): [keep] after a held product, code-less domain ask
# --------------------------------------------------------------------------- #


def test_keep_with_an_inventory_ask_stocks_the_held_product_and_resets_nothing(seam):
    _held_srt1(seam)
    out = seam.say(
        "stock?",
        _emit(domain_hint="inventory", intent_hint="check_stock", domain_in_message=True,
              state_changes=[_op("keep")]),
    )
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of["SRT1"]]
    _no_reset(seam)
    state = _mem_state(seam, out)
    assert [c["op"] for c in state["changes"]] == ["keep"]
    assert state["after"]["subject"]["product"] == ["SRT1"]
    assert not state.get("rejected")


# --------------------------------------------------------------------------- #
# S4 item 3 (C5, R9): the parser's pick wins over the engine's own reading
# --------------------------------------------------------------------------- #


def test_a_bare_2_with_the_parsers_pick_of_position_1_answers_option_1(seam):
    """The parser's change says position 1; with the state path on the engine does not
    re-read the bare number (`engine_pick`), so option 1 is answered, not option 2."""
    prev = _roster_state(seam, OWNER_FAMILY[:3])
    out = seam.say(
        "2",
        _emit(
            domain_hint="inventory", intent_hint="check_stock", reference_positions=[1],
            open_question_answer={"mode": "pick", "picked": [1], "items": [], "qty_for_all": None},
            state_changes=[_op("pick", position=1)],
        ),
        previous=prev,
    )
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of[OWNER_FAMILY[0]]]
    kinds = [r.get("kind") for r in seam.trace_rows(out)]
    assert "engine_pick" not in kinds, kinds
    assert [c["op"] for c in _mem_state(seam, out)["changes"]] == ["pick"]


# --------------------------------------------------------------------------- #
# S4 item 4 (C5): the seam is idempotent across APPLY passes / replays of one input
# --------------------------------------------------------------------------- #


def test_the_same_turn_input_gives_the_same_focus_every_pass(seam):
    prev = _roster_state(seam, OWNER_FAMILY[:3])
    emission = _emit(
        domain_hint="inventory", intent_hint="check_stock", reference_positions=[1],
        open_question_answer={"mode": "pick", "picked": [1], "items": [], "qty_for_all": None},
        state_changes=[_op("pick", position=1)],
    )
    first = seam.say("2", emission, previous=prev)
    first_focus, first_passes = seam.state["focus"], [focus_to_wire(st.focus) for st in seam.last_applies]
    second = seam.say("2", emission, previous=prev)
    assert seam.state["focus"] == first_focus
    assert [focus_to_wire(st.focus) for st in seam.last_applies] == first_passes
    # every APPLY pass inside one turn lands on the same focus as the first
    assert all(p == first_passes[0] for p in first_passes)
    # the seam was reached (not a silent no-op): both runs logged the same change and result
    a, b = _mem_state(seam, first), _mem_state(seam, second)
    assert a["changes"] == b["changes"] and a["after"] == b["after"]


# --------------------------------------------------------------------------- #
# S4 item 5 (C4): an invalid change is today's path, with the reason on the log
# --------------------------------------------------------------------------- #


def test_an_invalid_change_runs_todays_path_and_logs_why(seam):
    """`set channel` while the parse carries no channel is invalid (R1)."""
    _held_srt1(seam)
    out = seam.say(
        "eta",
        _emit(domain_hint="inventory", intent_hint="check_stock",
              state_changes=[_op("set", axis="channel")]),
    )
    assert seam.rules_fired == ["reuse_alive", "domains_from_asks"]
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of["SRT1"]]
    rejected = _mem_state(seam, out).get("rejected")
    assert rejected, "an invalid change must be on the memory event as state.rejected"


# --------------------------------------------------------------------------- #
# S4 item 6 (D1, D2): the memory event carries shown / before / changes / after
# --------------------------------------------------------------------------- #


def test_the_memory_event_carries_the_state_log_and_before_is_what_was_shown(seam):
    from app.services.chatbot.turn import chat_state
    from app.services.chatbot.turn.state import focus_from_wire

    prev = _held_srt1(seam)
    changes = [_op("keep")]
    out = seam.say(
        "stock?",
        _emit(domain_hint="inventory", intent_hint="check_stock", domain_in_message=True,
              state_changes=changes),
    )
    state = _mem_state(seam, out)
    assert set(state) >= {"shown", "before", "changes", "after"}
    assert state["changes"] == changes
    # D2: before is the view of the STORED state the turn started from, and shown is its render
    stored = State(focus=focus_from_wire(prev["focus"]))
    expected = json.loads(json.dumps(chat_state.view(stored, prev.get("open_question"))))
    assert state["before"] == expected
    assert state["before"]["subject"]["product"] == ["SRT1"]
    assert state["shown"] == chat_state.render(expected)
    assert state["after"]["subject"]["product"] == ["SRT1"]


def test_a_turn_that_showed_a_list_records_it_in_the_stored_focus_when_the_state_path_is_on(seam):
    out = seam.say("check stock STWC2867", _stock("STWC2867", state_changes=[_op("new_subject")]), previous={})
    options = (seam.state["open_question"] or {}).get("options") or []
    assert options, "the miss must offer a did-you-mean list for this test to mean anything"
    last = seam.state["focus"]["last_list"]
    assert last, "focus.last_list is not written after a turn that showed a list"
    assert [(i["position"], i["code"]) for i in last] == [(o["position"], o["code"]) for o in options]
    assert _mem_state(seam, out)["after"]["last_list"]


# --------------------------------------------------------------------------- #
# S4b (C9, R14 rewritten, owner 16 Oct ~22:45): the PARSER says which held slot
# applies (keep / skip / clear); no per-domain table, no word rule.
# --------------------------------------------------------------------------- #

HELD_WINDOW = {"start": "2026-10-01", "end": "2026-10-07"}


def _incoming_ask(*slots: dict[str, Any]) -> dict[str, Any]:
    return _emit(
        domain_hint="incoming", intent_hint="check_incoming", domain_in_message=True,
        entities=[_product("SRT391-GM")], state_changes=[_op("new_subject"), *slots],
    )


def _order_ask(*changes: dict[str, Any]) -> dict[str, Any]:
    return _emit(
        domain_hint="order", intent_hint="check_order", domain_in_message=True,
        state_changes=list(changes),
    )


def _blob(seam: Seam) -> str:
    return json.dumps(seam.turn_calls[-1], default=str)


def _held_codes(seam: Seam) -> list[Any]:
    return [c.get("canonical_code") for c in seam.state["focus"]["customers"]]


def test_s4b_skip_customer_and_period_keep_them_out_of_the_args_and_in_the_stored_focus(seam):
    prev = _held_customer_state(window=HELD_WINDOW)
    out = seam.say(
        "SRT391-GM eta", _incoming_ask(_op("skip", "customer"), _op("skip", "period")), previous=prev
    )
    calls = seam.turn_calls[-1]
    incoming = [(n, a) for n, a in calls if n in INCOMING_TOOLS]
    assert incoming and any(seam.uuid_of["SRT391-GM"] in a.get("product_ids", []) for _, a in incoming)
    blob = _blob(seam)
    assert CUSTOMER_UUID not in blob and "Customer X" not in blob
    assert "2026-10-01" not in blob and "2026-10-07" not in blob
    state = _mem_state(seam, out)
    assert not state.get("rejected"), state.get("rejected")
    assert _held_codes(seam) == ["C0001"], f"skip must leave it held; rules {seam.rules_fired}"
    assert seam.state["focus"]["date_window"] == HELD_WINDOW


def test_s4b_a_following_keep_customer_applies_the_customer_to_the_order_ask(seam):
    prev = _held_customer_state(window=HELD_WINDOW)
    seam.say("SRT391-GM eta", _incoming_ask(_op("skip", "customer")), previous=prev)
    out = seam.say("delivery?", _order_ask(_op("switch_domain"), _op("keep", "customer")))
    reply = (out.reply or {}).get("text") or ""
    assert CUSTOMER_UUID in _blob(seam) or "Customer X" in reply, (
        f"the customer was not applied to the order ask: {reply!r}"
    )


def test_s4b_clear_customer_drops_it_from_the_stored_focus(seam):
    prev = _held_customer_state(window=HELD_WINDOW)
    seam.say("delivery?", _order_ask(_op("switch_domain"), _op("clear", "customer")), previous=prev)
    assert _held_codes(seam) == []
    assert CUSTOMER_UUID not in _blob(seam)


def test_s4b_clear_period_drops_it_from_the_stored_focus(seam):
    prev = _held_customer_state(window=HELD_WINDOW)
    seam.say("SRT391-GM eta", _incoming_ask(_op("skip", "customer"), _op("clear", "period")), previous=prev)
    assert seam.state["focus"]["date_window"] is None
    assert _held_codes(seam) == ["C0001"]


def test_s4b_keep_period_applies_the_held_window_to_the_order_ask(seam):
    prev = _held_customer_state(window=HELD_WINDOW)
    out = seam.say(
        "delivery?", _order_ask(_op("keep"), _op("keep", "period")), previous=prev
    )
    assert "2026-10-01" in _blob(seam), (_blob(seam), (out.reply or {}).get("text"))
    assert not _mem_state(seam, out).get("rejected")


def test_s4b_no_statement_about_a_held_slot_applies_it_as_on_main(seam):
    """Regression: [keep] says nothing about the customer, so the order ask names it."""
    prev = _held_customer_state(window=HELD_WINDOW)
    out = seam.say("delivery?", _order_ask(_op("keep")), previous=prev)
    assert CUSTOMER_UUID in _blob(seam) or "Customer X" in ((out.reply or {}).get("text") or "")
    assert _held_codes(seam) == ["C0001"]


def test_s4b_the_engine_does_not_consult_domain_narrowing_for_the_customer(seam):
    """`order` takes a customer per `chatbot_domains.narrowing`; the parser's skip still wins."""
    prev = _held_customer_state(window=HELD_WINDOW)
    out = seam.say("delivery?", _order_ask(_op("keep"), _op("skip", "customer")), previous=prev)
    reply = (out.reply or {}).get("text") or ""
    assert CUSTOMER_UUID not in _blob(seam) and "Customer X" not in reply, reply
    assert _held_codes(seam) == ["C0001"]


# --------------------------------------------------------------------------- #
# Security LOW: a stored last-list pick is re-resolved, never trusted by its stored uuid
# --------------------------------------------------------------------------- #


def _stored_list_state(seam: Seam) -> dict[str, Any]:
    rows = [
        {"position": 1, "label": "SRT1", "code": "SRT1", "entity_type": "product",
         "uuid": seam.uuid_of["SRT1"], "uuids": [seam.uuid_of["SRT1"]]},
        {"position": 2, "label": "SRT391-GM", "code": "SRT391-GM", "entity_type": "product",
         "uuid": seam.uuid_of["SRT391-GM"], "uuids": [seam.uuid_of["SRT391-GM"]]},
    ]
    return {"focus": focus_to_wire(Focus(domains=["inventory"], last_list=rows)), "open_question": None}


def _drop_company_scope(seam: Seam) -> None:
    from sqlalchemy import text

    db = seam.session_factory()
    db.execute(
        text("DELETE FROM respond_contact_companies WHERE respond_contact_id IN "
             "(SELECT id FROM respond_contacts WHERE respond_io_id = :c)"),
        {"c": str(CONTACT_ID)},
    )
    db.commit()


def test_a_stored_list_pick_after_the_scope_changed_does_not_fetch_the_out_of_scope_product(seam):
    prev = _stored_list_state(seam)
    _drop_company_scope(seam)
    out = seam.say(
        "1",
        _emit(domain_hint="inventory", intent_hint="check_stock", reference_positions=[1],
              state_changes=[_op("pick", position=1)]),
        previous=prev,
    )
    assert seam.stock_ids(seam.turn_calls[-1]) == [], "the stored uuid reached a tool call"
    assert seam.uuid_of["SRT1"] not in json.dumps(seam.turn_calls[-1], default=str)
    picked = [e for e in out.ctx["parse"]["output"]["entities"] if e.get("canonical_code") == "SRT1"]
    assert picked, "the pick must still inject the code as an entity"
    assert all(not e.get("uuid") and not e.get("uuids") for e in picked), picked


def test_a_stored_list_pick_in_scope_resolves_the_code_fresh(seam):
    prev = _stored_list_state(seam)
    out = seam.say(
        "1",
        _emit(domain_hint="inventory", intent_hint="check_stock", reference_positions=[1],
              state_changes=[_op("pick", position=1)]),
        previous=prev,
    )
    picked = [e for e in out.ctx["parse"]["output"]["entities"] if e.get("canonical_code") == "SRT1"]
    assert picked and all(not e.get("uuid") for e in picked)
    assert [e for e in picked if e.get("uuids")] == [], "the stored uuids must not ride along"
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of["SRT1"]], "still found, by a fresh resolve"


# --------------------------------------------------------------------------- #
# Reviewer findings (round 4), engine level
# --------------------------------------------------------------------------- #


def test_b1_close_question_on_an_answered_stock_check_does_not_stop_the_incoming_ask(seam):
    from app.services.chatbot.turn import task as task_mod

    task = task_mod.Task(
        kind="stock_qty", domain="inventory", status=task_mod.ANSWERED,
        slots=(task_mod.Slot(seam.uuid_of["SRT1"], "SRT1", 5),),
    )
    row = {"raw": "SRT1", "canonical_code": "SRT1", "hint": "product", "uuid": seam.uuid_of["SRT1"],
           "current_message": False}
    prev = {"focus": focus_to_wire(Focus(products=[row], domains=["inventory"], tasks=(task,))), "open_question": None}
    seam.say(
        "eta",
        _emit(domain_hint="incoming", intent_hint="check_incoming", domain_in_message=True,
              state_changes=[_op("switch_domain"), _op("close_question")]),
        previous=prev,
    )
    incoming = [a for n, a in seam.turn_calls[-1] if n in INCOMING_TOOLS]
    assert incoming and seam.uuid_of["SRT1"] in incoming[0].get("product_ids", []), seam.turn_calls[-1]
    assert not any("reset" in r for r in seam.rules_fired), seam.rules_fired
    assert [p["canonical_code"] for p in seam.state["focus"]["products"]] == ["SRT1"]


def test_s1_a_rejected_change_that_shows_a_new_list_still_records_it_and_a_pick_uses_it(seam):
    for bad in ([_op("bogus")], [], None):
        v = _stock("STWC2867")
        v["state_changes"] = bad
        seam.say("check stock STWC2867", v, previous={})
        last = seam.state["focus"].get("last_list")
        assert last and len(last) == 2, (bad, last)
        assert [i["code"] for i in last] == ["SRTWC286-SH", "SRTWC286-SH-P"]


def test_s6_new_subject_on_an_empty_state_runs_exactly_as_the_same_emission_without_it(seam):
    def ranking(**extra):
        return _emit(domain_hint="sales", intent_hint="check_sales", domain_in_message=True,
                     status="top_selling", top_n=10, group_by="product", **extra)

    seam.say("top 10 hot selling", ranking(), previous={})
    plain = (seam.rules_fired, [n for n, _ in seam.turn_calls[-1]], seam.state["focus"])
    seam.say("top 10 hot selling", ranking(state_changes=[_op("new_subject")]), previous={})
    assert (seam.rules_fired, [n for n, _ in seam.turn_calls[-1]], seam.state["focus"]) == plain


# --------------------------------------------------------------------------- #
# Round 5: a hand-over offer is answered, a picked option wins, a replaced product stays gone
# --------------------------------------------------------------------------- #


def _offer_state() -> dict[str, Any]:
    from app.services.chatbot.turn import pending as pending_mod

    offer = pending_mod.ask(
        "team_pick",
        [{"position": 1, "label": "customer service", "entity_type": "team", "payload": {"team": "customer_service"}}],
        team="customer_service", expects="yes_no", asked_at_turn=2,
        payload={"agent": None, "brand_code": None},
    )
    return {"focus": focus_to_wire(Focus(domains=["inventory"])), "open_question": pending_mod.to_wire(offer)}


def test_r5_a_yes_to_a_held_hand_over_offer_runs_the_hand_over_like_main(seam):
    """[answer] with an affirmative runs exactly the path the same reply takes with no state_changes."""
    base = dict(domain_hint=None, intent_hint=None, is_affirmative=True,
                escalation={"is_escalation_confirmation": True, "company_pick": None},
                open_question_answer={"mode": "yes", "picked": [], "items": [], "qty_for_all": None})
    seam.say("yes please", _emit(**base), previous=_offer_state())
    main_rules, main_kinds = seam.rules_fired, [a["kind"] for a in seam.results[-1].actions]
    assert "answer_pending_accept" in main_rules, main_rules
    seam.say("yes please", _emit(state_changes=[_op("answer")], **base), previous=_offer_state())
    assert seam.rules_fired == main_rules
    assert [a["kind"] for a in seam.results[-1].actions] == main_kinds


def _armed_set_page() -> dict[str, Any]:
    return {"set_key": {"shown": 0}, "other_brands": []}


def test_r5_a_declared_roster_pick_is_not_rewritten_by_the_set_count_reader(seam):
    prev = _roster_state(seam, OWNER_FAMILY[:3])
    prev["focus"]["set_page"] = _armed_set_page()
    prev["focus"]["products"] = [
        {"raw": c, "canonical_code": c, "hint": "product", "uuid": seam.uuid_of[c]} for c in OWNER_FAMILY[:3]
    ]
    seam.say(
        "2",
        _emit(domain_hint="inventory", intent_hint="check_stock", reference_positions=[2],
              open_question_answer={"mode": "pick", "picked": [2], "items": [], "qty_for_all": None},
              state_changes=[_op("pick", position=2)]),
        previous=prev,
    )
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of[OWNER_FAMILY[1]]], seam.rules_fired
    assert seam.results[-1].ctx["parse"]["output"].get("top_n") is None, "the pick was read as a row count"


def test_r5_an_answer_op_picking_a_roster_option_also_survives_the_set_count_reader(seam):
    prev = _roster_state(seam, OWNER_FAMILY[:3])
    prev["focus"]["set_page"] = _armed_set_page()
    prev["focus"]["products"] = [
        {"raw": c, "canonical_code": c, "hint": "product", "uuid": seam.uuid_of[c]} for c in OWNER_FAMILY[:3]
    ]
    seam.say(
        "3",
        _emit(domain_hint="inventory", intent_hint="check_stock", reference_positions=[3],
              open_question_answer={"mode": "pick", "picked": [3], "items": [], "qty_for_all": None},
              state_changes=[_op("answer")]),
        previous=prev,
    )
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of[OWNER_FAMILY[2]]], seam.rules_fired
    assert seam.results[-1].ctx["parse"]["output"].get("top_n") is None, "the pick was read as a row count"


def test_r5_a_replaced_product_is_not_brought_back_by_a_code_that_cannot_be_settled(seam):
    prev_turn = _held_srt1(seam)
    out = seam.say(
        "ZZQ9999 stock",
        _emit(domain_hint="inventory", intent_hint="check_stock", domain_in_message=True,
              entities=[_product("ZZQ9999")], state_changes=[_op("set", "product")]),
        previous=prev_turn,
    )
    assert seam.uuid_of["SRT1"] not in json.dumps(seam.turn_calls[-1], default=str), "the old product answered"
    assert "ZZQ9999" in ((out.reply or {}).get("text") or "").upper()


# --------------------------------------------------------------------------- #
# Round 6 (reviewer delta)
# --------------------------------------------------------------------------- #


def test_r6_a_mock_emission_without_the_state_changes_key_is_off_and_with_a_null_key_is_on(seam):
    off = seam.say("check stock STWC2867", _stock("STWC2867"), previous={})
    assert "state" not in seam.memory(off)
    assert seam.state["focus"].get("last_list") is None
    v = _stock("STWC2867")
    v["state_changes"] = None
    on = seam.say("check stock STWC2867", v, previous={})
    assert "state" in seam.memory(on)
    assert seam.state["focus"].get("last_list"), "the list shown is recorded on the state path"


def test_r6_b2_example_5_the_ask_runs_as_order_for_the_customer_with_the_held_period(seam):
    prev = _held_customer_state(window=HELD_WINDOW)
    seam.say(
        "SRT391-GM delivery?",
        _emit(domain_hint="incoming", intent_hint="check_incoming", domain_in_message=True,
              entities=[_product("SRT391-GM")], state_changes=[_op("keep"), _op("unclear", "customer")]),
        previous=prev,
    )
    assert seam.state["focus"]["domains"] == ["order"], seam.state["focus"]["domains"]
    assert "2026-10-01" in _blob(seam)
    assert CUSTOMER_UUID in _blob(seam)


def test_r6_the_hand_over_after_a_second_unclear_customer_is_the_ordinary_offer_not_a_period_question(seam, monkeypatch):
    from app.services.chatbot.turn import chat_state

    monkeypatch.setattr(chat_state, "KIND_DOMAIN", {})
    prev = _held_customer_state(window=HELD_WINDOW)
    seam.say("delivery?", _order_ask(_op("keep"), _op("unclear", "customer")), previous=prev)
    out = seam.say("delivery??", _order_ask(_op("keep"), _op("unclear", "customer")))
    reply = ((out.reply or {}).get("text") or "").lower()
    assert "which period" not in reply, reply
    assert "escalate" in reply or "customer service" in reply, reply


def test_r6_security_a_skipped_customer_is_not_trusted_by_its_stored_uuid_after_the_scope_changes(seam):
    prev = _held_customer_state(window=HELD_WINDOW)
    seam.say("SRT391-GM eta", _incoming_ask(_op("skip", "customer")), previous=prev)
    assert _held_codes(seam) == ["C0001"]
    _drop_company_scope(seam)
    seam.say("delivery?", _order_ask(_op("switch_domain"), _op("keep", "customer"), _op("keep", "period")))
    assert CUSTOMER_UUID not in _blob(seam), "the stored uuid reached a tool call after the scope changed"


def test_r6_nit_a_canned_lane_on_a_quoted_reply_keeps_the_live_focus_without_the_set_aside_customer():
    """The quoted turn skipped a customer (set aside); the live focus never had it. A canned
    lane keeps the WHOLE live focus, so the set-aside row must not be restored onto it."""
    from dataclasses import replace

    from app.services.chatbot.turn import chat_state
    from app.services.chatbot.turn import tail as tail_mod

    row = {"raw": "Customer X", "canonical_code": "C0001", "hint": "customer", "uuid": CUSTOMER_UUID}
    live = {"focus": Focus(domains=["order"]), "pending": None, "quoted_open": None}
    state = replace(
        State(focus=Focus(domains=["order"]), turn_no=4), held_aside={"customers": [row]}, quoted_live=live
    )
    carried = tail_mod.carry_live_state(state, False, live, non_answer=True)
    assert chat_state.restored(carried).focus.customers == []


# --------------------------------------------------------------------------- #
# S7: a carried customer the contact can no longer read is dropped on the state path
# --------------------------------------------------------------------------- #

COMPANY_A = SORENTO


def _seed_real_customer(seam: Seam, *, company_id: str = COMPANY_A, name: str = "Customer X") -> str:
    from app.models.order import Customer

    db = seam.session_factory()
    db.info["company_scope"] = frozenset({company_id})
    row = Customer(
        id=str(uuid.uuid4()), customer_code=f"ZZT-{uuid.uuid4().hex[:8]}"[:30],
        customer_name=name, company_id=company_id,
    )
    db.add(row)
    db.commit()
    return row.id


def _code_of(seam: Seam, customer_id: str) -> str:
    from sqlalchemy import text

    return seam.session_factory().execute(
        text("SELECT customer_code FROM customers WHERE id = :i"), {"i": customer_id}
    ).scalar()


def _held_real_customer_state(customer_id: str, code: str = "C0001") -> dict[str, Any]:
    row = {
        "raw": "Customer X", "name": "Customer X", "canonical_code": code,
        "hint": "customer", "uuid": customer_id, "current_message": False,
    }
    return {
        "focus": focus_to_wire(Focus(customers=[row], domains=["order"], date_window=dict(HELD_WINDOW))),
        "open_question": None,
    }


def _contact_pk(db) -> str:
    from sqlalchemy import text

    return db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)}
    ).scalar()


def _swap_company(seam: Seam, *, add_b: bool) -> None:
    from sqlalchemy import text

    from app.models.company import Company

    db = seam.session_factory()
    pk = _contact_pk(db)
    db.execute(text("DELETE FROM respond_contact_companies WHERE respond_contact_id = :p"), {"p": pk})
    if add_b:
        company_b = str(uuid.uuid4())
        db.add(Company(id=company_b, name="ZZT Other Co", code=f"ZZ{uuid.uuid4().hex[:6]}"))
        db.flush()
        db.execute(
            text("INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                 "VALUES (gen_random_uuid(), :p, :c)"),
            {"p": pk, "c": company_b},
        )
    db.commit()


def _order_keep(seam: Seam, prev: dict[str, Any]) -> Any:
    return seam.say("delivery?", _order_ask(_op("keep")), previous=prev)


def test_s7_a_held_customer_whose_company_link_was_removed_is_dropped(seam):
    cid = _seed_real_customer(seam)
    prev = _held_real_customer_state(cid, _code_of(seam, cid))
    _swap_company(seam, add_b=False)
    _order_keep(seam, prev)
    assert cid not in _blob(seam), "the stale customer uuid reached a tool call"
    assert seam.state["focus"]["customers"] == []


def test_s7_a_contact_moved_to_another_company_loses_the_held_customer_of_the_old_one(seam):
    cid = _seed_real_customer(seam)
    prev = _held_real_customer_state(cid, _code_of(seam, cid))
    _swap_company(seam, add_b=True)
    _order_keep(seam, prev)
    assert cid not in _blob(seam)
    assert seam.state["focus"]["customers"] == []


def test_s7_an_enforced_contact_loses_a_held_customer_whose_own_link_was_removed(seam):
    from app.models.access import RespondContactCustomer

    held = _seed_real_customer(seam)
    other = _seed_real_customer(seam, name="Customer Y")
    db = seam.session_factory()
    db.info["company_scope"] = frozenset({COMPANY_A})
    pk = _contact_pk(db)
    db.add(RespondContactCustomer(contact_id=pk, customer_id=other, company_id=COMPANY_A, is_primary=True))
    db.commit()  # the contact keeps its company and has a link to Customer Y only
    prev = _held_real_customer_state(held, _code_of(seam, held))
    _order_keep(seam, prev)
    assert held not in _blob(seam)
    assert [c for c in seam.state["focus"]["customers"] if c.get("uuid") == held] == []
    assert [c for c in seam.state["focus"]["customers"] if c.get("canonical_code") == _code_of(seam, held)] == []


def test_s7_a_still_readable_held_customer_is_kept_with_its_uuid_and_applied(seam):
    cid = _seed_real_customer(seam)
    prev = _held_real_customer_state(cid, _code_of(seam, cid))
    _order_keep(seam, prev)
    assert cid in _blob(seam), _blob(seam)[:300]
    assert [c.get("uuid") for c in seam.state["focus"]["customers"]] == [cid]


def test_s7_with_the_state_path_off_the_stale_customer_is_untouched_as_on_main(seam, monkeypatch):
    """Pin of the gate-off path: no state_changes key on a normal parse, behaviour as main."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.head import parser as parser_mod
    from tests.chatbot.test_engine import _envelope

    cid = _seed_real_customer(seam)
    prev = _held_real_customer_state(cid, _code_of(seam, cid))
    _swap_company(seam, add_b=False)
    emission = _order_ask()
    emission.pop("state_changes", None)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: emission)
    out = engine_mod.run_turn(
        _envelope(
            is_test=True, previous_conversation_state=prev,
            message={
                "event_type": "message.received", "contact": {"id": CONTACT_ID},
                "message": {"messageId": f"ZZT-s7-{uuid.uuid4().hex[:8]}", "contactId": CONTACT_ID,
                            "channelId": "whatsapp", "traffic": "incoming",
                            "message": {"type": "text", "text": "delivery?"}},
            },
        ),
        session_factory=seam.session_factory,
    )
    stored = [c.get("uuid") for c in (out.session_patch or {}).get("focus", {}).get("customers", [])]
    assert stored == [cid], "main keeps the stored row untouched"
    assert any(n.startswith("crm_order") for n, _ in seam.tool_calls)
    assert [r for r in seam.trace_rows(out) if r.get("kind") == "memory" and "state" in r] == []


def test_s7_b_carried_customer_shells_with_no_code_and_no_uuid_are_not_persisted(seam):
    shell = {"hint": "customer", "current_message": False}
    prev = {"focus": focus_to_wire(Focus(customers=[shell], domains=["order"])), "open_question": None}
    seam.say("delivery?", _order_ask(_op("keep")), previous=prev)
    assert [c for c in seam.state["focus"]["customers"] if not c.get("canonical_code") and not c.get("uuid")] == []


def _two_customer_state(good_id: str, good_code: str, bad: dict[str, Any]) -> dict[str, Any]:
    good = {"raw": "Customer X", "name": "Customer X", "canonical_code": good_code,
            "hint": "customer", "uuid": good_id, "current_message": False}
    return {
        "focus": focus_to_wire(Focus(customers=[bad, good], domains=["order"], date_window=dict(HELD_WINDOW))),
        "open_question": None,
    }


@pytest.mark.parametrize("field", ["uuid", "uuids"])
def test_s7_a_non_uuid_customer_key_does_not_break_the_turn_and_only_that_row_is_dropped(seam, field):
    gid = _seed_real_customer(seam)
    bad = {"raw": "Customer Z", "canonical_code": "CBAD", "hint": "customer", "current_message": False}
    bad[field] = "not-a-uuid" if field == "uuid" else ["not-a-uuid"]
    out = _order_keep(seam, _two_customer_state(gid, _code_of(seam, gid), bad))
    assert out.error is None, out.error
    assert ((out.reply or {}).get("text") or "").strip()
    stored = seam.state["focus"]["customers"]
    assert [c.get("canonical_code") for c in stored if c.get("canonical_code") == "CBAD"] == []
    assert [c.get("uuid") for c in stored] == [gid]


def test_s7_if_the_hygiene_read_raises_the_turn_completes_and_keyed_rows_are_dropped(seam, monkeypatch):
    from app.services.chatbot import engine as engine_mod

    gid = _seed_real_customer(seam)
    calls = {"n": 0}
    real = engine_mod.business_services.customer_scope

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scoped read failed")
        return real(*a, **k)

    monkeypatch.setattr(engine_mod.business_services, "customer_scope", flaky)
    bad = {"raw": "Customer Z", "canonical_code": "CBAD", "hint": "customer", "uuid": str(uuid.uuid4()),
           "current_message": False}
    out = _order_keep(seam, _two_customer_state(gid, _code_of(seam, gid), bad))
    assert out.error is None, out.error
    assert [c for c in seam.state["focus"]["customers"] if c.get("uuid")] == []


def test_r8_a_reference_to_the_held_subject_never_reaches_the_resolver_as_entities(seam):
    prev = _held_srt1(seam)
    stray = [
        entity("that one", "customer", canonical_code=None, hint_confident=False, confident=False,
               current_message=False),
        entity("that one", "form", canonical_code=None, hint_confident=False, confident=False,
               current_message=False),
    ]
    out = seam.say(
        "that one",
        _emit(domain_hint="inventory", intent_hint="check_stock", entities=stray, state_changes=[_op("keep")]),
        previous=prev,
    )
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of["SRT1"]], seam.turn_calls[-1]
    reply = (out.reply or {}).get("text") or ""
    assert "this (" not in reply and "that one" not in reply.lower(), reply
    assert seam.state["open_question"] is None or seam.state["open_question"].get("kind") != "pick_one"
    assert [e for e in out.ctx["parse"]["output"]["entities"] if e.get("raw") == "that one"] == []
    assert [p["canonical_code"] for p in seam.state["focus"]["products"]] == ["SRT1"]


def test_r9_a_stated_attachment_type_runs_the_file_lookup_for_the_held_products(seam):
    from tests.chatbot import test_product_attachment_picker_stamp as base

    base._seed_attachment_type(seam.session_factory, "Product Videos")
    prev = _held_srt1(seam)
    out = seam.say(
        "video",
        _emit(domain_hint="product_attachment", intent_hint="check_attachment", domain_in_message=True,
              entities=[entity("video", "attachment_type", canonical_code="Product Videos")],
              state_changes=[_op("switch_domain"), _op("keep")]),
        previous=prev,
    )
    files = [a for n, a in seam.turn_calls[-1] if n == "crm_master_product_attachments_list"]
    assert files, [n for n, _ in seam.turn_calls[-1]]
    assert seam.uuid_of["SRT1"] in json.dumps(files, default=str)
    assert "which kind" not in ((out.reply or {}).get("text") or "").lower()


def test_r11_a_pick_with_a_misspelt_code_beside_it_answers_the_picked_option_not_a_did_you_mean(seam):
    prev = _roster_state(seam, OWNER_FAMILY[:3])
    out = seam.say(
        "2 stwc2867",
        _emit(domain_hint="inventory", intent_hint="check_stock", reference_positions=[2],
              entities=[_product("STWC2867")],
              open_question_answer={"mode": "pick", "picked": [2], "items": [], "qty_for_all": None},
              state_changes=[_op("pick", position=2)]),
        previous=prev,
    )
    assert seam.stock_ids(seam.turn_calls[-1]) == [seam.uuid_of[OWNER_FAMILY[1]]], seam.rules_fired
    assert "did you mean" not in ((out.reply or {}).get("text") or "").lower()


def test_r11_set_product_with_an_unresolvable_code_leaves_the_replaced_product_out_of_the_stored_focus(seam):
    prev_turn = _held_srt1(seam)
    out = seam.say(
        "ZZQ9999 stock",
        _emit(domain_hint="inventory", intent_hint="check_stock", domain_in_message=True,
              entities=[_product("ZZQ9999")], state_changes=[_op("set", "product")]),
        previous=prev_turn,
    )
    assert seam.uuid_of["SRT1"] not in json.dumps(seam.turn_calls[-1], default=str)
    assert "ZZQ9999" in ((out.reply or {}).get("text") or "").upper()
    assert "SRT1" not in [p.get("canonical_code") for p in seam.state["focus"]["products"]]
