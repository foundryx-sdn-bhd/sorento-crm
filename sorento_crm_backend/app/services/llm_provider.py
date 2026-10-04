"""LLM provider abstraction (OpenAI + Anthropic + Google Gemini).

Goal: keep the agent loop and reformulator/embedder pieces in
``ai_assistant_service`` provider-agnostic. Each provider exposes the same
``chat`` / ``embed`` / ``test_connection`` surface and returns a normalized
``ChatResult`` whose ``raw`` field carries enough provider-native data to
iterate tool calls in the agent loop.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """Normalized chat completion result.

    ``tool_calls`` is a normalized OpenAI-style list of pending calls the
    caller needs to execute. ``raw`` retains the provider-native response
    so the agent loop can append the assistant message back in the
    provider's expected schema.
    """

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tool_calls: list[dict] = field(default_factory=list)  # [{id, name, arguments}]
    raw: Any = None  # provider-native response


@dataclass
class ImagePart:
    """Single image attached to the last user message in a multimodal call.

    ``mime`` must be a content type the active provider accepts
    (``image/png``, ``image/jpeg``, ``image/webp``; Gemini additionally takes
    ``image/heic`` and ``image/heif``). ``data_b64`` is raw
    base64 with no ``data:`` prefix - each provider's ``chat`` adapter
    wraps it in the native shape.
    """

    mime: str
    data_b64: str


@dataclass
class ProviderModel:
    """One model the provider says this key may call.

    `value` is the id sent on the wire; `label` is what an operator reads. The
    pair exists because Anthropic ships a display name and the other two do not,
    so the label is derived where it is missing rather than at three call sites.
    """

    value: str
    label: str


# Listing is not an operator-initiated call the way `chat` and `test_connection`
# are: it runs on every render of three settings screens. An SDK's default (up to
# ten minutes across retries) would hold a request, and one threadpool worker, for
# the whole of it on a provider that accepts the connection and never answers - so
# the "degrades to the built-in list" promise would arrive minutes late. Ten
# seconds is generous for a call that returns a few hundred names.
LIST_MODELS_TIMEOUT_SECONDS = 10.0


# A provider's own list is authority on what EXISTS, never on what WORKS: Google
# lists `gemini-2.5-flash-lite` to a key that gets `404 ... no longer available
# to new users` the moment it calls generateContent on it (measured 2026-08-22,
# and the cause of every over-quota photo failing in the media lane). Anything
# picked from this list still has to be probed with a real call - that is what
# `probe_model` in `provider_model_catalog` is for.
# Last-resort model per provider, for a caller that has no configured model to
# use. Each is that provider's cheap-but-capable general model, and every caller
# that needs one reads it through `default_model_for` so registering a fourth
# provider is a single edit here rather than a hunt for two-branch conditionals
# ("gpt-4o if openai else claude") that silently hand the new provider another
# vendor's model id.
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
}


def default_model_for(provider_name: Optional[str]) -> str:
    """The fallback model for ``provider_name``.

    An unknown name still returns something so the caller can build its error
    with a concrete model in hand; the ``get_provider`` call that follows is
    what actually refuses it with a ``ValueError``.
    """
    key = (provider_name or "").strip().lower()
    return DEFAULT_MODELS.get(key, DEFAULT_MODELS["anthropic"])


def resolve_model(cfg: Any, provider_name: str, explicit: Optional[str] = None) -> str:
    """The model to run on ``provider_name``: ``explicit`` first, else the
    assistant row's model, but only when that row is configured for the same
    provider, else the provider's default.

    Model ids are vendor-specific. A lane pointed at Gemini with no model of its
    own must not inherit the assistant's ``gpt-4o`` and post it to Google, which
    404s in a way that reads like an outage rather than a configuration gap.
    Names are normalized the same way ``resolve_api_key`` normalizes them.
    """
    if explicit:
        return str(explicit)
    provider_key = (provider_name or "").strip().lower()
    cfg_provider = (
        (getattr(cfg, "provider", None) or "") if cfg is not None else ""
    ).strip().lower()
    cfg_model = (getattr(cfg, "model", None) if cfg is not None else None) or ""
    if cfg_model and cfg_provider == provider_key:
        return str(cfg_model)
    return default_model_for(provider_key)


def resolve_api_key(cfg: Any, provider_name: str) -> str:
    """The key to send to ``provider_name``, given the AI assistant config row.

    One resolver for every caller, because the same mistake was made twice: a
    per-agent provider is operator-settable, so the provider a call runs on is
    often NOT the one the assistant row is configured for, and the generic
    ``api_key_ciphertext`` column then holds somebody else's key. Posting an
    OpenAI key to Google produces a 400 that reads like a Gemini outage rather
    than "no key configured", so the generic column counts only when the
    assistant itself runs on the provider being asked for.

    Order per provider: the provider-specific column, then the generic column
    when it belongs to this provider, then the environment key. An empty string
    means no key is configured, which is the caller's cue to say so.

    Names are normalized exactly as ``get_provider`` and ``default_model_for``
    normalize them, so a stored ``"Gemini"`` cannot resolve an OpenAI key here
    and then be handed to a Gemini client.
    """
    from app.config import settings as app_settings

    provider_name = (provider_name or "").strip().lower()

    def column(name: str) -> str:
        return (getattr(cfg, name, None) if cfg is not None else "") or ""

    cfg_provider = (
        (getattr(cfg, "provider", None) or "") if cfg is not None else ""
    ).strip().lower()
    generic = column("api_key_ciphertext") if cfg_provider == provider_name else ""
    if provider_name == "anthropic":
        return (
            column("anthropic_api_key_ciphertext")
            or generic
            or app_settings.anthropic_api_key
            or ""
        )
    if provider_name == "gemini":
        return (
            column("gemini_api_key_ciphertext")
            or generic
            or app_settings.gemini_api_key
            or ""
        )
    return generic or app_settings.openai_api_key or ""


class LLMProvider(Protocol):
    name: str

    def chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        images: Optional[list[ImagePart]] = None,
        response_format: Optional[dict] = None,
        json_schema: Optional[dict] = None,
        json_schema_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ChatResult: ...

    def embed(self, text: str) -> list[float]: ...

    def test_connection(self) -> tuple[bool, str, int]: ...

    def list_models(self) -> list[ProviderModel]: ...


# ---------------------------------------------------------------------------
# Multimodal helpers
# ---------------------------------------------------------------------------


def _attach_images_openai(
    messages: list[dict], images: list[ImagePart]
) -> list[dict]:
    """Return a copy of ``messages`` with ``images`` appended to the final
    user message in OpenAI's vision format.

    If no user message exists, a new one is created. The original text is
    preserved as a ``{"type":"text"}`` block so callers can keep passing
    plain-string content.
    """
    if not images:
        return messages
    out = [dict(m) for m in messages]
    last_user_idx = -1
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx == -1:
        out.append({"role": "user", "content": []})
        last_user_idx = len(out) - 1

    msg = out[last_user_idx]
    existing = msg.get("content")
    blocks: list[dict] = []
    if isinstance(existing, str) and existing:
        blocks.append({"type": "text", "text": existing})
    elif isinstance(existing, list):
        blocks.extend(existing)
    for img in images:
        blocks.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img.mime};base64,{img.data_b64}",
                },
            }
        )
    msg["content"] = blocks
    out[last_user_idx] = msg
    return out


def _attach_images_anthropic(
    messages: list[dict], images: list[ImagePart]
) -> list[dict]:
    """Return a copy of ``messages`` with ``images`` appended to the final
    user message in Anthropic's content-block format."""
    if not images:
        return messages
    out = [dict(m) for m in messages]
    last_user_idx = -1
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx == -1:
        out.append({"role": "user", "content": []})
        last_user_idx = len(out) - 1

    msg = out[last_user_idx]
    existing = msg.get("content")
    blocks: list[dict] = []
    if isinstance(existing, str) and existing:
        blocks.append({"type": "text", "text": existing})
    elif isinstance(existing, list):
        blocks.extend(existing)
    for img in images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.mime,
                    "data": img.data_b64,
                },
            }
        )
    msg["content"] = blocks
    out[last_user_idx] = msg
    return out


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


