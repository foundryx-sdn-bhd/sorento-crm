"""In-app chatbot console: one dry-run turn, in process (Slice D final, chatbot growth r1).

`run_console_turn` is what `POST /api/v1/system/chatbot/console/turn` calls: it borrows the
contact's most recent stored envelope (a turn reads that shape at `received`, and an
invented one fails there for a field it does not carry - the same reason
`scripts/chatbot_console_check.py::_base_envelope` borrows one instead of building one from
nothing), stamps this turn's text onto it, marks it a DRY RUN two ways (`is_test=True`,
`test_run_id=run_id` - D14: zero writes outside `chatbot.turns`), and calls `run_turn` IN
PROCESS - no HTTP hop, because the caller (the FastAPI route) and the engine are the same
process here. The ad-hoc script instead posts to a URL, because ITS whole point is grading
whichever backend PROCESS is actually serving traffic, local or remote - an in-process call
there would silently stop testing the thing it exists to test.

Two named exceptions to the "zero writes outside `chatbot.turns`" claim above exist, each
stated at its own site rather than folded into this summary: media (below) and the
`ideate` lane's test turns (#1179, below the media block).

**Deliberately NOT shared code with `scripts/chatbot_console_check.py`.** The two modules'
shapes look alike - both borrow an envelope, both force the two lane switches on for one
call and restore them in a `finally`, both honour `previous_conversation_state` /
`prompt_overrides` - because both solve the same problem (a harness turn with no real
WhatsApp delivery), not because one wraps the other. `tests/chatbot/test_import_boundary.py`
(AC-002) does not list the script among the files allowed to import `app.services.chatbot`,
and that boundary is right for it: an in-process call would defeat the script's own stated
purpose ("a REAL turn through a REAL backend"). Folding the two together was considered and
rejected for that reason - see the coder's report on this change for where that is written
up. If the two ever need to agree on a THIRD behaviour, the shared piece belongs in
`contracts.py`, which both already read.

Every call flips `system_settings.chatbot_business_lane_enabled` and
`chatbot_completed_lanes` ON for the one turn and restores the PRIOR values in a `finally`,
the same rule the script's `_lanes_on` states: without it a turn whose branch is not in
`chatbot_completed_lanes` comes back with an empty reply (delegated to n8n), which would
grade the handoff rather than the answer - and a console with a permanently-empty reply
teaches nobody anything. Scoped to the single call, on a session of its own, never left
flipped for the next request even if the turn itself raises.
"""
from __future__ import annotations

import base64
import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.chatbot_turn import ChatbotTurn
from app.models.user import SystemSetting
from app.services.chatbot import run_turn
from app.services.chatbot.contracts import BRANCH_KINDS, TurnRequest
from app.services.chatbot.lanes.escalation import routing_line
from app.services.error_handler import AppException

# S8a's own key (`ai_prompt_versions.name` for the chatbot's semantic parser), restated
# here rather than imported from `head.parser` for the same reason the script restates it:
# this is the module's own outward-facing constant, and `head.parser` is an internal one.
PARSER_PROMPT_KEY = "chatbot_semantic_parser"

# The two envelope fields excluded from `args_short` in the trace summary: neither is
# useful to an operator reading a console reply (an internal view flag and identifiers the
# reply already carries in its own shape), and `_diagnostics` is a debug bag the tool
# itself may attach. Mirrors the script's own `_trace_line` filter so the two present the
# same trace to a human either way it was run.
_TRACE_ARGS_DROPPED = ("view", "contact_id", "space_id", "_diagnostics")


class ConsoleContactUnknown(AppException):
    """No stored turn for this contact: there is no envelope shape to borrow."""

    def __init__(self, contact_respond_id: str) -> None:
        super().__init__(
            status_code=404,
            message="This contact has no chatbot turn to borrow a session from.",
            detail=(
                f"no chatbot.turns envelope exists yet for contact {contact_respond_id!r}. "
                "Send one real WhatsApp message from this contact first, or pick a "
                "different contact."
            ),
            code="CHATBOT_CONSOLE_NO_ENVELOPE",
        )


