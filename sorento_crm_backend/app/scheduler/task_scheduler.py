"""General background task scheduler (imports, integrations, notifications, summaries)."""
import logging
from contextlib import contextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.database import SessionLocal
from app.models.base import set_company_scope
from app.services.integration_service import IntegrationLogService
from app.services.queue_service import get_queue, run_sync_rq_jobs
from app.services.scheduled_task_service import (
    run_due_tasks,
    register_handler,
)
from app.services.respond_sync_handler import run_respond_contacts_sync
from app.services.attachment_storage_audit_service import run_attachment_storage_audit
from app.services.n8n_liveness_service import run_n8n_liveness_ping
from app.services.system_health_alert_service import run_health_watchdog, run_health_daily_digest
from app.services.user_sla_daily_summary_service import run_user_sla_daily_summary
from app.services.marketing_service import PromotionService
from app.services.automation_service import AutomationService
from app.config import settings
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@contextmanager
def scheduler_session(name: str = "scheduler"):
    """A DB session for background ticks, explicitly scoped to ALL companies.

    Sessions default to ``UNSET``, which the company-scope filter treats as zero
    rows (fail closed). That is right for an unauthenticated request and wrong for
    a scheduler tick: it has no principal, yet must see every company's work. Left
    at the default, an overdue-SLA scan reads zero rows from every company-scoped
    table, escalates nothing, and raises nothing - the failure is completely silent.

    Background work opts out of the filter, exactly as ``import_tasks`` and
    ``export_tasks`` already do. Per-row company still governs what each tick then
    does with what it read (an SLA tracker escalates up its own company's ladder).

    Every write in the tick is audited as the `scheduler` actor with ``job_id`` =
    ``name`` and no user (identity S0, AC-10); the previous actor is restored after.
    """
    from app.audit_context import AuditActor, actor_scope

    db = SessionLocal()
    set_company_scope(db, None)
    # One correlation id per tick (#1281 S0), so one tick's changes read as one action.
    import uuid as _uuid

    from app.audit_context import audit_context_scope

    try:
        with actor_scope(AuditActor(actor_type="scheduler", job_id=name), db=db), \
                audit_context_scope(correlation_id=_uuid.uuid4().hex[:16]):
            yield db
    finally:
        db.close()


def _handler_integration_log_retry(db, task):
    """Handler for integration_log_retry: process pending integration logs."""
    service = IntegrationLogService(db)
    result = service.process_pending_logs()
    return {
        "processed": result.get("processed", 0),
        "succeeded": result.get("succeeded", 0),
        "failed": result.get("failed", 0),
    }


def _handler_import_job_processor(db, task):
    """Drain RQ imports queue (race-safe lpop) + settle rows RQ has already given up on."""
    drain = run_sync_rq_jobs("imports", max_jobs=2)
    orphans = _reconcile_orphan_import_jobs(db)
    return {**drain, "orphans_failed": orphans}


