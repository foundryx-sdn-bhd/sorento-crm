"""F5 / F6: per-kind retention and stale windows for attachment_bulk_zip rows."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.models.download import DownloadStatus, UserDownload

from tests._pg_fixture import blank_session


class _Backend:
    def __init__(self):
        self.deleted: list[str] = []

    def delete_file(self, key):
        self.deleted.append(key)
        return True


def _row(db, *, kind, status, age, user_id, key=None) -> str:
    rid = str(uuid.uuid4())
    db.add(
        UserDownload(
            id=rid,
            user_id=user_id,
            kind=kind,
            status=status,
            filename=f"{rid[:8]}.zip",
            storage_provider="s3" if key else None,
            storage_key=key,
            created_at=datetime.utcnow() - age,
        )
    )
    db.commit()
    return rid


def test_f5_retention_is_per_kind(monkeypatch):
    from app.services import download_service

    backend = _Backend()
    monkeypatch.setattr("app.services.storage_router.get_backend", lambda *a, **k: backend)
    uid = str(uuid.uuid4())
    with blank_session() as db:
        old_zip = _row(db, kind="attachment_bulk_zip", status=DownloadStatus.READY.value,
                       age=timedelta(hours=25), user_id=uid, key=f"bulk-exports/{uuid.uuid4()}.zip")
        old_pdf = _row(db, kind="complaint_pdf", status=DownloadStatus.READY.value,
                       age=timedelta(hours=25), user_id=uid, key=f"complaints/{uuid.uuid4()}.pdf")
        young_zip = _row(db, kind="attachment_bulk_zip", status=DownloadStatus.READY.value,
                         age=timedelta(hours=2), user_id=uid, key=f"bulk-exports/{uuid.uuid4()}.zip")
        zip_key = db.get(UserDownload, old_zip).storage_key

        download_service.purge_expired_downloads(db, retention_days=30, now=datetime.utcnow())

        db.expire_all()
        assert db.get(UserDownload, old_zip) is None
        assert db.get(UserDownload, old_pdf) is not None
        assert db.get(UserDownload, young_zip) is not None
        assert backend.deleted == [zip_key]

    assert download_service.RETENTION_BY_KIND == {"attachment_bulk_zip": timedelta(days=1)}


def test_f6_stale_window_is_per_kind():
    from app.services import download_service

    uid = str(uuid.uuid4())
    with blank_session() as db:
        bulk = _row(db, kind="attachment_bulk_zip", status=DownloadStatus.PROCESSING.value,
                    age=timedelta(minutes=30), user_id=uid)
        pdf = _row(db, kind="complaint_pdf", status=DownloadStatus.PROCESSING.value,
                   age=timedelta(minutes=30), user_id=uid)

        download_service.DownloadService(db).fail_stale(uid)

        db.expire_all()
        assert db.get(UserDownload, bulk).status == DownloadStatus.PROCESSING.value
        assert db.get(UserDownload, pdf).status == DownloadStatus.FAILED.value

    assert download_service.STALE_AFTER_BY_KIND == {
        "attachment_bulk_zip": timedelta(hours=2, minutes=10)
    }
