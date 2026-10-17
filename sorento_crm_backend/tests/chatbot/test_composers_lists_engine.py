"""CHAT-COMPOSER Lists slice red tests: the engine arm (G1) and the bare "more" turn for each list.

Spec: `documentation/plans/chatbot/chat-composer-seam-design-lists.md` DL1 (the engine arm writes
`focus.set_page` from `set_window["carry"]`), L1-c, L2-h, L3-d and section 7. Real `engine.run_turn`,
the parser and the MCP edge doubled, the resolver real over rows seeded on the blank schema.
Turn 1 gate on prints page 1 and leaves the carry in the stored focus; turn 2 is a bare "more"
(`continuation: True`, nothing else); gate off leaves no carry.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from tests.chatbot.test_composers_stock_xd import _set_gate
from tests.chatbot.test_engine import CONTACT_ID, _parser_output, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_set_stock_render import world  # noqa: F401 - fixture used by name

BUDGET = 3900


def _find(node: Any, key: str) -> Any:
    """The first value stored under `key` anywhere in a nested session blob."""
    if isinstance(node, dict):
        if key in node and node[key] is not None:
            return node[key]
        for v in node.values():
            found = _find(v, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find(v, key)
            if found is not None:
                return found
    return None


def _stored_set_page(session_factory, contact_id: str) -> Any:
    db = session_factory()
    blob = db.execute(
        sa_text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(contact_id)}
    ).scalar()
    return _find(blob if isinstance(blob, (dict, list)) else json.loads(blob or "{}"), "set_page")


def _more_verdict(domain: str, intent: str):
    return _parser_output(
        message_type="business_query",
        intent_hint=intent,
        domain_hint=domain,
        continuation=True,
        entities=[],
        requested_attributes=[],
        user_goal="more",
    )


# --------------------------------------------------------------------------- #
# L1: a code-lookup list
# --------------------------------------------------------------------------- #


def _codes(text):
    return list(dict.fromkeys(re.findall(r"\*Product Code:\* (\S+)", text)))


def _l1_chat(session_factory, monkeypatch, stub_parser, stub_access, world):
    from tests.chatbot.test_set_stock_render import make_chat

    for i in range(13):
        p = world.basin(world.sorento, f"ZZQ{i:02d}", 10, 4)
        p.product_name = "N" * 250
    world.commit()
    return make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")


def _l1_ask():
    from tests.chatbot.test_attribute_asks_round2 import _stock_verdict

    return _stock_verdict(
        [{"raw": "ZZQ", "hint": "product"}],
        "details of ZZQ",
        intent_hint="check_product",
        domain_hint="master_products",
        requested_attributes=[],
    )


def test_engine_arms_and_pages(session_factory, monkeypatch, stub_parser, stub_access, world):
    _set_gate(monkeypatch, True)
    chat = _l1_chat(session_factory, monkeypatch, stub_parser, stub_access, world)
    first = chat.say("ZZQ", _l1_ask())
    head = first.splitlines()[0]
    m = re.search(r"\(showing 1 to (\d+)\)", head)
    assert m, head
    k = int(m.group(1))
    assert 0 < k < 13 and len(first) <= BUDGET, (k, len(first))
    assert f'Reply "more" to see the next {13 - k}.' in first
    page_one = _codes(first)
    assert len(page_one) == k and set(page_one) <= world.codes

    carry = _stored_set_page(session_factory, chat.contact_id)
    key = carry["set_key"]
    assert "capped" not in key
    assert key["code_list"] is True and key["total"] == 13 and key["shown"] == k and key["domain"] == "master_products"
    assert len(key["code_ids"]) == 13

    second = chat.say("more", _more_verdict("master_products", "check_product"))
    assert second.startswith(f"Here's what you want: matching products (showing {k + 1} to 13)"), second[:200]
    page_two = _codes(second)
    assert not set(page_one) & set(page_two)
    assert set(page_one) | set(page_two) == world.codes
    assert 'Reply "more"' not in second and "That is all" not in second
    lines = [ln for ln in second.strip().split("\n") if ln]
    # this world prints no footer, so the sentence is the last line (the l1 tests pin the with-footer case)
    sentence = lines[-2] if lines[-1].startswith("_") else lines[-1]
    assert sentence == "Those are the first 13 matches. Send more of the code to narrow it.", lines[-3:]

    third = chat.say("more", _more_verdict("master_products", "check_product"))
    assert third.strip() == "Those are the first 13 matches. Send more of the code to narrow it.", third[:200]


def test_engine_gate_off_leaves_no_code_list_carry(session_factory, monkeypatch, stub_parser, stub_access, world):
    _set_gate(monkeypatch, False)
    chat = _l1_chat(session_factory, monkeypatch, stub_parser, stub_access, world)
    first = chat.say("ZZQ", _l1_ask())
    assert len(_codes(first)) == 13 and len(first) > BUDGET, (len(_codes(first)), len(first))
    assert 'Reply "more"' not in first and "showing" not in first
    assert _stored_set_page(session_factory, chat.contact_id) is None


# --------------------------------------------------------------------------- #
# L2: the outstanding report's groups
# --------------------------------------------------------------------------- #


def _l2_envelope(offset: int) -> str:
    text = (
        "Product: SRTWT7445\nCustomer: all\nLocation: all\nOrder date: all\n\n"
        "*Sales order outstanding*\nSales orders: 45\n\nReply 1 for the sales order list."
    )
    shown = 20 if offset < 40 else 5
    return json.dumps(
        {
            "result_type": "outstanding_report",
            "response": text,
            "has_result": True,
            "group_window": {"offset": offset, "shown": shown, "total": 45},
        }
    )


def _l2_turn(session_factory, monkeypatch, *, qf, text, msg_id, offset=0):
    from tests.chatbot.test_outstanding_lane import PRODUCT_CODE, PRODUCT_UUID, _run_turn

    return _run_turn(
        session_factory,
        monkeypatch,
        qf=qf,
        text_body=text,
        msg_id=msg_id,
        attributes=["sales_orders.outstanding"],
        matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        mcp_response=_l2_envelope(offset),
    )


def test_engine_more_reruns_with_offset(session_factory, monkeypatch):
    from tests.chatbot.test_outstanding_lane import PRODUCT_CODE, _qf, _seed_contact

    _set_gate(monkeypatch, True)
    _seed_contact(session_factory, variables={})
    tag = uuid.uuid4().hex[:8]
    _result, first = _l2_turn(
        session_factory, monkeypatch, qf=_qf(order_status="so_outstanding"),
        text="SRTWT7445 sales order outstanding", msg_id=f"ZZT-l2-1-{tag}",
    )
    assert first and first[0][0] == "crm_outstanding_report"
    assert first[0][1].get("group_offset") == 0, first[0][1]
    carry = _stored_set_page(session_factory, CONTACT_ID)
    assert carry["set_key"]["report_groups"] is True and carry["set_key"]["shown"] == 20

    _result, second = _l2_turn(
        session_factory, monkeypatch, qf=_more_verdict("order", "check_order"), text="more",
        msg_id=f"ZZT-l2-2-{tag}", offset=20,
    )
    assert second and second[0][0] == "crm_outstanding_report", second
    args = second[0][1]
    assert args.get("group_offset") == 20, args
    assert args.get("product_code") == PRODUCT_CODE, args
    assert "detail" not in args or args["detail"] is None

    _result, third = _l2_turn(
        session_factory, monkeypatch,
        qf=_parser_output(message_type="casual", intent_hint=None, domain_hint=None, entities=[], reference_positions=[1]),
        text="1", msg_id=f"ZZT-l2-3-{tag}", offset=20,
    )
    assert third and third[0][1].get("detail") == "so", third
    assert "group_offset" not in third[0][1], third[0][1]


def test_engine_gate_off_sends_no_group_offset(session_factory, monkeypatch):
    from tests.chatbot.test_outstanding_lane import _qf, _seed_contact

    _set_gate(monkeypatch, False)
    _seed_contact(session_factory, variables={})
    _result, first = _l2_turn(
        session_factory, monkeypatch, qf=_qf(order_status="so_outstanding"),
        text="SRTWT7445 sales order outstanding", msg_id=f"ZZT-l2-off-{uuid.uuid4().hex[:8]}",
    )
    assert first and "group_offset" not in first[0][1]
    assert _stored_set_page(session_factory, CONTACT_ID) is None


# --------------------------------------------------------------------------- #
# L3: a cut PO card
# --------------------------------------------------------------------------- #


def _l3_turn(session_factory, monkeypatch, qf, text, msg_id):
    from tests.chatbot.test_outstanding_lane import _run_turn
    from tests.chatbot.test_ref_probes_answer import DO_LIST, PLACED

    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=text, msg_id=msg_id,
        attributes=["sales_orders.outstanding", PLACED], real_resolver=True, mcp_response=DO_LIST,
    )
    return (result.reply or {}).get("text") or ""


def _l3_seed(session_factory):
    from tests.chatbot.test_outstanding_lane import _seed_contact
    from tests.chatbot.test_po_ref_ask import _make_staff
    from tests.chatbot.test_ref_status import _seed_po

    _seed_contact(session_factory, variables={})
    _make_staff(session_factory)
    db = session_factory()
    number = f"PO-2026/04-{uuid.uuid4().int % 10000:04d}"
    _seed_po(db, number, [(f"ZZTL{uuid.uuid4().hex[:5]}{i:02d}", i + 1) for i in range(25)], supplier="Supplier Bhd")
    db.commit()
    return number


def _l3_ask(number):
    from tests.chatbot.test_ref_probes_answer import _ask

    return _ask(number, "purchase_order")


def test_engine_walks_the_cards(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    number = _l3_seed(session_factory)
    tag = uuid.uuid4().hex[:8]
    first = _l3_turn(session_factory, monkeypatch, _l3_ask(number), f"status of {number}", f"ZZT-l3-1-{tag}")
    assert first.rstrip().endswith('Reply "more" to see the next 10.'), first[-200:]
    key = _stored_set_page(session_factory, CONTACT_ID)["set_key"]
    assert key["ref_cards"][0]["kind"] == "po" and key["card"] == 0 and key["shown"] == 10 and key["total"] == 25

    more = _more_verdict("order", "check_order")
    second = _l3_turn(session_factory, monkeypatch, more, "more", f"ZZT-l3-2-{tag}")
    assert second.splitlines()[0] == f"*Purchase order {number}* - Supplier Bhd (25, showing 11 to 20)", second[:200]
    assert second.rstrip().endswith('Reply "more" to see the next 5.')

    third = _l3_turn(session_factory, monkeypatch, more, "more", f"ZZT-l3-3-{tag}")
    assert third.splitlines()[0] == f"*Purchase order {number}* - Supplier Bhd (25, showing 21 to 25)", third[:200]
    assert 'Reply "more"' not in third and "more lines" not in third

    fourth = _l3_turn(session_factory, monkeypatch, more, "more", f"ZZT-l3-4-{tag}")
    assert fourth.strip() == f"*Purchase order {number}* - Supplier Bhd (25). That is all 25."


def test_engine_gate_off_ref_card_leaves_no_carry(session_factory, monkeypatch):
    _set_gate(monkeypatch, False)
    number = _l3_seed(session_factory)
    first = _l3_turn(session_factory, monkeypatch, _l3_ask(number), f"status of {number}", f"ZZT-l3-off-{uuid.uuid4().hex[:8]}")
    assert "and 15 more lines" in first and 'Reply "more"' not in first
    assert _stored_set_page(session_factory, CONTACT_ID) is None
