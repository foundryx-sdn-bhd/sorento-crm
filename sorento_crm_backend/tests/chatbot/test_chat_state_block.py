"""CHAT-STATE S1 (red): the state block the parser is shown, `view` + `render`.

`app/services/chatbot/turn/chat_state.py` does not exist yet, so every test here fails
with ModuleNotFoundError until S1 lands. The module is imported inside each test body so
pytest lists every test as its own failure, not one collection error.

Traces to UAC A1, B1, B2, B3 and plan test list S1. Asserts on structure (header, line
order, labels and codes present, no uuids, no "and N more"), not on full line wording.
"""
from __future__ import annotations

import copy
import re
from uuid import uuid4

from app.services.chatbot.turn import question as question_mod
from app.services.chatbot.turn.pending import ask
from app.services.chatbot.turn.state import Focus, State
from app.services.chatbot.turn.task import ANSWERED, OPEN, Slot, Task

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
PREFIXES = ("- subject", "- open question", "- last list", "- pending task")


def _product(code: str, **extra):
    return {"raw": code, "canonical_code": code, "hint": "product", "current_message": False, **extra}


def _render(state: State, open_question=None) -> str:
    from app.services.chatbot.turn import chat_state

    return chat_state.render(chat_state.view(state, open_question)) or ""


