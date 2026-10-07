"""QUOTED-REPLY slice 3: a quoted bot message is the turn's context, through `engine.run_turn`.

UAC: AC-QR-5 (trace facts), 7, 8 (the owner case), 9, 10, 11, 12, 13, 14, 15.
Plan: documentation/plans/chatbot/PLAN-quoted-reply-07oct.md (R2 to R7, E1 to E6).

What is REAL: the engine, the resolver, the presenter, the stored `chatbot.turns` rows this
file writes (the quoted turns). What is STUBBED: the parser verdict (each turn's verdict is
the one the live parser SHOULD emit, as in the round 9 console) and the stock tool.

Red-first: no engine seam, no `state` event and no `quoted_turn` fact exists yet.

Every contact id, message id and uuid is synthetic; the codes are catalogue codes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models.chat_history import ChatHistory
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.turn import pending as turn_pending

from tests.chatbot._r9_engine_console import TOO_BIG, EngineConsole, answer, product, reply, stock
from tests.chatbot._avail_mode_console import _seed
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access  # noqa: F401 - a pytest fixture

pytestmark = pytest.mark.usefixtures("stub_access")

A = "ELP3754"
B = "SRTWC287-S-150"
C = "SRTWC286-SH-UF"
E2_CODE = "SRTBF11841"
CONTACT = str(CONTACT_ID)


def ask_text(code: str) -> str:
    return f"How many units of {code}?"


def _console(session_factory, monkeypatch, stub_access, phone: str) -> EngineConsole:
    return EngineConsole(session_factory, monkeypatch, stub_access, phone=phone)


def _task_wire(c: EngineConsole, code: str, *, value: Any = None, status: str = "open") -> dict[str, Any]:
    return {
        "kind": "stock_qty",
        "domain": "inventory",
        "status": status,
        "opened_at_turn": 1,
        "touched_at_turn": 1,
        "slots": [{"key": c.uuid_of[code], "label": code, "value": value}],
        "not_checked": [],
    }


def _focus_wire(c: EngineConsole, code: str, tasks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "products": [{"raw": code, "canonical_code": code, "uuid": c.uuid_of[code], "hint": "product"}],
        "customers": [], "warehouse": [], "brands": [], "outstanding_brand_ids": [], "tier": [],
        "domains": ["inventory"], "document": [], "status": None, "sales_channel": None,
        "date_window": None, "sort": None, "set_page": None, "set_clarify": None,
        "top_selling": None, "required_ask": None, "low_stock": None,
        "tasks": tasks or [], "extra": {},
    }


def _store_turn(
    c: EngineConsole,
    *,
    reply_text: str,
    focus: dict[str, Any] | None,
    open_question: dict[str, Any] | None = None,
    turn_no: int = 1,
    age_s: int = 600,
    contact: str = CONTACT,
    is_test: bool = True,
    trace: list[dict[str, Any]] | None = None,
    state_extra: dict[str, Any] | None = None,
) -> ChatbotTurn:
    """An earlier engine turn whose reply the dealer later quotes. Old enough to sit before
    every turn the test runs (the shared transaction stamps `now()` at its own start)."""
    created = datetime.now(tz=timezone.utc) - timedelta(seconds=age_s)
    if trace is None:
        trace = [
            {"kind": "state", "focus": focus, "open_question": open_question, "turn_no": turn_no, **(state_extra or {})}
        ]
    db = c.session_factory()
    row = ChatbotTurn(
        id=str(uuid.uuid4()),
        contact_respond_id=contact,
        message_id=f"ZZT-qr-{uuid.uuid4().hex[:10]}",
        envelope={},
        is_test=is_test,
        status="done",
        response={"reply": {"text": reply_text}},
        trace=trace,
        created_at=created,
        finished_at=created + timedelta(seconds=4),
    )
    db.add(row)
    db.commit()
    return row


def _store_asked(c: EngineConsole, code: str, **kw: Any) -> ChatbotTurn:
    return _store_turn(c, reply_text=ask_text(code), focus=_focus_wire(c, code, [_task_wire(c, code)]), **kw)


def say_quoted(
    c: EngineConsole,
    message: str,
    v: dict[str, Any],
    *,
    quote: str | None,
    reply_id: str | None = None,
    source: str = "n8n",
) -> str:
    """`EngineConsole.say` with the WhatsApp quote on the envelope (`replyTo`)."""
    c._turns += 1
    c._next = v
    inner: dict[str, Any] = {
        "messageId": f"ZZT-qr-in-{uuid.uuid4().hex[:10]}",
        "contactId": CONTACT_ID,
        "channelId": "whatsapp",
        "traffic": "incoming",
        "message": {"type": "text", "text": message},
    }
    if quote is not None:
        inner["replyTo"] = {
            "id": reply_id or f"17800{uuid.uuid4().int % 10**11:011d}",
            "sender": {"source": source},
            "message": {"text": quote},
        }
    out = engine_mod.run_turn(
        _envelope(
            is_test=True,
            previous_conversation_state=c.state,
            message={
                "event_type": "message.received",
                "contact": {"id": CONTACT_ID},
                "message": inner,
            },
        ),
        session_factory=c.session_factory,
    )
    assert out.error is None, out.error
    c.state = out.session_patch
    text = (out.reply or {}).get("text") or ""
    c.turn_traces.append(c.traces[-1] if c.traces else None)
    c.transcript += [message, f"-> {text}"]
    return text


def _latest_engine_turn(c: EngineConsole, message_text: str) -> ChatbotTurn:
    db = c.session_factory()
    hits = []
    for row in db.query(ChatbotTurn).filter(ChatbotTurn.contact_respond_id == CONTACT).all():
        body = (((row.envelope or {}).get("message") or {}).get("message") or {}).get("message") or {}
        if isinstance(body, dict) and body.get("text") == message_text:
            hits.append(row)
    assert hits, f"no engine turn recorded for {message_text!r}"
    return max(hits, key=lambda r: r.finished_at or datetime.min.replace(tzinfo=timezone.utc))


def quoted_facts(c: EngineConsole, message_text: str) -> dict[str, Any]:
    row = _latest_engine_turn(c, message_text)
    received = next(e for e in row.trace if e.get("stage") == "received")
    return received["facts"].get("quoted_turn")


def _ctx_lines(block: str) -> list[str]:
    prefixes = ("Current subject:", "Open task:", "Open question:", "Pending:", "Open question options:")
    return [line for line in block.splitlines() if line.startswith(prefixes)]


def _open_task_labels(c: EngineConsole) -> list[str]:
    tasks = ((c.state or {}).get("focus") or {}).get("tasks") or []
    return [s["label"] for t in tasks if t.get("status") == "open" for s in t.get("slots", [])]


def _fill(qty: int, code: str | None, position: int = 1) -> dict[str, Any]:
    return reply(demand_qty=qty, open_question_answer=answer("fill", items=[(position, code, qty)]))


def _answered(code: str, qty: int) -> str:
    return f"{code} x {qty}: {TOO_BIG}"


def _live_c(c: EngineConsole) -> None:
    """The live open stock task: the dealer asked about C and the bot is waiting on a quantity."""
    assert c.say(f"check stock {C}", stock(product(C))) == ask_text(C)


# --------------------------------------------------------------------------- #
# AC-QR-8 (the owner case) and AC-QR-7 (the context is the quoted state)
# --------------------------------------------------------------------------- #


def test_ac_qr_8_owner_case_quote_a_then_b(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000401")
    _store_asked(c, A, turn_no=1, age_s=900)
    _store_asked(c, B, turn_no=2, age_s=800)
    _live_c(c)

    first = say_quoted(c, "1 unit", _fill(1, A), quote=ask_text(A))
    assert first == _answered(A, 1)
    assert B not in first and C not in first
    assert _open_task_labels(c) == [C], "the live stock task is still C, unanswered"

    second = say_quoted(c, "1 unit", _fill(1, B), quote=ask_text(B))
    assert second == _answered(B, 1)
    assert A not in second and C not in second
    assert _open_task_labels(c) == [C]
    assert ask_text(C) not in (first, second), "no reply re-asks C"


def test_ac_qr_7_parser_block_is_built_from_the_quoted_state(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000402")
    _store_asked(c, A, turn_no=1, age_s=900)
    _live_c(c)
    say_quoted(c, "1 unit", _fill(1, A), quote=ask_text(A))
    lines = _ctx_lines(c.last_block)
    assert any(line.startswith("Current subject:") and A in line for line in lines), lines
    assert any(A in line for line in lines if line.startswith("Open task:")), lines
    assert not any(C in line for line in lines), lines


# --------------------------------------------------------------------------- #
# AC-QR-5 and AC-QR-6 through the engine: the trace facts
# --------------------------------------------------------------------------- #


def test_ac_qr_6_trace_facts_when_resolved_by_text(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000403")
    stored = _store_asked(c, A, turn_no=1, age_s=900)
    _live_c(c)
    say_quoted(c, "1 unit", _fill(1, A), quote=ask_text(A))
    facts = quoted_facts(c, "1 unit")
    assert facts is not None, "the received record carries quoted_turn"
    assert facts["found"] is True
    assert facts["via"] == "text"
    assert facts["turn_id"] == stored.id
    assert facts["same_as_live"] is False


def test_ac_qr_5_trace_facts_when_resolved_by_id(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000404")
    stored = _store_asked(c, A, turn_no=1, age_s=900)
    db = c.session_factory()
    db.add(
        ChatHistory(
            channel="whatsapp",
            contact_id=CONTACT,
            phone_number="+60900000404",
            message="the quote as the client shows it",
            sent_at=datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(seconds=890),
            type="outgoing",
            message_id="1780000000009001",
            chatbot_turn_id=stored.id,
        )
    )
    db.commit()
    _live_c(c)
    say_quoted(c, "1 unit", _fill(1, A), quote="the quote as the client shows it", reply_id="1780000000009001")
    facts = quoted_facts(c, "1 unit")
    assert facts is not None
    assert (facts["found"], facts["via"], facts["turn_id"]) == (True, "id", stored.id)


# --------------------------------------------------------------------------- #
# AC-QR-9: a quoted message that asked nothing
# --------------------------------------------------------------------------- #


def test_ac_qr_9_quote_of_a_non_question_answers_about_the_quoted_product(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000405")
    c.codes.update(_seed(session_factory, [E2_CODE]))
    c.uuid_of = {code: pid for pid, code in c.codes.items()}
    e2_text = f"I have {E2_CODE}. What would you like me to know?"
    _store_turn(c, reply_text=e2_text, focus=_focus_wire(c, E2_CODE), turn_no=1, age_s=900)
    _live_c(c)
    c.tool_calls.clear()

    out = say_quoted(
        c,
        "have stock? or eta date?",
        reply(entity_op="reuse", domain_hint="inventory", intent_hint="check_stock"),
        quote=e2_text,
    )
    stock_calls = [args for name, args in c.tool_calls if name == "crm_inventory_stock_balance_list"]
    asked_ids = {pid for args in stock_calls for pid in (args.get("product_ids") or [])}
    assert c.uuid_of[E2_CODE] in asked_ids, "the stock tool was called for the quoted subject"
    assert c.uuid_of[C] not in asked_ids, "never for the live open task's product"
    # Lead decision (round 2, owner ruling pending): this turn's own open stock ask wins, as a
    # fresh ask does on main; the written task is the quoted product's only.
    assert _open_task_labels(c) == [E2_CODE]
    assert out == ask_text(E2_CODE)


# --------------------------------------------------------------------------- #
# AC-QR-10: already answered, the parser interprets
# --------------------------------------------------------------------------- #


def test_ac_qr_10_quoting_an_answered_ask_with_a_bare_number_revises_that_product(
    session_factory, monkeypatch, stub_access
):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000406")
    answered = _focus_wire(c, A, [_task_wire(c, A, value=10, status="answered")])
    _store_turn(c, reply_text=ask_text(A), focus=answered, turn_no=2, age_s=900)
    _live_c(c)
    out = say_quoted(c, "20", _fill(20, None), quote=ask_text(A))
    assert out == _answered(A, 20)
    assert C in _open_task_labels(c)


# --------------------------------------------------------------------------- #
# AC-QR-11: quoted == live changes nothing
# --------------------------------------------------------------------------- #


def test_ac_qr_11_quoting_the_live_question_is_byte_identical_to_not_quoting(
    session_factory, monkeypatch, stub_access
):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000407")
    _live_c(c)
    saved = c.state

    plain = c.say("10 unit", _fill(10, C))
    plain_lines = _ctx_lines(c.last_block)
    c.state = saved
    quoted = say_quoted(c, "10 unit", _fill(10, C), quote=ask_text(C))
    assert quoted == plain == _answered(C, 10)
    assert _ctx_lines(c.last_block) == plain_lines
    facts = quoted_facts(c, "10 unit")
    assert facts is not None and facts["found"] is True and facts["same_as_live"] is True


# --------------------------------------------------------------------------- #
# AC-QR-12: every fallback is main's behaviour, facts say found false
# --------------------------------------------------------------------------- #


def _assert_runs_as_main(c: EngineConsole, out: str) -> None:
    assert out == _answered(C, 1), "the live open task is answered, exactly as on main"
    facts = quoted_facts(c, "1 unit")
    assert facts is not None, "the received record carries quoted_turn even when nothing resolves"
    assert facts["found"] is False


def test_ac_qr_12_no_reply_to(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000408")
    _store_asked(c, A, age_s=900)
    _live_c(c)
    _assert_runs_as_main(c, say_quoted(c, "1 unit", _fill(1, C), quote=None))


def test_ac_qr_12_the_dealer_quoted_their_own_message(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000409")
    _store_asked(c, A, age_s=900)
    _live_c(c)
    out = say_quoted(c, "1 unit", _fill(1, C), quote=ask_text(A), source="contact")
    _assert_runs_as_main(c, out)


def test_ac_qr_12_nothing_resolves(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000410")
    _live_c(c)
    _assert_runs_as_main(c, say_quoted(c, "1 unit", _fill(1, C), quote="a bot sentence nobody stored"))


def test_ac_qr_12_the_quoted_turn_has_no_state_and_no_memory_event(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000411")
    _store_turn(c, reply_text=ask_text(A), focus=None, trace=[{"stage": "received", "facts": {}}], age_s=900)
    _live_c(c)
    _assert_runs_as_main(c, say_quoted(c, "1 unit", _fill(1, C), quote=ask_text(A)))


# --------------------------------------------------------------------------- #
# AC-QR-13: an expired escalation offer is not revived
# --------------------------------------------------------------------------- #


def test_ac_qr_13_an_old_escalation_offer_is_not_revived(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000412")
    offer_text = "Would you like me to bring in the warehouse team?"
    offer = turn_pending.to_wire(
        turn_pending.ask(
            "team_pick",
            [{"position": 1, "label": "Warehouse", "payload": {"team": "warehouse"}}],
            team="warehouse",
            asked_at_turn=1,
        )
    )
    assert offer["kind"] in turn_pending.ESCALATION_OFFER_KINDS
    _store_turn(c, reply_text=offer_text, focus=_focus_wire(c, A), open_question=offer, turn_no=1, age_s=900)
    for i in range(3):  # three later turns, so live turn_no - quoted turn_no >= 3
        _store_turn(c, reply_text=f"filler {i}", focus=_focus_wire(c, B), turn_no=2 + i, age_s=800 - i)
    _live_c(c)
    say_quoted(c, "yes", reply(is_affirmative=True), quote=offer_text)
    lines = _ctx_lines(c.last_block)
    assert not any(line.startswith(("Pending:", "Open question options:")) for line in lines), lines
    assert not any("team_pick" in line for line in lines), lines
    assert any(line.startswith("Current subject:") and A in line for line in lines), lines


# --------------------------------------------------------------------------- #
# AC-QR-14: nothing is loaded from the quoted turn but focus and the open question
# --------------------------------------------------------------------------- #


def test_ac_qr_14_access_levels_and_language_stay_the_live_contacts(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000413")
    _store_turn(
        c,
        reply_text=ask_text(A),
        focus=_focus_wire(c, A, [_task_wire(c, A)]),
        state_extra={"access_levels": ["finance_all"], "reply_language": "ms", "profile": {"tier": "vip"}},
        age_s=900,
    )
    _live_c(c)
    say_quoted(c, "1 unit", _fill(1, A), quote=ask_text(A))
    assert (c.state or {}).get("access_levels") != ["finance_all"]
    assert (c.state or {}).get("reply_language") == "en"


def test_ac_qr_14_a_turn_of_another_contact_is_never_used(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000414")
    _store_turn(c, reply_text=ask_text(A), focus=_focus_wire(c, A, [_task_wire(c, A)]), contact="900000099", age_s=900)
    _live_c(c)
    out = say_quoted(c, "1 unit", _fill(1, C), quote=ask_text(A))
    assert out == _answered(C, 1)
    facts = quoted_facts(c, "1 unit")
    assert facts is not None and facts["found"] is False


def test_ac_qr_14_a_live_world_turn_is_never_used_by_a_dry_run(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000415")
    _store_turn(c, reply_text=ask_text(A), focus=_focus_wire(c, A, [_task_wire(c, A)]), is_test=False, age_s=900)
    _live_c(c)
    out = say_quoted(c, "1 unit", _fill(1, C), quote=ask_text(A))
    assert out == _answered(C, 1)
    facts = quoted_facts(c, "1 unit")
    assert facts is not None and facts["found"] is False


# --------------------------------------------------------------------------- #
# AC-QR-15: every turn records a `state` event
# --------------------------------------------------------------------------- #


def test_ac_qr_15_every_turn_trace_carries_one_state_event(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000416")
    _live_c(c)
    row = _latest_engine_turn(c, f"check stock {C}")
    events = [e for e in row.trace if e.get("kind") == "state"]
    assert len(events) == 1, [e.get("kind") or e.get("stage") for e in row.trace]
    event = events[0]
    assert event["focus"] == c.state["focus"]
    assert event["open_question"] == c.state["open_question"]
    assert isinstance(event["turn_no"], int)
