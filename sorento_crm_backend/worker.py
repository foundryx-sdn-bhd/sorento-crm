#!/usr/bin/env python
"""RQ worker + APScheduler combined.

Single process owns both queue draining (see QUEUES / DEFAULT_QUEUES) and cron
ticks fired by `app.scheduler.task_scheduler`. Since #569, compose runs two services
sharing this file by role: `worker` (role=batch, owns the scheduler) and
`worker_fast` (role=fast, no scheduler) - so jobs and ticks are never duplicated
across blue/green API containers, and a slow batch job never sits in front of a
customer-facing one on the same drain loop.

Set `ENABLE_SCHEDULER=true` in the `worker` container only; every other container
(API, `worker_fast`) leaves it false.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Loaded HERE, before anything imports the storage layer, because only
# `app/main.py` did it and the worker is a separate process.
#
# The symptom is quiet and expensive: `storage_router.default_provider()` reads
# `STORAGE_DEFAULT_PROVIDER` straight from the environment, so with `.env`
# unloaded the API signed URLs against R2 while the worker signed them against
# S3. A catalogue PDF export then died in the worker on a CloudFront private key
# the deployment does not use. Compose hides it, because `env_file:` puts the
# real variables in the container environment, so it only bites bare-metal and
# local workers - the two places where somebody is trying to reproduce a bug.
try:  # pragma: no cover - import-time wiring
    from dotenv import load_dotenv as _load_dotenv

    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        _load_dotenv(_env_path, override=True)
    else:
        _load_dotenv(override=True)
except ImportError:
    # A container that ships real environment variables does not need it.
    pass

import logging
from rq import Worker, Queue
from app.services.queue_service import redis_conn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ForkSafeWorker(Worker):
    """RQ worker that disposes the SQLAlchemy engine pool inside each forked
    work-horse before running the job.

    RQ forks a work-horse per job. The child inherits the parent's open DB
    sockets - and when ``ENABLE_SCHEDULER=true`` the parent's APScheduler
    threads are frequently mid-query, so a live connection is shared across the
    fork. Two processes on one libpq socket corrupts the protocol, surfacing far
    from the cause as "This result object does not return rows. It has been
    closed automatically." / "PGRES_TUPLES_OK and no message from the libpq".

    ``perform_job`` runs in the child, so disposing here gives the work-horse a
    fresh pool; ``close=False`` leaves the parent's sockets untouched.

    The job runs as the `worker` audit actor, on behalf of whoever enqueued it
    (``job.meta["actor"]``, identity S0 AC-10), through the same helper the
    in-process drain uses.
    """

    def perform_job(self, job, queue):  # noqa: ANN001
        try:
            from app.database import engine

            engine.dispose(close=False)
        except Exception:
            logger.exception("ForkSafeWorker: engine.dispose(close=False) failed")
        from app.services.queue_service import job_actor_scope

        with job_actor_scope(job):
            return super().perform_job(job, queue)


def _maybe_start_scheduler():
    if os.getenv("ENABLE_SCHEDULER", "false").lower() != "true":
        logger.info("APScheduler disabled (ENABLE_SCHEDULER != true)")
        return None
    try:
        from app.scheduler.task_scheduler import start_scheduler
        sched = start_scheduler()
        logger.info("APScheduler started in worker process")
        return sched
    except Exception:
        # Fail fast: this container is the single owner of all cron ticks
        # (email outbox drainer, scheduled tasks heartbeat). Running on with a
        # dead scheduler silently strands work (e.g. email_outbox rows pending
        # forever) - crash instead so `restart: unless-stopped` surfaces it.
        logger.exception("Failed to start APScheduler; exiting so the container restarts visibly")
        sys.exit(1)


# ONE ordered registry: queue name -> latency role. Tuple order is drain order -
# `catalogue_render` is separate from `imports` on purpose (a Chromium render is
# slow and memory-hungry, and sharing the imports queue means one catalogue PDF
# blocks every Excel upload behind it); `flyer_read` is separate for the same
# reason and drains last (a 20 to 60 second PyMuPDF extraction should not sit in
# front of every Excel import); `project_docs` (quotation PDF and Excel
# rendering) sits between them - slower than an import, faster than a flyer
# read. `media` drains the chatbot media extraction jobs - the /external/media
# endpoint enqueues and then AWAITS the job, so a worker not draining it does
# not merely delay the work, every media turn waits out
# media_sync_wait_seconds and returns `pending`. `respond_io` is one outbound
# WhatsApp send per job - a user is looking at "sending...". `notifications`
# drains EVERY notification delivery - email, web push, WhatsApp
# (notification_service enqueues them all with queue_name="notifications"). It
# was missing from this registry once, so those jobs enqueued and nothing ever
# ran them: 86 notification_deliveries rows sat `pending` in production, 32 in
# one month. The failure is silent by construction - the enqueue succeeds and no
# exception is raised anywhere - so tests/test_worker_queue_defaults.py pins it.
#
# QUEUES is the ONE place a queue is classified batch vs fast. Compose (see
# below) names only a ROLE on each service - never a queue - so adding a queue
# is a single line here, and tests/test_worker_queue_defaults.py fails until
# every queue has exactly one of the two known roles. Neither compose nor
# scripts/blue_green_deploy.sh's step 6c changes when a queue is added.
QUEUES = (
    ('imports', 'batch'),
    ('respond_io', 'fast'),
    ('catalogue_render', 'batch'),
    ('media', 'fast'),
    # `chat` is the chatbot turn engine's optional worker offload (S7 / AC-703,
    # CHATBOT_TURN_ON_WORKER). Same latency class as `media` and for the same
    # reason: the request that enqueued it is WAITING on it with a customer
    # watching "typing...", so behind a 39-minute import is the one place it
    # must never be. Drains next to `media` rather than at the end for that
    # reason; the relative order of every other queue is unchanged.
    ('chat', 'fast'),
    ('project_docs', 'batch'),
    ('flyer_read', 'batch'),
    # `analytics` (chat analytics CA-2): the nightly topic labeller, queued by the
    # scheduler at 02:15 Malaysia. Minutes of model calls nobody waits on: batch.
    ('analytics', 'batch'),
    ('notifications', 'fast'),
)
ROLES = ('batch', 'fast')

# The no-env fallback (e2e-stack.sh, a bare `python worker.py` with neither
# WORKER_QUEUES nor WORKER_ROLE set) drains everything, in the order above -
# the pre-#569 default, in the pre-#569 order, plus `chat` inserted at S7.
DEFAULT_QUEUES = tuple(name for name, _role in QUEUES)


def queues_for_role(role):
    """Queue names for a role, preserving QUEUES's relative order."""
    return [name for name, r in QUEUES if r == role]