class ConsoleTurnResult:
    """What `run_console_turn` hands the route: exactly the shape `ConsoleTurnResponse`
    declares, kept as a plain object so the service stays free of the wire schema."""

    __slots__ = (
        "turn_id",
        "branch_kind",
        "reply_text",
        "quick_replies",
        "send_messages",
        "session_vars",
        "trace_summary",
        # Media (commit 2): all None on a plain text turn. `media_status` is one of
        # "pending" | "done" | "failed" only when a `media` attachment was sent.
        "media_status",
        "media_id",
        "media_text",
        "media_error",
        # Item 6: the parser prompt version the turn ran, off the `understood` stage.
        "prompt_version",
        # The turn's own raw `actions`, carried through unfiltered - see
        # `ConsoleTurnResponse.actions`'s own docstring.
        "actions",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))


def _borrow_envelope(db: Session, contact_respond_id: str) -> dict[str, Any]:
    """The contact's most recent REAL INBOUND envelope, as the shape to borrow.

    Raises rather than inventing one: a hand-built envelope missing a field the engine
    reads at `received` fails there, and a console whose failures are its own bug is worse
    than no console. The phone is filled in from `respond_contacts` when the borrowed
    envelope carries none, the same backfill `chatbot_console_check.py::_base_envelope`
    does - the escalation lane's assignee read is a 400 without it.

    **Only a `webhook` ingress row that is not a test.** A console turn stores its OWN
    envelope back onto `chatbot.turns`, so borrowing "the newest row of any kind" makes
    the console borrow from itself: one hand-built or in-process envelope with a thin
    `contact` block poisons every console turn for that contact afterwards, and the
    fields it is missing are read as ABSENT rather than as never-recorded. Measured on
    the hand-pass clone (16 Sep 2026): an in-process smoke turn wrote
    `contact.custom_fields: []`, so `engine._stock_check_denied` read `is_allowed_stock`
    as missing on every console turn since and "check stock srtwc286" answered with the
    demand-quantity ask instead of the stock. Contract 61/62 is right; what was wrong is
    that the console was replaying a synthetic envelope as if respond.io had sent it.
    The last real inbound envelope for that contact carries the field.

    **Through the ORM model, not raw SQL naming the `chatbot` schema.** A schema-qualified
    `FROM chatbot.turns` bypasses `search_path` entirely, so under a test's translated
    scratch schema (`tests/_pg_fixture.py`) it would silently read the REAL, shared
    `chatbot.turns` table instead of the isolated one - the same real-DB-read finding
    `test_chat_turn_endpoint.py::_turns_count` documents. `ChatbotTurn.__table_args__`
    carries `schema="chatbot"` as an ORM construct, which IS translated correctly.
    """
    row = (
        db.query(ChatbotTurn)
        .filter(
            ChatbotTurn.contact_respond_id == str(contact_respond_id),
            ChatbotTurn.envelope.isnot(None),
            ChatbotTurn.ingress == "webhook",
            ChatbotTurn.is_test.is_(False),
        )
        .order_by(ChatbotTurn.created_at.desc())
        .first()
    )
    if row is None:
        raise ConsoleContactUnknown(contact_respond_id)
    phone = db.execute(
        sa_text("SELECT phone_number FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": str(contact_respond_id)},
    ).scalar()
    envelope = dict(row.envelope)
    stored = envelope.get("contact") or {}
    if not stored.get("phone") and phone:
        envelope["contact"] = {**stored, "phone": phone}
    return envelope


