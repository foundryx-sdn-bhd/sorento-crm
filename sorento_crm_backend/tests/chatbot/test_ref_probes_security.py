"""REF-PROBES security round 2 (review on HEAD 567d3ac54): three leaks of the new reference
kinds past the visibility rules.

1. The casual (low_signal) lane hands parser hints straight to the resolver as
   `allowed_entity_types`, so a dealer's purchase_request / purchase_order hint, or a foreign
   customer's sales_order, reached the clarifier prompt.
2. The resolver's per-token fallback widens to `allowed_entity_types=None` when `attachment`
   is among the caller's types, which now reaches the PO / PR / SO / PO ref probes.
3. The foreign-SO drop in the engine runs only when the contact's scope is enforced (linked
   and not staff); a non-staff contact with NO links is unenforced and must still never be
   offered or answered another customer's SO.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from tests.chatbot.test_customer_scope_lane import _link_customers, _other_customer
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_po_ref_ask import _seed_po_so
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, _seed_so

PLACED = "purchase_orders.placed"
FOREIGN = "ZZT FOREIGN TRADING"
PR_NO = "ZZTPR90001"
SO_NO = "SO2610-0991"


def _seed_pr(session_factory, number: str) -> None:
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO purchase_requests (id, request_type, request_number, status, revision_no, source, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'purchase_request', :n, 'draft', 0, 'external', now(), now())"
        ),
        {"n": number},
    )
    db.commit()


def _casual_turn(session_factory, monkeypatch, raw: str, hint: str) -> str:
    qf = _parser_output(
        message_type="casual",
        domain_hint=None,
        intent_hint=None,
        user_goal=f"status of {raw}",
        entities=[{"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}],
    )
    _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=f"status of {raw}",
        msg_id=f"ZZT-refsec-{uuid.uuid4().hex[:10]}",
        attributes=[OUTSTANDING_KEY, PLACED], real_resolver=True,
        extra_completed_lanes=["low_signal"],
    )
    return ""


def test_casual_lane_dealer_prompt_has_no_po_pr_match(session_factory, monkeypatch, _stub_casual_llm):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, "ZZT OWN A")
    _seed_pr(session_factory, PR_NO)
    _casual_turn(session_factory, monkeypatch, PR_NO, "purchase_request")
    assert _stub_casual_llm, "the low_signal clarifier was never called"
    prompt = str(_stub_casual_llm[-1]["user_prompt"])
    assert "purchase_request" not in prompt, prompt
    assert PR_NO not in prompt.replace(f"status of {PR_NO}", ""), prompt


def test_casual_lane_dealer_prompt_has_no_foreign_sales_order(session_factory, monkeypatch, _stub_casual_llm):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, "ZZT OWN A")
    foreign = _other_customer(session_factory, FOREIGN)
    _seed_so(session_factory, SO_NO, customer_id=foreign, debtor_name=FOREIGN, lines=[(1, 0)])
    _casual_turn(session_factory, monkeypatch, SO_NO, "sales_order")
    assert _stub_casual_llm, "the low_signal clarifier was never called"
    prompt = str(_stub_casual_llm[-1]["user_prompt"])
    assert "sales_order" not in prompt, prompt


# --- 2 ----------------------------------------------------------------------- #
def test_attachment_in_caller_types_does_not_widen_fallback_to_new_kinds():
    from app.models.base import set_company_scope
    from app.models.order import SalesOrder
    from app.models.procurement import PurchaseOrder, PurchaseRequestHeader
    from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from tests._pg_fixture import blank_session, unique_code

    register_company_scope_listeners()
    po_number = f"ZZT-PO-{unique_code()[:6].upper()}"
    with blank_session() as db:
        current = db.execute(text("SHOW search_path")).scalar()
        db.execute(text(f"SET LOCAL search_path TO {current}, public"))
        set_company_scope(db, None)
        db.add(PurchaseOrder(id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, po_number=po_number, status="active"))
        db.add(PurchaseRequestHeader(id=str(uuid.uuid4()), request_type="purchase_request", request_number=po_number, status="draft"))
        db.add(SalesOrder(id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, so_number=po_number, status="open", po_ref=po_number, po_ref_key=po_number.replace("-", "")))
        db.flush()
        payload = ResolveReferenceRequest(
            **{"tokens": [po_number], "allowed_entity_types": ["customer_order", "attachment"], "fallback_to_all_types": True}
        )
        out = resolve_reference_post(payload, current_user={}, db=db)
    blob = repr(out).lower()
    for kind in ("purchase_order", "purchase_request", "sales_order", "customer_po_ref"):
        assert f"'{kind}'" not in blob and f'"{kind}"' not in blob, kind

