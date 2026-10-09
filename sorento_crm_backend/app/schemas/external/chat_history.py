"""External chat history schemas."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field, field_validator


def _id_to_str(value: Any) -> Any:
    """Accept an id that arrived as a JSON number and store its string form.

    Respond's ``messageId`` IS an epoch-microsecond integer, and one n8n consume
    variant forwards it unquoted while another quotes it, so the same id reaches
    this route as ``1791283416935171`` and as ``"1791283416935171"``. Pydantic v2
    refuses int -> str on its own (``string_type``), which is what failed every
    quote-reply save in prod (CHAT-HISTORY-SCHEMA, 6 Oct 2026). ``None`` stays
    ``None``; anything else is handed on for the normal str check to judge.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    return value


# A Respond message id / n8n execution id: str on the row, int or str on the wire.
RespondId = Annotated[Optional[str], BeforeValidator(_id_to_str)]


class ChatHistoryMessageIngestRequest(BaseModel):
    channel: str = "whatsapp"
    contact_id: str
    phone_number: str
    # Defaults to "" rather than Optional: the column is NOT NULL, and a non-text
    # incoming item (photo / voice / document) is built without a `message` key
    # (undefined is dropped by JSON.stringify). The row must still land - the SLA
    # pairing, the inbound push and the quote-reply state lookup all key on it -
    # and "" is the honest value: no text was sent, none is invented.
    message: str = ""
    sent_at: int = Field(..., description="Epoch milliseconds")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    type: str
    message_id: RespondId = Field(
        None, max_length=64, description="Respond.io message id of this message"
    )
    result: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Numbered-options result set sent in this message "
        "(idx/uuid/label/...), referencable later via conversation-variables.",
    )
    reply_to_message_id: RespondId = Field(
        None,
        max_length=64,
        description="Incoming quote-reply only: respond.io message id of the "
        "replied-to message. Matches the `message_id` of the earlier outgoing "
        "row. Null for normal (non-reply) messages.",
    )
    reply_to_message: Optional[str] = Field(
        None, description="Incoming quote-reply only: text of the replied-to message."
    )
    turn_id: RespondId = Field(
        None,
        max_length=64,
        description="n8n $execution.id, stamped on BOTH the incoming and the outgoing "
        "save of one turn. Pairs a reply to the message that triggered it exactly, so "
        "round-trip latency survives message bursts and reordering. Omit on proactive "
        "sends (campaigns, SLA notices) - those are excluded from the latency SLA "
        "rather than paired by proximity.",
    )
    chatbot_turn_id: Optional[str] = Field(
        None,
        max_length=36,
        description="Outgoing rows only: the engine turn uuid (chatbot.turns.id) this reply "
        "came from, when n8n passes it. Absent today; the ingest then links the row itself.",
    )
    state_trace: Optional[dict[str, Any]] = Field(
        None,
        description="Per-turn conversation state transition (v1). Incoming rows only; "
        "NULL on outgoing. Opaque by design: {v, before, parser_raw, parser_applied, "
        "after}. `after: null` means the turn wrote no state (no-access refusal, "
        "voice-not-allowed, LLM fallback) and is a real signal - never coerce it to {}. "
        "Stored as received; the producer owns the inner shape.",
    )
    # Lane CHAT-LOCAL-FIRST (R5): what the local thread could not render before. All
    # optional; n8n sends them when the Respond message carries them, and the delta /
    # reconcile read fills them on rows that arrived without.
    media_url: Optional[str] = Field(
        None, description="Attachment URL of a media message (Respond `attachment.url`)."
    )
    media_type: Optional[str] = Field(
        None, max_length=32, description="image | video | audio | file | sticker ..."
    )
    media_file_name: Optional[str] = Field(None, max_length=512)
    sender_source: Optional[str] = Field(
        None,
        max_length=32,
        description="Respond `sender.source`: contact | user | bot | api | workflow ...",
    )
    sender_user_id: Optional[str] = Field(
        None, max_length=64, description="Respond user id behind a `user` send."
    )


    @field_validator("chatbot_turn_id")
    @classmethod
    def _chatbot_turn_id_is_uuid(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("chatbot_turn_id must be a UUID") from exc


class ChatHistoryMessageIngestResponse(BaseModel):
    id: int
    # "created" for a new row, "duplicate" when this Respond message_id was
    # already mirrored for this contact and the existing row was returned
    # instead (AC-J5 - the direct webhook lane and Respond's outgoing-message
    # trigger both mirror the same message).
    status: str = "created"


class ChatHistoryMessagesRequest(BaseModel):
    channel: str = "whatsapp"
    contact_id: str
    limit: int = 50
    order: Literal["asc", "desc"] = "asc"


class ChatHistoryMessageItem(BaseModel):
    id: int
    channel: str
    contact_id: str
    phone_number: str
    message: str
    sent_at: datetime
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    type: str
    message_id: Optional[str] = None
    result: Optional[list[dict[str, Any]]] = None
    reply_to_message_id: Optional[str] = None
    reply_to_message: Optional[str] = None


class ChatHistoryMessagesResponse(BaseModel):
    messages: list[ChatHistoryMessageItem]

