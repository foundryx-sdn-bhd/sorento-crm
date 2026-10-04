"""System API: the turn trace behind System > Chat History (S2b, AC-255/AC-257), plus the
in-app chatbot console (Slice D final, chatbot growth r1).

Three read-or-act routes over `chatbot.turns`, for the operator journey "a customer says
the bot answered wrongly, what did it actually do?":

* `GET /turns` - one contact's turns, newest first, cursor-paged;
* `GET /turns/failed-contacts` - which contacts have a failed turn in a range, so the
  list can filter to them without pulling every turn;
* `POST /turns/{id}/retry` - re-post the original message at the ingress (R4: the only
  retry there is, and only a person can ask for it).

All three are LIVE-turn surfaces (H57, D14). The two reads exclude `is_test` rows unless
`include_test=true` is asked for, and the retry refuses one outright: what an operator is
looking at here is a real customer's conversation, and a dry run never happened to them.

Two slugs, deliberately. `system.chat_history.view` reads the trace - the same grant that
already reads the transcript it hangs under. `system.chat_history.manage` re-injects a
WhatsApp turn at a real customer, which is a different thing to hand out.

Two more routes are the console, `system-management/chatbot-console`'s own backend
(`system.chat_history.view`, the same grant): `POST /console/turn` runs one DRY-RUN turn
in process (`app.services.chatbot.console_service.run_console_turn`) and `GET
/console/prompt-versions` lists the parser's versions so the console can pin one. Every
console turn is `is_test=True` (D14): nothing it does reaches a real customer.

This module is an authorised importer of `app.services.chatbot` (AC-002): the retry seam
lives in the module package, the trace screen is the module's own read surface, and the
console is the module's own dry-run entry point.
"""
from __future__ import annotations

