"""Endpoint tests for the record-action write endpoints the AI assistant drives.

Covers the auth change that lets a *confirmed* chat write execute via the
X-API-Key (act-as) principal instead of 401-ing:

  * complaint close        POST /complaints-management/complaints/{id}/close
  * PR approve/reject      POST /procurement/purchase-requests/{id}/approval-decision
  * PR reject-submitted    POST /procurement/purchase-requests/{id}/reject-submitted
  * order cancel (NEW)     POST /order-management/orders/{id}/cancel

Two layers:
  A. Hermetic HTTP/auth (sqlite + TestClient): the swap is proven by route
     introspection (each endpoint's dependency tree now includes
     ``get_current_user_or_api_key``); delegation + RBAC gating + no-creds 401 are
     proven with a spy that raises a 418 sentinel the instant it is reached, so we
     assert past-auth/past-RBAC without seeding the whole entity + serializer.
  B. Real-DB throwaway (SessionLocal, prefix cleanup - mirrors
     test_complaint_resolve / test_purchase_request_approval): the actual state
     transitions + preconditions, and the new /cancel flip + complaint re-eval.
"""
from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Import app.main first so the full module graph (dependencies, guards, models)
# initializes in the right order - avoids a partially-initialized circular import.
from app.main import app
import app.dependencies as deps
from app.database import SessionLocal, engine
from app.models.order import Order
from app.models.complaints import Complaint
from app.models.procurement import PurchaseRequestHeader
from app.services.error_handler import AppException
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

_UUID = "2654ab89-2449-4910-84bf-f718ccc661d2"

CLOSE_URL = f"/api/v1/complaints-management/complaints/{_UUID}/close"
DECISION_URL = f"/api/v1/procurement/purchase-requests/{_UUID}/approval-decision"
REJECT_SUBMITTED_URL = f"/api/v1/procurement/purchase-requests/{_UUID}/reject-submitted"
CANCEL_URL = f"/api/v1/order-management/orders/{_UUID}/cancel"


# --------------------------------------------------------------------------- #
# A. Hermetic HTTP / auth layer
# --------------------------------------------------------------------------- #
def _dependant_callables(route) -> set:
    """Every dependency callable reachable from a route's dependant tree."""
    seen: set = set()
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        d = stack.pop()
        if d.call is not None:
            seen.add(d.call)
        stack.extend(d.dependencies)
    return seen


def _route(path: str, method: str = "POST"):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/complaints-management/complaints/{complaint_id}/close",
        "/api/v1/procurement/purchase-requests/{request_id}/approval-decision",
        "/api/v1/procurement/purchase-requests/{request_id}/reject-submitted",
        "/api/v1/order-management/orders/{order_id}/cancel",
    ],
)
def test_endpoint_accepts_api_key_principal(path):
    """The swap: each write endpoint now resolves auth via
    get_current_user_or_api_key (X-API-Key + act-as), not the JWT-only
    get_current_user."""
    calls = _dependant_callables(_route(path))
    assert deps.get_current_user_or_api_key in calls, f"{path} not api-key capable"
    assert deps.get_current_user not in calls, f"{path} still JWT-only via get_current_user"


def test_new_cancel_route_is_registered():
    r = _route("/api/v1/order-management/orders/{order_id}/cancel")
    assert "POST" in r.methods


@pytest.fixture
def api(monkeypatch) -> Iterator[tuple[TestClient, set]]:
    """TestClient on an empty sqlite session with a controllable permission gate.

    Service methods are spied (see individual tests) so no real entity/serializer
    is needed - the endpoint is exercised only up to the point it reaches the
    service, which is exactly the auth + RBAC + delegation surface under test.
    """
    with blank_session() as sess:
        allow: set = set()

        def _override_db():
            yield sess

        app.dependency_overrides[deps.get_db] = _override_db
        app.dependency_overrides[deps.get_current_user] = lambda: {"id": "u-admin"}
        app.dependency_overrides[deps.get_current_user_or_api_key] = lambda: {"id": "u-admin"}
        monkeypatch.setattr(
            UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
        )
        client = TestClient(app)
        try:
            yield client, allow
        finally:
            app.dependency_overrides.pop(deps.get_db, None)
            app.dependency_overrides.pop(deps.get_current_user, None)
            app.dependency_overrides.pop(deps.get_current_user_or_api_key, None)