def _reconcile_orphan_import_jobs(db) -> int:
    """Settle `import_jobs` rows that RQ has already finished with.

    Two statuses, two deliberately different rules, because "Redis has never heard
    of this job" means opposite things for each:

    QUEUED
        The row was never picked up, so a missing job hash can only mean the
        enqueue is gone (worker exited before `start_job`, task body raised at
        import time, temp file unreachable across containers). Fail it.

    STARTED
        Only an explicit `failed` / `canceled` from RQ counts. A missing hash is
        ambiguous here: a job that FINISHED loses its hash after `result_ttl`
        (500s by default), so "no hash" fits "it succeeded and the task crashed
        before writing the row" exactly as well as it fits "it died". Failing on
        that would relabel completed imports as failures, which is worse than
        leaving one row unsettled.

    STARTED is covered at all because of the 19 Aug 2026 SPO import: a deploy
    recreates the worker in place, Docker allows ten seconds for RQ's warm
    shutdown, and an import still running at t+10s takes the SIGKILL with the
    parent. The parent stops heartbeating, the started-registry entry expires
    ~90s later (`job_monitoring_interval + 60`, NOT `job_timeout`), and the next
    worker's maintenance sweep moves the job to the FailedJobRegistry with
    `AbandonedJobError`. The DB row meanwhile still said `started` for ever,
    because the only thing that settled it was `JobService.sync_job_status`
    running as a side effect of somebody opening the job page.

    Only rows older than 3 minutes are inspected, so a fresh enqueue is never raced.
    """
    from rq.job import Job
    from rq.exceptions import NoSuchJobError
    from app.models.job import ImportJob, JobStatus
    from app.services.job_service import JobService
    from app.services.queue_service import redis_conn

    cutoff = datetime.utcnow() - timedelta(minutes=3)
    rows = (
        db.query(ImportJob)
        .filter(
            ImportJob.status.in_([JobStatus.QUEUED.value, JobStatus.STARTED.value]),
            ImportJob.created_at < cutoff,
        )
        .limit(50)
        .all()
    )
    if not rows:
        return 0
    job_service = JobService(db)
    failed = 0
    for row in rows:
        rq_job_id = str(row.job_id)
        was_started = row.status == JobStatus.STARTED.value
        exc_info = None
        try:
            rq_job = Job.fetch(rq_job_id, connection=redis_conn)
            status = rq_job.get_status()
            exc_info = getattr(rq_job, "exc_info", None)
        except NoSuchJobError:
            status = None
        except Exception:
            # A Redis we cannot reach says nothing about the job.
            continue

        # `.value` FIRST, never bare `str()`. RQ's JobStatus is a `(str, Enum)` and
        # for a mixin enum `__str__` is Enum's, so `str(JobStatus.FAILED)` is
        # "JobStatus.FAILED". Same trap as `project_extraction_recovery_service`.
        status = getattr(status, "value", status)
        status = str(status).lower() if status else None

        if status in ("failed", "canceled", "cancelled"):
            # RQ's own words, so the drawer and the job page say what the
            # FailedJobRegistry says instead of a generic sentence of our own.
            reason = (exc_info or "").strip() or (
                "The import was interrupted; the worker running it stopped."
            )
        elif status is None and not was_started:
            reason = "Import did not start (worker dropped before processing)."
        else:
            continue

        try:
            # S1 (fix round 5): the RAW text, whole. `fail_job` -> `_summarise_error`
            # already caps its own OUTPUT to 2000 chars; slicing the INPUT here first cut
            # a long traceback before the exception line - which sits at the END - was
            # ever read, so the row stored a mid-stack frame instead of the failure.
            job_service.fail_job(rq_job_id, reason)
            failed += 1
        except Exception:
            logger.exception("Failed to fail orphan import job %s", rq_job_id)
    return failed


def _handler_notification_delivery_processor(db, task):
    """Handler for notification_delivery_processor: drain notifications queue (race-safe lpop)."""
    return run_sync_rq_jobs("notifications", max_jobs=20)


def _handler_embedding_job_processor(db, task):
    """Process embedding jobs: Redis RQ first; optional Postgres embedding_queue fallback."""
    rq_result = _run_queue_jobs_impl(settings.embedding_queue_name, max_jobs_per_run=10)
    if not getattr(settings, "embedding_queue_db_fallback_enabled", True):
        return {
            "processed": rq_result.get("processed", 0),
            "queued_redis": rq_result.get("queued", 0),
            "processed_redis": rq_result.get("processed", 0),
            "processed_db": 0,
            "db_fallback_enabled": False,
        }
    db_result = _run_embedding_db_queue_fallback(db, max_jobs_per_run=10)
    return {
        "processed": rq_result.get("processed", 0) + db_result.get("processed", 0),
        "queued_redis": rq_result.get("queued", 0),
        "queued_db_pending_before": db_result.get("queued_db_pending_before", 0),
        "processed_redis": rq_result.get("processed", 0),
        "processed_db": db_result.get("processed", 0),
        "db_fallback_enabled": True,
    }


def _run_embedding_db_queue_fallback(db, max_jobs_per_run: int) -> dict:
    """Run worker for pending ``embedding_queue`` rows (available now), no Redis required."""
    from sqlalchemy import case, func
    from app.models.embeddings import EmbeddingQueue
    from app.services.embedding_worker import process_embedding_queue_item

    now_naive = datetime.utcnow()
    pending_before = (
        db.query(func.count(EmbeddingQueue.id))
        .filter(EmbeddingQueue.status == "pending", EmbeddingQueue.available_at <= now_naive)
        .scalar()
        or 0
    )
    ids = (
        db.query(EmbeddingQueue.id)
        .filter(EmbeddingQueue.status == "pending", EmbeddingQueue.available_at <= now_naive)
        .order_by(
            case((EmbeddingQueue.source_type == "mcp_tool", 0), else_=1).asc(),
            EmbeddingQueue.created_at.asc(),
        )
        .limit(max_jobs_per_run)
        .all()
    )
    ids = [str(row[0]) for row in ids]
    processed = 0
    for qid in ids:
        try:
            process_embedding_queue_item(qid)
            processed += 1
        except Exception as e:
            logger.error("Embedding DB fallback failed for queue id %s: %s", qid, e, exc_info=True)
    return {"processed": processed, "queued_db_pending_before": int(pending_before)}


