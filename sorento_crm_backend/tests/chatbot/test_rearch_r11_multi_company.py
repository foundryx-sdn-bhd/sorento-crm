"""Hand pass 11, finding 3 - multi-company parity on a HIT (owner: "for items that exist
in sorento and mocha, we search both sides ... offer to escalate to either company and
clarify the company when unclear"). RED, test-first.

Prod turn "M90SS-DIY kim seng jaya send yet": every row carries "*Company:* Mocha", then
"*Sorento:* no orders records found for customer KIM SENG JAYA SDN BHD, product
M90SS-DIY." and the offer reads "escalate to *Sorento* customer service team?". Lane: same
two orders, no Company field, no silent-company line, plain offer. Second local chain
"SRTRT203 Technical drawing" -> miss "checked in Mocha and Sorento" -> customer answers
"mocha" -> escalated to "marketing product team" with the company answer unused.

Measured (captain's brief): the production renderer is still on the lane
(`lanes/business/fetch.py:2483-2531`'s silent-company note reads `envelope.lookup_companies`;
the offer team from `gate.company_team` at answer.py:1617/3735; `escalation._clarify_over`'s
company pairs) - the lane's hit path is not feeding it. The coder measures the drop point.

Harness: the REAL resolver against a two-company seeded chain (`test_engine_company_scope.py`'s
own helpers - the only harness that exercises company scope for real), the orders tool
stubbed with the envelope the backend's `stamp_lookup_companies` produces for a lookup that
spanned two companies (`company_scope.py:437`: per-row `company_name`, top-level
`lookup_companies`). Postgres only, every row seeded. No live parser, no :8766, no key.
"""
from __future__ import annotations

import json
from typing import Any

from app.models.order import Customer
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business.services import FetchServices
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_engine import _parser_output, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_engine_company_scope import (
    _scope_envelope,
    _seed_company,
    _seed_contact,
    _seed_product,
    _seed_workspace,
    _set_completed_lanes,
    _wire_answer_services,
    _wire_real_resolve_entity,
)
from app.services.chatbot.lanes.escalation import _next_assignee_body, escalation_context
from tests.chatbot.test_rearch_r4_answering_a_miss import _fake_escalation_lane

ORDERS_TOOL = "crm_order_management_orders_list"
MOCHA = "ZZT Mocha Co"
SORENTO = "ZZT Sorento Co"
PRODUCT_CODE = "ZZTM90SS-DIY"
# Deliberately NOT "ZZT"-prefixed like `PRODUCT_CODE`/`MOCHA`/`SORENTO`: the AND-mode
# resolver's own token-intersection (`entity_resolver._and_max_tier_filter`) treats every
# supplied token as a global set filter across every allowed type, so a shared "ZZT"
# substring between the customer name and the product code makes the PRIMARY AND-mode
# pass find the product alone (non-empty), which then skips `references.py`'s
# AND-empty -> OR-mode fallback - the ONLY path that resolves a customer-hinted token at
# all here. A real customer name never shares a prefix with a real product code, so this
# is a fixture-collision avoidance, not a change of what is being tested.
CUSTOMER_NAME = "QQZ KIM SENG JAYA SDN BHD"
CUSTOMER_CODE = "QQZKSJ001"
CONTACT_ID = "ZZT-contact-r11-multico"


def _seed_customer(session_factory: Any, *, company_id: str, name: str, code: str) -> str:
    """`code` is shared across a two-company chain's calls (mc-label-n8n /
    `gate.py`'s own "N exact hits sharing ONE code are the same customer in several
    companies, not a choice" collapse - the real ledger cross-company pattern, `customer
    300-C043 ... cross-company`, `references.py:1493`). A per-company UNIQUE code would
    instead make the gate treat the two rows as a genuine ambiguity and mint a customer
    picker, which is a fixture artifact this file is not testing."""
    db = session_factory()
    row = Customer(customer_code=code[:50], customer_name=name, is_active=True, company_id=company_id)
    db.add(row)
    db.commit()
    return row.id


def _order_row(company_name: str, so_number: str) -> dict[str, Any]:
    return {
        "fields": [
            {"key": "company_name", "label": "Company", "value": company_name},
            {"key": "order_number", "label": "Order Number", "value": so_number},
            {"key": "customer_name", "label": "Customer", "value": CUSTOMER_NAME},
            {"key": "product_code", "label": "Product Code", "value": PRODUCT_CODE},
            {"key": "status", "label": "Status", "value": "Confirmed"},
        ]
    }


