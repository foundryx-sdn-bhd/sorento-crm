"""S20: a document read that is killed must end as failed, not sit on "running" for ever.

The bug this pins, reproduced on real data on 2026-08-08: a customer PO sat on screen as
"Waiting to be read", 10 pages, 0 lines, with a progress bar, indefinitely. The RQ
work-horse had been killed (signal 15) and RQ had correctly moved the job to its
FailedJobRegistry, but ``project_po_versions.extraction_state`` still said ``running``,
because the task writes its failure inside an ``except`` block and **an ``except`` block
never runs when the process is killed**.

So the detection cannot live inside the dying process. It lives in
``project_extraction_recovery_service``, which asks RQ what became of the job whose id the
row recorded. The three things worth pinning, and the reason each one is here:

1. **A killed read is reported.** The row ends ``failed`` with a sentence that says the
   read was interrupted, which is a different sentence from the one a document that
   genuinely could not be parsed gets. Telling somebody to re-scan a document that scans
   fine is advice that cannot help them.
2. **A long healthy read is NOT reported.** Extraction is one vision call per page,
   sequentially: ten pages measured at 166 seconds, so a forty page PO legitimately runs
   for over ten minutes. A reconciler that calls a live job dead is a worse bug than the
   one it fixes, so the decision is made from RQ's own answer about the job and never from
   the clock, wherever a job id exists to ask about.
3. **It can be retried**, on the same version, without touching the database by hand.

An RQ ``on_failure`` callback was the obvious mechanism and does NOT work here: in rq
2.6.1 ``execute_failure_callback`` is called from ``Worker.perform_job``, which runs INSIDE
the work-horse, and from ``StartedJobRegistry.cleanup``. ``monitor_work_horse``, the path
that actually handles a killed horse, calls ``handle_job_failure`` and never the callback.
A callback would have had exactly the same blind spot as the ``except`` block.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.project_so import (
    DeliverySchedule,
    DeliveryScheduleVersion,
    ProjectPOVersion,
)
from app.models.user import User
from app.services import project_extraction_recovery_service as recovery
from app.services import project_seed_service
from app.services.error_handler import AppException

from ._pg_fixture import blank_session

MARKER = "zzt-extraction-recovery"


def _uid() -> str:
    return str(uuid.uuid4())


def _message(exc: AppException) -> str:
    detail = exc.detail
    return (detail or {}).get("message", "") if isinstance(detail, dict) else str(detail)


def _code(exc: AppException) -> str:
    detail = exc.detail
    return (detail or {}).get("code", "") if isinstance(detail, dict) else ""


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _project(db, company_id: str, owner: str):
    from app.services.project_service import register_project

    return register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Tuju {_uid()[:12]}",
    )


def _po(db, project, owner: str):
    from app.services import project_po_service as po_svc

    return po_svc.create_po(
        db,
        project=project,
        actor_user_id=owner,
        payload={
            "po_number": f"ZZT/{_uid()[:8]}",
            "po_source": "contractor_direct",
        },
    )


def _po_version(
    db,
    po,
    *,
    state: str = "running",
    job_id: str | None = "zzt-job",
    started_minutes_ago: float | None = 5,
    error: str | None = None,
    version_no: int = 1,
) -> ProjectPOVersion:
    version = ProjectPOVersion(
        company_id=po.company_id,
        purchase_order_id=po.id,
        version_no=version_no,
        source_filename=f"{MARKER}.pdf",
        page_count=10,
        extraction_state=state,
        extraction_error=error,
        extraction_job_id=job_id,
        extraction_started_at=(
            None
            if started_minutes_ago is None
            else datetime.utcnow() - timedelta(minutes=started_minutes_ago)
        ),
    )
    db.add(version)
    db.flush()
    return version


def _schedule_version(
    db,
    project,
    po,
    *,
    state: str = "running",
    job_id: str | None = "zzt-schedule-job",
    started_minutes_ago: float | None = 5,
) -> DeliveryScheduleVersion:
    schedule = DeliverySchedule(
        id=_uid(),
        company_id=po.company_id,
        project_id=project.id,
        purchase_order_id=po.id,
        label=f"{MARKER} programme",
    )
    db.add(schedule)
    db.flush()
    version = DeliveryScheduleVersion(
        company_id=po.company_id,
        delivery_schedule_id=schedule.id,
        version_no=1,
        source_filename=f"{MARKER}-schedule.pdf",
        extraction_state=state,
        extraction_job_id=job_id,
        extraction_started_at=(
            None
            if started_minutes_ago is None
            else datetime.utcnow() - timedelta(minutes=started_minutes_ago)
        ),
    )
    db.add(version)
    db.flush()
    return version


def _answer(monkeypatch, view: recovery.JobView | None) -> None:
    """Make RQ answer with `view` for whatever job id it is asked about."""
    monkeypatch.setattr(recovery, "look_up_job", lambda job_id: view)


def _enqueues(monkeypatch) -> list[str]:
    """Capture what a retry hands to the queue, without a Redis anywhere near the test."""
    from app.tasks import project_document_tasks

    seen: list[str] = []

    class _Job:
        id = "zzt-new-job"

    monkeypatch.setattr(
        project_document_tasks,
        "enqueue_po_extraction",
        lambda version_id: (seen.append(version_id), _Job())[1],
    )
    monkeypatch.setattr(
        project_document_tasks,
        "enqueue_schedule_extraction",
        lambda version_id: (seen.append(version_id), _Job())[1],
    )
    return seen


@pytest.fixture()
def seeded():
    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} CONTACT AH")
        project = _project(db, company_id, owner)
        yield db, project, _po(db, project, owner), owner


# ------------------------------------------------------- the death gets reported


def test_a_killed_work_horse_leaves_the_version_failed_and_not_running(seeded, monkeypatch):
    """The reported bug, end to end.

    RQ's own words for a killed horse are "Work-horse terminated unexpectedly; waitpid
    returned 15 (signal 15)". The row has to end somewhere a person can act from.
    """
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="running", started_minutes_ago=6)

    _answer(
        monkeypatch,
        recovery.JobView(
            status="failed",
            exc_info="Work-horse terminated unexpectedly; waitpid returned 15 (signal 15); ",
        ),
    )
    changed = recovery.reconcile_one(db, version)

    assert changed is True
    assert version.extraction_state == "failed"
    assert version.extraction_error


def test_the_reason_says_the_read_was_interrupted_and_names_the_signal(seeded, monkeypatch):
    """A death and a parse failure must not read the same.

    "The document could not be read, try a sharper scan" is actively wrong advice for a
    document that was never read at all, and it sends somebody off to re-scan a perfectly
    good PDF.
    """
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="running", started_minutes_ago=6)

    _answer(
        monkeypatch,
        recovery.JobView(
            status="failed",
            exc_info="Work-horse terminated unexpectedly; waitpid returned 15 (signal 15); ",
        ),
    )
    recovery.reconcile_one(db, version)

    reason = version.extraction_error
    assert reason.startswith(recovery.INTERRUPTED_PREFIX)
    assert "signal 15" in reason
    # And it points at the way out rather than leaving a dead end.
    assert "again" in reason.lower()


def test_a_job_that_vanished_from_the_queue_is_given_up_on(seeded, monkeypatch):
    """Redis knows nothing about the job any more, and the row never went terminal.

    Nothing is running: a live job keeps its own hash alive. So the only honest reading is
    that the read ended without recording anything.
    """
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="queued", started_minutes_ago=None)
    # Nothing has started, so the age is measured from when the row was created.
    version.created_at = datetime.utcnow() - timedelta(minutes=30)
    db.flush()

    _answer(monkeypatch, None)
    assert recovery.reconcile_one(db, version) is True
    assert version.extraction_state == "failed"
    assert version.extraction_error.startswith(recovery.INTERRUPTED_PREFIX)


def test_a_job_that_finished_without_writing_a_result_is_reported(seeded, monkeypatch):
    """RQ says finished, the row says running. Something returned without recording."""
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="running", started_minutes_ago=6)

    _answer(monkeypatch, recovery.JobView(status="finished", exc_info=None))
    assert recovery.reconcile_one(db, version) is True
    assert version.extraction_state == "failed"


# ------------------------------------------- and a healthy read is NOT reported


def test_a_long_but_healthy_read_is_left_exactly_where_it_is(seeded, monkeypatch):
    """The constraint that makes this fix safe.

    Ten pages measured at 166 seconds, one vision call per page, sequentially. Twenty five
    minutes of a forty page document is not evidence of anything: RQ says the job is
    started, so it is started, and the clock does not get a vote.
    """
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="running", started_minutes_ago=25)

    _answer(monkeypatch, recovery.JobView(status="started", exc_info=None))
    assert recovery.reconcile_one(db, version) is False
    assert version.extraction_state == "running"
    assert version.extraction_error is None


def test_a_read_still_waiting_its_turn_on_the_queue_is_left_alone(seeded, monkeypatch):
    """Queued behind another document is not dead, however long the queue is."""
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="queued", started_minutes_ago=None)
    version.created_at = datetime.utcnow() - timedelta(minutes=45)
    db.flush()

    _answer(monkeypatch, recovery.JobView(status="queued", exc_info=None))
    assert recovery.reconcile_one(db, version) is False
    assert version.extraction_state == "queued"


def test_a_read_that_has_only_just_started_is_never_second_guessed(seeded, monkeypatch):
    """Even with no job to ask about, a fresh row is left alone.

    An upload commits the row BEFORE it enqueues, so for a moment there is genuinely a
    running-looking row with nothing behind it yet. Racing that would fail every upload.
    """
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="queued", job_id=None, started_minutes_ago=None)
    version.created_at = datetime.utcnow() - timedelta(seconds=20)
    db.flush()

    _answer(monkeypatch, None)
    assert recovery.reconcile_one(db, version) is False
    assert version.extraction_state == "queued"


def test_a_row_with_no_job_id_is_only_given_up_on_well_past_the_job_timeout(
    seeded, monkeypatch
):
    """The path for rows that predate this fix, and only that path.

    With no job id there is nothing to ask RQ about, so time is the only evidence left. The
    floor is set past the RQ job timeout (1800s), after which RQ kills the horse itself, so
    a read still legitimately in flight can never reach it.
    """
    db, _project, po, _owner = seeded
    healthy = _po_version(db, po, state="running", job_id=None, started_minutes_ago=25)
    _answer(monkeypatch, None)
    assert recovery.reconcile_one(db, healthy) is False
    assert healthy.extraction_state == "running"

    stranded = _po_version(
        db, po, state="running", job_id=None, started_minutes_ago=45, version_no=2
    )
    assert recovery.reconcile_one(db, stranded) is True
    assert stranded.extraction_state == "failed"
    assert stranded.extraction_error.startswith(recovery.INTERRUPTED_PREFIX)


def test_a_document_that_really_could_not_be_parsed_keeps_its_own_reason(
    seeded, monkeypatch
):
    """Terminal is terminal. The reconciler corrects a missing verdict, never an existing
    one, so "Pages 4, 5 could not be read" is not overwritten by a generic death."""
    db, _project, po, _owner = seeded
    parse_failure = "The document reader failed (page 4 came back blank)."
    version = _po_version(db, po, state="failed", error=parse_failure)

    _answer(monkeypatch, recovery.JobView(status="failed", exc_info="anything at all"))
    assert recovery.reconcile_one(db, version) is False
    assert version.extraction_error == parse_failure


def test_a_finished_read_is_never_touched(seeded, monkeypatch):
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="done")

    _answer(monkeypatch, None)
    assert recovery.reconcile_one(db, version) is False
    assert version.extraction_state == "done"


# ----------------------------------------------------------------------- retry


def test_a_stranded_version_can_be_read_again_without_touching_the_database(
    seeded, monkeypatch
):
    """The whole point of the slice: a way out that does not need a DBA.

    The version number, the stored document and the history are all kept. Re-uploading
    would have made a version 2 of a document that was never the problem.
    """
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="failed", error="The read was interrupted...")
    queued = _enqueues(monkeypatch)

    recovery.retry(db, version)

    assert version.extraction_state == "queued"
    assert version.extraction_error is None
    assert version.extraction_started_at is None
    assert queued == [str(version.id)]
    # The new job is recorded, so the next reconcile asks about THIS attempt.
    assert version.extraction_job_id == "zzt-new-job"


def test_a_read_that_is_genuinely_running_refuses_to_be_restarted(seeded, monkeypatch):
    """The same guard as the reconciler, from the other side.

    Two work-horses reading one document would both write lines onto it. RQ says started,
    so the answer is no, and the message says why rather than failing silently.
    """
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="running", started_minutes_ago=25)
    queued = _enqueues(monkeypatch)
    _answer(monkeypatch, recovery.JobView(status="started", exc_info=None))

    with pytest.raises(AppException) as raised:
        recovery.retry(db, version)

    assert raised.value.status_code == 409
    assert "still" in _message(raised.value).lower()
    assert queued == []
    assert version.extraction_state == "running"


def test_a_stranded_read_is_retried_even_though_the_row_still_says_running(
    seeded, monkeypatch
):
    """A retry reconciles first, so a person does not have to wait for a poll to flip the
    row before the button will work."""
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="running", started_minutes_ago=6)
    queued = _enqueues(monkeypatch)
    _answer(
        monkeypatch,
        recovery.JobView(status="failed", exc_info="Work-horse terminated unexpectedly"),
    )

    recovery.retry(db, version)

    assert version.extraction_state == "queued"
    assert queued == [str(version.id)]


def test_a_document_already_read_is_not_re_read_over_the_top_of_it(seeded, monkeypatch):
    """A second read would discard every line a person has since corrected by hand."""
    db, _project, po, _owner = seeded
    version = _po_version(db, po, state="done")
    queued = _enqueues(monkeypatch)
    _answer(monkeypatch, None)

    with pytest.raises(AppException) as raised:
        recovery.retry(db, version)

    assert raised.value.status_code == 409
    assert queued == []


def test_a_confirmed_version_cannot_be_read_again(seeded, monkeypatch):
    """Confirmed means a person has agreed to what this document said. Re-reading it would
    rewrite the record underneath them."""
    db, _project, po, owner = seeded
    version = _po_version(db, po, state="failed")
    version.confirmed_at = datetime.utcnow()
    version.confirmed_by = owner
    db.flush()
    queued = _enqueues(monkeypatch)
    _answer(monkeypatch, None)

    with pytest.raises(AppException) as raised:
        recovery.retry(db, version)

    assert raised.value.status_code == 409
    assert queued == []


# ------------------------------------------- the delivery schedule, same shape


def test_a_killed_schedule_read_is_reported_the_same_way(seeded, monkeypatch):
    """The schedule task has the identical shape and the identical hole, so it gets the
    identical fix rather than a second invention."""
    db, project, po, _owner = seeded
    version = _schedule_version(db, project, po, state="running", started_minutes_ago=6)

    _answer(
        monkeypatch,
        recovery.JobView(
            status="failed",
            exc_info="Work-horse terminated unexpectedly; waitpid returned 9 (signal 9); ",
        ),
    )
    assert recovery.reconcile_one(db, version) is True
    assert version.extraction_state == "failed"
    assert version.extraction_error.startswith(recovery.INTERRUPTED_PREFIX)
    assert "signal 9" in version.extraction_error


def test_a_long_schedule_read_is_left_alone_too(seeded, monkeypatch):
    db, project, po, _owner = seeded
    version = _schedule_version(db, project, po, state="running", started_minutes_ago=25)

    _answer(monkeypatch, recovery.JobView(status="started", exc_info=None))
    assert recovery.reconcile_one(db, version) is False
    assert version.extraction_state == "running"


def test_a_schedule_can_be_read_again(seeded, monkeypatch):
    db, project, po, _owner = seeded
    version = _schedule_version(db, project, po, state="failed")
    queued = _enqueues(monkeypatch)

    recovery.retry(db, version)

    assert version.extraction_state == "queued"
    assert queued == [str(version.id)]
    assert version.extraction_job_id == "zzt-new-job"


def test_a_partially_read_schedule_is_not_re_read_over_the_top_of_it(seeded, monkeypatch):
    """`partial` is a first-class outcome on a schedule, not a failure: the columns that
    came back are usable and may already have been corrected."""
    db, project, po, _owner = seeded
    version = _schedule_version(db, project, po, state="partial")
    queued = _enqueues(monkeypatch)
    _answer(monkeypatch, None)

    with pytest.raises(AppException) as raised:
        recovery.retry(db, version)

    assert raised.value.status_code == 409
    assert queued == []


# ------------------------------------------------------- the queue decoupling

# The client asked for this in as many words: "make sure the processing is by a queue in
# the backend so refreshing the page doesn't kill it". It already is. These two pin it, so
# that it cannot quietly stop being true.


def test_no_request_path_ever_calls_the_reader():
    """Extraction is reachable ONLY from the task module.

    A browser refresh cancels the HTTP request, so anything the reader did inside a request
    would die with it. It runs in a work-horse the request never touches, and the only way
    that stays true is if nothing outside `app/tasks` can start it.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    callers = []
    for path in sorted(root.rglob("*.py")):
        mentions = [
            line.strip() for line in path.read_text().splitlines() if "run_extraction(" in line
        ]
        if not mentions:
            continue
        # A file that only DEFINES the method is not a caller of it. Recognised by shape
        # rather than by a list of filenames: the PO reader's definition has already moved
        # once (the 2026-08-12 split of the service into three mixins carried it into
        # `project_po_intake_lifecycle.py`), and a hardcoded exclusion list goes stale on
        # every such move - failing here for a refactor, which is not what this guards.
        if all(line.startswith("def ") for line in mentions):
            continue
        callers.append(str(path.relative_to(root)))

    # The invariant is WHERE a caller lives, not which files exist. Naming one file made
    # this fail the moment main added `tasks/media_tasks.py`, which reads a voice note the
    # same way and is just as safely inside the queue. A caller outside `tasks/` is the
    # only thing that breaks the promise in the docstring.
    outside = [name for name in callers if not name.startswith("tasks/")]

    assert not outside, f"extraction is startable from outside app/tasks: {outside}"
    assert callers, "nothing calls run_extraction; the guard is watching a dead name"


