"""HARD BLOCKER (UAC-response-attachments.md F2) + attribution (item 9).

A staff-uploaded (``uploader_kind='user'``) attachment can never be unlinked
from the portal, even by the contact who owns the submission, even with a
valid token -- the FE hides the control, but this asserts the server-side
403 that is the real contract (F2). The contact's OWN upload still deletes
(F3). Also covers portal-upload attribution stamping (B1) and legacy NULL
uploader_kind rendering (B5/J4 no-regression).
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# MUST be first app import - resolves circular-import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.access import RespondContact
from app.models.entity_attachment import EntityAttachmentLink
from app.models.portal import PortalToken
from app.models.procurement import StockInquiry
from app.models.resources import Attachment
from tests._pg_fixture import blank_session


class _FakeStorageBackend:
    def upload_file(self, *args, **kwargs):
        key = args[1] if len(args) > 1 else kwargs.get("file_path")
        return key, f"https://cdn.test/{key}"


def _seed_portal_attachment_type(db: Session) -> None:
    from app.models.resources import AttachmentType
    from app.services.portal_service import PORTAL_ATTACHMENT_TYPE_CODE

    db.add(
        AttachmentType(
            id=str(uuid.uuid4()),
            code=PORTAL_ATTACHMENT_TYPE_CODE,
            type_name="Portal Submission",
            allowed_extensions="jpg,jpeg,png,pdf",
            max_file_size_mb=10,
        )
    )
    db.commit()


@pytest.fixture
def client(monkeypatch):
    from app.database import get_db
    import app.services.storage_router as storage_router

    with blank_session() as db:
        _seed_portal_attachment_type(db)
        fake_backend = _FakeStorageBackend()
        monkeypatch.setattr(storage_router, "default_provider", lambda: "s3")
        monkeypatch.setattr(storage_router, "get_backend", lambda provider: fake_backend)
        monkeypatch.setattr(
            storage_router, "cdn_base_url", lambda provider, key: f"https://cdn.test/{key}"
        )

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        try:
            with TestClient(app) as c:
                yield c, db
        finally:
            app.dependency_overrides.clear()


def _contact(db: Session, *, name="Contact") -> RespondContact:
    c = RespondContact(
        id=str(uuid.uuid4()), phone_number=f"+6011{uuid.uuid4().hex[:8]}", name=name
    )
    db.add(c)
    db.flush()
    return c


def _token(db: Session, contact: RespondContact, space_id: str) -> PortalToken:
    t = PortalToken(
        id=str(uuid.uuid4()),
        token=f"tok-{uuid.uuid4().hex}",
        contact_id=contact.id,
        space_id=space_id,
        expires_at=datetime.utcnow() + timedelta(days=30),
        verified_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(t)
    db.commit()
    return t


def _inquiry(db: Session, contact: RespondContact, space_id: str) -> StockInquiry:
    row = StockInquiry(
        id=str(uuid.uuid4()),
        inquiry_number="PORTAL-LOCK-1",
        status="pending_purchasing",
        contact_id=contact.id,
        space_id=space_id,
    )
    db.add(row)
    db.commit()
    return row


def _link_attachment(
    db: Session, entity_id: str, *, uploader_kind, uploaded_by=None, uploaded_by_contact_id=None
) -> str:
    att = Attachment(
        id=str(uuid.uuid4()),
        original_filename="f.jpg",
        stored_filename="f.jpg",
        file_path="https://cdn.test/f.jpg",
        uploaded_by=uploaded_by,
        uploaded_by_contact_id=uploaded_by_contact_id,
        uploader_kind=uploader_kind,
    )
    db.add(att)
    db.flush()
    link = EntityAttachmentLink(
        entity_type="stock_inquiry", entity_id=entity_id, attachment_id=att.id
    )
    db.add(link)
    db.commit()
    return str(link.id)


# --------------------------------------------------------------------------
# F2 hard blocker
# --------------------------------------------------------------------------


def test_portal_delete_of_staff_uploaded_attachment_is_403(client):
    c, db = client
    contact = _contact(db)
    token = _token(db, contact, "space-1")
    inquiry = _inquiry(db, contact, "space-1")
    staff_link_id = _link_attachment(
        db, str(inquiry.id), uploader_kind="user", uploaded_by=str(uuid.uuid4())
    )

    res = c.delete(
        f"/api/v1/public/portal/attachments/{staff_link_id}",
        headers={"X-Portal-Token": token.token},
    )
    assert res.status_code == 403, res.text
    body = res.json()
    detail = body.get("detail") or body
    assert "STAFF_UPLOAD_LOCKED" in str(detail) or "cannot be removed" in str(detail).lower()

    # The row must still exist - rejected, not silently no-op deleted.
    assert (
        db.query(EntityAttachmentLink)
        .filter(EntityAttachmentLink.id == staff_link_id)
        .first()
        is not None
    )


def test_portal_delete_of_own_contact_upload_still_works(client):
    c, db = client
    contact = _contact(db)
    token = _token(db, contact, "space-1")
    inquiry = _inquiry(db, contact, "space-1")
    own_link_id = _link_attachment(
        db, str(inquiry.id), uploader_kind="contact", uploaded_by_contact_id=contact.id
    )

    res = c.delete(
        f"/api/v1/public/portal/attachments/{own_link_id}",
        headers={"X-Portal-Token": token.token},
    )
    assert res.status_code == 200, res.text
    assert (
        db.query(EntityAttachmentLink).filter(EntityAttachmentLink.id == own_link_id).first()
        is None
    )


def test_portal_delete_requires_a_valid_token(client):
    c, db = client
    contact = _contact(db)
    inquiry = _inquiry(db, contact, "space-1")
    link_id = _link_attachment(
        db, str(inquiry.id), uploader_kind="contact", uploaded_by_contact_id=contact.id
    )

    res = c.delete(f"/api/v1/public/portal/attachments/{link_id}")
    assert res.status_code == 401


# --------------------------------------------------------------------------
# Attribution (B1/B4/B5/J4)
# --------------------------------------------------------------------------


def test_portal_upload_stamps_contact_attribution(client):
    c, db = client
    contact = _contact(db, name="CONTACT X Salesman")
    token = _token(db, contact, "space-1")
    inquiry = _inquiry(db, contact, "space-1")

    res = c.post(
        "/api/v1/public/portal/attachments",
        data={"kind": "stock_inquiry", "submission_id": str(inquiry.id)},
        files={"file": ("photo.jpg", io.BytesIO(b"bytes"), "image/jpeg")},
        headers={"X-Portal-Token": token.token},
    )
    assert res.status_code == 200, res.text
    attachment_id = res.json()["attachment_id"]

    att = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    assert att is not None
    assert att.uploader_kind == "contact"
    assert att.uploaded_by_contact_id == contact.id


def test_attachment_list_shows_uploader_attribution_and_can_unlink(client):
    c, db = client
    from app.models.user import User

    contact = _contact(db, name="CONTACT X Salesman")
    token = _token(db, contact, "space-1")
    inquiry = _inquiry(db, contact, "space-1")

    staff_user = User(
        id=str(uuid.uuid4()), email="purchasing@test.com", name="Purchasing Staff", status="ACTIVE"
    )
    db.add(staff_user)
    db.flush()

    staff = _link_attachment(
        db, str(inquiry.id), uploader_kind="user", uploaded_by=staff_user.id
    )
    own = _link_attachment(
        db, str(inquiry.id), uploader_kind="contact", uploaded_by_contact_id=contact.id
    )
    legacy = _link_attachment(db, str(inquiry.id), uploader_kind=None)

    res = c.get(
        "/api/v1/public/portal/attachments",
        params={"kind": "stock_inquiry", "submission_id": str(inquiry.id)},
        headers={"X-Portal-Token": token.token},
    )
    assert res.status_code == 200, res.text
    items = {i["link_id"]: i for i in res.json()["items"]}

    assert items[staff]["uploader_kind"] == "user"
    assert items[staff]["can_unlink"] is False
    assert items[staff]["uploaded_by_name"] == "Purchasing Staff"
    assert items[staff]["uploaded_by_role"] == "staff"

    assert items[own]["uploader_kind"] == "contact"
    assert items[own]["can_unlink"] is True
    assert items[own]["uploaded_by_name"] == "CONTACT X Salesman"
    assert items[own]["uploaded_by_role"] == "contact"

    # Legacy row with NULL uploader_kind: no-regression, still renders and is
    # still unlinkable (uploader_kind != 'user') per B5/J4.
    assert items[legacy]["uploader_kind"] is None
    assert items[legacy]["uploaded_by_name"] == "Unknown"
    assert items[legacy]["uploaded_by_role"] == "unknown"
    assert items[legacy]["can_unlink"] is True