# Newer OpenAI models reject parameters the older ones require: `max_tokens` became
# `max_completion_tokens`, and some refuse a non-default `temperature` outright. The
# model is chosen in the UI per agent, so a hardcoded list of which model wants which
# spelling would go stale the first time someone picks a new one. Instead: send the
# normal shape, read the parameter name out of the 400, adapt, retry. Bounded so a
# genuinely broken request cannot loop.
#
# The ceiling goes out as `max_completion_tokens`, which every OpenAI chat model accepts;
# sending `max_tokens` first cost every gpt-5.4-mini call a refused round trip
# (CHAT-SLOW-MISS). What a model does refuse is remembered per model for the life of the
# process, so the 400 is paid once rather than on every call.
_MAX_PARAM_RETRIES = 4
_CEILING_SPELLINGS = {"max_completion_tokens": "max_tokens", "max_tokens": "max_completion_tokens"}
_OPENAI_REJECTED_PARAMS: dict[str, set[str]] = {}


def _adapt(attempt: dict, param: str) -> None:
    """Drop `param`, or move the ceiling to its other spelling."""
    value = attempt.pop(param)
    other = _CEILING_SPELLINGS.get(param)
    if other is not None:
        attempt[other] = value


def _create_chat_completion(client, kwargs: dict) -> Any:
    """`client.chat.completions.create`, adapting to a model's parameter dialect."""
    attempt = dict(kwargs)
    if "max_tokens" in attempt:
        attempt["max_completion_tokens"] = attempt.pop("max_tokens")
    rejected = _OPENAI_REJECTED_PARAMS.setdefault(str(attempt.get("model")), set())
    for param in [p for p in attempt if p in rejected]:
        _adapt(attempt, param)
    for _ in range(_MAX_PARAM_RETRIES):
        try:
            return client.chat.completions.create(**attempt)
        except Exception as exc:  # noqa: BLE001 - inspected, then re-raised if not ours
            param = getattr(exc, "param", None) or _unsupported_param(str(exc))
            if not param or param not in attempt:
                raise
            # Only a refusal of the parameter itself is a fact about the model. A 400
            # that merely NAMES a parameter (context length on `messages`, a bad
            # schema on `response_format`) fails this call alone, as it always did.
            if _is_unsupported(exc, param):
                rejected.add(param)
            _adapt(attempt, param)
            logger.info("openai: model rejected %r, retrying without it", param)
    return client.chat.completions.create(**attempt)


_UNSUPPORTED_PARAM_RE = re.compile(r"[Uu]nsupported parameter: '([a-z_]+)'|'([a-z_]+)' is not supported")
_UNSUPPORTED_CODES = frozenset({"unsupported_parameter", "unsupported_value"})


def _is_unsupported(exc: Exception, param: str) -> bool:
    """True when the 400 says the model does not take `param` at all."""
    message = str(exc)
    return (
        getattr(exc, "code", None) in _UNSUPPORTED_CODES
        or _unsupported_param(message) == param
        or f"nsupported value: '{param}'" in message
    )