def test_the_worker_this_checkout_starts_listens_on_the_project_documents_queue():
    """The gap that made every PO look broken.

    Document reads go to their own queue, `project_docs`, so a worker started from another
    checkout cannot claim a job whose task module it does not have. That only works if the
    worker started from THIS checkout actually listens on it: a worker on
    `imports,respond_io` alone drains nothing, uploads enqueue and nothing ever reads them,
    and the symptom is identical to the stranding this slice fixes.
    """
    from pathlib import Path

    from app.tasks.project_document_tasks import PROJECT_DOCS_QUEUE

    worker = (Path(__file__).resolve().parents[1] / "worker.py").read_text()
    assert f"'{PROJECT_DOCS_QUEUE}'" in worker or f"{PROJECT_DOCS_QUEUE}" in worker


# --------------------------------------------------------------------------------------
# What RQ actually hands back
#
# Every test above replaces `look_up_job`, which is the documented seam and the reason the
# decision table can be exercised without a Redis. It is also a blind spot: no real
# `JobStatus` ever reaches the line that turns RQ's answer into our vocabulary, so a bug in
# that one conversion is invisible to all of them. One did live there, and it disabled this
# entire module in production. These two tests cover the seam itself.
# --------------------------------------------------------------------------------------


def test_a_killed_job_is_read_by_value_because_str_of_a_JobStatus_is_its_repr(monkeypatch):
    """The bug measured on 2026-08-09 by killing a real work-horse.

    RQ's `JobStatus` is a `(str, Enum)`, so `__str__` is Enum's, not str's:
    `str(JobStatus.FAILED)` is "JobStatus.FAILED". Lowercased that is "jobstatus.failed",
    which is in neither `_ALIVE` nor `_DEAD`, so `_verdict` took its "an unrecognised
    status is not evidence of death" branch and left the row on `running` for ever - the
    exact stranding this module exists to remove, with RQ having correctly logged
    "moving job to FailedJobRegistry (signal 15)" a minute earlier.
    """
    from rq.job import JobStatus

    class _Job:
        exc_info = "Work-horse terminated unexpectedly; waitpid returned 15 (signal 15);"

        def get_status(self):
            # The real enum member, exactly as rq returns it.
            return JobStatus.FAILED

    monkeypatch.setattr("rq.job.Job.fetch", classmethod(lambda cls, *a, **k: _Job()))

    view = recovery.look_up_job("any-job-id")

    assert view is not None
    assert view.status == "failed", (
        f"got {view.status!r}: RQ's status must be read by .value, never str()"
    )
    assert view.status in recovery._DEAD


def test_every_status_rq_can_report_is_one_this_module_recognises():
    """No status RQ can produce may fall through to "not evidence of death".

    The fall-through branch is deliberately safe - guessing that an unknown status means
    death would fail live reads - but it is only safe while it is unreachable for real
    statuses. If RQ adds one, this fails here rather than by silently stranding rows again.
    """
    from rq.job import JobStatus

    known = recovery._ALIVE | recovery._DEAD | {"finished"}
    unrecognised = sorted(s.value for s in JobStatus if s.value not in known)

    assert not unrecognised, f"rq can report {unrecognised}, which this module ignores"
