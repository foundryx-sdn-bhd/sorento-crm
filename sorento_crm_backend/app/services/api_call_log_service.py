"""Write + sanitize `api_call_log` rows.

The sanitizing lives here rather than in the middleware so it is testable without
an ASGI round-trip, and so a direct writer cannot bypass it.

Redaction is **key-based**, not value-based. Guessing which values look like
secrets fails open on the one you did not anticipate; an explicit key list fails
closed and is auditable.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

REDACTED = "***redacted***"
TRUNCATION_MARKER = "…[truncated]"
# ~8KB per payload. Two payloads per row; beyond this the marginal diagnostic
# value drops fast while the storage cost does not.
MAX_PAYLOAD_CHARS = 8192
MAX_SOURCE_CHARS = 32
DEFAULT_SOURCE = "unknown"

# Lowercased. Matched exactly against each key, at every nesting level.
_SECRET_KEYS = frozenset(
    {
        "x-api-key",
        "apikey",
        "api_key",
        "authorization",
        "auth",
        "cookie",
        "set-cookie",
        "x-auth-token",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "client_secret",
        "private_key",
    }
)


# Suffixes catch prefixed names such as `anthropic_api_key` / `gemini_api_key` (the AI
# assistant settings PUT) that the exact set misses. `_token` does not match `max_tokens`.
_SECRET_SUFFIXES = ("_api_key", "_password", "_secret", "_token")


def _is_secret_key(key: Any) -> bool:
    k = str(key).strip().lower()
    return k in _SECRET_KEYS or k.endswith(_SECRET_SUFFIXES)


def redact_mapping(value: Any) -> Any:
    """Deep-copy `value`, replacing any secret-keyed entry with `REDACTED`.

    Returns a new structure - the middleware redacts headers the live request is
    still using, so mutating in place would strip the caller's own credentials.
    """
    if isinstance(value, Mapping):
        return {
            k: REDACTED if _is_secret_key(k) else redact_mapping(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_mapping(v) for v in value]
    return value


def truncate_payload(payload: Any) -> Optional[str]:
    """Coerce a body to a bounded string, or None when there is nothing to store."""
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        # A binary body (file upload) must not raise inside the logging path.
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str):
        payload = str(payload)
    if len(payload) <= MAX_PAYLOAD_CHARS:
        return payload
    return payload[:MAX_PAYLOAD_CHARS] + TRUNCATION_MARKER


def sanitize_body(payload: Any) -> Optional[str]:
    """Redact then bound a request/response body.

    A JSON body is parsed so secret KEYS can be stripped structurally; anything
    that is not JSON is stored as-is (bounded). Redaction has to happen before
    truncation - truncating first can cut a secret in half and store the front of
    it, which is still a leak.
    """
    if payload is None:
        return None
    raw = payload
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        raw = str(raw)
    if not raw.strip():
        return None

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        # Not JSON (form-encoded, plain text, HTML error page). No structure to
        # redact by key; store bounded as-is.
        return truncate_payload(raw)

    try:
        return truncate_payload(json.dumps(redact_mapping(parsed), default=str))
    except (TypeError, ValueError):
        return truncate_payload(raw)


def resolve_source(headers: Mapping[str, str]) -> str:
    """Caller identity from `X-Source`, bounded to the column width.

    Falls back to `unknown` rather than `n8n`: most external traffic is n8n today,
    but writing an assumption into the evidence table defeats its purpose. An
    unrecognised value is kept (a new caller should be visible), just bounded.
    """
    for key, value in headers.items():
        if str(key).strip().lower() == "x-source":
            cleaned = (value or "").strip().lower()
            if cleaned:
                return cleaned[:MAX_SOURCE_CHARS]
    return DEFAULT_SOURCE


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    for key, value in headers.items():
        if str(key).strip().lower() == name:
            return (value or "").strip() or None
    return None


def resolve_tool_name(headers: Mapping[str, str]) -> Optional[str]:
    value = _header(headers, "x-tool-name")
    return value[:128] if value else None


def resolve_correlation_id(headers: Mapping[str, str]) -> Optional[str]:
    value = _header(headers, "x-correlation-id")
    return value[:64] if value else None


MAX_ERROR_MESSAGE_CHARS = 1024


def summarize_validation_errors(status_code: Optional[int], response_body: Any) -> Optional[str]:
    """Name the offending field(s) of a 422 so the row is the diagnosis.

    A validation refusal never reaches the handler, so nothing route-side can log
    it; the only evidence is the 422 body the app's RequestValidationError handler
    emits (``{"detail": [{"loc": [...], "msg": ..., "type": ...}, ...]}``). The body
    is already stored as ``response_payload``, but the UI surfaces and searches
    ``error_message``, and "body.reply_to_message_id: Input should be a valid
    string" is what somebody scanning the log needs to read, not a JSON blob.

    Returns None for anything that is not a 422 with that shape, so a route that
    hand-rolls its own 422 is stored exactly as before.
    """
    if status_code != 422 or response_body is None:
        return None
    raw = response_body
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    # Any failure here is a telemetry failure, and telemetry never reaches the
    # caller: this runs in the middleware's send path, outside _write's guard.
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    detail = parsed.get("detail") if isinstance(parsed, dict) else None
    if not isinstance(detail, list):
        return None

    parts: list[str] = []
    for error in detail:
        if not isinstance(error, dict):
            continue
        loc = error.get("loc")
        field = ".".join(str(p) for p in loc) if isinstance(loc, (list, tuple)) else "?"
        msg = str(error.get("msg") or error.get("type") or "invalid")
        parts.append(f"{field}: {msg}")
    if not parts:
        return None
    summary = "validation failed - " + "; ".join(parts)
    if len(summary) > MAX_ERROR_MESSAGE_CHARS:
        summary = summary[:MAX_ERROR_MESSAGE_CHARS] + TRUNCATION_MARKER
    return summary


def classify_outcome(status_code: Optional[int]) -> str:
    """Bucket a status code. A missing status means the request died before
    producing one - our fault, and it must not land in `success`."""
    if status_code is None:
        return "server_error"
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "client_error"
    return "success"


def prune_api_call_log(db, *, payload_retention_days: int, row_retention_days: int) -> dict:
    """Two-stage retention.

    Payloads NULL first, rows DELETE later. Payloads are the bulk of the bytes and
    the shortest-lived value; the metadata row (endpoint, latency, outcome) stays
    useful for trend analysis long after the body does, so deleting whole rows at
    the payload window would throw away the volume history.
    """
    from datetime import datetime, timedelta

    from app.models.api_call_log import ApiCallLog

    now = datetime.utcnow()
    payload_cutoff = now - timedelta(days=int(payload_retention_days))
    row_cutoff = now - timedelta(days=int(row_retention_days))

    # Delete BEFORE nulling, so the two counts describe disjoint sets. The other
    # order counts a payload-clear for every row it is about to delete, and the
    # task then reports more work than it did.
    rows_deleted = (
        db.query(ApiCallLog)
        .filter(ApiCallLog.created_at < row_cutoff)
        .delete(synchronize_session=False)
    )

    # Guard on "not already NULL" so a re-run reports 0 rather than re-clearing
    # the same rows forever - the task's own output would otherwise imply work
    # that is not happening.
    payloads_cleared = (
        db.query(ApiCallLog)
        .filter(
            ApiCallLog.created_at < payload_cutoff,
            (ApiCallLog.request_payload.isnot(None))
            | (ApiCallLog.response_payload.isnot(None)),
        )
        .update(
            {ApiCallLog.request_payload: None, ApiCallLog.response_payload: None},
            synchronize_session=False,
        )
    )

    db.commit()
    return {"payloads_cleared": int(payloads_cleared), "rows_deleted": int(rows_deleted)}


def write_call_log(db, **fields) -> None:
    """Persist one row. Never raises into the request path.

    A telemetry write failing must not turn a working API call into a 500 - the
    row is evidence about the request, not part of it.
    """
    try:
        from app.models.api_call_log import ApiCallLog

        db.add(ApiCallLog(**fields))
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("api_call_log write failed", exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Sortable columns, allowlisted. A free-form sort key would be an injection
# surface and lets the UI request a sort the indexes cannot serve.
_SORTABLE = {
    "created_at": "created_at",
    "endpoint": "endpoint",
    "source": "source",
    "status_code": "status_code",
    "outcome": "outcome",
    "latency_ms": "latency_ms",
}


def list_call_logs(
    db,
    *,
    date_from=None,
    date_to=None,
    source: Optional[str] = None,
    outcome: Optional[str] = None,
    endpoint: Optional[str] = None,
    correlation_id: Optional[str] = None,
    search: Optional[str] = None,
    min_latency_ms: Optional[int] = None,
    page: int = 1,
    limit: int = DEFAULT_LIMIT,
    sort: Optional[str] = None,
    dir_: str = "desc",
):
    """Offset page + total for the DataGrid.

    Offset rather than keyset because the grid needs a total and arbitrary page
    jumps - the same trade-off taken for the chat-history listing.
    """
    from app.models.api_call_log import ApiCallLog

    q = db.query(ApiCallLog)
    if date_from is not None:
        q = q.filter(ApiCallLog.created_at >= date_from)
    if date_to is not None:
        q = q.filter(ApiCallLog.created_at <= date_to)
    if source:
        q = q.filter(ApiCallLog.source == source)
    if outcome:
        q = q.filter(ApiCallLog.outcome == outcome)
    if endpoint:
        q = q.filter(ApiCallLog.endpoint.ilike(f"%{endpoint}%"))
    if correlation_id:
        q = q.filter(ApiCallLog.correlation_id == correlation_id)
    if min_latency_ms is not None:
        q = q.filter(ApiCallLog.latency_ms >= min_latency_ms)
    if search:
        term = f"%{search}%"
        q = q.filter(
            (ApiCallLog.endpoint.ilike(term))
            | (ApiCallLog.tool_name.ilike(term))
            | (ApiCallLog.error_message.ilike(term))
        )

    total = q.count()

    column = getattr(ApiCallLog, _SORTABLE.get((sort or "").strip(), "created_at"))
    q = q.order_by(column.asc() if dir_ == "asc" else column.desc())

    limit = max(1, min(int(limit), MAX_LIMIT))
    rows = q.offset((max(1, int(page)) - 1) * limit).limit(limit).all()
    return rows, total
