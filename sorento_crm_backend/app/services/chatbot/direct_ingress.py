"""CHAT-SPEED: the CRM's respond.io webhook starts the turn itself (option D).

Before this, a WhatsApp message reached the CRM through n8n: respond.io -> producer
`sorento-main` (contact lookup, redis push) -> dispatcher `sorento-dispatcher` (a 1 s poll)
-> `/chat/turn`. That path cost 2.30 s at p50 (3.53 s p90) between the CRM's own webhook
seeing the message and the turn starting (card `CARD-chat-speed-ingress.md`, dev copy, 11-15
Oct). The CRM already receives every message on its signed webhook
(`app/api/v1/public/respond_webhook.py`), so with the switch on it enqueues the turn there.

Three parts, one module:

* `skip_reason` - the gate the webhook asks, AFTER it has stored the message. None means
  enqueue. Everything else is left to the n8n copy, which keeps running untouched, so a skip
  here costs nothing but the saving.
* `run` - the turn body, on the API process's bounded direct-turn pool
  (`app/tasks/chat_turns.py::_submit`). It looks the contact up, runs the turn with
  `ingress = "respond_webhook"`, then hands the actions to the n8n sender.
* `send` - one POST of the finished actions to the thin n8n sender. **The CRM still does not
  send** (D9): the sender runs the same `action.kind` Switch the spine runs after `/chat/turn`.

**The n8n copy is the fallback, and D15 is what makes it safe.** Every message reaches the CRM
twice. Whichever copy arrives first runs the turn; the later one is answered `duplicate: true`
at once (`engine._duplicate_result`), and n8n's `duplicate` arm sends nothing. So switching
this off, or any skip below, falls back to exactly today's behaviour.

**No automatic retry** (R4). A failed POST writes a `failed` outbound integration log row
(final status, so the integration sweeper, which re-sends only `pending` / `processing`, never
picks it up) and a `sent` trace record on the turn, where Retry is.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from app.services.outbound_url_guard import OutboundUrlRejected, assert_safe_outbound_url

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_CONTACTS = "contacts"
MODE_ALL = "all"
MODES = (MODE_OFF, MODE_CONTACTS, MODE_ALL)

# Owner ruling Q3 (a), 18 Oct: a body older than this by its own clock never starts a turn
# here. respond.io signs the body but not a timestamp, so a captured delivery replays forever;
# the n8n copy still answers anything older, so nothing is lost by refusing it.
STALE_AFTER_SECONDS = 120.0

# The producer's per-contact burst limit (Redis INCR, 1 s TTL, more than 30 is dropped).
RATE_LIMIT_PER_SECOND = 30

# A sender source that is a person or a system, not the customer. respond.io marks the
# customer's own messages `contact`; the rule is a deny list so an unknown future source
# (respond.io renames things) still lets the customer through, guarded by `traffic`.
STAFF_SENDER_SOURCES = frozenset({"user", "api", "workflow", "ai_agent", "echo", "broadcast"})

SENDER_KEY_HEADER = "X-Chatbot-Sender-Key"
# The sender answers as soon as it has accepted the actions; it does not wait on respond.io.
SENDER_TIMEOUT_SECONDS = 10.0
INTEGRATION_CHANNEL = "chatbot_sender"
MAX_LOGGED_BODY_CHARS = 20000

_TRUE_WORDS = frozenset({"true", "1", "yes"})


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def listed_contacts(raw: str | None) -> frozenset[str]:
    """The respond.io contact ids in `chatbot_direct_ingress_contacts` (lines or commas)."""
    return frozenset(token for token in re.split(r"[\s,]+", raw or "") if token)


def message_age_seconds(message: dict[str, Any], now: float | None = None) -> float | None:
    """How old the message is by respond.io's own clock, or None when it carries none.

    The message id IS the send time in epoch microseconds (`conversation_thread_service.
    _message_id_to_ms`); `timestamp` (ms, sometimes s) is the fallback.
    """
    now = time.time() if now is None else now
    sent_ms: int | None = None
    try:
        raw_id = int(str(message.get("messageId")))
    except (TypeError, ValueError):
        raw_id = 0
    if raw_id > 1e14:
        sent_ms = raw_id // 1000
    elif raw_id > 1e11:
        sent_ms = raw_id
    if sent_ms is None:
        try:
            stamp = int(message.get("timestamp"))
        except (TypeError, ValueError):
            stamp = 0
        if stamp > 1e14:
            sent_ms = stamp // 1000
        elif stamp > 1e11:
            sent_ms = stamp
        elif stamp > 0:
            sent_ms = stamp * 1000
    if sent_ms is None:
        return None
    return now - sent_ms / 1000.0


def _human_intervened(contact: dict[str, Any]) -> bool | None:
    """The contact's `is_human_intervened` custom field: True, False, or None when the
    record carries no `custom_fields` at all. Only True stops a turn, as in the producer's
    If1 (a null value passes there too)."""
    fields = contact.get("custom_fields")
    if not isinstance(fields, list):
        return None
    for field in fields:
        if isinstance(field, dict) and field.get("name") == "is_human_intervened":
            value = field.get("value")
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in _TRUE_WORDS if value is not None else False
    return False


def _settings(row: Any) -> tuple[str, frozenset[str]]:
    """The mode and the listed contacts off the settings row or the engine's snapshot."""
    if row is None:
        return MODE_OFF, frozenset()
    mode = str(getattr(row, "chatbot_direct_ingress_mode", None) or MODE_OFF)
    return (mode if mode in MODES else MODE_OFF), listed_contacts(
        getattr(row, "chatbot_direct_ingress_contacts", None)
    )