def _spy_raise_418(monkeypatch, target_cls, method_name):
    """Replace a service method with a spy that records kwargs then raises a 418
    sentinel - proving the endpoint reached it past auth + RBAC."""
    captured: dict = {}

    def _spy(self, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        raise AppException(status_code=418, message="reached-service")

    monkeypatch.setattr(target_cls, method_name, _spy)
    return captured


# ---- complaint close ---- #
def test_close_reaches_service_with_note_when_permitted(api, monkeypatch):
    client, allow = api
    allow.add("complaint_management.complaints.close")
    from app.services.complaints_service import ComplaintService

    captured = _spy_raise_418(monkeypatch, ComplaintService, "close_complaint")
    resp = client.post(CLOSE_URL, json={"note": "done"})
    assert resp.status_code == 418
    assert captured["args"][0] == _UUID
    assert captured["kwargs"]["note"] == "done"


def test_close_forbidden_without_permission(api, monkeypatch):
    client, _allow = api  # permission NOT granted
    from app.services.complaints_service import ComplaintService

    captured = _spy_raise_418(monkeypatch, ComplaintService, "close_complaint")
    resp = client.post(CLOSE_URL, json={"note": "x"})
    assert resp.status_code == 403
    assert "args" not in captured  # service never reached


# ---- PR approval-decision (approve + reject) ---- #
@pytest.mark.parametrize("action", ["approved", "rejected"])
def test_approval_decision_reaches_service_when_permitted(api, monkeypatch, action):
    client, allow = api
    allow.add("procurement.purchase_requests.send_for_approval")
    from app.services.procurement_service import PurchaseRequestService

    captured = _spy_raise_418(monkeypatch, PurchaseRequestService, "decide_approval")
    resp = client.post(DECISION_URL, json={"action": action, "comments": "c"})
    assert resp.status_code == 418
    assert captured["args"][0] == _UUID
    assert captured["kwargs"]["action"] == action
    assert captured["kwargs"]["approval_comments"] == "c"


def test_approval_decision_forbidden_without_permission(api, monkeypatch):
    client, _allow = api
    from app.services.procurement_service import PurchaseRequestService

    captured = _spy_raise_418(monkeypatch, PurchaseRequestService, "decide_approval")
    resp = client.post(DECISION_URL, json={"action": "approved"})
    assert resp.status_code == 403
    assert "args" not in captured


# ---- order cancel ---- #
def test_cancel_reaches_update_order_with_is_cancelled(api, monkeypatch):
    client, allow = api
    allow.add("order_management.orders.view")  # cancel answers with the DO, so it is view-gated (#1669)
    from app.services.order_service import OrderService

    captured = _spy_raise_418(monkeypatch, OrderService, "update_order")
    resp = client.post(CANCEL_URL, json={"reason": "changed mind"})
    assert resp.status_code == 418
    # update_order(order_id, OrderUpdate(is_cancelled=True, remarks=reason), user_id)
    assert captured["args"][0] == _UUID
    order_update = captured["args"][1]
    assert order_update.is_cancelled is True
    assert order_update.remarks == "changed mind"


def test_cancel_without_reason_does_not_set_remarks(api, monkeypatch):
    client, allow = api
    allow.add("order_management.orders.view")
    from app.services.order_service import OrderService

    captured = _spy_raise_418(monkeypatch, OrderService, "update_order")
    resp = client.post(CANCEL_URL, json={})
    assert resp.status_code == 418
    order_update = captured["args"][1]
    dumped = order_update.model_dump(exclude_unset=True)
    assert dumped == {"is_cancelled": True}  # remarks left unset → preserved


# ---- no-credentials denial (real auth stack; sqlite get_db) ---- #
@pytest.mark.parametrize(
    "url,body",
    [
        (CLOSE_URL, {"note": "x"}),
        (DECISION_URL, {"action": "approved"}),
        (REJECT_SUBMITTED_URL, {"rejection_reason": "x"}),
        (CANCEL_URL, {"reason": "x"}),
    ],
)
def test_no_credentials_returns_401(monkeypatch, url, body):
    with blank_session() as sess:

        def _override_db():
            yield sess

        app.dependency_overrides[deps.get_db] = _override_db
        client = TestClient(app)
        try:
            resp = client.post(url, json=body)  # no Authorization, no X-API-Key
            assert resp.status_code == 401
        finally:
            app.dependency_overrides.pop(deps.get_db, None)


# --------------------------------------------------------------------------- #
# B. Real-DB throwaway business behaviour
# --------------------------------------------------------------------------- #
@pytest.fixture
def db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(autouse=True)
def _cleanup_throwaway():
    yield
    from sqlalchemy import text

    with engine.connect() as conn:
        for stmt in (
            "DELETE FROM complaints WHERE complaint_number LIKE 'ZZRA-%'",
            "DELETE FROM purchase_requests WHERE request_number LIKE 'ZZRA-%'",
            "DELETE FROM orders WHERE order_number LIKE 'ZZRA-%'",
        ):
            try:
                conn.execute(text(stmt))
            except Exception:
                pass
        conn.commit()


def _seed_complaint(db: Session, status: str) -> Complaint:
    c = Complaint(
        id=str(uuid.uuid4()),
        complaint_number=f"ZZRA-{uuid.uuid4().hex[:6]}",
        status=status,
        customer_name="ACME",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_close_complaint_service_approved_to_closed(db: Session):
    from app.services.complaints_service import ComplaintService

    c = _seed_complaint(db, "approved")
    out = ComplaintService(db).close_complaint(c.id, note="cannot resolve")
    assert out.status == "closed"
    assert out.resolved_at is not None


def test_close_complaint_service_rejects_non_approved(db: Session):
    from app.services.complaints_service import ComplaintService

    c = _seed_complaint(db, "submitted")
    with pytest.raises(AppException) as ei:
        ComplaintService(db).close_complaint(c.id)
    assert ei.value.status_code == 400  # "must be approved before it can be marked closed"
    db.rollback()


def _seed_pr(db: Session, approval_status, status="submitted") -> PurchaseRequestHeader:
    h = PurchaseRequestHeader(
        id=str(uuid.uuid4()),
        request_number=f"ZZRA-{uuid.uuid4().hex[:6]}",
        request_type="purchase_request",
        status=status,
        approval_status=approval_status,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@pytest.fixture
def _silence_pr_side_effects(monkeypatch):
    from app.services import procurement_service as pmod

    for name in (
        "_notify_requester_on_approved",
        "_notify_contact_on_approval_approved",
        "_notify_contact_on_approval_rejected",
        "_dispatch_approval_automation",
    ):
        monkeypatch.setattr(pmod.PurchaseRequestService, name, lambda self, *a, **k: None)
    monkeypatch.setattr(pmod, "emit_form_event", lambda *a, **k: None, raising=False)


@pytest.mark.parametrize("action", ["approved", "rejected"])
def test_decide_approval_service_pending_advances(db, _silence_pr_side_effects, action):
    from app.services.procurement_service import PurchaseRequestService

    h = _seed_pr(db, approval_status="pending")
    # A rejection requires a reason; supply one for both branches.
    out = PurchaseRequestService(db).decide_approval(
        h.id, action=action, approved_by="a@b.c", approval_comments="reason"
    )
    assert out.approval_status == action
    assert out.status == action


def test_decide_approval_service_rejects_non_pending(db, _silence_pr_side_effects):
    from app.services.procurement_service import PurchaseRequestService
    from app.services.error_handler import AppException as _Ae

    h = _seed_pr(db, approval_status="approved", status="approved")
    with pytest.raises(_Ae) as ei:
        PurchaseRequestService(db).decide_approval(h.id, action="approved")
    assert ei.value.status_code == 409
    db.rollback()


def test_order_cancel_service_flips_and_reruns_complaint_reeval(db, monkeypatch):
    """The /cancel endpoint delegates to update_order; assert the real service
    flips is_cancelled AND runs the complaint (un)link re-evaluation on cancel."""
    from app.services.order_service import OrderService
    from app.schemas.order import OrderUpdate
    from app.config import settings
    import app.services.complaint_fulfilment_service as cfs_mod

    ran: dict = {}

    def _spy_apply(self, payloads):
        ran["payloads"] = payloads

    monkeypatch.setattr(cfs_mod.ComplaintFulfilmentService, "apply_for_orders", _spy_apply)

    # orders.updated_by is a UUID column - use a real existing user id.
    real_user_id = settings.external_api_key_act_as_user_id
    o = Order(id=str(uuid.uuid4()), order_number=f"ZZRA-{uuid.uuid4().hex[:6]}", is_cancelled=False)
    db.add(o)
    db.commit()

    out = OrderService(db).update_order(o.id, OrderUpdate(is_cancelled=True, remarks="void"), real_user_id)
    assert out.is_cancelled is True
    assert out.remarks == "void"
    assert "payloads" in ran  # complaint re-evaluation ran because is_cancelled changed


def test_order_cancel_service_missing_order_404(db: Session):
    from app.services.order_service import OrderService
    from app.schemas.order import OrderUpdate

    with pytest.raises(AppException) as ei:
        OrderService(db).update_order(str(uuid.uuid4()), OrderUpdate(is_cancelled=True), "u-admin")
    assert ei.value.status_code == 404
    db.rollback()
