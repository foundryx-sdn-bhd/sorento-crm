"""Phase 2 RED tests - the fetch layer forces a scoped contact's `customer_ids` (D4).

`documentation/plans/chatbot/PLAN-chatbot-customer-scope-29sep.md` D4 and
`chatbot-customer-scope-29sep-acceptance-criteria.md` AC-CS-31 and AC-CS-32.

`fetch.entity_ids_transformer`, given `semantic_input.scope_customer_ids`, forces
`customer_ids` on every tool in `fetch.CUSTOMER_SCOPED_TOOLS`; `fetch.ScopeViolation` is
raised (no call made) for any requested id outside the scope. The set itself is pinned to
the MCP catalogue so a new tool taking `customer_ids` fails here until it is classified.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.chatbot.lanes.business import fetch
from app.services.mcp_tool_capability_service import _load_catalog_specs

#: The eight tools of D4, spelled out so a rename in the set shows up as a diff here.
SCOPED_TOOLS = (
    "crm_order_management_orders_list",
    "crm_order_management_orders_by_product_list",
    "crm_outstanding_report",
    "crm_sales_report",
    "crm_top_selling_report",
    "crm_report_ask",
    "crm_order_analytics",
    "crm_master_customers_list",
)

#: A tool that takes no customer scope: a stock read.
UNSCOPED_TOOL = "crm_inventory_stock_balance_list"


def _customer(uid: str, code: str = "ZZT-C") -> dict[str, Any]:
    return {"uuid": uid, "entity_type": "customer", "canonical_code": code}


def _trigger(tool: str, scope: list[str] | None, entities: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    semantic_input: dict[str, Any] = {"contact_id": "1", "space_id": "364817", **extra}
    if scope is not None:
        semantic_input["scope_customer_ids"] = scope
    return {"tool": tool, "entities": entities, "semantic_input": semantic_input}


@pytest.fixture
def ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


@pytest.mark.parametrize("tool", SCOPED_TOOLS)
class TestTransformerForcesTheScope:
    def test_none_requested_gives_the_scope(self, tool, ids) -> None:
        """AC-CS-31: no customer asked for -> `customer_ids` is the whole scope."""
        a, b, _z = ids
        out = fetch.entity_ids_transformer(_trigger(tool, [a, b], []))
        if tool == "crm_report_ask":
            # REPORT-ENGINE 1b N3: the route forces the links itself, so none are added here.
            assert not out.get("customer_ids"), out
            return
        assert out["customer_ids"] == [a, b], out

    def test_a_subset_requested_is_kept(self, tool, ids) -> None:
        """AC-CS-31: a requested subset of the scope -> exactly that subset."""
        a, b, _z = ids
        out = fetch.entity_ids_transformer(_trigger(tool, [a, b], [_customer(a)]))
        assert out["customer_ids"] == [a], out

    def test_an_id_outside_the_scope_raises_before_any_call(self, tool, ids) -> None:
        """AC-CS-31: one requested id outside the scope -> `ScopeViolation`, so no tool call."""
        a, b, z = ids
        with pytest.raises(fetch.ScopeViolation):
            fetch.entity_ids_transformer(_trigger(tool, [a, b], [_customer(z)]))
        with pytest.raises(fetch.ScopeViolation):
            fetch.entity_ids_transformer(_trigger(tool, [a, b], [_customer(a), _customer(z)]))

    def test_customer_query_is_never_sent(self, tool, ids) -> None:
        """AC-CS-31: `customer_query` is never sent for a scoped contact."""
        a, _b, _z = ids
        out = fetch.entity_ids_transformer(_trigger(tool, [a], [], customer_query="hanlim"))
        # REPORT-ENGINE 1b N3: for crm_report_ask the route forces the links, so none are added here.
        assert out.get("customer_ids") == (None if tool == "crm_report_ask" else [a]), out
        assert "customer_query" not in out, out


class TestUnscopedTools:
    def test_a_tool_outside_the_set_is_untouched(self, ids) -> None:
        """AC-CS-31: tools outside `CUSTOMER_SCOPED_TOOLS` are untouched: no forced ids, and
        a customer entity outside the scope is not a violation there."""
        a, _b, z = ids
        forced = fetch.entity_ids_transformer(_trigger(UNSCOPED_TOOL, [a], []))
        assert "customer_ids" not in forced, forced
        named = fetch.entity_ids_transformer(_trigger(UNSCOPED_TOOL, [a], [_customer(z)]))
        assert named.get("customer_ids") == [z], named

    def test_no_scope_leaves_a_scoped_tool_untouched(self, ids) -> None:
        """AC-CS-31 (unscoped contacts): no `scope_customer_ids` -> today's arguments."""
        a, _b, z = ids
        out = fetch.entity_ids_transformer(_trigger("crm_outstanding_report", None, [_customer(z)]))
        assert out["customer_ids"] == [z], out
        bare = fetch.entity_ids_transformer(_trigger("crm_outstanding_report", None, []))
        assert "customer_ids" not in bare, bare