def covers(row: Any, contact_id: str) -> bool:
    """Does direct ingress apply to this contact? Read by the engine to order its turns."""
    mode, contacts = _settings(row)
    return mode == MODE_ALL or (mode == MODE_CONTACTS and contact_id in contacts)


def skip_reason(
    db: Any,
    payload: dict[str, Any],
    *,
    contact_id: str,
    traffic: str,
    channel: str,
    already_existed: bool,
    hmac_signed: bool,
    now: float | None = None,
) -> str | None:
    """Why this stored message must NOT start a turn here, or None to enqueue it.

    `contact_id`, `traffic` and `channel` are what the webhook already mapped onto the
    `chat_histories` row, so the gate reads the message the way the thread does.
    """
    from app.models.user import SystemSetting

    row = db.query(SystemSetting).first()
    if _settings(row)[0] == MODE_OFF:
        return "off"
    if not covers(row, contact_id):
        return "contact_not_listed"
    if not hmac_signed:
        # Security review M1: the shared-secret header mode sends the key itself on every
        # request, so whoever holds it can write any body, a fresh clock included. Only a
        # body HMAC proves respond.io wrote this message; the n8n copy answers the rest.
        return "unsigned"
    if already_existed:
        # A respond.io retry, or the n8n mirror got there first: a turn is already on its way.
        return "duplicate"
    if traffic != "incoming":
        return "not_incoming"
    if channel != "whatsapp":
        return "not_whatsapp"
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    if str(sender.get("source") or "").strip().lower() in STAFF_SENDER_SOURCES:
        return "not_from_contact"
    age = message_age_seconds(message, now)
    if age is None:
        return "no_clock"
    if age > STALE_AFTER_SECONDS:
        return "stale"
    inner = message.get("message") if isinstance(message.get("message"), dict) else {}
    if str(inner.get("type") or "").strip().lower() != "text":
        # Media stays on the n8n path for now: its intake waits up to the media budget on
        # an extraction job, which would hold one of the few direct-turn threads that long.
        return "not_text"
    if not _sender_url(db):
        # Review S2: the direct turn would win and send nothing, and the n8n copy would
        # come back a duplicate. Without a sender, the n8n path keeps the message.
        return "no_sender"
    # Last, so only a message this path would otherwise take counts against the limit.
    if _over_rate_limit(contact_id):
        return "rate_limited"
    # The takeover flag is NOT in the signed webhook body (crew, 8,434 rows: no
    # `custom_fields`), so the job reads it through the contact lookup, before the turn.
    return None


_RATE_SCRIPT = (
    "local c = redis.call('INCR', KEYS[1]) "
    "if c == 1 then redis.call('EXPIRE', KEYS[1], 1) end "
    "return c"
)


def _sender_url(db: Any) -> str:
    workspace = _default_workspace(db)
    return (getattr(workspace, "chatbot_sender_url", None) or "").strip() if workspace else ""


def rate_key(contact_id: str) -> str:
    return f"chatbot:direct:rate:{contact_id}"


def _over_rate_limit(contact_id: str) -> bool:
    """The producer's own gate (`sorento-main`, crew 18 Oct): more than
    `RATE_LIMIT_PER_SECOND` messages from one contact inside a second. The n8n copy still
    gets the producer's fixed rate-limit reply; this path just stays out of the burst.
    A redis error reads as over the limit: the job could not be enqueued on it anyway."""
    from app.services.queue_service import redis_conn

    key = rate_key(contact_id)
    try:
        # One script, so the TTL is set in the same step as the first increment: an INCR
        # that lost its EXPIRE would hold a contact over the limit forever (review B2).
        count = int(redis_conn.eval(_RATE_SCRIPT, 1, key))
    except Exception:  # noqa: BLE001
        logger.warning("direct ingress: rate limit unreadable for %s", contact_id, exc_info=True)
        return True
    return count > RATE_LIMIT_PER_SECOND


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #


