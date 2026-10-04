"""CUSTOMER-ASKS-REFER-ONLY (owner ruling 1 Oct 2026): Customer asks logs EVERY reply that
told the customer to refer to their salesman, and ONLY those.

One source of truth per kind of reply, never a read of the reply text:

* A stock ask's own line: the MCP presenter stamps `refers_to_salesman` on each answered
  `stock_availability` entry, derived from the tail it printed (`_AVAILABILITY_TAILS`).
  B3 `incoming` ("no stock at the moment, ETA ...") does not refer, so it is not logged.
* Every other refer reply: the backend composers print the line through ONE helper,
  `turn/refer.py`, which also marks the turn. The ask writer reads the mark.

Real turns reuse the S4/S5 harness (`LiveDealer`) and ESCALATION-CONTROL's (#1406) barred
contact turns, which wrote no row before this lane.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from app.models.stock_ask import StockAsk
from app.services import stock_ask_service
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import refer_asks
from app.services.chatbot.turn import refer
from app.services.chatbot.turn.task import REFER_TO_SALESMAN

from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_escalation_agent_carry import (
    _capture_next_assignee,
    _capture_sla,
    _seed_contact,
    _write_open_question,
    _yes_verdict,
)
from tests.chatbot.test_escalation_control import (
    _CUSTOMER_OFFER_CASE,  # noqa: F401 - the fallback harness below reads it
    _help_verdict,
    _incoming_miss,
    _make_dealer,
    _play_fallback,
    lane,  # noqa: F401 - fixture by name
    sent_text,
)
from tests.chatbot.test_stock_ask_notify import LiveDealer


BACKEND = Path(__file__).resolve().parents[2]
MCP_ROOT = BACKEND.parent / "sorento_crm_mcp"


def _presenters():
    if str(MCP_ROOT) not in sys.path:
        sys.path.append(str(MCP_ROOT))
    from sorento_crm_mcp import presenters

    return presenters


def _rows(session_factory) -> list[StockAsk]:
    db = session_factory()
    db.info["company_scope"] = None
    return db.query(StockAsk).order_by(StockAsk.product_code).all()


# --------------------------------------------------------------------------- #
# (1) The stock ask's own line: the presenter's flag, from the tail it printed
# --------------------------------------------------------------------------- #

_ANSWERED = [
    {"product_code": "ZZT-BIG", "requested_qty": 300, "branch": "too_big"},
    {"product_code": "ZZT-INS", "requested_qty": 50, "branch": "in_stock"},
    {"product_code": "ZZT-INC", "requested_qty": 150, "branch": "incoming", "eta": "19/10/2026"},
    {"product_code": "ZZT-NOI", "requested_qty": 20, "branch": "no_incoming"},
]


def _present(entries: list[dict]) -> dict:
    import json

    presenters = _presenters()
    raw = {
        "data": [],
        "stock_visibility": {"mode": "availability", "warehouse_codes": None, "source": "contact"},
        "stock_availability": [dict(e) for e in entries],
    }
    return json.loads(presenters.present_response("crm_inventory_stock_balance_list", json.dumps(raw)))


def test_the_presenter_stamps_each_answered_entry_from_the_tail_it_printed():
    presenters = _presenters()
    envelope = _present(_ANSWERED)
    stamped = {e["product_code"]: e["refers_to_salesman"] for e in envelope["stock_availability"]}
    for entry in _ANSWERED:
        line = presenters._availability_line(entry)
        assert stamped[entry["product_code"]] is line.endswith(REFER_TO_SALESMAN), line


def test_pin_only_b3_incoming_answers_without_the_refer_line():
    """Fails the day a tail changes: the set of refer branches is derived, so a reworded
    tail moves a branch in or out of Customer asks, and that has to be a decision."""
    envelope = _present(_ANSWERED)
    refers = {e["branch"] for e in envelope["stock_availability"] if e["refers_to_salesman"]}
    assert refers == {"too_big", "in_stock", "no_incoming"}


def test_an_entry_still_owing_a_quantity_is_not_stamped():
    envelope = _present([{"product_code": "ZZT-Q", "needs_quantity": True}])
    assert "refers_to_salesman" not in envelope["stock_availability"][0]


def test_the_writer_keeps_only_the_entries_that_refer():
    entries = [
        {**_ANSWERED[0], "refers_to_salesman": True},
        {**_ANSWERED[2], "refers_to_salesman": False},
        # An entry from an MCP that predates the flag is not guessed at.
        {**_ANSWERED[1]},
        {"product_code": "ZZT-R", "requested_qty": None, "branch": "referred", "refers_to_salesman": True},
    ]
    assert [e["product_code"] for e in stock_ask_service.refer_entries(entries)] == ["ZZT-BIG", "ZZT-R"]


# --------------------------------------------------------------------------- #
# (2) Every other refer reply: one helper prints the line and marks the turn
# --------------------------------------------------------------------------- #


def test_the_helper_prints_the_constant_and_marks_the_turn():
    with refer.tracking() as mark:
        assert mark.referred is False
        assert refer.after("Couldn't find ZZT1.") == f"Couldn't find ZZT1.\n\n{REFER_TO_SALESMAN}"
        assert mark.referred is True
    with refer.tracking() as mark:
        assert refer.sentence() == REFER_TO_SALESMAN
        assert refer.after("") == REFER_TO_SALESMAN
        assert refer.after("Couldn't find ZZT1.", sep=" ") == f"Couldn't find ZZT1. {REFER_TO_SALESMAN}"
        assert mark.referred is True


def test_a_nested_tracking_shares_the_outer_turns_mark():
    """`run_turn` -> `complete_turn` in one process is one turn."""
    with refer.tracking() as outer:
        with refer.tracking() as inner:
            refer.sentence()
        assert inner is outer and outer.referred is True


def test_the_helper_outside_a_turn_still_prints_the_line():
    assert refer.sentence() == REFER_TO_SALESMAN


def test_referred_entries_are_built_only_on_a_marked_turn_and_carry_the_flag():
    def build(referred: bool):
        return refer_asks.referred_entries(
            referred=referred,
            reply_text=f"Couldn't find ZZT9. {REFER_TO_SALESMAN}",
            envelopes=[{"domain": "inventory", "miss": ["ZZT9"]}],
            plan=None,
            pending_before=None,
            message_text="stock ZZT9",
            answered=[],
        )

    assert build(False) == []
    rows = build(True)
    assert [(r["product_code"], r["branch"], r["refers_to_salesman"]) for r in rows] == [
        ("ZZT9", "referred", True)
    ]


def test_consume_reads_the_mark_once_so_a_second_tail_cannot_log_twice():
    with refer.tracking():
        refer.sentence()
        assert refer.consume() is True
        assert refer.consume() is False
    assert refer.consume() is False  # outside a turn


def test_a_miss_outside_stock_and_incoming_is_not_a_product_code():
    """Review: another domain's `miss` is a customer name or an order number; a barred
    contact's order miss is named by what they typed, never "ACME SDN BHD" as a product."""
    rows = refer_asks.referred_entries(
        referred=True,
        reply_text=f"Couldn't find orders for ACME SDN BHD. {REFER_TO_SALESMAN}",
        envelopes=[{"domain": "order", "miss": ["ACME SDN BHD"], "unresolved": ["SO-123"]}],
        plan=None,
        pending_before=None,
        message_text="orders for ACME last month",
        answered=[],
    )
    assert [(r["product_code"], r["branch"]) for r in rows] == [("orders for ACME last month", "referred")]