def _build_envelope(
    base: dict[str, Any],
    *,
    contact_respond_id: str,
    message_text: str,
    run_id: str,
    session_vars: dict[str, Any] | None,
    prompt_version_id: str | None,
) -> dict[str, Any]:
    """The borrowed envelope with this turn's words in it. Never mutates `base`.

    Mirrors `chatbot_console_check.py::_envelope_for` field for field: same `is_test` /
    `test_run_id` markers (D14), the same `previous_conversation_state` MEMBERSHIP rule
    (`session_vars=None` omits the key so the engine falls back to the contact's stored
    session; `session_vars={}` says "this contact remembers nothing", which is what a
    console Reset sends), and the same `prompt_overrides` harness key the Prompts screen's
    "Run a turn" button uses (dry-run only by construction, `engine._prompt_override`).
    """
    envelope = json.loads(json.dumps(base))
    envelope["contact"] = {**(envelope.get("contact") or {}), "id": str(contact_respond_id)}
    envelope.setdefault("message", {})
    envelope["message"]["contact"] = {"id": str(contact_respond_id)}
    inner = envelope["message"].setdefault("message", {})
    inner["contactId"] = str(contact_respond_id)
    inner["messageId"] = f"console-{uuid.uuid4().hex[:12]}"
    inner["message"] = {"type": "text", "text": message_text}
    envelope["message"]["event_type"] = "message.received"
    envelope["is_test"] = True
    envelope["test_run_id"] = run_id
    envelope["ingress"] = "console"
    envelope["console_origin"] = True
    envelope["shadow_of"] = None
    if session_vars is not None:
        envelope["previous_conversation_state"] = session_vars
    else:
        envelope.pop("previous_conversation_state", None)
    if prompt_version_id:
        envelope["prompt_overrides"] = {PARSER_PROMPT_KEY: str(prompt_version_id)}
    else:
        envelope.pop("prompt_overrides", None)
    return envelope


def _read_switches(db: Session) -> tuple[bool, Any]:
    row = db.query(SystemSetting).first()
    if row is None:
        return False, []
    return bool(row.chatbot_business_lane_enabled), row.chatbot_completed_lanes


def _write_switches(db: Session, enabled: bool, lanes: Any) -> None:
    row = db.query(SystemSetting).first()
    if row is None:
        return
    row.chatbot_business_lane_enabled = enabled
    row.chatbot_completed_lanes = list(lanes or [])
    db.commit()


@contextmanager
def _lanes_on() -> Iterator[None]:
    """Business lane on and every branch answered FOR THIS ONE CALL, restored after.

    On a FRESH session per read/write, each closed immediately - never one session held
    open across the `yield`. `run_turn` opens and closes many of its own sessions on the
    same underlying test connection (`tests/_pg_fixture.py`'s savepoint-per-`Session`
    scheme); a session left idle-but-open here for the whole turn interleaves its
    save point with theirs and can lose a write the turn made, discovered when the D14
    zero-writes test for this endpoint undercounted `chatbot.turns` by exactly the row this
    context manager's own held-open session's savepoint had shadowed. Same restore rule as
    `chatbot_console_check.py::_lanes_on` otherwise, without that script's production
    refusal: this endpoint is gated by `system.chat_history.view` rather than an operator's
    own shell, so the permission system is what decides who may run a console turn at all.
    """
    db = SessionLocal()
    try:
        before_enabled, before_lanes = _read_switches(db)
    finally:
        db.close()
    db = SessionLocal()
    try:
        _write_switches(db, True, list(BRANCH_KINDS))
    finally:
        db.close()
    try:
        yield
    finally:
        db = SessionLocal()
        try:
            _write_switches(db, before_enabled, before_lanes or [])
        finally:
            db.close()


def _next_state(body: dict[str, Any]) -> dict[str, Any] | None:
    """What the NEXT turn remembers: the sealed reply's own `variables`."""
    patch = (body.get("reply") or {}).get("session_patch")
    if not isinstance(patch, dict):
        patch = body.get("session_patch")
    if not isinstance(patch, dict):
        return None
    variables = patch.get("variables")
    return variables if isinstance(variables, dict) else patch