def _handler_user_sla_daily_summary(db, task):
    """Daily in-app/email summary of SLA performance and outstanding assignments per user."""
    return run_user_sla_daily_summary(db, task)


def _handler_promotion_active_window(db, task):
    """Set promotion is_active from Malaysia today vs inclusive [start_date, end_date]."""
    return PromotionService(db).sync_promotion_active_by_calendar_window()


def _handler_price_tag_auto_collect(db, task):
    """Close a price tag hand-over nobody came back for (r9 D11).

    Reads the configured days off the settings row; 0 turns it off entirely.
    """
    from app.services.price_tag_request_service import PriceTagRequestService

    collected = PriceTagRequestService.run_auto_collect(db)
    return {"collected": collected}


def _handler_coverage_subscription_expiry(db, task):
    """Deactivate coverage subscriptions whose expires_at has passed."""
    from app.services.coverage_subscription_service import CoverageSubscriptionService

    count = CoverageSubscriptionService(db).deactivate_expired_subscriptions()
    return {"deactivated": count}


def _handler_takeover_request_commit(db, task):
    """Commit pending SLA takeover requests whose cooldown has elapsed unchallenged
    (re-validating first); void those whose premise changed. See PLAN-takeover-cooldown."""
    from app.services.sla_takeover_service import SlaTakeoverService

    return SlaTakeoverService(db).commit_due()


def _handler_form_action_commit(db, task):
    """Execute deferred actions whose grace window has closed.

    Two families ride this one sweep, because they share one table: form-SLA actions
    (PLAN-form-sla-undo) and the record actions that replaced the confirmation dialog
    (D7, S6). The lazy commit on read covers anyone watching the form or the record;
    this sweep is what makes "closing the tab still commits" true."""
    from app.services.form_action_service import FormActionService
    import app.services.form_actions  # noqa: F401  (registers the form actions)
    import app.services.record_actions  # noqa: F401  (registers the record actions)

    return FormActionService(db).commit_due()


def _handler_chat_message_resolver(db, task):
    """Fill `respond_ts` / delivery status on chat rows from Respond itself.

    n8n's `sent_at` is its own wall clock, so the round-trip SLA cannot be measured from
    it. This resolves the authoritative Respond-side timestamp per message id, in
    batches, so both ends of the measurement share one clock."""
    from app.services.chat_message_resolver import resolve_pending

    limit = int((task.metadata_ or {}).get("batch_limit", 200)) if task is not None else 200
    return resolve_pending(db, limit=limit)


def _handler_chat_history_reconcile(db, task):
    """One Respond.io delta read per recently active contact (lane CHAT-LOCAL-FIRST, R4).

    The thread renders from `chat_histories`; this is what keeps that table honest when
    the n8n or webhook feed missed a message. Enqueued on the `respond_io` RQ queue rather
    than run inline: this heartbeat runs handlers one after another, and a few hundred
    Respond calls here would stall every scheduled task behind them (review B5). Knobs on
    the task's `metadata`: `activity_days` (7), `concurrency` per workspace key (2),
    `batch_limit` (500). The run's own counts are in the worker log."""
    from app.tasks.chat_reconcile_tasks import enqueue_reconcile

    return enqueue_reconcile(getattr(task, "metadata_", None))


def _handler_chat_latency_watchdog(db, task):
    """Evaluate the WhatsApp round-trip p99 and the per-turn hard ceiling."""
    from app.services.system_health_alert_service import run_chat_latency_watchdog

    return run_chat_latency_watchdog(db)


def _handler_user_downloads_purge(db, task):
    """Delete My Downloads rows and their stored objects past the retention window.

    Nothing purged this table before; chat-history CSV exports make it matter."""
    from app.models.user import SystemSetting
    from app.services.download_service import purge_expired_downloads

    settings = db.query(SystemSetting).first()
    days = int(getattr(settings, "downloads_retention_days", 30) or 30) if settings else 30
    return purge_expired_downloads(db, retention_days=days)


