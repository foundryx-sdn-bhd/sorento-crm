"""Request-, job- and tick-scoped audit actor for automatic audit logging.

One ``AuditActor`` says who did a write (identity S0, #1280; plan section 8):

- ``user_id`` is the EFFECTIVE actor (the impersonation target when an admin is
  impersonating), ``real_user_id`` is who was at the keyboard. They differ only
  under impersonation; ``created_by`` / ``updated_by`` columns keep the effective
  user and ``real_user_id`` records the admin (plan 8.2).
- ``actor_type`` is user | contact | integration | worker | scheduler |
  public_link | system.

Carriers. ``stamp_actor`` writes the actor to three places at once:

- the contextvar, for code on the same context (async dependencies, the path op,
  an RQ job run in-process, a scheduler tick);
- ``db.info["audit_actor"]``, which lives on the Session object and so survives
  FastAPI running a sync dependency in a different threadpool thread from the
  path op and the flush (the AC-12 gap);
- ``request.state.audit_actor``, read by the API call log middleware.

``get_actor(db)`` prefers ``db.info`` over the contextvar.

``get_audit_context`` is the one older reader kept, for its service callers.

Audit standard S0 (#1281) adds no actor of its own: WHO acted is always the ``AuditActor``
above (plan section 8 of PLAN-unified-identity-26sep.md is the actor contract). S0 adds only
the BUSINESS action around it, an ``AuditContext``: the ``@audit_event`` verb, the reason,
the channel (``source``) and the ``correlation_id`` that ties one action's rows together
across the request and its jobs. ``LoggingMiddleware`` puts a fresh one in a contextvar at
the start of every request and code MUTATES it rather than calling ``.set()``: a sync
dependency runs on a COPIED context in a threadpool, and a copy still holds the same object.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

ACTOR_TYPES = ("user", "contact", "integration", "worker", "scheduler", "public_link", "system")

_USER_AGENT_MAX = 512
_DB_INFO_KEY = "audit_actor"


@dataclass
class AuditActor:
    actor_type: str  # user | contact | integration | worker | scheduler | public_link | system
    user_id: Optional[str] = None  # effective actor
    real_user_id: Optional[str] = None  # at the keyboard; equals user_id unless impersonating
    auth_method: Optional[str] = None  # password | phone_otp | portal_link | portal_token | api_key | impersonation
    session_id: Optional[str] = None
    integration_id: Optional[str] = None
    contact_id: Optional[str] = None
    job_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None  # truncated to 512
    tool_name: Optional[str] = None  # MCP X-Tool-Name, written into description when the row has none

    def __post_init__(self) -> None:
        if self.user_agent is not None:
            self.user_agent = str(self.user_agent)[:_USER_AGENT_MAX] or None


_actor: contextvars.ContextVar[Optional[AuditActor]] = contextvars.ContextVar(
    "audit_actor", default=None
)


def stamp_actor(actor: AuditActor, *, db: Any = None, request: Any = None) -> None:
    """Record ``actor`` as the author of every audited write that follows."""
    _actor.set(actor)
    if db is not None:
        try:
            db.info[_DB_INFO_KEY] = actor
        except Exception:
            pass
    if request is not None:
        try:
            request.state.audit_actor = actor
        except Exception:
            pass


def get_actor(db: Any = None) -> Optional[AuditActor]:
    """The current actor. ``db.info["audit_actor"]`` wins over the contextvar."""
    if db is not None:
        try:
            stamped = db.info.get(_DB_INFO_KEY)
        except Exception:
            stamped = None
        if stamped is not None:
            return stamped
    return _actor.get()


def clear_actor(db: Any = None) -> None:
    """Forget the stamped actor (the contextvar, and ``db.info`` when given)."""
    _actor.set(None)
    if db is not None:
        try:
            db.info.pop(_DB_INFO_KEY, None)
        except Exception:
            pass


@contextmanager
def actor_scope(actor: AuditActor, *, db: Any = None) -> Iterator[AuditActor]:
    """Stamp ``actor`` for the body, then restore whatever was stamped before.

    For jobs and ticks, which run on long-lived threads: without the restore, a
    worker or scheduler actor would outlive its job on that thread.
    """
    token = _actor.set(actor)
    previous_db_actor = None
    if db is not None:
        previous_db_actor = db.info.get(_DB_INFO_KEY)
        db.info[_DB_INFO_KEY] = actor
    try:
        yield actor
    finally:
        _actor.reset(token)
        if db is not None:
            if previous_db_actor is None:
                db.info.pop(_DB_INFO_KEY, None)
            else:
                db.info[_DB_INFO_KEY] = previous_db_actor


# --------------------------------------------------------------------------- #
# Older reader, kept for its service callers.                                  #
# --------------------------------------------------------------------------- #
def get_audit_context() -> tuple[Optional[str], Optional[str]]:
    """Return (user_id, ip_address): the effective actor, as audit rows record it."""
    actor = _actor.get()
    if actor is None:
        return None, None
    return actor.user_id, actor.ip_address


# Per-request correlation id (Sub-plan D Tier-2). The LoggingMiddleware stamps one
# id per request (a job carries its enqueuer's); the audit listener copies it onto
# every AuditLog row so all changes from one action are correlatable.
_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "audit_trace_id", default=None
)


def set_trace_id(trace_id: Optional[str]) -> None:
    """Set the current request's trace/correlation id."""
    _trace_id.set(trace_id)


