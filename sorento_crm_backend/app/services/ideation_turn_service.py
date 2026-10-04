"""Shared-service plumbing the ideate flow keeps from the old multi-turn draft path.

The turn itself is `ideation_capture_service.handle_capture_turn` (IDEATION-CAPTURE). What
stays here is what live code still imports:

* the shared-service connection: `_resolve_ideation_config`, `_IdeationConfig`,
  `call_create_idea`, `IdeationServiceError`, and the contact read `_get_contact_row`;
* the access-denied reply composer `compose_ideate_denial_reply` (called from
  `app.services.chatbot.lanes.canned`);
* the S4 idle sweep (`sweep_idle_ideation_drafts`), which still drains `ideation` pointers
  written by the old draft flow: a reminder at 24h idle, then a `cancel` close.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import json
from fastapi import HTTPException, status as http_status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.access import RespondContact
from app.services import ai_prompt_registry
from app.services.ai_assistant_service import AIAssistantConfigService
from app.services.conversation_variables_service import (
    _coerce_to_dict,
    get_for_contact,
    overwrite_for_contact,
)
from app.services.llm_provider import get_provider
from app.services.respond_workspace_service import RespondWorkspaceService

logger = logging.getLogger(__name__)

_CREATE_IDEA_PATH = "/ideation/intake/create-idea"
_TIMEOUT_SECONDS = 15

# create_idea statuses that CLOSE a draft (the idle sweep's close reads them).
_TERMINAL_STATUSES = {"complete", "voted", "cancelled"}

# Should fix 2 (reviewer, round 2), narrowed by Fix round 3 (shared-service
# contract facts, PR #87): the S4 close's ONLY signal that a draft is already
# gone shared-service side (safe to clear the pointer without a retry) -
# NOT every 4xx. 401/403 (a rotated or wrong api_key) and 408/429 mean the
# REQUEST failed, not that the draft is gone. 409 (transition_blocked) means
# the draft is still OPEN - the tenant's status set blocks draft -> rejected.
# 422 means sorento sent a bad payload (a validation error or a title over 8
# words) - never a gone draft. Treating any of those as "gone" would orphan
# the draft on shared-service while sorento silently forgets it.
_DRAFT_GONE_STATUS_CODES = {404, 410}

# Fix round 3: a 422 (sorento payload bug) is retried at most once more before
# the idle sweep stops attempting the close - the pointer is never cleared for
# a 422, so a human has to look at the payload bug either way.
_MAX_CLOSE_PAYLOAD_ERROR_ATTEMPTS = 2

_QUESTION_MARKS = "?？"  # ASCII ? and full-width ？


def _passes_denial_checks(text_out: str) -> bool:
    """AC-1307: the access-denied composition has no positive facts to verify - only that
    the LLM did not invent a URL or tack on a question."""
    text_out = (text_out or "").strip()
    if not text_out:
        return False
    if re.search(r"https?://", text_out):
        return False
    return not any(ch in text_out for ch in _QUESTION_MARKS)


def _call_ideate_reply_llm(db: Session, *, user_message: str) -> str | None:
    """The S3 LLM call for the denial reply: same provider plumbing as the extractor, prompt
    key ``ideate_reply``. Returns ``None`` on any failure so the caller falls back to the
    canned text - never raises."""
    try:
        config = AIAssistantConfigService(db).get()
    except Exception:  # noqa: BLE001
        logger.warning("ideate_reply: config read failed; falling back", exc_info=True)
        return None

    api_key = config.api_key_ciphertext or settings.openai_api_key
    if not api_key:
        return None

    try:
        system, _version = ai_prompt_registry.render(db, "ideate_reply")
    except Exception:  # noqa: BLE001
        logger.warning("ideate_reply: prompt render failed; falling back", exc_info=True)
        return None

    user_block = (
        "FACTS (never invent, never alter these):\n"
        "status: access_denied\n"
        "denied_agent: ideation"
        f"\n\nUser's latest message (read for LANGUAGE only):\n{user_message or ''}"
    )
    messages_in = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_block},
    ]

    try:
        provider = get_provider(config.provider, api_key, config.model)
        result = provider.chat(messages_in, temperature=0.3, model=config.model, max_tokens=400)
        content = (result.content or "").strip()
    except Exception:  # noqa: BLE001
        logger.warning("ideate_reply: LLM call failed; falling back", exc_info=True)
        return None
    return content or None


def compose_ideate_denial_reply(db: Session, *, user_message: str, fallback_text: str) -> str:
    """S3/AC-1307: the ``ideation`` agent's access-denied reply, in the user's language,
    falling back to the existing ``access_denied`` canned text on any failure or a reply that
    fails the deterministic checks. Called from
    ``app.services.chatbot.lanes.canned.access_denied_text``."""
    composed = _call_ideate_reply_llm(db, user_message=user_message)
    if composed is None or not _passes_denial_checks(composed):
        return fallback_text
    return composed


class IdeationServiceError(Exception):
    """Raised when the shared-service ``create_idea`` call cannot be completed
    (outage/timeout/HTTP error/malformed body). The caller degrades to a graceful
    reply - never a 500 on the n8n send sub-flow (AC-19).

    ``status_code`` (Should fix 4, reviewer round 1) carries the shared-service
    HTTP status when the failure was a response, not a transport/parse error -
    None for a timeout, connection failure, or malformed body. The S4 idle
    sweep's close reads it to tell "the draft is already gone" (404/410 -
    clear the pointer) from "the tenant blocks this transition" (409), from
    "sorento sent a bad payload" (422), from "the service is down" (5xx/
    transport - keep retrying).

    ``response_detail`` (Fix round 3, PR #1222: shared-service contract facts
    from PR #87) carries the parsed response body when the failure was a
    response - the idle sweep's 422 branch logs it as the sorento payload bug
    it is."""

    def __init__(
        self, message: str, *, status_code: int | None = None, response_detail: Any = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_detail = response_detail


class _ContactState:
    __slots__ = ("phone_number", "session_vars", "display_name", "submitter_tier")

    def __init__(
        self,
        phone_number: str,
        session_vars: dict[str, Any],
        display_name: str | None = None,
        submitter_tier: str | None = None,
    ):
        self.phone_number = phone_number
        self.session_vars = session_vars
        # Human name from respond_contacts (WS-A). None when the CRM has no name
        # for this contact → the capture turn falls back to the n8n-supplied name.
        self.display_name = display_name
        # R7/AC-1207: the code of the FIRST ContactAccessType in the contact's
        # access_types relationship order (sort_order, then code). None when the
        # contact holds no access type.
        self.submitter_tier = submitter_tier


def _derive_display_name(name: Any, first_name: Any, last_name: Any) -> str | None:
    """Prefer the full ``name``; else join first+last; else None (never ""). WS-A."""
    full = (name or "").strip()
    if full:
        return full
    parts = " ".join(p for p in ((first_name or "").strip(), (last_name or "").strip()) if p)
    return parts or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_contact_row(db: Session, respond_io_id: str) -> _ContactState:
    """Load the contact's phone (E.164 submitter), session_vars and submitter tier
    by respond_io_id. 404 when no contact matches (n8n only routes ideate turns for
    known contacts).

    ``submitter_tier`` (R7/AC-1207) is the code of the FIRST ``ContactAccessType``
    in the contact's ``access_types`` relationship order (``sort_order``, then
    ``code``) - a correlated subquery rather than an ORM relationship load, so this
    stays the single query it always was.
    """
    row = db.execute(
        text(
            "SELECT rc.phone_number, rc.name, rc.first_name, rc.last_name, "
            "rc.session_vars, "
            "(SELECT cat.code FROM respond_contact_access_types rcat "
            " JOIN contact_access_types cat ON cat.code = rcat.access_type_code "
            " WHERE rcat.contact_id = rc.id "
            " ORDER BY cat.sort_order, cat.code LIMIT 1) AS submitter_tier "
            "FROM respond_contacts rc WHERE rc.respond_io_id = :cid"
        ),
        {"cid": respond_io_id},
    ).first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Respond contact not found for respond_io_id={respond_io_id!r}.",
        )
    return _ContactState(
        row.phone_number,
        _coerce_to_dict(row.session_vars),
        display_name=_derive_display_name(row.name, row.first_name, row.last_name),
        submitter_tier=row.submitter_tier,
    )


class _IdeationConfig:
    """Resolved ideation shared-service connection for a turn.

    DB (default workspace row) is the source of truth; each field falls back to
    ``app.config`` settings ONLY when the workspace field is blank (keeps legacy
    ``.env`` installs working). Any missing piece => fail-closed (no create_idea).
    """

    __slots__ = ("base_url", "api_key", "product_id")

    def __init__(self, base_url: str | None, api_key: str | None, product_id: str | None):
        self.base_url = base_url
        self.api_key = api_key
        self.product_id = product_id

    @property
    def is_ready(self) -> bool:
        return bool(self.base_url and self.api_key and self.product_id)


def _resolve_ideation_config(db: Session) -> _IdeationConfig:
    """Read base URL, intake API key, and Product binding from the DEFAULT
    workspace (decrypting the key); fall back to ``app.config`` per-field when a
    workspace field is blank. DB wins; settings are the legacy fallback."""
    svc = RespondWorkspaceService(db)
    workspace = svc.get_default()

    base_url = None
    api_key = None
    product_id = None
    if workspace is not None:
        base_url = (getattr(workspace, "ideation_shared_service_url", None) or "").strip() or None
        product_id = (getattr(workspace, "ideation_product_id", None) or "").strip() or None
        api_key = svc.decrypt_ideation_api_key(workspace)

    if not base_url:
        base_url = (settings.ideation_shared_service_url or "").strip() or None
    if not api_key:
        api_key = (settings.ideation_intake_api_key or "").strip() or None

    return _IdeationConfig(base_url=base_url, api_key=api_key, product_id=product_id)


def call_create_idea(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST the §5.1 input to shared-service ``create_idea`` (server-to-server HTTP).

    Raises ``IdeationServiceError`` on any transport/HTTP/parse failure so the
    caller can reply gracefully instead of 500ing (AC-19)."""
    url = base_url.rstrip("/") + _CREATE_IDEA_PATH
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(
                url, json=payload, headers={"Authorization": f"Bearer {api_key}"}
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json()
        except (ValueError, json.JSONDecodeError):
            detail = exc.response.text
        raise IdeationServiceError(
            f"create_idea request failed: {exc}",
            status_code=exc.response.status_code,
            response_detail=detail,
        ) from exc
    except httpx.HTTPError as exc:
        raise IdeationServiceError(f"create_idea request failed: {exc}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise IdeationServiceError(f"create_idea returned a malformed body: {exc}") from exc
    if not isinstance(data, dict):
        raise IdeationServiceError("create_idea returned a non-object body")
    return data



# =============================================================================
# S4 - 24h idle reminder and close (plan section S4, AC-1401 to AC-1408)
# =============================================================================

_IDLE_REMINDER_AFTER = timedelta(hours=24)
_IDLE_CLOSE_AFTER = timedelta(hours=24)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ideation_reminder_text(title: str | None) -> str:
    """Fixed wording (plan S4) - not the LLM: nobody's message to take a
    language from, and a template has fixed wording anyway."""
    if title:
        return f'Your idea "{title}" is still open. Reply to finish it, or say cancel.'
    return "Your idea is still open. Reply to finish it, or say cancel."


def _send_ideation_reminder(db: Session, *, respond_io_id: str, title: str | None) -> None:
    """AC-1401/AC-1405/AC-1406: best-effort. Any failure - outage, no template
    mapped (``TemplateSendSkipped``), or the contact's outbound kill switch
    (``assert_outbound_enabled``, asserted inside ``RespondClient`` itself) -
    is logged to ``integration_logs`` and swallowed. The caller writes
    ``reminded_at`` either way: the draft still closes on schedule."""
    from app.services.respond_messaging_service import send_text_or_template

    text_out = _ideation_reminder_text(title)
    try:
        send_text_or_template(
            db, identifier=respond_io_id, text=text_out, use_case="ideation_draft_reminder"
        )
    except Exception as exc:  # noqa: BLE001 - never block reminded_at / the close schedule
        logger.warning(
            "ideation idle sweep: reminder send failed for respond_io_id=%s",
            respond_io_id,
            exc_info=True,
        )
        try:
            from app.schemas.integration import IntegrationLogCreate
            from app.services.integration_service import IntegrationLogService

            IntegrationLogService(db).create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="respond_contacts.session_vars",
                    business_id=str(uuid.uuid4()),
                    external_reference=respond_io_id,
                    direction="outbound",
                    endpoint="ideation_draft_reminder",
                    http_method="POST",
                    status="failed",
                    error_message=str(exc)[:2000],
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("ideation idle sweep: could not log the failed reminder send")


# Fix round 3 outcomes for _close_idle_ideation_draft. Plain strings, not an
# Enum - the caller (sweep_idle_ideation_drafts) only ever compares them.
_CLOSE_GONE = "gone"  # clear the pointer
_CLOSE_BLOCKED = "blocked"  # 409 transition_blocked: keep, record the block, skip later ticks
_CLOSE_PAYLOAD_ERROR = "payload_error"  # 422: keep, count the retry, log at error level
_CLOSE_RETRY = "retry"  # everything else: keep, retry next tick as today


def _close_idle_ideation_draft(
    db: Session, *, respond_io_id: str, phone_number: str, draft_id: str | None
) -> str:
    """AC-1402/AC-1407: close the draft via the same ``cancel: true`` contract a
    live turn uses (plan S4: ``{product_id, draft_id, cancel: true,
    submitter_contact_id}``). Returns one of the ``_CLOSE_*`` outcomes; the
    caller decides what to do to the pointer.

    Should fix 4 (reviewer round 1), narrowed by Fix round 3 (shared-service
    contract facts, PR #87): 404/410 mean the draft is already gone
    shared-service side - ``_CLOSE_GONE``, clearing the pointer rather than
    re-POSTing the same dead draft_id every 15 minutes forever. 409
    (``transition_blocked``) means the draft is still OPEN - the tenant's
    status set blocks draft to rejected - so the pointer is kept
    (``_CLOSE_BLOCKED``). 422 is a sorento payload bug, never a gone draft -
    the pointer is kept and the failure logged at error level with the
    response detail (``_CLOSE_PAYLOAD_ERROR``). Anything else (401/403/408/429,
    5xx, transport) is a request failure, not a gone draft - ``_CLOSE_RETRY``,
    same as today (AC-1407).

    A 200 response always carries a terminal status per the contract (a
    cancel call either closes the draft or 409s); the returned ``status`` is
    still checked defensively rather than trusting any 200 blindly."""
    config = _resolve_ideation_config(db)
    if not config.is_ready:
        return _CLOSE_RETRY
    payload: dict[str, Any] = {
        "product_id": config.product_id,
        "submitter_contact_id": phone_number,
        "cancel": True,
    }
    if draft_id:
        payload["draft_id"] = draft_id
    try:
        result = call_create_idea(config.base_url, config.api_key, payload)
    except IdeationServiceError as exc:
        if exc.status_code in _DRAFT_GONE_STATUS_CODES:
            logger.warning(
                "ideation idle sweep: close got %s for respond_io_id=%s (draft "
                "already gone) - clearing the pointer instead of retrying",
                exc.status_code,
                respond_io_id,
            )
            return _CLOSE_GONE
        if exc.status_code == 409:
            logger.warning(
                "ideation idle sweep: close blocked (409 transition_blocked) for "
                "respond_io_id=%s - the tenant blocks draft to rejected; recording "
                "the block and skipping this draft until the pointer changes",
                respond_io_id,
            )
            return _CLOSE_BLOCKED
        if exc.status_code == 422:
            logger.error(
                "ideation idle sweep: close got 422 for respond_io_id=%s (sorento "
                "payload bug, not a gone draft) - %s",
                respond_io_id,
                exc.response_detail,
            )
            return _CLOSE_PAYLOAD_ERROR
        logger.warning(
            "ideation idle sweep: close outage for respond_io_id=%s", respond_io_id, exc_info=True
        )
        return _CLOSE_RETRY
    status_val = str(result.get("status") or "")
    if status_val in _TERMINAL_STATUSES:
        return _CLOSE_GONE
    logger.warning(
        "ideation idle sweep: close got 200 with unexpected status=%r for "
        "respond_io_id=%s - keeping the pointer for retry",
        status_val,
        respond_io_id,
    )
    return _CLOSE_RETRY


def sweep_idle_ideation_drafts(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """S4: one WhatsApp reminder at 24h idle, then close (R3).

    Reads every contact with an open ``ideation`` pointer. A pointer with no
    ``reminded_at`` whose ``updated_at`` is stale sends the one reminder and
    stamps ``reminded_at`` - the idempotency key (AC-1404): a second sweep in
    the same tick, or any tick before the NEXT 24h elapses, sees it already
    set and does nothing. A pointer with a stale ``reminded_at`` closes the
    draft. Test turns never reach here: #1182 stops a test turn from ever
    writing the pointer, so a test draft never accumulates in
    ``respond_contacts.session_vars``.

    Never raises - each contact is independent, and one failure (a malformed
    pointer, a single outage) must not stop the rest of the batch.
    """
    now = now or datetime.now(timezone.utc)
    reminded = 0
    closed = 0

    rows = (
        db.query(RespondContact)
        .filter(RespondContact.session_vars.has_key("ideation"))
        .all()
    )
    for contact in rows:
        try:
            session_vars = _coerce_to_dict(contact.session_vars)
            ideation = session_vars.get("ideation") or {}
            # Only the old draft flow's pointers (they carry a draft_id) drain here; a
            # capture-flow held list has no draft in ss, so there is nothing to remind or close.
            if not ideation or not ideation.get("draft_id"):
                continue
            updated_at = _parse_iso(ideation.get("updated_at"))
            reminded_at = _parse_iso(ideation.get("reminded_at"))

            if reminded_at is None:
                if updated_at is None or (now - updated_at) < _IDLE_REMINDER_AFTER:
                    continue
                # The send is a network call - a live turn can land on this
                # contact while it is in flight. Should fix 5 (reviewer round
                # 1): re-read session_vars FRESH right before writing and merge
                # reminded_at onto THAT, rather than blindly overwriting with
                # the snapshot read at the top of this loop (which would
                # silently discard whatever the live turn just wrote).
                _send_ideation_reminder(
                    db, respond_io_id=contact.respond_io_id, title=ideation.get("title")
                )
                fresh_session_vars = get_for_contact(db, respond_io_id=contact.respond_io_id)
                fresh_ideation = fresh_session_vars.get("ideation")
                if not fresh_ideation:
                    continue  # a live turn finished/cleared the draft meanwhile
                new_ideation = dict(fresh_ideation)
                new_ideation["reminded_at"] = now.isoformat()
                new_session_vars = dict(fresh_session_vars)
                new_session_vars["ideation"] = new_ideation
                overwrite_for_contact(
                    db, respond_io_id=contact.respond_io_id, state=new_session_vars
                )
                reminded += 1
                continue

            if (now - reminded_at) < _IDLE_CLOSE_AFTER:
                continue
            # Fix round 3: a draft already recorded as blocked (409) or that
            # spent its payload-error retry budget (422) is skipped without
            # calling shared-service again - "not re-sent every tick" - until
            # a live turn rebuilds the pointer from scratch (the capture turn never
            # carries these fields forward).
            if ideation.get("close_blocked_at") or (
                int(ideation.get("close_retry_count") or 0) >= _MAX_CLOSE_PAYLOAD_ERROR_ATTEMPTS
            ):
                continue
            outcome = _close_idle_ideation_draft(
                db,
                respond_io_id=contact.respond_io_id,
                phone_number=contact.phone_number,
                draft_id=ideation.get("draft_id"),
            )
            if outcome == _CLOSE_GONE:
                # Same race, same fix: re-read fresh, and only clear the
                # pointer if it is still pointing at the draft we just closed -
                # a live turn may have started a NEW draft while the close
                # call was in flight.
                fresh_session_vars = get_for_contact(db, respond_io_id=contact.respond_io_id)
                fresh_ideation = fresh_session_vars.get("ideation")
                if fresh_ideation and fresh_ideation.get("draft_id") == ideation.get("draft_id"):
                    new_session_vars = dict(fresh_session_vars)
                    new_session_vars.pop("ideation", None)
                    overwrite_for_contact(
                        db, respond_io_id=contact.respond_io_id, state=new_session_vars
                    )
                closed += 1
            elif outcome == _CLOSE_BLOCKED:
                fresh_session_vars = get_for_contact(db, respond_io_id=contact.respond_io_id)
                fresh_ideation = fresh_session_vars.get("ideation")
                if fresh_ideation and fresh_ideation.get("draft_id") == ideation.get("draft_id"):
                    new_ideation = dict(fresh_ideation)
                    new_ideation["close_blocked_at"] = now.isoformat()
                    new_session_vars = dict(fresh_session_vars)
                    new_session_vars["ideation"] = new_ideation
                    overwrite_for_contact(
                        db, respond_io_id=contact.respond_io_id, state=new_session_vars
                    )
            elif outcome == _CLOSE_PAYLOAD_ERROR:
                fresh_session_vars = get_for_contact(db, respond_io_id=contact.respond_io_id)
                fresh_ideation = fresh_session_vars.get("ideation")
                if fresh_ideation and fresh_ideation.get("draft_id") == ideation.get("draft_id"):
                    new_ideation = dict(fresh_ideation)
                    new_ideation["close_retry_count"] = (
                        int(fresh_ideation.get("close_retry_count") or 0) + 1
                    )
                    new_session_vars = dict(fresh_session_vars)
                    new_session_vars["ideation"] = new_ideation
                    overwrite_for_contact(
                        db, respond_io_id=contact.respond_io_id, state=new_session_vars
                    )
            # else _CLOSE_RETRY: outage/request failure - keep the pointer as
            # is, the next tick retries (AC-1407).
        except Exception:  # noqa: BLE001 - one bad row must not sink the batch
            db.rollback()
            logger.exception(
                "ideation idle sweep: failed for respond_io_id=%s", contact.respond_io_id
            )

    return {"reminded": reminded, "closed": closed}
