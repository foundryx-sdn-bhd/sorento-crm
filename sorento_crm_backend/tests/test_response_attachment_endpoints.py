"""HTTP-level tests for the staff response-attachment routes (UAC-response-attachments.md
groups C/F4): POST/DELETE /api/v1/procurement/stock-inquiries/{id}/response-attachments
and /api/v1/complaints-management/complaints/{id}/response-attachments.

Covers: happy-path upload stamps uploader_kind='user' + uploaded_by (item 9 of the
test brief, "internal create"); 401 auth denial; DELETE hard-unlinks (F4).

Storage is faked (no real S3/R2 network) per the mock-every-external-call rule.
"""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# MUST be first app import - resolves circular-import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests._pg_fixture import blank_session

_USER_ID = "2f6a9c9a-1c2e-5c1e-9a5c-9a5c9a5c9a5c"


def _seed_response_attachment_type(db: Session) -> None:
    from app.models.resources import AttachmentType

    db.add(
        AttachmentType(
            id=str(uuid.uuid4()),
            code="response_attachment",
            type_name="Response Attachment",
            allowed_extensions="jpg,jpeg,png,pdf,xlsx",
            max_file_size_mb=10,
        )
    )
    db.commit()


class _FakeStorageBackend:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str | None]] = []

    def upload_file(self, *args, **kwargs):
        # entity_attachment_service calls positionally (contents, key, content_type=...);
        # image_thumbnailer calls with keywords (file_content=, file_path=, content_type=).
        if args:
            content, key = args[0], args[1]
            content_type = kwargs.get("content_type")
        else:
            content, key, content_type = (
                kwargs.get("file_content"),
                kwargs.get("file_path"),
                kwargs.get("content_type"),
            )
        self.uploads.append((key, content, content_type))
        return key, f"https://cdn.test/{key}"


@pytest.fixture
def client(monkeypatch):
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    import app.services.storage_router as storage_router

    with blank_session() as db:
        _seed_response_attachment_type(db)

        fake_backend = _FakeStorageBackend()
        monkeypatch.setattr(storage_router, "default_provider", lambda: "s3")
        monkeypatch.setattr(storage_router, "get_backend", lambda provider: fake_backend)
        monkeypatch.setattr(
            storage_router, "cdn_base_url", lambda provider, key: f"https://cdn.test/{key}"
        )

        def _override_get_db():
            yield db

        def _override_current_user():
            return {"id": _USER_ID, "email": "staff@test.com"}

        # LIST-API-PERMS: GET /complaints/{id} carries a permission gate now; this principal
        # has no users row, so answer the role lookup as admin (the gate has its own tests).
        from app.services.user_service import UserPermissionService

        monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_current_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_current_user

        try:
            with TestClient(app) as c:
                yield c, db
        finally:
            app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Stock inquiry
# --------------------------------------------------------------------------


def _mk_inquiry(db: Session) -> str:
    from app.models.procurement import StockInquiry

    row = StockInquiry(id=str(uuid.uuid4()), inquiry_number="RESP-SI-1", status="pending_purchasing")
    db.add(row)
    db.commit()
    return str(row.id)


