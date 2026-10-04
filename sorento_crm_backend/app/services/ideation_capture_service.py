"""The one-message ideation capture turn (IDEATION-CAPTURE, PLAN-ideation-capture-02oct.md 4a).

`POST /external/ideation/turn` calls `handle_capture_turn`. One message becomes one idea:
no draft, no review step, no media lookback. Per turn:

  1. the access gate: the sender must be a linked, active CRM user holding
     `ideation.board.view` (Q2), else nothing is created;
  2. a held similar-list (`session_vars.ideation.status == "similar_offered"`): a bare number
     answers with that idea's link, NEW creates from the HELD message, anything else is a
     fresh message;
  3. a fresh message: the extractor, then ss "similar own ideas"; any hit holds the message and
     offers the numbered list, no hit creates the idea at once and replies with its CRM link and
     the fields it did not catch.

Every reply goes through `ideation_capture_replies.render_reply`; the language is the
extractor's reading of the message (a held list keeps the language of the ORIGINAL message).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.access import RespondContact
from app.models.user import User
from app.services.chatbot_reply_copy import FALLBACK_LANGUAGES
from app.services.conversation_variables_service import overwrite_for_contact
from app.services.ideation_capture_replies import render_reply
from app.services.ideation_extractor import (
    IdeateExtraction,
    extract_ideate_turn,
    normalise_field_value,
    normalise_title,
)
from app.services.ideation_turn_service import (
    IdeationServiceError,
    _TIMEOUT_SECONDS,
    _derive_display_name,
    _get_contact_row,
    _now_iso,
    _parse_iso,
    _resolve_ideation_config,
)
from app.services.user_service import UserPermissionService

logger = logging.getLogger(__name__)

_SIMILAR_OWN_PATH = "/ideation/intake/ideas/similar-own"
_CREATE_PATH = "/ideation/intake/ideas"
_VIEW_PERMISSION = "ideation.board.view"
_HOLD_MAX_AGE = timedelta(hours=24)
_MAX_SIMILAR = 3
_PHOTOS = "Photos or files"
#: What the one message may not have given, in the order the reply lists it. The names stay
#: English in every language (owner: "exact").
_OPTIONAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("proposed_solution", "Proposed solution"),
    ("impact", "Impact"),
    ("department", "Department"),
)
_REQUIRED_FIELDS: tuple[str, ...] = ("problem",)
_EDGE = " \t\r\n.,!?;:'\"()[]"


def _post_json(base_url: str, api_key: str, path: str, payload: dict[str, Any], what: str) -> dict[str, Any]:
    """POST `payload` to ss `path` (server-to-server HTTP) and return the JSON object.

    Raises `IdeationServiceError` on any transport/HTTP/parse failure so the caller replies
    gracefully instead of 500ing. ss errors are `{"error": {"code", "message"}}`: the code is
    logged, never shown."""
    url = base_url.rstrip("/") + path
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(url, json=payload, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json()
        except (ValueError, json.JSONDecodeError):
            detail = exc.response.text
        error = detail.get("error") if isinstance(detail, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        logger.warning("ideation %s refused by ss: status=%s code=%s", what, exc.response.status_code, code)
        raise IdeationServiceError(
            f"{what} request failed: {exc}",
            status_code=exc.response.status_code,
            response_detail=detail,
        ) from exc
    except httpx.HTTPError as exc:
        raise IdeationServiceError(f"{what} request failed: {exc}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise IdeationServiceError(f"{what} returned a malformed body: {exc}") from exc
    if not isinstance(data, dict):
        raise IdeationServiceError(f"{what} returned a non-object body")
    return data


def call_similar_own(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ss `/ideation/intake/ideas/similar-own`; the answer is `{matches: [...]}`."""
    return _post_json(base_url, api_key, _SIMILAR_OWN_PATH, payload, "similar-own")