def _customer_texts(body: dict[str, Any]) -> tuple[str, list[str]]:
    """`(reply_text, send_messages)`: the primary bubble, then any EXTRA ones.

    `actions` carries every `send_message` the turn composed, and on most lanes the first
    one is the SAME text as `reply.text` (the tail builds one from the other) - rendering
    both would show a customer's answer twice. The escalation lane is the exception
    (`includeResponse: false`): `reply.text` is empty and the two sentences live only in
    `actions`, so nothing is dropped there.
    """
    reply = body.get("reply") or {}
    reply_text = reply.get("text") or ""
    action_texts = [
        a.get("text")
        for a in (body.get("actions") or [])
        if isinstance(a, dict) and a.get("kind") == "send_message" and isinstance(a.get("text"), str) and a.get("text")
    ]
    # A long reply goes out as its marked parts (`engine.split_send_actions`): the
    # console shows them the way the customer gets them, one bubble per part.
    from app.services.chatbot.engine import split_marked_message

    parts = split_marked_message(reply_text) if reply_text else []
    if len(parts) > 1 and action_texts[: len(parts)] == parts:
        return parts[0], action_texts[1:]
    if reply_text and action_texts and action_texts[0] == reply_text:
        action_texts = action_texts[1:]
    return reply_text, action_texts


def _quick_replies(body: dict[str, Any]) -> list[str]:
    """AC-507: `quick_replies` is a comma-joined string or null on the wire, never a list."""
    raw = (body.get("reply") or {}).get("quick_replies")
    if not raw or not isinstance(raw, str):
        return []
    return [chip.strip() for chip in raw.split(",") if chip.strip()]


def _trace_summary(db: Session, turn_id: str | None) -> dict[str, Any]:
    """The tool call, the cross-domain rungs and the dropped reveals, off the persisted
    trace (`TurnTrace.persisted()` interleaves stage records with the `tool` / `crossdomain`
    / `reveals` events `trace.add` writes). Same source `chatbot_console_check.py::
    _trace_line` reads for its one-line summary; this reshapes the same events for the
    console page's trace field instead of a printed string.
    """
    empty: dict[str, Any] = {
        "tool": None,
        "args_short": None,
        "crossdomain_rungs": [],
        "reveals_dropped": [],
        "routing_line": None,
    }
    if not turn_id:
        return empty
    # Through the ORM model, not raw SQL - see `_borrow_envelope`'s docstring for why a
    # schema-qualified `chatbot.turns` string is wrong under a test's translated schema.
    row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
    records = [e for e in ((row.trace if row else None) or []) if isinstance(e, dict)]
    events = [e for e in records if e.get("kind")]
    # Fix round 3 (the owner's 27 Sep retest): an escalation's draw, one readable line off
    # its `looked_up` stage, so the console turn says where it went without the trace.
    routing = next(
        (
            (r.get("facts") or {}).get("routing")
            for r in reversed(records)
            if r.get("stage") == "looked_up" and isinstance(r.get("facts"), dict)
        ),
        None,
    )
    tool: str | None = None
    args_short: dict[str, Any] | None = None
    crossdomain_rungs: list[str] = []
    reveals_dropped: list[str] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event["kind"] == "tool" and tool is None:
            tool = payload.get("name")
            args_short = {
                k: v for k, v in (payload.get("args") or {}).items() if k not in _TRACE_ARGS_DROPPED
            }
        elif event["kind"] == "crossdomain":
            crossdomain_rungs.append(str(payload.get("rung") or payload.get("tool") or ""))
        elif event["kind"] == "reveals":
            reveals_dropped.extend(str(d) for d in (payload.get("dropped") or []))
    return {
        "tool": tool,
        "args_short": args_short,
        "crossdomain_rungs": [r for r in crossdomain_rungs if r],
        "reveals_dropped": reveals_dropped,
        "routing_line": routing_line(routing),
    }


def _empty_trace_summary() -> dict[str, Any]:
    return {
        "tool": None,
        "args_short": None,
        "crossdomain_rungs": [],
        "reveals_dropped": [],
        "routing_line": None,
    }


def trace_prompt_version(trace: Any) -> int | None:
    """The parser prompt version a turn ran: the `understood` stage record's
    `facts.prompt_version` (engine.py writes it from `parser_config.prompt_version`; the
    trace screen's `trace_detail._parse` reads the same field). None when the turn never
    reached the parser or the record carries no version."""
    for record in trace or []:
        if not isinstance(record, dict) or record.get("kind") is not None:
            continue
        if record.get("stage") == "understood":
            facts = record.get("facts") if isinstance(record.get("facts"), dict) else {}
            value = facts.get("prompt_version")
            return int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    return None


