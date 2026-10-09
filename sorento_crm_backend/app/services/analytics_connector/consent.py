"""The consent hop (contract section 2): SS sends the browser here with a signed consent request;
the signed-in staff member allows or denies; Allow returns a signed user assertion for SS.

Single use of a request's ``jti`` is kept in ``audit_logs``: approve and deny each write one EVENT row
(``connector.consent``, entity ``connector_consent`` / sha256 of the jti) under a transaction advisory
lock, and a later approve, deny or inspect finds it. That is the smallest durable store here: the
decision is audited anyway, the row outlives a Redis flush, and it only has to hold for the request's
own 10-minute life, so no new table is needed.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.services.analytics_connector.access import USE, ConnectorError, has_connector_permission

REQUEST_TYP = "connector_consent_request"
REQUEST_ISSUER = "foundryx-ss"
REQUEST_AUDIENCE = "sorento-connector-consent"
REQUEST_MAX_LIFETIME = 600  # seconds, exp - iat
ASSERTION_TYP = "connector_assertion"
ASSERTION_ISSUER = "sorento"
ASSERTION_AUDIENCE = "foundryx-connector"
ASSERTION_TTL = 120  # seconds
CLOCK_SKEW = 5
ENTITY = "connector_consent"
EVENT = "connector.consent"
_STR_CLAIMS = ("typ", "iss", "aud", "connection_id", "request_id", "return_url", "jti")


def _bad(message: str = "This connection request is not valid.") -> ConnectorError:
    return ConnectorError(400, "bad_request", message)


def _configured() -> bool:
    return all((
        settings.connector_ss_to_product_secret,
        settings.connector_product_to_ss_secret or "",
        settings.connector_connection_id,
        settings.connector_ss_callback_url,
    ))


def verify_request(req: str) -> dict:
    """The verified claims of a consent request, or ConnectorError (400 / 503)."""
    if not _configured():
        raise ConnectorError(503, "upstream_error", "The Claude connector is not configured.")
    if not isinstance(req, str) or not req or len(req) > 4096:
        raise _bad()
    try:
        claims = jwt.decode(
            req,
            settings.connector_ss_to_product_secret or "",
            algorithms=["HS256"],
            options={
                "verify_aud": False, "verify_exp": False, "verify_iat": False, "verify_nbf": False,
                "verify_iss": False, "verify_sub": False, "verify_jti": False,
            },
        )
    except JWTError:
        raise _bad()
    if any(not isinstance(claims.get(k), str) or not claims[k] for k in _STR_CLAIMS):
        raise _bad()
    if any(not isinstance(claims.get(k), int) or isinstance(claims[k], bool) for k in ("iat", "exp")):
        raise _bad()
    if (
        claims["typ"] != REQUEST_TYP
        or claims["iss"] != REQUEST_ISSUER
        or claims["aud"] != REQUEST_AUDIENCE
        or claims["connection_id"] != settings.connector_connection_id
        or claims["return_url"] != settings.connector_ss_callback_url
    ):
        raise _bad()
    now = time.time()
    if claims["exp"] < now - CLOCK_SKEW:
        raise ConnectorError(400, "request_expired", "This connection request has expired. Start again in Claude.")
    if not 0 < claims["exp"] - claims["iat"] <= REQUEST_MAX_LIFETIME or claims["iat"] > now + CLOCK_SKEW:
        raise _bad()
    return claims


def _jti_key(claims: dict) -> str:
    return hashlib.sha256(claims["jti"].encode()).hexdigest()


def _used(db: Session, claims: dict) -> bool:
    from app.models.audit import AuditLog

    return db.query(AuditLog.id).filter(
        AuditLog.entity_type == ENTITY, AuditLog.entity_id == _jti_key(claims), AuditLog.event == EVENT
    ).first() is not None


def _refuse_if_used(db: Session, claims: dict) -> None:
    if _used(db, claims):
        raise ConnectorError(400, "request_used", "This connection request was already answered.")


def _consume(db: Session, claims: dict, decision: str) -> None:
    """Mark the request answered, once: serialised per jti, then re-checked under the lock."""
    from app.services import audit_service

    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{ENTITY}:{_jti_key(claims)}"})
    _refuse_if_used(db, claims)
    audit_service.record(
        db,
        event=EVENT,
        entity_type=ENTITY,
        entity_id=_jti_key(claims),
        new_values={"decision": decision, "request_id": claims["request_id"][:200]},
    )
    db.commit()


def inspect(db: Session, req: str, user: dict) -> dict[str, Any]:
    claims = verify_request(req)
    _refuse_if_used(db, claims)
    return {
        "request_id": claims["request_id"],
        "client_name": str(claims.get("client_name") or "Claude"),
        "redirect_host": str(claims.get("redirect_host") or ""),
        "expires_at": datetime.fromtimestamp(claims["exp"], tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "user_name": user.get("name"),
        "allowed": has_connector_permission(db, str(user["id"]), USE),
    }


def approve(db: Session, req: str, user: dict) -> dict[str, Any]:
    claims = verify_request(req)
    _refuse_if_used(db, claims)
    if not has_connector_permission(db, str(user["id"]), USE):
        raise ConnectorError(403, "connector_access_removed", "You do not have access to the Claude connector.")
    _consume(db, claims, "approved")
    now = int(time.time())
    assertion = jwt.encode(
        {
            "typ": ASSERTION_TYP,
            "iss": ASSERTION_ISSUER,
            "aud": ASSERTION_AUDIENCE,
            "connection_id": claims["connection_id"],
            "request_id": claims["request_id"],
            "sub": str(user["id"]),
            "name": user.get("name") or "",
            "email": user.get("email") or "",
            "iat": now,
            "exp": now + ASSERTION_TTL,
            "jti": uuid.uuid4().hex,
        },
        settings.connector_product_to_ss_secret or "",
        algorithm="HS256",
    )
    return {"request_id": claims["request_id"], "return_url": claims["return_url"], "assertion": assertion}


def deny(db: Session, req: str) -> dict[str, Any]:
    claims = verify_request(req)
    _consume(db, claims, "denied")
    return {"request_id": claims["request_id"], "return_url": claims["return_url"]}