class TestSetIsPinnedToTheCatalogue:
    def test_the_set_is_the_seven_tools(self) -> None:
        """AC-CS-31: the set named in D4."""
        assert fetch.CUSTOMER_SCOPED_TOOLS == frozenset(SCOPED_TOOLS)

    def test_customer_scoped_tools_pinned_to_catalogue(self) -> None:
        """AC-CS-32: every catalogue tool whose `query_params` carry `customer_ids` is in
        `CUSTOMER_SCOPED_TOOLS`, and the set names nothing the catalogue lacks. A new tool
        taking customer ids fails here until it is classified."""
        carrying = {spec.name for spec in _load_catalog_specs() if "customer_ids" in spec.query_params}
        assert carrying, "the catalogue no longer has a tool taking customer_ids: the pin is stale"
        assert carrying == set(fetch.CUSTOMER_SCOPED_TOOLS), carrying ^ set(fetch.CUSTOMER_SCOPED_TOOLS)


class TestRunFetchAnswersAScopeViolationWithTheRefusal:
    def test_run_fetch_turns_a_scope_violation_into_the_refusal_reply(self, session_factory) -> None:
        """D4: `run_fetch` given `scope_customer_ids == [A]` (the enforced customer scope on
        the lane's ctx) and compatible entities naming customer Z returns the refusal line
        (never the generic "not allowed" text) and calls no tool."""
        from app.services.chatbot.lanes.business import run_fetch
        from app.services.chatbot.lanes.business.services import FetchServices

        a, z = str(uuid.uuid4()), str(uuid.uuid4())
        refusal = "Sorry, that isn't under your account. I can only check on ZZT OWN A."
        calls: list[tuple[str, dict[str, Any]]] = []

        def _mcp(name: str, args: dict[str, Any]) -> Any:
            calls.append((name, args))
            return "{}"

        payload = {
            "gate": {"compatible_entities": [{"uuid": z, "entity_type": "customer", "code": "ZZT-Z"}]},
            "tier_gate": None,
            "ctx": {
                "contact": {"id": "1"},
                "access": {"attributes": ["sales_orders.outstanding"]},
                "parse": {
                    "output": {
                        "domain_hint": "order",
                        "intent_hint": "check_order",
                        "message_type": "business_query",
                        "order_status": "outstanding_both",
                        "entities": [],
                    }
                },
                "customer_scope": {"ids": [a], "enforced": True, "refusal": refusal},
            },
        }
        db = session_factory()
        try:
            fragment = run_fetch(payload, services=FetchServices(mcp_call=_mcp), db=db)
        finally:
            db.close()
        assert calls == []
        assert (fragment.get("fetch") or {}).get("response") == refusal, fragment
        assert "escalate" not in str(fragment).lower()


# --------------------------------------------------------------------------- #
# CHATBOT-SELFREF-SCOPE R4: the fetch and tier-probe refusals name the ids they refused
# --------------------------------------------------------------------------- #


def _scoped_payload(*, scope: list[str], entities: list[dict[str, Any]], parse: dict[str, Any], tier_gate=None):
    return {
        "gate": {"compatible_entities": entities},
        "tier_gate": tier_gate,
        "ctx": {
            "contact": {"id": "1"},
            "access": {"attributes": ["sales_orders.outstanding", "sales_orders.sales_report"]},
            "parse": {"output": {"domain_hint": "order", "intent_hint": "check_order", "message_type": "business_query", "entities": [], **parse}},
            "customer_scope": {
                "ids": scope, "enforced": True,
                "refusal": "Sorry, that isn't under your account. I can only check on ZZT OWN A.",
            },
        },
    }