def _unsupported_param(message: str) -> str | None:
    match = _UNSUPPORTED_PARAM_RE.search(message)
    if not match:
        return None
    return match.group(1) or match.group(2)


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, default_model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key
        self.default_model = default_model

    # Lazy-import the SDK so importing this module is cheap and we don't fail
    # in environments without the SDK present (tests monkey-patch it anyway).
    def _client(self, **overrides: Any):
        """The SDK seam. `overrides` reach the constructor (timeouts for listing)."""
        from openai import OpenAI

        return OpenAI(api_key=self.api_key, **overrides)

    def chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        images: Optional[list[ImagePart]] = None,
        response_format: Optional[dict] = None,
        json_schema: Optional[dict] = None,
        json_schema_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ChatResult:
        # A caller-set timeout turns the SDK's own retries off: the caller owns the retry
        # loop and the budget (`chatbot/llm_call.py`), and a silent second attempt doubles it.
        client = self._client(timeout=timeout, max_retries=0) if timeout is not None else self._client()
        outbound_messages = (
            _attach_images_openai(messages, images) if images else messages
        )
        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": outbound_messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        # Structured-output: force a schema-valid JSON object as the response
        # (OpenAI native strict mode). Takes precedence over response_format.
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema_name or "structured_result",
                    "schema": json_schema,
                    "strict": True,
                },
            }
        elif response_format is not None:
            kwargs["response_format"] = response_format

        completion = _create_chat_completion(client, kwargs)

        # Defensive normalization - tests use simple stub objects.
        choice = completion.choices[0]
        msg = choice.message
        content = (getattr(msg, "content", None) or "")
        normalized_calls: list[dict] = []
        for call in (getattr(msg, "tool_calls", None) or []):
            fn = getattr(call, "function", None)
            args_raw = getattr(fn, "arguments", "") if fn is not None else ""
            try:
                parsed = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except Exception:
                parsed = {}
            normalized_calls.append(
                {
                    "id": getattr(call, "id", None),
                    "name": getattr(fn, "name", "") if fn is not None else "",
                    "arguments": parsed if isinstance(parsed, dict) else {},
                }
            )

        usage = getattr(completion, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)

        return ChatResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            tool_calls=normalized_calls,
            raw=completion,
        )

    def embed(self, text: str) -> list[float]:
        client = self._client()
        resp = client.embeddings.create(model="text-embedding-3-small", input=text)
        return list(resp.data[0].embedding)

    def test_connection(self) -> tuple[bool, str, int]:
        """Retrieve the selected model: authenticated, free, no thinking-token trap.

        A one-token generate was the probe until a reasoning model (gpt-5.4-mini)
        spent its first tokens thinking and OpenAI answered 400 "max_tokens or
        model output limit was reached" to a working key (#1315). `GET
        /models/{id}` still fails in OpenAI's own words for a bad key (401) and
        for a model this key cannot see (404).
        """
        started = time.perf_counter()
        try:
            self._client().models.retrieve(self.default_model)
            elapsed = int((time.perf_counter() - started) * 1000)
            return True, "OK", elapsed
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            return False, str(exc), elapsed

    def list_models(self) -> list[ProviderModel]:
        """Chat-capable models this key can see, newest first.

        OpenAI returns one flat list covering embeddings, speech, images and
        moderation alongside chat, and nothing in the payload says which is
        which - so the families that cannot hold a conversation are named here
        and dropped. A new chat family is included automatically; a new
        non-chat one shows up until its keyword is added, which is the right way
        round for a list an operator can also type into.
        """
        client = self._client(timeout=LIST_MODELS_TIMEOUT_SECONDS, max_retries=1)
        models = client.models.list()
        rows: list[tuple[int, ProviderModel]] = []
        for item in getattr(models, "data", None) or []:
            model_id = str(getattr(item, "id", "") or "")
            if not model_id or not _openai_is_chat_model(model_id):
                continue
            # `created` is the release timestamp OpenAI ships with each model.
            # Sorting on the id instead only looks like newest-first: it puts
            # `o4-mini` above every `gpt-*` and `gpt-4o` above `gpt-4.1`.
            created = int(getattr(item, "created", 0) or 0)
            rows.append((created, ProviderModel(value=model_id, label=model_id)))
        rows.sort(key=lambda row: (row[0], row[1].value), reverse=True)
        return [model for _, model in rows]


# The non-chat families in OpenAI's flat model list. Substring match, because the
# ids carry the family in the middle as often as at the front
# (`gpt-4o-mini-tts`, `gpt-4o-transcribe`).
_OPENAI_NON_CHAT = (
    "embedding",
    "tts",
    "transcribe",
    "whisper",
    "dall-e",
    "moderation",
    "image",
    "audio",
    "realtime",
    "sora",
    "codex",
    "babbage",
    "davinci",
)


