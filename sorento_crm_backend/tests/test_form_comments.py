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
SERIALIZED_KEYS = {"id", "body", "author_name", "author_kind", "created_at", "attachments"}
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


def test_staff_add_without_inbox_url_saves_and_reports_no_whatsapp(db, enqueued):
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


def test_contact_add_emails_only_the_sla_handler_and_never_whatsapps(db, enqueued):
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


def test_contact_add_does_not_email_the_last_responder_who_is_not_the_handler(db, enqueued):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    responder = _user(db, "Responder", responder_id=f"resp-{uuid.uuid4().hex[:8]}")
    inquiry = _inquiry(db, contact_id=contact_id, last_responded_by=responder.respond_user_id)
    _tracker(db, "stock_inquiry", inquiry.id, handler)

    _svc(db).add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "hello")

    recipients = [e for emails in _emails_of_notifications(db) for e in emails]
    assert recipients == [handler.email]
    assert responder.email not in recipients


def test_contact_add_does_not_email_an_earlier_staff_commenter_who_is_not_the_handler(db, enqueued):
    contact_id = _contact(db)
    handler = _user(db, "Handler")
    commenter = _user(db, "Earlier")
    inquiry = _inquiry(db, contact_id=contact_id)
    _tracker(db, "stock_inquiry", inquiry.id, handler)
    svc = _svc(db)
    svc.add_staff_comment("stock_inquiry", inquiry.id, commenter, "first")
    enqueued.clear()

    svc.add_contact_comment("stock_inquiry", inquiry.id, contact_id, SPACE, "thanks")

    recipients = [e for emails in _emails_of_notifications(db) for e in emails]
    assert recipients == [handler.email]
    assert len(_by_queue(enqueued, "notifications")) == 1


def test_contact_add_with_no_tracker_reports_no_recipients(db, enqueued):
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


def test_staff_comment_with_a_pdf_links_the_file_to_the_form_and_the_comment(db, enqueued, storage):
    staff = _user(db)
    inquiry = _inquiry(db)

    comment, _ = _unpack(_svc(db).add_staff_comment("stock_inquiry", inquiry.id, staff, "see attached", files=[PDF]))

    assert len(comment["attachments"]) == 1
    att = comment["attachments"][0]
    assert set(att) == ATTACHMENT_KEYS
    assert (att["filename"], att["size"], att["content_type"]) == (PDF[0], len(PDF[1]), PDF[2])
    assert _links(db, att["attachment_id"]) == {("stock_inquiry", inquiry.id), ("form_comment", comment["id"])}
    assert len(storage.uploads) == 1


def test_contact_comment_with_a_file_is_a_contact_upload_with_two_links(db, enqueued, storage):
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
    assert _links(db, att["attachment_id"]) == {("stock_inquiry", inquiry.id), ("form_comment", comment["id"])}


def test_sponsorship_comment_file_links_the_form_side_as_purchase_request(db, enqueued, storage):
    staff = _user(db)
    row = _request(db, request_type="sponsorship_form")

    comment, _ = _unpack(_svc(db).add_staff_comment("sponsorship_form", row.id, staff, "sponsor file", files=[PDF]))

    att = comment["attachments"][0]
    assert _links(db, att["attachment_id"]) == {("purchase_request", row.id), ("form_comment", comment["id"])}


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


def test_portal_download_of_a_comment_file_works_for_the_owner_and_404s_for_others(portal, db, storage, monkeypatch):
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
