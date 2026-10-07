"""AC-D3: a bulk download job that failed stays failed through the RQ status sync.

The task marks the job failed and returns normally, so RQ reports its own job as
'finished'. sync_job_status must not flip the DB row back to finished.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from app.models.job import ImportJob, JobStatus
from app.models.user import User

from tests._pg_fixture import blank_session


def _val(x):
    return getattr(x, "value", x)


def _job(db, *, status, error=None, completed=False) -> ImportJob:
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid[:8]}@test.com", name="Sync", status="ACTIVE"))
    db.flush()
    job = ImportJob(
        job_id=str(uuid.uuid4()),
        job_type="attachment_bulk_download",
        status=status,
        user_id=uid,
        error=error,
        completed_at=datetime.utcnow() if completed else None,
        job_metadata={},
    )
    db.add(job)
    db.commit()
    return job


def test_sync_keeps_a_failed_job_failed(monkeypatch):
    from app.services.job_service import JobService

    monkeypatch.setattr(
        "app.services.job_service.get_job_status",
        lambda jid: {"status": "finished", "result": None},
    )
    with blank_session() as db:
        failed = _job(db, status=JobStatus.FAILED.value, error="No file could be read", completed=True)
        running = _job(db, status=JobStatus.STARTED.value)

        JobService(db).sync_job_status(failed.job_id)
        JobService(db).sync_job_status(running.job_id)

        db.expire_all()
        f = db.query(ImportJob).filter(ImportJob.id == failed.id).one()
        assert _val(f.status) == "failed"
        assert f.error == "No file could be read"
        assert f.result is None
        # Control: a started job RQ reports finished still flips to finished.
        r = db.query(ImportJob).filter(ImportJob.id == running.id).one()
        assert _val(r.status) == "finished"