def _handler_api_call_log_prune(db, task):
    """Two-stage retention: NULL payloads at 30d, DELETE rows at 180d.

    Split because payloads are the bulk of the bytes and the shortest-lived
    value, while the metadata row stays useful for trend analysis."""
    from app.models.user import SystemSetting
    from app.services.api_call_log_service import prune_api_call_log

    settings = db.query(SystemSetting).first()
    payload_days = int(getattr(settings, "api_call_log_payload_retention_days", 30) or 30) if settings else 30
    row_days = int(getattr(settings, "api_call_log_row_retention_days", 180) or 180) if settings else 180
    return prune_api_call_log(
        db, payload_retention_days=payload_days, row_retention_days=row_days
    )


def _handler_import_job_rows_prune(db, task):
    """Retention for per-row import outcome detail.

    Only the drill-down rows age out; the job's counts and aggregated breakdown
    live on import_jobs.result and survive forever, so an old job still says what
    it did - you just can no longer page through its individual rows.
    """
    from app.models.user import SystemSetting
    from app.services.import_outcome import prune_import_job_rows

    settings = db.query(SystemSetting).first()
    days = int(getattr(settings, "import_job_rows_retention_days", 90) or 90) if settings else 90
    return prune_import_job_rows(db, retention_days=days)


def _handler_automation_runner(db, task):
    """Heartbeat for automations: dispatch every enabled automation whose next_run_at is due."""
    return AutomationService(db).evaluate_due()


def _handler_form_sla_overdue_scan(db, task):
    """Scan unresolved form SLA trackers, escalate any past due_at to next tier."""
    from app.services.form_sla_service import FormSLAOrchestrator

    return FormSLAOrchestrator(db).scan_overdue_and_escalate()


def _handler_email_outbox_drainer(db, task):
    """Drain pending email_outbox rows respecting guardrail caps."""
    from app.tasks.email_outbox_tasks import drain_email_outbox

    return drain_email_outbox()


def _handler_respond_templates_sync(db, task):
    """Sync Respond.io channels + WhatsApp templates for all active workspaces."""
    from app.services.respond_template_service import run_respond_templates_sync

    return run_respond_templates_sync(db, task)


def _handler_product_discontinued_check(db, task):
    """Report newly-discontinued products (batched) to subscribed staff."""
    from app.services.product_discontinued_notify_service import (
        run_product_discontinued_check,
    )

    return run_product_discontinued_check(db, task)


def _handler_project_staleness_sweep(db, task):
    """Move every live project up or off the staleness ladder (AC-H5, AC-H6).

    Runs on this existing heartbeat rather than introducing a scheduler of its own. The
    sweep notifies on a level CHANGE only, so a daily cadence does not re-nag.
    """
    from app.services.project_staleness_service import sweep

    return sweep(db)


def _handler_scm_analytics(db, task):
    """Nightly SCM analytics full recompute (demand + ABC/XYZ + supplier performance).

    Runs ``run_analytics`` on the scheduler/worker process. ``run_analytics`` opens its
    own ``scm.scm_analytics_run`` log row up-front and, on any failure, stamps that row
    ``status='failed'`` + ``error_text`` before re-raising - so a bad run is always
    observable in the run log. We re-raise here so the scheduled-task run is marked
    failed too; the heartbeat's outer guard (``run_due_tasks``) keeps the scheduler
    process alive, so a failed analytics run never crashes the scheduler.

    Optional ``scheduled_tasks.metadata`` keys tune the run with no code change:
      * ``scope`` - dict forwarded to run_analytics (product_ids / supplier_ids / ...).
      * ``config`` - dict forwarded to run_analytics (e.g. ``as_of``).
    Absent metadata => full-catalog run as of today (the nightly default).
    """
    from app.services.scm.analytics_service import run_analytics

    metadata = getattr(task, "metadata_", None)
    scope = metadata.get("scope") if isinstance(metadata, dict) else None
    config = metadata.get("config") if isinstance(metadata, dict) else None
    try:
        return run_analytics(db, scope=scope, config=config)
    except Exception:
        # run_analytics already recorded status='failed' + error_text on its run-log
        # row; re-raise so the scheduled-task run is failed too. The heartbeat catches
        # this so the scheduler keeps ticking.
        logger.exception("Scheduled SCM analytics run failed")
        raise


