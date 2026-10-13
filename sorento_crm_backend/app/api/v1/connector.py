"""claude.ai connector, sorento's side (CLAUDE-CONNECTOR r3).

Contract: documentation/plans/analytics/CONTRACT-claude-connector-ss-09oct.md. The shared service (SS)
owns OAuth, grants and the MCP endpoint. Here:
* ``/consent/*``: the staff member's own session answers SS's consent request (section 2);
* the manifest and the chat data routes: only an SS-signed ``Authorization: Connector`` call, already
  verified, scoped and audited by ``access.connector_call_gate`` (section 3). A staff session gets 401.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.services.analytics_connector import consent, datasets
from app.services.analytics_connector.access import ConnectorCall, ConnectorError, connector_call
from app.services.analytics_connector.manifest import build_manifest

router = APIRouter()


# ------------------------------------------------------------------ consent (staff browser)


class ConsentIn(BaseModel):
    req: str = Field(..., min_length=1, max_length=4096)


def _staff_user(request: Request, current_user: dict = Depends(get_current_user)) -> dict:
    """A real staff session, not a view-as: an admin cannot consent on someone's behalf."""
    real = getattr(request.state, "real_user", None)
    if isinstance(real, dict) and str(real.get("id")) != str(current_user["id"]):
        raise ConnectorError(403, "impersonation_not_allowed", "Stop viewing as another user to connect Claude.")
    return current_user


@router.post("/consent/inspect")
def consent_inspect(body: ConsentIn, user: dict = Depends(_staff_user), db: Session = Depends(get_db)):
    return consent.inspect(db, body.req, user)


@router.post("/consent/approve")
def consent_approve(body: ConsentIn, user: dict = Depends(_staff_user), db: Session = Depends(get_db)):
    return consent.approve(db, body.req, user)


@router.post("/consent/deny")
def consent_deny(body: ConsentIn, _user: dict = Depends(_staff_user), db: Session = Depends(get_db)):
    return consent.deny(db, body.req)


# ------------------------------------------------------------------ SS-signed calls only


def signed_call(request: Request) -> ConnectorCall:
    call = connector_call(request)
    if call is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="A connector signature is required.")
    return call


@router.get("/manifest")
def manifest(_call: ConnectorCall = Depends(signed_call)):
    return build_manifest()


def _found(result: Optional[dict]) -> dict:
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown dataset")
    return result


def _checked(fn):
    try:
        return fn()
    except datasets.QueryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/datasets")
def list_datasets(_call: ConnectorCall = Depends(signed_call), db: Session = Depends(get_db)):
    return datasets.list_datasets(db)


@router.get("/datasets/{key}")
def describe_dataset(key: str, _call: ConnectorCall = Depends(signed_call), db: Session = Depends(get_db)):
    if key not in datasets.CATALOG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown dataset")
    return _found(datasets.describe_dataset(db, key))


@router.post("/query")
def query(spec: datasets.QuerySpec, _call: ConnectorCall = Depends(signed_call), db: Session = Depends(get_db)):
    return _checked(lambda: datasets.run_query(db, spec))


@router.get("/messages/search")
def search_messages(
    text: Optional[str] = None,
    contact: Optional[str] = None,
    customer: Optional[str] = None,
    direction: Optional[Literal["incoming", "outgoing"]] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _call: ConnectorCall = Depends(signed_call),
    db: Session = Depends(get_db),
):
    return _checked(lambda: datasets.search_messages(
        db, text_=text, contact=contact, customer=customer, direction=direction,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset))


@router.get("/conversation")
def get_conversation(
    contact: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _call: ConnectorCall = Depends(signed_call),
    db: Session = Depends(get_db),
):
    found = datasets.resolve_contact(db, contact)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such contact")
    if len(found) > 1:
        return {"candidates": found}
    return _checked(lambda: datasets.get_conversation(db, found[0], date_from, date_to, limit, offset))