def test_stock_inquiry_response_attachment_upload_stamps_user(client):
    c, db = client
    inquiry_id = _mk_inquiry(db)

    res = c.post(
        f"/api/v1/procurement/stock-inquiries/{inquiry_id}/response-attachments",
        files={"file": ("photo.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["filename"] == "photo.jpg"
    assert body["attachment_id"]

    from app.models.resources import Attachment

    att = db.query(Attachment).filter(Attachment.id == body["attachment_id"]).first()
    assert att is not None
    assert att.uploader_kind == "user"
    assert att.uploaded_by == _USER_ID


def test_stock_inquiry_response_attachment_upload_requires_auth(client):
    c, db = client
    inquiry_id = _mk_inquiry(db)
    from app.dependencies import get_current_user, get_current_user_or_api_key

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_user_or_api_key, None)
    try:
        res = c.post(
            f"/api/v1/procurement/stock-inquiries/{inquiry_id}/response-attachments",
            files={"file": ("photo.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")},
        )
        assert res.status_code == 401
    finally:
        from app.dependencies import get_db

        def _override_current_user():
            return {"id": _USER_ID, "email": "staff@test.com"}

        app.dependency_overrides[get_current_user] = _override_current_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_current_user


def test_stock_inquiry_response_attachment_delete_unlinks(client):
    c, db = client
    inquiry_id = _mk_inquiry(db)

    res = c.post(
        f"/api/v1/procurement/stock-inquiries/{inquiry_id}/response-attachments",
        files={"file": ("photo.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")},
    )
    link_id = res.json()["link_id"]

    del_res = c.delete(f"/api/v1/procurement/stock-inquiries/response-attachments/{link_id}")
    assert del_res.status_code == 200, del_res.text

    from app.models.entity_attachment import EntityAttachmentLink

    assert db.query(EntityAttachmentLink).filter(EntityAttachmentLink.id == link_id).first() is None


# --------------------------------------------------------------------------
# Complaint
# --------------------------------------------------------------------------


def _mk_complaint(db: Session) -> str:
    from app.models.complaints import Complaint

    c = Complaint(id=str(uuid.uuid4()), complaint_number="RESP-CX-1", status="new")
    db.add(c)
    db.commit()
    return str(c.id)


def test_complaint_response_attachment_upload_stamps_user(client):
    c, db = client
    complaint_id = _mk_complaint(db)

    res = c.post(
        f"/api/v1/complaints-management/complaints/{complaint_id}/response-attachments",
        files={"file": ("report.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")},
    )
    assert res.status_code == 201, res.text
    body = res.json()

    from app.models.resources import Attachment

    att = db.query(Attachment).filter(Attachment.id == body["attachment_id"]).first()
    assert att is not None
    assert att.uploader_kind == "user"
    assert att.uploaded_by == _USER_ID


def test_complaint_response_attachment_upload_requires_auth(client):
    c, db = client
    complaint_id = _mk_complaint(db)
    from app.dependencies import get_current_user, get_current_user_or_api_key

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_user_or_api_key, None)
    try:
        res = c.post(
            f"/api/v1/complaints-management/complaints/{complaint_id}/response-attachments",
            files={"file": ("report.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")},
        )
        assert res.status_code == 401
    finally:
        def _override_current_user():
            return {"id": _USER_ID, "email": "staff@test.com"}

        app.dependency_overrides[get_current_user] = _override_current_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_current_user


def test_complaint_response_attachment_delete_unlinks(client):
    c, db = client
    complaint_id = _mk_complaint(db)

    res = c.post(
        f"/api/v1/complaints-management/complaints/{complaint_id}/response-attachments",
        files={"file": ("report.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")},
    )
    link_id = res.json()["link_id"]

    del_res = c.delete(f"/api/v1/complaints-management/complaints/response-attachments/{link_id}")
    assert del_res.status_code == 200, del_res.text

    from app.models.entity_attachment import EntityAttachmentLink

    assert db.query(EntityAttachmentLink).filter(EntityAttachmentLink.id == link_id).first() is None


# --------------------------------------------------------------------------
# Attribution must survive the response_model (UAC B2/B5)
#
# Both regressions were invisible to the service-level tests: serialize_link
# computed the attribution correctly, but the route's Pydantic response_model
# listed neither the attribution fields nor a link_type the FE could act on, so
# the GET dropped them and the panel rendered "Unknown" with the unlink routed
# to the wrong endpoint.
# --------------------------------------------------------------------------


def _seed_user(db: Session) -> None:
    from app.models.user import User

    if db.query(User).filter(User.id == _USER_ID).first():
        return
    db.add(User(id=_USER_ID, email="staff@test.com", name="Staff Member"))
    db.commit()


def test_complaint_get_exposes_uploader_attribution(client):
    c, db = client
    _seed_user(db)
    complaint_id = _mk_complaint(db)

    c.post(
        f"/api/v1/complaints-management/complaints/{complaint_id}/response-attachments",
        files={"file": ("report.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")},
    )

    res = c.get(f"/api/v1/complaints-management/complaints/{complaint_id}")
    assert res.status_code == 200, res.text
    attachments = res.json()["attachments"]
    assert len(attachments) == 1
    row = attachments[0]
    assert row["uploader_kind"] == "user"
    assert row["uploaded_by_name"] == "Staff Member"
    assert row["uploaded_by_role"] == "staff"
    # A staff upload is never unlinkable from a contact-facing surface.
    assert row["can_unlink"] is False
    # The FE routes the unlink by link_type, so a staff response upload must not
    # be labelled with the generic per-entity link type.
    assert row["link_type"] == "response_attachment"


def test_stock_inquiry_get_exposes_uploader_attribution(client):
    c, db = client
    _seed_user(db)
    inquiry_id = _mk_inquiry(db)

    c.post(
        f"/api/v1/procurement/stock-inquiries/{inquiry_id}/response-attachments",
        files={"file": ("photo.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")},
    )

    res = c.get(f"/api/v1/procurement/stock-inquiries/{inquiry_id}")
    assert res.status_code == 200, res.text
    attachments = res.json()["attachments"]
    assert len(attachments) == 1
    row = attachments[0]
    assert row["uploader_kind"] == "user"
    assert row["uploaded_by_name"] == "Staff Member"
    assert row["uploaded_by_role"] == "staff"
    assert row["can_unlink"] is False
    assert row["link_type"] == "response_attachment"