def _turn_prompt_version(db: Session, turn_id: str | None) -> int | None:
    if not turn_id:
        return None
    row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
    return trace_prompt_version(row.trace if row else None)


_BASE_PROBE_CHARS = 200

# The retired S1b slim rewrite's own opening 200 characters (chatbot turn
# re-architecture S0, AC-1506: the slim constant is gone from the live module, but
# every "compact"-lineage row published before this lane still needs to classify -
# see `alembic/_legacy_prompt_bodies.py` for the full retired body).
_COMPACT_LINEAGE_HEAD = (
    "You are the Sorento Semantic Parser. You are given:\n"
    "- Previous response: the assistant's last message (may be \"(none)\").\n"
    "- previous_conversation_state: the prior state (team, agent, domain, access level"
)


def prompt_base(template: Any) -> str:
    """Which lineage a parser prompt version belongs to: "full" (the live-derived body,
    `SEMANTIC_PARSER_PROMPT`), "compact" (the retired S1b slim rewrite) or "other". The
    two bodies diverge inside their first 200 characters ("...last message to the user
    (may be" against "...last message (may be"), and every published version of either
    lineage is that body with rules appended or woven in further down, so the opening is
    the lineage. Item 6 (8 Sep 2026): the console defaults to the newest "full" rather
    than to the production label, which locally sits on the compact one."""
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    head = template[:_BASE_PROBE_CHARS] if isinstance(template, str) else ""
    if not head:
        return "other"
    if head == SEMANTIC_PARSER_PROMPT[:_BASE_PROBE_CHARS]:
        return "full"
    if head == _COMPACT_LINEAGE_HEAD[:_BASE_PROBE_CHARS]:
        return "compact"
    return "other"


def run_console_turn(
    db: Session,
    *,
    contact_respond_id: str,
    text: str,
    session_vars: dict[str, Any] | None = None,
    prompt_version_id: str | None = None,
    run_id: str,
    media: dict[str, Any] | None = None,
) -> ConsoleTurnResult:
    """Run one dry-run turn for the in-app console page, in process.

    `db` is used only for the reads that never touch the engine's own sessions
    (borrowing the envelope, reading the trace back after, and - with `media` - the
    real media pipeline's ledger/job writes, section below). The engine opens and closes
    its own sessions around the turn, the same reason `/external/chat/turn` hands it
    `SessionLocal` rather than the request session.

    `media` (commit 2): `{kind, filename, mime, content_base64}`. Routes through
    `_run_console_media_turn`, which is the real `/external/media/process` decide-and-wait
    path, not a second extractor - see that function's own docstring for the shape and the
    D14 deviation it carries.
    """
    if media is not None:
        return _run_console_media_turn(
            db,
            contact_respond_id=contact_respond_id,
            caption=text,
            session_vars=session_vars,
            prompt_version_id=prompt_version_id,
            run_id=run_id,
            media=media,
        )

    base = _borrow_envelope(db, contact_respond_id)
    # Same rule `chat.py`'s `/turn` route states and applies before calling `run_turn`:
    # the read above left `db` mid-transaction, and holding that open for the whole turn
    # - which opens and closes several sessions of its OWN on `SessionLocal` - pins a
    # connection (and, under a test's shared-connection savepoint scheme, can shadow a
    # write one of those sessions made) for no reason once the read is done with.
    db.rollback()
    envelope_dict = _build_envelope(
        base,
        contact_respond_id=contact_respond_id,
        message_text=text,
        run_id=run_id,
        session_vars=session_vars,
        prompt_version_id=prompt_version_id,
    )
    envelope = TurnRequest(envelope=envelope_dict).envelope

    with _lanes_on():
        result = run_turn(envelope, session_factory=SessionLocal)
    body = result.as_dict()

    reply_text, send_messages = _customer_texts(body)
    return ConsoleTurnResult(
        turn_id=body.get("turn_id"),
        branch_kind=body.get("branch_kind"),
        reply_text=reply_text,
        quick_replies=_quick_replies(body),
        send_messages=send_messages,
        session_vars=_next_state(body),
        trace_summary=_trace_summary(db, body.get("turn_id")),
        prompt_version=_turn_prompt_version(db, body.get("turn_id")),
        actions=body.get("actions"),
    )


