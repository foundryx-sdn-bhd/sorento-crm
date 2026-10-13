"""AC-A6: the attachments list can be filtered to PDFs (and any other mime).

Backs `PLAN-flyer-read-hardening.md`'s picker gap: the file library had a
document-CLASS filter (`attachment_type_id`) but nothing that asked "what is
this file's own type", which is what a picker restricted to PDFs needs.

Mirrors the attachment list tests' pattern for this exact
resource: seed rows with a unique `original_filename` prefix against the live
Postgres test DB, assert, clean up by prefix. This endpoint has MANY callers,
so the load-bearing assertion here is as much "the unfiltered call is
unchanged" as it is "the filter works".
"""
from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.main import app
from app.models.resources import Attachment
from app.services.resources_service import AttachmentService

# Unique marker so query=/prefix filters match ONLY this test's rows.
PREFIX = "ZZTMIME-"


@pytest.fixture(autouse=True)
def _clean_state():
    with engine.connect() as conn:
        try:
            conn.execute(text("DELETE FROM attachments WHERE original_filename LIKE 'ZZTMIME-%'"))
            conn.commit()
        except Exception:
            conn.rollback()
    yield
    with engine.connect() as conn:
        try:
            conn.execute(text("DELETE FROM attachments WHERE original_filename LIKE 'ZZTMIME-%'"))
            conn.commit()
        except Exception:
            conn.rollback()


@pytest.fixture
def db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed(db: Session, original_filename: str, *, mime_type: str | None) -> Attachment:
    a = Attachment(
        id=str(uuid.uuid4()),
        original_filename=original_filename,
        stored_filename=original_filename,
        file_path=f"general/{original_filename}",
        mime_type=mime_type,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# --------------------------------------------------------------------------- #
# Service-level: AttachmentService.list_attachments
# --------------------------------------------------------------------------- #
def test_mime_type_filters_to_that_type_only(db: Session) -> None:
    pdf_a = _seed(db, f"{PREFIX}a.pdf", mime_type="application/pdf")
    pdf_b = _seed(db, f"{PREFIX}b.pdf", mime_type="application/pdf")
    _seed(db, f"{PREFIX}c.png", mime_type="image/png")

    svc = AttachmentService(db)
    result = svc.list_attachments(page=1, limit=50, query=PREFIX, mime_type="application/pdf")

    ids = {row.id for row in result["data"]}
    assert ids == {pdf_a.id, pdf_b.id}


def test_mime_types_unions_with_mime_type(db: Session) -> None:
    pdf = _seed(db, f"{PREFIX}u1.pdf", mime_type="application/pdf")
    png = _seed(db, f"{PREFIX}u2.png", mime_type="image/png")
    _seed(db, f"{PREFIX}u3.docx", mime_type="application/msword")

    svc = AttachmentService(db)
    result = svc.list_attachments(
        page=1,
        limit=50,
        query=PREFIX,
        mime_type="application/pdf",
        mime_types=["image/png"],
    )

    ids = {row.id for row in result["data"]}
    assert ids == {pdf.id, png.id}


def test_charset_suffix_on_the_stored_value_still_matches(db: Session) -> None:
    row = _seed(db, f"{PREFIX}charset-stored.pdf", mime_type="application/pdf; charset=binary")

    svc = AttachmentService(db)
    result = svc.list_attachments(page=1, limit=50, query=PREFIX, mime_type="application/pdf")

    assert {r.id for r in result["data"]} == {row.id}


def test_charset_suffix_on_the_filter_value_still_matches(db: Session) -> None:
    row = _seed(db, f"{PREFIX}charset-filter.pdf", mime_type="application/pdf")

    svc = AttachmentService(db)
    result = svc.list_attachments(
        page=1, limit=50, query=PREFIX, mime_type="application/pdf; charset=utf-8"
    )

    assert {r.id for r in result["data"]} == {row.id}


def test_mime_filter_is_case_insensitive(db: Session) -> None:
    row = _seed(db, f"{PREFIX}case.pdf", mime_type="application/pdf")

    svc = AttachmentService(db)
    result = svc.list_attachments(page=1, limit=50, query=PREFIX, mime_type="APPLICATION/PDF")

    assert {r.id for r in result["data"]} == {row.id}


def test_an_unknown_mime_type_returns_zero_rows(db: Session) -> None:
    _seed(db, f"{PREFIX}known.pdf", mime_type="application/pdf")

    svc = AttachmentService(db)
    result = svc.list_attachments(
        page=1, limit=50, query=PREFIX, mime_type="application/x-nonexistent-type"
    )

    assert result["data"] == []


def test_the_unfiltered_call_is_unchanged(db: Session) -> None:
    """The regression this endpoint's many callers depend on: omitting the mime
    params must leave behaviour exactly as it was before they existed."""
    pdf = _seed(db, f"{PREFIX}reg1.pdf", mime_type="application/pdf")
    png = _seed(db, f"{PREFIX}reg2.png", mime_type="image/png")
    none = _seed(db, f"{PREFIX}reg3.bin", mime_type=None)

    svc = AttachmentService(db)
    result = svc.list_attachments(page=1, limit=50, query=PREFIX)

    ids = {row.id for row in result["data"]}
    assert ids == {pdf.id, png.id, none.id}
    assert result["pagination"].total == 3


# --------------------------------------------------------------------------- #
# Endpoint-level
# --------------------------------------------------------------------------- #
@pytest.fixture
def authed_client(monkeypatch):
    from app.dependencies import get_current_user_or_api_key
    from app.services.user_service import UserPermissionService

    # LIST-API-PERMS: the list carries a permission gate now; this principal has no users
    # row, so answer the role lookup as admin (the gate has tests/test_list_api_perms.py).
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
    app.dependency_overrides[get_current_user_or_api_key] = lambda: {
        "id": "zzt-mime-filter-user",
        "email": "zzt-mime-filter@test.com",
    }
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user_or_api_key, None)


def test_endpoint_mime_type_filters_the_list(db: Session, authed_client: TestClient) -> None:
    pdf = _seed(db, f"{PREFIX}ep1.pdf", mime_type="application/pdf")
    _seed(db, f"{PREFIX}ep2.png", mime_type="image/png")

    res = authed_client.get(
        "/api/v1/resource-management/attachments/",
        params={"query": PREFIX, "mime_type": "application/pdf"},
    )

    assert res.status_code == 200, res.text
    ids = {row["id"] for row in res.json()["data"]}
    assert ids == {pdf.id}


def test_endpoint_without_mime_params_is_unchanged(
    db: Session, authed_client: TestClient
) -> None:
    pdf = _seed(db, f"{PREFIX}ep3.pdf", mime_type="application/pdf")
    png = _seed(db, f"{PREFIX}ep4.png", mime_type="image/png")

    res = authed_client.get(
        "/api/v1/resource-management/attachments/", params={"query": PREFIX}
    )

    assert res.status_code == 200, res.text
    ids = {row["id"] for row in res.json()["data"]}
    assert ids == {pdf.id, png.id}
