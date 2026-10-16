"""QUOTE-APPROVAL-UX (15 Oct 2026): who is told when a quotation moves along the approval
ladder. UAC B1-B5, D5 and the fix-round-2 items. Driven over HTTP, because the design is
"the route commits the status, THEN notifies (best effort)".

The notification backend is replaced at its one door,
``NotificationService.create_with_channel_preferences``, with a recorder, so the test sees who
would be told, over which channels and with what data, without Redis, SMTP or the real
``notifications`` table. Postgres only, via the routes suite's ``api`` fixture (blank schema);
every user is seeded here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.company import UserCompany
from app.models.user import User, UserStatus

from .test_project_quotation_approval import APPROVE
from .test_project_quotation_approval_routes import (  # noqa: F401  (api is a fixture)
    ABOVE_FLOOR,
    BASE,
    BELOW_FLOOR,
    _grant,
    _quotation,
    _status_id,
    api,
)

MARKER = "zzt-qapprnotify"

REQUESTED = "project_quotation_approval_requested"
APPROVED = "project_quotation_approval_approved"
REJECTED = "project_quotation_approval_rejected"
WITHDRAWN = "project_quotation_approval_withdrawn"
PENDING = "/api/v1/pending-actions"


@pytest.fixture()
def sent(monkeypatch):
    """Every notification the code under test tried to send, in order."""
    from app.services.notification_service import NotificationService

    calls: list[dict] = []

    def fake(self, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", fake)
    return calls


def _user(db, name: str, **extra) -> str:
    user_id = str(uuid.uuid4())
    db.add(
        User(
            id=user_id,
            email=f"{user_id}@zzt.test",
            name=name,
            status=extra.pop("status", UserStatus.ACTIVE.value),
            **extra,
        )
    )
    db.flush()
    return user_id


def _approver(db, name: str, company_id: str, *, with_company: bool = True, **extra) -> str:
    user_id = _user(db, name, **extra)
    _grant(db, user_id, APPROVE)
    if with_company:
        db.add(UserCompany(id=str(uuid.uuid4()), user_id=user_id, company_id=company_id))
        db.flush()
    return user_id


def _act_as(user_id: str) -> None:
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "superadmin"}
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)


def _root(project, document: dict) -> str:
    return f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}"


def _send(client, db, root: str):
    return client.post(
        f"{root}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )


def _of_type(calls: list[dict], notif_type: str) -> list[dict]:
    return [c for c in calls if c.get("type") == notif_type]


def _check_data(call: dict, project, document: dict) -> None:
    data = call["data"]
    assert data["project_id"] == str(project.id)
    assert data["document_id"] == document["id"]
    assert data["document_no"] == document["document_no"]
    # LINK: the bell deep-links to the quotation page.
    assert data["link"] == f"/project-sales/{project.id}/quotation-documents/{document['id']}"


# ------------------------------------------------------------------ B1 + LINK + WHATSAPP


def test_b1_sending_for_approval_tells_every_active_non_integration_approver(api, sent):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    seller = _user(db, f"{MARKER} Seller")
    first = _approver(db, f"{MARKER} A", company_id)
    second = _approver(db, f"{MARKER} B", company_id)
    _approver(db, f"{MARKER} Bot", company_id, is_integration=True)
    _approver(db, f"{MARKER} Gone", company_id, status="INACTIVE")
    _approver(db, f"{MARKER} Trashed", company_id, is_trashed=True)
    _user(db, f"{MARKER} No grant")
    db.commit()
    _act_as(seller)

    response = _send(client, db, _root(project, document))
    assert response.status_code == 200, response.text

    calls = _of_type(sent, REQUESTED)
    assert sorted(c["user_id"] for c in calls) == sorted([first, second])
    for call in calls:
        _check_data(call, project, document)
        assert call["send_in_app"] is True
        assert call["send_email"] is True
        assert call["send_whatsapp"] is True


def test_whatsapp_requested_and_withdrawn_carry_the_template_payload(api, sent):
    from app.models.respond_template import TEMPLATE_DEFAULT_USE_CASES

    assert "quotation_approval" in TEMPLATE_DEFAULT_USE_CASES

    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    seller = _user(db, f"{MARKER} Seller")
    _approver(db, f"{MARKER} A", company_id)
    db.commit()
    _act_as(seller)
    root = _root(project, document)

    assert _send(client, db, root).status_code == 200
    withdrawn = client.post(f"{root}/approval-revise", json={"reason": "Spec changed"})
    assert withdrawn.status_code == 200, withdrawn.text

    for notif_type in (REQUESTED, WITHDRAWN):
        calls = _of_type(sent, notif_type)
        assert len(calls) == 1, notif_type
        data = calls[0]["data"]
        assert data["whatsapp_use_case"] == "quotation_approval"
        assert document["document_no"] in data["whatsapp_text"]
        assert f"{MARKER} Seller" in data["whatsapp_text"]
        assert data["whatsapp_context_vars"]["entity_number"] == document["document_no"]


# ------------------------------------------------------------------ B2 / B3 + LINK


def test_b2_approving_tells_the_salesperson_who_sent_it_and_not_the_approver(api, sent):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    sender = _user(db, f"{MARKER} Sender")
    manager = _approver(db, f"{MARKER} Manager", company_id)
    db.commit()
    root = _root(project, document)
    _act_as(sender)
    assert _send(client, db, root).status_code == 200
    del sent[:]

    _act_as(manager)
    assert client.post(f"{root}/approve").status_code == 200

    calls = _of_type(sent, APPROVED)
    assert [c["user_id"] for c in calls] == [sender]
    _check_data(calls[0], project, document)
    assert calls[0]["send_in_app"] is True
    assert calls[0]["send_email"] is True
    assert calls[0]["send_web_push"] is True
    assert manager not in [c["user_id"] for c in sent]


def test_b3_rejecting_tells_the_same_recipient_and_the_body_carries_the_reason(api, sent):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    sender = _user(db, f"{MARKER} Sender")
    manager = _approver(db, f"{MARKER} Manager", company_id)
    db.commit()
    root = _root(project, document)
    _act_as(sender)
    assert _send(client, db, root).status_code == 200
    del sent[:]

    _act_as(manager)
    rejected = client.post(f"{root}/reject", json={"reason": "Bring the WC suite back to RM 240."})
    assert rejected.status_code == 200, rejected.text

    calls = _of_type(sent, REJECTED)
    assert [c["user_id"] for c in calls] == [sender]
    _check_data(calls[0], project, document)
    assert "Bring the WC suite back to RM 240." in calls[0]["body"]
    assert calls[0]["send_web_push"] is True
    assert calls[0]["send_email"] is True


# ------------------------------------------------------------------ B4


def test_b4_a_resubmit_from_rejected_tells_the_approvers_again_once_per_press(api, sent):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    sender = _user(db, f"{MARKER} Sender")
    manager = _approver(db, f"{MARKER} Manager", company_id)
    db.commit()
    root = _root(project, document)

    _act_as(sender)
    assert _send(client, db, root).status_code == 200
    _act_as(manager)
    assert client.post(f"{root}/reject", json={"reason": "Too low."}).status_code == 200
    _act_as(sender)
    assert _send(client, db, root).status_code == 200  # C1: straight from Rejected

    calls = [c for c in _of_type(sent, REQUESTED) if c["user_id"] == manager]
    assert len(calls) == 2, "one notification per press, not one per document"
    assert calls[0]["dedup_key"] != calls[1]["dedup_key"]


# ------------------------------------------------------------------ D5 + withdrawn body


def test_d5_revise_from_pending_tells_the_approvers_and_the_body_carries_the_reason(api, sent):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    seller = _user(db, f"{MARKER} Seller")
    first = _approver(db, f"{MARKER} A", company_id)
    _approver(db, f"{MARKER} Bot", company_id, is_integration=True)
    db.commit()
    root = _root(project, document)
    _act_as(seller)
    assert _send(client, db, root).status_code == 200
    del sent[:]

    revised = client.post(f"{root}/approval-revise", json={"reason": "Customer changed the spec"})
    assert revised.status_code == 200, revised.text

    calls = _of_type(sent, WITHDRAWN)
    assert [c["user_id"] for c in calls] == [first]
    _check_data(calls[0], project, document)
    assert "Customer changed the spec" in calls[0]["body"]


def test_d5_revise_from_approved_tells_nobody(api, sent):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    seller = _user(db, f"{MARKER} Seller")
    manager = _approver(db, f"{MARKER} Manager", company_id)
    db.commit()
    root = _root(project, document)
    _act_as(seller)
    assert _send(client, db, root).status_code == 200
    _act_as(manager)
    assert client.post(f"{root}/approve").status_code == 200
    del sent[:]

    _act_as(seller)
    assert client.post(f"{root}/approval-revise", json={"reason": "Changed"}).status_code == 200

    assert sent == []


# ------------------------------------------------------------------ B5 / B5b


def test_b5_a_notification_backend_that_raises_never_fails_the_action(api, monkeypatch):
    """Route level: send, approve and reject all answer 200 and the rung still moves."""
    from app.services.notification_service import NotificationService

    client, db, company_id, _, project, product = api
    seller = _user(db, f"{MARKER} Seller")
    manager = _approver(db, f"{MARKER} Manager", company_id)
    db.commit()
    attempts: list[str] = []

    def boom(self, **kwargs):
        attempts.append(kwargs.get("type"))
        raise RuntimeError("notification backend is down")

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", boom)

    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = _root(project, document)
    _act_as(seller)
    sent_for_approval = _send(client, db, root)
    assert sent_for_approval.status_code == 200, sent_for_approval.text
    assert sent_for_approval.json()["approval_status_key"] == "pending_approval"
    assert attempts, "the requested notification was never even attempted"

    _act_as(manager)
    approved = client.post(f"{root}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status_key"] == "approved"
    assert APPROVED in attempts

    _act_as(seller)
    second = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root2 = _root(project, second)
    _send(client, db, root2)
    _act_as(manager)
    rejected = client.post(f"{root2}/reject", json={"reason": "Too low."})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["approval_status_key"] == "rejected"
    assert REJECTED in attempts


def test_b5b_a_notification_that_poisons_the_session_does_not_roll_the_status_back(
    api, monkeypatch
):
    """The fake runs a statement that fails inside the SAME session, then raises. The status
    was committed first, so a fresh read still shows it moved (send and approve)."""
    from app.services.notification_service import NotificationService

    client, db, company_id, _, project, product = api
    seller = _user(db, f"{MARKER} Seller")
    manager = _approver(db, f"{MARKER} Manager", company_id)
    db.commit()
    attempts: list[str] = []

    def poisoned(self, **kwargs):
        attempts.append(kwargs.get("type"))
        db.execute(text("select * from zz_no_such_table"))
        raise RuntimeError("unreachable")

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", poisoned)

    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = _root(project, document)
    _act_as(seller)
    assert _send(client, db, root).status_code == 200
    assert attempts, "nothing was attempted, so the test proves nothing"
    assert client.get(root).json()["approval_status_key"] == "pending_approval"

    _act_as(manager)
    assert client.post(f"{root}/approve").status_code == 200
    assert client.get(root).json()["approval_status_key"] == "approved"


# ------------------------------------------------------------------ COMPANY SCOPE


def test_company_scope_an_approver_without_a_company_grant_is_not_told(api, sent):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    seller = _user(db, f"{MARKER} Seller")
    granted = _approver(db, f"{MARKER} Granted", company_id)
    _approver(db, f"{MARKER} Stranger", company_id, with_company=False)
    db.commit()
    root = _root(project, document)
    _act_as(seller)

    assert _send(client, db, root).status_code == 200
    assert [c["user_id"] for c in _of_type(sent, REQUESTED)] == [granted]

    assert client.post(f"{root}/approval-revise", json={"reason": "Changed"}).status_code == 200
    assert [c["user_id"] for c in _of_type(sent, WITHDRAWN)] == [granted]


# ------------------------------------------------------------------ ACTOR EXCLUDED


def test_actor_an_approver_who_sends_their_own_quotation_is_not_told(api, sent):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    me = _approver(db, f"{MARKER} Me", company_id)
    other = _approver(db, f"{MARKER} Other", company_id)
    db.commit()
    _act_as(me)

    assert _send(client, db, _root(project, document)).status_code == 200

    assert [c["user_id"] for c in _of_type(sent, REQUESTED)] == [other]


def test_actor_an_approver_who_decides_their_own_quotation_gets_no_decided_notification(
    api, sent
):
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    me = _approver(db, f"{MARKER} Me", company_id)
    db.commit()
    root = _root(project, document)
    _act_as(me)
    assert _send(client, db, root).status_code == 200
    del sent[:]

    assert client.post(f"{root}/approve").status_code == 200

    assert _of_type(sent, APPROVED) == []


# ------------------------------------------------------------------ REASON LENGTH


@pytest.mark.parametrize("length,ok", [(1000, True), (1001, False)])
def test_reason_length_revise_and_reject_cap_at_1000(api, sent, length, ok):
    client, db, company_id, _, project, product = api
    manager = _approver(db, f"{MARKER} Manager", company_id)
    db.commit()
    reason = "x" * length

    revisable = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = _root(project, revisable)
    assert _send(client, db, root).status_code == 200
    revised = client.post(f"{root}/approval-revise", json={"reason": reason})
    assert revised.status_code == (200 if ok else 422), revised.text

    rejectable = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root2 = _root(project, rejectable)
    assert _send(client, db, root2).status_code == 200
    rejected = client.post(f"{root2}/reject", json={"reason": reason})
    assert rejected.status_code == (200 if ok else 422), rejected.text


# ------------------------------------------------------------------ pending-action guards


def test_a_non_uuid_entity_id_is_a_4xx_not_a_500(api):
    client, *_ = api
    response = client.post(
        PENDING,
        json={
            "action_key": "quotation_document.delete",
            "entity_type": "quotation_document",
            "entity_id": "not-a-uuid",
            "payload": {},
        },
    )
    assert 400 <= response.status_code < 500, response.text


def test_commit_time_recheck_a_delete_parked_on_a_draft_fails_once_it_is_issued(api):
    from app.models.projects import ProjectQuotationDocument
    from app.models.sla import SlaFormAction

    client, db, _, _, project, product = api
    document = _quotation(client, project.id, product.id, ABOVE_FLOOR)
    parked = client.post(
        PENDING,
        json={
            "action_key": "quotation_document.delete",
            "entity_type": "quotation_document",
            "entity_id": document["id"],
            "payload": {},
        },
    )
    assert parked.status_code == 202, parked.text

    issued = client.post(f"{_root(project, document)}/issue")
    assert issued.status_code == 201, issued.text

    db.query(SlaFormAction).filter(SlaFormAction.id == parked.json()["id"]).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False
    )
    db.commit()
    current = client.get(
        f"{PENDING}/current",
        params={"entity_type": "quotation_document", "entity_id": document["id"]},
    )
    assert current.status_code == 200, current.text
    assert (current.json().get("last_outcome") or {}).get("status") != "committed", current.text
    db.expire_all()
    assert (
        db.query(ProjectQuotationDocument)
        .filter(ProjectQuotationDocument.id == document["id"])
        .first()
        is not None
    ), "an issued quotation must survive a delete parked while it was a draft"


# ------------------------------------------------------------------ REQUESTER


def test_requester_is_the_latest_sender_of_this_document_despite_newer_rows_of_others(
    api, sent
):
    from app.models.activities import ActivityEvent

    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    other_document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    sender = _user(db, f"{MARKER} Sender")
    elsewhere = _user(db, f"{MARKER} Elsewhere")
    manager = _approver(db, f"{MARKER} Manager", company_id)
    root = _root(project, document)
    db.commit()
    _act_as(sender)
    assert _send(client, db, root).status_code == 200

    future = datetime(2099, 1, 1)
    for i in range(60):
        db.add(
            ActivityEvent(
                id=str(uuid.uuid4()),
                entity_type="project",
                entity_id=str(project.id),
                kind="system",
                system_template="quotation_approval_requested",
                system_payload={"document_id": other_document["id"], "document_no": "x"},
                actor_id=elsewhere,
                created_at=future + timedelta(seconds=i),
            )
        )
    db.commit()
    del sent[:]

    _act_as(manager)
    assert client.post(f"{root}/approve").status_code == 200

    assert [c["user_id"] for c in _of_type(sent, APPROVED)] == [sender]


def test_requester_fallback_skips_an_integration_project_owner(api, sent):
    from app.models.projects import ProjectQuotationDocument
    from app.services import project_quotation_approval_service as approval
    from app.models.status import Status

    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    manager = _approver(db, f"{MARKER} Manager", company_id)
    bot = _user(db, f"{MARKER} Bot owner", is_integration=True)
    project.owner_user_id = bot
    row = db.query(ProjectQuotationDocument).filter(
        ProjectQuotationDocument.id == document["id"]
    ).first()
    pending = (
        db.query(Status)
        .filter(Status.entity_type == "quotation", Status.key == "pending_approval")
        .first()
    )
    approval._set_status(db, row, pending)  # pending with no "requested" activity row
    db.commit()
    _act_as(manager)

    assert client.post(f"{_root(project, document)}/approve").status_code == 200

    assert _of_type(sent, APPROVED) == []


# ------------------------------------------------------------------ round 3


def test_company_scope_an_approver_granted_only_another_company_is_not_told(api, sent):
    from app.models.company import Company

    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    seller = _user(db, f"{MARKER} Seller")
    granted = _approver(db, f"{MARKER} Granted", company_id)
    other = Company(id=str(uuid.uuid4()), name=f"{MARKER} Other Co", code=f"ZZ{uuid.uuid4().hex[:6]}")
    db.add(other)
    db.flush()
    elsewhere = _approver(db, f"{MARKER} Elsewhere", other.id, with_company=True)
    db.commit()
    root = _root(project, document)
    _act_as(seller)

    assert _send(client, db, root).status_code == 200
    assert [c["user_id"] for c in _of_type(sent, REQUESTED)] == [granted]
    assert client.post(f"{root}/approval-revise", json={"reason": "Changed"}).status_code == 200
    assert [c["user_id"] for c in _of_type(sent, WITHDRAWN)] == [granted]
    assert elsewhere not in [c["user_id"] for c in sent]


def test_whatsapp_portal_url_is_absolute_while_the_in_app_link_stays_relative(
    api, sent, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "frontend_base_url", "https://crm.example.test/", raising=False)
    client, db, company_id, _, project, product = api
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    seller = _user(db, f"{MARKER} Seller")
    _approver(db, f"{MARKER} A", company_id)
    db.commit()
    _act_as(seller)

    assert _send(client, db, _root(project, document)).status_code == 200

    call = _of_type(sent, REQUESTED)[0]
    link = f"/project-sales/{project.id}/quotation-documents/{document['id']}"
    assert call["data"]["link"] == link
    assert call["data"]["whatsapp_context_vars"]["portal_url"] == "https://crm.example.test" + link


def test_commit_time_recheck_losing_edit_rights_before_the_commit_keeps_the_document(
    api, monkeypatch
):
    from app.models.projects import ProjectQuotationDocument
    from app.models.sla import SlaFormAction
    from app.services.user_service import UserPermissionService

    client, db, _, _, project, product = api
    document = _quotation(client, project.id, product.id, ABOVE_FLOOR)
    parked = client.post(
        PENDING,
        json={
            "action_key": "quotation_document.delete",
            "entity_type": "quotation_document",
            "entity_id": document["id"],
            "payload": {},
        },
    )
    assert parked.status_code == 202, parked.text

    # The requester is no longer the owner, a collaborator or a manager.
    project.owner_user_id = _user(db, f"{MARKER} New owner")
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_permission_slugs",
        lambda self, uid: ["projects.projects.view", "projects.projects.edit",
                           "projects.projects.delete"],
    )
    db.query(SlaFormAction).filter(SlaFormAction.id == parked.json()["id"]).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False
    )
    db.commit()
    current = client.get(
        f"{PENDING}/current",
        params={"entity_type": "quotation_document", "entity_id": document["id"]},
    )
    assert current.status_code == 200, current.text
    assert (current.json().get("last_outcome") or {}).get("status") != "committed", current.text
    db.expire_all()
    assert (
        db.query(ProjectQuotationDocument)
        .filter(ProjectQuotationDocument.id == document["id"])
        .first()
        is not None
    )
