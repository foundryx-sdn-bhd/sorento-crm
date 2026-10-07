"""Red tests for the Files page folder bulk download (FOLDER-BULK-DOWNLOAD).

Contract: documentation/plans/resources/folder-bulk-download-acceptance-criteria.md
(AC-B*, C*, D*, F2). Nothing here imports the new modules at file level, so each test
fails on its own (ImportError / 404) until the slice lands. Every test seeds its own
companies, folders, files and users on a blank Postgres schema (blank_session); the
fake storage backend stands in for S3/R2 and nothing here touches a real bucket.
"""
from __future__ import annotations

import importlib
import io
import re
import uuid
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves circular-import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.base import company_scope, set_company_scope
from app.models.company import Company
from app.models.download import DownloadStatus, UserDownload
from app.models.job import ImportJob, JobStatus
from app.models.resources import Attachment, AttachmentDirectory

from tests._pg_fixture import blank_session, unique_code

_SVC = "app.services.attachment_bulk_download_service"
_TASK = "app.tasks.attachment_bulk_download_task"
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_URL = "/api/v1/resource-management/attachments/bulk-download"


def _svc():
    return importlib.import_module(_SVC)


def _val(x):
    return getattr(x, "value", x)


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _company(db, stem: str) -> str:
    cid = str(uuid.uuid4())
    db.add(Company(id=cid, name=unique_code(stem), code=unique_code(stem)[:20]))
    db.commit()
    return cid


def _folder(db, name, parent_id=None, company_id=None, is_deleted=False) -> str:
    fid = str(uuid.uuid4())
    db.add(
        AttachmentDirectory(
            id=fid, name=name, parent_id=parent_id, company_id=company_id, is_deleted=is_deleted
        )
    )
    db.commit()
    return fid


_clock = {"n": 0}


def _file(db, name, directory_id=None, company_id=None, is_deleted=False, stored=True) -> str:
    """Seed an attachment. Files are created oldest-first in call order."""
    aid = str(uuid.uuid4())
    _clock["n"] += 1
    when = datetime(2026, 1, 1) + timedelta(minutes=_clock["n"])
    db.add(
        Attachment(
            id=aid,
            original_filename=name,
            stored_filename=name if stored else name,
            file_path=f"attachments/{aid}.bin",
            directory_id=directory_id,
            company_id=company_id,
            file_size_bytes=10,
            mime_type="application/octet-stream",
            access_levels=["dealer"],
            uploaded_at=when,
            created_at=when,
            is_deleted=is_deleted,
        )
    )
    db.commit()
    return aid


def _paths(plan) -> list[str]:
    return sorted(e.path for e in plan.entries)


def _plan(db, *, directory_id=None, folder_ids=(), attachment_ids=(), scope=None):
    with company_scope(db, scope):
        return _svc().plan_archive(
            db,
            directory_id=directory_id,
            folder_ids=list(folder_ids),
            attachment_ids=list(attachment_ids),
        )


# --------------------------------------------------------------------------- #
# B: planning rules
# --------------------------------------------------------------------------- #
def test_b1_mixed_selection_paths_relative_to_viewed_folder(db):
    viewed = _folder(db, "Marketing")
    photo = _folder(db, "Photo", parent_id=viewed)
    raw = _folder(db, "Raw", parent_id=photo)
    a = _file(db, "a.pdf", directory_id=viewed)
    _file(db, "p1.jpg", directory_id=photo)
    _file(db, "r.cr2", directory_id=raw)
    _file(db, "not-selected.pdf", directory_id=viewed)

    plan = _plan(db, directory_id=viewed, folder_ids=[photo], attachment_ids=[a])

    assert _paths(plan) == ["Photo/Raw/r.cr2", "Photo/p1.jpg", "a.pdf"]
    assert all(e.attachment is not None for e in plan.entries)


