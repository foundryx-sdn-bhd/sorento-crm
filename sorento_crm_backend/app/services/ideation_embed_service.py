"""Ideas iframe embed-session mint (SSO) - sorento side of §5.3 (D7/D17).

A logged-in sorento user opening ``/ideas`` (board) or ``/ideas/{id}`` (detail) needs
to iframe the shared-service ideation UI with seamless auth. This service:

  1. mints a short-lived **signed assertion** for the user
     (``ideation_embed_signing_secret``, audience ``"ideation-embed"``, carrying the
     ``ideation_embed_connection_id``);
  2. POSTs it to ``{ideation_shared_service_url}/embed/session`` (server-to-server httpx)
     which returns an embed token (``typ="embed"``);
  3. returns ``{ iframe_url, token, expires_at }`` where ``iframe_url`` =
     ``{shared}/embed/ideas[/{id}]`` - the FE iframes that URL and passes the token per
     the embed framework.

DORMANT when any of the three settings is blank (``IdeationEmbedNotConfigured`` → the
router returns a clean 4xx, never a 500). A shared-service outage raises
``IdeationEmbedUpstreamError`` → the router returns a clean 502 the FE turns into a
retry state (AC-44). **Secrets** (signing secret, the minted assertion, the embed token)
are never logged.

The shared-service ``/embed/session`` endpoint is in the DEFERRED capture-spine set
(embed framework not built yet); this service is built + tested against the §5.3 contract
with that POST stubbed. Live-iframe verification (AC-43/45) is DEFERRED to that dependency.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from jose import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.services.respond_workspace_service import RespondWorkspaceService

logger = logging.getLogger(__name__)

_EMBED_SESSION_PATH = "/embed/session"
_EMBED_IDEAS_PATH = "/embed/ideas"
_ASSERTION_AUDIENCE = "ideation-embed"
_ASSERTION_TTL_SECONDS = 120  # short-lived - exchanged immediately for an embed token
_TIMEOUT_SECONDS = 15
_FALLBACK_DISPLAY_NAME = "Sorento staff"


class IdeationEmbedNotConfigured(Exception):
    """Raised when the ideation embed settings are blank (feature dormant)."""


class IdeationEmbedUpstreamError(Exception):
    """Raised when the shared-service ``/embed/session`` call cannot be completed
    (outage/timeout/HTTP error/malformed body/missing token)."""


class _EmbedConfig:
    """Resolved Ideas iframe embed connection for a request.

    DB (default workspace row) is the source of truth; each field falls back to
    ``app.config`` settings ONLY when the workspace field is blank (keeps legacy
    ``.env`` installs working). Any missing piece => dormant (no assertion minted).

    ``base_url`` is the shared-service BACKEND base (``…/be``) for
    ``POST /embed/session``; ``fe_base_url`` is the shared-service FRONTEND root the
    iframe points at. They are DIFFERENT values and must never be collapsed (AC-E-3).
    ``secret`` is the plaintext signing secret (decrypted from the DB ciphertext or
    read from ``.env``) - never logged.
    """

    __slots__ = ("base_url", "fe_base_url", "connection_id", "secret")

    def __init__(
        self,
        base_url: str | None,
        fe_base_url: str | None,
        connection_id: str | None,
        secret: str | None,
    ):
        self.base_url = base_url
        self.fe_base_url = fe_base_url
        self.connection_id = connection_id
        self.secret = secret

    @property
    def is_ready(self) -> bool:
        return bool(self.base_url and self.fe_base_url and self.connection_id and self.secret)


def _resolve_embed_config(db: Session | None) -> _EmbedConfig:
    """Read the embed connection from the DEFAULT ``RespondWorkspace`` (decrypting the
    signing secret); fall back to ``app.config`` per-field when a workspace field is
    blank. DB wins; settings are the legacy fallback. Mirrors
    ``ideation_turn_service._resolve_ideation_config``.

    ``base_url`` reuses the existing ``ideation_shared_service_url`` (backend base);
    ``fe_base_url`` is the new ``ideation_embed_fe_base_url`` (FE root)."""
    base_url = None
    fe_base_url = None
    connection_id = None
    secret = None

    if db is not None:
        svc = RespondWorkspaceService(db)
        workspace = svc.get_default()
        if workspace is not None:
            base_url = (getattr(workspace, "ideation_shared_service_url", None) or "").strip() or None
            fe_base_url = (getattr(workspace, "ideation_embed_fe_base_url", None) or "").strip() or None
            connection_id = (getattr(workspace, "ideation_embed_connection_id", None) or "").strip() or None
            secret = svc.decrypt_ideation_embed_secret(workspace)

    if not base_url:
        base_url = (settings.ideation_shared_service_url or "").strip() or None
    if not fe_base_url:
        fe_base_url = (settings.ideation_embed_fe_base_url or "").strip() or None
    if not connection_id:
        connection_id = (settings.ideation_embed_connection_id or "").strip() or None
    if not secret:
        secret = (settings.ideation_embed_signing_secret or "").strip() or None

    return _EmbedConfig(
        base_url=base_url,
        fe_base_url=fe_base_url,
        connection_id=connection_id,
        secret=secret,
    )


def mint_embed_assertion(user: dict[str, Any], *, secret: str, connection_id: str) -> str:
    """Sign a short-lived assertion identifying the logged-in user for the embed
    connection. Signed with the resolved embed signing secret (never the app JWT
    secret) so the shared-service verifies it against the embed connection only."""
    if not secret or not connection_id:
        raise IdeationEmbedNotConfigured("ideation embed signing not configured")

    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "typ": "assertion",
        "aud": _ASSERTION_AUDIENCE,
        "iss": "sorento",
        "sub": str(user.get("id") or ""),
        "email": user.get("email"),
        # The display name other readers see on a comment (AC-A-07): never the email.
        "name": (user.get("name") or "").strip() or _FALLBACK_DISPLAY_NAME,
        "connection_id": connection_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_ASSERTION_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def post_embed_session(base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST the signed assertion to shared-service ``/embed/session`` (server-to-server).

    Raises ``IdeationEmbedUpstreamError`` on any transport/HTTP/parse failure so the
    caller returns a clean 502 instead of a 500 crash (AC-44)."""
    url = base_url.rstrip("/") + _EMBED_SESSION_PATH
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise IdeationEmbedUpstreamError(f"embed session request failed: {exc}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise IdeationEmbedUpstreamError(f"embed session returned a malformed body: {exc}") from exc
    if not isinstance(data, dict):
        raise IdeationEmbedUpstreamError("embed session returned a non-object body")
    return data


def create_embed_session(
    db: Session | None,
    user: dict[str, Any],
    *,
    idea_id: str | None = None,
) -> dict[str, Any]:
    """Mint an embed session for ``user``. Returns ``{ iframe_url, token, expires_at }``.

    Config is DB-driven (default ``RespondWorkspace`` row); ``.env`` is a per-field
    fallback only (AC-E-2). The backend ``POST /embed/session`` uses the backend
    ``base_url`` while ``iframe_url`` is built from the distinct ``fe_base_url``
    (AC-E-3). Raises ``IdeationEmbedNotConfigured`` (dormant) or
    ``IdeationEmbedUpstreamError`` (shared-service outage / bad response)."""
    config = _resolve_embed_config(db)
    if not config.is_ready:
        raise IdeationEmbedNotConfigured("ideation embed not configured for this deployment")

    assert config.base_url and config.fe_base_url and config.connection_id and config.secret

    assertion = mint_embed_assertion(
        user, secret=config.secret, connection_id=config.connection_id
    )
    request_payload: dict[str, Any] = {
        "connection_id": config.connection_id,
        "assertion": assertion,
    }
    if idea_id:
        request_payload["idea_id"] = idea_id

    try:
        data = post_embed_session(config.base_url, request_payload)
    except IdeationEmbedUpstreamError:
        # Log WITHOUT the assertion/secret/token - just the failure fact.
        logger.warning("ideation embed session mint failed (shared-service upstream).", exc_info=True)
        raise

    token = data.get("token")
    if not token:
        raise IdeationEmbedUpstreamError("embed session response missing token")

    # iframe points at the shared-service FRONTEND root, NOT the backend base (AC-E-3).
    iframe_url = config.fe_base_url.rstrip("/") + _EMBED_IDEAS_PATH + (f"/{idea_id}" if idea_id else "")
    return {
        "iframe_url": iframe_url,
        "token": token,
        "expires_at": data.get("expires_at"),
    }
