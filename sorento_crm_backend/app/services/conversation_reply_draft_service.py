"""AI assist for the ticket composer: draft a reply from the visible thread.

UAC AC-L5, slice S4.4. Deliberately NOT a new AI surface - it reuses the CRM
assistant's existing plumbing end to end: the prompt comes from the prompt
registry (`conversation_reply_draft`, editable and versioned in the FE like
every other key), the provider/model come from the same per-agent resolution
`spec_understanding` uses, and the spend lands in the same
`ai_assistant_usage_logs` table.

Shaped as ONE bounded call, not an agent loop: no tools, no retrieval, no
compute. The output goes into a STAFF MEMBER's input box for them to read and
edit, which is exactly why it can stay this small - the human is the validator,
and nothing here can reach a customer on its own.

Where it deliberately differs from `product_spec_understanding` (the closest
sibling): that one DEGRADES to a deterministic reading when the model is
unavailable, because a search that silently got worse is better than no search.
There is no deterministic fallback for "write a sentence to a customer", so this
one RAISES instead: a button that quietly does nothing is worse than one that
says the assistant is not configured.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.ai_assistant import AIAssistantUsageLog
from app.services.ai_prompt_registry import agent_model, get_prompt
from app.services.error_handler import AppException
from app.services.llm_provider import get_provider, resolve_api_key, resolve_model

logger = logging.getLogger(__name__)

# The prompt key in the AI prompt registry. Editable + versioned in the FE at
# system-management/ai-assistant/prompts; the hardcoded copy is its fallback.
AGENT_NAME = "conversation_reply_draft"

# How many of the most recent messages are handed to the model by default -
# roughly what the assignee can see in the drawer without scrolling back.
DEFAULT_TAIL = 20
MAX_TAIL = 50

_MAX_OUTPUT_TOKENS = 500
# Not 0: a reply that reads like a person wrote it needs a little room. Not high
# either - this is a draft a human signs off, not creative writing.
_TEMPERATURE = 0.3

# One message is at most this long in the transcript. A pasted invoice dump
# should not eat the whole context and push the actual question out of it.
_MAX_LINE_CHARS = 600


@dataclass
class ReplyDraft:
    draft: str
    model: Optional[str]
    provider: Optional[str]
    # How many thread messages the prompt actually carried. Surfaced so the FE
    # (and a reader of a bug report) can tell "the model had nothing to go on"
    # apart from "the model ignored the thread".
    grounded_on: int
    elapsed_ms: int


def _resolve_provider(db: Session):
    """The provider and model THIS agent runs on. ``(None, ...)`` when unset.

    Per-agent with a fall back to the global assistant config, exactly as
    `product_spec_understanding._resolve_provider` does: drafting a customer
    reply is a different job from reading a spec sentence, so it gets its own
    row rather than sharing one setting with everything.

    The agent's provider is operator-settable, so it is often NOT the one the
    assistant row is configured for; the key and model therefore come from the
    shared `resolve_api_key` / `resolve_model` pair, which only hand over the
    generic columns when they belong to the provider being asked for. Reading
    them unconditionally posted the OpenAI key and a Claude model id to Google
    whenever this agent was pointed at Gemini.
    """
    from app.models.ai_assistant import AIAssistantConfig

    cfg = (
        db.query(AIAssistantConfig).order_by(AIAssistantConfig.created_at.asc()).first()
    )
    agent_provider, agent_model_name = agent_model(db, AGENT_NAME)
    provider_name = agent_provider or (cfg.provider if cfg else "openai") or "openai"
    model_name = resolve_model(cfg, provider_name, agent_model_name)
    api_key = resolve_api_key(cfg, provider_name)
    if not api_key:
        return None, provider_name, model_name
    try:
        return get_provider(provider_name, api_key, model=model_name), provider_name, model_name
    except ValueError:
        logger.warning("reply draft: unknown provider %r", provider_name)
        return None, provider_name, model_name


def _line_for(item: dict) -> Optional[str]:
    """One transcript line, or None when the message carries nothing readable.

    Attachments become their typed placeholder ("[image]") rather than being
    dropped: "the customer sent a photo" is context, and a silently missing turn
    would make the model answer the wrong message.
    """
    message = item.get("message") if isinstance(item.get("message"), dict) else {}
    text = str(message.get("text") or "").strip()
    if not text:
        # A quick_reply keeps its prose under `title`; without this the model
        # reads "[quick_reply]" where the bot asked a question.
        text = str(message.get("title") or "").strip()
    if not text:
        kind = str(message.get("type") or "").strip()
        text = f"[{kind}]" if kind and kind != "text" else ""
    if not text:
        return None
    if len(text) > _MAX_LINE_CHARS:
        text = text[:_MAX_LINE_CHARS].rstrip() + "..."
    who = "Customer" if str(item.get("traffic") or "") == "incoming" else "Us"
    return f"{who}: {text}"


def build_prompt_messages(
    *,
    system_prompt: str,
    contact_name: str,
    enquiry: str,
    transcript: list[str],
    instruction: Optional[str],
) -> list[dict]:
    """The two-message payload handed to the provider.

    Kept a pure function so a test can read exactly what the model was told
    without a database or a network anywhere near it.
    """
    parts = [f"Customer: {contact_name.strip() or 'Unknown contact'}"]
    if enquiry.strip():
        parts.append(f"Their enquiry: {enquiry.strip()}")
    parts.append(
        "Recent conversation (oldest first):\n" + "\n".join(transcript)
        if transcript
        else "Recent conversation: (no messages in this thread yet)"
    )
    if (instruction or "").strip():
        parts.append(f"The agent wants this draft to: {instruction.strip()}")
    parts.append("Write the reply now.")
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _contact_name_of(tracking: Any) -> str:
    contact = getattr(tracking, "contact", None)
    if contact is None:
        return ""
    name = (getattr(contact, "name", None) or "").strip()
    if name:
        return name
    parts = [
        (getattr(contact, "first_name", None) or "").strip(),
        (getattr(contact, "last_name", None) or "").strip(),
    ]
    return " ".join(p for p in parts if p).strip()


def draft_reply(
    db: Session,
    tracking: Any,
    *,
    instruction: Optional[str] = None,
    tail: int = DEFAULT_TAIL,
    user_id: Optional[str] = None,
) -> ReplyDraft:
    """Draft the next reply for this ticket. Raises when it cannot.

    The thread is read SERVER-SIDE (the same paginated read the drawer uses),
    not posted by the browser: one less contract surface, and a client cannot
    talk the model into grounding on a conversation it is not looking at.
    """
    from app.services.sla_service import ConversationSLATrackingService

    tail = max(1, min(int(tail or DEFAULT_TAIL), MAX_TAIL))

    # Same viewer scope as the drawer's own read. The route has already checked
    # it, so this is belt and braces - but the read is scoped BY the caller, and
    # a grounding read the person asking for the draft could not repeat for
    # themselves would be exactly the leak the scope exists to stop.
    page = ConversationSLATrackingService(db).fetch_conversation_thread_page(
        str(tracking.id), viewer_user_id=str(user_id or ""), limit=tail
    )
    items = list(page.get("items") or [])[-tail:]
    transcript = [line for line in (_line_for(i) for i in items) if line]

    provider, provider_name, model_name = _resolve_provider(db)
    if provider is None:
        raise AppException(
            status_code=503,
            message=(
                "The AI assistant is not configured, so a draft cannot be written. "
                "Ask an admin to set it up in System Management."
            ),
            code="AI_NOT_CONFIGURED",
        )

    messages = build_prompt_messages(
        system_prompt=get_prompt(db, AGENT_NAME).text,
        contact_name=_contact_name_of(tracking),
        enquiry=str(getattr(tracking, "source_message_text", "") or ""),
        transcript=transcript,
        instruction=instruction,
    )

    started = time.monotonic()
    try:
        result = provider.chat(
            messages, temperature=_TEMPERATURE, max_tokens=_MAX_OUTPUT_TOKENS
        )
    except Exception as exc:  # noqa: BLE001 - reported, never a 500
        logger.warning("reply draft failed for tracking %s: %s", tracking.id, exc)
        raise AppException(
            status_code=503,
            message="The assistant could not write a draft just now. Try again.",
            code="AI_DRAFT_FAILED",
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    draft = (getattr(result, "content", "") or "").strip()
    if not draft:
        raise AppException(
            status_code=503,
            message="The assistant returned an empty draft. Try again.",
            code="AI_DRAFT_EMPTY",
        )

    # Bookkeeping is best-effort: the draft is already written and refusing to
    # hand it over because a usage row would not insert helps nobody.
    try:
        db.add(
            AIAssistantUsageLog(
                user_id=user_id,
                feature="ticket_reply_draft",
                provider=provider_name,
                model=model_name,
                prompt_tokens=int(getattr(result, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(result, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(result, "total_tokens", 0) or 0),
                cached_tokens=int(getattr(result, "cached_tokens", 0) or 0),
                tool_calls_count=0,
                response_time_ms=elapsed_ms,
                was_answered=True,
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("reply draft: usage log failed: %s", exc)
        db.rollback()

    return ReplyDraft(
        draft=draft,
        model=model_name or None,
        provider=provider_name or None,
        grounded_on=len(transcript),
        elapsed_ms=elapsed_ms,
    )
