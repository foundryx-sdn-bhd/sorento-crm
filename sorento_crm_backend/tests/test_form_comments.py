"""FORM-COMMENTS Phase 2 red tests: the comment thread on a submitted form.

Spec: documentation/plans/forms/PLAN-form-comments-09oct.md, "Contract".
Service: ``app.services.form_comment_service`` (imported lazily so a missing module fails each
test on its own, not the collection of the file). Routes: ``/api/v1/form-comments/{kind}/{id}``
(staff) and ``/api/v1/public/portal/submissions/{kind}/{id}/comments`` (portal).

Postgres only (blank scratch schema); every row is seeded here. Nothing may leave the building:
``queue_service.enqueue_job`` is replaced by a recorder in every test that can reach an add path.

Run: SORENTO_ENV_FILE=.env.ci-tests venv/bin/python -m pytest tests/test_form_comments.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.complaints import Complaint
from app.models.entity_conversation import EntityConversationMessage
from app.models.notification import Notification
from app.models.procurement import PurchaseRequestHeader, StockInquiry
from app.models.user import User
from tests._pg_fixture import blank_session

MARKER = "ZZT-FCOM"
SORENTO = "00000000-0000-0000-0000-000000000001"
SPACE = "zzt-fcom-space"
# A ':' in the last segment passes straight through resolve_send_identifier (no contact row needed).
INBOX_URL = "https://app.respond.io/space/364817/inbox/id:60123"
SCOPE = "form_comment"
SERIALIZED_KEYS = {
    "id", "kind", "body", "author_name", "author_kind", "posted_by_name", "created_at", "attachments",
    "parent_id", "edited_at", "deleted", "can_edit", "can_delete", "can_reply",
}
ATTACHMENT_KEYS = {"attachment_id", "filename", "size", "content_type"}


def _uid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Fixtures and seeds
# --------------------------------------------------------------------------- #


@pytest.fixture
def db():
    from app.models.base import set_company_scope

    with blank_session() as session:
        set_company_scope(session, frozenset({SORENTO}))
        yield session


@pytest.fixture
def enqueued(monkeypatch):
    """Record every enqueue_job call; nothing is ever sent."""
    calls: list[dict] = []

    def _record(func, *args, **kwargs):
        calls.append({"func": func, "args": args, "kwargs": kwargs, "queue": kwargs.get("queue_name")})
        return None

    monkeypatch.setattr("app.services.queue_service.enqueue_job", _record)
    return calls


def _user(db, name="Staffer", responder_id=None) -> User:
    row = User(
        id=_uid(),
        email=f"{MARKER.lower()}-{uuid.uuid4().hex[:8]}@example.test",
        name=f"{MARKER} {name}",
        status="ACTIVE",
        respond_user_id=responder_id,
    )
    db.add(row)
    db.commit()
    return row


def _contact(db, name="Portal Contact") -> str:
    cid = _uid()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, name, session_vars) "
            "VALUES (:id, :rid, :phone, :name, CAST('{}' AS jsonb))"
        ),
        {
            "id": cid,
            "rid": f"ZZT-{_uid()[:8]}",
            "phone": f"+6006{uuid.uuid4().int % 10**7:07d}",
            "name": f"{MARKER} {name}",
        },
    )
    db.commit()
    return cid


def _inquiry(db, *, contact_id=None, status="responded", inbox=INBOX_URL, last_responded_by=None):
    row = StockInquiry(
        id=_uid(),
        inquiry_number=f"{MARKER}-SI-{uuid.uuid4().hex[:6]}",
        salesperson=f"{MARKER} salesperson",
        product_code=f"{MARKER}-P",
        status=status,
        contact_id=contact_id,
        space_id=SPACE if contact_id else None,
        respond_inbox_url=inbox,
        last_responded_by=last_responded_by,
    )
    db.add(row)
    db.commit()
    return row


def _complaint(db, *, contact_id=None, status="responded"):
    row = Complaint(
        id=_uid(),
        complaint_number=f"{MARKER}-C-{uuid.uuid4().hex[:6]}",
        customer_name=f"{MARKER} customer",
        status=status,
        contact_id=contact_id,
        space_id=SPACE if contact_id else None,
    )
    db.add(row)
    db.commit()
    return row


def _request(db, *, request_type="sponsorship_form", contact_id=None, status="responded"):
    row = PurchaseRequestHeader(
        id=_uid(),
        request_type=request_type,
        request_number=f"{MARKER}-R-{uuid.uuid4().hex[:6]}",
        status=status,
        contact_id=contact_id,
        space_id=SPACE if contact_id else None,
    )
    db.add(row)
    db.commit()
    return row


def _svc(db):
    from app.services.form_comment_service import FormCommentService  # lazy: red until it exists

    return FormCommentService(db)


def _unpack(result):
    """The service may return {comment, delivery} or a (comment, delivery) pair."""
    if isinstance(result, dict):
        return result["comment"], result["delivery"]
    return result[0], result[1]


def _strings(call: dict) -> list[str]:
    return [v for v in (*call["args"], *call["kwargs"].values()) if isinstance(v, str)]


def _by_queue(calls, queue):
    return [c for c in calls if c["queue"] == queue]


def _status_of(exc: HTTPException) -> int:
    return exc.status_code


# --------------------------------------------------------------------------- #
# Service: staff add
# --------------------------------------------------------------------------- #


def test_staff_add_on_responded_inquiry_queues_one_whatsapp(db, enqueued):
    staff = _user(db, "Alice")
    inquiry = _inquiry(db)

    comment, delivery = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "  Please confirm the colour.  "))

    assert comment["author_kind"] == "staff"
    assert comment["author_name"] == staff.name
    assert comment["body"] == "Please confirm the colour."
    assert delivery == "queued"
    sends = _by_queue(enqueued, "respond_io")
    assert len(sends) == 1
    func = sends[0]["func"]
    assert (func.__module__, func.__name__) == ("app.tasks.respond_io_tasks", "send_form_comment_message")
    texts = _strings(sends[0])
    message = next(t for t in texts if t.startswith("There is a new comment on your stock inquiry"))
    assert inquiry.inquiry_number in message
    assert "Please confirm the colour." in message
    assert _by_queue(enqueued, "notifications") == []


def test_staff_add_persists_comment_even_when_enqueue_fails(db, monkeypatch):
    staff = _user(db)
    inquiry = _inquiry(db)

    def _boom(func, *args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.queue_service.enqueue_job", _boom)

    comment, delivery = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "Still here"))

    assert delivery == "failed"
    db.rollback()  # a comment that was only flushed would vanish here; a committed one stays
    rows = db.query(EntityConversationMessage).filter(EntityConversationMessage.id == comment["id"]).all()
    assert len(rows) == 1 and rows[0].scope == SCOPE


def test_staff_add_without_inbox_url_saves_and_reports_no_whatsapp(db, enqueued, view_denied):
    staff = _user(db)
    inquiry = _inquiry(db, inbox=None)

    comment, delivery = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "Internal-ish"))

    assert comment["body"] == "Internal-ish"
    assert delivery == "no_whatsapp"
    assert _by_queue(enqueued, "respond_io") == []


# --------------------------------------------------------------------------- #
# Service: contact add
# --------------------------------------------------------------------------- #


def _emails_of_notifications(db) -> list[list[str]]:
    return [
        list((n.data or {}).get("recipient_emails") or [])
        for n in db.query(Notification).all()
    ]


def _tracker(db, kind: str, form_id: str, handler: User | None) -> None:
    """A form SLA tracker for (kind, form id) handled by ``handler`` (the only email recipient)."""
    from app.models.sla import ConversationSLATracking, SLAPolicy

    policy = SLAPolicy(id=_uid(), code=f"{MARKER}-{uuid.uuid4().hex[:6]}", name=f"{MARKER} policy", company_id=SORENTO)
    db.add(policy)
    db.flush()
    db.add(
        ConversationSLATracking(
            id=_uid(),
            policy_id=policy.id,
            current_tier=1,
            due_at=datetime(2026, 10, 20, 9, 0, 0),
            source_entity_type=kind,
            source_entity_id=form_id,
            handled_by_id=handler.id if handler else None,
        )
    )
    db.commit()


def test_contact_add_emails_only_the_sla_handler_and_never_whatsapps(db, enqueued, view_denied):
    contact_id = _contact(db, "Dealer Dan")
    handler = _user(db, "Handler")
    inquiry = _inquiry(db, contact_id=contact_id)
    _tracker(db, "stock_inquiry", inquiry.id, handler)

    comment, delivery = _unpack(
        _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "Need it sooner")
    )

    assert comment["author_kind"] == "submitter"
    assert comment["author_name"] == f"{MARKER} Dealer Dan"
    assert delivery == "queued"
    assert _by_queue(enqueued, "respond_io") == []
    mails = _by_queue(enqueued, "notifications")
    assert len(mails) == 1
    assert mails[0]["func"].__name__ == "send_notification_deliveries"
    assert _emails_of_notifications(db) == [[handler.email]]


def test_contact_add_with_no_tracker_reports_no_recipients(db, enqueued, view_denied):
    contact_id = _contact(db)
    inquiry = _inquiry(db, contact_id=contact_id)

    comment, delivery = _unpack(
        _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "Anyone there?")
    )

    assert delivery == "no_recipients"
    assert comment["body"] == "Anyone there?"
    assert enqueued == []
    assert _emails_of_notifications(db) == []
    assert db.query(EntityConversationMessage).filter(EntityConversationMessage.id == comment["id"]).count() == 1


def test_contact_add_with_a_handler_without_email_reports_no_recipients(db, enqueued):
    contact_id = _contact(db)
    handler = _user(db, "Mute")
    handler.email = "   "
    db.commit()
    complaint = _complaint(db, contact_id=contact_id)
    _tracker(db, "complaint", complaint.id, handler)

    comment, delivery = _unpack(
        _svc(db).add_contact_comment("complaint", complaint.id, contact_id, SPACE, "Hello?")
    )

    assert delivery == "no_recipients"
    assert enqueued == []
    assert db.query(EntityConversationMessage).filter(EntityConversationMessage.id == comment["id"]).count() == 1


# --------------------------------------------------------------------------- #
# Service: validation and states
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("body", ["", "   \n\t "], ids=["empty", "whitespace"])
def test_blank_body_is_422(db, enqueued, body):
    staff = _user(db)
    inquiry = _inquiry(db)
    with pytest.raises(HTTPException) as exc:
        _svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, body)
    assert _status_of(exc.value) == 422
    assert enqueued == []


def test_body_over_4000_chars_is_422_and_4000_is_accepted(db, enqueued):
    staff = _user(db)
    inquiry = _inquiry(db)
    svc = _svc(db)
    with pytest.raises(HTTPException) as exc:
        svc.add_staff_comment("stock_inquiry", inquiry.id, staff, "x" * 4001)
    assert _status_of(exc.value) == 422
    comment, _ = _unpack(svc.add_staff_comment("stock_inquiry", inquiry.id, staff, "x" * 4000))
    assert len(comment["body"]) == 4000


@pytest.mark.parametrize("status", ["draft", "voided"])
def test_add_on_draft_or_voided_row_is_422(db, enqueued, status):
    staff = _user(db)
    inquiry = _inquiry(db, status=status)
    with pytest.raises(HTTPException) as exc:
        _svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "too late")
    assert _status_of(exc.value) == 422
    assert enqueued == []


def test_contact_add_on_draft_row_is_422(db, enqueued):
    contact_id = _contact(db)
    inquiry = _inquiry(db, contact_id=contact_id, status="draft")
    with pytest.raises(HTTPException) as exc:
        _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hello")
    assert _status_of(exc.value) == 422


def test_list_works_on_a_voided_row(db, enqueued):
    staff = _user(db)
    inquiry = _inquiry(db)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, staff, "before void")
    inquiry.status = "voided"
    db.commit()

    items = svc.list_comments("stock_inquiry", inquiry.id)

    assert [c["body"] for c in items] == ["before void"]


# --------------------------------------------------------------------------- #
# Service: listing
# --------------------------------------------------------------------------- #


def test_list_is_oldest_first_and_only_form_comment_rows_of_that_entity(db, enqueued):
    staff = _user(db)
    inquiry = _inquiry(db)
    other_inquiry = _inquiry(db)
    svc = _svc(db)
    first, _ = _unpack(svc.add_staff_comment("stock_inquiry", inquiry.id, staff, "first"))
    second, _ = _unpack(svc.add_staff_comment("stock_inquiry", inquiry.id, staff, "second"))
    svc.add_staff_comment("stock_inquiry", other_inquiry.id, staff, "other entity")
    # Rows written in one transaction share now(); pin the order explicitly, newest id first.
    base = datetime(2026, 10, 1, 9, 0, 0)
    db.query(EntityConversationMessage).filter(EntityConversationMessage.id == second["id"]).update({"created_at": base})
    db.query(EntityConversationMessage).filter(EntityConversationMessage.id == first["id"]).update(
        {"created_at": base + timedelta(minutes=5)}
    )
    # A different scope on the SAME entity row key as the first comment.
    twin = db.query(EntityConversationMessage).filter(EntityConversationMessage.id == first["id"]).one()
    db.add(
        EntityConversationMessage(
            id=_uid(),
            entity_type=twin.entity_type,
            entity_id=twin.entity_id,
            scope="internal_note",
            body_html="a private note",
            created_at=base + timedelta(minutes=1),
        )
    )
    db.commit()

    items = svc.list_comments("stock_inquiry", inquiry.id)

    assert [c["body"] for c in items] == ["second", "first"]
    assert all(set(c) == SERIALIZED_KEYS for c in items)


def test_sponsorship_kind_works_on_its_row_and_purchase_request_kind_is_404(db, enqueued):
    staff = _user(db)
    row = _request(db, request_type="sponsorship_form")
    svc = _svc(db)

    comment, delivery = _unpack(svc.add_staff_comment("sponsorship_form", row.id, staff, "sponsor note"))

    assert comment["author_kind"] == "staff"
    assert delivery in {"queued", "no_whatsapp"}
    assert [c["body"] for c in svc.list_comments("sponsorship_form", row.id)] == ["sponsor note"]
    with pytest.raises(HTTPException) as exc:
        svc.list_comments("purchase_request", row.id)
    assert _status_of(exc.value) == 404


# --------------------------------------------------------------------------- #
# API: staff routes
# --------------------------------------------------------------------------- #


@pytest.fixture
def staff_client(db, monkeypatch, enqueued):
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.services.user_service import UserPermissionService

    actor_row = _user(db, "ApiActor")
    actor = {"id": actor_row.id, "email": actor_row.email, "role": "staff"}
    granted: set[str] = set()
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in granted)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_current_user_or_api_key] = lambda: actor
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            c.granted = granted
            c.actor = actor_row
            yield c
    finally:
        app.dependency_overrides.clear()


def test_staff_routes_are_403_without_the_kind_view_permission(staff_client, db):
    inquiry = _inquiry(db)
    url = f"/api/v1/form-comments/stock_inquiry/{inquiry.id}"
    staff_client.granted.add("complaint_management.complaints.view")  # a different kind's slug does not help

    assert staff_client.get(url).status_code == 403
    assert staff_client.post(url, data={"body": "hi"}).status_code == 403


def test_staff_routes_with_permission_list_and_post(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    url = f"/api/v1/form-comments/stock_inquiry/{inquiry.id}"
    staff_client.granted.add("procurement.stock_inquiries.view")

    posted = staff_client.post(url, data={"body": "From the API"})
    assert posted.status_code in (200, 201), posted.text
    payload = posted.json()
    assert set(payload) == {"comment", "delivery"}
    assert payload["comment"]["body"] == "From the API"
    assert payload["comment"]["author_kind"] == "staff"
    assert payload["delivery"] == "queued"

    listed = staff_client.get(url)
    assert listed.status_code == 200, listed.text
    assert [c["body"] for c in listed.json()["items"]] == ["From the API"]


def test_unknown_kind_is_422_and_unknown_row_is_404(staff_client, db):
    staff_client.granted.update(
        {
            "complaint_management.complaints.view",
            "procurement.stock_inquiries.view",
            "procurement.purchase_requests.view",
            "procurement.sponsorship_forms.view",
        }
    )
    assert staff_client.get(f"/api/v1/form-comments/not_a_kind/{_uid()}").status_code == 422
    assert staff_client.get(f"/api/v1/form-comments/stock_inquiry/{_uid()}").status_code == 404
    assert staff_client.post(f"/api/v1/form-comments/stock_inquiry/{_uid()}", data={"body": "x"}).status_code == 404


def test_staff_post_with_blank_body_is_422(staff_client, db):
    inquiry = _inquiry(db)
    staff_client.granted.add("procurement.stock_inquiries.view")
    resp = staff_client.post(f"/api/v1/form-comments/stock_inquiry/{inquiry.id}", data={"body": "   "})
    assert resp.status_code == 422


def test_serialized_comment_carries_no_user_or_contact_ids(staff_client, db):
    inquiry = _inquiry(db)
    staff_client.granted.add("procurement.stock_inquiries.view")
    url = f"/api/v1/form-comments/stock_inquiry/{inquiry.id}"
    posted = staff_client.post(url, data={"body": "ids stay home"}).json()

    assert set(posted["comment"]) == SERIALIZED_KEYS
    listed = staff_client.get(url).json()["items"]
    assert all(set(c) == SERIALIZED_KEYS for c in listed)
    blob = str(posted) + str(listed)
    assert "author_user_id" not in blob and "author_contact_id" not in blob


# --------------------------------------------------------------------------- #
# API: portal routes
# --------------------------------------------------------------------------- #


@pytest.fixture
def portal(db, enqueued):
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.base import set_company_scope
    from app.models.portal import PortalToken
    from app.services.company_scope_resolver import apply_company_scope

    async def _scope():
        set_company_scope(db, frozenset({SORENTO}))
        return frozenset({SORENTO})

    state = {"contact": None}

    def _as(contact_id: str) -> TestClient:
        state["contact"] = contact_id
        return TestClient(app, headers={"X-Portal-Token": "zzt-token"}, raise_server_exceptions=False)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[apply_company_scope] = _scope
    app.dependency_overrides[get_portal_token] = lambda: PortalToken(
        id=_uid(), contact_id=state["contact"], space_id=SPACE
    )
    try:
        yield _as
    finally:
        app.dependency_overrides.clear()


def test_portal_owner_can_list_and_post_a_comment(portal, db, enqueued):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    client = portal(owner)
    url = f"/api/v1/public/portal/submissions/stock_inquiry/{inquiry.id}/comments"

    posted = client.post(url, data={"body": "from the portal"})
    assert posted.status_code in (200, 201), posted.text
    payload = posted.json()
    assert payload["comment"]["author_kind"] == "submitter"
    assert payload["comment"]["author_name"] == f"{MARKER} Owner"
    assert set(payload["comment"]) == SERIALIZED_KEYS
    assert "delivery" in payload

    listed = client.get(url)
    assert listed.status_code == 200, listed.text
    assert [c["body"] for c in listed.json()["items"]] == ["from the portal"]
    assert _by_queue(enqueued, "respond_io") == []


def test_portal_other_contact_gets_404_on_both_verbs(portal, db, enqueued):
    owner = _contact(db, "Owner")
    stranger = _contact(db, "Stranger")
    inquiry = _inquiry(db, contact_id=owner)
    url = f"/api/v1/public/portal/submissions/stock_inquiry/{inquiry.id}/comments"
    # positive control: the route exists and the owner is served
    assert portal(owner).get(url).status_code == 200

    other = portal(stranger)
    assert other.get(url).status_code == 404
    assert other.post(url, data={"body": "not mine"}).status_code == 404
    assert db.query(EntityConversationMessage).filter(EntityConversationMessage.scope == SCOPE).count() == 0


# --------------------------------------------------------------------------- #
# Attachments (owner answer Q5 b)
# --------------------------------------------------------------------------- #

PDF = ("quote.pdf", b"%PDF-1.4 zzt fixture", "application/pdf")


class _FakeBackend:
    def __init__(self) -> None:
        self.uploads: list[str] = []

    def upload_file(self, *args, **kwargs):
        key = args[1] if args else kwargs.get("file_path")
        self.uploads.append(key)
        return key, f"https://cdn.test/{key}"


@pytest.fixture
def storage(db, monkeypatch):
    """Both attachment types seeded, storage and thumbnails faked: no byte leaves the sandbox."""
    import app.services.storage_router as storage_router
    from app.models.resources import AttachmentType

    for code in ("response_attachment", "portal_submission"):
        if not db.query(AttachmentType).filter(AttachmentType.code == code).first():
            db.add(
                AttachmentType(
                    id=_uid(),
                    code=code,
                    type_name=f"{MARKER} {code}",
                    allowed_extensions="jpg,jpeg,png,pdf,xlsx",
                    max_file_size_mb=10,
                )
            )
    db.commit()
    backend = _FakeBackend()
    monkeypatch.setattr(storage_router, "default_provider", lambda: "s3")
    monkeypatch.setattr(storage_router, "get_backend", lambda provider: backend)
    monkeypatch.setattr(storage_router, "cdn_base_url", lambda provider, key: f"https://cdn.test/{key}")
    monkeypatch.setattr("app.services.image_thumbnailer.store_thumbnail", lambda *a, **k: None)
    return backend


def _links(db, attachment_id: str) -> set[tuple[str, str]]:
    from app.models.entity_attachment import EntityAttachmentLink

    rows = db.query(EntityAttachmentLink).filter(EntityAttachmentLink.attachment_id == attachment_id).all()
    return {(r.entity_type, str(r.entity_id)) for r in rows}


def test_staff_comment_with_a_pdf_links_the_file_to_the_comment_only(db, enqueued, storage):
    staff = _user(db)
    inquiry = _inquiry(db)

    comment, _ = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "see attached", files=[PDF]))

    assert len(comment["attachments"]) == 1
    att = comment["attachments"][0]
    assert set(att) == ATTACHMENT_KEYS
    assert (att["filename"], att["size"], att["content_type"]) == (PDF[0], len(PDF[1]), PDF[2])
    assert _links(db, att["attachment_id"]) == {("form_comment", comment["id"])}
    assert len(storage.uploads) == 1


def test_contact_comment_with_a_file_is_a_contact_upload_linked_to_the_comment_only(db, enqueued, storage):
    from app.models.resources import Attachment

    contact_id = _contact(db)
    inquiry = _inquiry(db, contact_id=contact_id)

    comment, _ = _unpack(
        _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "my file", files=[PDF])
    )

    att = comment["attachments"][0]
    row = db.query(Attachment).filter(Attachment.id == att["attachment_id"]).one()
    assert row.uploader_kind == "contact"
    assert row.uploaded_by_contact_id == contact_id
    assert _links(db, att["attachment_id"]) == {("form_comment", comment["id"])}


def test_sponsorship_comment_file_links_to_the_comment_only(db, enqueued, storage):
    staff = _user(db)
    row = _request(db, request_type="sponsorship_form")

    comment, _ = _unpack(_svc(db).add_staff_comment("sponsorship_form", row.id, staff, "sponsor file", files=[PDF]))

    att = comment["attachments"][0]
    assert _links(db, att["attachment_id"]) == {("form_comment", comment["id"])}


def test_empty_body_with_one_file_is_accepted(db, enqueued, storage):
    staff = _user(db)
    inquiry = _inquiry(db)

    comment, _ = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "", files=[PDF]))

    assert comment["body"] == ""
    assert len(comment["attachments"]) == 1


def test_empty_body_without_files_is_422(db, enqueued, storage):
    staff = _user(db)
    inquiry = _inquiry(db)
    with pytest.raises(HTTPException) as exc:
        _svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "  ", files=[])
    assert _status_of(exc.value) == 422


def test_six_files_is_422_and_five_is_accepted(db, enqueued, storage):
    staff = _user(db)
    inquiry = _inquiry(db)
    svc = _svc(db)
    with pytest.raises(HTTPException) as exc:
        svc.add_staff_comment("stock_inquiry", inquiry.id, staff, "too many", files=[PDF] * 6)
    assert _status_of(exc.value) == 422
    comment, _ = _unpack(svc.add_staff_comment("stock_inquiry", inquiry.id, staff, "five", files=[PDF] * 5))
    assert len(comment["attachments"]) == 5


def test_whatsapp_text_names_the_attached_files(db, enqueued, storage):
    staff = _user(db)
    inquiry = _inquiry(db)

    _svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "see attached", files=[PDF])

    sends = _by_queue(enqueued, "respond_io")
    assert len(sends) == 1
    assert any(f"Attachments: {PDF[0]}" in t for t in _strings(sends[0]))


def test_list_comments_returns_each_comments_attachments(db, enqueued, storage):
    staff = _user(db)
    inquiry = _inquiry(db)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, staff, "with file", files=[PDF])

    items = svc.list_comments("stock_inquiry", inquiry.id)

    assert len(items) == 1
    assert [set(a) for a in items[0]["attachments"]] == [ATTACHMENT_KEYS]
    assert items[0]["attachments"][0]["filename"] == PDF[0]


def test_comment_without_files_has_an_empty_attachments_list(db, enqueued, storage):
    staff = _user(db)
    inquiry = _inquiry(db)
    comment, _ = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "plain"))
    assert comment["attachments"] == []


def test_staff_api_multipart_post_returns_201_with_one_attachment(staff_client, db, storage):
    inquiry = _inquiry(db)
    staff_client.granted.add("procurement.stock_inquiries.view")

    resp = staff_client.post(
        f"/api/v1/form-comments/stock_inquiry/{inquiry.id}",
        data={"body": "with file"},
        files=[("files", PDF)],
    )

    assert resp.status_code == 201, resp.text
    assert len(resp.json()["comment"]["attachments"]) == 1


def test_portal_api_multipart_post_returns_201_with_one_attachment(portal, db, storage):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)

    resp = portal(owner).post(
        f"/api/v1/public/portal/submissions/stock_inquiry/{inquiry.id}/comments",
        data={"body": "with file"},
        files=[("files", PDF)],
    )

    assert resp.status_code == 201, resp.text
    assert len(resp.json()["comment"]["attachments"]) == 1


def test_portal_download_of_a_comment_file_works_for_the_owner_and_404s_for_others(portal, db, storage, monkeypatch, view_denied):
    owner = _contact(db, "Owner")
    stranger = _contact(db, "Stranger")
    inquiry = _inquiry(db, contact_id=owner)
    staff = _user(db)
    comment, _ = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "for you", files=[PDF]))
    attachment_id = comment["attachments"][0]["attachment_id"]
    monkeypatch.setattr("app.services.resources_service.AttachmentService.get_file_content", lambda self, aid: PDF[1])
    url = f"/api/v1/public/portal/attachments/{attachment_id}/download"

    ok = portal(owner).get(url)
    assert ok.status_code == 200, ok.text
    assert ok.content == PDF[1]
    assert portal(stranger).get(url).status_code == 404


# --------------------------------------------------------------------------- #
# Review round: handler choice, failure isolation, names, limits, guards
# --------------------------------------------------------------------------- #


def _tracker_at(db, kind, form_id, handler, *, initiated, resolved=False):
    from app.models.sla import ConversationSLATracking, SLAPolicy

    policy = SLAPolicy(id=_uid(), code=f"{MARKER}-{uuid.uuid4().hex[:6]}", name=f"{MARKER} policy", company_id=SORENTO)
    db.add(policy)
    db.flush()
    row = ConversationSLATracking(
        id=_uid(),
        policy_id=policy.id,
        current_tier=1,
        due_at=datetime(2026, 10, 20, 9, 0, 0),
        source_entity_type=kind,
        source_entity_id=form_id,
        handled_by_id=handler.id,
        initiated_at=initiated,
    )
    if resolved:
        # "Open" is is_resolved False and not voided (sla_scope.open_tracker_scope); the
        # timestamp alone would leave the stage open and the fallback path untested.
        row.is_resolved = True
        row.resolved_at = initiated + timedelta(hours=1)
    db.add(row)
    db.commit()


def _recipients(db):
    return [e for emails in _emails_of_notifications(db) for e in emails]


def test_open_newer_tracker_handler_wins_over_resolved_older(db, enqueued, view_denied):
    contact_id = _contact(db)
    a, b = _user(db, "A"), _user(db, "B")
    inquiry = _inquiry(db, contact_id=contact_id)
    _tracker_at(db, "stock_inquiry", inquiry.id, a, initiated=datetime(2026, 10, 1), resolved=True)
    _tracker_at(db, "stock_inquiry", inquiry.id, b, initiated=datetime(2026, 10, 2))
    _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi")
    assert _recipients(db) == [b.email]


def test_only_resolved_trackers_newest_handler_wins(db, enqueued, view_denied):
    contact_id = _contact(db)
    a, b = _user(db, "A"), _user(db, "B")
    inquiry = _inquiry(db, contact_id=contact_id)
    _tracker_at(db, "stock_inquiry", inquiry.id, a, initiated=datetime(2026, 10, 1), resolved=True)
    _tracker_at(db, "stock_inquiry", inquiry.id, b, initiated=datetime(2026, 10, 2), resolved=True)
    _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi")
    assert _recipients(db) == [b.email]


def test_another_forms_tracker_is_never_used(db, enqueued, view_denied):
    contact_id = _contact(db)
    c, mine = _user(db, "C"), _user(db, "Mine")
    inquiry, other = _inquiry(db, contact_id=contact_id), _inquiry(db, contact_id=contact_id)
    _tracker_at(db, "stock_inquiry", other.id, c, initiated=datetime(2026, 10, 3))
    _tracker_at(db, "stock_inquiry", inquiry.id, mine, initiated=datetime(2026, 10, 1))
    _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi")
    assert _recipients(db) == [mine.email]



def test_fallback_to_the_newest_resolved_tracker_stays_on_this_form(db, enqueued, view_denied):
    # Only resolved stages, so the newest-tracker fallback runs: another form's newer resolved
    # stage must never win over this form's own (kill test K3).
    contact_id = _contact(db)
    c, mine = _user(db, "C"), _user(db, "Mine")
    inquiry, other = _inquiry(db, contact_id=contact_id), _inquiry(db, contact_id=contact_id)
    _tracker_at(db, "stock_inquiry", inquiry.id, mine, initiated=datetime(2026, 10, 1), resolved=True)
    _tracker_at(db, "stock_inquiry", other.id, c, initiated=datetime(2026, 10, 3), resolved=True)
    _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi")
    assert _recipients(db) == [mine.email]

@pytest.mark.parametrize("breaker", ["enqueue", "render"])
def test_submitter_comment_survives_a_notification_failure(db, monkeypatch, breaker, view_denied):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    inquiry = _inquiry(db, contact_id=contact_id)
    _tracker(db, "stock_inquiry", inquiry.id, handler)

    def _boom(*a, **k):
        raise RuntimeError("down")

    if breaker == "enqueue":
        monkeypatch.setattr("app.services.queue_service.enqueue_job", _boom)
    else:
        monkeypatch.setattr("app.services.email_template_service.EmailTemplateService.render_code", _boom)

    comment, delivery = _unpack(_svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "kept"))

    assert delivery == "failed"
    db.rollback()
    assert db.query(EntityConversationMessage).filter(EntityConversationMessage.id == comment["id"]).count() == 1


def test_staff_author_without_a_name_reads_sorento_team(db, enqueued):
    staff = _user(db)
    staff.name = "  "
    db.commit()
    inquiry = _inquiry(db)
    comment, _ = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "hello"))
    assert comment["author_name"] == "Sorento team"
    assert staff.email not in str(_svc(db).list_comments("stock_inquiry", inquiry.id))


def test_portal_post_is_429_when_the_bucket_is_full(portal, db, monkeypatch):
    from app.services import rate_limit

    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    monkeypatch.setattr(rate_limit, "peek", lambda *a, **k: rate_limit.RateResult(allowed=False, retry_after_seconds=30))
    resp = portal(owner).post(
        f"/api/v1/public/portal/submissions/stock_inquiry/{inquiry.id}/comments", data={"body": "x"}
    )
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "30"
    assert db.query(EntityConversationMessage).filter(EntityConversationMessage.scope == SCOPE).count() == 0


def _impersonation(db, monkeypatch, *, contact_id, ended=False, admin_roles=frozenset({"admin"})):
    """A persisted impersonation portal token + session for a fresh admin; returns (token, admin)."""
    from datetime import datetime as _dt

    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.impersonation import ContactImpersonationSession
    from app.models.portal import PortalToken
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    admin = _user(db, "Admin")
    token = PortalToken(
        token=f"zzt-{uuid.uuid4().hex}",
        contact_id=contact_id,
        space_id=SPACE,
        expires_at=_dt.utcnow() + timedelta(hours=1),
        verified_at=_dt.utcnow(),
        is_impersonation=True,
    )
    db.add(token)
    db.flush()
    db.add(
        ContactImpersonationSession(
            admin_user_id=admin.id,
            target_contact_id=contact_id,
            space_id=SPACE,
            portal_token_id=token.id,
            ended_at=_dt.utcnow() if ended else None,
        )
    )
    db.commit()
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set(admin_roles))

    async def _scope():
        return frozenset({SORENTO})

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[apply_company_scope] = _scope
    app.dependency_overrides[get_portal_token] = lambda: token
    return token, admin


def _comment_rows(db):
    return db.query(EntityConversationMessage).filter(EntityConversationMessage.scope == SCOPE).all()


def test_impersonating_admin_comments_on_behalf_of_the_contact(db, enqueued, storage, monkeypatch, view_denied):
    from app.models.resources import Attachment

    owner = _contact(db, "Owner")
    handler = _user(db, "Handler")
    inquiry = _inquiry(db, contact_id=owner)
    _tracker(db, "stock_inquiry", inquiry.id, handler)
    _, admin = _impersonation(db, monkeypatch, contact_id=owner)
    url = f"/api/v1/public/portal/submissions/stock_inquiry/{inquiry.id}/comments"
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(url, data={"body": "on their behalf"}, files=[("files", PDF)])
        assert resp.status_code == 201, resp.text
        comment = resp.json()["comment"]
        assert set(comment) == SERIALIZED_KEYS
        assert (comment["author_kind"], comment["author_name"]) == ("submitter", f"{MARKER} Owner")
        assert comment["posted_by_name"] == admin.name
        row = _comment_rows(db)[0]
        assert (row.author_contact_id, row.author_user_id) == (owner, admin.id)
        att = db.query(Attachment).filter(Attachment.id == comment["attachments"][0]["attachment_id"]).one()
        assert (att.uploader_kind, att.uploaded_by_contact_id, att.uploaded_by) == ("contact", owner, admin.id)
        assert _by_queue(enqueued, "respond_io") == []
        assert _recipients(db) == [handler.email]
        notif = db.query(Notification).one()
        assert f"Posted by {admin.name} on behalf of {MARKER} Owner." in (notif.body or "")
        listed = client.get(url).json()["items"][0]
        assert (listed["author_kind"], listed["posted_by_name"]) == ("submitter", admin.name)
    finally:
        app.dependency_overrides.clear()
    assert "author_user_id" not in str(listed)


def test_impersonating_admin_comment_reads_the_same_on_the_staff_side(staff_client, db, monkeypatch):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    admin = _user(db, "Admin")
    svc = _svc(db)
    svc.add_contact_comment("stock_inquiry", inquiry.id, owner, SPACE, "behalf", acting_user_id=admin.id)
    staff_client.granted.add("procurement.stock_inquiries.view")
    item = staff_client.get(f"/api/v1/form-comments/stock_inquiry/{inquiry.id}").json()["items"][0]
    assert (item["author_kind"], item["author_name"], item["posted_by_name"]) == (
        "submitter", f"{MARKER} Owner", admin.name,
    )


def test_acting_admin_without_a_name_reads_sorento_team(db, enqueued):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    admin = _user(db, "Admin")
    admin.name = " "
    db.commit()
    comment, _ = _unpack(
        _svc(db).add_contact_comment("stock_inquiry", inquiry.id, owner, SPACE, "x", acting_user_id=admin.id)
    )
    assert comment["posted_by_name"] == "Sorento team"


@pytest.mark.parametrize("case", ["ended", "lost_role", "inactive", "trashed"])
def test_impersonation_post_is_403_and_saves_nothing_when_no_longer_allowed(db, enqueued, monkeypatch, case):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    _token, admin = _impersonation(
        db, monkeypatch, contact_id=owner, ended=(case == "ended"),
        admin_roles=frozenset() if case == "lost_role" else frozenset({"admin"}),
    )
    if case == "inactive":
        admin.status = "INACTIVE"
    elif case == "trashed":
        admin.is_trashed = True
    db.commit()
    try:
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/public/portal/submissions/stock_inquiry/{inquiry.id}/comments", data={"body": "x"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403
    assert "viewing as a contact" in resp.text
    assert _comment_rows(db) == []


def test_unknown_portal_token_is_401(db):
    from app.database import get_db

    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    app.dependency_overrides[get_db] = lambda: db
    try:
        resp = TestClient(app, headers={"X-Portal-Token": "zzt-unknown"}, raise_server_exceptions=False).post(
            f"/api/v1/public/portal/submissions/stock_inquiry/{inquiry.id}/comments", data={"body": "x"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 401


def test_plain_contact_comment_has_null_posted_by_name(db, enqueued):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    comment, _ = _unpack(_svc(db).add_contact_comment("stock_inquiry", inquiry.id, owner, SPACE, "mine"))
    assert comment["posted_by_name"] is None


def test_portal_non_uuid_submission_id_is_not_a_500(portal, db):
    owner = _contact(db, "Owner")
    client = portal(owner)
    url = "/api/v1/public/portal/submissions/stock_inquiry/not-a-uuid/comments"
    assert client.get(url).status_code in (404, 422)
    assert client.post(url, data={"body": "x"}).status_code in (404, 422)


def test_module_gate_is_per_kind_when_strict(staff_client, db, monkeypatch):
    from app.config import settings

    staff_client.granted.update({"complaint_management.complaints.view", "procurement.stock_inquiries.view"})
    complaint, inquiry = _complaint(db), _inquiry(db)
    monkeypatch.setattr(settings, "module_guard_strict", True, raising=False)
    monkeypatch.setattr("app.modules.runtime.installer.tenant_has_any_module_row", lambda db, t: True)
    monkeypatch.setattr(
        "app.modules.runtime.installer.is_module_enabled", lambda db, t, m: m == "procurement"
    )
    monkeypatch.setattr("app.modules.runtime.guards._all_modules_effectively_enabled", lambda db, t: False)
    monkeypatch.setattr("app.modules.runtime.guards.is_module_enabled", lambda db, t, m: m == "procurement")
    assert staff_client.get(f"/api/v1/form-comments/complaint/{complaint.id}").status_code == 403
    assert staff_client.get(f"/api/v1/form-comments/stock_inquiry/{inquiry.id}").status_code == 200


def test_more_than_five_files_is_422_before_any_read(staff_client, db, storage):
    inquiry = _inquiry(db)
    staff_client.granted.add("procurement.stock_inquiries.view")
    resp = staff_client.post(
        f"/api/v1/form-comments/stock_inquiry/{inquiry.id}",
        data={"body": "many"},
        files=[("files", PDF)] * 6,
    )
    assert resp.status_code == 422
    assert storage.uploads == []


# ---------------------------------------------------------------------------
# Crew audit (9 Oct): a comment never moves the form's status (owner Q3), and both upload paths
# enforce their attachment type's extension allowlist.


@pytest.mark.parametrize("author", ["staff", "submitter"])
@pytest.mark.parametrize(
    "kind,status",
    [
        ("stock_inquiry", "responded"),
        ("stock_inquiry", "pending_purchasing"),
        ("complaint", "responded"),
        ("purchase_request", "approved"),
        ("sponsorship_form", "approved"),
    ],
)
def test_a_comment_never_changes_the_form_status(db, enqueued, kind, status, author):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    if kind == "stock_inquiry":
        row = _inquiry(db, contact_id=contact_id, status=status)
        row.purchasing_response = "brand?"
        db.commit()
    elif kind == "complaint":
        row = _complaint(db, contact_id=contact_id, status=status)
    else:
        row = _request(db, request_type=kind, contact_id=contact_id, status=status)
    _tracker(db, kind, row.id, handler)
    form_id = row.id
    model = type(row)

    if author == "staff":
        _svc(db).add_staff_comment(kind, form_id, _user(db), "Which brand?")
    else:
        _svc(db).add_contact_comment(kind, form_id, contact_id, SPACE, "Hafele")

    db.expire_all()
    fresh = db.query(model).filter(model.id == form_id).one()
    assert fresh.status == status
    if kind == "stock_inquiry":
        assert fresh.purchasing_response == "brand?"


@pytest.mark.parametrize("author", ["staff", "submitter"])
def test_a_disallowed_file_type_is_refused_and_nothing_is_saved(db, enqueued, storage, author):
    # The shared attachment-type check (check_quota / portal _check_quota) answers 400, the same
    # as the existing staff response and portal upload routes.
    from app.models.entity_conversation import EntityConversationMessage

    contact_id = _contact(db)
    inquiry = _inquiry(db, contact_id=contact_id)
    bad = ("payload.exe", b"MZ\x90\x00", "application/octet-stream")

    with pytest.raises(HTTPException) as exc:
        if author == "staff":
            _svc(db).add_staff_comment("stock_inquiry", inquiry.id, _user(db), "x", files=[bad])
        else:
            _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "x", files=[bad])

    assert _status_of(exc.value) == 400
    assert "Unsupported file type" in str(getattr(exc.value, "detail", exc.value))
    db.rollback()
    assert (
        db.query(EntityConversationMessage)
        .filter(EntityConversationMessage.entity_id == inquiry.id)
        .count()
        == 0
    )
    assert storage.uploads == []
    assert enqueued == []


def test_comment_files_do_not_count_as_staff_response_attachments(db, enqueued, storage):
    from app.services.entity_attachment_service import count_staff_attachments

    staff = _user(db)
    inquiry = _inquiry(db)
    _svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "see attached", files=[PDF])
    assert count_staff_attachments(db, "stock_inquiry", inquiry.id) == 0


def test_portal_attachment_list_and_detail_hide_comment_files(portal, db, storage):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    staff = _user(db)
    _svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "for you", files=[PDF])
    client = portal(owner)

    listed = client.get(
        "/api/v1/public/portal/attachments", params={"kind": "stock_inquiry", "submission_id": inquiry.id}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []
    detail = client.get(f"/api/v1/public/portal/submissions/stock_inquiry/{inquiry.id}")
    assert detail.status_code == 200, detail.text
    assert PDF[0] not in detail.text


def test_portal_cannot_delete_a_comment_file_link(portal, db, storage):
    from app.models.entity_attachment import EntityAttachmentLink
    from app.models.resources import Attachment

    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    comment, _ = _unpack(
        _svc(db).add_contact_comment("stock_inquiry", inquiry.id, owner, SPACE, "mine", files=[PDF])
    )
    link = db.query(EntityAttachmentLink).filter(EntityAttachmentLink.entity_id == comment["id"]).one()

    resp = portal(owner).delete(f"/api/v1/public/portal/attachments/{link.id}")

    assert resp.status_code in (403, 404)
    assert db.query(EntityAttachmentLink).filter(EntityAttachmentLink.id == link.id).count() == 1
    assert db.query(Attachment).filter(Attachment.id == comment["attachments"][0]["attachment_id"]).count() == 1


# --------------------------------------------------------------------------- #
# Round 2 (9 Oct): the reply as the first entry, reply / edit / delete
# Spec: PLAN-form-comments-09oct.md, "Owner hand test round 2" (the Contract is binding).
# Ambiguities pinned here: PATCH takes a JSON body {"body": ...}; the pending-action executor
# is `get_action("form_comment.delete").execute(db, {"entity_id": <comment id>,
# "requested_by_id": <user id>})` (the comment row itself says which form it is on); the
# portal DELETE answers 200 or 204.
# --------------------------------------------------------------------------- #

INQ_VIEW = "procurement.stock_inquiries.view"
COMPLAINT_VIEW = "complaint_management.complaints.view"


def _staff_url(kind, form_id, comment_id=None):
    base = f"/api/v1/form-comments/{kind}/{form_id}"
    return base + (f"/comments/{comment_id}" if comment_id else "")


def _portal_url(kind, form_id, comment_id=None):
    base = f"/api/v1/public/portal/submissions/{kind}/{form_id}/comments"
    return base + (f"/{comment_id}" if comment_id else "")


def _staff_says(db, kind, form_id, user, body, **kw):
    return _unpack(_svc(db).add_staff_comment(kind, form_id, user, body, **kw))[0]


def _contact_says(db, kind, form_id, contact_id, body, **kw):
    return _unpack(_svc(db).add_contact_comment(kind, form_id, contact_id, SPACE, body, **kw))[0]


def _roles(monkeypatch, admins=()):
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"} if str(uid) in set(admins) else set()
    )


def _as_staff_list(staff_client, kind, form_id, slug=INQ_VIEW):
    staff_client.granted.add(slug)
    resp = staff_client.get(_staff_url(kind, form_id))
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _row(db, comment_id):
    db.expire_all()
    return db.query(EntityConversationMessage).filter(EntityConversationMessage.id == comment_id).first()


def _mark_deleted(db, comment_id):
    db.execute(text("UPDATE entity_conversation_messages SET deleted_at = now() WHERE id = :i"), {"i": comment_id})
    db.commit()


def _with_reply(db, inquiry, responder, text_="brand?"):
    inquiry.purchasing_response = text_
    inquiry.last_responded_by = responder.respond_user_id
    inquiry.last_responded_at = datetime(2026, 10, 2, 8, 30, 0)
    db.commit()


# ---- the response entry ------------------------------------------------------------------


def test_list_starts_with_the_purchasing_reply_as_a_synthetic_entry(staff_client, db, enqueued):
    responder = _user(db, "Responder", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    inquiry = _inquiry(db)
    _with_reply(db, inquiry, responder)
    _staff_says(db, "stock_inquiry", inquiry.id, _user(db, "Later"), "a later comment")

    items = _as_staff_list(staff_client, "stock_inquiry", inquiry.id)

    first = items[0]
    assert (first["id"], first["kind"]) == ("response", "response")
    assert first["author_name"] == responder.name
    assert first["author_kind"] == "staff"
    assert first["body"] == "brand?"
    assert first["created_at"].startswith("2026-10-02T08:30:00")
    assert (first["can_edit"], first["can_delete"], first["can_reply"]) == (False, False, False)
    assert [i["kind"] for i in items] == ["response", "comment"]


def test_list_times_carry_a_utc_offset(staff_client, db, enqueued):
    """COMMENT-TZ: the columns hold naive UTC, so the API stamps +00:00 rather than leave the
    reader to guess (a 16:26 MYT comment read as 8:26 AM)."""
    responder = _user(db, "Responder", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    inquiry = _inquiry(db)
    _with_reply(db, inquiry, responder)
    said = _staff_says(db, "stock_inquiry", inquiry.id, _user(db, "Later"), "a later comment")
    db.query(EntityConversationMessage).filter(EntityConversationMessage.id == said["id"]).update(
        {"created_at": datetime(2026, 10, 9, 8, 26, 20), "edited_at": datetime(2026, 10, 9, 8, 30, 0)}
    )
    db.commit()

    items = _as_staff_list(staff_client, "stock_inquiry", inquiry.id)

    assert items[0]["created_at"] == "2026-10-02T08:30:00+00:00"
    assert items[1]["created_at"] == "2026-10-09T08:26:20+00:00"
    assert items[1]["edited_at"] == "2026-10-09T08:30:00+00:00"


def test_response_entry_falls_back_to_the_team_name_for_an_unknown_responder(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    inquiry.purchasing_response = "no idea who"
    inquiry.last_responded_by = "resp-nobody"
    inquiry.last_responded_at = datetime(2026, 10, 2, 8, 30, 0)
    db.commit()
    assert _as_staff_list(staff_client, "stock_inquiry", inquiry.id)[0]["author_name"] == "Sorento team"


def test_complaint_response_entry_uses_the_technical_team_response(staff_client, db, enqueued):
    responder = _user(db, "Tech", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    complaint = _complaint(db)
    complaint.technical_team_response = "replace the unit"
    complaint.last_responded_by = responder.respond_user_id
    complaint.last_responded_at = datetime(2026, 10, 3, 9, 0, 0)
    db.commit()

    first = _as_staff_list(staff_client, "complaint", complaint.id, COMPLAINT_VIEW)[0]

    assert (first["id"], first["body"], first["author_name"]) == ("response", "replace the unit", responder.name)


@pytest.mark.parametrize("reply", [None, "", "   \n"], ids=["none", "empty", "whitespace"])
def test_no_response_entry_when_the_reply_is_blank(staff_client, db, enqueued, reply):
    inquiry = _inquiry(db)
    inquiry.purchasing_response = reply
    db.commit()
    assert _as_staff_list(staff_client, "stock_inquiry", inquiry.id) == []


@pytest.mark.parametrize("request_type", ["purchase_request", "sponsorship_form"])
def test_request_kinds_have_no_response_entry(staff_client, db, enqueued, request_type):
    row = _request(db, request_type=request_type)
    slug = "procurement.purchase_requests.view" if request_type == "purchase_request" else "procurement.sponsorship_forms.view"
    assert _as_staff_list(staff_client, request_type, row.id, slug) == []


def test_response_entry_carries_the_forms_response_attachment_files(staff_client, db, enqueued, storage):
    from app.services.entity_attachment_service import create_response_attachment

    responder = _user(db, "Responder", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    inquiry = _inquiry(db)
    _with_reply(db, inquiry, responder)
    create_response_attachment(
        db, entity_type="stock_inquiry", entity_id=inquiry.id, contents=PDF[1], filename=PDF[0],
        content_type=PDF[2], uploaded_by=responder.id,
    )

    first = _as_staff_list(staff_client, "stock_inquiry", inquiry.id)[0]

    assert [set(a) for a in first["attachments"]] == [ATTACHMENT_KEYS]
    assert first["attachments"][0]["filename"] == PDF[0]


def test_every_comment_entry_carries_the_thread_fields(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "plain")

    item = _as_staff_list(staff_client, "stock_inquiry", inquiry.id)[0]

    assert set(item) == SERIALIZED_KEYS
    assert (item["kind"], item["parent_id"], item["edited_at"], item["deleted"]) == ("comment", None, None, False)


# ---- reply -------------------------------------------------------------------------------


def test_staff_reply_is_201_with_parent_id_and_notifies_like_a_comment(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    top = _staff_says(db, "stock_inquiry", inquiry.id, _user(db, "Other"), "top level")
    enqueued.clear()
    staff_client.granted.add(INQ_VIEW)

    resp = staff_client.post(_staff_url("stock_inquiry", inquiry.id), data={"body": "a reply", "parent_id": top["id"]})

    assert resp.status_code == 201, resp.text
    assert resp.json()["comment"]["parent_id"] == top["id"]
    assert len(_by_queue(enqueued, "respond_io")) == 1


def test_submitter_reply_emails_the_handler(portal, db, enqueued, view_denied):
    owner = _contact(db, "Owner")
    handler = _user(db, "Handler")
    inquiry = _inquiry(db, contact_id=owner)
    _tracker(db, "stock_inquiry", inquiry.id, handler)
    staffer = _user(db, "Staffer")
    top = _staff_says(db, "stock_inquiry", inquiry.id, staffer, "question")
    enqueued.clear()

    resp = portal(owner).post(_portal_url("stock_inquiry", inquiry.id), data={"body": "answer", "parent_id": top["id"]})

    assert resp.status_code == 201, resp.text
    assert resp.json()["comment"]["parent_id"] == top["id"]
    # Round 3: the staff author of the parent joins the handler.
    assert sorted(_recipients(db)) == sorted([handler.email, staffer.email])
    assert _by_queue(enqueued, "respond_io") == []


def test_reply_to_an_unacceptable_parent_is_422_and_saves_nothing(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    other_form = _inquiry(db)
    staff = _user(db, "Other")
    top = _staff_says(db, "stock_inquiry", inquiry.id, staff, "top")
    nested = _staff_says(db, "stock_inquiry", inquiry.id, staff, "reply", parent_id=top["id"])
    foreign = _staff_says(db, "stock_inquiry", other_form.id, staff, "elsewhere")
    gone = _staff_says(db, "stock_inquiry", inquiry.id, staff, "to be deleted")
    _mark_deleted(db, gone["id"])
    staff_client.granted.add(INQ_VIEW)
    before = len(_comment_rows(db))
    enqueued.clear()

    for bad in (foreign["id"], nested["id"], gone["id"], "response", _uid()):
        resp = staff_client.post(_staff_url("stock_inquiry", inquiry.id), data={"body": "x", "parent_id": bad})
        assert resp.status_code == 422, (bad, resp.text)

    assert len(_comment_rows(db)) == before
    assert enqueued == []


# ---- edit --------------------------------------------------------------------------------


def _patch(client, url, body):
    return client.patch(url, json={"body": body})


def test_staff_author_edits_own_comment_without_any_notification(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    mine = _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "before")
    staff_client.granted.add(INQ_VIEW)
    enqueued.clear()

    resp = _patch(staff_client, _staff_url("stock_inquiry", inquiry.id, mine["id"]), "  after  ")

    assert resp.status_code == 200, resp.text
    edited = resp.json().get("comment", resp.json())
    assert edited["body"] == "after"
    assert edited["edited_at"]
    assert enqueued == []
    assert db.query(Notification).count() == 0
    assert _row(db, mine["id"]).edited_at is not None


def test_another_staff_users_comment_cannot_be_edited(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    theirs = _staff_says(db, "stock_inquiry", inquiry.id, _user(db, "Other"), "theirs")
    staff_client.granted.add(INQ_VIEW)

    resp = _patch(staff_client, _staff_url("stock_inquiry", inquiry.id, theirs["id"]), "hijack")

    assert resp.status_code == 403, resp.text
    assert _row(db, theirs["id"]).body_html == "theirs"


def test_a_crm_admin_can_edit_anyones_staff_comment(staff_client, db, enqueued, monkeypatch):
    inquiry = _inquiry(db)
    theirs = _staff_says(db, "stock_inquiry", inquiry.id, _user(db, "Other"), "theirs")
    staff_client.granted.add(INQ_VIEW)
    _roles(monkeypatch, admins=[staff_client.actor.id])

    resp = _patch(staff_client, _staff_url("stock_inquiry", inquiry.id, theirs["id"]), "moderated")

    assert resp.status_code == 200, resp.text
    assert _row(db, theirs["id"]).body_html == "moderated"


def test_blank_or_deleted_edit_is_422(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    mine = _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "keep")
    gone = _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "gone")
    _mark_deleted(db, gone["id"])
    staff_client.granted.add(INQ_VIEW)

    assert _patch(staff_client, _staff_url("stock_inquiry", inquiry.id, mine["id"]), "   ").status_code == 422
    assert _patch(staff_client, _staff_url("stock_inquiry", inquiry.id, mine["id"]), "x" * 4001).status_code == 422
    assert _patch(staff_client, _staff_url("stock_inquiry", inquiry.id, gone["id"]), "revive").status_code == 422
    assert _row(db, mine["id"]).body_html == "keep"


def test_staff_edit_needs_the_kinds_view_permission(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    mine = _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "mine")
    assert _patch(staff_client, _staff_url("stock_inquiry", inquiry.id, mine["id"]), "x").status_code == 403


def test_contact_edits_own_portal_comment(portal, db, enqueued):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    mine = _contact_says(db, "stock_inquiry", inquiry.id, owner, "before")
    enqueued.clear()

    resp = _patch(portal(owner), _portal_url("stock_inquiry", inquiry.id, mine["id"]), "after")

    assert resp.status_code == 200, resp.text
    assert _row(db, mine["id"]).body_html == "after"
    assert _row(db, mine["id"]).edited_at is not None
    assert enqueued == []


def test_contact_cannot_edit_a_staff_comment_and_a_stranger_gets_404(portal, db, enqueued):
    owner = _contact(db, "Owner")
    stranger = _contact(db, "Stranger")
    inquiry = _inquiry(db, contact_id=owner)
    staff_says = _staff_says(db, "stock_inquiry", inquiry.id, _user(db), "from staff")
    url = _portal_url("stock_inquiry", inquiry.id, staff_says["id"])

    assert _patch(portal(owner), url, "rewrite").status_code == 403
    assert _patch(portal(stranger), url, "rewrite").status_code == 404
    assert _row(db, staff_says["id"]).body_html == "from staff"


def test_impersonating_admin_edits_the_contacts_comment_through_the_portal(db, enqueued, monkeypatch):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    mine = _contact_says(db, "stock_inquiry", inquiry.id, owner, "before")
    _impersonation(db, monkeypatch, contact_id=owner)
    try:
        resp = _patch(TestClient(app, raise_server_exceptions=False), _portal_url("stock_inquiry", inquiry.id, mine["id"]), "fixed")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert _row(db, mine["id"]).body_html == "fixed"


# ---- delete ------------------------------------------------------------------------------


def _execute_delete(db, comment_id, requested_by, *, holds_view=True):
    """Run the commit-time executor. ``holds_view`` stands in for the requester holding the kind's
    view slug (the commit re-checks it); pass False for a requester who has lost it."""
    from unittest.mock import patch

    import app.services.record_actions  # noqa: F401  registers the actions
    from app.services.form_action_registry import get_action
    from app.services.user_service import UserPermissionService

    with patch.object(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: holds_view), patch.object(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: {INQ_VIEW} if holds_view else set()
    ):
        return get_action("form_comment.delete").execute(db, {"entity_id": comment_id, "requested_by_id": requested_by})


def test_form_comment_delete_is_a_registered_destructive_record_action():
    import app.services.record_actions  # noqa: F401
    from app.services.form_action_grace import WINDOW_DESTRUCTIVE
    from app.services.form_action_registry import get_action

    action = get_action("form_comment.delete")
    assert action is not None
    assert action.entity_types == ("form_comment",)
    assert action.window == WINDOW_DESTRUCTIVE


def test_delete_without_replies_removes_the_row_and_its_comment_links(db, enqueued, storage):
    from app.models.entity_attachment import EntityAttachmentLink

    staff = _user(db)
    inquiry = _inquiry(db)
    comment = _staff_says(db, "stock_inquiry", inquiry.id, staff, "bye", files=[PDF])
    att_id = comment["attachments"][0]["attachment_id"]
    enqueued.clear()

    _execute_delete(db, comment["id"], staff.id)

    assert _row(db, comment["id"]) is None
    remaining = {(r.entity_type, str(r.entity_id)) for r in db.query(EntityAttachmentLink).filter(EntityAttachmentLink.attachment_id == att_id)}
    assert ("form_comment", comment["id"]) not in remaining
    assert enqueued == []
    assert db.query(Notification).count() == 0


def test_delete_with_live_replies_keeps_a_placeholder(staff_client, db, enqueued, storage):
    staff = staff_client.actor
    inquiry = _inquiry(db)
    top = _staff_says(db, "stock_inquiry", inquiry.id, staff, "parent", files=[PDF])
    _staff_says(db, "stock_inquiry", inquiry.id, _user(db, "Replier"), "child", parent_id=top["id"])

    _execute_delete(db, top["id"], staff.id)

    assert _row(db, top["id"]).deleted_at is not None
    items = _as_staff_list(staff_client, "stock_inquiry", inquiry.id)
    placeholder = next(i for i in items if i["id"] == top["id"])
    assert placeholder["deleted"] is True and placeholder["body"] == "" and placeholder["attachments"] == []
    assert any(i["parent_id"] == top["id"] and i["body"] == "child" for i in items)


def test_delete_by_a_non_author_non_admin_is_403_and_an_admin_may(db, enqueued, monkeypatch):
    author = _user(db, "Author")
    intruder = _user(db, "Intruder")
    boss = _user(db, "Boss")
    inquiry = _inquiry(db)
    comment = _staff_says(db, "stock_inquiry", inquiry.id, author, "mine")
    _roles(monkeypatch, admins=[boss.id])

    with pytest.raises(HTTPException) as exc:
        _execute_delete(db, comment["id"], intruder.id)
    assert exc.value.status_code == 403
    assert _row(db, comment["id"]) is not None

    _execute_delete(db, comment["id"], boss.id)
    assert _row(db, comment["id"]) is None


def test_portal_delete_without_replies_removes_the_row(portal, db, enqueued):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    mine = _contact_says(db, "stock_inquiry", inquiry.id, owner, "bye")
    enqueued.clear()

    resp = portal(owner).delete(_portal_url("stock_inquiry", inquiry.id, mine["id"]))

    assert resp.status_code in (200, 204), resp.text
    assert _row(db, mine["id"]) is None
    assert enqueued == []


def test_portal_delete_with_replies_keeps_a_placeholder(portal, db, enqueued):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    mine = _contact_says(db, "stock_inquiry", inquiry.id, owner, "parent")
    _staff_says(db, "stock_inquiry", inquiry.id, _user(db), "child", parent_id=mine["id"])
    client = portal(owner)

    assert client.delete(_portal_url("stock_inquiry", inquiry.id, mine["id"])).status_code in (200, 204)

    items = client.get(_portal_url("stock_inquiry", inquiry.id)).json()["items"]
    placeholder = next(i for i in items if i["id"] == mine["id"])
    assert (placeholder["deleted"], placeholder["body"], placeholder["attachments"]) == (True, "", [])
    assert any(i["parent_id"] == mine["id"] for i in items)


def test_portal_cannot_delete_a_staff_comment_or_a_strangers_row(portal, db, enqueued):
    owner = _contact(db, "Owner")
    stranger = _contact(db, "Stranger")
    inquiry = _inquiry(db, contact_id=owner)
    theirs = _staff_says(db, "stock_inquiry", inquiry.id, _user(db), "staff words")
    url = _portal_url("stock_inquiry", inquiry.id, theirs["id"])

    assert portal(owner).delete(url).status_code == 403
    assert portal(stranger).delete(url).status_code == 404
    assert _row(db, theirs["id"]) is not None


# ---- can_edit / can_delete / can_reply per viewer -----------------------------------------


def _flags(item):
    return (item["can_edit"], item["can_delete"], item["can_reply"])


def test_staff_viewer_flags_own_versus_others_and_replies(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    mine = _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "mine")
    theirs = _staff_says(db, "stock_inquiry", inquiry.id, _user(db, "Other"), "theirs")
    reply = _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "reply", parent_id=mine["id"])
    gone = _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "gone")
    _mark_deleted(db, gone["id"])

    by_id = {i["id"]: i for i in _as_staff_list(staff_client, "stock_inquiry", inquiry.id)}

    assert _flags(by_id[mine["id"]]) == (True, True, True)
    assert _flags(by_id[theirs["id"]]) == (False, False, True)
    assert _flags(by_id[reply["id"]]) == (True, True, False)  # one level only
    assert by_id[gone["id"]]["can_reply"] is False
    assert by_id[gone["id"]]["can_edit"] is False


def test_admin_viewer_may_edit_and_delete_everything(staff_client, db, enqueued, monkeypatch):
    inquiry = _inquiry(db)
    theirs = _staff_says(db, "stock_inquiry", inquiry.id, _user(db, "Other"), "theirs")
    _roles(monkeypatch, admins=[staff_client.actor.id])

    by_id = {i["id"]: i for i in _as_staff_list(staff_client, "stock_inquiry", inquiry.id)}

    assert _flags(by_id[theirs["id"]]) == (True, True, True)


def test_portal_viewer_may_edit_and_delete_only_contact_authored_comments(portal, db, enqueued):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    mine = _contact_says(db, "stock_inquiry", inquiry.id, owner, "mine")
    staff_says = _staff_says(db, "stock_inquiry", inquiry.id, _user(db), "staff")

    by_id = {i["id"]: i for i in portal(owner).get(_portal_url("stock_inquiry", inquiry.id)).json()["items"]}

    assert _flags(by_id[mine["id"]]) == (True, True, True)
    assert _flags(by_id[staff_says["id"]]) == (False, False, True)


# --------------------------------------------------------------------------- #
# Migration fcom_0003_conv_thread is covered in tests/test_migration_fcom_0003_conv_thread.py
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Review round (red): delete gating, park check, impersonation on edit/delete,
# voided edit, reply date. Contract: PLAN-form-comments-09oct.md, "Owner hand test round 2".
# --------------------------------------------------------------------------- #


# ---- B1: the delete commit re-checks the form and the kind's view grant ---------------------


def test_b1_delete_commit_by_an_author_who_lost_the_view_slug_is_403_and_keeps_the_row(db, enqueued):
    author = _user(db, "Author")
    inquiry = _inquiry(db)
    comment = _staff_says(db, "stock_inquiry", inquiry.id, author, "mine")

    with pytest.raises(HTTPException) as exc:
        _execute_delete(db, comment["id"], author.id, holds_view=False)

    assert exc.value.status_code == 403
    assert _row(db, comment["id"]) is not None


def test_b1_delete_commit_for_a_comment_whose_form_cannot_be_loaded_is_refused_and_keeps_the_row(db, enqueued):
    # Forms carry no company_id in this schema, so "a form the requester's scope cannot load" is
    # seeded as a form row that is not there: the commit must load the form, not trust the comment.
    author = _user(db, "Author")
    inquiry = _inquiry(db)
    comment = _staff_says(db, "stock_inquiry", inquiry.id, author, "mine")
    db.execute(text("DELETE FROM stock_inquiries WHERE id = :i"), {"i": inquiry.id})
    db.commit()

    with pytest.raises(HTTPException) as exc:
        _execute_delete(db, comment["id"], author.id)

    assert exc.value.status_code in (403, 404)
    assert _row(db, comment["id"]) is not None


# ---- B2: park time refuses a delete the requester could never commit -----------------------


def test_b2_parking_a_delete_on_someone_elses_comment_is_404_and_does_not_block_the_author(staff_client, db, enqueued, monkeypatch):
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.models.sla import SlaFormAction
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(UserPermissionService, "get_user_permission_slugs", lambda self, uid: {INQ_VIEW})
    author = _user(db, "Author")
    inquiry = _inquiry(db)
    comment = _staff_says(db, "stock_inquiry", inquiry.id, author, "authors words")
    body = {"action_key": "form_comment.delete", "entity_type": "form_comment", "entity_id": comment["id"], "payload": {}}

    refused = staff_client.post("/api/v1/pending-actions", json=body)

    assert refused.status_code == 404, refused.text
    assert db.query(SlaFormAction).filter(SlaFormAction.source_entity_id == comment["id"]).count() == 0

    as_author = {"id": author.id, "email": author.email, "role": "staff"}
    app.dependency_overrides[get_current_user] = lambda: as_author
    app.dependency_overrides[get_current_user_or_api_key] = lambda: as_author
    parked = staff_client.post("/api/v1/pending-actions", json=body)
    assert parked.status_code == 202, parked.text


# ---- B3: impersonation is re-checked on edit and delete, as on post -------------------------


@pytest.mark.parametrize("verb", ["patch", "delete"])
@pytest.mark.parametrize("case", ["ended", "lost_role", "inactive", "trashed"])
def test_b3_impersonation_edit_and_delete_are_403_and_change_nothing_when_no_longer_allowed(db, enqueued, monkeypatch, case, verb):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    mine = _contact_says(db, "stock_inquiry", inquiry.id, owner, "before")
    _token, admin = _impersonation(
        db, monkeypatch, contact_id=owner, ended=(case == "ended"),
        admin_roles=frozenset() if case == "lost_role" else frozenset({"admin"}),
    )
    if case == "inactive":
        admin.status = "INACTIVE"
    elif case == "trashed":
        admin.is_trashed = True
    db.commit()
    url = _portal_url("stock_inquiry", inquiry.id, mine["id"])
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch(url, json={"body": "after"}) if verb == "patch" else client.delete(url)
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403, resp.text
    row = _row(db, mine["id"])
    assert row is not None
    assert row.body_html == "before" and row.edited_at is None and row.deleted_at is None


# ---- B4: edit is refused on a voided form, like post ---------------------------------------


def _void(db, form):
    form.status = "voided"
    db.commit()


def test_b4_staff_edit_on_a_voided_form_is_422_and_changes_nothing(staff_client, db, enqueued):
    inquiry = _inquiry(db)
    mine = _staff_says(db, "stock_inquiry", inquiry.id, staff_client.actor, "before")
    staff_client.granted.add(INQ_VIEW)
    _void(db, inquiry)

    resp = _patch(staff_client, _staff_url("stock_inquiry", inquiry.id, mine["id"]), "after")

    assert resp.status_code == 422, resp.text
    assert _row(db, mine["id"]).body_html == "before"
    assert _row(db, mine["id"]).edited_at is None


def test_b4_portal_edit_on_a_voided_form_is_422_and_changes_nothing(portal, db, enqueued):
    owner = _contact(db, "Owner")
    inquiry = _inquiry(db, contact_id=owner)
    mine = _contact_says(db, "stock_inquiry", inquiry.id, owner, "before")
    _void(db, inquiry)

    resp = _patch(portal(owner), _portal_url("stock_inquiry", inquiry.id, mine["id"]), "after")

    assert resp.status_code == 422, resp.text
    assert _row(db, mine["id"]).body_html == "before"
    assert _row(db, mine["id"]).edited_at is None


# ---- B5: a reply with no recorded time lists with a null created_at ------------------------


def test_b5_response_entry_without_last_responded_at_is_listed_with_null_created_at(staff_client, db, enqueued, view_denied):
    inquiry = _inquiry(db)
    inquiry.purchasing_response = "brand?"
    inquiry.last_responded_at = None
    db.commit()

    items = _as_staff_list(staff_client, "stock_inquiry", inquiry.id)

    first = items[0]
    assert first["kind"] == "response" and first["body"] == "brand?"
    assert first["created_at"] is None


# --------------------------------------------------------------------------- #
# Round 3: recipients union + team fallback + never silent
# (PLAN-form-comments-09oct.md, "Owner hand test round 3")
# Replaced two round-1 tests that pinned handler-only mail (last responder / earlier staff
# commenter must NOT be emailed): they contradict rule 1 and are rewritten below.
# --------------------------------------------------------------------------- #


def _mail_sets(db):
    """One entry per form_comment_notification: the set of recipient emails."""
    rows = db.query(Notification).filter(Notification.type == "form_comment_notification").all()
    return [set((n.data or {}).get("recipient_emails") or []) for n in rows]


def _prior_notification(db, kind, form_id, user, type_="sla_breach"):
    db.add(
        Notification(
            id=_uid(), user_id=user.id, type=type_, title=f"{MARKER} earlier",
            source_entity_type=kind, source_entity_id=form_id,
        )
    )
    db.commit()


def test_r1_recipients_are_handler_plus_staff_commenters_plus_responder_deduplicated(db, enqueued, view_denied):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    commenter = _user(db, "Commenter")
    responder = _user(db, "Responder", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    inquiry = _inquiry(db, contact_id=contact_id)
    _with_reply(db, inquiry, responder)
    _tracker(db, "stock_inquiry", inquiry.id, handler)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, commenter, "first")
    svc.add_staff_comment("stock_inquiry", inquiry.id, handler, "handler also comments")
    enqueued.clear()

    _, delivery = _unpack(svc.add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "thanks"))

    assert delivery == "queued"
    assert len(_by_queue(enqueued, "notifications")) == 1
    assert _mail_sets(db) == [{handler.email, commenter.email, responder.email}]
    notif = db.query(Notification).filter(Notification.type == "form_comment_notification").one()
    assert len(notif.data["recipient_emails"]) == 3  # handler is both handler and commenter: once


def test_r1_responder_resolves_by_users_id_too(db, enqueued, view_denied):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    responder = _user(db, "ById")
    inquiry = _inquiry(db, contact_id=contact_id)
    inquiry.purchasing_response = "brand?"
    inquiry.last_responded_by = responder.id
    inquiry.last_responded_at = datetime(2026, 10, 2, 8, 30, 0)
    db.commit()
    _tracker(db, "stock_inquiry", inquiry.id, handler)

    _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi")

    assert _mail_sets(db) == [{handler.email, responder.email}]


def test_r1_contact_authored_comments_do_not_add_their_user(db, enqueued, monkeypatch, view_denied):
    owner = _contact(db, "Owner")
    handler = _user(db, "Handler")
    inquiry = _inquiry(db, contact_id=owner)
    _tracker(db, "stock_inquiry", inquiry.id, handler)
    admin = _user(db, "Impersonator")
    svc = _svc(db)
    # a plain contact comment, and one posted by an impersonating admin (both author ids set)
    svc.add_contact_comment("stock_inquiry", inquiry.id, owner, SPACE, "plain")
    svc.add_contact_comment("stock_inquiry", inquiry.id, owner, SPACE, "on behalf", acting_user_id=admin.id)
    row = [r for r in _comment_rows(db) if r.author_user_id == admin.id][0]
    assert row.author_contact_id == owner
    db.query(Notification).delete()
    db.commit()
    enqueued.clear()

    svc.add_contact_comment("stock_inquiry", inquiry.id, owner, SPACE, "again")

    assert _mail_sets(db) == [{handler.email}]


def test_r1_inactive_and_blank_email_staff_are_excluded(db, enqueued, view_denied):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    gone = _user(db, "Gone")
    mute = _user(db, "Mute")
    inquiry = _inquiry(db, contact_id=contact_id)
    _tracker(db, "stock_inquiry", inquiry.id, handler)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, gone, "a")
    svc.add_staff_comment("stock_inquiry", inquiry.id, mute, "b")
    gone.status = "INACTIVE"
    mute.email = "   "
    db.commit()
    enqueued.clear()

    svc.add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi")

    assert _mail_sets(db) == [{handler.email}]


def test_r1_acting_impersonating_admin_is_excluded_even_as_staff_commenter(db, enqueued, view_denied):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    admin = _user(db, "Admin")
    inquiry = _inquiry(db, contact_id=contact_id)
    _tracker(db, "stock_inquiry", inquiry.id, handler)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, admin, "earlier as staff")
    enqueued.clear()

    svc.add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "on behalf", acting_user_id=admin.id)

    assert _mail_sets(db) == [{handler.email}]


def test_r2_no_handler_still_emails_staff_commenters_and_responder(db, enqueued, view_denied):
    contact_id = _contact(db)
    commenter = _user(db, "Commenter")
    responder = _user(db, "Responder", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    inquiry = _inquiry(db, contact_id=contact_id)
    _with_reply(db, inquiry, responder)
    _tracker(db, "stock_inquiry", inquiry.id, None)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, commenter, "first")
    enqueued.clear()

    _, delivery = _unpack(svc.add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi"))

    assert delivery == "queued"
    assert _mail_sets(db) == [{commenter.email, responder.email}]
    assert len(_by_queue(enqueued, "notifications")) == 1


def test_r3_empty_set_falls_back_to_users_who_got_this_forms_earlier_notifications(db, enqueued, view_denied):
    contact_id = _contact(db)
    a, b, other_form_user, inactive = _user(db, "A"), _user(db, "B"), _user(db, "Elsewhere"), _user(db, "Inactive")
    inquiry = _inquiry(db, contact_id=contact_id)
    other = _inquiry(db)
    _prior_notification(db, "stock_inquiry", inquiry.id, a)
    _prior_notification(db, "stock_inquiry", inquiry.id, b)
    _prior_notification(db, "stock_inquiry", inquiry.id, a, type_="another_type")  # a once
    _prior_notification(db, "stock_inquiry", inquiry.id, inactive)
    _prior_notification(db, "stock_inquiry", other.id, other_form_user)
    inactive.status = "INACTIVE"
    db.commit()

    _, delivery = _unpack(_svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "anyone?"))

    assert delivery == "queued"
    assert _mail_sets(db) == [{a.email, b.email}]
    assert len(_by_queue(enqueued, "notifications")) == 1


def test_r3_earlier_form_comment_notifications_do_not_feed_the_fallback(db, enqueued):
    contact_id = _contact(db)
    a = _user(db, "A")
    inquiry = _inquiry(db, contact_id=contact_id)
    _prior_notification(db, "stock_inquiry", inquiry.id, a, type_="form_comment_notification")

    _, delivery = _unpack(_svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "anyone?"))

    assert delivery == "no_recipients"
    assert _by_queue(enqueued, "notifications") == []


def test_r4_nothing_at_all_is_no_recipients_with_a_warning_naming_the_form(db, enqueued, caplog):
    import logging

    contact_id = _contact(db)
    inquiry = _inquiry(db, contact_id=contact_id)

    with caplog.at_level(logging.DEBUG):
        _, delivery = _unpack(_svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hello?"))

    assert delivery == "no_recipients"
    assert enqueued == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and str(inquiry.id) in r.getMessage()]
    assert warnings, [r.getMessage() for r in caplog.records]


def test_r3_sponsorship_fallback_reads_purchase_request_typed_notifications(db, enqueued, view_denied):
    contact_id = _contact(db)
    team = _user(db, "Team")
    stranger = _user(db, "Stranger")
    form = _request(db, request_type="sponsorship_form", contact_id=contact_id)
    other = _request(db, request_type="sponsorship_form")
    _prior_notification(db, "purchase_request", form.id, team)
    _prior_notification(db, "purchase_request", other.id, stranger)  # guard: different id

    _, delivery = _unpack(_svc(db).add_contact_comment("sponsorship_form", form.id, contact_id, SPACE, "anyone?"))

    assert delivery == "queued"
    assert _mail_sets(db) == [{team.email}]


def test_r3_sponsorship_fallback_ignores_other_ids_purchase_request_notifications(db, enqueued):
    contact_id = _contact(db)
    stranger = _user(db, "Stranger")
    form = _request(db, request_type="sponsorship_form", contact_id=contact_id)
    other = _request(db, request_type="sponsorship_form")
    _prior_notification(db, "purchase_request", other.id, stranger)

    _, delivery = _unpack(_svc(db).add_contact_comment("sponsorship_form", form.id, contact_id, SPACE, "anyone?"))

    assert delivery == "no_recipients"
    assert _by_queue(enqueued, "notifications") == []


# --------------------------------------------------------------------------- #
# Round 3 security: recipients must still be able to view the form (S1), and the
# responder resolves by respond_user_id first (S2).
# --------------------------------------------------------------------------- #


@pytest.fixture
def view_denied(monkeypatch):
    """Permission stand-in for the recipient tests: every user holds every view slug except the
    ids put in the returned set (their role no longer grants it). Admin status is untouched
    (``get_user_role_slugs``), so an admin id stays allowed whatever is in the set. Tests that
    install their own patches later simply override this one."""
    from app.services.user_service import UserPermissionService
    from app.services.form_comment_service import VIEW_PERMISSION

    denied: set[str] = set()
    all_slugs = set(VIEW_PERMISSION.values())
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: str(uid) not in denied
    )
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_permission_slugs",
        lambda self, uid: set() if str(uid) in denied else set(all_slugs),
    )
    return denied


def test_s1_recipients_without_the_kinds_view_slug_are_not_emailed(db, enqueued, view_denied):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    lost = _user(db, "LostAccess")
    kept = _user(db, "Kept")
    responder = _user(db, "Responder", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    inquiry = _inquiry(db, contact_id=contact_id)
    _with_reply(db, inquiry, responder)
    _tracker(db, "stock_inquiry", inquiry.id, handler)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, lost, "a")
    svc.add_staff_comment("stock_inquiry", inquiry.id, kept, "b")
    view_denied.update({handler.id, lost.id, responder.id})
    enqueued.clear()

    _, delivery = _unpack(svc.add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi"))

    assert delivery == "queued"
    assert _mail_sets(db) == [{kept.email}]


def test_s1_an_admin_without_the_slug_is_still_emailed(db, enqueued, view_denied, monkeypatch):
    contact_id = _contact(db)
    admin = _user(db, "AdminCommenter")
    inquiry = _inquiry(db, contact_id=contact_id)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, admin, "a")
    view_denied.add(admin.id)
    _roles(monkeypatch, admins=[admin.id])
    enqueued.clear()

    svc.add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi")

    assert _mail_sets(db) == [{admin.email}]


def test_s1_the_fallback_skips_notified_users_without_the_slug(db, enqueued, view_denied):
    contact_id = _contact(db)
    gone, kept = _user(db, "Gone"), _user(db, "Kept")
    inquiry = _inquiry(db, contact_id=contact_id)
    _prior_notification(db, "stock_inquiry", inquiry.id, gone)
    _prior_notification(db, "stock_inquiry", inquiry.id, kept)
    view_denied.add(gone.id)

    _, delivery = _unpack(_svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi"))

    assert delivery == "queued"
    assert _mail_sets(db) == [{kept.email}]


def test_s1_a_fallback_of_only_unauthorised_users_is_no_recipients_with_a_warning(db, enqueued, view_denied, caplog):
    import logging

    contact_id = _contact(db)
    gone = _user(db, "Gone")
    inquiry = _inquiry(db, contact_id=contact_id)
    _prior_notification(db, "stock_inquiry", inquiry.id, gone)
    view_denied.add(gone.id)

    with caplog.at_level(logging.DEBUG):
        _, delivery = _unpack(_svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi"))

    assert delivery == "no_recipients"
    assert _by_queue(enqueued, "notifications") == []
    assert [r for r in caplog.records if r.levelno == logging.WARNING and str(inquiry.id) in r.getMessage()]


def test_s2_responder_matches_respond_user_id_before_a_users_id_collision(db, enqueued, view_denied):
    contact_id = _contact(db)
    a = _user(db, "ByRespondId", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    b = _user(db, "ById")
    inquiry = _inquiry(db, contact_id=contact_id)
    # B's users.id equals the stored responder value, A's respond_user_id does too: A wins.
    a.respond_user_id = b.id
    db.commit()
    inquiry.purchasing_response = "brand?"
    inquiry.last_responded_by = b.id
    inquiry.last_responded_at = datetime(2026, 10, 2, 8, 30, 0)
    db.commit()

    _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hi")

    assert _mail_sets(db) == [{a.email}]
