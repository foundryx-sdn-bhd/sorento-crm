"""External ideation brain-path schemas (ideate turn endpoint, §5.1/§5.2)."""
from typing import Any

from pydantic import BaseModel, Field


class IdeationTurnRequest(BaseModel):
    """Body n8n posts for an `ideate`-classified WhatsApp turn."""

    respond_io_id: str = Field(..., description="Respond.io contact id (not internal UUID).")
    message_text: str = Field(..., description="The user's latest raw turn.")
    submitter_name: str | None = Field(
        None,
        description=(
            "Optional sender display name from the Respond.io profile (n8n fallback, "
            "WS-A). Used only when the CRM's respond_contacts row has no name."
        ),
    )
    media_selection: str | None = Field(
        None,
        description=(
            "Multi-modal capture (DC-7): the parser-extracted reference-positions "
            "answering an outstanding media menu - e.g. '1,3', 'all', 'none'. Present "
            "ONLY when session_vars.ideation.pending_media is set and the parser found "
            "a position reference; otherwise absent (the turn is a normal ideate turn)."
        ),
    )
    is_new_idea: bool | None = Field(
        None,
        description=(
            "Multi-modal capture (DC-10): the semantic 'this is a new/different idea' "
            "signal, extracted by the parser with the open-draft topic as context. When "
            "true and a draft is open, sorento starts a fresh draft and discards the old."
        ),
    )
    session_vars: dict[str, Any] | None = Field(
        None,
        description=(
            "Caller-supplied session state. The ideation pointer is read from "
            "session_vars.ideation (or session_vars.variables.ideation). n8n owns/writes "
            "session_vars, so the endpoint trusts this over its own (possibly stale) DB "
            "copy - this is what makes the draft accumulate across turns."
        ),
    )
    ask_reply: bool = Field(
        False,
        description=(
            "True when this message answers the bot's ask-back for an idea (the previous "
            "turn's status was ask_idea). A reply with still no idea in it then ends the ask "
            "with a clear message (status ask_idea_gave_up) instead of asking again."
        ),
    )
    is_test: bool = Field(
        False,
        description=(
            "A test turn (#1179): the chatbot's dry run sets this. The idea is still "
            "created in the shared service, flagged is_test so the board hides it by "
            "default, and the endpoint does NOT persist session_vars for the contact - "
            "the caller carries the returned pointer instead."
        ),
    )


class IdeationOfferedMedia(BaseModel):
    """One entry of the media menu this turn offered (#1277), numbered as the menu
    numbers it."""

    position: int
    kind: str
    url: str
    filename: str | None = None


class IdeationTurnResponse(BaseModel):
    """What n8n relays: the tool's reply, an optional deep link, the full
    updated session_vars blob, and the media the reply's menu offered."""

    status: str
    reply_text: str
    link: str | None = None
    language: str | None = Field(
        None, description="The language the reply was written in (en, ms or zh)."
    )
    session_vars: dict[str, Any] = Field(default_factory=dict)
    offered_media: list[IdeationOfferedMedia] = Field(
        default_factory=list,
        description=(
            "The recent files the reply's media menu lists, in menu order (#1277). "
            "The chatbot's ideate lane sends the images among them, captioned with "
            "their number. Empty on any turn that offers no menu."
        ),
    )
