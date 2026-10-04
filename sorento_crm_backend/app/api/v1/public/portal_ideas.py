"""Public customer track page proxy (IDEATION-IN-CRM, AC-H-01..H-07).

    GET  /public/portal/ideas/{token}            -> ss GET  /public/ideas/{token}
    GET  /public/portal/ideas/{token}/comments   -> ss GET  /public/ideas/{token}/comments
    POST /public/portal/ideas/{token}/comments   -> ss POST /public/ideas/{token}/comments

The token is the credential (no login, no embed session). Everything the browser receives passes
through an allow-list, never a blind relay, so an ss field added later cannot leak by default.
A malformed token and an unknown one are the same 404, and every answer carries the private
headers ss itself sends.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import rate_limit
from app.services.ideation_embed_service import _TIMEOUT_SECONDS, _resolve_embed_config
from app.services.ideation_gateway_service import ss_error_message

logger = logging.getLogger(__name__)
router = APIRouter()

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
    "Referrer-Policy": "no-referrer",
}
_RATE_WINDOW_SECONDS = 900
_TOKEN_LIMIT = 5
_GLOBAL_LIMIT = 200
_TOO_MANY = "Too many comments. Try again later."
_UNAVAILABLE = "This page isn't available right now."
_STAFF_NAME = "Sorento staff"

_IDEA_FIELDS = (
    "title", "status", "ideaNumber", "statusColor", "productName", "problem", "proposedSolution",
    "impact", "department", "submitterFirstName", "submittedAt", "upvotes", "nextStep",
)


class PortalCommentIn(BaseModel):
    """Only the text and the thread it replies to. Who is speaking is ss's decision."""

    body: str = Field(min_length=1, max_length=2000)
    parentId: Optional[str] = None


def _reply(status_code: int, content: Any, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(content, status_code=status_code, headers={**_PRIVATE_HEADERS, **(headers or {})})


def _not_found() -> JSONResponse:
    return _reply(404, {"detail": "Not found."})


def _idea_view(raw: dict) -> dict:
    out = {k: raw.get(k) for k in _IDEA_FIELDS if k in raw}
    out["timeline"] = [
        {"label": s.get("label"), "color": s.get("color"), "state": s.get("state")}
        for s in (raw.get("timeline") or [])
        if isinstance(s, dict)
    ]
    merged = raw.get("mergedInto")
    out["mergedInto"] = (
        {"ideaNumber": merged.get("ideaNumber"), "title": merged.get("title")} if isinstance(merged, dict) else None
    )
    return out


def _comment_view(raw: dict) -> dict:
    name = raw.get("authorName")
    # A display name that is really an email address is masked: an address never reaches a customer.
    if isinstance(name, str) and "@" in name:
        name = _STAFF_NAME
    return {
        "id": raw.get("id"),
        "parentId": raw.get("parentId"),
        "authorName": name,
        "isSubmitter": raw.get("authorKind") == "public",
        "body": raw.get("body"),
        "isDeleted": bool(raw.get("isDeleted")),
        "createdAt": raw.get("createdAt"),
        "editedAt": raw.get("editedAt"),
    }


def _ss(db: Session, method: str, path: str, *, json: Any = None):
    """The ss public call. Returns `(parsed JSON body, None)` or `(None, error reply)`: a 2xx that
    is not JSON is a 502 like any other ss fault, and never an unhandled error without the private
    headers."""
    base = _resolve_embed_config(db).base_url
    if not base:
        return None, _not_found()
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.request(method, base.rstrip("/") + path, json=json)
    except httpx.HTTPError:
        logger.warning("portal ideas: ss unreachable (%s)", method)
        return None, _reply(502, {"detail": _UNAVAILABLE})
    if resp.status_code == 404:
        return None, _not_found()
    if resp.status_code >= 500:
        return None, _reply(502, {"detail": _UNAVAILABLE})
    if resp.status_code >= 400:
        return None, _reply(resp.status_code, {"detail": ss_error_message(resp, _UNAVAILABLE)})
    try:
        return resp.json(), None
    except ValueError:
        logger.warning("portal ideas: ss answered a non-JSON body")
        return None, _reply(502, {"detail": _UNAVAILABLE})


@router.get("/ideas/{token}")
def read_idea(token: str, db: Session = Depends(get_db)):
    if not _TOKEN_RE.match(token):
        return _not_found()
    data, error = _ss(db, "GET", f"/public/ideas/{token}")
    if error is not None:
        return error
    return _reply(200, _idea_view(data if isinstance(data, dict) else {}))


@router.get("/ideas/{token}/comments")
def read_comments(token: str, db: Session = Depends(get_db)):
    if not _TOKEN_RE.match(token):
        return _not_found()
    data, error = _ss(db, "GET", f"/public/ideas/{token}/comments")
    if error is not None:
        return error
    return _reply(200, [_comment_view(c) for c in data if isinstance(c, dict)] if isinstance(data, list) else [])


@router.post("/ideas/{token}/comments")
def post_comment(token: str, payload: PortalCommentIn, db: Session = Depends(get_db)):
    if not _TOKEN_RE.match(token):
        return _not_found()
    # Per token (the authoritative limit) and one ceiling across every token. There is no per-IP
    # bucket: behind the CRM's nginx the left-most X-Forwarded-For is client-controlled, so it
    # would only let an attacker pick which bucket they land in (AC-H-07).
    # Both are CHECKED first but CHARGED only when ss accepts the comment: a well-formed token ss
    # does not know costs the caller nothing, so random tokens cannot use up the shared ceiling.
    token_key = hashlib.sha256(token.encode()).hexdigest()
    buckets = (
        ("ideas_public_comment_token", token_key, _TOKEN_LIMIT),
        ("ideas_public_comment_global", "all", _GLOBAL_LIMIT),
    )
    for bucket, ident, limit in buckets:
        result = rate_limit.peek(bucket, ident, limit=limit, window_seconds=_RATE_WINDOW_SECONDS)
        if not result.allowed:
            return _reply(
                429,
                {"detail": _TOO_MANY},
                {"Retry-After": str(result.retry_after_seconds or _RATE_WINDOW_SECONDS)},
            )
    body: dict[str, Any] = {"body": payload.body}
    if payload.parentId:
        body["parentId"] = payload.parentId
    data, error = _ss(db, "POST", f"/public/ideas/{token}/comments", json=body)
    if error is not None:
        return error
    for bucket, ident, _limit in buckets:
        rate_limit.record(bucket, ident, window_seconds=_RATE_WINDOW_SECONDS)
    return _reply(201, _comment_view(data if isinstance(data, dict) else {}))
