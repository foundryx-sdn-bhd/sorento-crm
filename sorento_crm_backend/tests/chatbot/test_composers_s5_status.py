"""CHAT-COMPOSER S5 red tests: order / document status. One period question (byte-identical fold,
no gate), the order's own customer in a sentence prints the group name (R3, lead decision OI-3),
and the miss header is the scope-block header (gated fold F1).

Goldens are literals captured from main code on 17 Oct. `do_ask.today_myt` is frozen to 2026-10-17.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services import ledger_family as LF
from app.services.chatbot import do_ask, so_status
from app.services.chatbot.lanes.business.answer import not_found_error_message
from tests.chatbot.test_composers_stock_xd import _set_gate
from tests.chatbot.test_customer_header_words import HANLIM, HANLIM_NAMES, _hanlim_rows, _mapping

TODAY = date(2026, 10, 17)
LEDGER = "HANLIM TRADING SDN BHD [A/C I]"
OFFER = "Would you like me to escalate to customer service team?"


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    monkeypatch.setattr(do_ask, "today_myt", lambda: TODAY)


def _do(si, subject_entities=()):
    return do_ask.range_reply(
        ctx={}, tool_name="t", order_tools={"t"}, entities=list(subject_entities), semantic_input=si
    )


def _so(si, subject=None):
    return so_status.period_reply(do_ask._parse(si.get("date_filter_start")), do_ask._parse(si.get("date_filter_end")), subject)


ASK_THIS_LAST = (
    "Which period?\n- This month (Oct 2026)\n- Last month (Sep 2026)\n"
    "Or type a month (e.g. August) or dates (e.g. 15 Sep to 10 Oct)."
)
LEAD6 = "That is 6 months (01/04/2026 to 30/09/2026). I can show up to 31 days of {noun} at a time:\n- Sep 2026\n- Apr 2026\nOr type a month or dates."
LEAD47 = "That is 47 days (01/09/2026 to 17/10/2026). I can show up to 31 days of {noun} at a time:\n- Oct 2026\n- Sep 2026\nOr type a month or dates."
LEAD121 = "That is 121 days (01/11/2026 to 01/03/2027). I can show up to 31 days of {noun} at a time:\n- Oct 2026\nOr type a month or dates."
GRID = [
    ({}, ASK_THIS_LAST),
    ({"date_filter_start": "2026-04-01", "date_filter_end": "2026-09-30"}, LEAD6),
    ({"date_filter_start": "2026-09-30", "date_filter_end": "2026-04-01"}, LEAD6),
    ({"date_filter_start": "2026-09-01"}, LEAD47),
    ({"date_filter_start": "2026-11-01", "date_filter_end": "2027-03-01"}, LEAD121),
    ({"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-20"}, None),
]


@pytest.mark.parametrize("gate", [False, True])
@pytest.mark.parametrize("si,tmpl", GRID, ids=[str(i) for i in range(len(GRID))])
def test_period_question_do_bytes(monkeypatch, gate, si, tmpl):
    _set_gate(monkeypatch, gate)
    assert _do(si) == (tmpl.format(noun="delivery orders") if tmpl else None)


@pytest.mark.parametrize("gate", [False, True])
@pytest.mark.parametrize("si,tmpl", GRID, ids=[str(i) for i in range(len(GRID))])
def test_period_question_so_bytes(monkeypatch, gate, si, tmpl):
    _set_gate(monkeypatch, gate)
    assert _so(si) == (tmpl.format(noun="sales orders") if tmpl else None)


def test_period_question_subject():
    expect = (
        "Which period for HANLIM TRADING SDN BHD?\n- This month (Oct 2026)\n- Last month (Sep 2026)\n"
        "Or type a month (e.g. August) or dates (e.g. 15 Sep to 10 Oct)."
    )
    assert _so({}, HANLIM) == expect


@pytest.mark.parametrize("si,tmpl", GRID, ids=[str(i) for i in range(len(GRID))])
def test_status_period_question_is_the_one_copy(si, tmpl):
    from app.services.chatbot.composers import status

    start, end = do_ask._parse(si.get("date_filter_start")), do_ask._parse(si.get("date_filter_end"))
    for noun in ("delivery orders", "sales orders"):
        got = status.period_question(start, end, today=TODAY, subject=None, noun=noun, max_days=31)
        assert got == (tmpl.format(noun=noun) if tmpl else None)


# --------------------------------------------------------------------------- #
# H2: the status-filtered order miss
# --------------------------------------------------------------------------- #


def _order(status, *, grouped=True, htype="order", code="DO123"):
    match = {"entity_type": htype, "uuid": "o1", "canonical_code": code, "display": {"customer_name": LEDGER}}
    resolved = {
        "tokens": [code],
        "unresolved_tokens": [],
        "resolutions": [{"token": code, "matches": [match]}],
        "intersection": [match],
        "by_entity_type": {htype: [match]},
    }
    parser = {
        "domain_hint": "order",
        "order_status": status,
        "entities": [{"hint": htype, "raw": code}],
        "routing": {"suggested_team": "customer_service"},
        "access_levels": [],
    }
    gate = {"gate_passed": True, "compatible_entities": [{"uuid": "o1", "entity_type": htype, "code": code}]}

    def run():
        return not_found_error_message({}, parser=parser, resolved=resolved, gate=gate).get("escalate_message")

    if grouped:
        with LF.customer_groups({LEDGER: HANLIM}):
            return run()
    return run()


def test_order_miss_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _order("delivered") == f"Order DO123 ({LEDGER}) hasn't been delivered yet. {OFFER}"
    assert _order("outstanding") == (
        f"Order DO123 ({LEDGER}) has no outstanding items - it looks already delivered or closed. {OFFER}"
    )


def test_order_miss_group_name_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    assert _order("delivered") == f"Order DO123 ({HANLIM}) hasn't been delivered yet. {OFFER}"


def test_order_miss_outstanding_group_name_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    assert _order("outstanding") == (
        f"Order DO123 ({HANLIM}) has no outstanding items - it looks already delivered or closed. {OFFER}"
    )


@pytest.mark.parametrize("status", ["delivered", "outstanding"])
def test_order_miss_ungrouped_unchanged_gate_on(monkeypatch, status):
    _set_gate(monkeypatch, False)
    off = _order(status, grouped=False)
    _set_gate(monkeypatch, True)
    assert _order(status, grouped=False) == off
    assert LEDGER in off


# --------------------------------------------------------------------------- #
# H1: the found bullet
# --------------------------------------------------------------------------- #


def test_found_bullet_order_group_name(monkeypatch):
    head = "Customer: all customers\nProduct: all products\nOrder: DO123\nDates: all dates\n\nHere's what you want:\n"
    tail = f"\n\nBut no order matched these. {OFFER}"
    _set_gate(monkeypatch, False)
    assert _order(None) == f"{head}• order: DO123 ({LEDGER}){tail}"
    _set_gate(monkeypatch, True)
    assert _order(None) == f"{head}• order: DO123 ({HANLIM}){tail}"


# --------------------------------------------------------------------------- #
# F1: the miss header is the scope-block header (DO number is a delivery order)
# --------------------------------------------------------------------------- #


def test_miss_header_gate_off_unchanged(monkeypatch):
    _set_gate(monkeypatch, False)
    text = _order(None, htype="customer_order")
    assert text.startswith("Customer: all customers\nProduct: all products\nOrder: DO123\nDates: all dates\n\n")


def test_miss_header_is_scope_block_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _order(None, htype="customer_order")
    assert text.startswith("Customer: all customers\nProduct: all products\nDelivery order: DO123\nDates: all dates\n\n")


@pytest.mark.parametrize("gate", [False, True])
def test_miss_header_customer_fixture_same_both_states(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    parser = {"domain_hint": "order", "entities": [], "routing": {}, "access_levels": []}
    resolved = {
        "by_entity_type": {"customer": ["300-H030"]},
        "resolutions": [],
        "unresolved_tokens": ["ZZXX"],
        "tokens": [],
    }
    gate_ = {"gate_passed": True, "compatible_entities": _hanlim_rows()}
    with LF.customer_groups(_mapping(HANLIM_NAMES, HANLIM)):
        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate_)["escalate_message"]
    assert out.startswith("Customer: HANLIM TRADING SDN BHD\nProduct: all products\nDates: all dates\n\n")


def test_s5_output_clean(monkeypatch):
    import re

    _set_gate(monkeypatch, True)
    texts = [_order("delivered"), _order("outstanding"), _order(None), _order(None, htype="customer_order")]
    for t in texts:
        assert "[A/C" not in t, t
        assert "accounts)" not in t and " · " not in t
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", t)


def test_status_module_import_boundary():
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "app" / "services" / "chatbot" / "composers" / "status.py"
    assert path.is_file(), "composers/status.py must exist"
    banned = ("engine", "head", "tail", "dialogue", "lanes")
    for node in ast.walk(ast.parse(path.read_text())):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            parts = name.split(".")
            if name.startswith("app.services.chatbot") and len(parts) > 3:
                assert parts[3] not in banned and parts[3:5] != ["turn", "compose"], name


# --------------------------------------------------------------------------- #
# R3: the customer found bullet names the group only, no "(+N more)"
# --------------------------------------------------------------------------- #


def _customer_miss():
    parser = {"domain_hint": "order", "entities": [], "routing": {}, "access_levels": []}
    resolved = {
        "by_entity_type": {"customer": ["300-H030"]},
        "resolutions": [],
        "unresolved_tokens": ["ZZXX"],
        "tokens": [],
    }
    gate_ = {"gate_passed": True, "compatible_entities": _hanlim_rows()}
    with LF.customer_groups(_mapping(HANLIM_NAMES, HANLIM)):
        return not_found_error_message({}, parser=parser, resolved=resolved, gate=gate_)["escalate_message"]


def test_customer_bullet_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert "\n• customer: HANLIM TRADING SDN BHD [A/C I] (+5 more)\n" in _customer_miss()


def test_customer_bullet_group_name_only_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _customer_miss()
    assert "\n• customer: HANLIM TRADING SDN BHD\n" in text, text
    assert "(+" not in text and "more)" not in text and "[A/C" not in text


@pytest.mark.parametrize("gate", [False, True])
def test_product_bullet_keeps_its_more_count(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    rows = [
        {
            "entity_type": "product",
            "uuid": f"u{i}",
            "canonical_code": f"SRTWC18{i}-BL",
            "code": f"SRTWC18{i}-BL",
            "display": {"description": "d"},
        }
        for i in range(10)
    ]
    parser = {
        "domain_hint": "master_products",
        "entities": [{"raw": "intelligent toilet", "hint": "product", "confident": False, "current_message": True}],
        "routing": {"suggested_team": "warehouse"},
        "access_levels": [],
    }
    resolved = {
        "by_entity_type": {"product": ["SRTWC180-BL"]},
        "resolutions": [{"token": "intelligent toilet", "matches": rows}],
        "unresolved_tokens": [],
        "tokens": [],
    }
    out = not_found_error_message(
        {}, parser=parser, resolved=resolved, gate={"gate_passed": True, "compatible_entities": rows}
    )["escalate_message"]
    assert "• product: SRTWC180-BL (+9 more)" in out