def _scm_reorder_run_kwargs(metadata, run_day):
    """Turns the ``scm_reorder_run`` task's metadata into ``create_run`` kwargs (#1340).

    Validates ``metadata`` with ``ScmReorderRunTaskMetadata`` (the model the PATCH path
    already refused bad values with), resolves the relative window against ``run_day`` -
    the scheduler's own day in the task's timezone, so a daily run never goes stale - and
    then builds a ``CreateReorderRunRequest`` so ``require_start_on_or_before_end`` and
    ``refuse_so_numbers_on_a_dealer_run`` run exactly as they do on ``POST /reorder-runs``.
    With no keys set the result is today's call, unchanged: every warehouse, every
    product, both demand legs, no window, market off.
    """
    from app.schemas.scheduled_task import ScmReorderRunTaskMetadata
    from app.schemas.scm_reorder import CreateReorderRunRequest

    md = ScmReorderRunTaskMetadata(**(metadata or {}))
    req = CreateReorderRunRequest(
        warehouse_codes=md.warehouse_codes or [],
        product_codes=md.product_codes or [],
        include_market=bool(md.include_market),
        plan_horizon_start=(
            run_day + timedelta(days=md.horizon_start_days)
            if md.horizon_start_days is not None else None
        ),
        plan_horizon_date=(
            run_day + timedelta(days=md.horizon_end_days)
            if md.horizon_end_days is not None else None
        ),
        demand_class=md.demand_class,
    )
    return {
        "warehouse_codes": list(req.warehouse_codes),  # empty = all active (M8-D1)
        "buy_scope": "warehouse",  # per-warehouse planning: each buy ties to a real WH
        "include_market": req.include_market,
        "product_codes": list(req.product_codes) or None,
        "plan_horizon_start": req.plan_horizon_start,
        "plan_horizon_date": req.plan_horizon_date,
        "demand_class": req.demand_class,
        "enqueue": False,  # run inline on this process
    }


def _handler_scm_reorder_run(db, task):
    """Daily scheduled reorder planning run (M8-D1/D6/D8; scope config #1340).

    Plans the scope stated on the task's own config page - defaulting, when unset, to
    ALL active warehouses/products, both demand legs, no window and market insight OFF -
    then funds EVERYTHING (full budget) unless a ``budget`` cap is configured, so the
    morning snapshot opens fully within-budget by default (M8-D6). Runs the pipeline
    INLINE on the scheduler/worker process (like ``_handler_scm_analytics`` runs
    ``run_analytics`` inline) so the run + the funding split are both complete +
    persisted when the handler returns; no dependence on a live RQ worker draining the
    run afterwards.

    Optional ``scheduled_tasks.metadata`` keys tune the run with no code change (the
    "configurable time" is the row's ``start_at``/interval; these tune the run body -
    see ``ScmReorderRunTaskMetadata`` for the validation each one gets):
      * ``warehouse_codes`` - narrows the run to these warehouses; absent/empty = all.
      * ``product_codes``   - narrows the run to these products; absent/empty = all.
      * ``demand_class``    - ``project`` or ``retail``; absent = both legs.
      * ``horizon_start_days`` / ``horizon_end_days`` - the "sales orders needed"
        window, in days from the run day (in the task's own timezone); either absent
        is unbounded on that side.
      * ``budget``        - a numeric cash cap for the scheduled split; null/absent =>
        full budget (fund everything, the default).
      * ``include_market`` - market-trend priority factor (default false; market never
        enters a run per M8-D5 unless explicitly opted in here).
    """
    from zoneinfo import ZoneInfo

    from app.services.scm import reorder_run_service as reorder_svc

    metadata = getattr(task, "metadata_", None)
    md = metadata if isinstance(metadata, dict) else {}

    # The run day is the task's own local day: the run fires at 06:00 KL, which is 22:00
    # UTC the day before, so a UTC "today" would put a 0-day window on yesterday.
    run_day = datetime.now(ZoneInfo(getattr(task, "timezone", None) or "UTC")).date()
    # Validates every key first; a bad stored value fails the run here, before anything
    # is created, rather than planning a scope nobody asked for.
    kwargs = _scm_reorder_run_kwargs(md, run_day)
    include_market = kwargs["include_market"]
    budget = md.get("budget")  # validated above: a non-negative number or absent

    created = reorder_svc.create_run(db, **kwargs)
    run_id = created["run_id"]
    reorder_svc.run_reorder(run_id, db=db)

    if isinstance(budget, (int, float)) and not isinstance(budget, bool):
        funding = reorder_svc.apply_run_budget(db, run_id, float(budget))
    else:
        # Full budget (M8-D6): fund every costed buy regardless of a numeric cap.
        funding = reorder_svc.apply_run_budget(db, run_id, None, full=True)

    # The daily low stock email is an automation (PLAN-excel-preview-26sep S1; owner ruling
    # 26 Sep, Q1): fire its trigger with this run's link. Best effort - a failure here is
    # logged and never fails a run that has already been planned and funded. The run and its
    # funding are committed by now, so the rollback only drops dispatch's own work; without it
    # a database error leaves this session aborted and the scheduler's `finish_run` on it
    # fails (review S1). `dispatch_ready` skips a run that did not complete (review B1).
    try:
        from app.services.scm import low_stock_report_service

        low_stock_report_service.dispatch_ready(db, run_id)
    except Exception:
        db.rollback()
        logger.exception("low_stock_report_ready dispatch failed for run %s", run_id)
    return {"run_id": run_id, "include_market": include_market, **funding}


