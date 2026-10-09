"""The focused chat report extractor (CA-3, revision 10 Oct 2026).

`documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md`, "Revision 10 Oct
2026: focused extractor". The parser only ROUTES a chat report; this one small, schema-forced
call reads the message into `{group_by, measure, subject, top_n, date_from, date_to}`.

Same plumbing as `ideation_extractor`: `get_provider` + `resolve_api_key`, the prompt from the
registry (`chat_report_extractor`, code fallback), the per-key model override. Any failure (no
key, provider error, timeout, output outside the contract) returns None and is logged; it never
raises, so the lane can say one clear line instead of hanging.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services import ai_prompt_registry
from app.services.chatbot import llm_call
from app.services.ai_assistant_service import AIAssistantConfigService
from app.services.llm_provider import get_provider, resolve_api_key, resolve_model

logger = logging.getLogger(__name__)

PROMPT_KEY = "chat_report_extractor"
SCHEMA_NAME = "chat_report_extraction"
GROUP_BY = ("topic", "subject", "product", "customer", "outcome", "failure_reason", "week", "month", "day", "language")
MEASURES = ("asks", "failed", "failure_rate", "contacts")
KEYS = ("group_by", "measure", "subject", "top_n", "date_from", "date_to")
MAX_TOP_N = 1000


def _domains(db: Session) -> list[tuple[str, str, list[str]]]:
    """(code, label, switch words) of every routing domain a subject can name."""
    from app.models.chatbot_policy import ChatbotDomain

    rows = (
        db.query(ChatbotDomain.name, ChatbotDomain.label, ChatbotDomain.switch_words)
        .order_by(ChatbotDomain.sort_order, ChatbotDomain.name)
        .all()
    )
    return [(r[0], r[1] or "", [w for w in (r[2] or []) if w]) for r in rows if r[0] and r[0] != "chat_analytics"]


def _describe(domain: tuple[str, str, list[str]]) -> str:
    """`code (label; words: w1, w2)`, the words part left out when there are none."""
    code, label, words = domain
    parts = [label] if label else []
    if words:
        parts.append("words: " + ", ".join(words))
    return f"{code} ({'; '.join(parts)})" if parts else code


def _schema(domains: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(KEYS),
        "properties": {
            "group_by": {"type": ["string", "null"], "enum": [*GROUP_BY, None]},
            "measure": {"type": ["string", "null"], "enum": [*MEASURES, None]},
            "subject": {"type": ["string", "null"], "enum": [*domains, None]},
            "top_n": {"type": ["integer", "null"]},
            "date_from": {"type": ["string", "null"]},
            "date_to": {"type": ["string", "null"]},
        },
    }


def _valid_date(value: Any) -> bool:
    if value is None:
        return True
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _checked(data: Any, domains: list[str]) -> dict[str, Any] | None:
    """The six keys, or None when the output is outside the contract."""
    if not isinstance(data, dict) or set(data) != set(KEYS):
        return None
    if data["group_by"] is not None and data["group_by"] not in GROUP_BY:
        return None
    if data["measure"] is not None and data["measure"] not in MEASURES:
        return None
    if data["subject"] is not None and data["subject"] not in domains:
        return None
    top_n = data["top_n"]
    if top_n is not None and (isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= MAX_TOP_N):
        return None
    if not (_valid_date(data["date_from"]) and _valid_date(data["date_to"])):
        return None
    return {k: data[k] for k in KEYS}


def extract_chat_report(db: Session, message_text: str, *, today: date) -> dict[str, Any] | None:
    """Read one staff message into a chat report request, or None. Never raises."""
    raw = (message_text or "").strip()
    if not raw:
        return None
    try:
        config = AIAssistantConfigService(db).get()
        agent_provider, agent_model_name = ai_prompt_registry.agent_model(db, PROMPT_KEY)
        provider_name = agent_provider or config.provider or "openai"
        model = resolve_model(config, provider_name, agent_model_name)
        api_key = resolve_api_key(config, provider_name)
        if not api_key:
            logger.warning("chat_report_extractor: no api key; no reading")
            return None
        domains = _domains(db)
        codes = [d[0] for d in domains]
        system, _version = ai_prompt_registry.render(db, PROMPT_KEY)
        system = system.replace("{today}", today.isoformat()).replace("{domains}", "; ".join(_describe(d) for d in domains))
        provider = get_provider(provider_name, api_key, model=model)
        result = provider.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": raw}],
            temperature=0.0,
            model=model,
            max_tokens=256,
            timeout=llm_call.ATTEMPT_TIMEOUT_SECONDS,
            json_schema=_schema(codes),
            json_schema_name=SCHEMA_NAME,
        )
        out = _checked(json.loads((result.content or "").strip() or "{}"), codes)
    except Exception:  # noqa: BLE001 - any failure is "no reading", never a broken turn
        logger.warning("chat_report_extractor: call failed; no reading", exc_info=True)
        return None
    if out is None:
        logger.warning("chat_report_extractor: output outside the contract; no reading")
    return out