import base64
import hashlib
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import require_permission
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.models.chatbot_turn import ChatbotTurn
from app.schemas.integration import IntegrationLogCreate
from app.schemas.chatbot_turn import (
    ChatbotTurnDetailResponse,
    ChatbotTurnListResponse,
    ChatbotTurnResponse,
    ConsoleMediaStatusResponse,
    ConsolePromptVersion,
    ConsoleTurnRequest,
    ConsoleTurnResponse,
    FailedContactListResponse,
    RetryTurnResponse,
)
from app.services.chatbot import console_service
from app.services.chatbot.contracts import TURN_STAGES
from app.services.chatbot.trace_detail import compose_trace_detail
from app.services.integration_service import IntegrationLogService
from app.services.outbound_url_guard import OutboundUrlRejected
from app.services.chatbot.dispatch import (
    ReinjectFailed,
    RetryUnavailable,
    reinject_envelope,
    retry_available,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chatbot")


def parser_prompt_key() -> str:
    """The prompt registry key the parser publishes under - read from the parser itself
    so this route and the engine can never name two different prompts."""
    from app.services.chatbot.head.parser import PROMPT_KEY

    return PROMPT_KEY

VIEW = "system.chat_history.view"
MANAGE = "system.chat_history.manage"

# The engine's own vocabulary. An unknown value is a 422 rather than an empty page,
# because "no turns are `delivered`" and "there is no such status as `delivered`" are
# different answers and only one of them means the caller has a bug.
TURN_STATUSES = ("queued", "processing", "delegated", "done", "failed")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# The list filter shows a roster of contacts to open, not a report. Past a couple of
# hundred the answer is "something is broadly wrong", which is a different screen.
FAILED_CONTACTS_LIMIT = 200
FAILED_CONTACTS_DEFAULT_DAYS = 7


RETRY_IN_FLIGHT_DETAIL = (
    "A retry for this turn is already on its way. Wait for it to arrive as a new turn "
    "before asking again."
)


def _stale_before(now: datetime) -> datetime:
    """The instant a `retry_requested_at` marker stops counting as in flight.

    One definition, read by the friendly pre-check AND by the conditional claim below, so
    the answer the operator is given and the row the database will actually let them take
    can never disagree.
    """
    return now - timedelta(minutes=max(1, int(settings.chatbot_retry_stale_minutes)))


def _retry_in_flight(row: ChatbotTurn) -> bool:
    """Is a requested retry still expected to arrive?

    The marker is cleared by the re-injected turn arriving. If it never does - n8n dropped
    it, the contact was deleted, the workflow was mid-deploy - the row would be
    un-retryable forever, which turns one lost message into a permanently stuck one. After
    `chatbot_retry_stale_minutes` the operator may try again.
    """
    if row.retry_requested_at is None:
        return False
    requested_at = row.retry_requested_at
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    return requested_at >= _stale_before(datetime.now(timezone.utc))


def _encode_cursor(row: ChatbotTurn) -> str:
    """Opaque keyset cursor: the last row's sort key, not an offset.

    Offsets shift under a table that is being written to while an operator pages, which on
    a newest-first list means rows appearing twice or not at all. `id` breaks ties so two
    turns created in the same millisecond still have a total order.
    """
    payload = {"created_at": row.created_at.isoformat(), "id": str(row.id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return datetime.fromisoformat(payload["created_at"]), payload["id"]
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid cursor. Use the `next_cursor` from the previous page.",
        ) from exc


def _media_facts(row: ChatbotTurn) -> dict | None:
    """The turn's own `media_intake` trace stage's `facts` AND `raw` MERGED into
    one dict, or `None` on a text turn (no such stage at all).

    Merged rather than `facts` alone (review round S5): `job_id`/`attachment_id`/
    the full `result` now live under the stage's `raw` (never printed on the trace
    screen itself - `facts` alone is what `TurnPanel` renders there), but `_media_
    block` below is a DIFFERENT projection (the `media` block on `GET /turns`) that
    still needs `attachment_id` and `result` to build its own shaped response.
    """
    stage = next(
        (r for r in (row.trace or []) if isinstance(r, dict) and r.get("stage") == "media_intake"),
        None,
    )
    if stage is None:
        return None
    facts = stage.get("facts") or {}
    raw = stage.get("raw") or {}
    return {**facts, **raw}


def _media_attachment_ids(rows) -> list[str]:
    """Every non-null `attachment_id` a page of turns' `media_intake` facts carry -
    review round note (c): the ONE id list `list_turns` batches its `Attachment`
    lookup on, instead of one query per row."""
    ids: list[str] = []
    for row in rows:
        facts = _media_facts(row)
        attachment_id = facts.get("attachment_id") if facts else None
        if attachment_id:
            ids.append(attachment_id)
    return ids


def _load_attachments(db: Session, attachment_ids: list[str]) -> dict[str, "Attachment"]:
    if not attachment_ids:
        return {}
    from app.models.resources import Attachment

    rows = db.query(Attachment).filter(Attachment.id.in_(attachment_ids)).all()
    return {str(a.id): a for a in rows}


def _media_block(
    db: Session, row: ChatbotTurn, *, attachments_by_id: dict[str, "Attachment"] | None = None
) -> dict | None:
    """The `media` block `GET /turns` and `/turns/{id}` both carry (chatbot
    media-into-turn, S4, AC-1839/AC-1840): the turn's own `media_intake` trace
    stage, plus a 15-minute signed url for whatever `attachments` row S4's worker
    task linked to it. `None` on a text turn (no such stage at all) or a denied
    one that stored no attachment (`url` stays null; the rest still reports what
    was decided).

    `attachments_by_id` (review round note (c)): a page's worth of `Attachment`
    rows the CALLER already batched in one query (`_load_attachments`) - `list_turns`
    always passes this; the single-turn detail route passes `None` and this
    function queries for its own one row, since there is only ever one.
    """
    facts = _media_facts(row)
    if facts is None:
        return None
    result = facts.get("result") or {}
    attachment_id = facts.get("attachment_id")
    url = None
    mime_type = None
    if attachment_id:
        if attachments_by_id is not None:
            attachment = attachments_by_id.get(str(attachment_id))
        else:
            from app.models.resources import Attachment

            attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
        if attachment is not None:
            from app.services.storage_router import resolve_signed_url

            mime_type = attachment.mime_type
            try:
                # `strict=True` (review round note (b)): a media attachment's url is
                # never worth silently degrading to an unsigned one - the customer's
                # own photo/voice note behind a broken signer must surface as no url
                # (already the contract here) rather than a link that quietly leaks.
                url = resolve_signed_url(
                    attachment.file_path,
                    provider=attachment.storage_provider,
                    expires_in=900,
                    strict=True,
                )
            except Exception:  # noqa: BLE001 - the trace still renders without a url
                logger.warning("chatbot turn %s: could not sign the media url", row.id, exc_info=True)
    entities = [
        {"raw": e} if isinstance(e, str) else e for e in (result.get("entities") or [])
    ]
    return {
        "modality": facts.get("modality"),
        "mime_type": mime_type,
        "attachment_id": attachment_id,
        "url": url,
        "transcript_or_rendered_text": result.get("rendered_text") or result.get("transcript"),
        "entities": entities,
        "attributes": list(result.get("attributes") or []),
        "notes": result.get("notes"),
        "truncated": bool(result.get("truncated")),
        "decision": facts.get("decision"),
    }


@router.get("/turns", response_model=ChatbotTurnListResponse)
def list_turns(
    contact_respond_id: str | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    turn_status: str | None = Query(None, alias="status"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = Query(None),
    include_test: bool = Query(False),
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """One contact's turns with their full trace, newest first.

    `include_test` is off by default (H57, D14). Chat History is read about a REAL
    customer - "the bot answered them wrongly, what did it do?" - and a dry run from the
    Prompts screen's Test button or the chat console never happened to that customer, so
    listing it beside their real turns invites an operator to explain a conversation from
    a turn that was never part of it. The rows are not hidden, only defaulted away: the
    param surfaces them for whoever needs to audit a test run.
    """
    _ = current_user

    if turn_status is not None and turn_status not in TURN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown status {turn_status!r}. Expected one of: {', '.join(TURN_STATUSES)}.",
        )

    query = db.query(ChatbotTurn)
    if contact_respond_id:
        query = query.filter(ChatbotTurn.contact_respond_id == contact_respond_id)
    if from_ is not None:
        query = query.filter(ChatbotTurn.created_at >= from_)
    if to is not None:
        query = query.filter(ChatbotTurn.created_at <= to)
    if turn_status is not None:
        query = query.filter(ChatbotTurn.status == turn_status)
    if not include_test:
        query = query.filter(ChatbotTurn.is_test.is_(False))
    if cursor:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        # Strictly AFTER the last row in newest-first order: older, or same instant with
        # a smaller id.
        query = query.filter(
            (ChatbotTurn.created_at < cursor_created_at)
            | ((ChatbotTurn.created_at == cursor_created_at) & (ChatbotTurn.id < cursor_id))
        )

    # One extra row answers "is there another page?" without a second COUNT over a table
    # that only grows.
    rows = (
        query.order_by(desc(ChatbotTurn.created_at), desc(ChatbotTurn.id))
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    available = retry_available(db)
    # Review round note (c): ONE `Attachment` query for the whole page's worth of
    # media_intake attachment ids, signed per row - not one query per turn.
    attachments_by_id = _load_attachments(db, _media_attachment_ids(page))
    items = [
        {
            **ChatbotTurnResponse.model_validate(row).model_dump(),
            "media": _media_block(db, row, attachments_by_id=attachments_by_id),
        }
        for row in page
    ]
    return ChatbotTurnListResponse(
        items=items,
        next_cursor=_encode_cursor(page[-1]) if has_more and page else None,
        retry_available=available,
        retry_unavailable_reason=None
        if available
        else (
            "Retry is not configured. Set the chatbot retry webhook URL on the default "
            "workspace under System > Respond Workspaces."
        ),
    )


@router.get("/turns/failed-contacts", response_model=FailedContactListResponse)
def failed_contacts(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    limit: int = Query(FAILED_CONTACTS_LIMIT, ge=1, le=FAILED_CONTACTS_LIMIT),
    include_test: bool = Query(False),
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Contacts with at least one failed turn in the range (AC-255).

    What the list's "Failed turns only" filter needs is which contacts to show and what
    went wrong last - two aggregates - not the turns themselves. Answering it by paging
    `/turns` and grouping in the browser would move thousands of rows to answer a question
    about tens, and would be wrong the moment the answer spans a page boundary.

    A range is REQUIRED (a default of the last 7 days when neither bound is given): this
    is the one query here that does not start from a contact id, so unbounded it is a scan
    of every failed turn ever recorded, growing forever, behind a toggle.
    """
    _ = current_user

    if from_ is None and to is None:
        from_ = datetime.now(timezone.utc) - timedelta(days=FAILED_CONTACTS_DEFAULT_DAYS)

    window = [ChatbotTurn.status == "failed"]
    if not include_test:
        # H57: a contact whose only failed turn is a DRY RUN has nothing wrong with it. The
        # list this feeds is the operator's "who needs attention" filter, and a test turn
        # that failed needs the person who ran it, not a customer follow-up.
        window.append(ChatbotTurn.is_test.is_(False))
    if from_ is not None:
        window.append(ChatbotTurn.created_at >= from_)
    if to is not None:
        window.append(ChatbotTurn.created_at <= to)

    # The counts.
    aggregates = (
        db.query(
            ChatbotTurn.contact_respond_id.label("contact_respond_id"),
            func.count(ChatbotTurn.id).label("count"),
            func.max(ChatbotTurn.created_at).label("last_failed_at"),
        )
        .filter(*window)
        .group_by(ChatbotTurn.contact_respond_id)
        .order_by(desc(func.max(ChatbotTurn.created_at)))
        .limit(limit)
        .all()
    )
    if not aggregates:
        return FailedContactListResponse(items=[])

    contact_ids = [row.contact_respond_id for row in aggregates]
    # The stage of the LAST failure per contact. DISTINCT ON returns ONE row per contact
    # rather than every failed row for the app to fold - on a contact with hundreds of
    # failures the difference is the whole result set.
    latest = (
        db.query(ChatbotTurn.contact_respond_id, ChatbotTurn.stage)
        .filter(*window, ChatbotTurn.contact_respond_id.in_(contact_ids))
        .distinct(ChatbotTurn.contact_respond_id)
        .order_by(ChatbotTurn.contact_respond_id, desc(ChatbotTurn.created_at))
        .all()
    )
    latest_stage = {contact_id: stage for contact_id, stage in latest}

    return FailedContactListResponse(
        items=[
            {
                "contact_respond_id": row.contact_respond_id,
                "last_failed_stage": latest_stage.get(row.contact_respond_id),
                "last_failed_at": row.last_failed_at,
                "count": row.count,
            }
            for row in aggregates
        ]
    )


@router.get("/turns/{turn_id}", response_model=ChatbotTurnDetailResponse)
def get_turn(
    turn_id: str,
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """One turn's row plus its normalised `trace_detail` (Slice D, AC-970).

    Registered AFTER `/turns/failed-contacts` (a literal path) so that fixed route
    still wins; this one only ever matches an actual turn id. Same `VIEW` slug as
    the list: opening a row's detail is the same read the list already grants.
    """
    _ = current_user
    row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Turn not found.")
    return ChatbotTurnDetailResponse(
        **{**ChatbotTurnResponse.model_validate(row).model_dump(), "media": _media_block(db, row)},
        trace_detail=compose_trace_detail(row, db),
    )


@router.post("/turns/{turn_id}/retry", response_model=RetryTurnResponse)
def retry_turn(
    turn_id: str,
    request: Request,
    current_user: dict = Depends(require_permission(MANAGE)),
    db: Session = Depends(get_db),
):
    """Re-post this turn's ORIGINAL message at the ingress (AC-705, R4).

    The row is NOT re-run here and NOT edited: it stays `failed`, keeps its trace, and
    gains a marker. The retry arrives back the ordinary way, as its own row with the next
    attempt - which is what makes the retried turn get the same ordering, the same lanes
    and the same sending path a live message gets.

    **The marker is written and COMMITTED before the POST**, and rolled back if the POST
    does not happen. The other order looks tidier and is a race: n8n can deliver the
    re-injected message, and the engine can look for the marker, before this request has
    committed it - so the engine would read `failed` with no marker, call it a duplicate,
    and the retry would vanish while the marker stayed set forever.
    """
    row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Turn not found.")

    if row.is_test:
        # H57 / D14. Retry re-posts the ORIGINAL message at the live n8n ingress, which
        # sends the answer to the real contact the test envelope named - so retrying a dry
        # run is the one action on this screen that converts a test into a real message to
        # a customer. Refused BEFORE the claim below, so the row is not left marked either.
        # A test turn is re-run from where it was run, with a new envelope.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "test_turn_not_retryable",
                "message": (
                    "This is a test turn. Retry re-posts the original message at the live "
                    "ingress, so it would answer a real contact; run the test again from "
                    "where it was started instead."
                ),
            },
        )

    if row.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Only a failed turn can be retried; this one is {row.status}. "
                "Nothing retries automatically (R4)."
            ),
        )
    if _retry_in_flight(row):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=RETRY_IN_FLIGHT_DETAIL
        )

    now = datetime.now(timezone.utc)
    actor_id = str(current_user.get("id") or "")

    # Claim FIRST, then send. See the docstring - and the claim is the conditional UPDATE
    # itself, not the read above. The read can only ever tell an operator what WAS true;
    # two POSTs a few milliseconds apart both pass it, and the customer is answered twice.
    # This UPDATE matches only a row still free to be claimed, so Postgres serialises the
    # two on the row lock and the loser gets zero rows and the same 409 the pre-check gives.
    # A STALE marker is still claimable, which is why the condition is not just IS NULL.
    claimed = (
        db.query(ChatbotTurn)
        .filter(
            ChatbotTurn.id == turn_id,
            (ChatbotTurn.retry_requested_at.is_(None))
            | (ChatbotTurn.retry_requested_at < _stale_before(now)),
        )
        .update({ChatbotTurn.retry_requested_at: now}, synchronize_session="fetch")
    )
    if not claimed:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=RETRY_IN_FLIGHT_DETAIL
        )
    trace = list(row.trace or [])
    trace.append(
        {
            "kind": "note",  # not a stage: rendered as a footer line, not a timeline row
            "stage": row.stage if row.stage in TURN_STAGES else "sent",
            "status": "ok",
            "started_at": now.isoformat(),
            "ms": 0,
            "summary": "Retry requested by an operator.",
            "why": (
                "The original message was re-posted to the chatbot ingress; it will arrive "
                "as a new turn with the next attempt number."
            ),
            "facts": {
                "attempt": row.attempt + 1,
                "requested_at": now.isoformat(),
                "requested_by": actor_id,
            },
            "error": None,
            "raw": {"retry": True, "from_attempt": row.attempt, "requested_by": actor_id},
        }
    )
    row.trace = trace
    db.commit()

    outcome_status = 200
    error_message: str | None = None
    try:
        reinject_envelope(db, row)
    except (RetryUnavailable, OutboundUrlRejected, ReinjectFailed) as exc:
        # Nothing was accepted by the ingress, so the claim must not stand: another
        # operator has to be able to try again, and a marker nobody will ever clear would
        # make this row permanently un-retryable.
        row.retry_requested_at = None
        row.trace = trace[:-1]
        db.commit()
        # Three refusals, three codes, because they need three different actions.
        # 409: nothing is configured, so there is nothing to fix about THIS turn.
        # 422: something IS configured and it breaks a rule - the URL is the thing to
        #      change, and saying 409 there would send the operator looking for a blank
        #      field that is not blank.
        # 502: the ingress itself refused; try again or look at n8n.
        unavailable = isinstance(exc, RetryUnavailable)
        rejected = isinstance(exc, OutboundUrlRejected)
        outcome_status = 409 if unavailable else (422 if rejected else 502)
        error_message = str(exc)
        _log_retry(db, request, row, actor_id, outcome_status, error_message)
        if unavailable:
            # Not an error the operator caused, and not a 500: this install simply has
            # no ingress wired, which is the correct state for a developer machine.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "retry_unavailable", "message": error_message},
            ) from exc
        if rejected:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "retry_url_rejected", "message": error_message},
            ) from exc
        # Status code and a fixed sentence, never the ingress response body. See
        # `ReinjectFailed`: the body would be a read of whatever the request reached.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_message,
        ) from exc

    _log_retry(db, request, row, actor_id, outcome_status, error_message)
    return RetryTurnResponse(turn_id=str(row.id), attempt=row.attempt + 1)


def _log_retry(
    db: Session,
    request: Request,
    row: ChatbotTurn,
    actor_id: str,
    status_code: int,
    error_message: str | None,
) -> None:
    """An `integration_log` row for the outbound POST, success AND failure.

    Same rule every Respond.io send follows: an outbound call that touched a customer
    leaves a record whichever way it went, so "did we re-send that?" is answerable from
    the database rather than from someone's memory of a button click.
    """
    try:
        IntegrationLogService(db).create_integration_log(
            IntegrationLogCreate(
                integration_channel="n8n",
                business_table="chatbot.turns",
                business_id=str(row.id),
                external_reference=row.contact_respond_id,
                direction="outbound",
                endpoint=str(request.url.path),
                http_method="POST",
                request_payload=json.dumps(
                    {
                        "turn_id": str(row.id),
                        "message_id": row.message_id,
                        "from_attempt": row.attempt,
                        "requested_by": actor_id,
                    }
                ),
                status_code=status_code,
                status="success" if status_code < 400 else "failed",
                error_message=error_message,
            )
        )
    except Exception as exc:  # noqa: BLE001 - the log must never fail the retry
        logger.warning("chatbot retry: integration log failed: %s", exc, exc_info=True)


# --------------------------------------------------------------------------- #
# In-app console (Slice D final, chatbot growth r1)
# --------------------------------------------------------------------------- #


@router.post("/console/turn", response_model=ConsoleTurnResponse)
def console_turn(
    payload: ConsoleTurnRequest,
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Run one dry-run chatbot turn for the console page, in process (D14: is_test=True,
    ingress='console', zero writes outside `chatbot.turns`). See
    `app.services.chatbot.console_service.run_console_turn` for what it does with
    `session_vars` (membership, not truthiness) and `prompt_version_id` (a harness
    override, dry-run only by construction).
    """
    result = console_service.run_console_turn(
        db,
        contact_respond_id=payload.contact_respond_id,
        text=payload.text,
        session_vars=payload.session_vars,
        prompt_version_id=payload.prompt_version_id,
        run_id=payload.run_id,
        media=payload.media.model_dump() if payload.media is not None else None,
    )
    return ConsoleTurnResponse(
        turn_id=result.turn_id,
        branch_kind=result.branch_kind,
        reply_text=result.reply_text,
        quick_replies=result.quick_replies,
        send_messages=result.send_messages,
        session_vars=result.session_vars,
        trace_summary=result.trace_summary,
        media_status=result.media_status,
        media_id=result.media_id,
        media_text=result.media_text,
        media_error=result.media_error,
        prompt_version=result.prompt_version,
        actions=result.actions,
    )


@router.get("/console/media/{media_id}", response_model=ConsoleMediaStatusResponse)
def console_media_status(
    media_id: str,
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Poll fallback for a media turn whose synchronous wait timed out (commit 2). The FE
    calls this every 2s until `status != "pending"`, then sends the extracted `text` as a
    plain console turn. See `console_service.get_console_media_status`.
    """
    status_body = console_service.get_console_media_status(db, media_id)
    return ConsoleMediaStatusResponse(**status_body)


@router.get("/console/prompt-versions", response_model=list[ConsolePromptVersion])
def console_prompt_versions(
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Every version of the chatbot's semantic parser prompt, newest first, so the console
    can pin one for the run (the same `prompt_overrides` harness key the Prompts screen's
    "Run a turn" test uses). `label` is whichever `ai_prompt_labels` row (production,
    staging, ...) currently points at that version, or null for one nothing points at.
    """
    rows = (
        db.query(AIPromptVersion)
        .filter(AIPromptVersion.name == console_service.PARSER_PROMPT_KEY)
        .order_by(AIPromptVersion.version.desc())
        .all()
    )
    labels = (
        db.query(AIPromptLabel.version_id, AIPromptLabel.label)
        .filter(AIPromptLabel.name == console_service.PARSER_PROMPT_KEY)
        .all()
    )
    label_by_version_id: dict[str, str] = {}
    for version_id, label in labels:
        # A version can carry more than one label (production AND staging pointing at the
        # same row); joined rather than picking one arbitrarily, so the console shows both.
        existing = label_by_version_id.get(str(version_id))
        label_by_version_id[str(version_id)] = f"{existing}, {label}" if existing else label
    return [
        ConsolePromptVersion(
            id=str(row.id),
            version=int(row.version),
            label=label_by_version_id.get(str(row.id)),
            chars=len(row.template or ""),
            base=console_service.prompt_base(row.template),
        )
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Prompt blocks: are the published parser blocks still what the rows say? (AC-1552)
# --------------------------------------------------------------------------- #


class PromptBlocksStatus(BaseModel):
    """What the Prompts page's "domain block out of date" banner reads.

    Hash based, not timestamp based: an owner who saves a domain and undoes it has
    changed nothing, and a banner that fired on `updated_at` would say otherwise. The
    rendered blocks ARE the comparison, so the answer is exactly "would publishing now
    change what the parser is told".
    """

    stale: bool
    current_hash: str
    published_hash: str | None
    published_version: int | None
    published_at: datetime | None


@router.get("/prompt-blocks/status", response_model=PromptBlocksStatus)
def prompt_blocks_status(
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Whether `chatbot_domains` / `chatbot_entity_kinds` have moved since the labelled
    parser version was published (AC-1552).

    The comparison is against `production` - the version a customer's turn actually runs
    on. An unlabelled version carrying newer blocks is not what anybody is being answered
    by, so it must not clear the banner.
    """
    _ = current_user
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services.chatbot_parser_prompt import blocks_of, prompt_blocks_hash

    current_hash = prompt_blocks_hash(db)

    label = (
        db.query(AIPromptLabel)
        .filter(
            AIPromptLabel.name == parser_prompt_key(),
            AIPromptLabel.label == "production",
        )
        .first()
    )
    published = (
        db.query(AIPromptVersion).filter(AIPromptVersion.id == label.version_id).first()
        if label is not None
        else None
    )
    if published is None:
        # Nothing is published, so nothing is out of date - there is no version for the
        # rows to disagree with. The banner stays quiet and the Prompts page's own empty
        # state is what says there is no parser version yet.
        return PromptBlocksStatus(
            stale=False,
            current_hash=current_hash,
            published_hash=None,
            published_version=None,
            published_at=None,
        )

    from app.services.chatbot_prompt_vars import is_wording_layer

    if is_wording_layer(published.template):
        # PLAN-prompt-dynamic-30sep D1: this version's lists render from the rows on every
        # turn, so it can never disagree with them.
        return PromptBlocksStatus(
            stale=False,
            current_hash=current_hash,
            published_hash=current_hash,
            published_version=published.version,
            published_at=published.created_at,
        )

    published_hash = (published.config_json or {}).get("blocks_hash")
    if not published_hash:
        # Published before the blocks carried a hash of their own (or by hand, body
        # only): hash what that body actually says, so an older version still answers
        # the question instead of reading as permanently stale.
        published_hash = hashlib.sha256(
            blocks_of(published.template).encode("utf-8")
        ).hexdigest()

    return PromptBlocksStatus(
        stale=published_hash != current_hash,
        current_hash=current_hash,
        published_hash=published_hash,
        published_version=published.version,
        published_at=published.created_at,
    )