def resolve_queue_names():
    """The queues this worker drains, in drain order.

    Precedence:
    1. WORKER_QUEUES (explicit list) - dev-worktree override, kept because every
       worktree on this machine shares Redis db 0 and a hardcoded list means one
       checkout's worker drains another's `imports` with the wrong branch's code.
       Blank / whitespace-only is treated as unset.
    2. WORKER_ROLE - production: compose names a role, QUEUES owns the list.
       An unknown role exits(1) loudly, same fail-fast as the scheduler, because a
       typo here would otherwise be a worker draining nothing with a green status.
    3. Neither - drain everything (e2e-stack.sh and bare local runs rely on it).
    """
    raw = os.getenv('WORKER_QUEUES') or ''
    explicit = [q.strip() for q in raw.split(',') if q.strip()]
    if explicit:
        return explicit

    role = (os.getenv('WORKER_ROLE') or '').strip()
    if role:
        if role not in ROLES:
            logger.error(
                "Unknown WORKER_ROLE=%r; known roles: %s", role, ', '.join(ROLES)
            )
            sys.exit(1)
        return queues_for_role(role)

    return list(DEFAULT_QUEUES)


def resolve_role_label():
    """The label reported in the startup log - mirrors resolve_queue_names's precedence.

    "custom" when WORKER_QUEUES overrode, the WORKER_ROLE value when that decided
    it, "all" when neither was set. scripts/blue_green_deploy.sh step 6c greps for
    `role=fast` specifically, so this label is the deploy contract - do not rename
    it without updating that script.
    """
    raw = os.getenv('WORKER_QUEUES') or ''
    if [q.strip() for q in raw.split(',') if q.strip()]:
        return 'custom'

    role = (os.getenv('WORKER_ROLE') or '').strip()
    if role:
        return role  # resolve_queue_names() already exits(1) on an unknown one

    return 'all'