def test_b2_nested_selection_appears_once_inside_parent(db):
    parent = _folder(db, "Parent")
    child = _folder(db, "Child", parent_id=parent)
    f_in_child = _file(db, "f.txt", directory_id=child)
    _file(db, "g.txt", directory_id=parent)

    plan = _plan(db, folder_ids=[parent, child], attachment_ids=[f_in_child])

    assert _paths(plan) == ["Parent/Child/f.txt", "Parent/g.txt"]


def test_b3_empty_folder_is_a_directory_entry(db):
    empty = _folder(db, "Empty")

    plan = _plan(db, folder_ids=[empty])

    assert _paths(plan) == ["Empty/"]
    assert plan.entries[0].attachment is None
    assert plan.folders == 1
    assert plan.files_not_visible == 0


def test_b5_same_name_case_insensitive_gets_copy_suffix(db):
    # Assumes the first-uploaded file keeps its name (rows are seeded oldest-first).
    folder = _folder(db, "Covers")
    _file(db, "Cover.JPG", directory_id=folder)
    _file(db, "cover.jpg", directory_id=folder)
    _file(db, "COVER.jpg", directory_id=folder)

    plan = _plan(db, folder_ids=[folder])

    assert _paths(plan) == sorted(
        ["Covers/Cover.JPG", "Covers/cover - copy.jpg", "Covers/COVER - copy (2).jpg"]
    )


def test_b6_slashes_in_folder_name_become_dash(db):
    folder = _folder(db, "SRT LITE / DIY")
    _file(db, "x.pdf", directory_id=folder)

    plan = _plan(db, folder_ids=[folder])

    assert _paths(plan) == ["SRT LITE - DIY/x.pdf"]


def test_b6b_dot_names_cannot_escape(db):
    svc = _svc()
    dotdot = _folder(db, "..")
    dot = _folder(db, ".", parent_id=dotdot)
    _file(db, "x.pdf", directory_id=dot)
    slash_file = _file(db, "/")

    plan = _plan(db, folder_ids=[dotdot], attachment_ids=[slash_file])

    for e in plan.entries:
        assert not e.path.startswith("/"), e.path
        for seg in e.path.rstrip("/").split("/"):
            assert seg not in ("", ".", ".."), f"unsafe segment in {e.path!r}"
    assert _paths(plan) == ["_", "_/_/x.pdf"]
    assert svc._safe_name("..") == "_"
    assert svc._safe_name(".") == "_"
    assert svc._safe_name("/") == "_"
    assert svc._safe_name("a/b") == "a-b"


def test_b7_archived_files_and_trashed_folders_excluded(db):
    top = _folder(db, "Top")
    trashed = _folder(db, "Trashed", parent_id=top, is_deleted=True)
    _file(db, "live-in-trashed.pdf", directory_id=trashed)
    _file(db, "archived.pdf", directory_id=top, is_deleted=True)
    _file(db, "kept.pdf", directory_id=top)

    plan = _plan(db, folder_ids=[top])

    assert _paths(plan) == ["Top/kept.pdf"]


def test_b9_archive_filename_cases():
    svc = _svc()
    now = datetime(2026, 10, 7, 14, 12, 33)
    assert svc.archive_filename("Marketing", now) == "Marketing-20261007-1412.zip"
    assert svc.archive_filename(None, now) == "Files-20261007-1412.zip"
    assert svc.archive_filename("SRT/DIY\\x", now) == "SRT-DIY-x-20261007-1412.zip"


def test_suffix_copy_name_rules():
    svc = _svc()
    assert svc.suffix_copy_name("x.jpg") == "x - copy.jpg"
    assert svc.suffix_copy_name("x - copy.jpg") == "x - copy (2).jpg"
    assert svc.suffix_copy_name("x - copy (2).jpg") == "x - copy (3).jpg"
    assert svc.suffix_copy_name("archive.tar.gz") == "archive.tar - copy.gz"