def _openai_is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    if any(part in lowered for part in _OPENAI_NON_CHAT):
        return False
    return lowered.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4"))


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def _split_system_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Anthropic and Gemini both carry the system prompt out of band.

    Anthropic wants a ``system`` parameter, Gemini a ``systemInstruction``
    block; either way it is not a member of the message list.

    We concatenate any ``system`` entries from the OpenAI-style list and
    return ``(system_text, non_system_messages)``. Tool/assistant messages
    are passed through; tool results are converted to a user-role message
    with ``tool_result`` content blocks (handled by ``_convert_messages``).
    """
    system_parts: list[str] = []
    rest: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            content = msg.get("content")
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        system_parts.append(block["text"])
        else:
            rest.append(msg)
    return ("\n\n".join(p for p in system_parts if p).strip(), rest)


def _convert_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-style ``[{type,function:{name,description,parameters}}]``
    to Anthropic's ``[{name,description,input_schema}]``."""
    out: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _convert_messages_to_anthropic(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-style messages (user/assistant/tool with tool_calls) to
    Anthropic's content-block format.

    Tool-result messages (``role=tool``) become ``user`` messages with a
    single ``tool_result`` content block keyed by ``tool_call_id`` →
    ``tool_use_id``. Assistant messages with ``tool_calls`` produce content
    blocks containing both any text and ``tool_use`` entries.
    """
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            tool_call_id = msg.get("tool_call_id") or msg.get("tool_use_id") or ""
            content = msg.get("content")
            if not isinstance(content, str):
                content = json.dumps(content)
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": content,
                        }
                    ],
                }
            )
            continue

        if role == "assistant":
            blocks: list[dict] = []
            text = msg.get("content")
            if isinstance(text, str) and text.strip():
                blocks.append({"type": "text", "text": text})
            for call in msg.get("tool_calls") or []:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                args_raw = fn.get("arguments") or "{}"
                try:
                    parsed = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                except Exception:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": fn.get("name") or "",
                        "input": parsed,
                    }
                )
            if not blocks:
                blocks = [{"type": "text", "text": ""}]
            out.append({"role": "assistant", "content": blocks})
            continue

        # default: user message (or anything else) - pass through as text
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": "user", "content": content})
        elif isinstance(content, list):
            out.append({"role": "user", "content": content})
        else:
            out.append({"role": "user", "content": json.dumps(content) if content is not None else ""})
    return out


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, default_model: str = "claude-haiku-4-5") -> None:
        self.api_key = api_key
        self.default_model = default_model

    def _client(self, **overrides: Any):
        """The SDK seam. `overrides` reach the constructor (timeouts for listing)."""
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key, **overrides)

    def chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        images: Optional[list[ImagePart]] = None,
        response_format: Optional[dict] = None,
        json_schema: Optional[dict] = None,
        json_schema_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ChatResult:
        # A caller-set timeout turns the SDK's own retries off: the caller owns the retry
        # loop and the budget (`chatbot/llm_call.py`), and a silent second attempt doubles it.
        client = self._client(timeout=timeout, max_retries=0) if timeout is not None else self._client()
        system_text, rest = _split_system_messages(messages)
        # Structured-output: Anthropic has no response_format=json_schema. Force
        # a single tool whose input_schema IS the target schema and require it
        # via tool_choice - the model's tool_use.input is the schema-valid object.
        # We surface that object as ``content`` (JSON string) so the caller parses
        # it exactly like the OpenAI path.
        if json_schema is not None:
            tool_name = json_schema_name or "structured_result"
            ant_messages = _convert_messages_to_anthropic(rest)
            if images:
                ant_messages = _attach_images_anthropic(ant_messages, images)
            forced_kwargs: dict[str, Any] = {
                "model": model or self.default_model,
                "messages": ant_messages,
                "temperature": temperature,
                "max_tokens": max_tokens or 1024,
                "tools": [{
                    "name": tool_name,
                    "description": "Emit the structured result.",
                    "input_schema": json_schema,
                }],
                "tool_choice": {"type": "tool", "name": tool_name},
            }
            if system_text:
                forced_kwargs["system"] = system_text
            resp = client.messages.create(**forced_kwargs)
            payload: dict | None = None
            for block in (getattr(resp, "content", None) or []):
                if getattr(block, "type", None) == "tool_use":
                    payload = getattr(block, "input", {}) or {}
                    break
            if payload is None:
                # No tool_use block (e.g. max_tokens truncation / refusal). Raise so
                # the caller's retry fires instead of silently emitting an empty
                # object that would validate into a confident default result.
                raise RuntimeError(
                    f"forced tool '{tool_name}' produced no tool_use block "
                    f"(stop_reason={getattr(resp, 'stop_reason', None)})"
                )
            usage = getattr(resp, "usage", None)
            pt = int(getattr(usage, "input_tokens", 0) or 0)
            ct = int(getattr(usage, "output_tokens", 0) or 0)
            return ChatResult(
                content=json.dumps(payload, ensure_ascii=False),
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=pt + ct,
                tool_calls=[],
                raw=resp,
            )
        # Anthropic has no native JSON-mode flag; emulate by appending an
        # explicit instruction to the system prompt. The extract service
        # validates the response is parseable JSON either way.
        if response_format and response_format.get("type") == "json_object":
            json_directive = (
                "Return ONLY a single JSON object as the assistant message - "
                "no prose, no code fences, no leading or trailing text."
            )
            system_text = f"{system_text}\n\n{json_directive}".strip()
        ant_messages = _convert_messages_to_anthropic(rest)
        if images:
            ant_messages = _attach_images_anthropic(ant_messages, images)
        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": ant_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 1024,
        }
        if system_text:
            kwargs["system"] = system_text
        if tools:
            kwargs["tools"] = _convert_tools_to_anthropic(tools)

        resp = client.messages.create(**kwargs)

        text_chunks: list[str] = []
        normalized_calls: list[dict] = []
        for block in (getattr(resp, "content", None) or []):
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_chunks.append(getattr(block, "text", "") or "")
            elif block_type == "tool_use":
                normalized_calls.append(
                    {
                        "id": getattr(block, "id", None) or "",
                        "name": getattr(block, "name", "") or "",
                        "arguments": getattr(block, "input", {}) or {},
                    }
                )

        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens = prompt_tokens + completion_tokens

        return ChatResult(
            content="".join(text_chunks),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            tool_calls=normalized_calls,
            raw=resp,
        )

    def embed(self, text: str) -> list[float]:
        """Anthropic does not currently expose a first-party embeddings API.

        Fall back to OpenAI when an OpenAI key is configured at the platform
        level; otherwise raise so the caller surfaces a clear error.
        """
        from app.config import settings

        if settings.openai_api_key:
            return OpenAIProvider(settings.openai_api_key).embed(text)
        raise RuntimeError(
            "Anthropic provider does not support embeddings; configure OPENAI_API_KEY "
            "as a fallback for the embedding pipeline."
        )

    def test_connection(self) -> tuple[bool, str, int]:
        """Retrieve the selected model, for the same reason as the OpenAI probe.

        A one-token generate leaves an extended-thinking model no room to answer
        (#1315); `GET /v1/models/{id}` accepts an alias, costs nothing, and fails
        in Anthropic's own words for a bad key (401) or an unknown model (404).
        """
        started = time.perf_counter()
        try:
            self._client().models.retrieve(self.default_model)
            elapsed = int((time.perf_counter() - started) * 1000)
            return True, "OK", elapsed
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            return False, str(exc), elapsed

    def list_models(self) -> list[ProviderModel]:
        """Every model this key can see. Anthropic lists chat models only.

        The API returns them newest first and carries a display name, so neither
        is reconstructed here.
        """
        client = self._client(timeout=LIST_MODELS_TIMEOUT_SECONDS, max_retries=1)
        models = client.models.list(limit=100)
        out: list[ProviderModel] = []
        for item in getattr(models, "data", None) or []:
            model_id = str(getattr(item, "id", "") or "")
            if not model_id:
                continue
            label = str(getattr(item, "display_name", "") or "") or model_id
            out.append(ProviderModel(value=model_id, label=label))
        return out


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------


# The REST surface rather than the `google-genai` SDK: `httpx` is already a
# dependency of this service, the generateContent shape is stable, and one less
# vendor SDK is one less import that can fail in the worker.
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Generous, because the vision lane sends a full-page image and the caller
# (the media worker) already bounds the whole extraction with its own join.
GEMINI_TIMEOUT_SECONDS = 120.0

GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"

# `gemini-embedding-001` is natively 3072-dimensional and supports Matryoshka
# truncation to a smaller size. A truncated vector is no longer unit-length,
# and cosine distance assumes it is, so anything below the native size is
# re-normalized here (Google's own guidance for every reduced output size).
GEMINI_EMBEDDING_NATIVE_DIMS = 3072

# Thinking budget, in tokens, for a `pro` model. 2.5 Pro cannot have thinking
# switched off and rejects anything below 128, so it gets a small fixed budget
# rather than a zero.
GEMINI_PRO_THINKING_BUDGET = 1024


def _gemini_thinking_budget(model: Optional[str]) -> Optional[int]:
    """How many tokens this model may spend thinking before it answers.

    `None` means the model has no thinking to budget and `thinkingConfig` must
    be left off the request altogether: Google introduced thinking budgets with
    the 2.5 family, and 2.0 and older reject the field with a 400. The model box
    on the media settings page is free text, so an operator typing
    `gemini-2.0-flash` would otherwise fail every call.

    From 2.5 up, thinking is on by default and its tokens are spent out of
    `maxOutputTokens`. Left alone, a tight budget (the vision lane sends 2048)
    can be consumed entirely by thinking and come back as a candidate with no
    parts at all - `finishReason=MAX_TOKENS` with nothing to parse.

    `flash` and `flash-lite` want the smallest budget the model will take: the
    work is reading a photo into a fixed JSON shape, not reasoning. On the 2.5
    family that is zero. **The 3.x family rejects zero outright** - measured
    2026-08-22 against `gemini-3.5-flash-lite`, `thinkingBudget: 0` comes back
    `400 INVALID_ARGUMENT` with no field named, while 1 and above answer
    normally - so 3.x gets 1, which says the same thing in the only dialect it
    accepts. Getting this wrong fails every call on the model, not some of them.

    A model id with no readable version is treated as pre-2.5, because omitting
    the field costs at worst a shorter answer while sending it to a model that
    does not take it costs the whole call.
    """
    name = (model or "").lower()
    version = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    if version is None:
        return None
    major, minor = int(version.group(1)), int(version.group(2) or 0)
    if (major, minor) < (2, 5):
        return None
    if "flash" in name:
        return 0 if major < 3 else 1
    return GEMINI_PRO_THINKING_BUDGET


# Gemini's schema dialect is a strict subset of JSON Schema (OpenAPI 3.0). An
# unknown key is a 400, not an ignored hint - and the schemas in this repo are
# written for OpenAI strict mode, which REQUIRES `additionalProperties: false`
# and spells a nullable as `["string", "null"]`. Both are rejected verbatim, so
# a schema is translated rather than passed through.
_GEMINI_SCHEMA_KEYS = {
    "anyOf",
    "description",
    "enum",
    "format",
    "items",
    "maxItems",
    "maximum",
    "minItems",
    "minimum",
    "nullable",
    "properties",
    "propertyOrdering",
    "required",
    "title",
    "type",
}


def _gemini_schema(schema: Any) -> Any:
    """Translate a JSON Schema into Gemini's OpenAPI subset."""
    if isinstance(schema, list):
        return [_gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            # `additionalProperties`, `$schema`, `default`, `examples`, ...
            continue
        if key == "type" and isinstance(value, list):
            concrete = [item for item in value if item != "null"]
            out["type"] = concrete[0] if concrete else "string"
            if len(concrete) != len(value):
                out["nullable"] = True
            continue
        if key == "enum" and isinstance(value, list):
            # `Schema.enum` is a proto `repeated string`, so the null branch an
            # OpenAI strict schema spells as a literal `None` member is a 400
            # rather than an ignored hint. It carries the same meaning as the
            # `["string","null"]` type union above, and is translated the same
            # way: dropped from the list, recorded as `nullable`.
            members = [item for item in value if item is not None and item != "null"]
            out["enum"] = members
            if len(members) != len(value):
                out["nullable"] = True
            continue
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {
                name: _gemini_schema(prop) for name, prop in value.items()
            }
            continue
        if key in ("items", "anyOf"):
            out[key] = _gemini_schema(value)
            continue
        out[key] = value
    return out


def _convert_tools_to_gemini(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-style ``[{type,function:{name,description,parameters}}]``
    to Gemini's ``[{functionDeclarations: [...]}]``.

    Gemini takes ONE tools entry holding every declaration, not one entry per
    tool, so the return is a single-element list (or empty, when nothing
    convertible was passed).
    """
    declarations: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        nested = tool.get("function")
        fn = nested if isinstance(nested, dict) else tool
        name = fn.get("name")
        if not name:
            continue
        declaration: dict[str, Any] = {
            "name": name,
            "description": fn.get("description") or "",
        }
        raw_params = fn.get("parameters")
        parameters = _gemini_schema(raw_params) if isinstance(raw_params, dict) else {}
        # Gemini rejects an OBJECT schema whose `properties` is empty with a 400,
        # so a parameterless tool carries no `parameters` at all.
        if parameters.get("properties"):
            declaration["parameters"] = parameters
        declarations.append(declaration)
    if not declarations:
        return []
    return [{"functionDeclarations": declarations}]


def _gemini_text_parts(content: Any) -> list[dict]:
    """The ``parts`` for a plain (non tool-call) message."""
    if isinstance(content, str):
        return [{"text": content}]
    if isinstance(content, list):
        parts: list[dict] = []
        for block in content:
            if isinstance(block, str):
                parts.append({"text": block})
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append({"text": block["text"]})
        return parts or [{"text": ""}]
    return [{"text": json.dumps(content) if content is not None else ""}]


def _convert_messages_to_gemini(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-style messages to Gemini ``contents``.

    Role mapping is ``assistant -> model`` and everything else ``-> user``;
    Gemini has no third role, so a tool result rides back as a ``user`` turn
    carrying a ``functionResponse`` part - the same trick the Anthropic adapter
    uses for ``tool_result``.

    Gemini keys a function response by NAME, not by call id, so the id -> name
    mapping is carried forward from the assistant turn that made the call. A
    tool message whose id was never announced falls back to its own ``name``
    field and finally to the id itself, so a malformed history still produces a
    well-formed request instead of raising.
    """
    contents: list[dict] = []
    call_names: dict[str, str] = {}

    for msg in messages:
        role = msg.get("role")

        if role == "tool":
            call_id = msg.get("tool_call_id") or msg.get("tool_use_id") or ""
            name = msg.get("name") or call_names.get(call_id) or call_id
            raw = msg.get("content")
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"result": raw}
            else:
                payload = raw
            if not isinstance(payload, dict):
                # Gemini requires an object here; a bare list or scalar is a 400.
                payload = {"result": payload}
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {"functionResponse": {"name": name, "response": payload}}
                    ],
                }
            )
            continue

        if role == "assistant":
            parts: list[dict] = []
            text = msg.get("content")
            if isinstance(text, str) and text.strip():
                parts.append({"text": text})
            for call in msg.get("tool_calls") or []:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                name = fn.get("name") or ""
                args_raw = fn.get("arguments") or "{}"
                try:
                    parsed = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                except Exception:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                call_id = call.get("id") if isinstance(call, dict) else None
                if call_id:
                    call_names[str(call_id)] = name
                parts.append({"functionCall": {"name": name, "args": parsed}})
            if not parts:
                parts = [{"text": ""}]
            contents.append({"role": "model", "parts": parts})
            continue

        contents.append({"role": "user", "parts": _gemini_text_parts(msg.get("content"))})

    return contents


