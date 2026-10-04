"""
Route-level tests for GET /api/v1/resource-management/upload-activity.

Auth bypass pattern copied from test_lookup_public_api.py.
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# MUST be first app import - resolves circular-import in app.modules.runtime.guards
from app.main import app  # noqa: E402
from tests._pg_fixture import blank_session


_USER_ID = "130c548f-048f-53b2-97a6-3a54676bea77"
_ROLE_ID = "7c50d6db-8dce-555a-85a2-86cf7756f33f"
def _seed_user(db: Session) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    db.add(
        UserRole(
            id=_ROLE_ID,
            slug="superadmin",
            name="Superadmin",
            description="",
            is_protected=True,
            is_default=False,
        )
    )
    db.flush()
    db.add(User(id=_USER_ID, email="u1@test.com", name="U1", status="ACTIVE"))
    db.flush()
    db.add(UserRoleAssignment(user_id=_USER_ID, role_id=_ROLE_ID))
    db.commit()


@pytest.fixture
def client():
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db

    with blank_session() as db:
        _seed_user(db)

        def _override_get_db():
            yield db

        def _override_current_user():
            return {"id": _USER_ID, "email": "u1@test.com"}

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_current_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_current_user

        try:
            with TestClient(app) as c:
                yield c, db
        finally:
            app.dependency_overrides.clear()


def _add_attachment(
    db: Session,
    *,
    filename: str,
    batch_id: str | None = None,
    created_at: datetime | None = None,
    attachment_type_id: str | None = None,
    untyped: bool = False,
) -> str:
    """Typed by default: an untyped attachment never reaches the drawer (owner
    rule, 2 Oct 2026), so a test about how a row renders needs a type. Pass
    ``untyped=True`` to build one of the rows the drawer leaves out."""
    from app.models.resources import Attachment

    if attachment_type_id is None and not untyped:
        attachment_type_id = _add_attachment_type(db, f"Type {uuid.uuid4().hex[:8]}")
    attachment_id = str(uuid.uuid4())
    db.add(
        Attachment(
            id=attachment_id,
            attachment_type_id=attachment_type_id,
            original_filename=filename,
            stored_filename=filename,
            file_path=f"/x/{filename}",
            file_size_bytes=10,
            mime_type="image/jpeg",
            access_levels=["dealer"],
            uploaded_by=_USER_ID,
            upload_batch_id=batch_id,
            created_at=created_at or datetime.utcnow(),
            uploaded_at=created_at or datetime.utcnow(),
        )
    )
    db.commit()
    return attachment_id


def _add_attachment_type(
    db: Session, type_name: str, *, triggers_n8n_webhook: bool = True
) -> str:
    from app.models.resources import AttachmentType

    type_id = str(uuid.uuid4())
    db.add(
        AttachmentType(
            id=type_id,
            type_name=type_name,
            allowed_extensions="xls,xlsx,xlsm",
            max_file_size_mb=10,
            triggers_n8n_webhook=triggers_n8n_webhook,
        )
    )
    db.commit()
    return type_id


def _add_log(
    db: Session,
    *,
    attachment_id: str,
    status: str = "success",
    payload: dict | None = None,
    error_code: str | None = None,
) -> str:
    from app.models.integration import IntegrationLog

    log_id = str(uuid.uuid4())
    db.add(
        IntegrationLog(
            id=log_id,
            integration_channel="n8n",
            business_table="attachments",
            business_id=attachment_id,
            direction="outbound",
            endpoint="https://example.invalid/webhook",
            http_method="POST",
            status=status,
            response_payload=json.dumps(payload) if payload is not None else None,
            error_code=error_code,
            processed_at=datetime.utcnow(),
        )
    )
    db.commit()
    return log_id


def test_returns_empty_when_no_attachments(client):
    c, _db = client
    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    assert r.json() == {"sessions": []}


def test_stock_list_uploads_excluded_from_feed(client):
    """Stock List replacements are background reference-data uploads - they
    never get an n8n 'linked' callback, so they must not appear in the drawer
    (they'd be stuck on Processing forever). Other typed uploads still show.

    The exclusion is now driven by `triggers_n8n_webhook`, not by the type NAME
    (migration 318), so the type has to opt out explicitly. On a real database
    the migration does that; a test builds its own row and must say so.
    """
    c, db = client
    stock_type_id = _add_attachment_type(db, "Stock_List", triggers_n8n_webhook=False)
    _add_attachment(
        db,
        filename="stock balance - Macro Version.xlsx",
        attachment_type_id=stock_type_id,
    )
    _add_attachment(db, filename="receipt.pdf")  # typed, opted in - must remain visible

    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    sessions = r.json()["sessions"]
    filenames = [f["filename"] for s in sessions for f in s["files"]]
    assert "receipt.pdf" in filenames
    assert "stock balance - Macro Version.xlsx" not in filenames


def test_untyped_uploads_excluded_from_feed(client):
    """Owner rule (2 Oct 2026): an attachment with no type is never sent to
    n8n, so nothing about it may appear in upload activity either. The
    loading-plan stock list is the case that surfaced it: stored untyped, it sat
    on "Processing" forever waiting for an n8n reply that is not coming.
    """
    c, db = client
    _add_attachment(db, filename="supplier stock list.xlsx", untyped=True)
    batch = str(uuid.uuid4())
    _add_attachment(db, filename="untyped-in-batch.pdf", batch_id=batch, untyped=True)
    _add_attachment(db, filename="typed-in-batch.pdf", batch_id=batch)
    _add_attachment(db, filename="brochure.pdf")

    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    filenames = [f["filename"] for s in r.json()["sessions"] for f in s["files"]]
    assert "supplier stock list.xlsx" not in filenames
    assert "untyped-in-batch.pdf" not in filenames
    assert "typed-in-batch.pdf" in filenames
    assert "brochure.pdf" in filenames


def test_single_attachment_renders_as_single_session(client):
    c, db = client
    aid = _add_attachment(db, filename="receipt.pdf")
    _add_log(
        db,
        attachment_id=aid,
        status="success",
        payload={
            "schema_version": 1,
            "outcome": "linked",
            "summary": "Linked",
            "linked": [
                {
                    "entity_type": "product",
                    "entity_id": "p1",
                    "display_name": "CBMC5570",
                    "matched_by": "filename_token",
                }
            ],
            "unlinked_reasons": [],
            "errors": [],
        },
    )

    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_type"] == "single"
    assert s["status"] == "linked"
    assert s["aggregate"]["linked"] == 1
    assert s["files"][0]["status"] == "linked"
    # Linked entities are now read from the DB link tables (product_attachments
    # etc.), not from response_payload. With no actual product link seeded the
    # list is empty even when the n8n payload claims a linked product.
    assert s["files"][0]["linked"] == []


def test_multi_file_batch_groups_attachments(client):
    c, db = client
    batch = str(uuid.uuid4())
    a1 = _add_attachment(db, filename="a.jpg", batch_id=batch)
    a2 = _add_attachment(db, filename="b.jpg", batch_id=batch)
    _add_log(db, attachment_id=a1, status="success", payload={"schema_version": 1, "outcome": "linked", "summary": "ok", "linked": [], "unlinked_reasons": [], "errors": []})
    _add_log(db, attachment_id=a2, status="failed", error_code="N8N_CALLBACK_TIMEOUT")

    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    sessions = r.json()["sessions"]
    multi = [s for s in sessions if s["session_type"] == "multi"]
    assert len(multi) == 1
    assert multi[0]["aggregate"]["total"] == 2
    assert multi[0]["aggregate"]["failed"] == 1
    assert multi[0]["status"] == "partial"
    assert multi[0]["needs_action"] is True


def test_bulk_zip_session_when_batch_matches_import_job(client):
    from app.models.job import ImportJob

    c, db = client
    job_id = str(uuid.uuid4())
    db.add(
        ImportJob(
            id=uuid.uuid4(),
            job_id=job_id,
            job_type="attachment_bulk_import",
            status="finished",
            user_id=_USER_ID,
            filename="brand_2026.zip",
        )
    )
    db.commit()
    a1 = _add_attachment(db, filename="x.jpg", batch_id=job_id)
    _add_log(db, attachment_id=a1, status="success", payload={"schema_version": 1, "outcome": "linked", "summary": "ok", "linked": [], "unlinked_reasons": [], "errors": []})

    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    sessions = r.json()["sessions"]
    bulk = [s for s in sessions if s["session_type"] == "bulk_zip"]
    assert len(bulk) == 1
    assert bulk[0]["import_job_id"] == job_id
    assert bulk[0]["title"] == "brand_2026.zip"


def _add_import_job(db: Session, *, job_type: str, status: str, **kw) -> str:
    from app.models.job import ImportJob

    job_id = str(uuid.uuid4())
    db.add(
        ImportJob(
            id=uuid.uuid4(),
            job_id=job_id,
            job_type=job_type,
            status=status,
            user_id=_USER_ID,
            **kw,
        )
    )
    db.commit()
    return job_id


def test_import_job_sessions_in_feed(client):
    """Excel/data import jobs (stock/DO/GRN/...) render as import_job sessions - 
    replaces the per-page LatestImportStatusPanel bar."""
    c, db = client
    running = _add_import_job(
        db,
        job_type="delivery_order_detail_import",
        status="started",
        filename="Order Listing - Macro Version.xlsx",
        total_rows=100,
        processed_rows=40,
    )
    finished = _add_import_job(
        db,
        job_type="stock_import",
        status="finished",
        total_rows=10,
        processed_rows=10,
        successful_rows=10,
    )
    partial = _add_import_job(
        db,
        job_type="grn_listing_import",
        status="finished",
        total_rows=5,
        processed_rows=5,
        successful_rows=3,
        failed_rows=2,
    )
    failed = _add_import_job(
        db, job_type="order_tracking_import", status="failed", error="boom"
    )

    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    by_id = {s["session_id"]: s for s in r.json()["sessions"]}

    s = by_id[running]
    assert s["session_type"] == "import_job"
    assert s["status"] == "processing"
    assert s["title"] == "Order Listing - Macro Version.xlsx"
    assert s["total_rows"] == 100 and s["processed_rows"] == 40

    assert by_id[finished]["status"] == "linked"
    assert by_id[finished]["title"] == "Stock import"  # no filename → label
    assert by_id[partial]["status"] == "partial"
    assert by_id[failed]["status"] == "failed"
    assert by_id[failed]["needs_action"] is True
    assert by_id[failed]["job_error"] == "boom"


def test_attachment_bulk_import_job_not_duplicated_as_import_job_session(client):
    from app.models.job import ImportJob

    c, db = client
    job_id = str(uuid.uuid4())
    db.add(
        ImportJob(
            id=uuid.uuid4(),
            job_id=job_id,
            job_type="attachment_bulk_import",
            status="finished",
            user_id=_USER_ID,
            filename="brand.zip",
        )
    )
    db.commit()
    a1 = _add_attachment(db, filename="x.jpg", batch_id=job_id)
    _add_log(db, attachment_id=a1, status="success", payload={"schema_version": 1, "outcome": "linked", "summary": "ok", "linked": [], "unlinked_reasons": [], "errors": []})

    r = c.get("/api/v1/resource-management/upload-activity")
    sessions = r.json()["sessions"]
    assert [s["session_type"] for s in sessions if s["session_id"] == job_id] == ["bulk_zip"]


def test_since_query_filters_old_attachments(client):
    c, db = client
    old_aid = _add_attachment(
        db,
        filename="old.jpg",
        created_at=datetime.utcnow() - timedelta(days=14),
    )
    _add_log(db, attachment_id=old_aid, status="success", payload={"schema_version": 1, "outcome": "linked", "summary": "ok", "linked": [], "unlinked_reasons": [], "errors": []})

    # Default 7-day cutoff: 14-day-old attachment excluded.
    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    assert r.json() == {"sessions": []}


def test_unsupported_scope_rejected(client):
    c, _db = client
    r = c.get("/api/v1/resource-management/upload-activity?scope=tenant")
    assert r.status_code == 422


def test_failed_log_surfaces_payload_error_when_column_is_null(client):
    """n8n's error branch posts only {status, response_payload:{error}} - it
    never writes the error_message COLUMN that every user-facing surface reads,
    so the drawer used to show a bare "Integration failed" while the real reason
    sat unread in response_payload. Fall back to it.
    """
    c, db = client
    aid = _add_attachment(db, filename="packing-list.pdf")
    _add_log(
        db,
        attachment_id=aid,
        status="failed",
        payload={"error": "Container TEMU1234567 was already recorded and is fully received."},
    )

    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    file = r.json()["sessions"][0]["files"][0]
    assert file["status"] == "failed"
    assert file["error_message"] == (
        "Container TEMU1234567 was already recorded and is fully received."
    )


def test_error_message_column_wins_over_payload_error(client):
    """When the backend stamped the column itself (duplicate packing list), that
    deliberate copy must win over n8n's serialized error string."""
    c, db = client
    aid = _add_attachment(db, filename="packing-list.pdf")
    log_id = _add_log(
        db,
        attachment_id=aid,
        status="failed",
        payload={"error": "409 - {\"detail\":\"...\"}"},
        error_code="DUPLICATE_PACKING_LIST",
    )
    from app.models.integration import IntegrationLog

    log = db.query(IntegrationLog).filter(IntegrationLog.id == log_id).first()
    log.error_message = "Container TEMU1234567 (shipment date 2026-06-01, ETA 2026-06-20) ..."
    db.commit()

    r = c.get("/api/v1/resource-management/upload-activity")
    assert r.status_code == 200, r.text
    file = r.json()["sessions"][0]["files"][0]
    assert file["error_code"] == "DUPLICATE_PACKING_LIST"
    assert file["error_message"].startswith("Container TEMU1234567 (shipment date")


def test_resubmit_refuses_an_untyped_attachment(client, monkeypatch):
    """Resubmit's no-prior-log fallback must not claim a send for an untyped
    file: the webhook helper skips it (owner rule, 2 Oct 2026), so the honest
    answer is a 400, and still no n8n log row."""
    from app.models.integration import IntegrationLog

    monkeypatch.setattr(
        "app.api.v1.resources.attachments.get_n8n_attachment_webhook_url",
        lambda _db: "https://n8n.test/hook",
    )
    c, db = client
    aid = _add_attachment(db, filename="supplier stock list.xlsx", untyped=True)

    r = c.post(f"/api/v1/resource-management/attachments/{aid}/resubmit")

    assert r.status_code == 400, r.text
    assert "no attachment type" in r.text
    assert (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "attachments", IntegrationLog.business_id == aid)
        .count()
        == 0
    )


def test_resubmit_refuses_an_untyped_attachment_with_a_legacy_log(client, monkeypatch):
    """An untyped file sent to n8n before the rule still has its log; resubmit
    must not take the legacy-log branch and force-resend it."""
    from app.services.integration_service import IntegrationLogService

    sent = []
    monkeypatch.setattr(
        IntegrationLogService,
        "send_webhook_for_log",
        lambda self, *a, **k: sent.append(a) or (True, None),
    )
    c, db = client
    aid = _add_attachment(db, filename="old untyped.xlsx", untyped=True)
    _add_log(db, attachment_id=aid, status="pending")

    r = c.post(f"/api/v1/resource-management/attachments/{aid}/resubmit")

    assert r.status_code == 400, r.text
    assert "no attachment type" in r.text
    assert sent == []
