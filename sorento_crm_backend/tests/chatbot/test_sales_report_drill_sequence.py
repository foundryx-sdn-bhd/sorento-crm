"""PR #1401 fix round 3 - the browser pass's drill sequence (crew copy, Chatbot Console, a
contact linked to many accounts), replayed through the lane harness turn by turn.

F6: after a drill over every link, a new report over ONE named account, then "1": the
re-run must be By product over that one account (the offer's own stored subject), with a
real tool call; the parser's stale `document: ["DO"]` must not outrank the picked position.
F7: a new report replaces the previous sales_report_detail offer, so "delivery orders" at
the new offer picks its own option 2.
F4: an out-of-range number re-asks with "Please reply with a number from <first> to <last>."
above the options (mock v3 section 8, carried into v4).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_customer_scope_lane import _link_customers
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact, _session_of
from tests.chatbot.test_sales_report_lane import SALES_REPORT_HIT

ATTRS = ["sales_orders.sales_report"]
SALES = "crm_sales_report"
LINKS = ("ZZT LINK ONE SDN BHD", "ZZT LINK TWO SDN BHD", "ZZT SOON HENG HARDWARE CO.SDN.BHD. [A/C I]")
SOON = LINKS[2]

MULTI_HIT: dict[str, Any] = {
    **SALES_REPORT_HIT,
    "customer_name": ", ".join(LINKS),
    "customer_count": 3,
    "date_from": "2026-07-01",
    "date_to": "2026-09-30",
    "options": [
        {"key": "customer", "label": "By customer"},
        {"key": "product", "label": "By product"},
        {"key": "delivery_order", "label": "Delivery orders"},
    ],
}

#: The DO list over every link: ten rows printed, so its options are numbered 11 and 12.
DO_LIST_HIT: dict[str, Any] = {
    **MULTI_HIT,
    "group_by": "delivery_order",
    "rows": [
        {"rank": i, "name": f"ZZT-DO-{i:02d}", "qty": 1, "amount": 10.0, "date": "2026-08-01",
         "customer_name": LINKS[0]}
        for i in range(1, 11)
    ],
    "more": 3,
    "options": [
        {"key": "customer", "label": "By customer"},
        {"key": "product", "label": "By product"},
    ],
}

#: The report over the one named account: By product and Delivery orders only.
ONE_HIT: dict[str, Any] = {
    **SALES_REPORT_HIT,
    "customer_name": SOON,
    "customer_count": 1,
    "date_from": "2026-07-01",
    "date_to": "2026-09-30",
}

WINDOW = {"date_filter_start": "2026-07-01", "date_filter_end": "2026-09-30"}


def _turn(session_factory, monkeypatch, qf, body, hit, matches=None):
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=body,
        msg_id=f"ZZT-drillseq-{uuid.uuid4().hex[:10]}", attributes=ATTRS, mcp_response=hit,
        matches=matches,
    )
    _turn.last_turn_id = result.turn_id
    return ((result.reply or {}).get("text") or ""), [args for name, args in captured if name == SALES], captured


def _trace_kinds(session_factory) -> list[Any]:
    from tests.chatbot.test_engine import _turn_row

    return [r.get("kind") for r in (_turn_row(session_factory, _turn.last_turn_id).trace or [])]


def _pick(position: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        reference_positions=[position],
    )
    base.update(over)
    return _parser_output(**base)


def _typed(word: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        message_type="casual", intent_hint=None, domain_hint=None, reference_positions=[],
        entities=[{"raw": word, "hint": "order", "canonical_code": None, "current_message": True, "confident": True}],
        entity_op="reuse",
    )
    base.update(over)
    return _parser_output(**base)


def _offer(session_factory) -> dict[str, Any]:
    return _session_of(session_factory).get("open_question") or {}


def _values(offer: dict[str, Any]) -> list[Any]:
    return [((o.get("payload") or {}).get("value"), o.get("position")) for o in offer.get("options") or []]


def _to_the_one_account_report(session_factory, monkeypatch) -> list[str]:
    """Steps 1 to 5 of the browser pass. Returns the link ids (SOON HENG last)."""
    _seed_contact(session_factory, variables={})
    links = _link_customers(session_factory, *LINKS)
    # 1. "my sales from July to September": every link, three options.
    _r, calls, _c = _turn(
        session_factory, monkeypatch,
        _parser_output(domain_hint="order", intent_hint="check_order", order_status="sales_report",
                       self_reference=True, entities=[], **WINDOW),
        "my sales from July to September", MULTI_HIT,
    )
    assert calls and calls[0]["customer_ids"] == links, calls
    # 2. "1": By customer.
    _r, calls, _c = _turn(session_factory, monkeypatch, _pick(1), "1", MULTI_HIT)
    assert calls and calls[0].get("group_by") == "customer", calls
    # 3. "DO": the DO list over every link (the parser emits document DO too).
    _r, calls, _c = _turn(session_factory, monkeypatch, _typed("DO", document=["DO"]), "DO", DO_LIST_HIT)
    assert calls and calls[0].get("group_by") == "delivery_order", calls
    assert _values(_offer(session_factory)) == [("customer", 11), ("product", 12)], _offer(session_factory)
    # 4. "9": nothing offered at 9.
    _r, calls, _c = _turn(session_factory, monkeypatch, _pick(9), "9", DO_LIST_HIT)
    assert calls == [], calls
    # 5. A new report over ONE named account.
    _r, calls, _c = _turn(
        session_factory, monkeypatch,
        _parser_output(
            domain_hint="order", intent_hint="check_order", order_status="sales_report",
            entities=[{"raw": "SOON HENG", "hint": "customer", "canonical_code": None,
                       "current_message": True, "confident": True}],
            entity_op="replace_combine", **WINDOW,
        ),
        f"sales of {SOON} from July to September", ONE_HIT,
        matches={"SOON HENG": {"uuid": links[2], "entity_type": "customer", "canonical_code": SOON}},
    )
    assert calls and calls[0]["customer_ids"] == [links[2]], calls
    assert _values(_offer(session_factory)) == [("product", 1), ("delivery_order", 2)], _offer(session_factory)
    return links


class TestF6APickAfterANewReportRunsOverThatReportsAccount:
    @pytest.mark.parametrize("self_reference", [False, True])
    def test_one_after_the_one_account_report_is_by_product_over_that_account(
        self, session_factory, monkeypatch, self_reference
    ) -> None:
        links = _to_the_one_account_report(session_factory, monkeypatch)
        # 6. "1", with the parser's stale carry from step 3 (document DO, group_by product).
        _reply, calls, captured = _turn(
            session_factory, monkeypatch,
            _pick(1, document=["DO"], group_by="product", entity_op="reuse", self_reference=self_reference),
            "1", ONE_HIT,
        )
        assert calls, ("the pick must call the tool, never replay an earlier body", captured)
        assert calls[0].get("group_by") == "product", calls
        assert calls[0]["customer_ids"] == [links[2]], calls
        assert calls[0].get("date_from") == "2026-07-01" and calls[0].get("date_to") == "2026-09-30", calls
        assert "tool" in _trace_kinds(session_factory), "the drill's tool call is on the turn's trace"


class TestF7ANewReportReplacesTheOffer:
    def test_delivery_orders_at_the_new_offer_picks_its_option_two(self, session_factory, monkeypatch) -> None:
        links = _to_the_one_account_report(session_factory, monkeypatch)
        reply, calls, _c = _turn(session_factory, monkeypatch, _typed("delivery orders"), "delivery orders", ONE_HIT)
        assert calls, reply
        assert calls[0].get("group_by") == "delivery_order", calls
        assert calls[0]["customer_ids"] == [links[2]], calls
        assert "11. By customer" not in reply, reply


class TestF4OutOfRangeSaysWhichNumbers:
    def test_a_number_past_the_end_names_the_range_above_the_options(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _turn(
            session_factory, monkeypatch,
            _parser_output(domain_hint="order", intent_hint="check_order", order_status="sales_report",
                           entities=[{"raw": "ZZT", "hint": "customer", "canonical_code": None,
                                      "current_message": True, "confident": True}]),
            "my sales", SALES_REPORT_HIT,
            matches={"ZZT": {"uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "entity_type": "customer",
                             "canonical_code": "ZZT"}},
        )
        reply, calls, _c = _turn(session_factory, monkeypatch, _pick(9), "9", SALES_REPORT_HIT)
        assert calls == [], calls
        assert reply.startswith("Please reply with a number from 1 to 2.\n"), reply
        assert "1. By product" in reply and "2. Delivery orders" in reply, reply

    def test_continued_numbering_names_the_first_and_last_option(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, *LINKS)
        _turn(
            session_factory, monkeypatch,
            _parser_output(domain_hint="order", intent_hint="check_order", order_status="sales_report",
                           self_reference=True, entities=[], **WINDOW),
            "my sales", DO_LIST_HIT,
        )
        assert _values(_offer(session_factory)) == [("customer", 11), ("product", 12)], _offer(session_factory)
        reply, calls, _c = _turn(session_factory, monkeypatch, _pick(9), "9", DO_LIST_HIT)
        assert calls == [], calls
        assert reply.startswith("Please reply with a number from 11 to 12.\n"), reply
        assert "11. By customer" in reply and "12. By product" in reply, reply


def _raws_in(node: Any) -> list[str]:
    """Every entity `raw` anywhere under the stored session focus, lower-cased."""
    if isinstance(node, dict):
        own = [str(node["raw"]).strip().lower()] if isinstance(node.get("raw"), str) else []
        return own + [r for v in node.values() for r in _raws_in(v)]
    if isinstance(node, list):
        return [r for v in node for r in _raws_in(v)]
    return []


class TestR4APickWordIsNeverCarriedAsASubject:
    """PR #1401 fix round 4: the word that settled a drill pick ("DO", "by product",
    "delivery orders") is the answer, not a subject. It must not be written onto the focus,
    or every later turn carries it, nothing resolves it, and the reply closes with
    "I could not find DO."."""

    def test_step_six_reply_carries_no_could_not_find(self, session_factory, monkeypatch) -> None:
        _to_the_one_account_report(session_factory, monkeypatch)
        reply, calls, _c = _turn(
            session_factory, monkeypatch,
            _pick(1, document=["DO"], group_by="product", entity_op="reuse"), "1", ONE_HIT,
        )
        assert calls, reply
        assert "could not find" not in reply.lower(), reply

    def test_a_pick_right_after_the_typed_do_carries_no_could_not_find(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, *LINKS)
        _turn(
            session_factory, monkeypatch,
            _parser_output(domain_hint="order", intent_hint="check_order", order_status="sales_report",
                           self_reference=True, entities=[], **WINDOW),
            "my sales from July to September", MULTI_HIT,
        )
        _turn(session_factory, monkeypatch, _typed("DO", document=["DO"]), "DO", DO_LIST_HIT)
        reply, calls, _c = _turn(session_factory, monkeypatch, _pick(12), "12", MULTI_HIT)
        assert calls and calls[0].get("group_by") == "product", calls
        assert "could not find" not in reply.lower(), reply

    @pytest.mark.parametrize(
        ("word", "over", "group_by"),
        [
            ("DO", {"document": ["DO"]}, "delivery_order"),
            ("by product", {}, "product"),
            ("delivery orders", {}, "delivery_order"),
        ],
    )
    def test_the_typed_pick_word_is_not_stored_on_the_focus(
        self, session_factory, monkeypatch, word, over, group_by
    ) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, *LINKS)
        _turn(
            session_factory, monkeypatch,
            _parser_output(domain_hint="order", intent_hint="check_order", order_status="sales_report",
                           self_reference=True, entities=[], **WINDOW),
            "my sales from July to September", MULTI_HIT,
        )
        reply, calls, _c = _turn(session_factory, monkeypatch, _typed(word, **over), word, MULTI_HIT)
        assert calls and calls[0].get("group_by") == group_by, calls
        assert "could not find" not in reply.lower(), reply
        focus = _session_of(session_factory).get("focus") or {}
        assert word.lower() not in _raws_in(focus), focus
