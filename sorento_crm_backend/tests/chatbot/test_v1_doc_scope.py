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
from sqlalchemy import text

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


# --------------------------------------------------------------------------- #
# V1-DOC-SCOPE-SALES: salespeople keep document access
# --------------------------------------------------------------------------- #
#
# Crew rulings (14 Oct 2026) on the owner's prod check: many unlinked non-staff contacts are
# salespeople. For document scope a salesperson is staff, and a salesperson is ONLY an ACTIVE,
# not trashed CRM user linked to the contact (`users.respond_contact_id`) holding the not
# trashed role `salesperson` (owner rule 11 Oct; security review: deactivating the user must
# revoke it, and a `sales_agents.contact_id` link, editable under master data, grants
# nothing). Unlinked, a salesperson sees any customer's DO / SO as main does today; linked, it
# is lifted only on the in-process number lookups. Anyone else unlinked and not staff stays
# blocked. A customer PO ref keeps the tier rule (`ref_staff`) everywhere.


def _contact(db):
    from app.models.access import RespondContact

    return db.query(RespondContact).filter(RespondContact.respond_io_id == str(CONTACT_ID)).one()


def _make_salesperson(
    session_factory,
    *,
    status: str = "ACTIVE",
    trashed: bool = False,
    role: str = "salesperson",
    role_trashed: bool = False,
) -> None:
    """Link a CRM user holding `role` to the seeded contact (`CONTACT_ID`). The defaults make
    the one salesperson that has document access: ACTIVE, not trashed, role `salesperson`."""
    from tests._chat_asker_seed import give_role, link_user

    db = session_factory()
    user = link_user(db, _contact(db), status=status, trashed=trashed, name="ZZT Sales User")
    give_role(db, user, role, role_trashed=role_trashed)
    db.commit()


def _link_agent(session_factory) -> None:
    """A sales agent linked to the seeded contact (`sales_agents.contact_id`)."""
    from tests._chat_asker_seed import link_agent

    db = session_factory()
    link_agent(db, _contact(db), f"ZZT AGENT {uuid.uuid4().hex[:6].upper()}")
    db.commit()


class TestSalespersonUnlinked:
    """An unlinked salesperson sees what main shows it today."""

    def test_sees_any_so_by_number(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory)
        cust = _other_customer(session_factory, "ZZT ANY SDN BHD")
        _seed_so(session_factory, SO_NO, customer_id=cust, debtor_name="ZZT ANY SDN BHD", lines=[(10, 10)])

        reply, _ = _turn(session_factory, monkeypatch, _number_ask(SO_NO, "order_number"), f"status of {SO_NO}")

        assert SO_NO in reply and "ZZT ANY SDN BHD" in reply and "fully delivered" in reply, reply

    def test_sees_any_do_by_number(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_do(session_factory, DO_NO, foreign, debtor_name=FOREIGN)

        _reply, captured = _turn(session_factory, monkeypatch, _number_ask(DO_NO, "order"), f"status of {DO_NO}")

        assert _calls(captured, ORDERS), "a salesperson still gets any customer's DO through the orders list"

    def test_lists_dos_unscoped(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory)

        reply, captured = _turn(session_factory, monkeypatch, _list_ask(), "deliveries this month")

        (args,) = _calls(captured, ORDERS)
        assert not args.get("customer_ids"), args
        assert reply.strip() != NO_LINK_REFUSAL, reply

    def test_gets_the_outstanding_report(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory)

        _reply, captured = _outstanding_turn(session_factory, monkeypatch)

        assert _calls(captured, REPORT), captured


class TestSalespersonLinked:
    """A linked salesperson is not held to its links on the in-process number lookups (owner
    rule 11 Oct: the customer link does not make it a dealer)."""

    def test_sees_another_customers_so_by_number(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory)
        _link_customers(session_factory, OWN)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_so(session_factory, "SO421777", customer_id=foreign, debtor_name=FOREIGN, lines=[(10, 10)])

        reply, _ = _turn(session_factory, monkeypatch, _number_ask("SO421777", "order_number"), "status of SO421777")

        assert "SO421777" in reply and FOREIGN in reply and "fully delivered" in reply, reply
        assert not reply.strip().startswith(REFUSAL_PREFIX), reply
        # The resolver kept the match (`engine._reference_scope` does not drop it), so the
        # card head names the kind, as it does for staff (REF-PROBES).
        assert reply.strip() == _card("Sales order SO421777", "Open", "fully delivered", name=FOREIGN), reply

    def test_scope_counts_as_staff_but_stays_enforced(self, session_factory) -> None:
        """Crew ruling (a), 14 Oct: the salesperson is document staff (lifted on the in-process
        number lookups), while `enforced` stays the core flag, so the customer-word gate, the
        lists and the order routes keep it on its links as main does
        (tests/chatbot/test_chat_report_scope_leak.py)."""
        from app.services.chatbot.lanes.business import services

        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory)
        own = _link_customers(session_factory, OWN)

        scope, staff = services.customer_scope_and_staff(session_factory(), CONTACT_ID, None)

        assert staff is True
        assert scope is not None and scope["ids"] == own, scope
        assert scope["enforced"] is True and scope["staff"] is True, scope

    def test_a_linked_dealer_is_still_enforced(self, session_factory) -> None:
        """Guard: the salesperson read does not lift an ordinary dealer."""
        from app.services.chatbot.lanes.business import services

        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, OWN)

        scope, staff = services.customer_scope_and_staff(session_factory(), CONTACT_ID, None)

        assert staff is False
        assert scope is not None and scope["enforced"] is True, scope

    def test_a_sales_agent_link_does_not_lift_a_dealer(self, session_factory) -> None:
        """Security review (14 Oct): `master_data.sales_agents.edit` can point an agent code at
        any contact; that must not make a dealer document staff."""
        from app.services.chatbot.lanes.business import services

        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, OWN)
        _link_agent(session_factory)

        scope, staff = services.customer_scope_and_staff(session_factory(), CONTACT_ID, None)

        assert staff is False
        assert scope is not None and scope["staff"] is False and scope["enforced"] is True, scope


