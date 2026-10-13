"""V1-DOC-SCOPE (security, 14 Oct 2026): a non-staff contact gets no other customer's documents.

Owner rule: unlinked != customer. A contact with no customer link that is not staff owns no
documents, so every customer-owned document (delivery order, sales order) is another
customer's. Staff = `chatbot_profile.tier == "office"` OR an active office access type
(read once per turn by `services.customer_scope_and_staff`; the lane sees `doc_staff`). A
linked dealer sees only its links'; staff are unchanged.

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
from tests.chatbot._turn_helpers import make_office_access_type
from tests.chatbot.test_outstanding_lane import CONTACT_ID, _run_turn, _seed_contact
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


def _seed_do(session_factory, number: str, customer_id: str | None, debtor_name: str | None = None) -> str:
    from app.models.order import Order
    from app.services.company_scope import DEFAULT_COMPANY_ID

    order_id = str(uuid.uuid4())
    db = session_factory()
    db.add(
        Order(
            id=order_id,
            order_number=number,
            customer_id=customer_id,
            debtor_name=debtor_name,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.commit()
    return order_id


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

    def test_office_access_type_staff_unchanged_gets_any_do(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        make_office_access_type(session_factory, CONTACT_ID)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_do(session_factory, DO_NO, foreign, debtor_name=FOREIGN)

        _reply, captured = _turn(session_factory, monkeypatch, _number_ask(DO_NO, "order"), f"status of {DO_NO}")

        assert _calls(captured, ORDERS), "office access type staff still get any customer's DO"


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

    def test_linked_contact_with_office_tier_stays_held_to_its_links(self, session_factory, monkeypatch) -> None:
        """Reviewer B1: the tier does not lift an enforced scope (linked, no office access
        type), exactly as the resolver's `_reference_scope` reads it and as main did."""
        _seed_contact(session_factory, variables={})
        _make_staff(session_factory)
        _link_customers(session_factory, OWN)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_so(session_factory, "SO421777", customer_id=foreign, debtor_name=FOREIGN, lines=[(1, 1)])

        reply, _captured = _turn(session_factory, monkeypatch, _number_ask("SO421777", "order_number"), "status of SO421777")

        assert FOREIGN not in reply and "delivered" not in reply, reply
        assert reply.strip() == f"{REFUSAL_PREFIX} I can only check on {OWN}.", reply

    def test_staff_unchanged_sees_any_so(self, session_factory, monkeypatch) -> None:
        """AC-SO-14 as the owner meant it: staff carry no customer scope."""
        _seed_contact(session_factory, variables={})
        _make_staff(session_factory)
        cust = _other_customer(session_factory, "ZZT ANY SDN BHD")
        _seed_so(session_factory, SO_NO, customer_id=cust, debtor_name="ZZT ANY SDN BHD", lines=[(10, 10)])

        reply, _ = _turn(session_factory, monkeypatch, _number_ask(SO_NO, "order_number"), f"status of {SO_NO}")

        assert SO_NO in reply and "ZZT ANY SDN BHD" in reply and "fully delivered" in reply, reply

    def test_office_access_type_staff_unchanged_sees_any_so(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        make_office_access_type(session_factory, CONTACT_ID)
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

    def test_office_access_type_staff_unchanged_lists_unscoped(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        make_office_access_type(session_factory, CONTACT_ID)

        _reply, captured = _turn(session_factory, monkeypatch, _list_ask(), "deliveries this month")

        (args,) = _calls(captured, ORDERS)
        assert not args.get("customer_ids"), args


# --------------------------------------------------------------------------- #
# Security review round 1: the other arms that list customer documents
# --------------------------------------------------------------------------- #

REPORT = "crm_outstanding_report"
PRODUCT_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PRODUCT_CODE = "SRTWT7445"


def _outstanding_turn(session_factory, monkeypatch):
    """"SRTWT7445 sales order outstanding": the outstanding report, whose rows carry DO / SO numbers
    and customer names (`outstanding_report_service`)."""
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=_parser_output(
            domain_hint="order",
            intent_hint="check_order",
            order_status="so_outstanding",
            entities=[
                {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        ),
        text_body=f"outstanding for {PRODUCT_CODE}",
        msg_id=f"ZZT-docscope-out-{uuid.uuid4().hex[:10]}",
        attributes=[OUTSTANDING_KEY],
        matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        mcp_response=FOREIGN_DO_LIST,
    )
    return ((result.reply or {}).get("text") or ""), captured


class TestOutstandingReport:
    def test_unlinked_non_staff_gets_no_report(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})

        reply, captured = _outstanding_turn(session_factory, monkeypatch)

        assert _calls(captured, REPORT) == [], captured
        assert reply.strip() == NO_LINK_REFUSAL, reply

    def test_staff_unchanged_gets_the_report(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        make_office_access_type(session_factory, CONTACT_ID)

        _reply, captured = _outstanding_turn(session_factory, monkeypatch)

        assert _calls(captured, REPORT), captured


def _so_list_fetch(session_factory, *, doc_staff: bool) -> tuple[str, list[str]]:
    """The lane's SO list arm ("all sales orders for ZZT FOREIGN"), called the way the runner
    calls it; the customer is one the contact named, not one of its links."""
    import json

    from app.services.chatbot.lanes import business
    from app.services.chatbot.lanes.business.services import FetchServices

    foreign = _other_customer(session_factory, FOREIGN)
    calls: list[str] = []

    def mcp_call(name, args):
        calls.append(name)
        return {"has_result": False}

    output = {
        "message_type": "business_query",
        "domain_hint": "order",
        "so_list": True,
        **({"doc_staff": True} if doc_staff else {}),
    }
    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": [{"uuid": foreign, "entity_type": "customer", "code": "", "display_name": FOREIGN}]},
        "ctx": {
            "parse": {"output": output},
            "contact": {"id": "900000008"},
            "access": {"allowed": True, "attributes": [OUTSTANDING_KEY]},
        },
    }
    fragment = business.run_fetch(
        payload, services=FetchServices(mcp_call=mcp_call), dry_run=False, db=session_factory()
    )
    return json.dumps(fragment, default=str), calls


class TestSoList:
    def test_unlinked_non_staff_gets_the_refusal_not_the_list(self, session_factory) -> None:
        said, calls = _so_list_fetch(session_factory, doc_staff=False)
        assert NO_LINK_REFUSAL in said, said
        assert FOREIGN not in said, said
        assert calls == [], calls

    def test_staff_unchanged_reaches_the_list(self, session_factory) -> None:
        """Staff pass the guard: the list asks its period, as before."""
        said, _calls_made = _so_list_fetch(session_factory, doc_staff=True)
        assert "Which period" in said and NO_LINK_REFUSAL not in said, said


class TestDidYouMeanAlternatives:
    def test_a_foreign_do_alternative_is_dropped(self, session_factory) -> None:
        """A mistyped DO number's trigram alternatives are offered as "did you mean"; another
        customer's DO must not be one of them."""
        from app.services.chatbot import turn_runtime

        _seed_contact(session_factory, variables={})
        foreign = _other_customer(session_factory, FOREIGN)
        order_id = _seed_do(session_factory, DO_NO, foreign, debtor_name=FOREIGN)
        db = TestPerMatchDropOnDeliveryOrders._db(session_factory)
        alt = {"uuid": order_id, "entity_type": "customer_order", "canonical_code": DO_NO}
        payload = {
            "resolved": {
                "tokens": ["202610-099"],
                "resolutions": [{"token": "202610-099", "matches": [], "alternatives": [alt]}],
                "intersection": [],
                "by_entity_type": {},
            }
        }

        out = turn_runtime._without_foreign_sales_orders(db, payload, [])

        (resolution,) = out["resolved"]["resolutions"]
        assert resolution["alternatives"] == [], resolution


class TestPerMatchDropOnDeliveryOrders:
    """Fix 1 on its own (the turn tests above are also held by the orders refusal): a DO match
    is kept only inside the links, by `so_status`'s scope rule."""

    @staticmethod
    def _db(session_factory):
        """A session scoped to the contact's company, as the engine's turn session is."""
        from app.models.base import set_company_scope
        from app.services.company_scope import DEFAULT_COMPANY_ID

        db = session_factory()
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        return db

    @staticmethod
    def _payload(order_id: str) -> dict[str, Any]:
        match = {"uuid": order_id, "entity_type": "customer_order", "canonical_code": DO_NO}
        return {
            "resolved": {
                "tokens": [DO_NO],
                "resolutions": [{"token": DO_NO, "matches": [match]}],
                "intersection": [],
                "by_entity_type": {"customer_order": [match]},
            },
            "gate": {"compatible_entities": [match]},
        }

    def test_no_links_drops_any_do_match(self, session_factory) -> None:
        from app.services.chatbot import turn_runtime

        _seed_contact(session_factory, variables={})
        order_id = _seed_do(session_factory, DO_NO, _other_customer(session_factory, FOREIGN), debtor_name=FOREIGN)

        out = turn_runtime._without_foreign_sales_orders(self._db(session_factory), self._payload(order_id), [])

        assert out["resolved"]["resolutions"][0]["matches"] == [], out
        assert out["resolved"]["by_entity_type"]["customer_order"] == [], out
        assert out["gate"]["compatible_entities"] == [], out
        assert DO_NO in out["resolved"]["unresolved_tokens"], out

    def test_own_do_match_is_kept(self, session_factory) -> None:
        from app.services.chatbot import turn_runtime

        _seed_contact(session_factory, variables={})
        own = _link_customers(session_factory, OWN)[0]
        order_id = _seed_do(session_factory, DO_NO, own, debtor_name=OWN)
        payload = self._payload(order_id)

        out = turn_runtime._without_foreign_sales_orders(self._db(session_factory), payload, [[own, OWN, ""]])

        assert out == payload, out


class TestCasualClarifierPrompt:
    def test_unlinked_non_staff_prompt_has_no_do_match(self, session_factory, monkeypatch, _stub_casual_llm) -> None:
        _seed_contact(session_factory, variables={})
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_do(session_factory, DO_NO, foreign, debtor_name=FOREIGN)
        qf = _parser_output(
            message_type="casual",
            domain_hint=None,
            intent_hint=None,
            user_goal=f"status of {DO_NO}",
            entities=[{"raw": DO_NO, "hint": "order", "canonical_code": None, "current_message": True, "confident": True}],
        )
        _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=f"status of {DO_NO}",
            msg_id=f"ZZT-docscope-casual-{uuid.uuid4().hex[:10]}",
            attributes=[OUTSTANDING_KEY], real_resolver=True,
            extra_completed_lanes=["low_signal"],
        )
        assert _stub_casual_llm, "the low_signal clarifier was never called"
        prompt = str(_stub_casual_llm[-1]["user_prompt"])
        assert "customer_order" not in prompt, prompt


class TestParserCannotGrantStaff:
    def test_a_doc_staff_key_in_the_verdict_grants_nothing(self, session_factory, monkeypatch) -> None:
        """The runner writes `doc_staff` itself, every turn: a parser verdict carrying
        `"doc_staff": true` (a provider that does not hold the schema) is overwritten."""
        _seed_contact(session_factory, variables={})

        reply, captured = _turn(
            session_factory, monkeypatch, {**_list_ask(), "doc_staff": True, "ref_staff": True},
            "deliveries this month",
        )

        assert _calls(captured, ORDERS) == [], captured
        assert reply.strip() == NO_LINK_REFUSAL, reply


class TestSecurityReviewRound2:
    def test_the_engine_strips_parser_emitted_staff_keys(self) -> None:
        """S-A: `engine` drops `required_fields.ENGINE_KEYS` off the parser's verdict before any
        lane reads it, so a parser-emitted `doc_staff` / `ref_staff` reaches no `run_fetch`
        (the tier-ask probe passes the parse output straight through)."""
        from app.services.chatbot import required_fields

        assert {"doc_staff", "ref_staff"} <= required_fields.ENGINE_KEYS

    def test_linked_contact_with_office_tier_gets_no_foreign_po_ref(self, session_factory, monkeypatch) -> None:
        """S-B: the PO-ref arm holds an enforced contact to its links whatever its tier, like
        the SO number arm; another customer's PO ref is a silent miss."""
        from tests.chatbot.test_po_ref_ask import _seed_po_so

        _seed_contact(session_factory, variables={})
        _make_staff(session_factory)
        _link_customers(session_factory, OWN)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_po_so(session_factory, "SOZZT200001", customer_id=foreign, po_ref="4500999", po_ref_key="4500999", debtor_name=FOREIGN)

        result, _captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_number_ask("4500999", "customer_po_ref"),
            text_body="status of 4500999",
            msg_id=f"ZZT-docscope-poref-{uuid.uuid4().hex[:10]}",
            attributes=[OUTSTANDING_KEY],
            real_resolver=True,
            mcp_response=FOREIGN_DO_LIST,
        )
        reply = (result.reply or {}).get("text") or ""

        assert FOREIGN not in reply and "SOZZT200001" not in reply, reply