class TestRefusalsAreTraced:
    def test_the_violation_carries_the_ids_outside_the_scope(self, ids) -> None:
        a, _b, z = ids
        with pytest.raises(fetch.ScopeViolation) as raised:
            fetch.entity_ids_transformer(_trigger("crm_sales_report", [a], [_customer(a), _customer(z)]))
        assert raised.value.dropped == [z]

    def test_the_fetch_refusal_traces_the_dropped_ids(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import run_fetch
        from app.services.chatbot.lanes.business.services import FetchServices
        from app.services.chatbot.trace import TurnTrace

        a, z = str(uuid.uuid4()), str(uuid.uuid4())
        trace = TurnTrace()
        payload = _scoped_payload(
            scope=[a], entities=[{"uuid": z, "entity_type": "customer", "code": "ZZT-Z"}],
            parse={"order_status": "outstanding_both"},
        )
        db = session_factory()
        try:
            fragment = run_fetch(payload, services=FetchServices(mcp_call=lambda n, a_: "{}"), db=db, trace=trace)
        finally:
            db.close()
        assert (fragment.get("fetch") or {}).get("response", "").startswith("Sorry, that isn't under your account")
        (event,) = [e for e in trace.events if e.get("kind") == "customer_scope"]
        assert event["refused"] == "customer_not_permitted"
        assert event["dropped"] == [z]
        assert event["reason"] == "fetch_customer_ids_outside_links"
        assert event["tool"] == "crm_outstanding_report"

    def test_the_tier_probe_refusal_writes_the_trace_event_it_omitted(self, session_factory, monkeypatch) -> None:
        """The probe tool takes no customer ids, so the violation is raised by a stubbed
        transformer: the seam's own handling is what is under test."""
        from app.services.chatbot.lanes.business import fetch as fetch_mod
        from app.services.chatbot.lanes.business import run_fetch
        from app.services.chatbot.lanes.business.services import FetchServices
        from app.services.chatbot.trace import TurnTrace

        a, z = str(uuid.uuid4()), str(uuid.uuid4())

        def _raise(trigger, *, space_id=None):
            raise fetch_mod.ScopeViolation("probe asked outside the scope", dropped=[z])

        monkeypatch.setattr(fetch_mod, "entity_ids_transformer", _raise)
        calls: list[Any] = []
        trace = TurnTrace()
        payload = _scoped_payload(
            scope=[a], entities=[], parse={"domain_hint": "promotion", "intent_hint": "check_promotion"},
            tier_gate={"tier_ask": True, "tier_probe_plan": [{"tier": "dealer", "access_levels": ["Sorento Dealer"]}]},
        )
        db = session_factory()
        try:
            fragment = run_fetch(payload, services=FetchServices(mcp_call=lambda n, a_: calls.append(n) or "{}"), db=db, trace=trace)
        finally:
            db.close()
        assert calls == []
        assert (fragment.get("fetch") or {}).get("response", "").startswith("Sorry, that isn't under your account")
        (event,) = [e for e in trace.events if e.get("kind") == "customer_scope"]
        assert event["reason"] == "tier_probe_customer_ids_outside_links"
        assert event["dropped"] == [z]


class TestMyMeansAllLinkedAccounts:
    """Owner rule Q6a (hand test, 30 Sep 2026): a carried account never narrows a
    self-reference ask; the turn's own ids (the links) stand on both reports."""

    @pytest.mark.parametrize("tool", ["crm_sales_report", "crm_outstanding_report"])
    def test_a_carried_own_account_does_not_narrow_my_report(self, tool, ids) -> None:
        a, b, _z = ids
        out = fetch.entity_ids_transformer(
            _trigger(tool, [a, b], [], self_reference=True, outstanding_carried_customer_ids=[a])
        )
        assert out["customer_ids"] == [a, b], out

    @pytest.mark.parametrize("tool", ["crm_sales_report", "crm_outstanding_report"])
    def test_without_self_reference_the_carry_still_narrows(self, tool, ids) -> None:
        a, b, _z = ids
        out = fetch.entity_ids_transformer(_trigger(tool, [a, b], [], outstanding_carried_customer_ids=[a]))
        assert out["customer_ids"] == [a], out

    def test_the_account_the_message_itself_named_stands(self, ids) -> None:
        """"my sales for a/c ii": the engine hands the named link as the entity; the
        carry is ignored, the named account is the subject."""
        a, b, _z = ids
        out = fetch.entity_ids_transformer(
            _trigger("crm_sales_report", [a, b], [_customer(b)], self_reference=True, outstanding_carried_customer_ids=[a])
        )
        assert out["customer_ids"] == [b], out