def _contact_id(payload: dict[str, Any]) -> str:
    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(contact.get("id") or message.get("contactId") or "").strip()


def _on_default_workspace(db: Any, contact_id: str) -> bool:
    """Is this contact on the default respond workspace? The sender URL and n8n's single
    respond.io credential speak for that one (security review L3), so a contact stored
    against another workspace stays on the n8n path. No row, or no workspace, reads as the
    default, the same fallback `RespondClient` takes."""
    from app.services.respond_identifier import contact_for_identifier

    contact = contact_for_identifier(db, contact_id)
    workspace_id = getattr(contact, "workspace_id", None) if contact is not None else None
    if not workspace_id:
        return True
    default = _default_workspace(db)
    return default is not None and str(default.id) == str(workspace_id)


def _lookup_contact(db: Any, contact_id: str) -> dict[str, Any] | None:
    """respond.io's contact record, `custom_fields` included: the call n8n's `findcontact`
    makes, so the engine reads the same contact on both paths. None when it fails."""
    from app.services.integration_service import RespondClient

    try:
        # `for_identifier`, not `for_contact_id`: the id here is respond.io's, and the latter
        # matches the CRM's own row id.
        record = RespondClient.for_identifier(db, contact_id).get_contact_by_identifier(contact_id)
    except Exception:  # noqa: BLE001 - the n8n copy answers a message this path cannot
        logger.warning("direct ingress: contact lookup failed for %s", contact_id, exc_info=True)
        return None
    if isinstance(record, dict) and "id" not in record and isinstance(record.get("data"), dict):
        record = record["data"]
    return record if isinstance(record, dict) else None


def envelope_for(payload: dict[str, Any], contact: dict[str, Any]):
    """The turn envelope: the respond.io body verbatim, the looked-up contact record, and
    the ingress that tells the engine to order it."""
    from app.services.chatbot.contracts import DIRECT_INGRESS, Envelope

    block = dict(contact)
    block["id"] = str(block.get("id") or _contact_id(payload))
    return Envelope(message=payload, contact=block, ingress=DIRECT_INGRESS)


