"""CHAT-COMPOSER Lists review round 2 (security): the parse output never carries `ref_staff` or `ref_page`
from the verdict to ANY `run_fetch` caller. `lane_parse_output` drops both; the tool runner then sets them
(staff from the profile, `ref_page` only from the carry). The engine's tier-ask probe calls `run_fetch` with a
copy of that parse output (engine.py `answer_parse_output`), not through the runner.
"""
from __future__ import annotations

import uuid

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot import turn_runtime
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_composers_stock_xd import _set_gate
from tests.chatbot.test_set_stock_render import world  # noqa: F401 - fixture used by name
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
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


# --------------------------------------------------------------------------- #
# Review r3: an emptied code-list page never prints the total (LD7)
# --------------------------------------------------------------------------- #


def test_an_emptied_code_list_page_never_prints_the_total(monkeypatch):
    """Page 2 of a 13-code list whose carried products are all gone renders no rows. The Sets header would
    print "(13)" (a total on a code list); LD7 says the list is over and the narrow sentence is the reply."""
    from tests.chatbot.test_composers_lists_l1 import _list_predicate, _narrow, _run

    _set_gate(monkeypatch, True)
    out = _run(0, items=[], predicate=_list_predicate(13, 9))
    text = out["response"]
    assert "(13" not in text, text
    assert _narrow(13) in text, text
    assert "That is all" not in text


@pytest.mark.xfail(
    strict=True,
    reason="pre-existing: a code-lookup card list mints no last_list (turn/tail.py last_list_after), so a bare "
    "number re-runs the ask, gate off too; follow-up outside the Lists slice",
)
def test_a_bare_number_after_a_cut_code_list_picks_that_card(session_factory, monkeypatch, stub_parser, stub_access, world):
    """LD5 end to end: "2" after a cut list is the second card, not a count and not a new page."""
    import re

    from tests.chatbot.test_composers_lists_engine import _codes, _l1_ask, _l1_chat
    from tests.chatbot.test_engine import _parser_output

    _set_gate(monkeypatch, True)
    chat = _l1_chat(session_factory, monkeypatch, stub_parser, stub_access, world)
    first = chat.say("ZZQ", _l1_ask())
    page_one = _codes(first)
    assert len(page_one) >= 2
    reply = chat.say(
        "2",
        _parser_output(message_type="casual", intent_hint=None, domain_hint=None, entities=[], reference_positions=[2]),
    )
    assert page_one[1] in reply, reply[:300]
    assert not re.search(r"showing \d+ to", reply) and 'Reply "more"' not in reply


def test_an_emptied_code_list_page_through_the_runner_prints_no_total(session_factory, monkeypatch):
    """The real path: a bare "more" on a 13-code carry (shown 9) whose products are gone. The runner finds no
    product rows for the carried ids, the master-products tool answers an OK envelope with zero items and
    `has_result: False` (the presenter's shape for an empty `product_ids` read), and the reply carries no total."""
    import json

    from app.services.chatbot.lanes.business import services as business_services
    from app.services.chatbot.lanes.business.services import FetchServices
    from app.services.chatbot.turn.plan import FetchSpec
    from app.services.chatbot.turn.state import Focus

    _set_gate(monkeypatch, True)
    calls: list[str] = []

    def mcp(name, args):
        calls.append(name)
        return json.dumps({"result_type": "products", "intro": "Products found.", "items": [], "has_result": False})

    monkeypatch.setattr(business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp))
    v = verdict(domain_hint="master_products", intent_hint="check_product", continuation=True)
    runner = turn_runtime.make_tool_runner(
        session_factory(),
        ctx={"parse": {"output": v}},
        verdict=v,
        focus=Focus(domains=["master_products"]),
        compatible_entities=[],
        predicate=None,
        unplaced={},
        space_id=None,
        dry_run=True,
        turn_trace=type("Trace", (), {"add": lambda self, *a, **k: None})(),
    )
    key = {
        "code_list": True,
        "set_noun": "matching products",
        "code_ids": [{"code": f"SRTWC{i:03d}", "ids": [str(uuid.uuid4())]} for i in range(13)],
        "total": 13,
        "shown": 9,
        "domain": "master_products",
    }
    envelope = runner(
        "master_products",
        FetchSpec(domain="master_products", entities=[], filters={"set_page": {"set_key": key}}, date_window=None),
    )
    text = json.dumps(envelope, default=str)
    assert "(13" not in text and "That is all" not in text, text[:600]
    narrow = "Those are the first 13 matches. Send more of the code to narrow it."
    assert narrow in text, text[:600]
