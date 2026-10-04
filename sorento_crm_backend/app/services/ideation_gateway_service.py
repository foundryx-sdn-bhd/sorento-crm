"""The CRM gateway to the ss embed API (IDEATION-IN-CRM, PLAN section 10).

Every Ideas call the CRM frontend makes goes through here: the backend mints the 120 s assertion
exactly as the iframe flow does (`ideation_embed_service.mint_embed_assertion`), exchanges it at
ss `POST /embed/session`, keeps the 5 minute embed token per CRM user until 30 s before it
expires, and forwards the call with `Authorization: Bearer <embed token>`. The token never leaves
the backend and is never logged.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from urllib.parse import quote
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import Response
from sqlalchemy.orm import Session

from app.services.error_handler import AppException
from app.services.ideation_embed_service import (
    IdeationEmbedNotConfigured,
    IdeationEmbedUpstreamError,
    _TIMEOUT_SECONDS,
    _resolve_embed_config,
    mint_embed_assertion,
    post_embed_session,
)

logger = logging.getLogger(__name__)

UNREACHABLE = "The Ideas workspace isn't reachable right now."
UNAVAILABLE = "The Ideas workspace isn't available on this deployment."
_REFRESH_MARGIN_SECONDS = 30
_DEFAULT_TOKEN_TTL_SECONDS = 300

# Keyed by (user id, connection id, ss base URL): a token is only valid for the connection it was
# minted on, so a changed workspace config can never reuse an old one (AC-A-12).
_cache: dict[tuple[str, str, str], tuple[str, float]] = {}
_cache_lock = threading.Lock()


def clear_token_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _drop(key: tuple[str, str, str]) -> None:
    with _cache_lock:
        _cache.pop(key, None)


def ss_path(*segments: str) -> str:
    """An ss path from segments, each percent-encoded so an id can never add a path level, a
    query string or a fragment (AC-A-10)."""
    return "/" + "/".join(quote(str(seg), safe="") for seg in segments)


def require_uuid(value: object, field: str = "id") -> str:
    """The canonical lowercase UUID, else 422 and nothing reaches ss (AC-A-10)."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise AppException(422, f"{field} must be an id.", code="VALIDATION_ERROR")


def _expiry_epoch(expires_at: Any) -> float:
    """The token's expiry as epoch seconds; a missing or unreadable value counts as the default TTL."""
    if isinstance(expires_at, str):
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    return time.time() + _DEFAULT_TOKEN_TTL_SECONDS


def _cache_key(user: dict[str, Any], config: Any) -> tuple[str, str, str]:
    return (str(user.get("id") or ""), str(config.connection_id), str(config.base_url))


def get_embed_token(db: Session, user: dict[str, Any], *, force_refresh: bool = False) -> str:
    """The user's embed token, from the cache while more than 30 s of it is left."""
    config = _resolve_embed_config(db)
    if not config.is_ready:
        raise IdeationEmbedNotConfigured("ideation embed not configured for this deployment")
    assert config.base_url and config.connection_id and config.secret

    key = _cache_key(user, config)
    if not force_refresh:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and hit[1] - _REFRESH_MARGIN_SECONDS > time.time():
            return hit[0]

    assertion = mint_embed_assertion(user, secret=config.secret, connection_id=config.connection_id)
    data = post_embed_session(
        config.base_url, {"connection_id": config.connection_id, "assertion": assertion}
    )
    token = data.get("token")
    if not token:
        raise IdeationEmbedUpstreamError("embed session response missing token")
    with _cache_lock:
        _cache[key] = (token, _expiry_epoch(data.get("expires_at")))
    return token


def ss_error_message(resp: httpx.Response, fallback: str) -> str:
    """The human message in an ss error body (`detail`, `message` or `error.message`)."""
    try:
        data = resp.json()
    except ValueError:
        return fallback
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            return detail["message"]
        if isinstance(data.get("message"), str) and data["message"]:
            return data["message"]
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str) and err["message"]:
            return err["message"]
    return fallback


def _unreachable() -> AppException:
    return AppException(502, UNREACHABLE, code="IDEATION_UNREACHABLE")


def _send(base_url: str, method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        return client.request(
            method,
            base_url.rstrip("/") + path,
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )


def call_ss(
    db: Session,
    user: dict[str, Any],
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Any = None,
    files: Any = None,
) -> httpx.Response:
    """Forward one call to the ss embed API as `user`; returns ss's 2xx response.

    An ss 401 drops the cached token, re-mints once and retries once (a second 401 is a 502). An
    ss 4xx otherwise passes through with ss's status and message, an unreachable or failing ss is a
    502, and a deployment without the connection configured is a 404.
    """
    kwargs: dict[str, Any] = {}
    if params:
        kwargs["params"] = params
    if json is not None:
        kwargs["json"] = json
    if files is not None:
        kwargs["files"] = files

    try:
        config = _resolve_embed_config(db)
        if not config.is_ready:
            raise IdeationEmbedNotConfigured("not configured")
        assert config.base_url
        resp: httpx.Response | None = None
        for attempt in (0, 1):
            token = get_embed_token(db, user, force_refresh=attempt == 1)
            resp = _send(config.base_url, method, path, token, **kwargs)
            if resp.status_code != 401:
                break
            _drop(_cache_key(user, config))
        assert resp is not None
    except IdeationEmbedNotConfigured:
        raise AppException(404, UNAVAILABLE, code="IDEATION_NOT_CONFIGURED")
    except (IdeationEmbedUpstreamError, httpx.HTTPError):
        logger.warning("ideation gateway: ss unreachable (%s %s)", method, path)
        raise _unreachable()

    if resp.status_code == 401:
        raise _unreachable()
    if resp.status_code >= 500:
        logger.warning("ideation gateway: ss answered %s for %s %s", resp.status_code, method, path)
        raise _unreachable()
    if resp.status_code >= 400:
        raise AppException(
            resp.status_code,
            ss_error_message(resp, "The Ideas workspace refused this request."),
            code="IDEATION_UPSTREAM",
        )
    return resp


def relay(resp: httpx.Response) -> Response:
    """ss's success answer as the CRM's: status and body unchanged. Only the content type and an
    attachment's disposition cross over, so nothing else ss sends reaches the browser."""
    headers = {}
    for name in ("content-disposition", "content-security-policy", "x-content-type-options"):
        if resp.headers.get(name):
            headers[name] = resp.headers[name]
    if resp.status_code == 204 or not resp.content:
        return Response(status_code=resp.status_code, headers=headers)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
        headers=headers,
    )


def user_for_requester(db: Session, user_id: str) -> dict[str, Any]:
    """The `user` dict an assertion needs, for a pending action committing as its requester."""
    from app.models.user import User

    from app.models.user import UserStatus

    row = db.query(User).filter(User.id == str(user_id)).first()
    if row is None:
        raise AppException(404, "The user who started this action no longer exists.", code="NOT_FOUND")
    # A parked action commits seconds later, as its requester: someone deactivated meanwhile
    # must not still act in ss (AC-A-12).
    if row.status != UserStatus.ACTIVE.value:
        raise AppException(403, "The user who started this action is no longer active.", code="FORBIDDEN")
    return {"id": str(row.id), "email": row.email, "name": row.name}