#: Each way a contact can look like a salesperson without being one for document access.
_NOT_A_SALESPERSON = {
    "agent_link_only": lambda sf: _link_agent(sf),
    "inactive_user_and_agent_link": lambda sf: (_make_salesperson(sf, status="INACTIVE"), _link_agent(sf)),
    "trashed_user_and_agent_link": lambda sf: (_make_salesperson(sf, trashed=True), _link_agent(sf)),
    "other_role_user_and_agent_link": lambda sf: (_make_salesperson(sf, role="warehouse_manager"), _link_agent(sf)),
    "inactive_user": lambda sf: _make_salesperson(sf, status="INACTIVE"),
    "trashed_role": lambda sf: _make_salesperson(sf, role_trashed=True),
}


class TestNotASalespersonStaysBlocked:
    """Only an ACTIVE, not trashed user holding the not trashed role `salesperson` lifts the
    block (crew ruling (a), 14 Oct): deactivating or trashing the user revokes it, and a
    sales agent link grants nothing."""

    @pytest.mark.parametrize("make", list(_NOT_A_SALESPERSON), ids=list(_NOT_A_SALESPERSON))
    def test_unlinked_lists_nothing(self, session_factory, monkeypatch, make) -> None:
        _seed_contact(session_factory, variables={})
        _NOT_A_SALESPERSON[make](session_factory)

        reply, captured = _turn(session_factory, monkeypatch, _list_ask(), "deliveries this month")

        assert _calls(captured, ORDERS) == [], captured
        assert reply.strip() == NO_LINK_REFUSAL, reply

    @pytest.mark.parametrize("make", ["agent_link_only", "inactive_user_and_agent_link"])
    def test_unlinked_gets_no_foreign_so(self, session_factory, monkeypatch, make) -> None:
        _seed_contact(session_factory, variables={})
        _NOT_A_SALESPERSON[make](session_factory)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_so(session_factory, SO_NO, customer_id=foreign, debtor_name=FOREIGN, lines=[(10, 10)])

        reply, _ = _turn(session_factory, monkeypatch, _number_ask(SO_NO, "order_number"), f"status of {SO_NO}")

        assert FOREIGN not in reply and reply.strip() == NO_LINK_REFUSAL, reply

    def test_deactivating_the_user_revokes_the_staff_bit(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import services

        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory, status="INACTIVE")
        _link_agent(session_factory)

        _scope, staff = services.customer_scope_and_staff(session_factory(), CONTACT_ID, None)

        assert staff is False


# --------------------------------------------------------------------------- #
# V1-DOC-SCOPE-SALES security review nit 3: a customer PO ref keeps the tier rule
# --------------------------------------------------------------------------- #

PO_REF = "4500999"
PO_REF_SO = "SOZZT200001"