def _warn_if_vapid_missing():
    """Say so at boot when web push cannot possibly work.

    `notification_tasks._send_web_push_for_notification` reads VAPID_PRIVATE_KEY
    from os.environ and, when it is absent, marks each delivery
    failed/"VAPID not configured" and moves on. 158 deliveries died that way over
    eleven weeks with nothing surfacing it. A WARNING, not a hard failure: a
    worker that refuses to start over one optional channel is worse than one that
    runs without it.
    """
    missing = [k for k in ('VAPID_PRIVATE_KEY', 'VAPID_SUBJECT') if not os.environ.get(k)]
    if missing:
        logger.warning(
            "Web push is DISABLED for this worker: %s not set. Notification "
            "deliveries on the web_push channel will be marked failed.",
            ', '.join(missing),
        )


def register_worker_listeners():
    """Session-class listeners the API registers in its startup_event, which the worker
    never runs. RQ forks a work-horse per job that inherits them from this parent."""
    # Multi-company isolation: register the fail-closed SELECT filter + insert
    # auto-stamp here too. The worker never runs the API's startup_event, and
    # RQ forks a work-horse per job that inherits these Session-class listeners
    # from the parent. NOTE: worker jobs must set the session scope from the
    # ImportJob company snapshot (later slice) - until then owned-table writes in
    # jobs are fail-closed (rejected). See app/services/company_scope.py.
    from app.services.company_scope import register_company_scope_listeners
    register_company_scope_listeners()

    # The spec listeners were registered in the API's startup_event only, so a Product
    # written inside an RQ job (an import, chiefly) never re-derived its specs - a
    # pre-existing hole, not something this slice introduced. The write backstop would
    # be blind on the same path, which is what surfaced it.
    from app.services.product_spec_change_listener import register_product_spec_listeners
    from app.services.product_spec_write import register_spec_write_backstop
    register_product_spec_listeners()
    register_spec_write_backstop()

    # Audit: a job's ORM writes to tracked models are flush-audited as the `worker`
    # actor (identity S0, AC-10), the same as when the API drains a job in-process.
    # Bulk imports keep suppressing per-row rows via `skip_audit_entity_types`.
    from app.services.audit_service import register_audit_listeners
    register_audit_listeners()


if __name__ == '__main__':
    # The worker decrypts stored secrets too and never imports app.main.
    from app.config import settings as _settings
    from app.utils.field_encryption import assert_field_encryption_ready
    from app.services.dev_login import running_in_container
    assert_field_encryption_ready(
        _settings.environment,
        environment_explicit="environment" in _settings.model_fields_set,
        production_entrypoint=running_in_container(),
    )

    register_worker_listeners()
    _maybe_start_scheduler()
    _warn_if_vapid_missing()
    queues = resolve_queue_names()
    role_label = resolve_role_label()

    # A deploy that changed the shipped spec rules re-reads the catalogue once, on the
    # queue, with nobody pressing anything (#1286, D10). Only a worker that drains
    # `imports` asks, so the job it queues is one this worker will run, and two workers
    # of different roles do not both queue it.
    if 'imports' in queues:
        from app.services.product_spec_rederive import catch_up_on_worker_start
        catch_up_on_worker_start()

    worker = ForkSafeWorker(queues, connection=redis_conn)
    logger.info("Starting RQ worker role=%s queues=%s", role_label, ', '.join(queues))
    worker.work()
