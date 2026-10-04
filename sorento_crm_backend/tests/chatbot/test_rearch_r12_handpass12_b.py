"""Hand pass 12 RED tests, SECOND batch (tester 51, hand pass 12, continuing tester
50's `test_rearch_r12_handpass12.py` - imported from rather than copy-pasted, per the
harness convention that file's own module docstring already states). No implementation
for any of this exists yet.

Two groups:

GROUP C - rerun on a RECORD KEY miss (owner ruling, 21 Sep 2026). `app/services/chatbot/
turn/decide.py:427-478`'s own REFINE table stays exactly as it is: a message with
entities and no domain word combines the carried subject with what it types
(`domain_in_message: false` + entities -> REFINE, `refines_standing_subject`). What is
NEW is the ANSWERING side: when that combined call MISSES and the entity typed in THIS
message is the domain's own RECORD KEY (incoming: `inbound_shipment` - a container/
shipment number; order: an order number, hints `order`/`customer_order`; PO: a PO
number), the engine reruns ONCE with ONLY the current message's own entities (every
carried filter dropped), and the reply says what was dropped. A typed FILTER (customer,
product, date, brand) never triggers a rerun - C3/C4/C6 are green guards pinning that.

Turns replayed: 5fbc9ee3 ("INCOMING SRTJC1303-R", carries product SRTJC1303-R onto
focus in domain incoming) then 0064e1b5 ("TCNU3167091" - a container/shipment number,
hint `inbound_shipment`, `continuation: true`) - the live trace's own recorded verdict
and tool-call args (`.claude/handpass/hp12-turns-21sep.json`) show the COMBINED call
carrying both `product_ids` AND `shipment_ids` and MISSING ("But no incoming matched
these. Would you like me to escalate to purchasing team?"); turn 834d9bdc ("for all
products") is the live shipment-only HIT this pass's C1 borrows the envelope shape of
(a real container with several product rows). Turn 229e3344 ("only TCNU3167091") is
C7(i)'s own base verdict, entity_op forced from its OWN recorded "replace_combine" to
"replace" for that one test.

GROUP B2CD - customer service escalation always shows the member picker (owner ruling,
relayed after tester 50's B2(a), which retired the did-you-mean-miss reply's own second
numbered list). See `TestGroupB2CDCustomerServiceEscalationAlwaysShowsTheMemberPicker`'s
own docstring for the measured "no roster-fetch call exists yet" finding and the outcome-
only assertion discipline that finding calls for.

Harness: `test_rearch_r12_handpass12.py`'s own helpers, imported directly - `_focus`,
`_seed_state`, `_state_of`, `_said`, `_order_row`, `_order_envelope`, `_unknown_envelope`,
the tool name constants, and `TestGroupBDidYouMeanPickByTypedLabelsReachesTheTool` (its
`_seed_pending` is reused for B2(a)'s own did-you-mean-plus-CS-offer starting state,
rather than a second copy of that fixture). `_mcp_double` / `_seed_contact_and_get` /
`_real_resolve_with_safe_probe` from `test_rearch_r5_production_decides.py`, `_run_turn_
engine` from `test_rearch_r6_review_round.py` - the SAME "real resolver/gate/narrower
over seeded Postgres rows, only the MCP boundary doubled" convention every hand pass 12
file already uses.

Postgres only (`session_factory`, blank schema). Every row seeded fresh per test; no row
is borrowed from another test or from any live/clone data.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date
from typing import Any

from app.models.order import Customer, Order
from app.models.procurement import InboundShipment
from app.services.chatbot.lanes.business.fetch import ORDER_TOOLS as ORDER_TOOLS_LOCAL
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_rearch_r5_production_decides import (
    _mcp_double,
    _real_resolve_with_safe_probe,
    _seed_contact_and_get,
)
from tests.chatbot.test_rearch_r6_review_round import _run_turn_engine
from tests.chatbot.test_rearch_r12_handpass12 import (
    INCOMING_TOOL,
    _focus,
    _order_envelope,
    _order_row,
    _said,
    _seed_state,
    _state_of,
    _unknown_envelope,
)
from tests.chatbot.test_rearch_r12_handpass12 import (
    TestGroupBDidYouMeanPickByTypedLabelsReachesTheTool as _DidYouMeanPendingFixture,
)

# --------------------------------------------------------------------------- #
# Shared fixtures - GROUP C's own envelope/seed builders. The order-side ones
# (`_order_row`/`_order_envelope`) are the sibling file's; incoming needs its own
# because that file's `_incoming_hit` hardcodes ONE product and ONE fixed container
# ("ZZTCONT1") - group C needs a variable container per test and, for C1, TWO
# distinct product codes in the same envelope (the carried one dropped, another
# one shown).
# --------------------------------------------------------------------------- #


def _incoming_row(code: str, container: str) -> dict[str, Any]:
    return {
        "flags": {},
        "title": code,
        "fields": [
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "shipping_container_number", "label": "Container", "value": container},
            {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-20"},
        ],
    }


def _incoming_envelope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "intro": "Here is the incoming stock I found." if rows else "No matching results found.",
        "items": rows,
        "has_result": bool(rows),
        "attachments": [],
        "result_type": "incoming",
        "action_links": [],
    }


def _seed_shipment(session_factory: Any, *, container: str) -> str:
    """A real `inbound_shipments` row the REAL resolver can find by container number
    (`entity_resolver.py::_probe_inbound_shipment`, exact case/whitespace-insensitive
    match on `shipping_container_number`)."""
    db = session_factory()
    row = InboundShipment(
        id=str(uuid.uuid4()),
        shipment_number=unique_code("SHIP")[:50],
        shipping_container_number=container,
        shipment_date=date(2026, 9, 1),
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.commit()
    return row.id


def _seed_customer(session_factory: Any, *, name: str) -> str:
    db = session_factory()
    row = Customer(
        id=str(uuid.uuid4()),
        customer_code=unique_code("CUST")[:50],
        customer_name=name,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.commit()
    return row.id


def _seed_order(session_factory: Any, *, order_number: str, customer_id: str | None) -> str:
    db = session_factory()
    row = Order(
        id=str(uuid.uuid4()),
        order_number=order_number,
        customer_id=customer_id,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.commit()
    return row.id


# --------------------------------------------------------------------------- #
# GROUP C - rerun on a RECORD KEY miss; a FILTER never reruns.
# --------------------------------------------------------------------------- #


class TestGroupCRerunOnMissForTypedRecordKey:
    def test_c1_container_typed_after_carried_product_reruns_shipment_only_on_miss(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turn 5fbc9ee3 -> 0064e1b5's own shape: focus carries product
        SRTJC1303-R in domain incoming; the customer types a container number
        (hint `inbound_shipment`, `continuation: true`, `entity_op: replace_combine`
        UNCHANGED from the live trace - the REFINE table itself is not touched).
        Measured directly off the live trace: the combined call carries BOTH
        `product_ids` and `shipment_ids` and misses. New rule: the engine reruns
        ONCE with `shipment_ids` alone (the carried product dropped), and the reply
        names both the dropped product and the container in one sentence, before
        the rerun's own rows (mirroring 834d9bdc's own shipment-only HIT shape:
        several products under one container)."""
        _seed_contact_and_get(session_factory)
        code = unique_code("C1PROD")
        product_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        container = unique_code("CONT")
        _seed_shipment(session_factory, container=container)
        other_code = unique_code("C1OTHER")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=other_code)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                products=[
                    {
                        "raw": code,
                        "hint": "product",
                        "uuid": product_id,
                        "company_name": "Sorento",
                        "canonical_code": code,
                    }
                ],
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=False,
            continuation=True,
            entity_op="replace_combine",
            entities=[
                {
                    "raw": container,
                    "hint": "inbound_shipment",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                if args.get("product_ids") and args.get("shipment_ids"):
                    return json.dumps(_incoming_envelope([]))
                if args.get("shipment_ids") and not args.get("product_ids"):
                    return json.dumps(
                        _incoming_envelope([_incoming_row(other_code, container)])
                    )
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=container,
            msg_id="zzt-c1-rerun",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        incoming_calls = [args for name, args in fetch_calls if name == INCOMING_TOOL]
        assert len(incoming_calls) == 2, (
            f"a RECORD KEY miss must rerun exactly once: {fetch_calls!r}"
        )
        assert "shipment_ids" in incoming_calls[1], incoming_calls[1]
        assert "product_ids" not in incoming_calls[1], (
            f"the rerun must drop every carried filter, keeping only the current "
            f"message's own entities: {incoming_calls[1]!r}"
        )

        lines = said.split("\n")
        combo_lines = [ln for ln in lines if code in ln and container in ln]
        assert combo_lines, (
            f"the product AND the container must both appear in one sentence, "
            f"before the rerun's own rows: {said!r}"
        )
        assert other_code in said, f"the rerun's own rows must print: {said!r}"
        assert "escalate" not in said.lower(), (
            f"a successful rerun must not still offer to escalate: {said!r}"
        )

        state = _state_of(session_factory)
        products_after = (state.get("focus") or {}).get("products") or []
        assert not any(p.get("canonical_code") == code for p in products_after), (
            f"the dropped product must not still ride the focus: {products_after!r}"
        )

    def test_c2_combined_call_hits_no_rerun(self, session_factory, monkeypatch) -> None:
        """Same start as C1, but the COMBINED call HITS: exactly one tool call, no
        rerun, reply as today (probably green)."""
        _seed_contact_and_get(session_factory)
        code = unique_code("C2PROD")
        product_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        container = unique_code("CONT")
        _seed_shipment(session_factory, container=container)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                products=[
                    {
                        "raw": code,
                        "hint": "product",
                        "uuid": product_id,
                        "company_name": "Sorento",
                        "canonical_code": code,
                    }
                ],
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=False,
            continuation=True,
            entity_op="replace_combine",
            entities=[
                {
                    "raw": container,
                    "hint": "inbound_shipment",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                return json.dumps(_incoming_envelope([_incoming_row(code, container)]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=container,
            msg_id="zzt-c2-hit-no-rerun",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        incoming_calls = [args for name, args in fetch_calls if name == INCOMING_TOOL]
        assert len(incoming_calls) == 1, (
            f"a combined call that HITS must never rerun: {fetch_calls!r}"
        )
        assert code in said and container in said, said

    def test_c3_order_domain_customer_carried_product_typed_filter_no_rerun(
        self, session_factory, monkeypatch
    ) -> None:
        """A typed PRODUCT over a carried customer is a FILTER, never a RECORD KEY -
        exactly ONE orders tool call on a miss, no rerun. Expected green today; it
        is the guard."""
        _seed_contact_and_get(session_factory)
        customer_id = _seed_customer(session_factory, name="ZZT C3 CUSTOMER")
        code = unique_code("C3PROD")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["order"],
                customers=[
                    {
                        "raw": "ZZT C3 CUSTOMER",
                        "hint": "customer",
                        "uuid": customer_id,
                        "canonical_code": "ZZT C3 CUSTOMER",
                    }
                ],
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_order",
            domain_hint="order",
            domain_in_message=False,
            entity_op="replace_combine",
            entities=[
                {
                    "raw": code,
                    "hint": "product",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
            routing={
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name in ORDER_TOOLS_LOCAL:
                return json.dumps(_order_envelope([]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=code,
            msg_id="zzt-c3-filter-no-rerun",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        order_calls = [args for name, args in fetch_calls if name in ORDER_TOOLS_LOCAL]
        assert len(order_calls) == 1, (
            f"a typed PRODUCT over a carried customer is a FILTER - it must never "
            f"trigger a rerun: {fetch_calls!r}"
        )

    def test_c4_customer_and_product_typed_together_no_rerun(
        self, session_factory, monkeypatch
    ) -> None:
        """Customer AND product typed in the SAME message, miss: one tool call,
        normal miss (both are filters, green guard)."""
        _seed_contact_and_get(session_factory)
        _seed_customer(session_factory, name="ZZT C4 CUSTOMER")
        code = unique_code("C4PROD")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        _seed_state(session_factory, focus=_focus(domains=["order"]))

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_order",
            domain_hint="order",
            domain_in_message=True,
            entity_op="replace_combine",
            entities=[
                {
                    "raw": "ZZT C4 CUSTOMER",
                    "hint": "customer",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                },
                {
                    "raw": code,
                    "hint": "product",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                },
            ],
            document=[],
            status=None,
            order_status=None,
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
            routing={
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name in ORDER_TOOLS_LOCAL:
                return json.dumps(_order_envelope([]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=f"ZZT C4 CUSTOMER {code}",
            msg_id="zzt-c4-both-typed-no-rerun",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        order_calls = [args for name, args in fetch_calls if name in ORDER_TOOLS_LOCAL]
        assert len(order_calls) == 1, (
            f"a customer typed alongside a product is still two FILTERS, never a "
            f"RECORD KEY - no rerun: {fetch_calls!r}"
        )

    def test_c5_order_number_typed_under_another_customer_reruns_order_only(
        self, session_factory, monkeypatch
    ) -> None:
        """Focus carries a customer in domain order; an order number that EXISTS
        but belongs to ANOTHER customer is typed (a RECORD KEY, REFINE), the
        combined call misses, the order-number-only call hits. Rerun with only the
        order id (the carried customer filter dropped); the reply says the order is
        not under the carried customer."""
        _seed_contact_and_get(session_factory)
        carried_customer_id = _seed_customer(session_factory, name="ZZT C5 CARRIED CO")
        other_customer_id = _seed_customer(session_factory, name="ZZT C5 OTHER CO")
        code = unique_code("C5PROD")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        order_number = unique_code("ORD")
        _seed_order(session_factory, order_number=order_number, customer_id=other_customer_id)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["order"],
                customers=[
                    {
                        "raw": "ZZT C5 CARRIED CO",
                        "hint": "customer",
                        "uuid": carried_customer_id,
                        "canonical_code": "ZZT C5 CARRIED CO",
                    }
                ],
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_order",
            domain_hint="order",
            domain_in_message=False,
            continuation=True,
            entity_op="replace_combine",
            entities=[
                {
                    "raw": order_number,
                    "hint": "order",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name in ORDER_TOOLS_LOCAL:
                if args.get("customer_ids") and args.get("order_ids"):
                    return json.dumps(_order_envelope([]))
                if args.get("order_ids") and not args.get("customer_ids"):
                    return json.dumps(_order_envelope([_order_row(order_number, code)]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=order_number,
            msg_id="zzt-c5-order-rerun",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        order_calls = [args for name, args in fetch_calls if name in ORDER_TOOLS_LOCAL]
        assert len(order_calls) == 2, (
            f"an order-number RECORD KEY miss must rerun exactly once: {fetch_calls!r}"
        )
        assert "order_ids" in order_calls[1], order_calls[1]
        assert "customer_ids" not in order_calls[1], (
            f"the rerun must drop the carried customer filter: {order_calls[1]!r}"
        )

        assert order_number in said, f"the rerun's own order must print: {said!r}"
        assert "ZZT C5 CARRIED CO" in said, (
            f"the reply must say the order is not under the carried customer, "
            f"naming it: {said!r}"
        )

    def test_c6_incoming_container_carried_product_typed_filter_no_rerun(
        self, session_factory, monkeypatch
    ) -> None:
        """Focus carries a container (incoming); a PRODUCT is typed, combined
        misses: one tool call, normal miss (product is a filter; green guard)."""
        _seed_contact_and_get(session_factory)
        container = unique_code("CONT")
        _seed_shipment(session_factory, container=container)
        code = unique_code("C6PROD")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                extra={
                    "order": [],
                    "category": [],
                    "customer_order": [],
                    "attachment_type": [],
                    "inbound_shipment": [
                        {
                            "raw": container,
                            "hint": "inbound_shipment",
                            "confident": True,
                            "canonical_code": container,
                            "hint_confident": True,
                            "current_message": False,
                        }
                    ],
                },
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=False,
            entity_op="replace_combine",
            entities=[
                {
                    "raw": code,
                    "hint": "product",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                return json.dumps(_incoming_envelope([]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=code,
            msg_id="zzt-c6-filter-no-rerun",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        incoming_calls = [args for name, args in fetch_calls if name == INCOMING_TOOL]
        assert len(incoming_calls) == 1, (
            f"a typed PRODUCT over a carried container is a FILTER - it must never "
            f"trigger a rerun: {fetch_calls!r}"
        )


class TestGroupC7OnlyXReadByTheLLMNotByCode:
    """C7 ("only X" / "just X"), owner ruling mid-task: the model reads this intent,
    not deterministic code. Two tests: (i) the CODE side - `decide.py:420-425`'s own
    `entity_op_replace` row already does the right thing once `entity_op: "replace"`
    arrives (probably green); (ii) the PROMPT side - RED, the missing half."""

    def test_c7i_entity_op_replace_drops_the_carried_product_on_the_first_call(
        self, session_factory, monkeypatch
    ) -> None:
        """Turn 229e3344's own shape ("only TCNU3167091"), with `entity_op` forced
        from its OWN recorded "replace_combine" to "replace" (the value `turn/
        decide.py:505`'s own `replaces_every_axis` flag reads) - the value the
        PROMPT never actually instructs the parser to emit (see (ii) below), but
        the value the CODE already honours when it does arrive: `decide.py:420`'s
        own `replaces_every_axis and entities` row outranks the REFINE table
        entirely, so the FIRST call should carry `shipment_ids` alone, no combine,
        no rerun needed."""
        _seed_contact_and_get(session_factory)
        code = unique_code("C7PROD")
        product_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        container = unique_code("CONT")
        _seed_shipment(session_factory, container=container)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                products=[
                    {
                        "raw": code,
                        "hint": "product",
                        "uuid": product_id,
                        "company_name": "Sorento",
                        "canonical_code": code,
                    }
                ],
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=False,
            continuation=False,
            scope_intent="specific",
            entity_op="replace",
            entities=[
                {
                    "raw": container,
                    "hint": "inbound_shipment",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                return json.dumps(_incoming_envelope([_incoming_row(code, container)]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=f"only {container}",
            msg_id="zzt-c7i-entity-op-replace",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        incoming_calls = [args for name, args in fetch_calls if name == INCOMING_TOOL]
        assert incoming_calls, f"no incoming tool was ever called: {fetch_calls!r}"
        assert "shipment_ids" in incoming_calls[0], incoming_calls[0]
        assert "product_ids" not in incoming_calls[0], (
            f"entity_op 'replace' must drop the carried product entirely, on the "
            f"FIRST call (no combine, no rerun needed): {incoming_calls[0]!r}"
        )

    def test_c7ii_prompt_documents_only_x_as_entity_op_replace(self) -> None:
        """C7(ii) - RED. Measured this session (grep of `app/services/
        chatbot_parser_prompt.py`'s `SEMANTIC_PARSER_PROMPT` plus every ADDENDUM
        constant): `entity_op`'s own declared OUTPUT SCHEMA enum is
        `"clear|replace_combine|modify|reuse"` - `"replace"` (bare, the value
        `decide.py:505`'s own `replaces_every_axis` flag reads) is never once
        documented as a value the parser may emit, and turn 229e3344's own
        RECORDED verdict (the live "only TCNU3167091" turn,
        `.claude/handpass/hp12-turns-21sep.json`) carries `entity_op:
        "replace_combine"`, not `"replace"` - the live parser never emitted it on
        the one turn that said "only". This is the missing prompt text the owner's
        ruling calls for: the model must be TOLD "only X"/"just X" sets
        `entity_op: "replace"`, not have downstream code guess it from the word."""
        from app.services import chatbot_parser_prompt as prompt_mod

        full = "\n".join(
            [
                prompt_mod.SEMANTIC_PARSER_PROMPT,
                prompt_mod.GROWTH_R1_ADDENDUM,
                prompt_mod.LAST_COST_ADDENDUM,
                prompt_mod.LOW_STOCK_ADDENDUM,
                prompt_mod.SALES_REPORT_ADDENDUM,
            ]
        )

        only_or_just = list(re.finditer(r"\b(only|just)\b", full, re.IGNORECASE))
        assert only_or_just, "test setup sanity: the prompt names 'only'/'just' at all"

        # A "replace" instruction within 400 chars of an "only"/"just" mention - the
        # width every other worked example in this prompt sits inside of its own
        # explanatory paragraph. Excludes "replace_combine" (a DIFFERENT, already-
        # documented value) via the negative lookahead.
        paired = [
            m
            for m in only_or_just
            if re.search(r'entity_op["\s:]*"replace"(?!_combine)', full[m.start() : m.start() + 400])
        ]
        assert paired, (
            "the prompt must document that 'only X'/'just X' sets entity_op "
            '"replace" (owner ruling, C7) - measured: entity_op\'s own declared '
            'enum in the OUTPUT SCHEMA section is "clear|replace_combine|modify|'
            "reuse\"; bare \"replace\" is never documented as a value the parser "
            "may emit anywhere in SEMANTIC_PARSER_PROMPT or its four ADDENDUM "
            "constants"
        )


# --------------------------------------------------------------------------- #
# GROUP B2CD - customer service escalation always shows the member picker.
# --------------------------------------------------------------------------- #


def _run_turn_dry(session_factory, monkeypatch, *, qf, text_body: str, msg_id: str) -> Any:
    """`test_rearch_r6_review_round.py::_run_turn_engine`, with `is_test=True` on the
    envelope (D14) instead of a caller-supplied business-lane `mcp_call` - these B2(c)/
    (d) turns never reach a business-lane fetch at all (they answer a PENDING
    escalation offer directly, `turn/apply.py:625`'s own `Plan(domains=[], fetch=[],
    ...)`), and a dry run means a turn that STILL reaches the real production
    escalation bundle (before the fix lands, or via any path this test's own
    `_escalation_lane_spy` does not intercept) completes safely rather than trying to
    move a live round-robin cursor - `test_engine.py::TestDryRun` is the same
    convention. Assertions read `result.actions` (every action the executor WOULD
    run, D14's own preview contract, `test_engine.py::test_every_action_carries_
    dry_run_true`) and `result.session_patch` (the previewed five-key patch), never
    `_state_of()` (which reads nothing on a dry run by design)."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.head import parser as parser_mod
    from app.services.chatbot.lanes.business.services import AnswerServices, FetchServices
    from tests.chatbot.test_engine import _envelope
    from tests.chatbot.test_outstanding_lane import _enable_business_lane

    _enable_business_lane(session_factory)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General",
            "attributes": [],
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub",
            prompt_version=1,
            provider="openai",
            model="gpt-test",
            api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)
    _real_resolve_with_safe_probe(monkeypatch)
    monkeypatch.setattr(
        engine_mod.business_services,
        "fetch_services",
        lambda db: FetchServices(mcp_call=lambda name, args: _unknown_envelope()),
    )
    monkeypatch.setattr(
        engine_mod.business_services,
        "answer_services_for",
        lambda session_factory: AnswerServices(
            mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
        ),
    )
    monkeypatch.setattr(
        engine_mod.business_services,
        "production_answer_services",
        lambda db: AnswerServices(
            mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
        ),
    )
    envelope = _envelope(is_test=True)
    envelope.message["message"]["messageId"] = msg_id
    envelope.message["message"]["message"]["text"] = text_body
    return engine_mod.run_turn(envelope, session_factory=session_factory)


def _escalation_lane_spy(monkeypatch, *, team_label: str) -> list[dict[str, Any]]:
    """Doubles `engine_mod.run_escalation_lane` - the module-level name `engine.py`'s
    own docstring at `_run_escalation_arm` names as the seam "a test can replace",
    the real one being `lanes.escalation.run` whose `services` default builds the
    PRODUCTION bundle - with a deterministic "escalated" fragment (a `send_message`
    plus an `assign_conversation` action, `lanes/escalation.py::_assignment_
    actions`'s own shape). Real `Team`/`AgentTeam` rows are orthogonal to what
    B2(c)/(d) measure (whether the lane is reached AT ALL for a customer_service
    accept, and which assignee id it was asked to use) - standing them up risks a
    RED for an unrelated environment reason rather than the real defect, the same
    "MCP tool boundary is the double" convention this whole hand pass already
    applies one layer up. Returns the calls list; each entry's own `preferred_
    assignee_id` is read off `ctx.parse.output.escalation.preferred_assignee_id` -
    `plan.trace.assignee`'s own documented destination (`turn/plan.py`'s own
    `Trace.assignee` docstring)."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.escalation import OUT_OF_SCOPE_REPLY, ROUTED_TO_PIC_REPLY

    calls: list[dict[str, Any]] = []

    def _fake(ctx: Any, item: Any, *, dry_run: bool, session_factory: Any) -> dict[str, Any]:
        escalation = (((ctx or {}).get("parse") or {}).get("output") or {}).get("escalation") or {}
        assignee_id = escalation.get("preferred_assignee_id") or None
        calls.append({"dry_run": dry_run, "preferred_assignee_id": assignee_id})
        return {
            "arm": "assign",
            "actions": [
                {"kind": "send_message", "text": OUT_OF_SCOPE_REPLY, "dry_run": dry_run},
                {
                    "kind": "assign_conversation",
                    "respond_user_id": assignee_id or "zzt-round-robin",
                    "dry_run": dry_run,
                },
                {
                    "kind": "add_comment",
                    "text": f"escalated to {team_label} (test double)",
                    "mention_user_ids": [],
                    "dry_run": dry_run,
                },
                {
                    "kind": "send_message",
                    "text": ROUTED_TO_PIC_REPLY.format(team=team_label),
                    "dry_run": dry_run,
                },
            ],
            "pending": None,
        }

    monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake)
    return calls


class TestGroupB2CDCustomerServiceEscalationAlwaysShowsTheMemberPicker:
    """Owner ruling mid-task, relayed after tester 50's B2(a): a "yes" that ACCEPTS
    an escalation offer whose TEAM is customer_service must show the member roster
    THIS turn (pending becomes `member_offer`, no assignment yet) - never the plain
    round-robin `lanes/escalation.py::run()` handover every OTHER team still gets.

    Measured by tester 50 (see the hand pass 12 handoff, `.claude/handoffs/
    20260921T091118Z-rearch-tester50-hp12-reds.md`): `lanes/escalation.py::run()`
    has NO roster-fetch call at all today - the only existing member-roster read is
    `answer_bridge.py::_miss_question`, which fires on a MISS creating a NEW offer,
    never on ACCEPTING one. This pass measured the SAME thing independently
    (`grep -rn "escalate_offered" app/services/chatbot/turn/apply.py`):
    `apply.py:625`'s own `answer_pending_accept` arm sends a "yes" over ANY offer
    straight to `trace.lane = "escalation"` with no team-based branch at all.

    The feature therefore does not exist yet, and every assertion below is on an
    OUTCOME (the reply text, the actions the executor would run, the PREVIEWED
    session_patch) - never on which internal function got called - so it targets
    whatever shape the coder's fix takes rather than a shape this pass invents.

    owner correction 21 Sep: yes = round robin, roster rides the offer reply. The
    two-step above (roster shows first, THEN a follow-up pick/yes assigns) is
    WRONG and is retired by (a) and (d) below - a "yes" over a customer_service
    accept, with no member preference named, assigns by round robin IMMEDIATELY,
    the exact same turn, exactly as production (coder 44's own `engine.py::
    _run_member_offer_arm`, commit 8e4ecdac7, implemented the two-step; that arm
    must be reverted). The member roster rides in the SAME reply as the OFFER
    instead (see `test_rearch_r12_handpass12.py::
    TestGroupB2AMissThatAlsoEarnsACsMemberOfferPrintsOneNumberedList` for the
    did-you-mean-miss half of this fix; (f) below is the plain customer-pick-miss
    half). b/c/e are UNCHANGED: a NUMBER or NAME picked over an already-open
    `member_offer` roster still assigns that specific member (b) or round-robins on
    a bare "yes" (c) - existing, correct behaviour, read not invented - and a
    non-customer_service team offer keeps today's immediate escalation (e, green
    guard)."""

    def test_a_did_you_mean_miss_customer_service_yes_assigns_immediately(
        self, session_factory, monkeypatch
    ) -> None:
        """owner correction 21 Sep: yes = round robin, roster rides the offer reply.

        Turn 99c114fd's own did-you-mean pending (`customer_order_pick`, team
        `customer_service`, `payload.escalate_offered: true` - `_seed_pending`,
        reused from the sibling file's Group B) -> a bare "yes" (turn e6f6d6c3's own
        recorded verdict shape: `is_affirmative: true`, `message_type: "casual"`,
        `entities: []`, `entity_op: "reuse"`) assigns by round robin on THIS turn,
        exactly as production and as `apply.py:625`'s own `answer_pending_accept`
        arm already does - the roster-first two-step (this test's own PRIOR
        assertions, tester 51's original write) is retired."""
        from app.services import team_roster_service

        fixture = _DidYouMeanPendingFixture()
        fixture._seed_pending(session_factory)
        spy_calls = _escalation_lane_spy(monkeypatch, team_label="Customer Service")
        monkeypatch.setattr(
            team_roster_service,
            "list_team_roster",
            lambda *a, **k: [
                {
                    "user_id": "zzt-b2cd-u1",
                    "name": "Zzt Amy",
                    "respond_user_id": "r-amy",
                    "email": "a@zzt.example",
                    "sort_order": 1,
                },
                {
                    "user_id": "zzt-b2cd-u2",
                    "name": "Zzt Ben",
                    "respond_user_id": "r-ben",
                    "email": "b@zzt.example",
                    "sort_order": 2,
                },
            ],
        )

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            domain_in_message=False,
            entities=[],
            entity_op="reuse",
            is_affirmative=True,
            # #1323: a "yes" over an escalation offer is the parser's semantic verdict.
            escalation={"is_escalation_confirmation": True, "company_pick": None},
            document=[],
            status=None,
            order_status=None,
            routing={"suggested_team": None, "suggested_agent": "general_enquiries", "team_source": None},
        )
        result = _run_turn_dry(
            session_factory, monkeypatch, qf=verdict, text_body="yes", msg_id="zzt-b2cd-a-yes"
        )
        assert result.status == "done", result.error
        said = _said(result)

        assert spy_calls, (
            f"a customer_service accept must reach the escalation lane and assign "
            f"THIS turn - no roster-first two-step: {result.actions!r}"
        )
        assert any(a.get("kind") == "assign_conversation" for a in (result.actions or [])), (
            f"the assignment action must be in THIS turn's actions: {result.actions!r}"
        )
        assert "zzt amy" not in said.lower(), (
            f"no member roster may print on the acceptance turn - assignment is "
            f"immediate: {said!r}"
        )
        assert re.search(r"(?m)^\s*1\.\s", said) is None, (
            f"no numbered roster on this turn: {said!r}"
        )

        patch = result.session_patch or {}
        oq_after = patch.get("open_question") or {}
        assert oq_after.get("kind") != "member_offer", (
            f"the pending must not become member_offer - assignment already "
            f"happened this turn: {oq_after!r}"
        )

    def test_b_number_pick_over_member_offer_escalates_to_that_specific_member(
        self, session_factory, monkeypatch
    ) -> None:
        """The FOLLOWING turn after (a): a `member_offer` pending answered by a
        NUMBER assigns that SPECIFIC member. Re-pinned for security finding M2
        (fixed lane commit f724be767): `preferred_assignee_id` must carry the
        picked option's own `uuid` (a real `users.id`), never its
        `respond_user_id` - `app/api/v1/external/next_assignee.py:460-476`
        resolves `preferred_assignee_id` via `TeamMember.user_id`, and
        `lanes/escalation.py:219-221` matches it against a roster row's own
        `uuid`, both of which are `users.id`, never a respond.io id. Hand-seeded
        directly rather than chained off (a)'s own dry-run preview (D14 writes
        nothing a second turn could read back)."""
        _seed_contact_and_get(session_factory)
        amy_uuid = str(uuid.uuid4())
        ben_uuid = str(uuid.uuid4())
        options = [
            {
                "position": 1,
                "label": "Zzt Amy",
                "entity_type": "member",
                "uuid": amy_uuid,
                "payload": {"respond_user_id": "r-amy"},
            },
            {
                "position": 2,
                "label": "Zzt Ben",
                "entity_type": "member",
                "uuid": ben_uuid,
                "payload": {"respond_user_id": "r-ben"},
            },
        ]
        _seed_state(
            session_factory,
            focus=_focus(),
            open_question={
                "kind": "member_offer",
                "team": "customer_service",
                "expects": None,
                "options": options,
                "payload": {},
                "asked_at_turn": 1,
            },
        )
        spy_calls = _escalation_lane_spy(monkeypatch, team_label="Customer Service")

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            domain_in_message=False,
            entities=[],
            entity_op="reuse",
            reference_target="result",
            reference_positions=[2],
            document=[],
            status=None,
            order_status=None,
            routing={"suggested_team": None, "suggested_agent": "general_enquiries", "team_source": None},
        )
        result = _run_turn_dry(
            session_factory, monkeypatch, qf=verdict, text_body="2", msg_id="zzt-b2cd-b-number"
        )
        assert result.status == "done", result.error

        assert spy_calls, (
            f"the escalation lane must be reached to assign a named member: {spy_calls!r}"
        )
        assert spy_calls[-1]["preferred_assignee_id"] == ben_uuid, (
            f"a numbered pick over a member_offer roster must assign THAT option's "
            f"own uuid (a real users.id), never its respond_user_id (M2, "
            f"f724be767): {spy_calls!r}"
        )

    def test_c_yes_over_member_offer_auto_assigns_round_robin(
        self, session_factory, monkeypatch
    ) -> None:
        """Instead of (b): "yes" over the SAME `member_offer` pending auto-assigns
        (round robin) - EXISTING behaviour, `preferred_assignee_id` empty."""
        _seed_contact_and_get(session_factory)
        options = [
            {
                "position": 1,
                "label": "Zzt Amy",
                "entity_type": "member",
                "uuid": str(uuid.uuid4()),
                "payload": {"respond_user_id": "r-amy"},
            },
            {
                "position": 2,
                "label": "Zzt Ben",
                "entity_type": "member",
                "uuid": str(uuid.uuid4()),
                "payload": {"respond_user_id": "r-ben"},
            },
        ]
        _seed_state(
            session_factory,
            focus=_focus(),
            open_question={
                "kind": "member_offer",
                "team": "customer_service",
                "expects": None,
                "options": options,
                "payload": {},
                "asked_at_turn": 1,
            },
        )
        spy_calls = _escalation_lane_spy(monkeypatch, team_label="Customer Service")

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            domain_in_message=False,
            entities=[],
            entity_op="reuse",
            is_affirmative=True,
            # #1323: a "yes" over an escalation offer is the parser's semantic verdict.
            escalation={"is_escalation_confirmation": True, "company_pick": None},
            document=[],
            status=None,
            order_status=None,
            routing={"suggested_team": None, "suggested_agent": "general_enquiries", "team_source": None},
        )
        result = _run_turn_dry(
            session_factory, monkeypatch, qf=verdict, text_body="yes", msg_id="zzt-b2cd-c-yes"
        )
        assert result.status == "done", result.error

        assert spy_calls, (
            f"a bare yes over a member_offer must still escalate (round robin): {spy_calls!r}"
        )
        assert not spy_calls[-1]["preferred_assignee_id"], (
            f"a bare yes must round-robin, not target a specific member: {spy_calls!r}"
        )

    def test_d_plain_customer_service_offer_yes_assigns_immediately(
        self, session_factory, monkeypatch
    ) -> None:
        """owner correction 21 Sep: yes = round robin, roster rides the offer reply.

        Turn 002a8f5b's own tail (its reply's own escalate offer: "Would you like
        me to escalate to customer service team?", a plain `team_pick`/`yes_no`
        with NO did-you-mean list riding alongside it) -> "yes" -> assigns by round
        robin on THIS turn, the SAME flip as (a), over a DIFFERENT pending shape
        (`team_pick`, already in `ESCALATION_OFFER_KINDS`, so `apply.py::
        _answer_offer` already routes this correctly once the roster-first arm is
        reverted)."""
        from app.services import team_roster_service

        _seed_contact_and_get(session_factory)
        _seed_state(
            session_factory,
            focus=_focus(domains=["order"], status="delivered"),
            open_question={
                "kind": "team_pick",
                "team": "customer_service",
                "expects": "yes_no",
                "options": [
                    {
                        "position": 1,
                        "label": "Customer Service",
                        "entity_type": "team",
                        "payload": {"team": "customer_service"},
                    }
                ],
                "payload": {},
                "asked_at_turn": 1,
            },
        )
        spy_calls = _escalation_lane_spy(monkeypatch, team_label="Customer Service")
        monkeypatch.setattr(
            team_roster_service,
            "list_team_roster",
            lambda *a, **k: [
                {
                    "user_id": "zzt-b2cd-u3",
                    "name": "Zzt Cara",
                    "respond_user_id": "r-cara",
                    "email": "c@zzt.example",
                    "sort_order": 1,
                }
            ],
        )

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            domain_in_message=False,
            entities=[],
            entity_op="reuse",
            is_affirmative=True,
            # #1323: a "yes" over an escalation offer is the parser's semantic verdict.
            escalation={"is_escalation_confirmation": True, "company_pick": None},
            document=[],
            status=None,
            order_status=None,
            routing={"suggested_team": None, "suggested_agent": "general_enquiries", "team_source": None},
        )
        result = _run_turn_dry(
            session_factory, monkeypatch, qf=verdict, text_body="yes", msg_id="zzt-b2cd-d-yes"
        )
        assert result.status == "done", result.error
        said = _said(result)

        assert spy_calls, (
            f"the escalation lane must run and assign THIS turn - no roster-first "
            f"two-step: {result.actions!r}"
        )
        assert "zzt cara" not in said.lower(), (
            f"no member roster may print on the acceptance turn: {said!r}"
        )

        patch = result.session_patch or {}
        oq_after = patch.get("open_question") or {}
        assert oq_after.get("kind") != "member_offer", (
            f"the pending must not become member_offer - assignment already "
            f"happened this turn: {oq_after!r}"
        )

    def test_f_plain_customer_pick_miss_reply_carries_member_roster_continuing_numbering(
        self, session_factory, monkeypatch
    ) -> None:
        """(d)'s own additional half, owner correction 21 Sep: a plain customer-pick
        MISS reply (4 customer options, turn 42e57c4e's own live count) must ALSO
        earn the CS member roster IN THE SAME REPLY, continuing the customer
        options' own numbering: customers 1..4 stay open under contract 36, members
        5..9 - the same fix as B2A(a), applied to a `customer`-typed roster instead
        of an `order`-typed one.

        Modelled via `_miss_question`'s `suggest_last_result_set` roster surface
        (`resolved.resolutions[].alternatives`, entity_type `customer` - the SAME
        surface B2A(a) exercises one file over, proven to reach `_miss_question`),
        not `gate.compatible_entities`/`require_specific` (turn 42e57c4e's own
        actual live surface, "Which customer do you mean?" - that TEXT is composed
        in `lanes/business/gate.py`/`lanes/business/answer.py`, outside
        `answer_bridge.py` entirely, and reproducing it exactly was beyond this
        pass's budget - flagged, not silently substituted). `_miss_question`'s own
        combining logic is the ONE function this fix touches either way, so the
        OUTCOME under test (numbering continues across the group boundary) is the
        same regardless of which roster surface fed it."""
        from app.services import team_roster_service
        from app.services.chatbot import answer_bridge
        from app.services.chatbot import copy as copy_mod
        from app.services.chatbot.lanes.business.services import AnswerServices

        ids = [str(uuid.uuid4()) for _ in range(4)]
        names = [
            "BATHIDEA BATHROOM & KITCHEN MARKETING SDN BHD (MCH)",
            "BATH IDEA BATHROOM & KITCHEN SPECIALIST (SRT)",
            "BATH IDEA (KEMAMAN OUTLET) (SRT)",
            "BATH IDEA BATHROOM & KITCHEN MARKETING SDN BHD (SRT)",
        ]
        raw1, raw2 = "bath idea kl", "bath idea kemaman"

        def _alt(name: str, uid: str) -> dict[str, Any]:
            return {
                "canonical_code": name,
                "entity_type": "customer",
                "uuid": uid,
                "display": {"customer_name": name},
                "match_tier": "fuzzy",
            }

        parser = {
            "domain_hint": "order",
            "intent_hint": "check_order",
            "message_type": "business_query",
            "entities": [
                {
                    "raw": raw1,
                    "hint": "customer",
                    "current_message": True,
                    "confident": True,
                },
                {
                    "raw": raw2,
                    "hint": "customer",
                    "current_message": True,
                    "confident": True,
                },
            ],
            "routing": {
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
            },
            "access_levels": [],
        }
        # TWO tokens, TWO alternatives each - the SAME shape B2A(a) exercises (two
        # missed raws, three alternatives each) at a smaller scale, confirmed to
        # reach `_miss_question`'s numbered did-you-mean roster; a single token
        # with several alternatives renders as a comma sentence instead
        # (`not_found_error_message`'s own multi-alternative-same-token shape),
        # measured directly and not the numbered shape turn 42e57c4e needs.
        resolved = {
            "resolutions": [
                {
                    "token": raw1,
                    "matches": [],
                    "alternatives": [_alt(names[0], ids[0]), _alt(names[1], ids[1])],
                },
                {
                    "token": raw2,
                    "matches": [],
                    "alternatives": [_alt(names[2], ids[2]), _alt(names[3], ids[3])],
                },
            ],
            "unresolved_tokens": [raw1, raw2],
            "tokens": [raw1, raw2],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [],
            "require_specific": False,
            "gate_debug": {"domain": "order"},
        }
        payload = {"resolved": resolved, "gate": gate, "_exit_kind": "not_found"}

        member_names = ["Sandy Lim", "Lin", "Nur", "Emily", "Zilin Poon"]
        member_ids = ["1136807", "1136805", "1136799", "1136808", "1204233"]
        monkeypatch.setattr(
            team_roster_service,
            "list_team_roster",
            lambda *a, **k: [
                {
                    "user_id": f"zzt-f-u{i}",
                    "name": n,
                    "respond_user_id": rid,
                    "email": f"{i}@zzt.example",
                    "sort_order": i,
                }
                for i, (n, rid) in enumerate(zip(member_names, member_ids), start=1)
            ],
        )

        _seed_contact_and_get(session_factory)
        db = session_factory()
        answer = answer_bridge.answer_for(
            payload,
            envelope=None,
            parser=parser,
            ctx={
                "parse": {"output": parser},
                "contact": {"id": "zzt-f-contact"},
                "session": {},
            },
            canned=copy_mod.fallback_copy(),
            services=AnswerServices(
                mcp_probe=lambda name, args: {"has_result": False, "answers": []},
                family_fetch=lambda query: {"data": []},
            ),
            db=db,
            asked_at_turn=3,
        )
        assert answer is not None
        text = answer.text or ""

        assert names[0] in text, f"test setup sanity, customer roster text: {text!r}"

        numbered_list_starts = re.findall(r"(?m)^\s*1\.\s", text)
        assert len(numbered_list_starts) <= 1, (
            f"exactly ONE numbered list, continuing the customer options' own "
            f"numbering into the member roster: {text!r}"
        )
        numbers = [int(n) for n in re.findall(r"(?m)^\s*(\d+)\.\s", text)]
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"numbers must never repeat inside one reply - customers 1..4 "
            f"continuing into members 5..9: {text!r}"
        )
        assert len(numbers) == 9, (
            f"4 customer options + 5 CS members must land in ONE list: {text!r}"
        )
        assert "lin" in text.lower(), (
            f"the member roster must ride in the SAME reply as the customer-pick "
            f"offer: {text!r}"
        )

        assert answer.question is not None
        options = answer.question.options or []
        assert len(options) == 9, (
            f"the pending must let BOTH option groups be answered - 9 entries: "
            f"{options!r}"
        )
        positions_seen = sorted(o.get("position") for o in options)
        assert positions_seen == list(range(1, 10)), (
            f"no position may repeat and none may be skipped: {options!r}"
        )
        member_at_6 = next((o for o in options if o.get("position") == 6), None)
        assert member_at_6 is not None and member_at_6.get("entity_type") == "member", (
            f"position 6 (Lin) must be a member option: {options!r}"
        )
        assert (member_at_6.get("payload") or {}).get("respond_user_id") == "1136805", (
            f"position 6 must be Lin, respond_user_id 1136805: {options!r}"
        )

    def test_e_green_guard_purchasing_offer_yes_still_escalates_immediately(
        self, session_factory, monkeypatch
    ) -> None:
        """GREEN GUARD: a NON-customer_service team offer (purchasing) must keep
        TODAY's behaviour unchanged - "yes" escalates immediately, no roster."""
        _seed_contact_and_get(session_factory)
        _seed_state(
            session_factory,
            focus=_focus(domains=["incoming"]),
            open_question={
                "kind": "team_pick",
                "team": "purchasing",
                "expects": "yes_no",
                "options": [
                    {
                        "position": 1,
                        "label": "Purchasing",
                        "entity_type": "team",
                        "payload": {"team": "purchasing"},
                    }
                ],
                "payload": {},
                "asked_at_turn": 1,
            },
        )
        spy_calls = _escalation_lane_spy(monkeypatch, team_label="Purchasing")

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            domain_in_message=False,
            entities=[],
            entity_op="reuse",
            is_affirmative=True,
            # #1323: a "yes" over an escalation offer is the parser's semantic verdict.
            escalation={"is_escalation_confirmation": True, "company_pick": None},
            document=[],
            status=None,
            order_status=None,
            routing={"suggested_team": None, "suggested_agent": "general_enquiries", "team_source": None},
        )
        result = _run_turn_dry(
            session_factory, monkeypatch, qf=verdict, text_body="yes", msg_id="zzt-b2cd-e-green"
        )
        assert result.status == "done", result.error

        assert spy_calls, (
            f"test setup sanity: a purchasing accept must still reach the "
            f"escalation lane exactly as today: {spy_calls!r}"
        )
        assert any(a.get("kind") == "assign_conversation" for a in (result.actions or [])), (
            f"a non-customer_service accept must still assign immediately, no "
            f"roster (green guard): {result.actions!r}"
        )