def run(payload: dict[str, Any], session_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Run the turn, then hand its actions to the sender. Returns what happened, for the log.

    `{turn_id, status, duplicate, sent, skipped}`. The contact is looked up first; a
    failed lookup or a human takeover runs no turn at all (`skipped` names which). A
    duplicate (the n8n copy ran first) or a turn with no actions sends nothing.
    """
    from app.database import SessionLocal
    from app.services.chatbot import run_turn

    factory = session_factory or SessionLocal
    contact_id = _contact_id(payload)
    skipped = {"turn_id": None, "status": None, "duplicate": False, "sent": False}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    age = message_age_seconds(message)
    if age is None or age > STALE_AFTER_SECONDS:
        # Re-checked here, not only at the webhook (review S5): a turn that waited behind
        # a backlog must not answer what the n8n copy has long since handled.
        return {**skipped, "skipped": "stale"}
    db = factory()
    try:
        if not _on_default_workspace(db, contact_id):
            return {**skipped, "skipped": "non_default_workspace"}
        record = _lookup_contact(db, contact_id)
    finally:
        db.close()
    if record is None:
        return {**skipped, "skipped": "lookup_failed"}
    if _human_intervened(record) is True:
        # The producer drops this message too (its If1), so nobody answers it: as today.
        return {**skipped, "skipped": "human_intervened"}

    envelope = envelope_for(payload, record)
    result = run_turn(envelope, session_factory=factory, offload=False)
    outcome = {
        "turn_id": result.turn_id,
        "status": result.status,
        "duplicate": bool(result.duplicate),
        "sent": False,
        "skipped": None,
    }
    if result.duplicate or not result.actions:
        return outcome
    outcome["sent"] = send(
        factory,
        turn_id=result.turn_id,
        contact_id=str(envelope.contact.get("id") or ""),
        actions=list(result.actions),
        reply=result.reply,
        branch_kind=result.branch_kind,
    )
    return outcome


# --------------------------------------------------------------------------- #
# The send
# --------------------------------------------------------------------------- #


def _default_workspace(db: Any) -> Any:
    """The row the sender target lives on: the DEFAULT workspace, for the same reason the
    retry ingress does (`dispatch._default_workspace`): one n8n instance per install."""
    from app.services.respond_workspace_service import RespondWorkspaceService

    return RespondWorkspaceService(db).get_default()


def send(
    session_factory: Callable[[], Any],
    *,
    turn_id: str,
    contact_id: str,
    actions: list[dict[str, Any]],
    reply: Any,
    branch_kind: str | None,
) -> bool:
    """POST the finished actions to the n8n sender. True when it accepted them. Never raises."""
    import httpx

    from app.services.respond_workspace_service import RespondWorkspaceService

    db = session_factory()
    try:
        workspace = _default_workspace(db)
        url = (getattr(workspace, "chatbot_sender_url", None) or "").strip() if workspace else ""
        if not url:
            _record_failure(
                db,
                turn_id,
                "No chatbot sender webhook is configured on the default workspace, so the "
                "reply was not handed to n8n.",
            )
            return False
        # `/chat/turn`'s own response keys plus `contact_id`, so the sender can run the
        # spine's action executor unchanged (`documentation/n8n/chatbot-sender.md`).
        body = {
            "turn_id": turn_id,
            "contact_id": contact_id,
            "is_test": False,
            "duplicate": False,
            "delegate": None,
            "branch_kind": branch_kind,
            "actions": actions,
            "reply": reply,
        }
        try:
            # Re-checked at USE time, as the retry ingress is: a DNS answer or a directly
            # written row can change under the save-time check.
            assert_safe_outbound_url(url, label="The chatbot sender webhook URL")
        except OutboundUrlRejected as exc:
            _log_call(db, turn_id, contact_id, url, body, None, exc.message)
            _record_failure(db, turn_id, exc.message)
            return False

        headers = {"Content-Type": "application/json"}
        key = RespondWorkspaceService.decrypt_chatbot_sender_key(workspace)
        if key:
            headers[SENDER_KEY_HEADER] = key
        try:
            response = httpx.post(
                url,
                json=body,
                headers=headers,
                timeout=SENDER_TIMEOUT_SECONDS,
                # Explicit: a 30x to a loopback or metadata address would walk past the guard.
                follow_redirects=False,
            )
        except Exception as exc:  # noqa: BLE001 - a transport failure is the sender refusing
            logger.warning("chatbot sender unreachable for turn %s: %s", turn_id, exc)
            error = "The chatbot sender could not be reached."
            _log_call(db, turn_id, contact_id, url, body, None, error)
            _record_failure(db, turn_id, error)
            return False
        if response.status_code >= 300:
            # The response body stays in the server log (see `dispatch.ReinjectFailed`).
            logger.warning(
                "chatbot sender refused turn %s: %s %s",
                turn_id,
                response.status_code,
                response.text[:300],
            )
            error = f"The chatbot sender refused the reply (HTTP {response.status_code})."
            _log_call(db, turn_id, contact_id, url, body, response.status_code, error)
            _record_failure(db, turn_id, error)
            return False
        _log_call(db, turn_id, contact_id, url, body, response.status_code, None)
        return True
    except Exception:  # noqa: BLE001 - the turn is answered; describing the send must not raise
        logger.exception("chatbot sender: unexpected failure for turn %s", turn_id)
        return False
    finally:
        db.close()


def _log_call(
    db: Any,
    turn_id: str,
    contact_id: str,
    url: str,
    body: dict[str, Any],
    status_code: int | None,
    error: str | None,
) -> None:
    """One outbound `integration_log` row per attempted POST, in a FINAL status."""
    from app.schemas.integration import IntegrationLogCreate
    from app.services.integration_service import IntegrationLogService

    try:
        IntegrationLogService(db).create_integration_log(
            IntegrationLogCreate(
                integration_channel=INTEGRATION_CHANNEL,
                business_table="chatbot.turns",
                business_id=turn_id,
                external_reference=contact_id,
                direction="outbound",
                # No query string: an admin could have put a token there.
                endpoint=url.split("?", 1)[0][:512],
                http_method="POST",
                request_headers=json.dumps({"Content-Type": "application/json"}),
                request_payload=json.dumps(body, default=str)[:MAX_LOGGED_BODY_CHARS],
                status_code=status_code,
                status="success" if error is None else "failed",
                error_message=error,
            )
        )
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("chatbot sender: failed to write the integration log", exc_info=True)


def _record_failure(db: Any, turn_id: str, error: str) -> None:
    """A failed `sent` record appended to the turn's trace, so the turn screen shows it."""
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import trace as trace_mod

    try:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
        if row is None:
            return
        record = trace_mod.TurnTrace()
        record.start()
        record.record(
            "sent",
            status="failed",
            summary="The reply was not handed to the n8n sender.",
            why="A turn the respond.io webhook started is sent by the n8n sender workflow.",
            facts={"ingress": "respond_webhook"},
            error=error,
        )
        row.trace = [*(row.trace or []), *record.persisted()]
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("chatbot sender: failed to record the send failure", exc_info=True)