def _drain_email_outbox_tick():
    """APScheduler tick wrapper. Owns its own DB session (drain_email_outbox handles errors).

    That session is not a `scheduler_session`, so the tick stamps the `scheduler` audit
    actor itself (identity S0, AC-10); the drainer's session reads it from the context.
    """
    from app.audit_context import AuditActor, actor_scope

    try:
        from app.tasks import email_outbox_tasks

        with actor_scope(AuditActor(actor_type="scheduler", job_id="email_outbox_drainer")):
            summary = email_outbox_tasks.drain_email_outbox()
        if summary.get("picked"):
            logger.info("Email outbox drainer tick: %s", summary)
    except Exception as e:
        logger.error("Email outbox drainer tick failed: %s", e, exc_info=True)


def _ai_trace_sweep_tick():
    """APScheduler tick: delete expired AI assistant traces (M2 retention).
    Owns its own DB session; sweep is best-effort and never raises."""
    try:
        from app.services.ai_trace import sweep_expired_traces

        with scheduler_session("ai_trace_sweep") as db:
            sweep_expired_traces(db)
    except Exception as e:
        logger.error("AI trace sweep tick failed: %s", e, exc_info=True)


def _spo_container_relink_sweep_tick():
    """APScheduler tick: relink SPO allocations whose shipment arrived after
    they did (S5, review re-check, 2026-09-06). Owns its own DB session;
    best-effort and never raises. Then refreshes the stored received figures
    of every container not yet fully received (SPO-DEDUPE-ALL), after the
    relink so a newly linked allocation counts the same night."""
    try:
        from app.services.rules.shipping_order_rules import (
            nightly_refresh_open_containers,
            nightly_relink_all_containers,
        )

        with scheduler_session("spo_container_relink_sweep") as db:
            relinked = nightly_relink_all_containers(db)
            if relinked:
                db.commit()
            refreshed = nightly_refresh_open_containers(db)
            logger.info("SPO container sweep: relinked %s, refreshed %s open containers", relinked, refreshed)
    except Exception as e:
        logger.error("SPO container relink sweep tick failed: %s", e, exc_info=True)


def _chatbot_delegated_sweep_tick():
    """APScheduler tick: fail turns an n8n lane took over and never finished (AC-260).
    Owns its own DB session; the sweep is best-effort and never raises."""
    try:
        from app.services.chatbot_turn_sweep import sweep_stalled_delegated_turns

        with scheduler_session("chatbot_delegated_sweep") as db:
            sweep_stalled_delegated_turns(db)
    except Exception as e:
        logger.error("Chatbot delegated sweep tick failed: %s", e, exc_info=True)


def _ideation_idle_sweep_tick():
    """APScheduler tick: one WhatsApp reminder at 24h idle on an open ideation
    draft, then close (S4, AC-1401 to AC-1408). Owns its own DB session;
    best-effort and never raises - same shape as
    `_chatbot_delegated_sweep_tick`."""
    try:
        from app.services.ideation_turn_service import sweep_idle_ideation_drafts

        with scheduler_session("ideation_idle_sweep") as db:
            sweep_idle_ideation_drafts(db)
    except Exception as e:
        logger.error("Ideation idle sweep tick failed: %s", e, exc_info=True)