def get_trace_id() -> Optional[str]:
    """Return the current request's trace/correlation id (None outside a request)."""
    return _trace_id.get()


# --------------------------------------------------------------------------- #
# Audit standard S0 (#1281): the business action around the actor.            #
# --------------------------------------------------------------------------- #
_ID_MAX = 64  # correlation_id is String(64); an inbound header is caller-controlled

# API-key integration type -> audit source. Anything unlisted is a generic external caller.
_SOURCE_BY_INTEGRATION_TYPE = {"automation": "n8n", "mcp": "mcp"}
_CHATBOT_PATH_PREFIX = "/api/v1/external/chat/"  # not /chat-history
# The AutoCount sync (review B3): every request the ESB's key makes, and the master and document
# ingest routes whoever calls them. Their writes mirror an external system of record.
_SYNC_INTEGRATION_TYPES = ("autocount_esb",)
_SYNC_PATH_PREFIXES = ("/api/v1/external/ingest/",)

# Channel when nothing more specific was recorded, by the actor's type.
_SOURCE_BY_ACTOR_TYPE = {
    "user": "ui",
    "contact": "portal",
    "integration": "external_api",
    "worker": "worker",
    "scheduler": "scheduler",
    "public_link": "public_link",
}
_PORTAL_AUTH_METHODS = ("portal_link", "portal_token")


def _clamp(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value)
    return value[:_ID_MAX] if value else None


@dataclass
class AuditContext:
    # ui | portal | chatbot | mcp | n8n | external_api | import | worker | scheduler |
    # public_link. Set where it is known (an integration key, the portal, a job's queue),
    # else derived from the actor by ``source_for``. Never read from a caller header.
    source: Optional[str] = None
    correlation_id: Optional[str] = None
    reason: Optional[str] = None
    event: Optional[str] = None
    # An inbound X-Correlation-Id, held aside: it becomes correlation_id only once an
    # integration key authenticates (MCP and n8n are the real producers), so an ordinary
    # caller cannot stitch its writes into someone else's business action.
    inbound_correlation_id: Optional[str] = field(default=None, repr=False)
    # Set while an integration sync, an import job or a scheduled job is writing (review B3 at
    # cba2b754, the volume gate): the automatic hooks then audit only the classes opted in
    # before default-on (``__audit_track__``), not every table. Names the path, for the log.
    # Not carried into jobs: an imports-queue job is marked by its queue.
    sync_writer: Optional[str] = None
    # (entity_type, entity_id) pairs the flush listener wrote while ``event`` was set, so the
    # ``@audit_event`` decorator can tell which ids got no row. Not carried into jobs.
    event_hits: set = field(default_factory=set, repr=False)


_ctx: contextvars.ContextVar[Optional[AuditContext]] = contextvars.ContextVar(
    "audit_business_context", default=None
)


def current_audit_context() -> Optional[AuditContext]:
    """The business context of the current request / job / tick, or None outside them."""
    return _ctx.get()


def _ensure() -> AuditContext:
    ctx = _ctx.get()
    if ctx is None:
        # Outside a request (a script, a test, a thread with no inherited context). A
        # ``.set()`` here only reaches this context, which is exactly the scope that asked.
        ctx = AuditContext()
        _ctx.set(ctx)
    return ctx


