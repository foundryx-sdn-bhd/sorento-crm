"""CHAT-COMPOSER Lists slice red tests, L3: the PO / PR reference cards (staff) page 10 lines per "more".

Spec: `documentation/plans/chatbot/chat-composer-seam-design-lists.md` section 3 (L3), Q4, section 7.
Page 1 keeps today's cards byte-identical and adds one "more" line at the end (gate on); a continuation
page is one card (head, range, next 10 lines); several cut cards continue the first, then the next.
Every row is seeded on the blank Postgres schema; no implementation exists when these are written.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot import ref_status, turn_runtime
from app.services.chatbot.lanes import business
from app.services.chatbot.turn.plan import FetchSpec
from app.services.chatbot.turn.state import Focus
from tests._pg_fixture import unique_code
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_composers_stock_xd import _set_gate
from tests.chatbot.test_ref_status import _seed_po, _seed_pr

MORE_10 = 'Reply "more" to see the next 10.'
GRANT = ["purchase_orders.placed"]


def _lines(n, tag="ZZTL"):
    return [(f"{tag}{i:02d}", i + 1) for i in range(n)]


def _seed(db):
    """A PO of 25 lines and a PR of 14 lines: both cards are cut at 10."""
    tag = unique_code()[:5].upper()
    po_no, pr_no = f"ZZT-PO-{tag}", f"ZZT-PR-{tag}"
    po = _seed_po(db, po_no, _lines(25, f"{tag}P"), supplier="Supplier Bhd")
    pr = _seed_pr(db, pr_no, [(f"{tag}R{i:02d}", i + 1) for i in range(14)], customer="Project Sdn Bhd")
    return po, pr


def _run_fetch(db, *, po=(), pr=(), ref_page=None, attributes=GRANT, staff=True):
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    def _no_tool(name, args):
        raise AssertionError("the reference card must come before any tool")

    out = {
        "domain_hint": "order",
        "intent_hint": "check_order",
        "message_type": "business_query",
        "entities": [],
        "po_numbers": list(po),
        "pr_numbers": list(pr),
        "ref_staff": staff,
    }
    if ref_page is not None:
        out["ref_page"] = ref_page
    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": []},
        "ctx": {
            "contact": {"id": str(uuid.uuid4())},
            "access": {"attributes": list(attributes)},
            "parse": {"output": out},
        },
    }
    return run_fetch(payload, services=FetchServices(mcp_call=_no_tool), db=db)


def _carry(po, pr, *, card=0, shown=10, total=25):
    return {
        "ref_cards": [{"kind": "po", "id": po.id, "lines": 25}, {"kind": "pr", "id": pr.id, "lines": 14}],
        "card": card,
        "shown": shown,
        "total": total,
    }


def test_gate_off_golden(session_factory, monkeypatch):
    _set_gate(monkeypatch, False)
    db = session_factory()
    po, pr = _seed(db)
    cards = ref_status.lookup_purchase_orders(db, [po.po_number]).cards
    fragment = _run_fetch(db, po=[po.po_number])
    assert fragment["fetch"]["response"] == cards[0]
    assert cards[0].endswith("and 15 more lines")
    assert "more" not in fragment["fetch"]["response"].replace("more lines", "")
    assert "set_window" not in fragment["fetch"]


def test_page_one_more_line(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    db = session_factory()
    po, pr = _seed(db)
    cards = [
        *ref_status.lookup_purchase_orders(db, [po.po_number]).cards,
        *ref_status.lookup_purchase_requests(db, [pr.request_number]).cards,
    ]
    fragment = _run_fetch(db, po=[po.po_number], pr=[pr.request_number])
    assert fragment["fetch"]["response"] == "\n\n".join(cards) + "\n\n" + MORE_10
    window = fragment["fetch"]["set_window"]
    assert (window["first"], window["last"], window["total"], window["next"]) == (1, 10, 25, 10)
    assert window["carry"] == {
        "ref_cards": [{"kind": "po", "id": po.id, "lines": 25}, {"kind": "pr", "id": pr.id, "lines": 14}],
        "card": 0,
        "shown": 10,
        "total": 25,
    }


def test_cut_cards_are_recorded_on_the_lookup(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    db = session_factory()
    po, _ = _seed(db)
    looked = ref_status.lookup_purchase_orders(db, [po.po_number])
    assert looked.cut == [{"kind": "po", "id": po.id, "lines": 25}]
    short = _seed_po(db, f"ZZT-PO-{unique_code()[:6].upper()}", _lines(3, "ZZTS"))
    assert ref_status.lookup_purchase_orders(db, [short.po_number]).cut == []


def test_card_page(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    db = session_factory()
    po, _ = _seed(db)
    text = ref_status.card_page(db, {"kind": "po", "id": po.id, "lines": 25}, 10)
    rows = text.splitlines()
    assert rows[0] == f"*Purchase order {po.po_number}* - Supplier Bhd (25, showing 11 to 20)"
    line_rows = [r for r in rows if " x " in r]
    assert len(line_rows) == 10
    assert line_rows[0].endswith(" x 11") and line_rows[-1].endswith(" x 20")
    assert rows[-1] == "and 5 more lines"


def test_next_ref_moves_to_the_next_card(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    base = {
        "ref_cards": [{"kind": "po", "id": "a", "lines": 25}, {"kind": "pr", "id": "b", "lines": 14}],
        "card": 0,
        "shown": 20,
        "total": 25,
    }
    step = ref_status.next_ref(base)
    assert (step["card"], step["shown"], step["total"]) == (0, 25, 25)
    step = ref_status.next_ref(step)
    assert (step["card"], step["shown"], step["total"]) == (1, 10, 14)
    step = ref_status.next_ref(step)
    assert (step["card"], step["shown"], step["total"]) == (1, 14, 14)
    assert ref_status.next_ref(step) == step


def test_past_the_end(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    db = session_factory()
    _, pr = _seed(db)
    text = ref_status.card_page(db, {"kind": "pr", "id": pr.id, "lines": 14}, 14)
    assert text == f"*Purchase request {pr.request_number}* - Project Sdn Bhd (14). That is all 14."


def test_card_page_of_a_gone_row_is_none(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    assert ref_status.card_page(session_factory(), {"kind": "po", "id": str(uuid.uuid4()), "lines": 25}, 10) is None


def test_page_turn_reads_the_next_ten(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    db = session_factory()
    po, pr = _seed(db)
    fragment = _run_fetch(db, ref_page=_carry(po, pr))
    text = fragment["fetch"]["response"]
    assert text.splitlines()[0] == f"*Purchase order {po.po_number}* - Supplier Bhd (25, showing 11 to 20)"
    assert text.endswith('Reply "more" to see the next 5.')
    assert fragment["fetch"]["set_window"]["carry"]["shown"] == 20


@pytest.mark.parametrize("grant,staff", [([], True), (GRANT, False)], ids=["no_grant", "not_staff"])
def test_page_refused_never_falls_through(session_factory, monkeypatch, grant, staff):
    _set_gate(monkeypatch, True)
    db = session_factory()
    po, pr = _seed(db)
    fragment = _run_fetch(db, ref_page=_carry(po, pr), attributes=grant, staff=staff)
    assert fragment["kind"] == "error"
    assert fragment["outcome"] == "not_found"
    assert fragment["error"] == "reference page refused"


def test_runner_skips_page_the_set(session_factory, monkeypatch):
    """A ref_cards carry is paged by the lane, not by `page_the_set` (which would raise here)."""
    _set_gate(monkeypatch, True)
    seen: list[dict[str, Any]] = []

    def fake_run_fetch(payload, **kwargs):
        seen.append(payload["ctx"]["parse"]["output"])
        return {"fetch": {"has_result": True, "response": "stubbed", "answers": []}}

    def boom(*args, **kwargs):
        raise AssertionError("page_the_set must not run for a ref_cards carry")

    monkeypatch.setattr(business, "run_fetch", fake_run_fetch)
    monkeypatch.setattr(turn_runtime, "page_the_set", boom)
    key = {
        "ref_cards": [{"kind": "po", "id": "a", "lines": 25}],
        "card": 0,
        "shown": 10,
        "total": 25,
        "domain": "order",
    }
    runner = turn_runtime.make_tool_runner(
        session_factory(),
        ctx={"parse": {"output": verdict(domain_hint="order", intent_hint="check_order")}},
        verdict=verdict(domain_hint="order", intent_hint="check_order"),
        focus=Focus(domains=["order"]),
        compatible_entities=[],
        predicate=None,
        unplaced={},
        space_id=None,
        dry_run=True,
        turn_trace=type("Trace", (), {"add": lambda self, *a, **k: None})(),
    )
    runner("order", FetchSpec(domain="order", entities=[], filters={"set_page": {"set_key": key}}, date_window=None))
    assert seen and seen[0]["ref_page"] == key
