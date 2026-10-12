"""REF-PROBES: who counts as staff for our PO / PR, and the dealer hand-test steps
(laneboard/scripts/1569.md steps 13-15, which dev cannot run: no dealer there holds
`sales_orders.outstanding` with one linked customer).

Crew security question (12 Oct 2026): several escalation-offer sites read
`is_staff = not offers_escalation(profile)`, which is also true for a dealer whose
escalation is barred. The PO / PR gates must not read that: staff for them is
`is_staff_profile` (the operator-set `chatbot_profile.tier == "office"`), escalation
never enters it.

Same harness as `test_ref_probes_answer.py`: one real `engine.run_turn` with the REAL
resolver over rows seeded on the blank schema; parser, access and MCP doubled.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from tests.chatbot.test_customer_scope_lane import _link_customers, _other_customer
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import CONTACT_ID, _run_turn, _seed_contact
from tests.chatbot.test_ref_probes_answer import DO_LIST, OWN, FOREIGN
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, _seed_so

PLACED = "purchase_orders.placed"
PO_NO = "ZZTPO-77001"
PR_NO = "ZZTPR-77001"
SUPPLIER = "ZZT SECRET SUPPLIER BHD"
PR_CUSTOMER = "ZZT SECRET PROJECT SDN BHD"


def _set_contact(session_factory, *, tier: str, escalation_allowed: bool) -> None:
    db = session_factory()
    db.execute(
        text(
            "UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb), escalation_allowed = :e "
            "WHERE respond_io_id = :c"
        ),
        {"p": f'{{"tier": "{tier}"}}', "e": escalation_allowed, "c": str(CONTACT_ID)},
    )
    db.commit()


def _seed_po_pr(session_factory) -> None:
    from app.models.procurement import PurchaseOrder, PurchaseRequestHeader, Supplier
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = session_factory()
    sup = Supplier(
        id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID,
        supplier_code=f"ZZTS{uuid.uuid4().hex[:8]}", supplier_name=SUPPLIER,
    )
    db.add(sup)
    db.flush()
    db.add(
        PurchaseOrder(
            id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, po_number=PO_NO,
            supplier_id=sup.id, issue_date=date(2026, 9, 5), status="active",
        )
    )
    db.add(
        PurchaseRequestHeader(
            id=str(uuid.uuid4()), request_type="purchase_request", request_number=PR_NO,
            customer_name=PR_CUSTOMER, status="processed_by_cs", approval_status="approved",
            request_date=date(2026, 9, 6),
        )
    )
    db.commit()


def _turn(session_factory, monkeypatch, raw: str, hint: str, *, attributes, po_refs=None) -> str:
    qf = _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[{"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}],
        po_refs=po_refs,
    )
    result, _ = _run_turn(
        session_factory,
        monkeypatch,
        qf=qf,
        text_body=f"status of {raw}",
        msg_id=f"ZZT-refdealer-{uuid.uuid4().hex[:10]}",
        attributes=list(attributes),
        real_resolver=True,
        mcp_response=DO_LIST,
    )
    return (result.reply or {}).get("text") or ""


# --------------------------------------------------------------------------- #
# Security: escalation-barred is not staff
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "hint", "secrets"),
    [
        (PO_NO, "purchase_order", (SUPPLIER, f"Purchase order {PO_NO}")),
        (PO_NO, "order", (SUPPLIER, f"Purchase order {PO_NO}")),
        (PR_NO, "purchase_request", (PR_CUSTOMER, f"Purchase request {PR_NO}")),
        (PR_NO, "order", (PR_CUSTOMER, f"Purchase request {PR_NO}")),
    ],
)
def test_escalation_barred_dealer_holding_placed_never_sees_po_or_pr(session_factory, monkeypatch, raw, hint, secrets):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN)
    _set_contact(session_factory, tier="dealer", escalation_allowed=False)
    _seed_po_pr(session_factory)
    reply = _turn(session_factory, monkeypatch, raw, hint, attributes=(OUTSTANDING_KEY, PLACED))
    for secret in secrets:
        assert secret not in reply, reply


def test_escalation_barred_contact_with_no_tier_never_sees_po(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET escalation_allowed = false WHERE respond_io_id = :c"),
        {"c": str(CONTACT_ID)},
    )
    db.commit()
    _seed_po_pr(session_factory)
    reply = _turn(session_factory, monkeypatch, PO_NO, "purchase_order", attributes=(OUTSTANDING_KEY, PLACED))
    assert SUPPLIER not in reply and f"Purchase order {PO_NO}" not in reply, reply


def test_escalation_barred_office_contact_still_gets_the_po_card(session_factory, monkeypatch):
    """Positive control: the gate reads the tier, so barring escalation on staff changes nothing."""
    _seed_contact(session_factory, variables={})
    _set_contact(session_factory, tier="office", escalation_allowed=False)
    _seed_po_pr(session_factory)
    reply = _turn(session_factory, monkeypatch, PO_NO, "purchase_order", attributes=(OUTSTANDING_KEY, PLACED))
    assert f"*Purchase order {PO_NO}* - {SUPPLIER}" in reply, reply


# --------------------------------------------------------------------------- #
# Hand-test steps 13-15 (dealer with the SO grant and one linked customer)
# --------------------------------------------------------------------------- #

OWN_SO = "SO2610-0127"
FOREIGN_SO = "SO2610-0991"
MISSING_SO = "SO2610-0992"


def test_step13_dealer_own_dashed_so_is_answered(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    own = _link_customers(session_factory, OWN)[0]
    _set_contact(session_factory, tier="dealer", escalation_allowed=True)
    _seed_so(session_factory, OWN_SO, customer_id=own, debtor_name=OWN, lines=[(4, 0)])
    reply = _turn(session_factory, monkeypatch, OWN_SO, "order", attributes=(OUTSTANDING_KEY,))
    assert OWN_SO in reply, reply
    assert "could not find" not in reply.lower(), reply


def test_step14_dealer_foreign_dashed_so_gets_the_scope_refusal(session_factory, monkeypatch):
    """Owner ruling (PLAN-so-number-ask.md row 2): an SO outside the account gets the scope
    refusal, not the miss line. The dash fix must not change that, and the refusal names only
    the dealer's own account, never the other customer or its card."""
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN)
    _set_contact(session_factory, tier="dealer", escalation_allowed=True)
    foreign = _other_customer(session_factory, FOREIGN)
    _seed_so(session_factory, FOREIGN_SO, customer_id=foreign, debtor_name=FOREIGN, lines=[(4, 0)])
    foreign_reply = _turn(session_factory, monkeypatch, FOREIGN_SO, "order", attributes=(OUTSTANDING_KEY,))
    missing_reply = _turn(session_factory, monkeypatch, MISSING_SO, "order", attributes=(OUTSTANDING_KEY,))
    assert foreign_reply == f"Sorry, that isn't under your account. I can only check on {OWN}.", foreign_reply
    assert missing_reply == f"I could not find {MISSING_SO}.", missing_reply  # no did-you-mean onto the foreign SO


def test_step15_dealer_named_po_never_shows_our_purchase_order(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN)
    _set_contact(session_factory, tier="dealer", escalation_allowed=True)
    _seed_po_pr(session_factory)
    reply = _turn(
        session_factory, monkeypatch, PO_NO, "purchase_order", attributes=(OUTSTANDING_KEY, PLACED), po_refs=[PO_NO]
    )
    assert SUPPLIER not in reply, reply
    assert f"Purchase order {PO_NO}" not in reply, reply