def _is_tool_result_turn(content: dict) -> bool:
    """A ``user`` turn that is really a tool result, not something a human said."""
    return any(
        isinstance(part, dict) and "functionResponse" in part
        for part in (content.get("parts") or [])
    )


def _attach_images_gemini(contents: list[dict], images: list[ImagePart]) -> list[dict]:
    """Append ``images`` to the final user turn as ``inlineData`` parts.

    Same placement rule as ``_attach_images_openai``: last user message, or a
    new one when the conversation has none yet - except that Gemini has no
    ``tool`` role, so a converted tool result also rides back as ``role: user``
    (``_convert_messages_to_gemini``). Dropping an image into one of those turns
    would mix an ``inlineData`` part into a ``functionResponse`` turn, so the
    scan skips them and a fresh user turn is synthesized when the tail of the
    conversation is nothing but tool results.
    """
    if not images:
        return contents
    out = [dict(c) for c in contents]
    last_user_idx = -1
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user" and not _is_tool_result_turn(out[i]):
            last_user_idx = i
            break
    if last_user_idx == -1:
        out.append({"role": "user", "parts": []})
        last_user_idx = len(out) - 1

    turn = out[last_user_idx]
    parts = list(turn.get("parts") or [])
    for img in images:
        parts.append({"inlineData": {"mimeType": img.mime, "data": img.data_b64}})
    turn["parts"] = parts
    out[last_user_idx] = turn
    return out