def _orders_envelope(rows: list[dict[str, Any]], lookup: list[dict[str, str]]) -> dict[str, Any]:
    """What the MCP presenter hands back for a lookup the backend labelled per company:
    `lookup_companies` is on the payload whenever the scope spanned more than one."""
    return {
        "result_type": "orders",
        "intro": "Here are the orders I found." if rows else "No matching results found.",
        "items": rows,
        "has_result": bool(rows),
        "lookup_companies": lookup,
    }


def _wire(session_factory, system_settings_row, monkeypatch, *, envelope: dict[str, Any]):
    set_chatbot_switches(session_factory, business_lane=True)
    _set_completed_lanes(session_factory, system_settings_row, ["business_query"])
    _wire_real_resolve_entity(monkeypatch)
    _wire_answer_services(monkeypatch)
    calls: list[tuple[str, dict[str, Any]]] = []

    def _mcp_call(name: str, args: dict[str, Any]) -> str:
        calls.append((name, dict(args)))
        if name == ORDERS_TOOL:
            return json.dumps(envelope)
        return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
    )
    return calls


def _two_company_chain(session_factory) -> dict[str, str]:
    a = _seed_company(session_factory, name=MOCHA)
    b = _seed_company(session_factory, name=SORENTO)
    product_a = _seed_product(session_factory, company_id=a, code=PRODUCT_CODE)
    product_b = _seed_product(session_factory, company_id=b, code=PRODUCT_CODE)
    customer_a = _seed_customer(session_factory, company_id=a, name=CUSTOMER_NAME, code=CUSTOMER_CODE)
    customer_b = _seed_customer(session_factory, company_id=b, name=CUSTOMER_NAME, code=CUSTOMER_CODE)
    workspace_id = _seed_workspace(session_factory)
    _seed_contact(
        session_factory, contact_id=CONTACT_ID, phone="+60000000911",
        workspace_id=workspace_id, company_ids=[a, b],
    )
    return {
        "a": a, "b": b, "product_a": product_a, "product_b": product_b,
        "customer_a": customer_a, "customer_b": customer_b,
    }


