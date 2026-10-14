"""Per-salesman → CS PIC pin-point routing + procurement CS handoff finalize.

Covers (PLAN-procurement-cs-handoff-and-pinpoint-routing.md):
 - FormSLAOrchestrator._resolve_pinned_assignee branches (D1 - D6): not-pinnable
    use_case, no contact, no pin, eligible pin (team membership no longer matters,
    CS-ROUTING-TARGETS), ineligible pin.
 - CsRoutingService.upsert validation (D4/D5): use_case + the eligible-PIC rule.
 - PurchaseRequestService finalize (D9): status guard, idempotency, happy path
    emits 'resolved' for the right source_entity_type.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.access import RespondContactCsRouting
from app.models.user import User
from app.services.cs_routing_service import CsRoutingService
from app.services.form_sla_service import FormSLAOrchestrator
from app.services.procurement_service import PurchaseRequestService


# ---- resolver -------------------------------------------------------------
# Real-DB coverage of the same branches (eligibility rule, CS-stage-only pin,
# round-robin fallback) lives in tests/test_cs_routing_targets.py.

PIC = "82dce68d-596c-5265-9263-07b67db11d44"
COMPANY = "00000000-0000-0000-0000-000000000001"


def _orch(query_map):
    """FormSLAOrchestrator whose db.query(Model) returns query_map[Model] from
    .first() and [query_map[Model]] (or []) from .all()."""
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        q.filter.return_value = q
        obj = query_map.get(model)
        q.first.return_value = obj
        q.all.return_value = [obj] if obj is not None else []
        return q

    db.query.side_effect = _query
    return FormSLAOrchestrator(db)


def _wildcard_pin():
    return SimpleNamespace(cs_pic_user_id=PIC, match_conditions=[], priority=0, created_at=None)


def _active_user():
    return SimpleNamespace(id=PIC, email="a@b.com", name="Aishah", status="ACTIVE", respond_user_id="r1")


def test_resolver_ignores_non_pinnable_use_case():
    # complaint never reads the pin table -> round-robin.
    orch = _orch({})
    assert orch._resolve_pinned_assignee("complaint", "c1", COMPANY) is None


def test_resolver_no_contact_returns_none():
    orch = _orch({})
    assert orch._resolve_pinned_assignee("purchase_request", None, COMPANY) is None


def test_resolver_no_pin_returns_none():
    orch = _orch({RespondContactCsRouting: None})
    assert orch._resolve_pinned_assignee("purchase_request", "c1", COMPANY) is None


def test_resolver_eligible_pin_outside_the_team_is_honoured():
    # CS-ROUTING-TARGETS: team membership no longer decides; the eligibility rule does.
    orch = _orch({RespondContactCsRouting: _wildcard_pin(), User: _active_user()})
    with patch("app.services.cs_routing_service.is_eligible_pic", return_value=True) as eligible:
        assert orch._resolve_pinned_assignee("sponsorship_form", "c1", COMPANY) == {
            "id": PIC,
            "email": "a@b.com",
            "name": "Aishah",
            "respond_user_id": "r1",
        }
    assert eligible.call_args.args[1] == PIC
    assert eligible.call_args.kwargs["company_id"] == COMPANY


def test_resolver_ineligible_pin_falls_back():
    orch = _orch({RespondContactCsRouting: _wildcard_pin(), User: _active_user()})
    with patch("app.services.cs_routing_service.is_eligible_pic", return_value=False):
        assert orch._resolve_pinned_assignee("purchase_request", "c1", COMPANY) is None


# ---- CsRoutingService validation -----------------------------------------

def _routing_svc():
    svc = CsRoutingService(MagicMock())
    svc._require_contact = MagicMock()
    return svc


def test_upsert_rejects_invalid_use_case():
    svc = _routing_svc()
    with pytest.raises(Exception):
        svc.upsert("c1", "complaint", PIC)


def test_upsert_rejects_ineligible_user():
    svc = _routing_svc()
    with patch("app.services.cs_routing_service.is_eligible_pic", return_value=False):
        with pytest.raises(Exception):
            svc.upsert("c1", "purchase_request", PIC)


def test_upsert_accepts_eligible_user_outside_the_team():
    svc = _routing_svc()
    with patch("app.services.cs_routing_service.is_eligible_pic", return_value=True):
        row = svc.upsert("c1", "purchase_request", PIC)
    assert row.cs_pic_user_id == PIC


# ---- procurement CS finalize ---------------------------------------------

def _pr_service(header):
    svc = PurchaseRequestService(MagicMock())
    svc.get_request = MagicMock(return_value=header)
    return svc


def test_finalize_rejects_non_approved():
    header = SimpleNamespace(id="r1", status="submitted", request_type="purchase_request")
    with pytest.raises(Exception):
        _pr_service(header).mark_processed_by_cs("r1")


def test_finalize_is_idempotent_when_already_in_target():
    header = SimpleNamespace(id="r1", status="processed_by_cs", request_type="purchase_request")
    svc = _pr_service(header)
    assert svc.mark_processed_by_cs("r1") is header


def test_close_sets_status_and_emits_resolved_for_sponsorship():
    header = SimpleNamespace(
        id="r1",
        status="approved",
        request_type="sponsorship_form",
        request_number="SF-1",
        contact_id="c1",
    )
    svc = _pr_service(header)
    svc._build_request_view_url = MagicMock(return_value="http://x/view")
    svc._send_purchase_request_contact_message = MagicMock()
    with patch("app.services.form_sla_service.emit_form_event") as emit:
        out = svc.close_request("r1", note="done")
    assert out.status == "closed"
    svc._send_purchase_request_contact_message.assert_called_once()
    emit.assert_called_once()
    args = emit.call_args.args
    assert args[1] == "sponsorship_form"  # source_entity_type
    assert args[3] == "resolved"  # event closes the CS stage