def _gemini_error_message(response: Any) -> str:
    """Gemini's own words for a failed call, never a generic message."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - fall back to the raw text
        return (getattr(response, "text", "") or "").strip()[:500]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
    return str(body)[:500]


def _gemini_operator_hint(status_code: int, provider_message: str) -> str:
    """The sentence that says whose problem this is, appended to Gemini's own words.

    A quota wall and a bad key are the operator's to fix, not the document's,
    and both used to reach the screen as raw provider JSON that read like the
    uploaded file was unreadable. The provider message is still carried (it is
    the only thing that distinguishes one 400 from another); this only adds who
    can act on it. Everything else gets nothing, because we would be guessing.
    """
    if status_code == 429:
        lowered = (provider_message or "").lower()
        reason = (
            "the billing cap has been reached"
            if "spend" in lowered
            else "the rate limit has been reached"
        )
        return (
            f" The reader is unavailable because {reason}. Nothing uploaded is lost. "
            "Raise the cap in Google AI Studio, then try again."
        )
    if status_code in (401, 403):
        return (
            " The reader rejected our key. Nothing uploaded is lost. "
            "Check GEMINI_API_KEY, then try again."
        )
    return ""


class GeminiProvider:
    """Google Gemini over the Generative Language REST API."""

    name = "gemini"

    def __init__(self, api_key: str, default_model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.default_model = default_model

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        """The single HTTP seam, so tests can record a response without a network."""
        import httpx

        if not self.api_key:
            raise RuntimeError("No Gemini API key is configured.")
        try:
            response = httpx.request(
                method,
                f"{GEMINI_API_BASE}/{path}",
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=json_body,
                params=params,
                timeout=GEMINI_TIMEOUT_SECONDS if timeout is None else timeout,
            )
        except httpx.HTTPError as exc:  # network-level failure
            raise RuntimeError(f"Gemini request failed: {exc}") from exc

        if response.status_code >= 400:
            provider_message = _gemini_error_message(response)
            raise RuntimeError(
                f"Gemini call failed ({response.status_code}): {provider_message}"
                f"{_gemini_operator_hint(response.status_code, provider_message)}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("Gemini returned a non-JSON response.") from exc
        if not isinstance(body, dict):
            raise RuntimeError("Gemini returned an unexpected response shape.")
        return body

    def chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        images: Optional[list[ImagePart]] = None,
        response_format: Optional[dict] = None,
        json_schema: Optional[dict] = None,
        json_schema_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ChatResult:
        system_text, rest = _split_system_messages(messages)
        contents = _convert_messages_to_gemini(rest)
        if images:
            contents = _attach_images_gemini(contents, images)

        model_name = model or self.default_model

        payload: dict[str, Any] = {"contents": contents}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if tools:
            declarations = _convert_tools_to_gemini(tools)
            if declarations:
                payload["tools"] = declarations

        wants_json = json_schema is not None or (
            bool(response_format) and response_format.get("type") == "json_object"
        )
        if payload.get("tools") and wants_json:
            # Gemini refuses `responseMimeType: application/json` alongside
            # function declarations with a 400. Raising here rather than quietly
            # dropping the mime type is deliberate: a caller that asked for both
            # is holding a contradiction, and silently answering in prose would
            # surface much later as a JSON parse failure with no clue why.
            raise ValueError(
                "Gemini cannot combine function calling with JSON response mode. "
                "Pass either `tools` or a JSON `response_format`/`json_schema`, "
                "not both."
            )

        generation: dict[str, Any] = {"temperature": temperature}
        # Thinking is on by default from 2.5 and is billed against
        # `maxOutputTokens`, so the budget is set explicitly and ADDED to what
        # the caller asked for - the caller's number is its answer budget, not
        # a pool the model gets to think out of. A model with no thinking to
        # budget (2.0 and older) gets no `thinkingConfig` and the caller's
        # `max_tokens` unchanged, because the field itself is a 400 there.
        thinking_budget = _gemini_thinking_budget(model_name)
        if thinking_budget is not None:
            generation["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        if max_tokens is not None:
            generation["maxOutputTokens"] = max_tokens + (thinking_budget or 0)
        # Structured output is native here: `responseSchema` constrains decoding
        # the same way OpenAI's strict json_schema does, so `content` comes back
        # as a schema-valid JSON string and the caller parses it identically.
        # `json_schema_name` has no Gemini counterpart - the schema is unnamed.
        if json_schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = _gemini_schema(json_schema)
        elif response_format and response_format.get("type") == "json_object":
            generation["responseMimeType"] = "application/json"
        payload["generationConfig"] = generation

        body = self._request(
            "POST", f"models/{model_name}:generateContent", json_body=payload, timeout=timeout
        )

        candidates = body.get("candidates") or []
        if not candidates:
            # A safety block returns 200 with no candidate at all. Raise rather
            # than emit an empty string the caller would treat as a real answer.
            reason = (body.get("promptFeedback") or {}).get("blockReason")
            raise RuntimeError(
                f"Gemini returned no candidates (blockReason={reason or 'unknown'})."
            )
        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        parts = (candidate.get("content") or {}).get("parts") or []

        text_chunks: list[str] = []
        normalized_calls: list[dict] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                text_chunks.append(part["text"])
                continue
            call = part.get("functionCall")
            if isinstance(call, dict) and call.get("name"):
                args = call.get("args")
                # Gemini returns no call id. The agent loop needs one to pair a
                # tool result back to its call, so it is synthesized from the
                # ordinal and the name: stable for a given response, and unique
                # within the turn even when a tool is called twice.
                normalized_calls.append(
                    {
                        "id": f"gemini_{len(normalized_calls)}_{call['name']}",
                        "name": str(call["name"]),
                        "arguments": args if isinstance(args, dict) else {},
                    }
                )

        finish = candidate.get("finishReason")
        # A truncated answer is worse than a failed one: half a purchase order
        # looks like a whole one with lines missing, and nothing downstream can
        # tell. Same for a candidate cut short by SAFETY or RECITATION - it
        # carries real text, so only the finish reason says it is incomplete.
        # `MAX_TOKENS` is named separately because the caller's answer budget is
        # already topped up with the thinking budget above, so reaching it means
        # the answer itself ran out of room.
        if finish == "MAX_TOKENS":
            raise RuntimeError("Gemini stopped early: MAX_TOKENS (response truncated).")
        if finish and finish != "STOP":
            raise RuntimeError(f"Gemini stopped early: {finish}.")

        if not text_chunks and not normalized_calls:
            raise RuntimeError(
                "Gemini returned an empty candidate "
                f"(finishReason={finish or 'unknown'})."
            )

        usage = body.get("usageMetadata") or {}
        prompt_tokens = int(usage.get("promptTokenCount") or 0)
        # Reasoning is reported separately as `thoughtsTokenCount` but is
        # billed as output and IS inside `totalTokenCount`. Counting only
        # `candidatesTokenCount` would leave prompt + completion short of the
        # total on every thinking model, and under-report the spend the usage
        # dashboard shows.
        completion_tokens = int(usage.get("candidatesTokenCount") or 0) + int(
            usage.get("thoughtsTokenCount") or 0
        )
        total_tokens = int(
            usage.get("totalTokenCount") or (prompt_tokens + completion_tokens)
        )

        return ChatResult(
            content="".join(text_chunks),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            tool_calls=normalized_calls,
            raw=body,
        )

    def embed(self, text: str) -> list[float]:
        """Embed via ``embedContent``, sized to the pgvector column.

        ``outputDimensionality`` is asked for explicitly because the column the
        pipeline writes into is ``settings.embedding_dimensions`` wide and the
        model's native size is larger; a mismatch is an insert error, not a
        quality question.
        """
        from app.config import settings

        dimensions = int(settings.embedding_dimensions or 0)
        request: dict[str, Any] = {
            "model": f"models/{GEMINI_EMBEDDING_MODEL}",
            "content": {"parts": [{"text": text}]},
        }
        if dimensions and dimensions != GEMINI_EMBEDDING_NATIVE_DIMS:
            request["outputDimensionality"] = dimensions

        body = self._request(
            "POST", f"models/{GEMINI_EMBEDDING_MODEL}:embedContent", json_body=request
        )
        values = (body.get("embedding") or {}).get("values") or []
        if not values:
            raise RuntimeError("Gemini embedContent returned no vector.")
        vector = [float(v) for v in values]
        if len(vector) != GEMINI_EMBEDDING_NATIVE_DIMS:
            magnitude = sum(v * v for v in vector) ** 0.5
            if magnitude:
                vector = [v / magnitude for v in vector]
        return vector

    def test_connection(self) -> tuple[bool, str, int]:
        """List one model: authenticated, free, and no thinking-token trap.

        A one-token generate is the trap all three adapters avoid now: a
        Gemini 2.5 model spends its first tokens thinking and returns a
        candidate with no parts at ``maxOutputTokens: 1`` - which would report
        a perfectly good key as broken.
        """
        started = time.perf_counter()
        try:
            self._request("GET", "models", params={"pageSize": 1})
            elapsed = int((time.perf_counter() - started) * 1000)
            return True, "OK", elapsed
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            return False, str(exc), elapsed

    def list_models(self) -> list[ProviderModel]:
        """Models this key can call `generateContent` on, by Google's own account.

        `supportedGenerationMethods` is the filter rather than a name pattern,
        because the same list carries embedding, speech and video models. The
        media families are dropped on top of it: several of them do support
        generateContent and none of them can read a photo into JSON, so offering
        one is offering a model that fails at the only job this picker is for.

        Google's display names are used where present. Note what this list is
        NOT: `gemini-2.5-flash-lite` appears here for a key that gets a 404 the
        moment it calls the model (see the note above `ProviderModel`).
        """
        out: list[ProviderModel] = []
        params: dict[str, Any] = {"pageSize": 200}
        # `pageSize` is a request, not a guarantee. An ignored `nextPageToken`
        # would drop models from the picker while still reporting a live list,
        # which reads exactly like a model the provider retired.
        for _ in range(10):
            body = self._request(
                "GET", "models", params=params, timeout=LIST_MODELS_TIMEOUT_SECONDS
            )
            for item in body.get("models") or []:
                if not isinstance(item, dict):
                    continue
                if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                    continue
                model_id = str(item.get("name") or "").split("/")[-1]
                if not model_id or _gemini_is_media_model(model_id):
                    continue
                label = str(item.get("displayName") or "") or model_id
                out.append(ProviderModel(value=model_id, label=label))
            token = body.get("nextPageToken")
            if not token:
                break
            params = {"pageSize": 200, "pageToken": token}
        return out


# Gemini families that answer `generateContent` without being able to do this
# lane's work (read text, read a photo). Substring match on the id.
_GEMINI_NON_TEXT = (
    "embedding",
    "tts",
    "image",
    "lyria",
    "veo",
    "imagen",
    "robotics",
    "computer-use",
)


def _gemini_is_media_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(part in lowered for part in _GEMINI_NON_TEXT)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_provider(provider: str, api_key: str, model: Optional[str] = None) -> LLMProvider:
    """Resolve a provider implementation by name.

    Raises ``ValueError`` for unknown providers so the calling code can
    surface a sensible error to the operator.
    """
    key = (provider or "").strip().lower()
    if key == "openai":
        return OpenAIProvider(api_key=api_key, default_model=model or "gpt-4o-mini")
    if key == "anthropic":
        return AnthropicProvider(api_key=api_key, default_model=model or "claude-haiku-4-5")
    if key == "gemini":
        return GeminiProvider(api_key=api_key, default_model=model or "gemini-2.5-flash")
    raise ValueError(f"Unsupported provider: {provider}")
