"""FastAPI application entry point."""
import os
# Load .env with override=True so file values beat any stale shell env
# (e.g. a STORAGE_DEFAULT_PROVIDER exported earlier in the session).
# SORENTO_ENV_FILE overrides which dotenv gets loaded, so tests can point at
# a private file instead of flipping the backend's real .env under a
# running dev server.
from pathlib import Path as _Path


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv as _load_dotenv
    except ImportError:
        return
    _override_path = os.environ.get("SORENTO_ENV_FILE")
    if _override_path:
        _resolved_override = _Path(_override_path)
        if not _resolved_override.is_absolute():
            _resolved_override = _Path(__file__).resolve().parent.parent / _resolved_override
        if not _resolved_override.exists():
            # A typo here must never fall back to the real .env - that would
            # point a test run (or anything else setting this) at the live
            # stack's database with no warning. See app.config's equivalent.
            raise RuntimeError(
                f"SORENTO_ENV_FILE={_override_path!r} does not resolve to an "
                f"existing file (looked for {_resolved_override}); refusing "
                f"to fall back to .env."
            )
        _load_dotenv(_resolved_override, override=True)
        return
    _env_path = _Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path, override=True)
    else:
        _load_dotenv(override=True)


_load_env_file()

from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import logging
from app.config import settings
from app.api.v1 import api_router
from app.services.error_handler import AppException
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.idempotency_middleware import IdempotencyMiddleware
from app.middleware.api_call_log_middleware import ApiCallLogMiddleware
from app.middleware.impersonation_ended_middleware import (
    IMPERSONATION_ENDED_HEADER,
    ImpersonationEndedMiddleware,
)
from app.middleware.relative_redirect_middleware import (
    RelativeRedirectMiddleware,
    SlashTolerantPathMiddleware,
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Verbose auth-dependency logging only in debug - at DEBUG it logs token length +
# first chars, which must never run in production (security audit 2026-06-29).
if settings.debug:
    logging.getLogger('app.dependencies').setLevel(logging.DEBUG)

# DEV-LOGIN-BYPASS: crash at import (never just warn) when DEV_AUTO_LOGIN is on anywhere but
# a local uvicorn run, and log a loud banner when it is legitimately active.
from app.services.dev_login import (
    assert_safe_startup as _assert_dev_login_safe,
    running_in_container as _running_in_container,
    running_under_gunicorn as _running_under_gunicorn,
)

_assert_dev_login_safe(
    enabled=settings.dev_auto_login,
    environment=settings.environment,
    environment_explicit="environment" in settings.model_fields_set,
    secret=settings.dev_auto_login_secret,
    under_gunicorn=_running_under_gunicorn(),
    in_container=_running_in_container(),
)

# Create FastAPI app
app = FastAPI(
    title="Sorento CRM API",
    description="FastAPI backend for Sorento CRM",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add logging middleware
app.add_middleware(LoggingMiddleware)

# A view-as header the auth dependency could not honour gets X-Impersonation-Ended: 1,
# so the client drops its banner instead of labelling the admin's own data as the
# target's. See app/middleware/impersonation_ended_middleware.py.
app.add_middleware(ImpersonationEndedMiddleware)

# A list route called without its trailing slash is served as the slashed route, no 307;
# any redirect that still happens to our own host gets a path-only Location.
# See app/middleware/relative_redirect_middleware.py.
app.add_middleware(RelativeRedirectMiddleware)
app.add_middleware(SlashTolerantPathMiddleware, routes=app.router.routes)

# Request idempotency: dedupe replays of allowlisted action endpoints (double-click,
# proxy retry, two tabs) so harmful side effects (SLA assignment, Respond sends,
# status transitions) execute exactly once. See app/middleware/idempotency_middleware.py.
app.add_middleware(IdempotencyMiddleware)
# Telemetry for /api/v1/external/* - total coverage by construction, so a new
# external route is logged the day it is added rather than opt-in per endpoint.
app.add_middleware(ApiCallLogMiddleware)

# Compress JSON responses. List endpoints (products, attachments) return 50-70KB
# of JSON per page; uncompressed transfer was ~1s of "content download" on prod.
# gzip cuts these payloads ~8-10x. minimum_size skips tiny bodies where the
# CPU cost outweighs the win.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    # Only expose headers the browser actually needs to read; "*" leaked internal
    # headers cross-origin (security audit 2026-06-29).
    expose_headers=["Content-Type", "Content-Length", "Content-Disposition", IMPERSONATION_ENDED_HEADER],
)

# Global exception handler for AppException
@app.exception_handler(AppException)
async def app_exception_handler(request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail
    )

# Global exception handler for all unhandled exceptions
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all unhandled exceptions and log them."""
    error_logger = logging.getLogger(__name__)
    import traceback
    error_traceback = traceback.format_exc()
    error_logger.error(f"Unhandled exception: {type(exc).__name__}: {str(exc)}")
    error_logger.error(f"Request URL: {request.url}")
    error_logger.error(f"Request method: {request.method}")
    error_logger.error(f"Traceback:\n{error_traceback}")
    
    # Never leak exception messages/types to clients in production - they aid
    # reconnaissance (DB schema, file paths, library hints). Full detail is logged
    # server-side above; only expose it when debug is explicitly on (security audit 2026-06-29).
    content = {"message": "Internal server error"}
    if settings.debug:
        content["detail"] = str(exc)
        content["type"] = type(exc).__name__
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=content,
        headers=_cors_headers_for(request),
    )


def _cors_headers_for(request: Request) -> dict:
    """The CORS headers this response would have got if it had come back normally.

    Starlette runs `Exception` handlers in `ServerErrorMiddleware`, which sits OUTSIDE the
    user middleware stack - so `CORSMiddleware` never sees a 500 raised from a route and the
    response goes back with no `Access-Control-Allow-Origin`. The browser then refuses to
    read it and reports `TypeError: Failed to fetch`, which looks like the network died.

    The cost is not cosmetic: a real, logged, diagnosable 500 (a unique-constraint violation
    on an upload) reached the user as "Failed to fetch", with the actual cause visible only
    in the server log. Whoever is looking at the screen deserves the status code at least.

    Echoes the request's own origin, and only when it is on the configured allow-list, so
    this cannot become a wildcard that leaks a credentialed response to any origin.
    """
    origin = request.headers.get("origin")
    if not origin:
        return {}
    allowed = settings.cors_origins_list
    if origin not in allowed and "*" not in allowed:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }

# Validation error handler to see detailed errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    validation_logger = logging.getLogger(__name__)
    validation_logger.error(f"Validation error: {exc.errors()}")
    validation_logger.error(f"Request URL: {request.url}")
    validation_logger.error(f"Request method: {request.method}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exc.errors())}
    )

# Triggers eligibility registrations side-effects.
from app.services import lookup_eligibility_registrations as _lookup_eligibility_registrations  # noqa: F401

from app.services.lookup_write_listener import register_lookup_write_listeners
register_lookup_write_listeners()

# Multi-company isolation: fail-closed SELECT filter + insert auto-stamp on every
# CompanyScopedMixin model. Registered here (import time, not startup_event) so it
# is active in the API; the worker registers it via its own import path.
from app.services.company_scope import register_company_scope_listeners
register_company_scope_listeners()

# Dealer Kit collection rules are evaluated by the SAME rule engine as automation
# triggers, so its `product` fact source has to be on the registry before the
# first /rule-facts request - otherwise the RuleBuilder renders an empty field
# list and a Designer cannot author a collection at all.
from app.services.dealer_kit.product_facts import register_product_facts
register_product_facts()


def _register_activities_adapters() -> None:
    """Register per-entity Activities & Notes adapters at process boot.

    Each consuming module hands its visibility / on_post / contacts hooks
    to the activities service via this single registration point so the
    generic ``/api/v1/activities/{entity_type}/{entity_id}/...`` routes
    can route module-specific behaviour without coupling.
    """
    try:
        from app.services.activities_registry import (
            ActivitiesAdapter,
            register_activities_adapter,
        )
        from app.services import tickets_service

        register_activities_adapter(
            ActivitiesAdapter(
                entity_type="ticket",
                permission_view="tickets.tickets.view",
                permission_post="tickets.tickets.view",
                get_respond_contacts=tickets_service.respond_contacts_for,
                on_post=lambda db, ticket_id, actor_id, body_html: tickets_service.handle_activity_posted(
                    db, ticket_id, actor_id, body_html
                ),
                can_view=tickets_service.can_view,
            )
        )
        logging.info("Activities adapter registered: ticket")

        from app.services import project_activity_service

        register_activities_adapter(
            ActivitiesAdapter(
                entity_type=project_activity_service.ENTITY_TYPE,
                permission_view="projects.projects.view",
                permission_post="projects.projects.view",
                # Posting is deliberately gated on VIEW, not edit: a colleague who can see a
                # project should be able to add "developer told me the tender closed" without
                # being able to change its numbers. Silencing observers loses the information
                # the pursuit record exists to hold.
                get_respond_contacts=project_activity_service.respond_contacts_for,
                # Without a can_view the generic gate is a no-op and any project id is
                # readable by any holder of the view permission, across companies.
                can_view=project_activity_service.can_view,
                on_post=lambda db, project_id, actor_id, body_html: (
                    project_activity_service.note_user_activity(
                        db, project_id=project_id, actor_id=actor_id
                    )
                ),
            )
        )
        logging.info("Activities adapter registered: project")
    except Exception as e:  # noqa: BLE001
        logging.error(
            f"Failed to register activities adapters: {str(e)}", exc_info=True
        )


# Run synchronously at import time so it precedes the first request.
_register_activities_adapters()

# Include API routes. The company-scope resolver runs as a router-level dependency
# for EVERY /api/v1/* request: it resolves the caller's four-state scope (JWT active
# company / X-API-Key contact / system) and stamps it onto the request DB session so
# the do_orm_execute filter enforces isolation without editing each route. It shares
# the route's session (get_db is cached per-request) and never 500s (fail-closed).
from app.services.company_scope_resolver import apply_company_scope
app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(apply_company_scope)])

# Initialize scheduler for background tasks
scheduler = None

@app.on_event("startup")
async def startup_event():
    """Startup event: initialize scheduler and audit listeners."""
    global scheduler
    # Build the storage client once at boot instead of letting the cost land on
    # the first request this worker happens to serve (see storage_router).
    try:
        from app.services.storage_router import warm_backends

        warm_backends()
    except Exception as e:  # noqa: BLE001 - never block boot on storage config
        logging.warning("Storage backend warm-up failed: %s", e)
    try:
        from app.services.audit_service import register_audit_listeners
        register_audit_listeners()
        logging.info("Audit listeners registered")
    except Exception as e:
        logging.error(f"Failed to register audit listeners: {str(e)}", exc_info=True)
    try:
        from app.services.embedding_change_listener import register_embedding_change_listeners
        register_embedding_change_listeners()
        logging.info("Embedding change listeners registered")
    except Exception as e:
        logging.error(f"Failed to register embedding change listeners: {str(e)}", exc_info=True)
    try:
        from app.services.product_spec_change_listener import register_product_spec_listeners
        register_product_spec_listeners()
        logging.info("Product spec listeners registered")
    except Exception as e:
        logging.error(f"Failed to register product spec listeners: {str(e)}", exc_info=True)
    try:
        from app.services.product_spec_write import register_spec_write_backstop
        register_spec_write_backstop()
        logging.info("Spec write backstop registered")
    except Exception as e:
        logging.error(f"Failed to register spec write backstop: {str(e)}", exc_info=True)
    try:
        from app.services.project_order_inquiry_service import (
            register_order_inquiry_post_commit_dispatch,
        )
        register_order_inquiry_post_commit_dispatch()
        logging.info("Order inquiry post-commit dispatch registered")
    except Exception as e:
        logging.error(
            f"Failed to register order inquiry post-commit dispatch: {str(e)}",
            exc_info=True,
        )
    try:
        from app.services.order_inquiry_reserve_service import (
            register_order_inquiry_reserve_post_commit_dispatch,
        )
        register_order_inquiry_reserve_post_commit_dispatch()
        logging.info("Order inquiry reserve post-commit dispatch registered")
    except Exception as e:
        logging.error(
            f"Failed to register order inquiry reserve post-commit dispatch: {str(e)}",
            exc_info=True,
        )
    try:
        # The status engine ships with an empty registry; every entity arrives from
        # a module. `inbound_shipment` is the first adopter in this repo, and it
        # registers a CHECKPOINT TIMELINE rather than a single-status graph - see
        # the module docstring for why there is no `status_id` column.
        from app.status_engine.entities.inbound_shipment import register as register_inbound_shipment_status
        register_inbound_shipment_status()
    except Exception as e:
        logging.error(f"Failed to register status entities: {str(e)}", exc_info=True)
    # Register task handlers unconditionally so manual "run now" works on API
    # containers even when scheduler ticks are gated to the worker container.
    try:
        from app.scheduler.task_scheduler import register_task_handlers
        register_task_handlers()
        logging.info("Scheduled task handlers registered")
    except Exception as e:
        logging.error(f"Failed to register task handlers: {str(e)}", exc_info=True)

    # Scheduler is gated so only the dedicated `worker` container runs it.
    # Blue/green API containers must NOT fire cron ticks (would double-process).
    if os.getenv("ENABLE_SCHEDULER", "false").lower() == "true":
        try:
            from app.scheduler.task_scheduler import start_scheduler
            scheduler = start_scheduler()
            logging.info("Background scheduler started successfully")
        except Exception as e:
            logging.error(f"Failed to start scheduler: {str(e)}", exc_info=True)
    else:
        logging.info("Background scheduler disabled (ENABLE_SCHEDULER != true)")
    try:
        from app.database import SessionLocal
        from app.services.mcp_tool_registry_service import sync_catalog
        _db = SessionLocal()
        try:
            report = sync_catalog(_db)
            _db.commit()
            logging.info(
                "MCP tool catalog synced at startup: added=%d updated=%d deactivated=%d",
                report.added,
                report.updated,
                report.deactivated,
            )
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Failed to sync MCP tool catalog at startup: {str(e)}", exc_info=True)

    try:
        # Seeds a denied agent_field_access row per gated field, so an admin opens
        # the screen to a complete checklist. A gate whose fields never appear
        # anywhere to tick is indistinguishable from a broken feature.
        from app.database import SessionLocal
        from app.services import container_status_bootstrap
        _db = SessionLocal()
        try:
            container_status_bootstrap.run(_db)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Container status bootstrap failed: {str(e)}", exc_info=True)

    try:
        from app.database import SessionLocal
        from app.rbac.permission_registry import sync_permissions
        _db = SessionLocal()
        try:
            created = sync_permissions(_db)
            logging.info("RBAC permissions synced at startup: added=%d", created)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Failed to sync RBAC permissions at startup: {str(e)}", exc_info=True)

    try:
        from app.database import SessionLocal
        from app.services.email_event_registry import seed_event_configs
        _db = SessionLocal()
        try:
            created = seed_event_configs(_db)
            logging.info("Email event configs seeded at startup: added=%d", created)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Failed to seed email event configs at startup: {str(e)}", exc_info=True)

    try:
        from app.database import SessionLocal
        from app.services import it_support_bootstrap
        _db = SessionLocal()
        try:
            it_support_bootstrap.run(_db)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"IT support bootstrap failed at startup: {str(e)}", exc_info=True)

    try:
        from app.database import SessionLocal
        from app.services import project_mcp_bootstrap
        _db = SessionLocal()
        try:
            # Runs after sync_catalog so the mcp_tools rows exist: enables the read-only
            # project tools for the in-app assistant without an admin visiting a settings
            # screen (AC-K1). Additive and idempotent.
            project_mcp_bootstrap.run(_db)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Project MCP bootstrap failed at startup: {str(e)}", exc_info=True)

    try:
        from app.database import SessionLocal
        from app.services import outstanding_report_bootstrap
        _db = SessionLocal()
        try:
            # Runs after sync_catalog so the crm_outstanding_report row exists:
            # enables it for the in-app assistant without an admin visiting a
            # settings screen (same mechanism as project_mcp_bootstrap above).
            # Additive and idempotent.
            outstanding_report_bootstrap.run(_db)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Outstanding report bootstrap failed at startup: {str(e)}", exc_info=True)

    # AC-65 STRUCK (security N4, Phase 3): `crm_low_stock_report` is deliberately NOT added
    # to the in-app assistant's `enabled_tools`. The assistant force-empties
    # `contact_id`/`space_id`, which this route requires (422), so the tool could only ever
    # 403/422 there - and a SIDE-EFFECTING tool (it creates a reorder run) must not sit on
    # the assistant's read list at all. No bootstrap.

    # AC-1642 STRUCK (security B1, Phase 3): `crm_sales_report` is deliberately NOT added to
    # the in-app assistant's `enabled_tools` either - the assistant is a DIFFERENT auth
    # boundary from the route's `order_management.orders.view` RBAC permission, and a staff
    # member who is 403 on the route could otherwise read the money figures through the
    # assistant instead, across every company. No bootstrap.

    try:
        from app.database import SessionLocal
        from app.services import project_seed_service
        _db = SessionLocal()
        try:
            # Additive only: never updates or removes a row, so a renamed status or a
            # deleted project type survives every restart.
            project_seed_service.run(_db)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Project Sales seed failed at startup: {str(e)}", exc_info=True)

    try:
        from app.database import SessionLocal
        from app.services.sales import sales_seed_service
        _db = SessionLocal()
        try:
            # Additive only, same rule as the Project Sales seed above: a renamed stage or
            # a deactivated one survives every restart.
            sales_seed_service.run(_db)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Sales module seed failed at startup: {str(e)}", exc_info=True)

    try:
        from app.database import SessionLocal
        from app.services import record_action_bootstrap
        _db = SessionLocal()
        try:
            record_action_bootstrap.run(_db)
        finally:
            _db.close()
    except Exception as e:
        logging.error(f"Record-action bootstrap failed at startup: {str(e)}", exc_info=True)

    # AI assistant wishlist clustering - register the callable so ops can wire
    # it to the existing background scheduler (or cron). We do not schedule it
    # by default to avoid surprise traffic on environments without an OpenAI
    # API key. To enable nightly runs, add a `scheduled_tasks` row keyed
    # "ai_wishlist_clustering" or invoke `python -m app.jobs.ai_wishlist_clustering`.
    try:
        from app.services.scheduled_task_service import register_handler
        from app.jobs.ai_wishlist_clustering import run_clustering_job

        def _handler_ai_wishlist_clustering(db, _task):
            return run_clustering_job(db=db)

        register_handler("ai_wishlist_clustering", _handler_ai_wishlist_clustering)
        logging.info("Registered scheduled task handler: ai_wishlist_clustering")
    except Exception as e:
        logging.error(f"Failed to register ai_wishlist_clustering handler: {str(e)}", exc_info=True)

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event: stop scheduler."""
    global scheduler
    if scheduler:
        try:
            scheduler.shutdown()
            logging.info("Background scheduler stopped")
        except Exception as e:
            logging.error(f"Error stopping scheduler: {str(e)}", exc_info=True)


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Sorento CRM API", "version": "1.0.0"}


def _git_sha() -> str:
    """The short sha of the checkout this process is running, read ONCE at import.

    A stack check has to be able to say WHICH code answered it, and the deploy is the
    only other thing that knows. `GIT_SHA` (set by the container build) wins where there
    is no git directory at all; a local run reads it from the checkout beside this file.
    Never per request: this shells out.
    """
    from os import environ

    env_sha = (environ.get("GIT_SHA") or "").strip()
    if env_sha:
        return env_sha
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_Path(__file__).resolve().parent.parent),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (out.stdout or "").strip() or "unknown"
    except Exception:  # noqa: BLE001 - a health probe never fails over its own metadata
        return "unknown"


GIT_SHA = _git_sha()


def _production_prompt_version(db) -> int | None:
    """The parser prompt version carrying the `production` label, or None.

    Read at REQUEST time, not at import: the label moves while the process runs (every
    prompt publish does it), and a cached number would tell a stack check the wrong
    thing exactly when it matters.
    """
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

    row = (
        db.query(AIPromptVersion.version)
        .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
        .filter(
            AIPromptLabel.name == "chatbot_semantic_parser",
            AIPromptLabel.label == "production",
        )
        .first()
    )
    return int(row[0]) if row else None


@app.get("/health")
async def health_check():
    """Readiness probe: 200 only if DB reachable. Blue/green deploy gates color swap on this.

    Also names the code and the prompt that answered, so a stack check can tell a lane
    running yesterday's commit from one running today's (`scripts/chatbot-stack-check.sh`).
    """
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            prompt_version = _production_prompt_version(db)
        finally:
            db.close()
        return {
            "status": "healthy",
            "git_sha": GIT_SHA,
            "prompt_version": prompt_version,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)[:200]},
        )