def call_create_idea(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ss `/ideation/intake/ideas` (flat fields). Success is a 2xx with
    `status == "captured"` and a UUID `idea_id`; anything else is an `IdeationServiceError`."""
    data = _post_json(base_url, api_key, _CREATE_PATH, payload, "create idea")
    if data.get("status") != "captured" or _valid_uuid(data.get("idea_id")) is None:
        raise IdeationServiceError(f"create idea returned an unexpected body: status={data.get('status')!r}")
    return data


def _missing_required(fields: dict[str, str]) -> list[str]:
    """The required fields the message did not give. The one seam for the shared
    required-field helper (LOWSTOCK-FILTER-ASK): its call replaces this body."""
    return [key for key in _REQUIRED_FIELDS if not (fields.get(key) or "").strip()]


def _ss_failure(exc: IdeationServiceError, step: str, respond_io_id: Any) -> str:
    """Log an ss failure and return its reply kind. A 4xx other than 408/429 will not heal on
    a retry (a scope, key or payload fault), so it is `config_error` at ERROR; everything else
    stays `error` at WARNING."""
    status = exc.status_code
    if isinstance(status, int) and 400 <= status < 500 and status not in (408, 429):
        detail = exc.response_detail if isinstance(exc.response_detail, dict) else {}
        err = detail.get("error")
        code = err.get("code") if isinstance(err, dict) else None
        logger.error(
            "ideation %s rejected by ss (permanent) status=%s code=%s respond_io_id=%s",
            step, status, code, respond_io_id, exc_info=True,
        )
        return "config_error"
    logger.warning("ideation %s failed for respond_io_id=%s", step, respond_io_id, exc_info=True)
    return "error"


def _valid_uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _crm_link(*segments: str) -> str | None:
    base = (settings.frontend_base_url or "").strip().rstrip("/")
    if not base:
        return None
    return "/".join([base, *segments])


def _idea_link(idea_id: Any) -> str | None:
    clean = _valid_uuid(idea_id)
    return _crm_link("ideas", clean) if clean else None


def _language(value: Any) -> str:
    return value if value in FALLBACK_LANGUAGES else "en"


def _held_pointer(ideation_state: dict[str, Any], is_test: bool) -> dict[str, Any] | None:
    """The similar-list pointer this turn may answer: shaped right, same `is_test`, under 24h."""
    if not isinstance(ideation_state, dict) or ideation_state.get("status") != "similar_offered":
        return None
    if bool(ideation_state.get("is_test")) != bool(is_test):
        return None
    similar = ideation_state.get("similar")
    if (
        not isinstance(similar, list)
        or not all(isinstance(item, dict) for item in similar)
        or not ideation_state.get("message_text")
    ):
        return None
    updated = _parse_iso(ideation_state.get("updated_at"))
    if updated is None or datetime.now(timezone.utc) - updated >= _HOLD_MAX_AGE:
        return None
    return ideation_state


def _access_user(db: Session, respond_io_id: str) -> User | None:
    """The active, untrashed CRM user linked to this contact who holds the Ideas permission."""
    user = (
        db.query(User)
        .join(RespondContact, RespondContact.id == User.respond_contact_id)
        .filter(RespondContact.respond_io_id == respond_io_id)
        .first()
    )
    if user is None or str(user.status or "").upper() != "ACTIVE" or user.is_trashed:
        return None
    if not UserPermissionService(db).check_user_has_permission(user.id, _VIEW_PERMISSION):
        return None
    return user


def _missing_list(sent: dict[str, Any]) -> list[str]:
    """What the CRM did not send, in the reply's order (ss returns no `captured`)."""
    missing = [label for key, label in _OPTIONAL_FIELDS if not sent.get(key)]
    return [*missing, _PHOTOS]


def handle_capture_turn(
    db: Session,
    *,
    respond_io_id: str,
    message_text: str,
    submitter_name: str | None = None,
    session_vars_in: dict[str, Any] | None = None,
    is_test: bool = False,
    ask_reply: bool = False,
) -> dict[str, Any]:
    """Handle one ideate turn. Returns `{status, reply_text, link?, session_vars, offered_media}`;
    `session_vars` is the full updated blob. A test turn never persists it. `language` is the one
    the reply was written in. `ask_reply` marks an answer to the ask-back."""
    contact = _get_contact_row(db, respond_io_id)
    session_vars = contact.session_vars
    if is_test:
        # A dry run carries its own pointer (it is never persisted): the caller's first.
        caller_sv = session_vars_in or {}
        ideation_state = (
            caller_sv.get("ideation")
            or (caller_sv.get("variables") or {}).get("ideation")
            or session_vars.get("ideation")
            or {}
        )
    else:
        # A live turn trusts only what this service persisted, in the contact's own row.
        ideation_state = session_vars.get("ideation") or {}
    held = _held_pointer(ideation_state, is_test)
    text_in = (message_text or "").strip()

    def finish(
        status: str,
        kind: str,
        facts: dict[str, Any],
        language: str,
        *,
        pointer: dict[str, Any] | None = None,
        persist: bool = True,
        link: str | None = None,
        user_message: str = text_in,
    ) -> dict[str, Any]:
        # A live write re-reads the row first: the extractor and the ss calls run in between, and
        # whatever another writer landed meanwhile must survive. Only `ideation` is this turn's.
        writes = persist and not is_test
        # The raw row, not get_for_contact: that one migrates `focus` on read and must not write it.
        base = _get_contact_row(db, respond_io_id).session_vars if writes else session_vars
        new_sv = dict(base)
        new_sv.pop("ideation", None)
        if pointer:
            new_sv["ideation"] = pointer
        if writes:
            overwrite_for_contact(db, respond_io_id=respond_io_id, state=new_sv)
        out: dict[str, Any] = {
            "status": status,
            "reply_text": render_reply(kind, facts, user_message=user_message, language=language, db=db),
            "session_vars": new_sv,
            "offered_media": [],
            "language": language,
        }
        if link:
            out["link"] = link
        return out

    # A bare number or NEW answers the held list; neither needs the extractor.
    choice: str | int | None = None
    if held is not None:
        token = text_in.strip(_EDGE)
        if token.isdigit() and 1 <= int(token) <= len(held["similar"]):
            choice = int(token)
        elif token.lower() == "new":
            choice = "new"

    extraction: IdeateExtraction | None = None
    if choice is None:
        # Fresh message (a held list is dropped). The extractor also reads the language, so it
        # runs before the gates and the gate replies can follow it.
        extraction = extract_ideate_turn(db, message_text=text_in)
        language = _language(extraction.language)
        held = None
    else:
        language = _language(held.get("language"))

    config = _resolve_ideation_config(db)
    if not config.is_ready:
        return finish("unconfigured", "unconfigured", {}, language, pointer=held, persist=False)

    user = _access_user(db, respond_io_id)
    if user is None:
        return finish("no_access", "no_access", {}, language)

    if isinstance(choice, int):
        idea = held["similar"][choice - 1]
        facts = {
            "idea_number": idea.get("idea_number"),
            "title": idea.get("title"),
            "link": _idea_link(idea.get("idea_id")),
        }
        return finish("similar_picked", "similar_picked", facts, language)

    if choice == "new":
        # Create from the HELD message: its fields, title and intake_ref ride on the pointer.
        fields = dict(held.get("fields") or {})
        title = str(held.get("title") or "")
        source_text = held["message_text"]
        intake_ref = _valid_uuid(held.get("intake_ref")) or str(uuid.uuid4())
    else:
        assert extraction is not None
        source_text = text_in
        fields = {
            key: cleaned
            for key, value in extraction.fields.items()
            if (cleaned := normalise_field_value(key, value))
        }
        title = normalise_title(extraction.title)
        # An intent-only message ("I have an idea") carries no idea whatever the extractor
        # wrote into problem, so problem is not counted when has_idea is False.
        checked = (
            {k: v for k, v in fields.items() if k != "problem"}
            if extraction.has_idea is False
            else fields
        )
        if _missing_required(checked):
            if ask_reply:
                # The user answered the ask-back and still gave no idea: end the ask with a
                # statement, never a second identical question.
                return finish("ask_idea_gave_up", "ask_idea_gave_up", {}, language)
            return finish("ask_idea", "ask_idea", {}, language)
        # Minted once the create is decided; a list offer keeps it so NEW (even retried after
        # an ss failure) lands on the same idea.
        intake_ref = str(uuid.uuid4())

        try:
            found = call_similar_own(
                config.base_url,
                config.api_key,
                {
                    "product_id": config.product_id,
                    "text": text_in,
                    "submitter_crm_user_id": user.id,
                    "submitter_phone": contact.phone_number,
                    "is_test": bool(is_test),
                },
            )
        except IdeationServiceError as exc:
            kind = _ss_failure(exc, "similar-own", respond_io_id)
            return finish(kind, kind, {}, language, persist=False)

        raw = [m for m in (found.get("matches") or []) if isinstance(m, dict)]
        shown = [
            {
                "idea_id": clean,
                "idea_number": m.get("idea_number"),
                "title": str(m.get("title") or ""),
            }
            for m in raw
            if (clean := _valid_uuid(m.get("idea_id")))
        ][:_MAX_SIMILAR]
        if shown:
            facts = {
                "similar": [{**s, "link": _idea_link(s["idea_id"])} for s in shown],
                # ss returns no total: a full page of 3 may be hiding more.
                "see_all": _crm_link("ideas?view=mine") if len(raw) >= _MAX_SIMILAR else None,
            }
            pointer: dict[str, Any] = {
                "status": "similar_offered",
                "message_text": text_in,
                "fields": fields,
                "title": title,
                "similar": shown,
                "intake_ref": intake_ref,
                "updated_at": _now_iso(),
                "is_test": bool(is_test),
            }
            if extraction.language in FALLBACK_LANGUAGES:
                pointer["language"] = extraction.language
            return finish("similar_offered", "similar_offered", facts, language, pointer=pointer)

    payload: dict[str, Any] = {
        "product_id": config.product_id,
        "problem": fields.get("problem", ""),
        "submitter_crm_user_id": user.id,
        "submitter_phone": contact.phone_number,
        "raw_transcript": source_text,
        "is_test": bool(is_test),
        "intake_ref": intake_ref,
    }
    for key, _label in _OPTIONAL_FIELDS:
        if fields.get(key):
            payload[key] = fields[key]
    name = contact.display_name or _derive_display_name(submitter_name, None, None)
    if name:
        payload["submitter_name"] = name
    if title:
        payload["title"] = title
    if contact.submitter_tier:
        payload["submitter_tier"] = contact.submitter_tier

    try:
        result = call_create_idea(config.base_url, config.api_key, payload)
    except IdeationServiceError as exc:
        kind = _ss_failure(exc, "capture create", respond_io_id)
        return finish(kind, kind, {}, language, pointer=held, persist=False)

    # The CRM link is built here from the id; ss's public `link` is never relayed.
    link = _idea_link(result.get("idea_id"))
    facts = {
        "idea_number": result.get("idea_number"),
        "title": result.get("title") or title,
        "link": link,
        "missing": _missing_list(payload),
    }
    return finish("complete", "complete", facts, language, link=link)