class TestPoRefKeepsTheTierRule:
    """`ref_staff` (the tier alone) decides who may see another customer's PO ref, in the lane
    (as main) AND in the resolver / clarifier per-match drop: a salesperson is document staff
    for SO / DO numbers, not for PO refs, so it never learns which SO another customer's PO
    ref is."""

    def test_the_drop_holds_po_refs_to_their_own_scope(self, session_factory) -> None:
        """A PO ref match is held to `po_ref_linked` while an SO match of the same payload,
        under no scope, is kept."""
        from app.services.chatbot import turn_runtime

        _seed_contact(session_factory, variables={})
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_so(session_factory, SO_NO, customer_id=foreign, debtor_name=FOREIGN, lines=[(1, 1)])
        _seed_so(session_factory, PO_REF_SO, customer_id=foreign, debtor_name=FOREIGN, lines=[(1, 1)])
        so_id, po_id = (
            str(session_factory().execute(text("SELECT id FROM sales_orders WHERE so_number = :n"), {"n": n}).scalar_one())
            for n in (SO_NO, PO_REF_SO)
        )
        so_match = {"uuid": so_id, "entity_type": "sales_order", "canonical_code": SO_NO}
        po_match = {"uuid": po_id, "entity_type": "customer_po_ref", "canonical_code": PO_REF_SO}
        payload = {
            "resolved": {
                "tokens": [SO_NO, PO_REF],
                "resolutions": [
                    {"token": SO_NO, "matches": [so_match]},
                    {"token": PO_REF, "matches": [po_match]},
                ],
                "intersection": [],
                "by_entity_type": {"sales_order": [so_match], "customer_po_ref": [po_match]},
            },
            "gate": {"compatible_entities": [so_match, po_match]},
        }

        out = turn_runtime._without_foreign_sales_orders(
            TestPerMatchDropOnDeliveryOrders._db(session_factory), payload, None, po_ref_linked=[]
        )

        so_res, po_res = out["resolved"]["resolutions"]
        assert so_res["matches"] == [so_match], out
        assert po_res["matches"] == [], out
        assert out["gate"]["compatible_entities"] == [so_match], out

    def test_unlinked_salesperson_clarifier_prompt_has_no_foreign_po_ref(
        self, session_factory, monkeypatch, _stub_casual_llm
    ) -> None:
        from tests.chatbot.test_po_ref_ask import _seed_po_so

        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_po_so(session_factory, PO_REF_SO, customer_id=foreign, po_ref=PO_REF, po_ref_key=PO_REF, debtor_name=FOREIGN)
        qf = _parser_output(
            message_type="casual",
            domain_hint=None,
            intent_hint=None,
            user_goal=f"status of {PO_REF}",
            entities=[{"raw": PO_REF, "hint": "customer_po_ref", "canonical_code": None, "current_message": True, "confident": True}],
        )
        _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=f"status of {PO_REF}",
            msg_id=f"ZZT-docscope-casual-po-{uuid.uuid4().hex[:10]}",
            attributes=[OUTSTANDING_KEY], real_resolver=True,
            extra_completed_lanes=["low_signal"],
        )
        assert _stub_casual_llm, "the low_signal clarifier was never called"
        prompt = str(_stub_casual_llm[-1]["user_prompt"])
        assert PO_REF_SO not in prompt and "customer_po_ref" not in prompt, prompt

    def test_unlinked_salesperson_resolver_holds_po_refs_to_no_links(self, session_factory, monkeypatch) -> None:
        """The main resolver path passes the tier rule's links for PO refs (`[]` for an
        unlinked contact that is not office tier) beside the document staff's `None`."""
        from tests.chatbot.test_po_ref_ask import _seed_po_so
        from app.services.chatbot import turn_runtime

        _seed_contact(session_factory, variables={})
        _make_salesperson(session_factory)
        foreign = _other_customer(session_factory, FOREIGN)
        _seed_po_so(session_factory, PO_REF_SO, customer_id=foreign, po_ref=PO_REF, po_ref_key=PO_REF, debtor_name=FOREIGN)
        seen: list[dict[str, Any]] = []
        real = turn_runtime._without_foreign_sales_orders

        def spy(db, payload, linked, **kwargs):
            seen.append({"linked": linked, **kwargs})
            return real(db, payload, linked, **kwargs)

        monkeypatch.setattr(turn_runtime, "_without_foreign_sales_orders", spy)

        reply, _captured = _turn(
            session_factory, monkeypatch, _number_ask(PO_REF, "customer_po_ref"), f"status of {PO_REF}"
        )

        assert seen and seen[0] == {"linked": None, "po_ref_linked": []}, seen
        assert FOREIGN not in reply and PO_REF_SO not in reply, reply


