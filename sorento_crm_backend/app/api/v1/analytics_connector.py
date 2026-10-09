"""claude.ai analytics connector: OAuth server, session probe and chat tools.

Card: documentation/plans/analytics/CARD-claude-connector-09oct.md. The OAuth routes the
analytics container forwards (register, authorize, token) carry the internal key and no session;
the consent routes carry the staff member's own session; every other route carries a connector
session, which `app.dependencies._resolve_session_to_user` has already guarded (internal key,
read-only methods, `analytics.connector.use`).
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.services.analytics_connector import datasets, oauth
from app.services.analytics_connector.access import (
    CHAT_VIEW,
    USE,
    current_grant,
    has_connector_permission,
    record_tool_call,
    require_internal_key,
)
from app.services.user_service import UserPermissionService

router = APIRouter()


def _oauth_error(exc: oauth.OAuthError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status_code, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ OAuth, called by the container


@router.post("/oauth/register", status_code=201, dependencies=[Depends(require_internal_key)])
def oauth_register(body: dict = Body(...), db: Session = Depends(get_db)):
    try:
        return oauth.register_client(db, body)
    except oauth.OAuthError as exc:
        return _oauth_error(exc)


class AuthorizeIn(BaseModel):
    response_type: Optional[str] = None
    client_id: Optional[str] = None
    redirect_uri: Optional[str] = None
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[str] = None
    state: Optional[str] = None
    scope: Optional[str] = None
    resource: Optional[str] = None


@router.post("/oauth/authorize", dependencies=[Depends(require_internal_key)])
def oauth_authorize(body: AuthorizeIn, db: Session = Depends(get_db)):
    try:
        return oauth.start_authorize(db, body.model_dump())
    except oauth.OAuthError as exc:
        return _oauth_error(exc)


@router.post("/oauth/token", dependencies=[Depends(require_internal_key)])
def oauth_token(
    grant_type: Optional[str] = Form(None),
    code: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    resource: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    form = {
        "code": code, "code_verifier": code_verifier, "client_id": client_id,
        "redirect_uri": redirect_uri, "resource": resource, "refresh_token": refresh_token,
    }
    try:
        if grant_type == "authorization_code":
            body = oauth.exchange_code(db, form)
        elif grant_type == "refresh_token":
            body = oauth.refresh_tokens(db, form)
        else:
            raise oauth.OAuthError("unsupported_grant_type", "use authorization_code or refresh_token")
    except oauth.OAuthError as exc:
        return _oauth_error(exc)
    return JSONResponse(body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


# ------------------------------------------------------------------ consent, called by the staff browser


def _staff_user(request: Request, current_user: dict = Depends(get_current_user)) -> dict:
    """A real staff session, not a connector one and not an impersonated view."""
    if getattr(request.state, "session_auth_method", None) == "claude_connector":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="staff_session_required")
    real = getattr(request.state, "real_user", None)
    if isinstance(real, dict) and str(real.get("id")) != str(current_user["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="impersonation_not_allowed")
    return current_user


def _raise_oauth(exc: oauth.OAuthError):
    raise HTTPException(exc.status_code, detail=exc.error)


@router.get("/authorize/{request_id}")
def consent_details(request_id: str, user: dict = Depends(_staff_user), db: Session = Depends(get_db)):
    try:
        return oauth.consent_details(db, request_id, user)
    except oauth.OAuthError as exc:
        _raise_oauth(exc)


@router.post("/authorize/{request_id}/approve")
def consent_approve(request_id: str, user: dict = Depends(_staff_user), db: Session = Depends(get_db)):
    if not has_connector_permission(db, str(user["id"]), USE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Permission required: {USE}")
    try:
        return oauth.approve(db, request_id, user)
    except oauth.OAuthError as exc:
        _raise_oauth(exc)


@router.post("/authorize/{request_id}/deny")
def consent_deny(request_id: str, user: dict = Depends(_staff_user), db: Session = Depends(get_db)):
    try:
        return oauth.deny(db, request_id)
    except oauth.OAuthError as exc:
        _raise_oauth(exc)


# ------------------------------------------------------------------ connector session routes


def connector_user(request: Request, current_user: dict = Depends(get_current_user)) -> dict:
    if getattr(request.state, "session_auth_method", None) != "claude_connector":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="connector_session_required")
    return current_user


def chat_user(user: dict = Depends(connector_user), db: Session = Depends(get_db)) -> dict:
    """Q2 (a): the connector permission alone does not open chat data."""
    if not UserPermissionService(db).check_user_has_permission(user["id"], CHAT_VIEW):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Permission required: {CHAT_VIEW}")
    return user


@router.get("/me")
def me(request: Request, user: dict = Depends(connector_user), db: Session = Depends(get_db)):
    grant = current_grant(db, getattr(request.state, "session_id", None))
    if grant is not None:
        grant.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    return {"user_id": user["id"], "name": user.get("name"), "email": user.get("email")}


def _audited(request: Request, db: Session, tool: str, args: dict, fn):
    started = time.perf_counter()
    try:
        result = fn()
    except datasets.QueryError as exc:
        record_tool_call(db, request, tool=tool, args=args, status_text="rejected", started=started)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except HTTPException:
        record_tool_call(db, request, tool=tool, args=args, status_text="error", started=started)
        raise
    rows = result.get("row_count") if isinstance(result, dict) else None
    record_tool_call(
        db, request, tool=tool, args=args, status_text="ok", started=started,
        rows=rows if rows is not None else (len(result["datasets"]) if "datasets" in result else None),
        chars=len(json.dumps(result, default=str)),
    )
    return result


@router.get("/datasets")
def list_datasets(request: Request, _u: dict = Depends(chat_user), db: Session = Depends(get_db)):
    return _audited(request, db, "chat_list_datasets", {}, lambda: datasets.list_datasets(db))


@router.get("/datasets/{key}")
def describe_dataset(key: str, request: Request, _u: dict = Depends(chat_user), db: Session = Depends(get_db)):
    if key not in datasets.CATALOG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown dataset")
    return _audited(request, db, "chat_describe_dataset", {"dataset": key}, lambda: datasets.describe_dataset(db, key))


@router.post("/query")
def query(spec: datasets.QuerySpec, request: Request, _u: dict = Depends(chat_user), db: Session = Depends(get_db)):
    args = spec.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    return _audited(request, db, "chat_query", args, lambda: datasets.run_query(db, spec))


@router.get("/messages/search")
def search_messages(
    request: Request,
    text: Optional[str] = None,
    contact: Optional[str] = None,
    customer: Optional[str] = None,
    direction: Optional[Literal["incoming", "outgoing"]] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _u: dict = Depends(chat_user),
    db: Session = Depends(get_db),
):
    args = {k: str(v) for k, v in dict(text=text, contact=contact, customer=customer, direction=direction,
                                       date_from=date_from, date_to=date_to, limit=limit, offset=offset).items()
            if v is not None}
    return _audited(request, db, "chat_search_messages", args, lambda: datasets.search_messages(
        db, text_=text, contact=contact, customer=customer, direction=direction,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset))


@router.get("/conversation")
def get_conversation(
    request: Request,
    contact: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _u: dict = Depends(chat_user),
    db: Session = Depends(get_db),
):
    args = {k: str(v) for k, v in dict(contact=contact, date_from=date_from, date_to=date_to,
                                       limit=limit, offset=offset).items() if v is not None}

    def run() -> dict:
        found = datasets.resolve_contact(db, contact)
        if not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such contact")
        if len(found) > 1:
            return {"candidates": found}
        return datasets.get_conversation(db, found[0], date_from, date_to, limit, offset)

    return _audited(request, db, "chat_get_conversation", args, run)


class ToolCallIn(BaseModel):
    tool: str = Field(..., max_length=100)
    args: dict[str, Any] = {}
    status: str = Field("ok", max_length=20)
    ms: Optional[int] = None
    rows: Optional[int] = None
    chars: Optional[int] = None


@router.post("/audit", status_code=204)
def audit_tool_call(body: ToolCallIn, request: Request, _u: dict = Depends(connector_user), db: Session = Depends(get_db)):
    """The container reports a reused catalog tool call right after it returns."""
    record_tool_call(
        db, request, tool=body.tool, args=body.args, status_text=body.status, started=time.perf_counter(),
        rows=body.rows, chars=body.chars, ms=body.ms,
    )
    return Response(status_code=204)