# --------------------------------------------------------------------------- #
# Media (chatbot media-into-turn, S2): one path, WhatsApp and console alike.
#
# The engine now runs the WHOLE decide/meter/enqueue/wait/extract pipeline itself,
# inside `run_turn` (`app/services/chatbot/media_intake.py`) - so this console path's
# only remaining job is getting the console's base64 bytes somewhere the engine's own
# fetch can reach them, and building the SAME attachment envelope shape a real
# WhatsApp media message carries (`message.message.message.attachment{type,url,
# mimeType,description}`). `run_turn` decides, meters, records, enqueues and waits;
# there is no second copy of that logic here any more.
#
# **Deviation from D14, unchanged from before this collapse.** Every OTHER console
# write is `chatbot.turns` only (`is_test=True`, zero writes elsewhere). Media cannot
# make that promise: `contact_media_usage` (the ledger) and `media_extraction_job`
# have no `is_test` column, so a console media send CONSUMES the picked contact's real
# monthly quota and writes a real ledger row, the same as a WhatsApp photo would.
# Acceptable for a manual testing tool used sparingly; stated here so it is a known
# trade-off, not a surprise.
# --------------------------------------------------------------------------- #

# **Deviation from D14, added #1179 (PR #1182).** A console turn that routes to the
# `ideate` lane is not a zero-write dry run either, for the same reason media is not:
# `crm_ideation_turn` is a real business tool, called with `is_test=True` rather than
# stood in with a placeholder, so ideation is exercisable anywhere but live WhatsApp.
# One console ideate turn writes: an idea row in the shared service (flagged `is_test`,
# hidden from the board by default), an `integration_log` row from the
# `/external/ideation/turn` endpoint, a storage upload when the turn is a media-selection
# answer (`snapshot_and_caption`, the same real upload a WhatsApp photo pick makes), and
# spends one real LLM extraction (`extract_ideate_turn`, never stubbed here). Unlike
# media, the CONTACT's own `respond_contacts.session_vars` stays untouched on a test turn
# (`ideation_capture_service.handle_capture_turn`'s `is_test` guard) - only the shared-service side
# writes. Acceptable for the same reason media's exception is: a manual testing tool used
# sparingly, stated here so it is a known trade-off, not a surprise.
# --------------------------------------------------------------------------- #

# Console media never claims to BE a respond.io modality string. The real ledger's
# `modality` column is `image | voice` (`ContactMediaUsage`); the composer's own vocabulary
# is `image | audio` (matches the owner's ask, "image/voice", and the file-picker's own
# `accept` groups); the WIRE attachment `type` respond.io itself uses is `image | audio`
# too (`media_intake.detect`'s own vocabulary) - so `kind` doubles as the attachment type
# and only the LEDGER's own column needs translating.
_MODALITY_TO_LEDGER = {"image": "image", "audio": "voice"}


def _upload_console_media(*, kind: str, filename: str, mime: str, content_base64: str) -> str:
    """Decode and upload console media bytes, return the URL the extractor fetches from.

    Uses whichever provider `STORAGE_DEFAULT_PROVIDER` points at, the same call
    `app/api/v1/resources/attachments.py`'s upload route makes
    (`backend.upload_file(file_content=..., file_path=..., content_type=...)`), under a
    `chatbot-console/` prefix so a bucket listing can tell these apart from tracked
    attachments at a glance. A signed URL, not the bare CDN one: the extractor's fetch is
    an anonymous `httpx.get` with no auth header of its own, the same as it would carry
    for a real (already-public) respond.io CDN link.
    """
    import base64 as base64_mod

    from app.services.storage_router import cdn_base_url, default_provider, get_backend, resolve_signed_url

    raw = base64_mod.b64decode(content_base64)
    provider = default_provider()
    backend = get_backend(provider)
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ("jpg" if kind == "image" else "webm")
    key = f"chatbot-console/{uuid.uuid4().hex}.{ext}"
    s3_key, _ = backend.upload_file(file_content=raw, file_path=key, content_type=mime)
    # `resolve_signed_url` takes the STORED path (the stable CDN url `cdn_base_url`
    # returns, the same shape `attachments.file_path` holds), not the bare key - see
    # `app/api/v1/resources/attachments.py::_resolve_attachment_file_path`.
    stored_path = cdn_base_url(provider, s3_key)
    url = resolve_signed_url(stored_path, provider=provider, expires_in=900)
    if not url:
        raise AppException(
            status_code=502,
            message="Could not prepare the attachment for reading.",
            detail=f"resolve_signed_url returned nothing for {provider}:{s3_key}",
            code="CHATBOT_CONSOLE_MEDIA_UPLOAD_FAILED",
        )
    return url