# --------------------------------------------------------------------------- #
# V1-DOC-SCOPE slice 3 (crew audit at 54740870f): the customer picker
# --------------------------------------------------------------------------- #
#
# An unlinked non-staff contact typing another customer's partial name reached the generic
# resolver: its customer picker listed other customers' names, and the picker's DO probe
# (`resolve_gate.CUSTOMER_PROBE_TOOL`, outside `run_fetch`'s guard) told it which of them have
# delivery orders. Owner rule (unlinked != customer): such a word is another customer's, so
# the turn is refused before the resolver, exactly as an enforced dealer's word outside its
# links is (`engine._customer_scope_gate`), with the existing no-link line. Document staff
# (tier, office access type, salesperson) are unchanged.


def _picker_turn(session_factory, monkeypatch, hint: str | None = "customer"):
    """"deliveries this month for hanlim": a DO ask whose customer word matches two other
    customers (the R18 HANLIM book), so the generic resolver would offer a picker. `hint` is
    the parser's tag on the word: under `order` the resolver reads a brand or category word as
    the customer it names (`entity_resolver._DOMAIN_HINT_EXPANSIONS`), and the fake resolver
    here answers customers whatever the tag."""
    from tests.chatbot.test_customer_scope_lane import _ent
    from tests.chatbot.test_outstanding_lane import _ambiguous_hanlim_resolve_services

    probes: list[dict[str, Any]] = []
    asked: list[list[str]] = []

    def probe(**kwargs):
        probes.append(kwargs)
        return {"items": [], "has_result": False}

    services = _ambiguous_hanlim_resolve_services(probe)
    inner = services.resolve_entity

    def resolve_entity(body):
        asked.append([str(t) for t in (body.get("tokens") or [])])
        return inner(body)

    services = services.__class__(access_types=services.access_types, resolve_entity=resolve_entity, probe=services.probe)
    qf = _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[{**_ent("hanlim"), "hint": hint}],
        date_filter_start="2026-10-01",
        date_filter_end="2026-10-31",
    )
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=qf,
        text_body="deliveries this month for hanlim",
        msg_id=f"ZZT-docscope-picker-{uuid.uuid4().hex[:10]}",
        attributes=[OUTSTANDING_KEY],
        resolve_services=services,
        mcp_response=FOREIGN_DO_LIST,
    )
    return ((result.reply or {}).get("text") or ""), captured, probes, asked


class TestCustomerPickerUnlinked:
    def test_unlinked_non_staff_gets_no_picker_names_and_no_probe(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})

        reply, captured, probes, asked = _picker_turn(session_factory, monkeypatch)

        assert "HANLIM" not in reply.upper(), reply
        assert reply.strip() == NO_LINK_REFUSAL, reply
        assert probes == [], probes
        assert _calls(captured, ORDERS) == [], captured
        assert not any("hanlim" in t.lower() for tokens in asked for t in tokens), asked

    @pytest.mark.parametrize(
        "make_staff",
        [
            lambda sf: make_office_access_type(sf, CONTACT_ID),
            lambda sf: _make_salesperson(sf),
            lambda sf: _make_staff(sf),
        ],
        ids=["office_access_type", "salesperson", "office_tier"],
    )
    def test_document_staff_keep_the_picker(self, session_factory, monkeypatch, make_staff) -> None:
        _seed_contact(session_factory, variables={})
        make_staff(session_factory)

        reply, _captured, _probes, _asked = _picker_turn(session_factory, monkeypatch)

        assert "Which one do you mean?" in reply, reply
        assert "HANLIM TRADING" in reply.upper() and "HANLIM HARDWARE" in reply.upper(), reply
        assert NO_LINK_REFUSAL not in reply, reply

    @pytest.mark.parametrize("hint", ["brand", "category", "product", "order", None])
    def test_a_word_the_resolver_reads_as_a_customer_gets_no_picker_and_no_probe(
        self, session_factory, monkeypatch, hint
    ) -> None:
        """Security review (slice 3): a word the parser did not tag `customer` still reaches
        the resolver, which answers it with other customers; those rows are dropped before
        the picker is built, so no name and no DO probe, and the turn gets the no-link line."""
        _seed_contact(session_factory, variables={})

        reply, captured, probes, _asked = _picker_turn(session_factory, monkeypatch, hint=hint)

        assert "HANLIM" not in reply.upper(), reply
        assert reply.strip() == NO_LINK_REFUSAL, reply
        assert probes == [], probes
        assert _calls(captured, ORDERS) == [], captured

    def test_document_staff_keep_the_picker_for_a_word_read_as_a_customer(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        make_office_access_type(session_factory, CONTACT_ID)

        reply, _captured, _probes, _asked = _picker_turn(session_factory, monkeypatch, hint="brand")

        assert "Which one do you mean?" in reply, reply
        assert "HANLIM TRADING" in reply.upper() and "HANLIM HARDWARE" in reply.upper(), reply