# --------------------------------------------------------------------------- #
# C2 / C3: the pinned scope triple
# --------------------------------------------------------------------------- #
def test_c2_c3_scope_triple_in_one_shared_folder(db):
    a, b = _company(db, "A"), _company(db, "B")
    shared_folder = _folder(db, "Shared", company_id=None)
    _file(db, "shared.pdf", directory_id=shared_folder, company_id=None)
    _file(db, "own-a.pdf", directory_id=shared_folder, company_id=a)
    _file(db, "other-b.pdf", directory_id=shared_folder, company_id=b)

    scoped = _plan(db, folder_ids=[shared_folder], scope=frozenset({a}))
    assert _paths(scoped) == ["Shared/own-a.pdf", "Shared/shared.pdf"]
    assert scoped.files_not_visible == 1

    everyone = _plan(db, folder_ids=[shared_folder], scope=None)
    assert _paths(everyone) == ["Shared/other-b.pdf", "Shared/own-a.pdf", "Shared/shared.pdf"]
    assert everyone.files_not_visible == 0


# --------------------------------------------------------------------------- #
# Fake storage + task harness
# --------------------------------------------------------------------------- #
class FakeBackend:
    def __init__(self, failing: set[str] | None = None):
        self.failing = failing or set()
        self.uploads: list[dict] = []
        self.deleted: list[str] = []

    def download_file(self, key):
        m = _UUID_RE.search(str(key))
        assert m, f"unrecognised storage key {key!r}"
        if m.group(0) in self.failing:
            raise RuntimeError("storage read failed")
        return b"data-" + m.group(0).encode()

    def upload_fileobj(self, fileobj, key, content_type=None):
        has_read = hasattr(fileobj, "read")
        data = b""
        if has_read:
            try:
                fileobj.seek(0)
            except Exception:
                pass
            data = fileobj.read()
        self.uploads.append(
            {"key": key, "has_read": has_read, "data": data, "content_type": content_type}
        )
        return key

    def delete_file(self, key):
        self.deleted.append(key)
        return True


def _user(db) -> str:
    from app.models.user import User

    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid[:8]}@test.com", name="Bulk", status="ACTIVE"))
    db.commit()
    return uid


def _job(db, *, user_id, folder_ids=(), attachment_ids=(), scope=None, directory_id=None):
    """An import_jobs row + pending user_downloads row, as the endpoint would write them."""
    from app.services.download_service import DownloadService
    from app.services.job_service import JobService

    job = JobService(db).create_job(
        job_type="attachment_bulk_download",
        user_id=user_id,
        filename="Files-test.zip",
        metadata={
            "directory_id": directory_id,
            "folder_ids": list(folder_ids),
            "attachment_ids": list(attachment_ids),
            "company_scope": sorted(scope) if scope is not None else None,
        },
    )
    db.commit()
    dl = DownloadService(db).create(
        user_id=user_id,
        kind="attachment_bulk_zip",
        filename="Files-test.zip",
        source_entity_type="import_job",
        source_entity_id=str(job.id),
    )
    job.job_metadata = {**job.job_metadata, "download_id": str(dl.id)}
    db.commit()
    return job, dl


def _run_task(db, monkeypatch, job, backend):
    monkeypatch.setattr("app.services.storage_router.get_backend", lambda *a, **k: backend)
    monkeypatch.setattr(f"{_TASK}.SessionLocal", lambda: db, raising=True)
    monkeypatch.setattr(f"{_TASK}.get_backend", lambda *a, **k: backend, raising=False)
    monkeypatch.setattr(db, "close", lambda: None)
    importlib.import_module(_TASK).process_attachment_bulk_download(str(job.id))
    db.expire_all()


def _reload(db, job, dl):
    return (
        db.query(ImportJob).filter(ImportJob.id == job.id).one(),
        db.query(UserDownload).filter(UserDownload.id == dl.id).one(),
    )


