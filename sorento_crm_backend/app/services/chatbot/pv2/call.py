"""The parser v2 call (CONTRACT-PV2-13oct.md sections 2 and 6).

Two halves, like `head/parser.py`: `resolve_v2_config` reads the database (the pinned
prompt version, its index keys, the model and key) and the caller closes the session;
`parse_v2` makes the one structured-output call and touches no database.

Messages, in order (the cache prefix is the whole system message, nothing dynamic in it):
1. system = the pinned version's text (head + INDEX);
2. the last `PV2_HISTORY_EXCHANGES` exchanges of this session, oldest first;
3. user = the MEMORY block + `<message>{text}</message>`.

Record mode: with env `PV2_RECORD_DIR` set, every successful parse appends
`{index_sha, version, memory, text, parser_out}` to `<dir>/pv2-record-<YYYYMMDD>.jsonl`,
which is how the crew records dev-split fixtures without a second model call.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.chatbot.pv2 import index as pv2_index
from app.services.chatbot.pv2.schema import (
    PV2_JSON_SCHEMA,
    PV2_SCHEMA_NAME,
    DroppedKey,
    IndexKeys,
    ParserV2Output,
    SchemaError,
    validate,
)

logger = logging.getLogger(__name__)

PV2_HISTORY_EXCHANGES = 3
PV2_MAX_TOKENS = 1200
RECORD_ENV = "PV2_RECORD_DIR"


class ParserV2Error(RuntimeError):
    """The v2 parse could not be completed. `usage` is what the provider billed, when it
    got far enough to bill; `rate_limited` mirrors `head.parser.ParserError`."""

    def __init__(self, message: str, *, rate_limited: bool = False, usage: dict | None = None):
        super().__init__(message)
        self.rate_limited = rate_limited
        self.usage = usage or {}


@dataclass
class V2Config:
    system_prompt: str
    prompt_version: int | None
    version_id: str | None
    index_sha: str
    keys: IndexKeys
    provider: str
    model: str
    api_key: str

    @property
    def cache_key(self) -> str:
        version = self.prompt_version if self.prompt_version is not None else "fallback"
        return f"{pv2_index.PROMPT_KEY_V2}:v{version}:{self.index_sha[:8]}"


@dataclass
class V2Result:
    output: ParserV2Output
    dropped: list[DroppedKey]
    usage: dict[str, Any]
    raw: dict
    messages: list[dict] = field(default_factory=list)


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _pinned_text(db: Session, override_version_id: str | None, label: str):
    """(template, version, version id) of the pinned v2 prompt, or None when none is
    published. Never the production label (none exists for v2 tonight)."""
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

    q = db.query(AIPromptVersion).filter(AIPromptVersion.name == pv2_index.PROMPT_KEY_V2)
    if override_version_id:
        row = q.filter(AIPromptVersion.id == override_version_id).first()
        if row is None:
            raise ParserV2Error(f"parser v2 version {override_version_id} not found")
    else:
        row = (
            q.join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
            .filter(AIPromptLabel.name == pv2_index.PROMPT_KEY_V2, AIPromptLabel.label == label)
            .first()
        )
    if row is None:
        return None
    return row.template, int(row.version), str(row.id)


def resolve_v2_config(
    db: Session, *, override_version_id: str | None = None, label: str = pv2_index.PUBLISH_LABEL
) -> V2Config:
    """Read the pinned prompt, its keys, the per-key model and the API key. Session-bound:
    call this, close the session, then `parse_v2`.

    With no published v2 version (a fresh dev copy) the system text is the file head plus
    a LIVE index render, `prompt_version` None: usable for a hand test, marked in every
    trace by the `vfallback` cache key and the null version."""
    from app.services.ai_assistant_service import AIAssistantConfigService
    from app.services.ai_prompt_registry import PROMPT_KEYS, agent_model
    from app.services.llm_provider import resolve_api_key

    pinned = _pinned_text(db, override_version_id, label)
    if pinned is not None:
        template, version, version_id = pinned
        body = pv2_index.index_of(template)
    else:
        head = PROMPT_KEYS[pv2_index.PROMPT_KEY_V2].fallback()
        body, _ = pv2_index.render_index(db)
        template, version, version_id = head.rstrip("\n") + "\n\n" + body, None, None

    provider_override, model_override = agent_model(db, pv2_index.PROMPT_KEY_V2, label)
    config = AIAssistantConfigService(db).get()
    if config is None:
        raise ParserV2Error("AI assistant configuration is not set")
    provider = provider_override or config.provider
    model = model_override or config.model
    api_key = resolve_api_key(config, provider)
    if not api_key:
        raise ParserV2Error(f"no API key configured for provider {provider!r}")
    return V2Config(
        system_prompt=template,
        prompt_version=version,
        version_id=version_id,
        index_sha=_sha(body),
        keys=pv2_index.parse_index_keys(template),
        provider=provider,
        model=model,
        api_key=api_key,
    )


def build_messages(
    config: V2Config, *, history: list[tuple[str, str]], memory_block: str, text: str
) -> list[dict]:
    messages = [{"role": "system", "content": config.system_prompt}]
    for user_text, bot_text in list(history)[-PV2_HISTORY_EXCHANGES:]:
        messages.append({"role": "user", "content": user_text or ""})
        messages.append({"role": "assistant", "content": bot_text or ""})
    messages.append({"role": "user", "content": f"{memory_block.rstrip()}\n<message>{text}</message>"})
    return messages


def _record(config: V2Config, memory_block: str, text: str, raw: dict) -> None:
    folder = os.environ.get(RECORD_ENV)
    if not folder:
        return
    try:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        row = {
            "index_sha": config.index_sha,
            "version": config.prompt_version,
            "version_id": config.version_id,
            "memory": memory_block,
            "text": text,
            "parser_out": raw,
        }
        with (path / f"pv2-record-{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:  # recording must never fail a turn
        logger.warning("pv2 record: could not append to %s", folder, exc_info=True)


def parse_v2(
    config: V2Config, *, history: list[tuple[str, str]], memory_block: str, text: str
) -> V2Result:
    """One structured-output call. Raises `ParserV2Error`; never returns a default."""
    from app.services.chatbot import llm_call

    messages = build_messages(config, history=history, memory_block=memory_block, text=text)
    try:
        result = llm_call.chat(
            config.provider,
            config.api_key,
            config.model,
            messages,
            temperature=0.0,
            max_tokens=PV2_MAX_TOKENS,
            json_schema=PV2_JSON_SCHEMA,
            json_schema_name=PV2_SCHEMA_NAME,
            prompt_cache_key=config.cache_key,
        )
    except llm_call.RateLimited as exc:
        raise ParserV2Error(f"parser v2 provider call failed: {exc}", rate_limited=True) from exc
    except Exception as exc:  # noqa: BLE001 - provider/transport failure is a failed stage
        raise ParserV2Error(f"parser v2 provider call failed: {exc}") from exc

    usage = {
        "provider": config.provider,
        "model": config.model,
        "prompt_tokens": int(getattr(result, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(result, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(result, "total_tokens", 0) or 0),
        "cached_tokens": int(getattr(result, "cached_tokens", 0) or 0),
    }
    content = (getattr(result, "content", None) or "").strip()
    try:
        if not content:
            raise ParserV2Error("parser v2 returned empty content")
        try:
            raw = json.loads(content)
        except ValueError as exc:
            raise ParserV2Error(f"parser v2 returned non-JSON content: {exc}") from exc
        if not isinstance(raw, dict):
            raise ParserV2Error("parser v2 returned a non-object")
        try:
            output, dropped = validate(raw, config.keys)
        except SchemaError as exc:
            raise ParserV2Error(f"parser v2 emission is not a ParserV2Output: {exc}") from exc
    except ParserV2Error as exc:
        exc.usage = usage
        raise
    _record(config, memory_block, text, raw)
    return V2Result(output=output, dropped=dropped, usage=usage, raw=raw, messages=messages)