def _body(block: str) -> list[str]:
    """The four lines after the 'State:' header."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    assert "State:" in lines, lines
    at = lines.index("State:")
    return lines[at + 1 : at + 5]


def _roster_question(labels: list[str]):
    pending = ask(
        "customer_pick",
        [
            {"position": i, "label": lab, "code": f"C{i:04d}", "name": lab, "entity_type": "customer",
             "payload": {"uuid": str(uuid4())}}
            for i, lab in enumerate(labels, start=1)
        ],
        asked_at_turn=2,
    )
    return pending, question_mod.of_pending(pending)


def test_four_fixed_lines_in_fixed_order_with_none_when_empty():
    full = State(
        focus=Focus(
            products=[_product("SRT5733")],
            customers=[{"raw": "Customer X", "canonical_code": "C0001", "hint": "customer", "current_message": False}],
            last_list=[{"position": 1, "label": "SRT5733", "code": "SRT5733"}],
            tasks=(Task(kind="stock_qty", domain="inventory", status=OPEN, slots=(Slot(key=str(uuid4()), label="SRT5733"),)),),
        ),
        turn_no=3,
    )
    pending, oq = _roster_question(["Customer X", "Customer Y"])
    full.pending = pending
    lines = _body(_render(full, oq))
    assert len(lines) == 4
    for line, prefix in zip(lines, PREFIXES):
        assert line.startswith(prefix), (line, prefix)
        assert "none" not in line.lower(), line

    only_subject = State(focus=Focus(products=[_product("SRT5733")]), turn_no=1)
    lines = _body(_render(only_subject, None))
    assert len(lines) == 4
    for line, prefix in zip(lines, PREFIXES):
        assert line.startswith(prefix), (line, prefix)
    assert "SRT5733" in lines[0]
    for line in lines[1:]:
        assert "none" in line.lower(), line


def test_brand_tier_sort_channel_and_report_keys_print_when_set():
    state = State(
        focus=Focus(
            brands=["SORENTO"],
            tier=["dealer"],
            sort={"by": "eta", "dir": "desc"},
            sales_channel="project",
            date_window={"start": "2026-09-01", "end": "2026-09-30"},
            status="top_selling",
            sales_ranking_frame={"basis": "qty_sold", "top_n": 3},
        ),
        turn_no=4,
    )
    subject = _body(_render(state, None))[0].lower()
    for needle in ("sorento", "dealer", "eta", "desc", "project", "2026-09-01", "2026-09-30", "top_selling", "qty_sold"):
        assert needle in subject, (needle, subject)


def test_fifty_products_and_thirty_three_list_rows_print_in_full():
    codes = [f"SRT{n:04d}" for n in range(1, 51)]
    rows = [{"position": i, "label": f"MWB{i:04d}", "code": f"MWB{i:04d}"} for i in range(1, 34)]
    state = State(focus=Focus(products=[_product(c) for c in codes], last_list=rows), turn_no=5)
    block = _render(state, None)
    for code in codes:
        assert code in block, code
    for row in rows:
        assert row["code"] in block, row
    assert not re.search(r"and \d+ more", block, re.I), block
    assert "..." not in block and "…" not in block


def test_no_uuid_even_when_rows_and_options_carry_uuids():
    keys = [str(uuid4()) for _ in range(3)]
    pending, oq = _roster_question(["Customer X", "Customer Y"])
    state = State(
        focus=Focus(
            products=[_product("SRTWC286", uuid=keys[0], id=keys[0])],
            customers=[{"raw": "Customer X", "canonical_code": "C0001", "uuid": keys[1], "current_message": False}],
            last_list=[{"position": 1, "label": "SRTWC286", "code": "SRTWC286", "key": keys[2]}],
            tasks=(Task(kind="stock_qty", domain="inventory", status=OPEN, slots=(Slot(key=keys[0], label="SRTWC286"),)),),
        ),
        pending=pending,
        turn_no=6,
    )
    block = _render(state, oq)
    assert block
    assert not UUID_RE.search(block), block
    for key in keys:
        assert key not in block


def test_roster_that_is_question_and_last_list_prints_once_and_answered_task_prints_once():
    labels = ["Customer Alpha", "Customer Beta", "Customer Gamma"]
    pending, oq = _roster_question(labels)
    rows = [{"position": i, "label": lab, "code": f"C{i:04d}"} for i, lab in enumerate(labels, start=1)]
    state = State(focus=Focus(last_list=rows), pending=pending, turn_no=7)
    block = _render(state, oq)
    for lab in labels:
        assert block.count(lab) == 1, (lab, block)

    answered = Task(
        kind="stock_qty", domain="inventory", status=ANSWERED,
        slots=(Slot(key=str(uuid4()), label="MWB8051", value=10),),
    )
    state = State(focus=Focus(tasks=(answered,)), turn_no=8)
    oq = question_mod.open_question(None, (answered,))
    block = _render(state, oq)
    assert block.count("MWB8051") == 1, block


def test_equal_inputs_give_equal_text_and_empty_state_renders_nothing():
    from app.services.chatbot.turn import chat_state

    pending, oq = _roster_question(["Customer X", "Customer Y"])
    state = State(
        focus=Focus(products=[_product("SRT5733")], brands=["SORENTO"], last_list=[{"position": 1, "label": "SRT5733", "code": "SRT5733"}]),
        pending=pending,
        turn_no=9,
    )
    first = chat_state.render(chat_state.view(state, oq))
    second = chat_state.render(chat_state.view(copy.deepcopy(state), copy.deepcopy(oq)))
    assert first and first == second

    assert not chat_state.render(chat_state.view(State(focus=Focus()), None))


def test_r5_a_hand_over_offer_is_stated_with_its_kind_and_team_name():
    """S-OFFER: the parser can read a yes as accepting only if the line says it is an offer."""
    from app.services.chatbot.turn import chat_state, question as question_mod
    from app.services.chatbot.turn.pending import ask
    from app.services.chatbot.turn.state import Focus, State

    offer = ask(
        "team_pick",
        [{"position": 1, "label": "incoming stock", "entity_type": "team", "payload": {"team": "purchasing"}}],
        team="purchasing", expects="yes_no", asked_at_turn=2, payload={"agent": "agent-7f3a", "brand_code": None},
    )
    state = State(focus=Focus(), pending=offer, turn_no=3)
    text = chat_state.render(chat_state.view(state, question_mod.of_pending(offer)))
    line = next(l for l in text.splitlines() if l.startswith("- open question:"))
    assert "team_pick" in line, line
    assert "purchasing" in line.lower(), line
    assert any(w in line.lower() for w in ("hand", "offer")), line
    assert "agent-7f3a" not in text
