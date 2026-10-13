"""V1-DOC-SCOPE (security, 14 Oct 2026): a non-staff contact gets no other customer's documents.

Owner rule: unlinked != customer. A contact with no customer link and no staff tier
(`chatbot_profile.tier != "office"`) owns no documents, so every customer-owned document
(delivery order, sales order) is another customer's. A linked dealer sees only its links';
staff are unchanged.

Paths under test, each found answering for ANY customer on production (2ba0cf0ee):

1. A DO number: the resolver places `orders.order_number` for everyone and the per-match drop
   (`turn_runtime._without_foreign_sales_orders`) covered only SO / PO ref matches.
2. An SO number with the `sales_orders.outstanding` reveal: the lane looked it up with no
   scope (`linked=None`) for anyone whose scope was not enforced, unlinked included.
3. A DO list with no number ("deliveries this month"): the orders list ran with no customer
   ids for an unlinked contact.

Harness: `test_ref_probes_answer.py`'s (one real `engine.run_turn` with the REAL resolver over
rows seeded on the blank schema; only the parser, access check and MCP edge are doubled).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_customer_scope_lane import ORDERS, _calls, _link_customers, _other_customer
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_po_ref_ask import _make_staff
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, REFUSAL_PREFIX, _card, _seed_so

FOREIGN = "ZZT FOREIGN DOCS TRADING"
OWN = "ZZT OWN DOCS A"
DO_NO = "202610-0991"
SO_NO = "SO421624"
#: The existing scope refusal for a contact with no links (`contact_customer_scope.refusal_line`
#: over no names); no new reply text.
NO_LINK_REFUSAL = f"{REFUSAL_PREFIX} I can only check on your own account."

#: What the orders list answers: it names the foreign customer, so a leak through the fetch
#: shows in the reply as well as in the captured call.
FOREIGN_DO_LIST = {
    "has_result": True,
    "response": f"*Orders*\n1. {DO_NO} - {FOREIGN} - Delivered",
    "items": [{"order_number": DO_NO, "debtor_name": FOREIGN}],
}


def _seed_do(session_factory, number: str, customer_id: str | None, debtor_name: str | None = None) -> None:
    from app.models.order import Order
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = session_factory()
    db.add(
        Order(
            id=str(uuid.uuid4()),
            order_number=number,
            customer_id=customer_id,
            debtor_name=debtor_name,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.commit()


def _turn(session_factory, monkeypatch, qf: dict[str, Any], body: str, *, attributes=(OUTSTANDING_KEY,)):
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=qf,
        text_body=body,
        msg_id=f"ZZT-docscope-{uuid.uuid4().hex[:10]}",
        attributes=list(attributes),
        real_resolver=True,
        mcp_response=FOREIGN_DO_LIST,
    )
    return ((result.reply or {}).get("text") or ""), captured


def _number_ask(raw: str, hint: str) -> dict[str, Any]:
    return _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[{"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}],
    )


def _list_ask() -> dict[str, Any]:
    """"deliveries this month": a DO list with a range and no number, no customer, no "my"."""
    return _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[],
        date_filter_start="2026-10-01",
        date_filter_end="2026-10-31",
    )


# --------------------------------------------------------------------------- #
# 1. A delivery order by number
# --------------------------------------------------------------------------- #


class TestDoByNumber:
    @pytest.mark.parametrize("hint", ["order", "delivery_order"])
    def test_unlinked_non_staff_gets_no_foreign_do(self, session_factory, monkeypatch, hint) -> None:
        _seed_contact(session_factory, variables={})
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_do(session_factory, DO_NO, foreign, debtor_name=FOREIGN)

        reply, captured = _turn(session_factory, monkeypatch, _number_ask(DO_NO, hint), f"status of {DO_NO}")

        assert FOREIGN not in reply, reply
        assert _calls(captured, ORDERS) == [], captured

    def test_unlinked_non_staff_gets_no_unowned_do(self, session_factory, monkeypatch) -> None:
        """A DO with no customer and no debtor code belongs to no link either."""
        _seed_contact(session_factory, variables={})
        _seed_do(session_factory, DO_NO, None, debtor_name=FOREIGN)

        reply, captured = _turn(session_factory, monkeypatch, _number_ask(DO_NO, "order"), f"status of {DO_NO}")

        assert FOREIGN not in reply, reply
        assert _calls(captured, ORDERS) == [], captured

    def test_linked_dealer_gets_no_foreign_do(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, OWN)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_do(session_factory, DO_NO, foreign, debtor_name=FOREIGN)

        reply, captured = _turn(session_factory, monkeypatch, _number_ask(DO_NO, "order"), f"status of {DO_NO}")

        assert FOREIGN not in reply, reply
        for args in _calls(captured, ORDERS):
            assert foreign not in (args.get("customer_ids") or []), args

    def test_linked_dealer_gets_own_do(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own = _link_customers(session_factory, OWN)[0]
        _seed_do(session_factory, DO_NO, own, debtor_name=OWN)

        _reply, captured = _turn(session_factory, monkeypatch, _number_ask(DO_NO, "order"), f"status of {DO_NO}")

        (args,) = _calls(captured, ORDERS)
        assert args.get("customer_ids") == [own], args

    def test_staff_unchanged_gets_any_do(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _make_staff(session_factory)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_do(session_factory, DO_NO, foreign, debtor_name=FOREIGN)

        _reply, captured = _turn(session_factory, monkeypatch, _number_ask(DO_NO, "order"), f"status of {DO_NO}")

        assert _calls(captured, ORDERS), "staff still get any customer's DO through the orders list"


# --------------------------------------------------------------------------- #
# 2. A sales order by number, with the SO reveal
# --------------------------------------------------------------------------- #


class TestSoByNumberWithReveal:
    @pytest.mark.parametrize("hint", ["order_number", "sales_order"])
    def test_unlinked_non_staff_gets_the_refusal_not_the_card(self, session_factory, monkeypatch, hint) -> None:
        _seed_contact(session_factory, variables={})
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_so(session_factory, SO_NO, customer_id=foreign, debtor_name=FOREIGN, lines=[(10, 10)])

        reply, captured = _turn(session_factory, monkeypatch, _number_ask(SO_NO, hint), f"status of {SO_NO}")

        assert FOREIGN not in reply and "delivered" not in reply, reply
        assert reply.strip() == NO_LINK_REFUSAL, reply
        assert _calls(captured, ORDERS) == [], captured

    def test_linked_dealer_gets_own_so_only(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own = _link_customers(session_factory, OWN)[0]
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_so(session_factory, SO_NO, customer_id=own, debtor_name=OWN, lines=[(10, 1)])
        _seed_so(session_factory, "SO421777", customer_id=foreign, debtor_name=FOREIGN, lines=[(1, 1)])

        reply, _captured = _turn(session_factory, monkeypatch, _number_ask(SO_NO, "order_number"), f"status of {SO_NO}")
        # The real resolver placed the number, so the card head names the kind (REF-PROBES).
        assert reply.strip() == _card(f"Sales order {SO_NO}", "Open", "partly delivered", name=OWN), reply

        reply, _captured = _turn(session_factory, monkeypatch, _number_ask("SO421777", "order_number"), "status of SO421777")
        assert FOREIGN not in reply and reply.strip().startswith(REFUSAL_PREFIX), reply

    def test_staff_unchanged_sees_any_so(self, session_factory, monkeypatch) -> None:
        """AC-SO-14 as the owner meant it: staff carry no customer scope."""
        _seed_contact(session_factory, variables={})
        _make_staff(session_factory)
        cust = _other_customer(session_factory, "ZZT ANY SDN BHD")
        _seed_so(session_factory, SO_NO, customer_id=cust, debtor_name="ZZT ANY SDN BHD", lines=[(10, 10)])

        reply, _ = _turn(session_factory, monkeypatch, _number_ask(SO_NO, "order_number"), f"status of {SO_NO}")

        assert SO_NO in reply and "ZZT ANY SDN BHD" in reply and "fully delivered" in reply, reply


# --------------------------------------------------------------------------- #
# 3. A DO list with no number
# --------------------------------------------------------------------------- #


class TestDoListWithNoNumber:
    def test_unlinked_non_staff_lists_nothing(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})

        reply, captured = _turn(session_factory, monkeypatch, _list_ask(), "deliveries this month")

        assert _calls(captured, ORDERS) == [], captured
        assert FOREIGN not in reply, reply
        assert reply.strip() == NO_LINK_REFUSAL, reply

    def test_linked_dealer_lists_own_only(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own = _link_customers(session_factory, OWN, "ZZT OWN DOCS B")

        _reply, captured = _turn(session_factory, monkeypatch, _list_ask(), "deliveries this month")

        (args,) = _calls(captured, ORDERS)
        assert sorted(args.get("customer_ids") or []) == sorted(own), args

    def test_staff_unchanged_lists_unscoped(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _make_staff(session_factory)

        _reply, captured = _turn(session_factory, monkeypatch, _list_ask(), "deliveries this month")

        (args,) = _calls(captured, ORDERS)
        assert not args.get("customer_ids"), args
