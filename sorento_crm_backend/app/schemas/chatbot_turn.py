"""Response shapes for the turn trace admin API (AC-257).

**Every field the screen reads is declared here.** `response_model` silently DROPS an
undeclared field, and the two that would go first - `trace` and `response` - are the whole
point of the screen: a trace list that arrives empty looks like "the engine recorded
nothing", not like "the schema forgot to say the column exists".

`trace` records and `response` stay loosely typed on purpose. Their shape is the ENGINE's
(`app/services/chatbot/trace.py`, `contracts.py`), the turn rows already in the table were
written by earlier versions of it, and a strict model here would drop a field the day the
engine adds one - the exact failure this file exists to prevent, arriving through the back
door.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatbotTurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contact_respond_id: str
    message_id: str | None = None
    ingress: str
    status: str
    stage: str | None = None
    branch_kind: str | None = None
    error: str | None = None
    attempt: int
    is_test: bool
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_requested_at: datetime | None = None
    # The stage records the timeline renders. `[]` when the engine wrote none.
    trace: list[dict[str, Any]] = []
    # The answer the turn returned: `{ctx, item, actions}` today, `{reply, actions}` from
    # S3. Null on a turn that failed or is still running.
    response: dict[str, Any] | None = None
    # The photo or voice note this turn read (chatbot media-into-turn, S4). Built by
    # the route from the turn's own `media_intake` trace stage - never an ORM column,
    # so a plain `model_validate(row)` never fills it; the route merges it in by hand
    # (`_media_block`). Null on a text turn. Declared here or `response_model` drops
    # it even when the route DOES set it (the lesson this file's own docstring names).
    media: dict[str, Any] | None = None


class ChatbotTurnDetailResponse(ChatbotTurnResponse):
    """`GET /turns/{id}` (Slice D, AC-970): the row plus the normalised trace detail
    `app.services.chatbot.trace_detail.compose_trace_detail` builds from `trace`.

    `trace_detail` stays a loose dict for the same reason `trace` and `response`
    above do: its shape is read off whatever the engine happened to write, which
    grows as the `data` and `dialogue` lanes add `TurnTrace.add` kinds this build
    has not seen yet. A strict model would DROP a kind the day it is added - the
    exact failure this file's own docstring already names.
    """

    trace_detail: dict[str, Any]


class ChatbotTurnListResponse(BaseModel):
    items: list[ChatbotTurnResponse]
    # Opaque. Absent (null) when there is no further page.
    next_cursor: str | None = None
    # Whether Retry can work in this environment at all. Rides the list rather than a
    # route of its own: it is one boolean the screen needs at the same moment it needs the
    # turns, and a second endpoint, hook, query and prop hop to deliver it is machinery a
    # single field does not need.
    retry_available: bool = False
    retry_unavailable_reason: str | None = None


class FailedContactRow(BaseModel):
    """One contact with at least one failed turn in the range (AC-255).

    An aggregate rather than a page of turns: the list filter needs "which contacts are
    worth opening", and answering that by pulling every turn and grouping in the frontend
    would move thousands of rows to answer a question about tens.
    """

    contact_respond_id: str
    last_failed_stage: str | None = None
    last_failed_at: datetime | None = None
    count: int


class FailedContactListResponse(BaseModel):
    items: list[FailedContactRow]


class RetryTurnResponse(BaseModel):
    turn_id: str
    # The attempt the RE-INJECTED turn will carry when it arrives as its own row. The row
    # being retried keeps its own attempt: it is a record of what happened.
    attempt: int


# --------------------------------------------------------------------------- #
# In-app console (Slice D final, chatbot growth r1): a dry-run turn from the
# `system-management/chatbot-console` page, run in process by
# `app/services/chatbot/console_service.py`.
# --------------------------------------------------------------------------- #


class ConsoleMediaInput(BaseModel):
    """An attached image or voice note, sent as base64 on the console turn body.

    `content_base64` rather than a multipart upload: the console body is already one small
    JSON POST, and a base64 image/short voice note stays well under the existing
    `MAX_TURN_BODY_BYTES`-style budgets other chatbot endpoints already police.
    """

    kind: str  # "image" | "audio"
    filename: str
    mime: str
    content_base64: str


class ConsoleReplyTo(BaseModel):
    """The bot bubble the console message quotes (QUOTED-REPLY AC-QR-18)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=128)
    text: str = Field(max_length=4096)


class ConsoleTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_respond_id: str
    text: str = ""
    # Membership matters, not just presence: `null` means "use this contact's stored
    # session", `{}` means "this contact remembers nothing" (a console Reset). Pydantic
    # can't distinguish "omitted" from "sent as null" once the field has a default, so the
    # route always receives one of the two, never a third "missing" state.
    session_vars: dict[str, Any] | None = None
    prompt_version_id: str | None = None
    run_id: str
    media: ConsoleMediaInput | None = None
    reply_to: ConsoleReplyTo | None = None


class ConsoleTraceSummary(BaseModel):
    tool: str | None = None
    args_short: dict[str, Any] | None = None
    crossdomain_rungs: list[str] = Field(default_factory=list)
    reveals_dropped: list[str] = Field(default_factory=list)
    # An escalation turn's draw in one line (team, brand, source, assignee); None on
    # every other turn.
    routing_line: str | None = None


class ConsoleTurnResponse(BaseModel):
    turn_id: str | None = None
    branch_kind: str | None = None
    reply_text: str = ""
    quick_replies: list[str] = Field(default_factory=list)
    send_messages: list[str] = Field(default_factory=list)
    # The patched state the NEXT turn should send back as `session_vars`. Null when this
    # turn produced no session patch (a failed lane never writes one) - the caller keeps
    # whatever it already had rather than treating null as "forget everything".
    session_vars: dict[str, Any] | None = None
    trace_summary: ConsoleTraceSummary
    # Media (commit 2): all null on a plain text turn. `media_status` is one of
    # "pending" | "done" | "failed" only when the request carried a `media` attachment.
    media_status: str | None = None
    media_id: str | None = None
    # What was read/heard, for the muted "Read from image: ..." / "Heard: ..." line.
    media_text: str | None = None
    media_error: str | None = None
    # Item 6 (8 Sep 2026): the parser prompt version this turn actually ran (the
    # `understood` stage's own fact, the same value the trace screen shows), so each bot
    # bubble can wear it. None when the turn never reached the parser.
    prompt_version: int | None = None
    # The turn's own raw `TurnResult.actions` (loosely typed on purpose, like
    # `ChatbotTurnResponse.response` above - it is the engine's shape, not this
    # endpoint's own). Carried through so the console can render a `send_attachments`
    # action's own file list under the reply it belongs to, the same source
    # `ChatbotTurn.response.actions` already gives the Chat History screen.
    actions: list[dict[str, Any]] | None = None


class ConsolePromptVersion(BaseModel):
    id: str
    version: int
    label: str | None = None
    chars: int
    # Item 6: which lineage the body is - "full" (the live-derived body), "compact" (the
    # S1b slim rewrite) or "other". Read off the opening of the template, see
    # `console_service.prompt_base`. The console defaults to the newest "full".
    base: str = "other"


class ConsoleMediaStatusResponse(BaseModel):
    status: str  # "pending" | "done" | "failed"
    text: str | None = None
    error: str | None = None