def _order_verdict() -> dict[str, Any]:
    return _parser_output(
        intent_hint="check_order", domain_hint="order",
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        **do_window(),
        entities=[
            {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            {"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
        ],
        routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries", "team_source": None},
    )


def _said(result) -> str:
    return "\n".join(
        [((result.reply or {}).get("text") or "")]
        + [a.get("text") or "" for a in (result.actions or []) if isinstance(a, dict)]
    )


def _open_question(session_factory) -> dict[str, Any]:
    from sqlalchemy import text

    db = session_factory()
    row = db.execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": CONTACT_ID},
    ).first()
    raw = row.session_vars if row is not None else {}
    sv = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return sv.get("open_question") or {}


# --------------------------------------------------------------------------- #
# A hit in ONE of two companies: rows carry Company, the silent company is named in
# main's exact shape, the offer names the silent company's own team
# --------------------------------------------------------------------------- #


class TestHitInOneCompanyNamesTheSilentOne:
    def test_both_companies_are_fetched_rows_carry_company_and_the_silent_one_is_named(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope(
            [_order_row(MOCHA, "ZZTM2609-0881"), _order_row(MOCHA, "ZZTM2609-0882")],
            [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}],
        )
        calls = _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(CONTACT_ID, message_id="ZZT-r11-multico-hit", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        order_calls = [args for name, args in calls if name == ORDERS_TOOL]
        assert order_calls, calls
        sent_products = {u for args in order_calls for u in (args.get("product_ids") or [])}
        sent_customers = {u for args in order_calls for u in (args.get("customer_ids") or [])}
        assert {ids["product_a"], ids["product_b"]} <= sent_products, (
            "orders must be fetched for BOTH companies' product rows", order_calls
        )
        assert {ids["customer_a"], ids["customer_b"]} <= sent_customers, (
            "orders must be fetched for BOTH companies' customer rows", order_calls
        )

        said = _said(result)
        assert f"*Company:* {MOCHA}" in said, said
        # `fetch.py:2521-2531`'s ONE silent-company sentence, main's exact shape.
        assert f"*{SORENTO}:* no orders records found for" in said, said
        # The offer names the SILENT company's customer service team.
        offer = said[said.lower().rfind("escalate"):] if "escalate" in said.lower() else ""
        assert offer, ("a one-company hit still offers the other company's team", said)
        assert SORENTO in offer and "customer service team" in offer, offer


# --------------------------------------------------------------------------- #
# A miss in BOTH: "checked in X and Y", then the escalate answer clarifies the company
# and "mocha" routes to the Mocha team
# --------------------------------------------------------------------------- #


class TestMissInBothCompaniesClarifiesTheCompany:
    def test_checked_in_both_then_mocha_routes_the_escalation_to_mocha(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope([], [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}])
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(CONTACT_ID, message_id="ZZT-r11-multico-miss", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        said = _said(result)
        # `answer.py:3112`'s scope statement, both companies in `_and_list` order.
        assert "checked in" in said, said
        assert MOCHA in said and SORENTO in said, said
        pending = _open_question(session_factory)
        labels = [o.get("label") or "" for o in (pending.get("options") or [])]
        assert any(MOCHA in label for label in labels) and any(SORENTO in label for label in labels), (
            "the escalate offer over a two-company miss must clarify WHICH company", pending
        )

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True,
                # `_company_keys`/`CO_ALIASES` (`lanes/escalation.py`) only match a
                # company whose OWN `company_name` is literally "sorento"/"mocha"/
                # "cabana" (production's real, unprefixed company names) or an alias
                # keyed to that exact string - this file's `ZZT`-prefixed test
                # company names do not alias to the bare word "mocha" (tester 49's
                # own measurement, `TestBlocker2aCompanyWordRoutesByRealCompanyId`'s
                # docstring note). The seeded name, lowered, is what a real customer
                # typing the company's own name back would resolve on.
                escalation={"is_escalation_confirmation": True, "company_pick": MOCHA.lower()},
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(CONTACT_ID, message_id="ZZT-r11-multico-mocha", text="mocha"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "out_of_scope", (result2.branch_kind, result2.error)
        assert len(calls) == 1, calls
        ctx, item = calls[0]
        # Strengthened (tester 50, hand pass 11 company-routing pins): the company
        # answer must route by Mocha's REAL `company_id`, not merely carry the name
        # somewhere in the context blob - `MOCHA in json.dumps(ctx)` was satisfied by
        # the name simply riding along in the injected roster plan regardless of
        # whether `_company_keys` matched anything at all (tester 49's own flag,
        # `.claude/handoffs/20260921T0703Z-rearch-tester49-company-routing-reds.md`).
        ec = escalation_context(item, ctx=ctx)
        assert ec.get("company_name") == MOCHA, ec
        assert ec.get("company_id") == ids["a"], (
            f"the company answer must resolve to Mocha's real company id, not just "
            f"its name: {ec!r}"
        )
        next_body = _next_assignee_body(ctx, ec)
        assert next_body.get("company_id") == ids["a"], (
            f"the round robin must be handed Mocha's real company id: {next_body!r}"
        )
        output = ((ctx.get("parse") or {}).get("output")) or {}
        assert (output.get("routing") or {}).get("suggested_team") == "customer_service", output.get("routing")


# --------------------------------------------------------------------------- #
# CONTROL, green today: a single-company hit is byte-identical to today
# --------------------------------------------------------------------------- #


class TestSingleCompanyHitIsUnchanged:
    def test_no_silent_company_line_and_no_checked_in(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        a = _seed_company(session_factory, name=MOCHA)
        product_a = _seed_product(session_factory, company_id=a, code=PRODUCT_CODE)
        customer_a = _seed_customer(session_factory, company_id=a, name=CUSTOMER_NAME, code=CUSTOMER_CODE)
        workspace_id = _seed_workspace(session_factory)
        _seed_contact(
            session_factory, contact_id=CONTACT_ID, phone="+60000000912",
            workspace_id=workspace_id, company_ids=[a],
        )
        envelope = _orders_envelope([_order_row(MOCHA, "ZZTM2609-0881")], [])
        calls = _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(CONTACT_ID, message_id="ZZT-r11-multico-single", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        order_calls = [args for name, args in calls if name == ORDERS_TOOL]
        assert order_calls and set(order_calls[0].get("product_ids") or []) == {product_a}, order_calls
        assert set(order_calls[0].get("customer_ids") or []) == {customer_a}, order_calls
        said = _said(result)
        assert "no orders records found" not in said, said
        assert "checked in" not in said, said
        assert "ZZTM2609-0881" in said, said