# --------------------------------------------------------------------------- #
# D3 / F2: task happy path
# --------------------------------------------------------------------------- #
def test_d3_f2_task_happy_path_zip_job_and_download_row(db, monkeypatch):
    uid = _user(db)
    photo = _folder(db, "Photo")
    empty = _folder(db, "Empty")
    root_file = _file(db, "a.pdf")
    _file(db, "p1.jpg", directory_id=photo)
    job, dl = _job(db, user_id=uid, folder_ids=[photo, empty], attachment_ids=[root_file])
    backend = FakeBackend()

    _run_task(db, monkeypatch, job, backend)

    job, dl = _reload(db, job, dl)
    assert len(backend.uploads) == 1
    up = backend.uploads[0]
    assert up["key"] == f"bulk-exports/{job.id}.zip"
    assert up["content_type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(up["data"]))
    assert zf.testzip() is None
    assert sorted(zf.namelist()) == ["Empty/", "Photo/p1.jpg", "a.pdf"]

    assert _val(job.status) == JobStatus.FINISHED.value
    r = job.result
    assert r["archive_filename"].startswith("Files-") and r["archive_filename"].endswith(".zip")
    assert (r["files_included"], r["files_failed"], r["files_not_visible"], r["folders"]) == (2, 0, 0, 2)
    assert r["bytes"] == len(up["data"])
    assert r["download_id"] == str(dl.id)
    assert job.total_rows == 2 and job.processed_rows == 2
    assert job.source_file_size is None

    assert _val(dl.status) == DownloadStatus.READY.value
    assert dl.storage_key == f"bulk-exports/{job.id}.zip"
    assert dl.storage_provider
    assert dl.filename == r["archive_filename"]


def test_d4_one_unreadable_file_is_counted_and_skipped(db, monkeypatch):
    uid = _user(db)
    folder = _folder(db, "Docs")
    _file(db, "ok.pdf", directory_id=folder)
    bad = _file(db, "bad.pdf", directory_id=folder)
    job, dl = _job(db, user_id=uid, folder_ids=[folder])
    backend = FakeBackend(failing={bad})

    _run_task(db, monkeypatch, job, backend)

    job, dl = _reload(db, job, dl)
    assert _val(job.status) == JobStatus.FINISHED.value
    assert job.result["files_failed"] == 1 and job.result["files_included"] == 1
    names = zipfile.ZipFile(io.BytesIO(backend.uploads[0]["data"])).namelist()
    assert "Docs/ok.pdf" in names and "Docs/bad.pdf" not in names


def test_d5_all_unreadable_fails_with_no_upload(db, monkeypatch):
    uid = _user(db)
    folder = _folder(db, "Docs")
    bad = _file(db, "bad.pdf", directory_id=folder)
    job, dl = _job(db, user_id=uid, folder_ids=[folder])
    backend = FakeBackend(failing={bad})

    _run_task(db, monkeypatch, job, backend)

    job, dl = _reload(db, job, dl)
    assert backend.uploads == []
    assert _val(job.status) == JobStatus.FAILED.value
    assert job.error == "No file could be read"
    assert _val(dl.status) == DownloadStatus.FAILED.value
    assert dl.error == "No file could be read"


def test_d6_cancelled_job_uploads_nothing(db, monkeypatch):
    uid = _user(db)
    folder = _folder(db, "Docs")
    _file(db, "ok.pdf", directory_id=folder)
    job, dl = _job(db, user_id=uid, folder_ids=[folder])
    job.status = JobStatus.CANCELLED.value
    db.commit()
    backend = FakeBackend()

    _run_task(db, monkeypatch, job, backend)

    job, dl = _reload(db, job, dl)
    assert backend.uploads == []
    assert _val(job.status) == JobStatus.CANCELLED.value
    assert _val(dl.status) == DownloadStatus.FAILED.value
    assert dl.error == "Download cancelled"


def test_d7_byte_count_above_int32_round_trips_in_result(db, monkeypatch):
    uid = _user(db)
    folder = _folder(db, "Big")
    _file(db, "big.bin", directory_id=folder)
    job, dl = _job(db, user_id=uid, folder_ids=[folder])
    backend = FakeBackend()

    def fake_write_archive(plan, read_bytes, out, on_progress=None, should_stop=None):
        out.write(b"PK-fake")
        return _svc().ArchiveStats(
            files_included=1, files_failed=0, bytes=3_300_000_000, cancelled=False
        )

    monkeypatch.setattr(f"{_SVC}.write_archive", fake_write_archive)
    monkeypatch.setattr(f"{_TASK}.write_archive", fake_write_archive, raising=False)

    _run_task(db, monkeypatch, job, backend)

    job, dl = _reload(db, job, dl)
    assert _val(job.status) == JobStatus.FINISHED.value
    assert job.result["bytes"] == 3_300_000_000
    assert job.source_file_size is None


def test_d7b_upload_receives_a_file_object_not_bytes(db, monkeypatch):
    uid = _user(db)
    folder = _folder(db, "Docs")
    _file(db, "ok.pdf", directory_id=folder)
    job, dl = _job(db, user_id=uid, folder_ids=[folder])
    backend = FakeBackend()

    _run_task(db, monkeypatch, job, backend)

    assert len(backend.uploads) == 1
    assert backend.uploads[0]["has_read"] is True


def test_c5_task_reapplies_two_company_scope_snapshot(db, monkeypatch):
    uid = _user(db)
    a, b, c = _company(db, "A"), _company(db, "B"), _company(db, "C")
    folder = _folder(db, "Shared", company_id=None)
    _file(db, "a.pdf", directory_id=folder, company_id=a)
    _file(db, "b.pdf", directory_id=folder, company_id=b)
    _file(db, "c.pdf", directory_id=folder, company_id=c)
    job, dl = _job(db, user_id=uid, folder_ids=[folder], scope=frozenset({a, b}))
    backend = FakeBackend()

    _run_task(db, monkeypatch, job, backend)

    names = sorted(zipfile.ZipFile(io.BytesIO(backend.uploads[0]["data"])).namelist())
    assert names == ["Shared/a.pdf", "Shared/b.pdf"]
    job, _ = _reload(db, job, dl)
    assert job.result["files_not_visible"] == 1


# --------------------------------------------------------------------------- #
# write_archive contract (zip validity, failure counting, progress, stop)
# --------------------------------------------------------------------------- #
def test_write_archive_valid_zip_counts_and_progress(db):
    svc = _svc()
    folder = _folder(db, "Docs")
    ids = [_file(db, f"f{i}.txt", directory_id=folder) for i in range(23)]
    bad = ids[3]
    plan = _plan(db, folder_ids=[folder])
    seen: list[int] = []

    def read_bytes(att):
        if att.id == bad:
            raise RuntimeError("boom")
        return b"x" * 5

    out = io.BytesIO()
    stats = svc.write_archive(plan, read_bytes, out, on_progress=seen.append)

    assert (stats.files_included, stats.files_failed) == (22, 1)
    assert stats.bytes == len(out.getvalue())
    zf = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    assert zf.testzip() is None
    assert len(zf.namelist()) == 22
    assert seen and seen[-1] == 23 and len(seen) >= 3  # >= once per 10 files plus the end


def test_write_archive_stops_early_when_should_stop(db):
    svc = _svc()
    folder = _folder(db, "Docs")
    for i in range(30):
        _file(db, f"f{i}.txt", directory_id=folder)
    plan = _plan(db, folder_ids=[folder])

    stats = svc.write_archive(plan, lambda att: b"x", io.BytesIO(), should_stop=lambda: True)

    assert stats.cancelled is True
    assert stats.files_included < 30


# --------------------------------------------------------------------------- #
# Endpoint: C1 / C4 / D1
# --------------------------------------------------------------------------- #
@pytest.fixture
def api(db):
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.models.user import User, UserRole, UserRoleAssignment
    from app.services.company_scope_resolver import apply_company_scope

    uid, role_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(UserRole(id=role_id, slug="superadmin", name="Superadmin", description="",
                    is_protected=True, is_default=False))
    db.flush()
    db.add(User(id=uid, email=f"{uid[:8]}@test.com", name="Bulk", status="ACTIVE"))
    db.flush()
    db.add(UserRoleAssignment(user_id=uid, role_id=role_id))
    db.commit()
    company_a = _company(db, "A")
    company_b = _company(db, "B")
    set_company_scope(db, frozenset({company_a}))

    def _override_get_db():
        yield db

    me = lambda: {"id": uid, "email": f"{uid[:8]}@test.com"}  # noqa: E731
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = me
    app.dependency_overrides[get_current_user_or_api_key] = me
    app.dependency_overrides[apply_company_scope] = lambda: None
    with TestClient(app) as c:
        yield SimpleNamespace(client=c, db=db, uid=uid, a=company_a, b=company_b)
    app.dependency_overrides.clear()


def test_c1_unauthenticated_is_401(db):
    from app.dependencies import get_db

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as c:
            resp = c.post(_URL, json={"directory_id": None, "folder_ids": [str(uuid.uuid4())],
                                      "attachment_ids": []})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 401, resp.text


def test_c4_empty_selection_is_400(api):
    resp = api.client.post(_URL, json={"directory_id": None, "folder_ids": [], "attachment_ids": []})
    assert resp.status_code == 400, resp.text


def test_c4_unknown_or_invisible_ids_are_404_and_create_no_job(api, monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.queue_service.enqueue_job",
                        lambda *a, **k: calls.append((a, k)) or SimpleNamespace(id="rq"))
    other_company_file = _file(api.db, "b-only.pdf", company_id=api.b)

    unknown = api.client.post(_URL, json={"directory_id": None, "folder_ids": [str(uuid.uuid4())],
                                          "attachment_ids": [str(uuid.uuid4())]})
    invisible = api.client.post(_URL, json={"directory_id": None, "folder_ids": [],
                                            "attachment_ids": [other_company_file]})

    assert unknown.status_code == 404, unknown.text
    assert invisible.status_code == 404, invisible.text
    assert calls == []
    assert api.db.query(ImportJob).filter(ImportJob.user_id == api.uid).count() == 0


def test_d1_happy_path_creates_job_download_row_and_enqueues(api, monkeypatch):
    calls = []

    def fake_enqueue(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return SimpleNamespace(id=str(kwargs.get("job_id") or "rq-1"))

    monkeypatch.setattr("app.services.queue_service.enqueue_job", fake_enqueue)
    folder = _folder(api.db, "Marketing", company_id=api.a)
    fid = _folder(api.db, "Photo", parent_id=folder, company_id=api.a)
    att = _file(api.db, "a.pdf", directory_id=folder, company_id=api.a)

    resp = api.client.post(_URL, json={"directory_id": folder, "folder_ids": [fid],
                                       "attachment_ids": [att]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"job_id", "id", "download_id", "message"} <= set(body)

    job = api.db.query(ImportJob).filter(ImportJob.id == body["id"]).one()
    assert job.job_type == "attachment_bulk_download"
    assert job.user_id == api.uid
    md = job.job_metadata
    assert md["directory_id"] == folder
    assert md["folder_ids"] == [fid]
    assert md["attachment_ids"] == [att]
    assert md["company_scope"] == [api.a]
    assert md["download_id"] == body["download_id"]

    dl = api.db.query(UserDownload).filter(UserDownload.id == body["download_id"]).one()
    assert dl.kind == "attachment_bulk_zip"
    assert _val(dl.status) == DownloadStatus.PENDING.value
    assert dl.user_id == api.uid
    assert dl.filename and dl.filename.startswith("Marketing-") and dl.filename.endswith(".zip")
    assert dl.source_entity_type == "import_job"
    assert dl.source_entity_id == str(job.id)

    assert len(calls) == 1
    func, args, kwargs = calls[0]
    assert func.__name__ == "process_attachment_bulk_download"
    assert kwargs.get("queue_name") == "imports"
    assert str(job.id) in [str(x) for x in args]


def test_c6_second_request_while_in_flight_is_409(api, monkeypatch):
    from app.services.download_service import DownloadService

    monkeypatch.setattr(
        "app.services.queue_service.enqueue_job",
        lambda func, *a, **k: SimpleNamespace(id=str(k.get("job_id") or "rq-1")),
    )
    folder = _folder(api.db, "Marketing", company_id=api.a)
    _file(api.db, "a.pdf", directory_id=folder, company_id=api.a)
    payload = {"directory_id": None, "folder_ids": [folder], "attachment_ids": []}

    first = api.client.post(_URL, json=payload)
    assert first.status_code == 200, first.text

    second = api.client.post(_URL, json=payload)
    assert second.status_code == 409, second.text
    assert "already being prepared" in second.text

    DownloadService(api.db).mark_ready(
        first.json()["download_id"], storage_provider="r2", storage_key="bulk-exports/x.zip"
    )
    third = api.client.post(_URL, json=payload)
    assert third.status_code == 200, third.text


# --------------------------------------------------------------------------- #
# Reviewer round: cancel path, fail-closed scope, 403, sibling folder names
# --------------------------------------------------------------------------- #
def test_d6b_cancel_does_not_kill_the_work_horse(db, monkeypatch):
    import time

    from app.services.job_service import JobService

    uid = _user(db)
    stops: list[str] = []
    monkeypatch.setattr("app.services.queue_service.cancel_job", lambda jid, *a, **k: stops.append(jid))
    monkeypatch.setattr("app.services.job_service.cancel_rq_job", lambda jid, *a, **k: stops.append(jid))

    svc = JobService(db)
    bulk = svc.create_job(job_type="attachment_bulk_download", user_id=uid, metadata={})
    other = svc.create_job(job_type="stock_import", user_id=uid, metadata={})
    bulk.status = JobStatus.STARTED.value
    other.status = JobStatus.STARTED.value
    db.commit()

    assert svc.cancel_job(str(bulk.job_id)) is True
    time.sleep(0.4)  # the RQ stop is sent from a daemon thread
    db.expire_all()
    assert _val(db.query(ImportJob).filter(ImportJob.id == bulk.id).one().status) == "cancelled"
    assert stops == [], "cancelling a bulk download must not stop the RQ work horse"

    # Control: any other job type keeps today's behaviour.
    assert svc.cancel_job(str(other.job_id)) is True
    time.sleep(0.4)
    assert stops == [str(other.job_id)]


def test_d6c_cancel_after_the_loop_stops_the_upload(db, monkeypatch, tmp_path):
    import tempfile

    uid = _user(db)
    folder = _folder(db, "Docs")
    for n in ("a.pdf", "b.pdf", "c.pdf"):
        _file(db, n, directory_id=folder)
    job, dl = _job(db, user_id=uid, folder_ids=[folder])
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    class CancelOnLast(FakeBackend):
        calls = 0

        def download_file(self, key):
            data = super().download_file(key)
            CancelOnLast.calls += 1
            if CancelOnLast.calls == 3:
                db.query(ImportJob).filter(ImportJob.id == job.id).update(
                    {"status": JobStatus.CANCELLED.value}
                )
                db.commit()
            return data

    backend = CancelOnLast()
    _run_task(db, monkeypatch, job, backend)

    job, dl = _reload(db, job, dl)
    assert backend.uploads == []
    assert _val(job.status) == JobStatus.CANCELLED.value
    assert _val(dl.status) == DownloadStatus.FAILED.value
    assert dl.error == "Download cancelled"
    assert list(tmp_path.iterdir()) == [], "temp file must be removed"


def test_c5b_missing_scope_key_fails_closed(db, monkeypatch):
    uid = _user(db)
    a = _company(db, "A")
    folder = _folder(db, "Shared", company_id=None)
    _file(db, "only-a.pdf", directory_id=folder, company_id=a)
    job, dl = _job(db, user_id=uid, folder_ids=[folder], scope=frozenset({a}))
    job.job_metadata = {k: v for k, v in job.job_metadata.items() if k != "company_scope"}
    db.commit()
    backend = FakeBackend()

    _run_task(db, monkeypatch, job, backend)

    job, dl = _reload(db, job, dl)
    assert backend.uploads == []
    assert _val(job.status) == JobStatus.FAILED.value
    assert "only-a.pdf" not in str(job.result) + str(job.error) + str(dl.error)


def test_c5c_unset_scope_snapshot_is_empty_list(api, monkeypatch):
    import app.models.base as base

    monkeypatch.setattr(
        "app.services.queue_service.enqueue_job",
        lambda func, *a, **k: SimpleNamespace(id=str(k.get("job_id") or "rq-1")),
    )
    folder = _folder(api.db, "Marketing", company_id=None)
    _file(api.db, "a.pdf", directory_id=folder, company_id=None)
    payload = {"directory_id": None, "folder_ids": [folder], "attachment_ids": []}

    # The visibility check runs under the real scope; only the snapshot read sees UNSET.
    real = base.get_company_scope
    seen = {"n": 0}

    def flaky(db):
        seen["n"] += 1
        return base.UNSET

    monkeypatch.setattr(base, "get_company_scope", flaky)
    try:
        resp = api.client.post(_URL, json=payload)
    finally:
        monkeypatch.setattr(base, "get_company_scope", real)

    assert resp.status_code in (200, 404), resp.text
    if resp.status_code == 404:
        pytest.fail("UNSET scope hid a shared folder before the snapshot was taken; "
                    "cannot assert the snapshot (route must snapshot independently)")
    job = api.db.query(ImportJob).filter(ImportJob.id == resp.json()["id"]).one()
    assert job.job_metadata["company_scope"] == []


def test_c1b_user_without_drive_permission_gets_403(db):
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.models.user import User, UserRole, UserRoleAssignment
    from app.services.company_scope_resolver import apply_company_scope

    uid, role_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(UserRole(id=role_id, slug=f"nobody-{role_id[:8]}", name="Nobody", description="",
                    is_protected=False, is_default=False))
    db.flush()
    db.add(User(id=uid, email=f"{uid[:8]}@test.com", name="Nobody", status="ACTIVE"))
    db.flush()
    db.add(UserRoleAssignment(user_id=uid, role_id=role_id))
    db.commit()
    folder = _folder(db, "Marketing")

    me = lambda: {"id": uid, "email": f"{uid[:8]}@test.com"}  # noqa: E731
    def _db_override():
        yield db

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_user] = me
    app.dependency_overrides[get_current_user_or_api_key] = me
    app.dependency_overrides[apply_company_scope] = lambda: None
    try:
        with TestClient(app) as c:
            resp = c.post(_URL, json={"directory_id": None, "folder_ids": [folder],
                                      "attachment_ids": []})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403, resp.text


def test_b5b_sibling_folder_names_are_unique(db):
    parent = _folder(db, "Parent")
    p1 = _folder(db, "Promo", parent_id=parent)
    p2 = _folder(db, "Promo", parent_id=parent)
    ab_slash = _folder(db, "A/B", parent_id=parent)
    ab_dash = _folder(db, "A-B", parent_id=parent)
    _file(db, "a.pdf", directory_id=p1)
    _file(db, "a.pdf", directory_id=p2)
    _file(db, "Promo", directory_id=parent)
    _file(db, "x.pdf", directory_id=ab_slash)
    _file(db, "y.pdf", directory_id=ab_dash)

    plan = _plan(db, folder_ids=[parent])
    paths = _paths(plan)

    assert len({p.lower() for p in paths}) == len(paths), paths
    top = {p.split("/")[1] for p in paths if p.count("/") >= 2}
    assert "Promo" in top and "Promo - copy" in top
    assert "A-B" in top and "A-B - copy" in top
    # the file named "Promo" shares the seen-set with the folders, so it is renamed too
    assert "Parent/Promo - copy (2)" in paths or any(
        p.startswith("Parent/Promo - copy") and p.count("/") == 1 for p in paths
    )