@contextmanager
def audit_context_scope(**fields) -> Iterator[AuditContext]:
    """Run the block under a fresh ``AuditContext``; the previous one comes back after."""
    if "correlation_id" in fields:
        fields["correlation_id"] = _clamp(fields["correlation_id"])
    token = _ctx.set(AuditContext(**fields))
    try:
        yield _ctx.get()
    finally:
        _ctx.reset(token)


def start_request_context(correlation_id: Optional[str] = None) -> AuditContext:
    """Called once per request by ``LoggingMiddleware``: a fresh object, never a shared one.

    ``correlation_id`` is the caller's X-Correlation-Id; it is trusted only after an
    integration key authenticates (``mark_integration_request``). Until then a row's
    correlation id is the request's trace id.
    """
    ctx = AuditContext(inbound_correlation_id=_clamp(correlation_id))
    _ctx.set(ctx)
    return ctx


def mark_integration_request(integration_type: Optional[str], path: Optional[str] = None) -> None:
    """An integration key authenticated: derive the channel and trust its correlation id."""
    ctx = _ensure()
    if ctx.inbound_correlation_id:
        ctx.correlation_id = ctx.inbound_correlation_id
    if path and path.startswith(_CHATBOT_PATH_PREFIX):
        ctx.source = "chatbot"
    else:
        ctx.source = _SOURCE_BY_INTEGRATION_TYPE.get(integration_type or "", "external_api")
    if integration_type in _SYNC_INTEGRATION_TYPES:
        ctx.sync_writer = integration_type
    elif path and path.startswith(_SYNC_PATH_PREFIXES):
        ctx.sync_writer = "ingest"


def set_source(source: Optional[str]) -> None:
    """Record the channel of the current request where the route knows it (the portal)."""
    _ensure().source = source


@contextmanager
def sync_writer_scope(name: str) -> Iterator[AuditContext]:
    """Mark the block as an integration sync or import path (review B3): default-on auditing
    is off inside it, and only ``__audit_track__`` classes are recorded by the hooks. The
    imports queue, every scheduler actor and the AutoCount ingest are marked centrally
    (``sync_writer_for``); this is for a sync path that runs anywhere else."""
    ctx = _ensure()
    previous = ctx.sync_writer
    ctx.sync_writer = name
    try:
        yield ctx
    finally:
        ctx.sync_writer = previous


def sync_writer_for(actor: Optional[AuditActor], ctx: Optional[AuditContext]) -> Optional[str]:
    """The sync, import or scheduled path writing now, or None for a staff-driven write.

    Marked: an imports-queue job (``job_actor_scope``), the AutoCount ESB key and the ingest
    routes (``mark_integration_request``), ``sync_writer_scope``, and every scheduler actor
    (each tick, heartbeat handler and Run now), whichever helper stamped it.
    """
    if ctx is not None and ctx.sync_writer:
        return ctx.sync_writer
    if actor is not None and actor.actor_type == "scheduler":
        return f"scheduler:{actor.job_id or ''}"
    return None


def source_for(actor: Optional[AuditActor], ctx: Optional[AuditContext]) -> Optional[str]:
    """The channel a row is stamped with: the recorded one, else derived from the actor."""
    if ctx is not None and ctx.source:
        return ctx.source
    if actor is None:
        return None
    if actor.auth_method in _PORTAL_AUTH_METHODS:
        return "portal"
    if actor.auth_method == "claude_connector":
        return "mcp"
    return _SOURCE_BY_ACTOR_TYPE.get(actor.actor_type)


def correlation_for(ctx: Optional[AuditContext]) -> Optional[str]:
    """One id per business action: a trusted inbound or inherited one, else the trace id."""
    if ctx is not None and ctx.correlation_id:
        return ctx.correlation_id
    return _clamp(get_trace_id())


# --- Jobs -------------------------------------------------------------------------


def business_meta_for_job() -> dict:
    """What an enqueued job inherits of the business action (the actor rides separately,
    in ``job.meta["actor"]``): the correlation id, so the job's rows join its request's."""
    correlation_id = correlation_for(_ctx.get())
    return {"correlation_id": correlation_id} if correlation_id else {}