def _media_intake_result_of(turn_id: str | None) -> dict[str, Any] | None:
    """The persisted `media_intake` trace stage's `facts` AND `raw` MERGED into one
    dict, for the console's own `media_status`/`media_id`/`media_text`/`media_error`
    fields - read back off the row `run_turn` just wrote, never recomputed.

    Merged rather than `facts` alone (review round S5): `job_id` and the full
    `result` now live under the record's `raw` (never printed on the trace
    screen - `facts` alone is display-safe), but this console-internal reader is
    not a display surface and still needs both, the same shape it always read.
    """
    if not turn_id:
        return None
    # Review round S8: closed rather than left open - this is a SHORT read, not a
    # session the caller goes on to use.
    db = SessionLocal()
    try:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
    finally:
        db.close()
    for record in (row.trace if row else None) or []:
        if isinstance(record, dict) and record.get("stage") == "media_intake":
            facts = record.get("facts") or {}
            raw = record.get("raw") or {}
            return {**facts, **raw}
    return None


def _run_console_media_turn(
    db: Session,
    *,
    contact_respond_id: str,
    caption: str,
    session_vars: dict[str, Any] | None,
    prompt_version_id: str | None,
    run_id: str,
    media: dict[str, Any],
) -> ConsoleTurnResult:
    """Upload the console's bytes, build the SAME attachment envelope shape a real
    WhatsApp media message carries, and hand it to `run_turn` - the engine now runs the
    whole decide/meter/enqueue/wait/extract pipeline itself (S2's `media_intake`
    module), so this function's only job is getting the bytes somewhere that pipeline's
    own fetch can reach and shaping the envelope. One path, console and WhatsApp alike
    (AC-1816): no borrowed envelope, no `_lanes_on` bypass of its own - `run_turn`
    forces the same lane switches through `_run_console_media_turn`'s caller,
    `run_console_turn`, exactly as the text path does.
    """
    kind = media.get("kind") if media.get("kind") in ("image", "audio") else "image"
    modality = _MODALITY_TO_LEDGER[kind]
    mime = str(media.get("mime") or "")
    content_base64 = str(media.get("content_base64") or "")
    media_url = _upload_console_media(
        kind=kind,
        filename=str(media.get("filename") or ""),
        mime=mime,
        content_base64=content_base64,
    )
    size = len(base64.b64decode(content_base64)) if content_base64 else None

    envelope_dict: dict[str, Any] = {
        "contact": {"id": str(contact_respond_id)},
        "message": {
            "event_type": "message.received",
            "contact": {"id": str(contact_respond_id)},
            "message": {
                "messageId": f"console-media-{uuid.uuid4().hex[:12]}",
                "contactId": str(contact_respond_id),
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {
                    "type": "attachment",
                    "attachment": {
                        "type": kind,
                        "url": media_url,
                        "mimeType": mime,
                        **({"size": size} if size is not None else {}),
                        **({"description": caption} if caption.strip() else {}),
                    },
                },
            },
        },
        "is_test": True,
        "test_run_id": run_id,
        "ingress": "console",
        "console_origin": True,
    }
    if session_vars is not None:
        envelope_dict["previous_conversation_state"] = session_vars
    if prompt_version_id:
        envelope_dict["prompt_overrides"] = {PARSER_PROMPT_KEY: str(prompt_version_id)}

    envelope = TurnRequest(envelope=envelope_dict).envelope

    with _lanes_on():
        result = run_turn(envelope, session_factory=SessionLocal)
    body = result.as_dict()

    reply_text, send_messages = _customer_texts(body)
    intake_facts = _media_intake_result_of(body.get("turn_id"))
    media_status: str | None = None
    media_id: str | None = None
    media_text: str | None = None
    media_error: str | None = None
    if intake_facts is not None:
        status = intake_facts.get("status")
        media_status = "done" if status == "completed" else "failed" if status == "failed" else "pending"
        # The console's OWN poll route (`get_console_media_status`) is keyed by
        # `MediaExtractionJob.id`, not the turn's id - the job_id `media_intake.run`
        # stashed on the trace facts is what a `pending` caller re-polls with below.
        media_id = intake_facts.get("job_id")
        job_result = intake_facts.get("result") or {}
        media_text = job_result.get("rendered_text") or job_result.get("transcript") or None
        if media_status == "failed":
            # `extraction_error` is the job's own raw error string, when the intake
            # got as far as running an extraction that then failed - never shown to
            # a real customer (whose reply stays the friendly `wording.nothing_read`/
            # `voice_unclear` text), but this IS the console's own diagnostic surface,
            # so `reply_text` - the SAME denial wording the turn actually replied with
            # (`wording.nothing_read`/`voice_unclear`/a gate notice) - is the fallback
            # for a denial that never ran an extraction at all (gate/quota/burst/no-url;
            # review round nit: never a second, unrelated generic sentence).
            media_error = (
                intake_facts.get("extraction_error")
                or reply_text
                or "This contact's media could not be read."
            )
            media_text = None

    return ConsoleTurnResult(
        turn_id=body.get("turn_id"),
        branch_kind=body.get("branch_kind"),
        reply_text=reply_text,
        quick_replies=_quick_replies(body),
        send_messages=send_messages,
        session_vars=_next_state(body),
        trace_summary=_trace_summary(db, body.get("turn_id")),
        prompt_version=_turn_prompt_version(db, body.get("turn_id")),
        actions=body.get("actions"),
        media_status=media_status,
        media_id=media_id,
        media_text=media_text,
        media_error=media_error,
    )