def test_the_ask_writer_never_fails_the_turn(monkeypatch, caplog):
    def boom(**_kw):
        raise RuntimeError("bad envelope")

    monkeypatch.setattr(refer_asks, "referred_entries", boom)
    written: list = []
    monkeypatch.setattr(engine_mod, "_after_stock_ask_turn", lambda *a, **k: written.append(k))
    with refer.tracking():
        refer.sentence()
        engine_mod._record_customer_asks(
            None,
            turn_id="ZZT-turn",
            contact_respond_id="ZZT",
            state=None,
            stock_entries=[],
            reply_text=REFER_TO_SALESMAN,
            refer_context=None,
            ctx={},
            dry_run=False,
            chat_console=False,
        )
    assert written == []
    assert "building the Customer asks rows failed" in caplog.text


def _refer_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and node.id in ("REFER_TO_SALESMAN", "SALESMAN_TEAM"):
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in ("REFER_TO_SALESMAN", "SALESMAN_TEAM"):
        return node.attr
    return None


def test_guard_no_backend_composer_prints_the_refer_line_except_through_the_helper():
    """Every use of the constant outside `turn/refer.py` is an import, a comparison, the
    tail's marker tuple, or the barred lane's team sentinel; never a string it builds."""
    allowed_parents = (ast.Compare,)
    offenders: list[str] = []
    for path in (BACKEND / "app").rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in (
            "app/services/chatbot/turn/refer.py",
            "app/services/chatbot/turn/task.py",
            # Writes a Customer asks row's answer summary for a line already printed (and the
            # turn already marked) by the composer; it builds no reply.
            "app/services/chatbot/refer_asks.py",
        ):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "REFER_TO_SALESMAN" not in source and "SALESMAN_TEAM" not in source:
            continue
        tree = ast.parse(source)
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            name = _refer_name(node)
            if name is None or not isinstance(getattr(node, "ctx", None), ast.Load):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Attribute):  # `task_mod.REFER_TO_SALESMAN` is the Attribute itself
                continue
            if isinstance(parent, allowed_parents):
                continue
            if rel == "app/services/chatbot/tail/compose.py" and isinstance(parent, ast.Tuple):
                continue  # MARKERS: detects the line, prints nothing
            if name == "SALESMAN_TEAM" and isinstance(parent, ast.IfExp):
                continue  # `offer_team = refer.SALESMAN_TEAM if barred else ...`: a sentinel
            offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], offenders