def _ideation_status_events_tick():
    """APScheduler tick: pull the shared service's idea status-event feed and send the
    ``ideation_status_update`` template to each requester (#1355, AC-IS017). Owns its
    own DB session; best-effort and never raises - same shape as
    `_ideation_idle_sweep_tick`."""
    try:
        from app.services import ideation_status_update_service

        with scheduler_session("ideation_status_events_poll") as db:
            ideation_status_update_service.poll_ideation_status_events(db)
    except Exception as e:
        logger.error("Ideation status events tick failed: %s", e, exc_info=True)


def _autocount_pull_advance_tick():
    """APScheduler tick: drive the AutoCount pull build -> preview transition
    server-side (D27), so a snapshot that finishes building has its preview
    enqueued within 30s whether or not anyone has the pull page open - see
    PLAN-autocount-pull-server-advance.md for the 23 Sep 2026 incident this
    closes. Owns its own DB session; best-effort and never raises."""
    try:
        from app.services.autocount_pull_service import advance_building_pulls

        with scheduler_session("autocount_pull_advance") as db:
            advance_building_pulls(db)
    except Exception as e:
        logger.error("AutoCount pull advance tick failed: %s", e, exc_info=True)


def _run_queue_jobs_impl(queue_name: str, max_jobs_per_run: int) -> dict:
    """Generic queue processor used by scheduled task heartbeat."""
    return run_sync_rq_jobs(queue_name, max_jobs_per_run)


def process_pending_integration_logs():
    """
    Process pending integration logs that are ready for retry.
    This function is called periodically by the scheduler.
    """
    try:
        with scheduler_session("integration_log_retry") as db:
            service = IntegrationLogService(db)
            result = service.process_pending_logs()

            if result["processed"] > 0:
                logger.info(
                    f"Processed {result['processed']} integration logs: "
                    f"{result['succeeded']} succeeded, {result['failed']} failed"
                )
    except Exception as e:
        logger.error(f"Error processing pending integration logs: {str(e)}", exc_info=True)


def _scheduled_tasks_heartbeat():
    """Heartbeat: run due DB-configured scheduled tasks and persist run logs."""
    try:
        with scheduler_session("scheduled_tasks_heartbeat") as db:
            run_due_tasks(db)
    except Exception as e:
        logger.error("Scheduled tasks heartbeat failed: %s", str(e), exc_info=True)


def register_task_handlers():
    """Register all scheduled task handlers. Safe to call multiple times."""
    register_handler("integration_log_retry", _handler_integration_log_retry)
    register_handler("import_job_processor", _handler_import_job_processor)
    register_handler("notification_delivery_processor", _handler_notification_delivery_processor)
    register_handler("embedding_job_processor", _handler_embedding_job_processor)
    register_handler("user_sla_daily_summary", _handler_user_sla_daily_summary)
    register_handler("promotion_active_window", _handler_promotion_active_window)
    register_handler("price_tag_auto_collect", _handler_price_tag_auto_collect)
    register_handler("respond_contacts_sync", run_respond_contacts_sync)
    register_handler("respond_templates_sync", _handler_respond_templates_sync)
    register_handler("automation_runner", _handler_automation_runner)
    register_handler("form_sla_overdue_scan", _handler_form_sla_overdue_scan)
    register_handler("email_outbox_drainer", _handler_email_outbox_drainer)
    register_handler("attachment_storage_audit", run_attachment_storage_audit)
    register_handler("product_discontinued_check", _handler_product_discontinued_check)
    register_handler("coverage_subscription_expiry", _handler_coverage_subscription_expiry)
    register_handler("takeover_request_commit", _handler_takeover_request_commit)
    register_handler("form_action_commit", _handler_form_action_commit)
    register_handler("n8n_liveness_ping", lambda db, task: run_n8n_liveness_ping(db))
    register_handler("system_health_watchdog", lambda db, task: run_health_watchdog(db))
    register_handler("system_health_daily_digest", lambda db, task: run_health_daily_digest(db, task))
    register_handler("chat_message_resolver", _handler_chat_message_resolver)
    register_handler("user_downloads_purge", _handler_user_downloads_purge)
    register_handler("api_call_log_prune", _handler_api_call_log_prune)
    register_handler("import_job_rows_prune", _handler_import_job_rows_prune)
    register_handler("chat_latency_watchdog", _handler_chat_latency_watchdog)
    register_handler("chat_history_reconcile", _handler_chat_history_reconcile)
    register_handler("project_staleness_sweep", _handler_project_staleness_sweep)
    register_handler("scm_analytics", _handler_scm_analytics)
    register_handler("scm_reorder_run", _handler_scm_reorder_run)


