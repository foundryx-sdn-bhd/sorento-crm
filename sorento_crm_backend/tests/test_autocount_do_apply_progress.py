"""DO-APPLY-PROGRESS (documentation/plans/autocount/PLAN-do-apply-progress.md).

The Delivery Orders apply job sat on STARTED with Total 0 / Processed 0 / Successful 0 for
its whole run (6,487 documents, ~2m12s on dev): `_apply_delivery_orders` never published
progress, only the preview did. These pin AC-1..5 of
`do-apply-progress-acceptance-criteria.md`: the total goes out before the ingest, running
processed / successful / failed / skipped tallies go out during it through a session that
is NOT the apply's own (so the batch's single commit is untouched), a publish failure never
fails the apply, and the job row ends on the final numbers.

Reuses the DO pull suite's seeding helpers and SR1's `task_db` fixture (one connection, one
savepoint per session), so a progress write committed on the apply's own session would be
released past the apply's own rollback - which is what the kill test below catches.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

# MUST be the first app import (repo convention, see test_ingest_deletions.py).
from app.main import app  # noqa: E402,F401

from tests.test_autocount_pull_delivery_orders import (
    _do_rows,
    _orders,
    _prepare_do_apply,
    _seed_masters,
)
from tests.test_autocount_pull_sr1 import (  # noqa: F401 - task_db is a fixture
    _FakeFoundryX,
    _job_row,
    _patch_foundryx,
    task_db,
)
from tests.test_autocount_pull_sr3 import _run_apply

PROGRESS_KEYS = ("processed_rows", "successful_rows", "failed_rows", "skipped_rows", "total_rows")


def _job_progress(db, job_id) -> dict:
    row = db.execute(
        text(f"SELECT {', '.join(PROGRESS_KEYS)} FROM import_jobs WHERE id = :id"),
        {"id": str(job_id)},
    ).mappings().first()
    return dict(row)


def _created_and_retryable_rows() -> list[dict]:
    """Document 1 creates; document 2 names an unknown product, so it is retryable."""
    rows = _do_rows()
    rows[1]["Details"][0]["ItemCode"] = "ZZAC-NOPE"
    return rows


@pytest.fixture
def report_every_record(monkeypatch):
    """Two documents are well under the ingest's 100-record cadence; report after each."""
    from app.services.autocount_doc_ingest_service import AutocountDocIngestService

    monkeypatch.setattr(AutocountDocIngestService, "PROGRESS_REPORT_EVERY", 1)


@pytest.fixture
def progress_calls(monkeypatch):
    """Every `JobService.update_job_progress` call, with the session it ran on, passed
    through to the real method so the row really moves."""
    from app.services.job_service import JobService

    calls: list[dict] = []
    real = JobService.update_job_progress

    def recording(self, job_id, **kwargs):
        calls.append({"job_id": job_id, "session": self.db, **kwargs})
        return real(self, job_id, **kwargs)

    monkeypatch.setattr(JobService, "update_job_progress", recording)
    return calls


def _apply_session_spy(monkeypatch, factory) -> list:
    """The sessions the task module opens, in order: the first is the apply's own."""
    opened: list = []

    def tracking_factory(*a, **k):
        session = factory(*a, **k)
        opened.append(session)
        return session

    monkeypatch.setattr("app.tasks.autocount_pull_tasks.SessionLocal", tracking_factory, raising=False)
    return opened


