"""Hand pass 12 RED tests (owner's 61 recorded live turns, `.claude/handpass/
hp12-turns-21sep.json`), written test-FIRST from the captain's brief and mid-task
owner rulings relayed by the coordinator. No implementation for any of this exists yet.

Four groups, each traced to specific recorded turns (ids quoted below are the JSON's
own 8-char turn ids):

A. Stale order facets (`focus.status`/`focus.document`) leak into a NON-order reply's
   own header, and into a non-order tool's own args. Owner ruling mid-task: "remembering
   is one thing, whether I use it is another" - the focus MAY keep the carried
   status/document across a domain change (no assertion on the focus itself); what must
   stop is a non-order domain USING them. `fetch.py:2379-2391` stamps "Here are the
   [outstanding|delivered] orders I found." onto ANY tool's envelope whenever
   `semantic_input.get("order_status")` reads "outstanding"/"delivered", with no domain
   gate at all - and `turn_runtime.lane_parse_output` (turn_runtime.py:522-534) is what
   projects that bucket off `focus.status`/`focus.document` whenever THIS turn's own
   verdict names neither. Turns replayed: 5fbc9ee3 ("INCOMING SRTJC1303-R", domain
   incoming, prior focus status "delivered"/document ["DO"]/domains ["order"] - reply
   opened "Here are the delivered orders I found." over an INCOMING block) and c21b0d35
   ("Srtkt31ss stock", domain inventory, SAME leak - "Here are the delivered orders I
   found." over a stock summary). The round-trip pin additionally replays the shape of
   3ab754ff ("casa sanitary ware sdn bhd sep delivery", domain order, verdict's OWN
   status "delivered") - a bare order-domain follow-up with no document/status word of
   its own must still print the delivered header and pass `order_status` to the order
   tool, proving the carry survives a domain detour and is re-used once back on its own
   domain.

B. Did-you-mean pick by typed labels/positions never reaches the tool. Turns 99c114fd
   (the miss: two order raws PS202609-0374/PS202609-0363, did-you-mean roster
   positions 1-6) then fff93d11 ("PS202609-0320, PS202609-0310", TYPED LABELS - the
   parser resolved them to `reference_positions: [1, 6]`, `reference_target: "result"`).
   Measured: `focus.extra.customer_order` gets the two picked uuids but
   `focus.extra.order` KEEPS the two missed raws, and `crm_order_management_orders_list`
   is called with `entities_in: 0` / no `order_ids` at all - the reply re-renders the
   SAME "Couldn't find some items" miss. Coordinator note (owner rulings taken since):
   PS202609-0374/PS202609-0363 genuinely do not exist on the clone (newest is
   PS202609-0334) - the miss itself is correct and is not under test here.

   B2 (new, owner ruling mid-task): one numbered list per message, numbers never repeat
   inside a reply. `tail/outcome.py::cs_offer_gate`'s own "g4-no-double-picker" guard
   (`gate is None or gate.require_specific is not True`) only protects the
   REQUIRE-SPECIFIC picker case - a plain did-you-mean roster is a DIFFERENT gate shape
   the guard never covers, so 99c114fd's own reply prints BOTH the did-you-mean roster
   (1-6) AND the customer-service member roster (restarting at 1, "Sandy Lim" etc) in
   ONE message, and the STORED pending (`open_question.after`, this session's own read
   of the trace) is "member_offer" - not "customer_order_pick" - so a customer picking
   by the position the text just showed them ("1") cannot even reach the did-you-mean
   roster. (a) below pins that collision at `answer_bridge.answer_for` directly (the
   function `_miss_question`/`cs_offer_gate` both live in) - the same level
   `test_rearch_r4_bridge_miss.py` already tests this seam at, and the only way to
   reach `cs_offer_gate`'s own precedence decision without standing up the whole
   chatbot_domains/agent/team roster machinery through the live HTTP resolver. (b)
   replays 87c55e9e's own verdict shape ("1 and 4", `reference_target: "dym"`,
   `reference_positions: [1, 4]` - a genuine ARRAY) over the SAME did-you-mean roster
   Group B seeds, pinning that a MULTI-position pick settles every position, not just
   the first. (c)/(d) - the "customer service escalation always shows the member
   picker" half of the ruling - could NOT be written this pass; see the handoff doc for
   why (no seam/model precedent exists yet for a roster-fetch double at the
   ESCALATION LANE's own accept path, `lanes/escalation.py::run` - unlike the
   `_miss_question` seam above, `run()` has no roster-fetch call at all today, so
   pinning "shows a member picker" as a NEW behaviour needs a shape decision only the
   coder/captain can make).

F. Customer pick header prints a customer CODE. Turns 42e57c4e (the offer, four
   customer options) then 002a8f5b ("2", picking option 2 - THREE distinct ledger
   uuids, per the live tool call's own `customer_ids` argument holding exactly 3
   entries). Owner ruling mid-task (changed from the original brief): "print the 3
   customer names" - the reply's own header must name every ledger the pick covers, not
   the option's own rollup code ("300-B094" in the live trace). Also: `open_question`
   must be CONSUMED by the pick (measured: it is not - `open_question.after` stays
   `customer_pick`, kind unchanged, in the live trace).

H. A missed leg of a multi-domain ask prints the bare `NO_RESULT_INTRO` ("No matching
   results found.", `fetch.py:1120`) with no domain or product named at all. Turns
   d16347c2 ("stock and eta": inventory HIT, incoming MISS, PO-rung MISS) and 92280f52
   ("stock and eta": incoming MISS, PO-rung HIT, inventory HIT - reply order:
   "No matching results found." bare, then the PO rows introduced as
   "Here are the delivered orders I found." (group A's own leak riding along, since
   this turn's carried focus was ALSO status "delivered"/document ["SO"]), then the
   stock block).

Harness: `test_rearch_r11_zero_stock_live_replay.py`'s own convention -
`test_rearch_r5_production_decides.py::_run_turn_with_mcp_call` /
`test_rearch_r6_review_round.py::_run_turn_engine`, the REAL resolver/gate/narrower
over seeded Postgres rows, only `FetchServices.mcp_call` / `AnswerServices.mcp_probe`
doubled. Prior focus/open_question states are seeded directly onto
`respond_contacts.session_vars` (the SAME raw-SQL convention
`test_rearch_r11_company_routing.py`'s own malformed-open-question test and
`test_rearch_r4_answering_a_miss.py::_write_session_vars` both use), never invented -
every wire key copied from a real recorded `memory`/`open_question` trace event.

Postgres only (`session_factory`, blank schema). Every row seeded fresh per test; no
row is borrowed from another test or from any live/clone data. No live parser, no
:8766, no API key.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import text as _sql_text

from app.models.order import Customer
from app.services.chatbot import copy as copy_mod
from app.services.chatbot.lanes.business.fetch import ORDER_TOOLS as ORDER_TOOLS_LOCAL
from app.services.chatbot.lanes.business.services import AnswerServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.test_engine import CONTACT_ID, _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_rearch_r5_production_decides import _mcp_double, _seed_contact_and_get
from tests.chatbot.test_rearch_r6_review_round import _run_turn_engine

STOCK_TOOL = "crm_inventory_stock_balance_list"
INCOMING_TOOL = "crm_incoming_stock_list"
PO_TOOL = "crm_procurement_po_placed_list"


# --------------------------------------------------------------------------- #
# Shared fixtures: session_vars read/write, focus builder, tool envelopes.
# --------------------------------------------------------------------------- #


def _seed_state(
    session_factory,
    *,
    contact_id: Any = CONTACT_ID,
    focus: dict[str, Any] | None = None,
    open_question: dict[str, Any] | None = None,
) -> None:
    """The FIVE-key wire shape (`session_state.FIVE_KEYS`) `test_rearch_r11_company_
    routing.py`'s own malformed-open-question test and `test_rearch_r4_answering_a_
    miss.py::_write_session_vars` both write session_vars in - never the legacy nested
    `variables` shape."""
    db = session_factory()
    db.execute(
        _sql_text(
            "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
            "WHERE respond_io_id = :cid"
        ),
        {
            "sv": json.dumps(
                {
                    "variables": {},
                    "focus": focus,
                    "open_question": open_question,
                    "ideation": None,
                    "access_levels": None,
                    "contains_flyer": None,
                }
            ),
            "cid": str(contact_id),
        },
    )
    db.commit()


def _state_of(session_factory, *, contact_id: Any = CONTACT_ID) -> dict[str, Any]:
    db = session_factory()
    row = db.execute(
        _sql_text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": str(contact_id)},
    ).first()
    raw = row.session_vars if row is not None else {}
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


def _focus(
    *,
    domains: list[str] = (),
    status: str | None = None,
    document: list[str] = (),
    products: list[dict[str, Any]] = (),
    customers: list[dict[str, Any]] = (),
    extra: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """`turn/state.py::focus_to_wire`'s own shape - every axis the wire carries."""
    return {
        "products": list(products),
        "customers": list(customers),
        "warehouse": [],
        "brands": [],
        "tier": [],
        "domains": list(domains),
        "document": list(document),
        "status": status,
        "sales_channel": None,
        "date_window": None,
        "set_page": None,
        "extra": extra
        if extra is not None
        else {
            "order": [],
            "category": [],
            "customer_order": [],
            "attachment_type": [],
            "inbound_shipment": [],
        },
    }


def _order_row(order_number: str, product_code: str) -> dict[str, Any]:
    return {
        "flags": {},
        "title": order_number,
        "fields": [
            {"key": "company_name", "label": "Company", "value": "Sorento"},
            {"key": "order_number", "label": "Order Number", "value": order_number},
            {"key": "product_code", "label": "Product Code", "value": product_code},
            {"key": "status", "label": "Status", "value": "Delivered"},
        ],
    }


def _order_envelope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "result_type": "orders",
        "intro": "Here are the orders I found." if rows else "No matching results found.",
        "items": rows,
        "has_result": bool(rows),
        "attachments": [],
        "action_links": [],
    }


def _incoming_hit(code: str) -> dict[str, Any]:
    return {
        "intro": "Here is the incoming stock I found.",
        "items": [
            {
                "flags": {},
                "title": code,
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": code},
                    {
                        "key": "shipping_container_number",
                        "label": "Container",
                        "value": "ZZTCONT1",
                    },
                    {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-20"},
                ],
            }
        ],
        "has_result": True,
        "attachments": [],
        "result_type": "incoming",
        "action_links": [],
    }


def _incoming_miss() -> dict[str, Any]:
    return {
        "intro": "No matching results found.",
        "items": [],
        "has_result": False,
        "attachments": [],
        "result_type": "incoming",
        "action_links": [],
    }


def _po_hit(code: str, po_number: str) -> dict[str, Any]:
    return {
        "intro": "Here is the PO placed I found.",
        "items": [
            {
                "flags": {},
                "title": po_number,
                "fields": [
                    {"key": "company_name", "label": "Company", "value": "Sorento"},
                    {"key": "po_number", "label": "PO Number", "value": po_number},
                    {"key": "product_code", "label": "Product Code", "value": code},
                    {"key": "ordered_qty", "label": "Ordered Qty", "value": 10},
                    {"key": "outstanding_qty", "label": "Outstanding Qty", "value": 10},
                    {"key": "po_date", "label": "PO Date", "value": "2026-09-11"},
                    {"key": "location", "label": "Location", "value": "BRW"},
                ],
            }
        ],
        "has_result": True,
        "attachments": [],
        "result_type": "purchase_orders_placed",
        "action_links": [],
    }


def _stock_hit(code: str) -> dict[str, Any]:
    return {
        "intro": "Stock summary for the requested products.",
        "items": [
            {
                "flags": {},
                "title": code,
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": code},
                    {"key": "total_on_hand", "label": "Total", "value": 12},
                ],
            }
        ],
        "has_result": True,
        "attachments": [],
        "result_type": "stock_compact",
        "action_links": [],
    }


def _unknown_envelope() -> str:
    return json.dumps({"result_type": "unknown", "items": [], "has_result": False})


def _said(result) -> str:
    return (result.reply or {}).get("text") or ""


def _run_turn_dry(session_factory, monkeypatch, *, qf, text_body: str, msg_id: str) -> Any:
    """`test_rearch_r12_handpass12_b.py::_run_turn_dry`, duplicated locally rather than
    imported (that file imports FROM this one, so importing it back would be
    circular) - a dry run (`is_test=True`, D14) so an escalation-outcome test does not
    fall through to `conversation_variables_service.overwrite_for_contact`'s own
    real-commit write path, which this pass measured crashes on a PRE-EXISTING,
    unrelated `respond_io_id` int/text mismatch (`text = integer`) the moment a turn
    actually reaches the escalation lane for real - not the defect this file targets."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.head import parser as parser_mod
    from app.services.chatbot.lanes.business.services import AnswerServices, FetchServices
    from tests.chatbot.test_engine import _envelope as _mk_envelope
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
    envelope = _mk_envelope(is_test=True)
    envelope.message["message"]["messageId"] = msg_id
    envelope.message["message"]["message"]["text"] = text_body
    return engine_mod.run_turn(envelope, session_factory=session_factory)


# --------------------------------------------------------------------------- #
# GROUP A - remembered order facets are not USED outside the order domain.
# --------------------------------------------------------------------------- #


class TestGroupARememberedOrderFacetsNotUsedOutsideOrderDomain:
    def test_incoming_ask_after_order_domain_does_not_print_delivered_header(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turn 5fbc9ee3 ("INCOMING SRTJC1303-R"). Prior focus: domain order,
        status delivered, document DO (a carry from an earlier order ask, e.g.
        60d76db9 in the live session). Owner ruling: the carry MAY stay on focus; the
        incoming reply must simply never USE it."""
        _seed_contact_and_get(session_factory)
        code = unique_code("A1PROD")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        _seed_state(
            session_factory,
            focus=_focus(domains=["order"], status="delivered", document=["DO"]),
        )

        verdict = _parser_output(
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=True,
            entities=[
                {
                    "raw": code,
                    "hint": "product",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
            document=[],
            status=None,
            order_status=None,
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                return json.dumps(_incoming_hit(code))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=f"INCOMING {code}",
            msg_id="zzt-a1-incoming",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        assert "delivered orders" not in said.lower(), (
            "a carried order-domain status must not leak into an INCOMING reply's own "
            f"header - fetch.py's order_status intro override is not domain-gated: {said!r}"
        )
        assert "outstanding orders" not in said.lower(), said

        incoming_calls = [args for name, args in fetch_calls if name == INCOMING_TOOL]
        assert len(incoming_calls) == 1, fetch_calls
        assert "order_status" not in incoming_calls[0], (
            f"a non-order tool must never receive the carried order_status arg: "
            f"{incoming_calls[0]!r}"
        )

    def test_inventory_ask_after_order_domain_does_not_print_delivered_header(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turn c21b0d35 ("Srtkt31ss stock") - the SAME leak, domain
        inventory."""
        _seed_contact_and_get(session_factory)
        code = unique_code("A2PROD")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        _seed_state(
            session_factory,
            focus=_focus(domains=["order"], status="delivered", document=["DO"]),
        )

        verdict = _parser_output(
            intent_hint="check_stock",
            domain_hint="inventory",
            domain_in_message=True,
            entities=[
                {
                    "raw": code,
                    "hint": "product",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            routing={
                "suggested_team": "warehouse",
                "suggested_agent": "general_enquiries",
                "team_source": None,
            },
            document=[],
            status=None,
            order_status=None,
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(_stock_hit(code))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=f"{code} stock",
            msg_id="zzt-a2-inventory",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        assert "delivered orders" not in said.lower(), (
            f"a carried order-domain status must not leak into a STOCK reply's own "
            f"header: {said!r}"
        )
        assert "outstanding orders" not in said.lower(), said

        stock_calls = [args for name, args in fetch_calls if name == STOCK_TOOL]
        assert len(stock_calls) == 1, fetch_calls
        assert "order_status" not in stock_calls[0], (
            f"a non-order tool must never receive the carried order_status arg: "
            f"{stock_calls[0]!r}"
        )

    def test_order_status_does_not_survive_a_domain_detour(
        self, session_factory, monkeypatch
    ) -> None:
        """Round trip: order ask (status delivered) -> "INCOMING <code>" (a domain
        detour) -> an order-domain follow-up naming no document/status word of its own.

        Superseded ruling. Hand pass 12 pinned the carried "delivered"/DO as REMEMBERED
        across the detour and USED again on the return. The owner's hand test of 27 Sep
        2026 (#1262 fix lane round 5, R2) reverses it: status and document come from the
        current message, and a message with its own intent or domain (a check stock, an
        incoming ask) drops them, "so a later DO ask starts clean" - the carry this test
        used to pin is what kept the outstanding bucket on every later DO ask. Turn 2
        still must not use it; turn 3 now runs the plain order list, no status filter."""
        _seed_contact_and_get(session_factory)
        code = unique_code("A3PROD")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        product_entity = {
            "raw": code,
            "hint": "product",
            "canonical_code": None,
            "current_message": True,
            "confident": True,
        }

        def _order_call(name: str, args: dict[str, Any]) -> str:
            if name in ORDER_TOOLS_LOCAL:
                return json.dumps(_order_envelope([_order_row("ZZT-DO-1", code)]))
            return _unknown_envelope()

        order_verdict = _parser_output(
            intent_hint="check_order",
            domain_hint="order",
            domain_in_message=True,
            entities=[product_entity],
            document=["DO"],
            status="delivered",
            order_status=None,
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
            routing={
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
                "team_source": None,
            },
        )
        mcp_call_1, calls_1 = _mcp_double(other=_order_call)
        result_1 = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=order_verdict,
            text_body=f"status delivery {code}",
            msg_id="zzt-a3-turn1-order",
            mcp_call=mcp_call_1,
        )
        assert result_1.status == "done", result_1.error

        def _incoming_call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                return json.dumps(_incoming_hit(code))
            return _unknown_envelope()

        incoming_verdict = _parser_output(
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=True,
            entities=[product_entity],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
        )
        mcp_call_2, calls_2 = _mcp_double(other=_incoming_call)
        result_2 = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=incoming_verdict,
            text_body=f"INCOMING {code}",
            msg_id="zzt-a3-turn2-detour",
            mcp_call=mcp_call_2,
        )
        assert result_2.status == "done", result_2.error
        said_2 = _said(result_2)
        assert "delivered orders" not in said_2.lower(), (
            f"turn 2 (the detour) must not use the carried status either: {said_2!r}"
        )

        followup_verdict = _parser_output(
            intent_hint="check_order",
            domain_hint="order",
            domain_in_message=True,
            entities=[product_entity],
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
        mcp_call_3, calls_3 = _mcp_double(other=_order_call)
        result_3 = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=followup_verdict,
            text_body=f"status {code}",
            msg_id="zzt-a3-turn3-back-on-order",
            mcp_call=mcp_call_3,
        )
        assert result_3.status == "done", result_3.error
        said_3 = _said(result_3)

        assert "delivered orders" not in said_3.lower(), (
            "the detour dropped the carried status; the follow-up names none of its own "
            f"(owner ruling 27 Sep 2026, R2): {said_3!r}"
        )
        order_calls_3 = [args for name, args in calls_3 if name in ORDER_TOOLS_LOCAL]
        assert order_calls_3, f"no order tool was ever called on turn 3: {calls_3!r}"
        assert not order_calls_3[0].get("order_status"), (
            f"a status from before the detour must not reach the order tool: {order_calls_3[0]!r}"
        )


# --------------------------------------------------------------------------- #
# GROUP B - did-you-mean pick by typed labels / a single number never reaches the tool.
# --------------------------------------------------------------------------- #


class TestGroupBDidYouMeanPickByTypedLabelsReachesTheTool:
    OPTION_A = "PS202609-0320"
    OPTION_A_UUID = str(uuid.uuid4())
    OPTION_B = "PS202609-0325"
    OPTION_B_UUID = str(uuid.uuid4())
    OPTION_C = "PS202609-0321"
    OPTION_C_UUID = str(uuid.uuid4())
    OPTION_D = "PS202609-0263"
    OPTION_D_UUID = str(uuid.uuid4())
    OPTION_E = "PS202609-0330"
    OPTION_E_UUID = str(uuid.uuid4())
    OPTION_F = "PS202609-0310"
    OPTION_F_UUID = str(uuid.uuid4())
    MISSED_RAW_1 = "PS202609-0374"
    MISSED_RAW_2 = "PS202609-0363"

    def _options(self) -> list[dict[str, Any]]:
        codes = [
            (self.OPTION_A, self.OPTION_A_UUID, 1),
            (self.OPTION_B, self.OPTION_B_UUID, 2),
            (self.OPTION_C, self.OPTION_C_UUID, 3),
            (self.OPTION_D, self.OPTION_D_UUID, 4),
            (self.OPTION_E, self.OPTION_E_UUID, 5),
            (self.OPTION_F, self.OPTION_F_UUID, 6),
        ]
        return [
            {
                "code": code,
                "uuid": uid,
                "label": code,
                "uuids": [uid],
                "payload": {"value": code},
                "position": pos,
                "entity_type": "customer_order",
            }
            for code, uid, pos in codes
        ]

    def _seed_pending(self, session_factory) -> None:
        """The recorded `before` state 87c55e9e's own `focus`/`open_question` trace
        events pin: two missed raws still on `focus.extra.order`, a customer_order_pick
        roster with `payload.escalate_offered` True, team customer_service."""
        _seed_contact_and_get(session_factory)
        _seed_state(
            session_factory,
            focus=_focus(
                domains=["order"],
                status="delivered",
                document=["DO"],
                extra={
                    "order": [
                        {
                            "raw": self.MISSED_RAW_1,
                            "hint": "order",
                            "confident": True,
                            "canonical_code": None,
                            "hint_confident": True,
                            "current_message": False,
                        },
                        {
                            "raw": self.MISSED_RAW_2,
                            "hint": "order",
                            "confident": True,
                            "canonical_code": None,
                            "hint_confident": True,
                            "current_message": False,
                        },
                    ],
                    "category": [],
                    "customer_order": [],
                    "attachment_type": [],
                    "inbound_shipment": [],
                },
            ),
            open_question={
                "kind": "customer_order_pick",
                "team": "customer_service",
                "expects": None,
                "options": self._options(),
                "payload": {"domain": "order", "escalate_offered": True},
                "asked_at_turn": 1,
            },
        )

    def test_typed_label_pick_reaches_the_tool_and_replaces_the_missed_raws(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turn fff93d11: the customer typed the option LABELS
        ("PS202609-0320, PS202609-0310"), which the parser resolved to
        reference_positions [1, 6]."""
        self._seed_pending(session_factory)
        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            reference_target="result",
            reference_positions=[1, 6],
            document=[],
            status=None,
            order_status=None,
        )
        mcp_call, calls = _mcp_double(other=lambda name, args: json.dumps(_order_envelope([])))
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=f"{self.OPTION_A}, {self.OPTION_F}",
            msg_id="zzt-b-typed-pick",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        order_calls = [args for name, args in calls if name in ORDER_TOOLS_LOCAL]
        assert order_calls, f"no order tool was ever called: {calls!r}"
        order_ids = {str(u) for u in (order_calls[0].get("order_ids") or [])}
        assert order_ids == {self.OPTION_A_UUID, self.OPTION_F_UUID}, (
            f"the picked uuids must reach the orders tool's own order_ids arg: "
            f"{order_calls[0]!r}"
        )

        state = _state_of(session_factory)
        focus_after = state.get("focus") or {}
        order_extra_after = (focus_after.get("extra") or {}).get("order") or []
        missed_still_there = {e.get("raw") for e in order_extra_after}
        assert self.MISSED_RAW_1 not in missed_still_there, (
            f"the missed raws the pick replaced must not still be on "
            f"focus.extra.order: {order_extra_after!r}"
        )
        assert self.MISSED_RAW_2 not in missed_still_there, order_extra_after

        said = _said(result)
        assert "Couldn't find some items" not in said, (
            f"a successful pick must not re-render the SAME did-you-mean miss: {said!r}"
        )

    def test_single_number_pick_settles_the_same_way(
        self, session_factory, monkeypatch
    ) -> None:
        """Not a live replay - a hand-built variant over the SAME pending, per the
        captain's own instruction: a single numbered pick ("2") must settle
        PS202609-0325 (option 2) the same way the typed-label pick above settles its
        own two options."""
        self._seed_pending(session_factory)
        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            reference_target="result",
            reference_positions=[2],
            document=[],
            status=None,
            order_status=None,
        )
        mcp_call, calls = _mcp_double(other=lambda name, args: json.dumps(_order_envelope([])))
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="2",
            msg_id="zzt-b-number-pick",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        order_calls = [args for name, args in calls if name in ORDER_TOOLS_LOCAL]
        assert order_calls, f"no order tool was ever called: {calls!r}"
        order_ids = {str(u) for u in (order_calls[0].get("order_ids") or [])}
        assert order_ids == {self.OPTION_B_UUID}, (
            f"a single numbered pick ('2') must settle PS202609-0325 the same way a "
            f"typed-label pick does: {order_calls[0]!r}"
        )


class TestGroupB2OneNumberedListPerMessage:
    """(b) - "1 and 4" over the did-you-mean roster (recorded verdict 87c55e9e's own
    shape). (a) is below its own class (a lighter, `answer_bridge.answer_for`-level
    fixture, not an engine replay - see the module docstring for why)."""

    def test_1_and_4_settles_option_1_and_option_4(
        self, session_factory, monkeypatch
    ) -> None:
        fixture = TestGroupBDidYouMeanPickByTypedLabelsReachesTheTool()
        fixture._seed_pending(session_factory)
        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            reference_target="dym",
            reference_positions=[1, 4],
            document=[],
            status=None,
            order_status=None,
        )
        mcp_call, calls = _mcp_double(other=lambda name, args: json.dumps(_order_envelope([])))
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="1 and 4",
            msg_id="zzt-b2-1-and-4",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        order_calls = [args for name, args in calls if name in ORDER_TOOLS_LOCAL]
        assert order_calls, f"no order tool was ever called: {calls!r}"
        order_ids = {str(u) for u in (order_calls[0].get("order_ids") or [])}
        assert order_ids == {fixture.OPTION_A_UUID, fixture.OPTION_D_UUID}, (
            f"reference_positions [1, 4] is an ARRAY - both positions must settle "
            f"(option 1 PS202609-0320, option 4 PS202609-0263): {order_calls[0]!r}"
        )

        state = _state_of(session_factory)
        focus_after = state.get("focus") or {}
        order_extra_after = {
            e.get("raw") for e in (focus_after.get("extra") or {}).get("order") or []
        }
        assert fixture.MISSED_RAW_1 not in order_extra_after, order_extra_after
        assert fixture.MISSED_RAW_2 not in order_extra_after, order_extra_after


class TestGroupB2AMissThatAlsoEarnsACsMemberOfferPrintsOneNumberedList:
    """(a) Turn 99c114fd's own precedence collision, pinned directly at
    `answer_bridge.answer_for` - the function both `_miss_question` (the did-you-mean
    roster) and `tail/outcome.py::cs_offer_gate` (the member offer) live inside. This is
    the SAME level `test_rearch_r4_bridge_miss.py` already tests this exact seam at -
    reaching it through the full engine would additionally need the live HTTP resolver
    (for the did-you-mean fuzzy match) and the real Team/AgentTeam/AccessAgentService
    roster chain (for the member offer) both live, which no existing fixture in this
    suite stands up; `list_team_roster` (the roster read) is doubled here instead, the
    SAME "the MCP tool boundary is the double" convention this whole file already
    uses one layer up.

    owner correction 21 Sep: yes = round robin, roster rides the offer reply. The
    captain's original two-step proposal (roster shows only AFTER a bare "yes") is
    wrong - replying "yes" to a customer service escalation offer with no member
    preference means assign by round robin IMMEDIATELY, exactly as production. The
    member roster instead rides IN THE SAME REPLY as the offer (production wording,
    turn 99c114fd's own second message: "To escalate, choose who to route to. ... Or
    just reply 'yes' and we'll assign automatically."), continuing the did-you-mean
    roster's OWN numbering (orders 1..6, members 7..11) - numbers never repeat inside
    one reply, and ONE pending carries both option groups (each option already
    carrying its own `entity_type`, `customer_order` or `member`)."""

    OPTION_1 = "PS202609-0320"
    OPTION_1_UUID = str(uuid.uuid4())
    OPTION_2 = "PS202609-0325"
    OPTION_2_UUID = str(uuid.uuid4())
    OPTION_3 = "PS202609-0321"
    OPTION_3_UUID = str(uuid.uuid4())
    OPTION_4 = "PS202609-0263"
    OPTION_4_UUID = str(uuid.uuid4())
    OPTION_5 = "PS202609-0330"
    OPTION_5_UUID = str(uuid.uuid4())
    OPTION_6 = "PS202609-0310"
    OPTION_6_UUID = str(uuid.uuid4())
    MISSED_RAW_1 = "PS202609-0374"
    MISSED_RAW_2 = "PS202609-0363"
    # The five-member CS roster, real recorded names/ids off turn 99c114fd's own live
    # `result_set` (`.claude/handpass/hp12-turns-21sep.json`) - continuous numbering
    # means they land at positions 7..11, right after the six order options above.
    MEMBER_7_LABEL = "Sandy Lim"
    MEMBER_7_RESPOND_ID = "1136807"
    MEMBER_8_LABEL = "Lin"
    MEMBER_8_RESPOND_ID = "1136805"
    MEMBER_9_LABEL = "Nur"
    MEMBER_9_RESPOND_ID = "1136799"
    MEMBER_10_LABEL = "Emily"
    MEMBER_10_RESPOND_ID = "1136808"
    MEMBER_11_LABEL = "Zilin Poon"
    MEMBER_11_RESPOND_ID = "1204233"

    def _order_options(self) -> list[dict[str, Any]]:
        codes = [
            (self.OPTION_1, self.OPTION_1_UUID, 1),
            (self.OPTION_2, self.OPTION_2_UUID, 2),
            (self.OPTION_3, self.OPTION_3_UUID, 3),
            (self.OPTION_4, self.OPTION_4_UUID, 4),
            (self.OPTION_5, self.OPTION_5_UUID, 5),
            (self.OPTION_6, self.OPTION_6_UUID, 6),
        ]
        return [
            {
                "code": code,
                "uuid": uid,
                "label": code,
                "uuids": [uid],
                "payload": {"value": code},
                "position": pos,
                "entity_type": "customer_order",
            }
            for code, uid, pos in codes
        ]

    def _member_options(self) -> list[dict[str, Any]]:
        rows = [
            (self.MEMBER_7_LABEL, self.MEMBER_7_RESPOND_ID, 7),
            (self.MEMBER_8_LABEL, self.MEMBER_8_RESPOND_ID, 8),
            (self.MEMBER_9_LABEL, self.MEMBER_9_RESPOND_ID, 9),
            (self.MEMBER_10_LABEL, self.MEMBER_10_RESPOND_ID, 10),
            (self.MEMBER_11_LABEL, self.MEMBER_11_RESPOND_ID, 11),
        ]
        return [
            {
                "position": pos,
                "label": label,
                "entity_type": "member",
                "uuid": str(uuid.uuid4()),
                "payload": {"respond_user_id": respond_id},
            }
            for label, respond_id, pos in rows
        ]

    def _seed_combined_pending(
        self, session_factory, member_options: list[dict[str, Any]] | None = None
    ) -> None:
        """The state AFTER turn 99c114fd's own reply, under the owner's correction: a
        single `customer_order_pick` pending whose 11 options span both groups -
        hand-seeded (not chained off a dry-run preview, which writes nothing a second
        turn could read back), the same convention `TestGroupB2CDCustomerService
        EscalationAlwaysShowsTheMemberPicker` uses one file over. An explicit
        `member_options` lets a caller keep its own reference to the seeded rows
        (each carrying a fresh random `uuid` from `_member_options`) instead of
        calling `_member_options()` a second time and getting different uuids."""
        _seed_contact_and_get(session_factory)
        _seed_state(
            session_factory,
            focus=_focus(
                domains=["order"],
                extra={
                    "order": [
                        {
                            "raw": self.MISSED_RAW_1,
                            "hint": "order",
                            "confident": True,
                            "canonical_code": None,
                            "hint_confident": True,
                            "current_message": False,
                        },
                        {
                            "raw": self.MISSED_RAW_2,
                            "hint": "order",
                            "confident": True,
                            "canonical_code": None,
                            "hint_confident": True,
                            "current_message": False,
                        },
                    ],
                    "category": [],
                    "customer_order": [],
                    "attachment_type": [],
                    "inbound_shipment": [],
                },
            ),
            open_question={
                "kind": "customer_order_pick",
                "team": "customer_service",
                "expects": None,
                "options": self._order_options() + (member_options or self._member_options()),
                "payload": {"domain": "order", "escalate_offered": True},
                "asked_at_turn": 1,
            },
        )

    def test_the_reply_prints_exactly_one_numbered_list_and_keeps_the_dym_pending(
        self, session_factory, monkeypatch
    ) -> None:
        """owner correction 21 Sep: yes = round robin, roster rides the offer reply."""
        from app.services import team_roster_service
        from app.services.chatbot import answer_bridge

        raw1, raw2 = self.MISSED_RAW_1, self.MISSED_RAW_2

        def _alt(code: str) -> dict[str, Any]:
            return {
                "canonical_code": code,
                "entity_type": "customer_order",
                "uuid": str(uuid.uuid4()),
                "display": {"order_number": code},
                "match_tier": "fuzzy",
            }

        parser = {
            "domain_hint": "order",
            "intent_hint": "check_order",
            "message_type": "business_query",
            "entities": [
                {"raw": raw1, "hint": "order", "current_message": True, "confident": True},
                {"raw": raw2, "hint": "order", "current_message": True, "confident": True},
            ],
            "routing": {
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
            },
            "access_levels": [],
        }
        resolved = {
            "resolutions": [
                {
                    "token": raw1,
                    "matches": [],
                    "alternatives": [
                        _alt(self.OPTION_1),
                        _alt(self.OPTION_2),
                        _alt(self.OPTION_3),
                    ],
                },
                {
                    "token": raw2,
                    "matches": [],
                    "alternatives": [
                        _alt(self.OPTION_4),
                        _alt(self.OPTION_5),
                        _alt(self.OPTION_6),
                    ],
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

        monkeypatch.setattr(
            team_roster_service,
            "list_team_roster",
            lambda *a, **k: [
                {
                    "user_id": "zzt-b2a-u1",
                    "name": self.MEMBER_7_LABEL,
                    "respond_user_id": self.MEMBER_7_RESPOND_ID,
                    "email": "s@zzt.example",
                    "sort_order": 1,
                },
                {
                    "user_id": "zzt-b2a-u2",
                    "name": self.MEMBER_8_LABEL,
                    "respond_user_id": self.MEMBER_8_RESPOND_ID,
                    "email": "l@zzt.example",
                    "sort_order": 2,
                },
                {
                    "user_id": "zzt-b2a-u3",
                    "name": self.MEMBER_9_LABEL,
                    "respond_user_id": self.MEMBER_9_RESPOND_ID,
                    "email": "n@zzt.example",
                    "sort_order": 3,
                },
                {
                    "user_id": "zzt-b2a-u4",
                    "name": self.MEMBER_10_LABEL,
                    "respond_user_id": self.MEMBER_10_RESPOND_ID,
                    "email": "e@zzt.example",
                    "sort_order": 4,
                },
                {
                    "user_id": "zzt-b2a-u5",
                    "name": self.MEMBER_11_LABEL,
                    "respond_user_id": self.MEMBER_11_RESPOND_ID,
                    "email": "z@zzt.example",
                    "sort_order": 5,
                },
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
                "contact": {"id": "zzt-b2a-contact"},
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

        # Test setup sanity - the did-you-mean roster's own text is present.
        assert self.OPTION_1 in text, f"test setup sanity, did-you-mean text: {text!r}"

        numbered_list_starts = re.findall(r"(?m)^\s*1\.\s", text)
        assert len(numbered_list_starts) <= 1, (
            "exactly ONE numbered list may appear in a single reply, continuously "
            f"numbered 1..11 - owner correction 21 Sep: {text!r}"
        )
        numbers = [int(n) for n in re.findall(r"(?m)^\s*(\d+)\.\s", text)]
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"numbers must never repeat inside one reply - ONE continuous numbering "
            f"1..11, orders first then members: {text!r}"
        )
        assert len(numbers) == 11, (
            f"6 did-you-mean order options + 5 CS members must land in ONE list: {text!r}"
        )
        assert self.MEMBER_8_LABEL.lower() in text.lower(), (
            f"the member roster must ride in the SAME reply as the did-you-mean offer, "
            f"not a later follow-up: {text!r}"
        )
        assert "customer service" in text.lower() or "'yes'" in text.lower(), (
            f"the reply must still offer to escalate to customer service: {text!r}"
        )
        assert "assign automatically" in text.lower() or "we'll assign" in text.lower(), (
            f"the bare-'yes' auto-assign sentence must be present, production wording "
            f"off turn 99c114fd: {text!r}"
        )

        assert answer.question is not None
        options = answer.question.options or []
        assert len(options) == 11, (
            f"the pending must let BOTH option groups be answered - 11 entries total: "
            f"{options!r}"
        )
        positions_seen = sorted(o.get("position") for o in options)
        assert positions_seen == list(range(1, 12)), (
            f"no position may repeat and none may be skipped: {options!r}"
        )
        member_at_8 = next((o for o in options if o.get("position") == 8), None)
        assert member_at_8 is not None and member_at_8.get("entity_type") == "member", (
            f"position 8 must be a member option: {options!r}"
        )
        assert (member_at_8.get("payload") or {}).get("respond_user_id") == self.MEMBER_8_RESPOND_ID, (
            f"position 8 must be Lin, respond_user_id {self.MEMBER_8_RESPOND_ID}: "
            f"{options!r}"
        )

    def test_follow_up_1_and_4_settles_orders_1_and_4(
        self, session_factory, monkeypatch
    ) -> None:
        """owner correction 21 Sep: yes = round robin, roster rides the offer reply.
        The combined pending's ORDER half must still answer the same way the
        6-option-only roster does (`TestGroupB2OneNumberedListPerMessage`) - the
        member half riding at 7..11 must not disturb it."""
        self._seed_combined_pending(session_factory)
        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            reference_target="dym",
            reference_positions=[1, 4],
            document=[],
            status=None,
            order_status=None,
        )
        mcp_call, calls = _mcp_double(other=lambda name, args: json.dumps(_order_envelope([])))
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="1 and 4",
            msg_id="zzt-b2a-1-and-4",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        order_calls = [args for name, args in calls if name in ORDER_TOOLS_LOCAL]
        assert order_calls, f"no order tool was ever called: {calls!r}"
        order_ids = {str(u) for u in (order_calls[0].get("order_ids") or [])}
        assert order_ids == {self.OPTION_1_UUID, self.OPTION_4_UUID}, (
            f"positions 1 and 4 are both order options - the member half riding "
            f"alongside must not change which uuids reach the tool: {order_calls[0]!r}"
        )

    def test_follow_up_8_assigns_the_member_at_position_8_no_order_pick(
        self, session_factory, monkeypatch
    ) -> None:
        """owner correction 21 Sep: yes = round robin, roster rides the offer reply.
        A position pick into the MEMBER half of the SAME combined roster assigns that
        member directly this turn - no order tool call, no member_offer follow-up.

        Re-pinned for security finding M2 (fixed lane commit f724be767):
        `preferred_assignee_id` must carry the picked option's own `uuid` (a real
        `users.id`), never its `respond_user_id`
        (`app/api/v1/external/next_assignee.py:460-476` resolves it via
        `TeamMember.user_id`; `lanes/escalation.py:219-221` matches it against a
        roster row's own `uuid`). This test's own escalation-lane double is made
        to behave like the real endpoint: given a `users.id` it looks up THAT
        member's own `respond_user_id` for the assign action, rather than
        echoing the uuid straight back as `respond_user_id`."""
        from app.services.chatbot import engine as engine_mod

        member_options = self._member_options()
        respond_id_by_uuid = {
            opt["uuid"]: opt["payload"]["respond_user_id"] for opt in member_options
        }
        self._seed_combined_pending(session_factory, member_options=member_options)
        assign_calls: list[dict[str, Any]] = []

        def _escalation_double(ctx, item, *, dry_run, session_factory):
            escalation = (((ctx or {}).get("parse") or {}).get("output") or {}).get("escalation") or {}
            assignee_id = escalation.get("preferred_assignee_id") or None
            assign_calls.append({"preferred_assignee_id": assignee_id})
            # The real `/external/next-assignee` handler resolves a `users.id`
            # to that member's own respond id (`user_service.get_member_assignee`)
            # - this double does the same lookup instead of echoing the uuid
            # straight back as `respond_user_id`.
            respond_user_id = respond_id_by_uuid.get(assignee_id) or "zzt-round-robin"
            return {
                "arm": "assign",
                "actions": [
                    {
                        "kind": "assign_conversation",
                        "respond_user_id": respond_user_id,
                        "dry_run": dry_run,
                    },
                ],
                "pending": None,
            }

        monkeypatch.setattr(engine_mod, "run_escalation_lane", _escalation_double)

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            reference_target="result",
            reference_positions=[8],
            document=[],
            status=None,
            order_status=None,
        )
        mcp_call, calls = _mcp_double(other=lambda name, args: json.dumps(_order_envelope([])))
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="8",
            msg_id="zzt-b2a-pick-8",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        order_calls = [args for name, args in calls if name in ORDER_TOOLS_LOCAL]
        assert not order_calls, (
            f"picking the member at position 8 must not also make an order pick: "
            f"{order_calls!r}"
        )
        member_8_uuid = next(
            opt["uuid"] for opt in member_options if opt["position"] == 8
        )
        assert assign_calls and assign_calls[-1]["preferred_assignee_id"] == member_8_uuid, (
            f"the escalation lane must be asked to assign by the picked option's "
            f"own uuid (a real users.id), never its respond_user_id (M2, "
            f"f724be767): {assign_calls!r}"
        )
        assert any(
            a.get("kind") == "assign_conversation"
            and a.get("respond_user_id") == self.MEMBER_8_RESPOND_ID
            for a in (result.actions or [])
        ), (
            f"position 8 (Lin, respond_user_id {self.MEMBER_8_RESPOND_ID}) must be "
            f"assigned directly, no roster shown again: {result.actions!r}"
        )

    def test_follow_up_yes_assigns_round_robin_immediately_no_roster_reply(
        self, session_factory, monkeypatch
    ) -> None:
        """owner correction 21 Sep: yes = round robin, roster rides the offer reply.
        A bare "yes" over the SAME combined pending (`payload.escalate_offered: True`,
        unaffected by the member half riding alongside) assigns by round robin on
        THIS turn - no second roster, no member_offer follow-up pending."""
        from app.services.chatbot import engine as engine_mod

        self._seed_combined_pending(session_factory)
        assign_calls: list[dict[str, Any]] = []

        def _escalation_double(ctx, item, *, dry_run, session_factory):
            escalation = (((ctx or {}).get("parse") or {}).get("output") or {}).get("escalation") or {}
            assignee_id = escalation.get("preferred_assignee_id") or None
            assign_calls.append({"preferred_assignee_id": assignee_id})
            return {
                "arm": "assign",
                "actions": [
                    {"kind": "send_message", "text": "Escalated.", "dry_run": dry_run},
                    {
                        "kind": "assign_conversation",
                        "respond_user_id": assignee_id or "zzt-round-robin",
                        "dry_run": dry_run,
                    },
                ],
                "pending": None,
            }

        monkeypatch.setattr(engine_mod, "run_escalation_lane", _escalation_double)

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            domain_in_message=False,
            entities=[],
            entity_op="reuse",
            is_affirmative=True,
            document=[],
            status=None,
            order_status=None,
        )
        result = _run_turn_dry(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="yes",
            msg_id="zzt-b2a-yes-round-robin",
        )
        assert result.status == "done", result.error
        said = _said(result)

        assert assign_calls, (
            f"a bare 'yes' must reach the escalation lane and assign THIS turn, not "
            f"show a second roster: {result.actions!r}"
        )
        assert not assign_calls[-1]["preferred_assignee_id"], (
            f"a bare yes round-robins, it does not target a specific member: "
            f"{assign_calls!r}"
        )
        assert any(a.get("kind") == "assign_conversation" for a in (result.actions or [])), (
            f"the assignment action must be in THIS turn's actions: {result.actions!r}"
        )
        assert not re.search(r"(?m)^\s*1\.\s", said), (
            f"no roster may print on the acceptance turn - assignment is immediate: "
            f"{said!r}"
        )


# --------------------------------------------------------------------------- #
# GROUP F - a customer pick's header must name every ledger it covers, never a code.
# --------------------------------------------------------------------------- #


class TestGroupFCustomerPickHeaderNamesEveryLedgerNotACode:
    def test_pick_names_every_ledger_the_option_covers_not_the_code(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turns 42e57c4e (offer) -> 002a8f5b ("2"). The picked option covers
        THREE distinct customer ledger uuids (the live tool call's own `customer_ids`
        carried exactly 3) - owner ruling mid-task: "print the 3 customer names". The
        option's own rollup `code`/`label` is deliberately DIFFERENT from the three
        real ledger names seeded below, so a fix that just echoes `option.name` cannot
        satisfy this test - it must resolve the real ledgers."""
        _seed_contact_and_get(session_factory)
        ids = [str(uuid.uuid4()) for _ in range(3)]
        names = [
            "ZZT BATH GROUP - A/C I",
            "ZZT BATH GROUP - A/C II",
            "ZZT BATH GROUP - A/C III",
        ]
        db = session_factory()
        for cid, name in zip(ids, names):
            db.add(
                Customer(
                    id=cid,
                    customer_code=unique_code("BATH")[:50],
                    customer_name=name,
                    company_id=DEFAULT_COMPANY_ID,
                )
            )
        db.commit()

        option_code = "ZZT-B094"
        options = [
            {
                "code": "ZZT-B110",
                "name": "ZZT OTHER CO",
                "uuid": str(uuid.uuid4()),
                "label": "ZZT OTHER CO",
                "uuids": [str(uuid.uuid4())],
                "payload": {},
                "position": 1,
                "entity_type": "customer",
            },
            {
                "code": option_code,
                "name": "ZZT BATH GROUP",
                "uuid": ids[0],
                "label": "ZZT BATH GROUP",
                "uuids": list(ids),
                "payload": {},
                "position": 2,
                "entity_type": "customer",
            },
        ]
        _seed_state(
            session_factory,
            focus=_focus(domains=["order"], status="delivered", document=["DO"]),
            open_question={
                "kind": "customer_pick",
                "expects": None,
                "options": options,
                "team": "customer_service",
                "asked_at_turn": 1,
                "payload": {"domain": "order", "status": "delivered"},
            },
        )

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            reference_target="result",
            reference_positions=[2],
            document=[],
            status=None,
            order_status=None,
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name in ORDER_TOOLS_LOCAL:
                return json.dumps(_order_envelope([]))  # a genuine MISS, matching live
            return _unknown_envelope()

        mcp_call, calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="2",
            msg_id="zzt-f-pick",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        # DO-ASK-SIMPLIFY rule 1 (owner, 2 Oct 2026) replaces "print the 3 customer names":
        # the header names each group once (owner, 2 Oct: group names only, no count). These names carry no
        # bracketed ledger marker, so they are three families to `ledger_family_key`.
        assert f"Customer: {', '.join(names)}" in said, (
            f"the miss header must name each group once, no count: {said!r}"
        )
        assert option_code not in said, f"no customer CODE must ever reach the reply text: {said!r}"

        # captain ruling 21 Sep: contract 36 keeps the customer roster open across a miss


# --------------------------------------------------------------------------- #
# GROUP H - a missed leg of a multi-domain ask names itself, never the bare
# NO_RESULT_INTRO.
# --------------------------------------------------------------------------- #


class TestGroupHMultiDomainMissNamesItself:
    def test_inventory_hit_incoming_miss_po_miss_names_the_missed_domain(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turn d16347c2 ("stock and eta"): inventory HIT (a direct ask),
        incoming MISS (a direct ask), PO-rung MISS (the ladder's own probe after the
        incoming ask missed)."""
        _seed_contact_and_get(session_factory)
        code = unique_code("H1PROD")
        product_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                status="delivered",
                document=["SO"],
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
            intent_hint="check_stock",
            domain_hint="inventory",
            domain_in_message=True,
            entities=[],
            entity_op="reuse",
            asks=[
                {"domain": "inventory", "intent": "check_stock"},
                {"domain": "incoming", "intent": "check_incoming"},
            ],
            document=[],
            status=None,
            order_status=None,
        )

        def _primary(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(_stock_hit(code))
            if name == INCOMING_TOOL:
                return json.dumps(_incoming_miss())
            if name == PO_TOOL:
                return json.dumps(_order_envelope([]))  # PO-rung MISS too, matching d16347c2
            return _unknown_envelope()

        # The multi-domain fan-out's OWN ladder (`turn/fetch.py::_climb`, walked off
        # `chatbot_domains.ladder`) runs every rung, including the PO one, through
        # `FetchServices.mcp_call` - the SAME seam the primary asks use, never
        # `AnswerServices.mcp_probe` (that is the OTHER, single-domain-miss ladder,
        # `answer_bridge._run_crossdomain_ladder`/`lanes.business.answer.run_
        # crossdomain`, which this multi-domain "asks" turn never reaches - `run_fetch`'s
        # own docstring: "a rung climbed here is fetched... bridge_owns_ladder" decides
        # which one owns it, and a multi-domain plan is never bridge-owned). Measured
        # directly this session: wiring PO_TOOL through `_capturing_probe` instead left
        # the rung unprobed and the PO block never rendered.
        mcp_call, fetch_calls = _mcp_double(other=_primary)

        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="stock and eta",
            msg_id="zzt-h1-inventory-hit-incoming-po-miss",
            mcp_call=mcp_call,
            # The PO rung is GATED on this reveal (`answer._CROSSDOMAIN_RUNG_GRANT`,
            # `turn/fetch.py::_rung_grant_missing`) - omitting it makes the rung look
            # "never offered" rather than "tried and missed", which is a different shape
            # from the live trace's own `rungs_tried: ["purchase_order"]`.
            attributes=["purchase_orders.placed"],
        )
        assert result.status == "done", result.error
        said = _said(result)

        po_calls = [args for name, args in fetch_calls if name == PO_TOOL]
        assert po_calls, (
            f"test setup sanity: the PO rung must actually be tried (the live trace's "
            f"own crossdomain event names it in rungs_tried) before this test's "
            f"assertions about what it PRINTED mean anything: {fetch_calls!r}"
        )

        assert "No matching results found." not in said, (
            f"a multi-domain miss must never fall back to the bare NO_RESULT_INTRO: "
            f"{said!r}"
        )
        assert "incoming" in said.lower(), (
            f"the miss sentence must name the domain that missed: {said!r}"
        )
        assert code in said, f"the miss sentence must name the product: {said!r}"

        stock_block_idx = said.find("*Total:*")
        incoming_miss_idx = said.lower().find("incoming")
        assert stock_block_idx != -1 and incoming_miss_idx != -1, said
        assert stock_block_idx < incoming_miss_idx, (
            f"the stock HIT block must print BEFORE the incoming-miss sentence: {said!r}"
        )

    def test_incoming_miss_po_hit_inventory_hit_orders_miss_first_never_as_delivered_orders(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turn 92280f52 ("stock and eta"): incoming MISS (direct ask), PO-rung
        HIT (the ladder's own probe), inventory HIT (a second direct ask). Owner
        ruling: the incoming-miss sentence must print BEFORE the PO rows, and the PO
        rows must be introduced as purchase orders - never as "delivered orders"
        (group A's own leak, which the live trace shows riding along here too since
        this turn's carried focus was ALSO status "delivered")."""
        _seed_contact_and_get(session_factory)
        code = unique_code("H2PROD")
        product_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                status="delivered",
                document=["SO"],
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
            domain_in_message=True,
            entities=[],
            entity_op="reuse",
            asks=[
                {"domain": "incoming", "intent": "check_incoming"},
                {"domain": "inventory", "intent": "check_stock"},
            ],
            document=[],
            status=None,
            order_status=None,
        )

        po_number = "ZZT-H2-PO-1"

        def _primary(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                return json.dumps(_incoming_miss())
            if name == STOCK_TOOL:
                return json.dumps(_stock_hit(code))
            if name == PO_TOOL:
                return json.dumps(_po_hit(code, po_number))  # PO-rung HIT, matching 92280f52
            return _unknown_envelope()

        # SAME seam correction as H1 above: the multi-domain fan-out's own ladder
        # (`turn/fetch.py::_climb`) probes a rung through `FetchServices.mcp_call`,
        # never `AnswerServices.mcp_probe`.
        mcp_call, fetch_calls = _mcp_double(other=_primary)

        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="stock and eta",
            msg_id="zzt-h2-incoming-miss-po-hit-inventory-hit",
            mcp_call=mcp_call,
            attributes=["purchase_orders.placed"],
        )
        assert result.status == "done", result.error
        said = _said(result)

        po_calls = [args for name, args in fetch_calls if name == PO_TOOL]
        assert po_calls, (
            f"test setup sanity: the PO rung must actually be tried and must HIT (the "
            f"live trace's own crossdomain event: answered='purchase_order'): "
            f"{fetch_calls!r}"
        )

        assert "No matching results found." not in said, said
        assert "incoming" in said.lower(), said
        assert code in said, said

        incoming_miss_idx = said.lower().find("incoming")
        po_idx = said.find(po_number)
        assert incoming_miss_idx != -1 and po_idx != -1, said
        assert incoming_miss_idx < po_idx, (
            f"the incoming-miss sentence must print BEFORE the PO rows: {said!r}"
        )
        assert "delivered orders" not in said.lower(), (
            f"the PO rows must be introduced as purchase orders, never as delivered "
            f"orders: {said!r}"
        )