def start_scheduler():
    """
    Start the APScheduler background scheduler.
    Uses a single heartbeat to execute due DB-configured tasks.
    """
    register_task_handlers()

    scheduler = BackgroundScheduler()

    # Single heartbeat every 10s: query due scheduled_tasks, run handlers, persist run logs
    scheduler.add_job(
        _scheduled_tasks_heartbeat,
        trigger=IntervalTrigger(seconds=10),
        id="scheduled_tasks_heartbeat",
        name="Scheduled tasks heartbeat",
        replace_existing=True,
    )

    # Email outbox drainer: read interval from SystemSetting (default 5s) so password-reset
    # and approval-link emails leave the queue near-instantly while attachment-linkage
    # bursts get throttled by the per-recipient cap inside drain_email_outbox.
    drain_seconds = 5
    try:
        with scheduler_session("email_outbox_drainer_interval") as db:
            from app.models.user import SystemSetting

            settings_row = db.query(SystemSetting).first()
            if settings_row and getattr(settings_row, "email_outbox_drain_interval_seconds", None):
                drain_seconds = int(settings_row.email_outbox_drain_interval_seconds)
    except Exception as e:
        logger.warning("Email outbox drainer interval lookup failed (%s); using 5s default.", e)
    scheduler.add_job(
        _drain_email_outbox_tick,
        trigger=IntervalTrigger(seconds=max(1, drain_seconds)),
        id="email_outbox_drainer",
        name="Email outbox drainer",
        replace_existing=True,
    )

    # AI assistant trace retention sweep (M2): daily. Deletes ok traces past
    # ai_trace_ttl_days and error/flagged past ai_trace_error_ttl_days.
    scheduler.add_job(
        _ai_trace_sweep_tick,
        trigger=IntervalTrigger(hours=24),
        id="ai_trace_sweep",
        name="AI assistant trace retention sweep",
        replace_existing=True,
    )

    # Chatbot delegated sweep (AC-260): every minute. A turn an n8n lane took over and
    # never finished stays `delegated` for ever - a ghost on the trace screen that Retry
    # cannot touch, because a manual retry needs a FAILED turn (R4).
    scheduler.add_job(
        _chatbot_delegated_sweep_tick,
        trigger=IntervalTrigger(minutes=1),
        id="chatbot_delegated_sweep",
        name="Chatbot delegated turn sweep",
        replace_existing=True,
    )

    # Ideation idle draft sweep (S4, AC-1408): every 15 minutes. One WhatsApp
    # reminder at 24h idle on an open ideation draft, then close.
    scheduler.add_job(
        _ideation_idle_sweep_tick,
        trigger=IntervalTrigger(minutes=15),
        id="ideation_idle_sweep",
        name="Ideation idle draft sweep",
        replace_existing=True,
    )

    # Ideation status events poll (#1355, AC-IS017): every 60s. Pulls the shared
    # service's idea status-event feed and sends the ideation_status_update template.
    scheduler.add_job(
        _ideation_status_events_tick,
        trigger=IntervalTrigger(seconds=60),
        id="ideation_status_events_poll",
        name="Ideation status events poll",
        replace_existing=True,
    )

    # SPO container relink sweep (S5, review re-check, 2026-09-06): daily. An
    # allocation whose shipment shows up AFTER the allocation was pushed
    # never otherwise gets linked - `relink_allocations_for_container`'s own
    # docstring already promised this sweep; this is where it runs.
    scheduler.add_job(
        _spo_container_relink_sweep_tick,
        trigger=IntervalTrigger(hours=24),
        id="spo_container_relink_sweep",
        name="SPO container relink sweep",
        replace_existing=True,
    )

    # AutoCount pull server advance (D27): every 30s. A pull's build -> preview
    # transition used to happen only inside a browser's status poll; this tick is
    # what makes it happen with no tab open (PLAN-autocount-pull-server-advance.md).
    scheduler.add_job(
        _autocount_pull_advance_tick,
        trigger=IntervalTrigger(seconds=30),
        id="autocount_pull_advance",
        name="AutoCount pull advance",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: scheduled tasks heartbeat (every 10s), email outbox drainer "
        "(every %ds), AI trace sweep (daily), chatbot delegated sweep (every minute), "
        "SPO container relink sweep (daily), AutoCount pull advance (every 30s)",
        max(1, drain_seconds),
    )
    return scheduler
