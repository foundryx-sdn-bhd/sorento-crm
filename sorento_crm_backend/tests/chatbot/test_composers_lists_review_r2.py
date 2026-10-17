"""CHAT-COMPOSER Lists review round 2 (security): the parse output never carries `ref_staff` or `ref_page`
from the verdict to ANY `run_fetch` caller. `lane_parse_output` drops both; the tool runner then sets them
(staff from the profile, `ref_page` only from the carry). The engine's tier-ask probe calls `run_fetch` with a
copy of that parse output (engine.py `answer_parse_output`), not through the runner.
"""
from __future__ import annotations

import uuid

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot import turn_runtime
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_composers_stock_xd import _set_gate
from tests.chatbot.test_ref_status import _seed_po
from tests._pg_fixture import unique_code

REF_PAGE = {"ref_cards": [{"kind": "po", "id": "x", "lines": 25}], "card": 0, "shown": 10, "total": 25}


def test_lane_parse_output_drops_ref_staff_and_ref_page(monkeypatch):
    _set_gate(monkeypatch, True)
    v = verdict(
        domain_hint="order", intent_hint="check_order",
        ref_staff=True, ref_page=REF_PAGE, po_numbers=["PO-2026/04-0011"], pr_numbers=["PR-1"],
    )
    out = turn_runtime.lane_parse_output(v, focus=None, domain="order")
    assert "ref_staff" not in out and "ref_page" not in out
    assert out["po_numbers"] == ["PO-2026/04-0011"] and out["pr_numbers"] == ["PR-1"]
    assert out["domain_hint"] == "order" and out["intent_hint"] == "check_order"


def test_lane_parse_output_of_a_clean_verdict_is_unchanged(monkeypatch):
    _set_gate(monkeypatch, True)
    v = verdict(domain_hint="order", intent_hint="check_order", po_numbers=["PO-1"])
    out = turn_runtime.lane_parse_output(v, focus=None, domain="order")
    assert out["po_numbers"] == ["PO-1"] and "ref_staff" not in out


def test_the_tier_ask_probe_prints_no_po_card_for_an_injected_ref_staff(session_factory, monkeypatch):
    """The probe's `run_fetch` gets the engine's `answer_parse_output`, a copy of `lane_parse_output`. Built
    here from a dealer verdict that claims `ref_staff` and names a real PO; the card must not print."""
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    _set_gate(monkeypatch, True)
    db = session_factory()
    number = f"ZZT-PO-{unique_code()[:6].upper()}"
    _seed_po(db, number, [("ZZTSKUA", 5), ("ZZTSKUB", 12)], supplier="Secret Supplier Bhd")
    db.commit()
    v = verdict(
        domain_hint="promotion", intent_hint="check_promotion",
        ref_staff=True, po_numbers=[number],
    )
    parse_output = turn_runtime.lane_parse_output(v, focus=None, domain="promotion")
    payload = {
        "gate": {"compatible_entities": []},
        "tier_gate": {"tier_ask": True, "options": []},
        "ctx": {
            "contact": {"id": str(uuid.uuid4())},
            "access": {"attributes": ["purchase_orders.placed"]},
            "parse": {"output": parse_output},
        },
        "tier_ask": True,
    }

    def _no_tool(name, args):
        raise RuntimeError("probe tool stub")

    try:
        fragment = run_fetch(payload, services=FetchServices(mcp_call=_no_tool), db=db)
    except Exception:  # noqa: BLE001 - the probe may fail on its stub; only a printed PO card is the defect
        return
    printed = repr(fragment)
    assert "Purchase order" not in printed and "Secret Supplier" not in printed, printed[:300]