class TestApplyPublishesProgress:
    def test_ac1_ac2_total_first_then_running_tallies_on_a_fresh_session(
        self, task_db, monkeypatch, report_every_record, progress_calls
    ):
        from app.tasks.autocount_pull_tasks import apply_autocount_pull

        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        _seed_masters(db)
        job_id = _prepare_do_apply(db, fake, rows=_created_and_retryable_rows())
        rq_job_id = db.execute(
            text("SELECT job_id FROM import_jobs WHERE id = :id"), {"id": str(job_id)}
        ).scalar()
        opened = _apply_session_spy(monkeypatch, factory)

        apply_autocount_pull(job_id)

        assert _job_row(db, job_id)["status"] == "finished", _job_row(db, job_id)["error"]
        assert progress_calls, "the apply published no progress at all"
        apply_session = opened[0]
        assert all(c["session"] is not apply_session for c in progress_calls), (
            "progress was written on the apply's own session"
        )
        assert {c["job_id"] for c in progress_calls} == {rq_job_id}
        seen = [
            {k: c.get(k) for k in PROGRESS_KEYS} for c in progress_calls
        ]
        # AC-1: the total goes out before any document is processed.
        assert seen[0] == {
            "processed_rows": 0, "successful_rows": 0, "failed_rows": 0,
            "skipped_rows": 0, "total_rows": 2,
        }
        # AC-2: after each document, the tallies of the documents processed so far.
        assert {
            "processed_rows": 1, "successful_rows": 1, "failed_rows": 0,
            "skipped_rows": 0, "total_rows": 2,
        } in seen
        assert {
            "processed_rows": 2, "successful_rows": 1, "failed_rows": 1,
            "skipped_rows": 0, "total_rows": 2,
        } in seen

    def test_ac5_job_row_ends_on_the_final_numbers_and_counts_keep_their_keys(
        self, task_db, monkeypatch
    ):
        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        _seed_masters(db)
        rows = _created_and_retryable_rows()
        first = _prepare_do_apply(db, fake, rows=rows)

        _run_apply(monkeypatch, factory, first)

        # The publisher's last call carries the same numbers, so this alone does not prove
        # the apply wrote them itself; test_ac4 (publisher broken) pins that.
        assert _job_progress(db, first) == {
            "processed_rows": 2, "successful_rows": 1, "failed_rows": 1,
            "skipped_rows": 0, "total_rows": 2,
        }
        assert _job_row(db, first)["metadata"]["autocount_apply"]["counts"] == {
            "total": 2, "created": 1, "updated": 0, "adopted": 0, "unchanged": 0,
            "failed": 0, "retryable": 1, "lines_deleted": 0, "with_warnings": 0,
        }

        # Re-applying the same snapshot: document 1 is now unchanged (skipped).
        second = _prepare_do_apply(db, fake, rows=rows)
        _run_apply(monkeypatch, factory, second)

        assert _job_progress(db, second) == {
            "processed_rows": 2, "successful_rows": 0, "failed_rows": 1,
            "skipped_rows": 1, "total_rows": 2,
        }


class TestProgressNeverTouchesTheBatch:
    def test_ac3_a_failure_after_the_ingest_still_writes_no_order(
        self, task_db, monkeypatch, report_every_record, progress_calls
    ):
        """Kill test: if progress were committed on the apply's own session, the
        documents ingested before it would survive the apply's rollback."""
        from app.services.autocount_doc_ingest_service import AutocountDocIngestService

        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        _seed_masters(db)
        job_id = _prepare_do_apply(db, fake, rows=_do_rows())
        real_ingest = AutocountDocIngestService.ingest

        def ingest_then_blow_up(self, *a, **k):
            real_ingest(self, *a, **k)
            raise RuntimeError("boom after the ingest")

        monkeypatch.setattr(AutocountDocIngestService, "ingest", ingest_then_blow_up)
        # On one shared connection the apply's rollback also unwinds the publisher's own
        # savepoints, so stand in for what a separate connection would already have
        # committed by the time the apply fails.
        db.execute(
            text(
                "UPDATE import_jobs SET successful_rows = 2, failed_rows = 1, skipped_rows = 1 "
                "WHERE id = :id"
            ),
            {"id": str(job_id)},
        )
        db.commit()

        _run_apply(monkeypatch, factory, job_id)

        row = _job_row(db, job_id)
        assert row["status"] == "failed"
        assert "boom" in (row["error"] or "")
        assert len(progress_calls) >= 2, "the run published no mid-ingest progress"
        assert _orders(db, 900001, 900002) == []
        # The published tallies would otherwise claim two documents written.
        progress = _job_progress(db, job_id)
        assert (progress["successful_rows"], progress["failed_rows"], progress["skipped_rows"]) == (0, 0, 0)

    def test_ac4_a_failing_progress_publish_never_fails_the_apply(self, task_db, monkeypatch):
        from app.services.job_service import JobService

        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        _seed_masters(db)
        job_id = _prepare_do_apply(db, fake, rows=_do_rows())
        attempts: list[str] = []

        def broken(self, job_id, **kwargs):
            attempts.append(job_id)
            raise RuntimeError("progress store down")

        monkeypatch.setattr(JobService, "update_job_progress", broken)

        _run_apply(monkeypatch, factory, job_id)

        row = _job_row(db, job_id)
        assert row["status"] == "finished", row["error"]
        assert attempts, "the apply never tried to publish progress"
        assert len(_orders(db, 900001, 900002)) == 2
        # The final numbers are the apply's own write, not the progress publisher's.
        assert _job_progress(db, job_id) == {
            "processed_rows": 2, "successful_rows": 2, "failed_rows": 0,
            "skipped_rows": 0, "total_rows": 2,
        }