def test_guard_the_sentence_is_spelled_only_in_the_constant():
    """No string literal outside `task.py` carries the words (docstrings aside), so the line
    cannot be printed around the helper by spelling it out."""
    offenders = []
    for path in (BACKEND / "app").rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel == "app/services/chatbot/turn/task.py":
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "refer to your salesman" not in source.lower():
            continue
        tree = ast.parse(source)
        docstrings = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "refer to your salesman" in node.value.lower()
                and id(node) not in docstrings
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], offenders


# --------------------------------------------------------------------------- #
# Real turns, real rows
# --------------------------------------------------------------------------- #


def test_a_live_stock_ask_logs_the_three_refer_lines_and_not_b3_incoming(session_factory, monkeypatch, stub_access):
    dealer = LiveDealer(session_factory, monkeypatch, stub_access, notify=False)
    out = dealer.ask_all_four()
    assert out.error is None, out.error
    assert "ZZTSA4-INC x 150: \u274c ETA 19/10/2026." in out.reply["text"]
    assert [(r.product_code, r.branch) for r in _rows(session_factory)] == [
        ("ZZTSA4-BIG", "too_big"),
        ("ZZTSA4-INS", "in_stock"),
        ("ZZTSA4-NOI", "no_incoming"),
    ]


def test_a_barred_contacts_incoming_miss_is_one_referred_row(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """#1406: "... No incoming and no stock for ZZTSC07.\\n\\nPlease refer to your salesman."
    to a contact whose escalation switch is off, not a dealer."""
    _seed_contact(session_factory, phone="+60900000005")
    _make_dealer(session_factory)
    result = _incoming_miss(session_factory, monkeypatch, stub_parser, stub_access)
    reply = (result.reply or {}).get("text") or ""
    assert reply.endswith(REFER_TO_SALESMAN), reply
    rows = _rows(session_factory)
    assert [(r.product_code, r.branch, r.quantity, r.source) for r in rows] == [
        ("ZZTSC07", "referred", None, "live")
    ]
    assert rows[0].answer_summary == reply
    assert rows[0].notify_skip_reason == "not_notified_branch"


def test_the_same_miss_for_an_allowed_contact_offers_the_team_and_logs_nothing(
    session_factory, stub_parser, stub_access, monkeypatch
):
    _seed_contact(session_factory, phone="+60900000007")
    result = _incoming_miss(session_factory, monkeypatch, stub_parser, stub_access)
    assert "escalate" in ((result.reply or {}).get("text") or "").lower()
    assert _rows(session_factory) == []


def test_a_barred_contact_asking_for_a_person_is_one_row_of_what_they_typed(
    session_factory, stub_parser, stub_access, monkeypatch
):
    _seed_contact(session_factory, phone="+60900000008")
    _make_dealer(session_factory)
    _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    stub_parser(_help_verdict())
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert (result.reply or {}).get("text") == REFER_TO_SALESMAN
    rows = _rows(session_factory)
    assert [(r.product_code, r.branch, r.answer_summary) for r in rows] == [
        ("price for SRTWC8517", "referred", REFER_TO_SALESMAN)
    ]


def test_a_barred_contacts_stale_yes_is_one_row(session_factory, stub_parser, stub_access, monkeypatch):
    _seed_contact(session_factory, phone="+60900000006")
    _make_dealer(session_factory)
    _write_open_question(
        session_factory,
        open_question={
            "kind": "team_pick",
            "expects": "yes_no",
            "team": "purchasing",
            "asked_at_turn": 1,
            "payload": {"agent": "incoming_stock_enquiries"},
            "options": [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {"team": "purchasing"}}],
        },
    )
    _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    stub_parser(_yes_verdict())
    stub_access()
    engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert [r.branch for r in _rows(session_factory)] == ["referred"]


def test_a_barred_contacts_small_talk_that_wrote_an_offer_is_one_row(session_factory, stub_access, lane):
    """The casual lane builds its send action before the `/complete` tail runs."""
    result = _play_fallback(
        session_factory,
        stub_access,
        lane,
        barred=True,
        clarifier={"ack": "Sorry about that. Would you like me to escalate to customer service team?", "language": "en"},
    )
    assert REFER_TO_SALESMAN in sent_text(result)
    rows = _rows(session_factory)
    assert [r.branch for r in rows] == ["referred"]
    assert REFER_TO_SALESMAN in rows[0].answer_summary


def test_an_unbarred_contacts_small_talk_logs_nothing(session_factory, stub_access, lane):
    _play_fallback(session_factory, stub_access, lane, barred=False)
    assert _rows(session_factory) == []


def test_a_dry_run_still_writes_nothing(session_factory, stub_parser, stub_access, monkeypatch):
    _seed_contact(session_factory, phone="+60900000009")
    _make_dealer(session_factory)
    _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    stub_parser(_help_verdict())
    stub_access()
    engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    assert _rows(session_factory) == []
