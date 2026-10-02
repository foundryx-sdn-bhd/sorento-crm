"""PARSER-PER-AUDIENCE, AC-PA-13 / gap G1 (#1429): a contact with no office access type and
NO linked customer is unscoped today (`contact_customer_scope.py`: `enforced` needs a link),
so it can read any customer's orders and the all-customer outstanding report. The owner
approved failing closed; it ships only after the owner's data step (linking the real
dealers), so this file lives on its own and its commit can be split off.

Expected: refused with the "no account linked" line and no order or report tool call.
The forced parser output, the `_hanlim_services` book and the harness are those of
`test_customer_scope_lane.py`. The resolver here answers "hanlim" with ONE customer so a
build that is not fail-closed would go on to call the tool (a picker would otherwise hide
the leak).

Red today for the three unlinked cases. The two positive controls (an office-type contact
with no links; a linked non-office contact) are green today and must stay green. No em or
en dashes.
"""
from __future__ import annotations

import pytest

from tests.chatbot.test_customer_scope_lane import (
    ORDERS,
    OUTSTANDING_KEY,
    OWN_A,
    REPORT,
    _ask,
    _calls,
    _ent,
    _give_access_type,
    _link_customers,
    _turn,
)
from tests.chatbot.test_outstanding_lane import HANLIM_UUID_1, REPORT_HIT, _seed_contact

UNLINKED_REFUSAL = "Sorry, I can't find an account linked to you yet."
HANLIM_ONE = {"hanlim": {"uuid": HANLIM_UUID_1, "entity_type": "customer", "canonical_code": "300-H070"}}


class TestUnlinkedNonOfficeContactFailsClosed:
    def test_a_named_customers_orders_are_refused(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch, _ask([_ent("hanlim")], order_status="all"),
            "orders for hanlim", attributes=(OUTSTANDING_KEY,), matches=HANLIM_ONE, mcp_response=REPORT_HIT,
        )
        assert UNLINKED_REFUSAL in reply, reply
        assert captured == [], captured
        assert _calls(captured, ORDERS) == []

    def test_a_named_customers_outstanding_is_refused(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch, _ask([_ent("hanlim")]), "outstanding for hanlim",
            attributes=(OUTSTANDING_KEY,), matches=HANLIM_ONE, mcp_response=REPORT_HIT,
        )
        assert UNLINKED_REFUSAL in reply, reply
        assert captured == [], captured

    def test_an_all_customer_outstanding_ask_is_refused(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch, _ask([]), "outstanding for all customers",
            attributes=(OUTSTANDING_KEY,), mcp_response=REPORT_HIT,
        )
        assert UNLINKED_REFUSAL in reply, reply
        assert _calls(captured, REPORT) == [], captured
        assert captured == [], captured


class TestPositiveControls:
    def test_an_office_contact_with_no_links_is_answered_normally(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _give_access_type(session_factory, "Sorento Office")
        reply, captured = _turn(
            session_factory, monkeypatch, _ask([_ent("hanlim")]), "outstanding for hanlim",
            attributes=(OUTSTANDING_KEY,), matches=HANLIM_ONE, mcp_response=REPORT_HIT,
        )
        assert UNLINKED_REFUSAL not in reply, reply
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [HANLIM_UUID_1], args

    def test_a_linked_non_office_contact_keeps_todays_scope(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        (own_id,) = _link_customers(session_factory, OWN_A)
        reply, captured = _turn(
            session_factory, monkeypatch, _ask([_ent("own a")]), "outstanding for own a",
            attributes=(OUTSTANDING_KEY,),
            matches={"own a": {"uuid": HANLIM_UUID_1, "entity_type": "customer", "canonical_code": "300-H070"}},
            mcp_response=REPORT_HIT,
        )
        assert UNLINKED_REFUSAL not in reply, reply
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [own_id], args
