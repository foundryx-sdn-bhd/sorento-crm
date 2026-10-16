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
    state = _mem_state(seam, out)
    assert not state.get("changes")
    assert set(state) >= {"shown", "before", "changes", "after"}


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
    assert picked and all(not e.get("uuid") or e.get("resolved_fresh", True) for e in picked)
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