class ConsoleMediaJobUnknown(AppException):
    """No such job - a stale poll, or a `media_id` that was never a job id."""

    def __init__(self, media_id: str) -> None:
        super().__init__(
            status_code=404,
            message="No such media job.",
            detail=f"no media_extraction_job row for id {media_id!r}",
            code="CHATBOT_CONSOLE_MEDIA_JOB_NOT_FOUND",
        )


def get_console_media_status(db: Session, media_id: str) -> dict[str, Any]:
    """`GET /console/media/{media_id}` - the poll the FE falls back to once a media
    turn's synchronous wait timed out. Safe to call repeatedly; no side effects.
    """
    from app.models.media import ContactMediaUsage, MediaExtractionJob

    try:
        # Scoped to CONSOLE jobs (review round 2, nit 2): the console stamps its usage
        # rows `message_id = "console-media-<hex>"` (`_run_console_media_turn`), so a
        # chat-history viewer holding this permission cannot poll a real customer's
        # extraction by guessing a job id. A real job is a 404 here, the same as none.
        row = (
            db.query(MediaExtractionJob)
            .join(ContactMediaUsage, ContactMediaUsage.id == MediaExtractionJob.usage_id)
            .filter(
                MediaExtractionJob.id == media_id,
                ContactMediaUsage.message_id.like("console-media-%"),
            )
            .first()
        )
    except Exception:  # noqa: BLE001 - a malformed id is a miss, not a 500
        db.rollback()
        row = None
    if row is None:
        raise ConsoleMediaJobUnknown(media_id)
    if row.status in ("queued", "running"):
        return {"status": "pending", "text": None, "error": None}
    if row.status == "failed":
        return {"status": "failed", "text": None, "error": row.error or "Extraction failed."}
    job_result = row.result or {}
    text = job_result.get("rendered_text") or job_result.get("transcript") or None
    return {"status": "done", "text": text, "error": None}
