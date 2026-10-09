"""AI assistant services (config + MCP-RAG chat orchestration)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo
import hashlib
import json
import logging
import re
import time
import uuid
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.models.ai_assistant import (
    AIAssistantConfig,
    AIAssistantConversation,
    AIAssistantGovernanceEvent,
    AIAssistantMessage,
    AIAssistantUsageLog,
)
from app.modules.runtime.installer import DEFAULT_TENANT_ID, get_enabled_module_keys, tenant_has_any_module_row
from app.schemas.ai_assistant import (
    AIAssistantAuthContext,
    AIAssistantConfigUpdate,
    PageSnapshotPayload,
)
from app.services import ai_prompt_registry
from app.services.ai_trace import (
    KIND_CHAIN,
    KIND_GUARDRAIL,
    KIND_RETRIEVER,
    KIND_TOOL,
    TurnTrace,
)
from app.services.embedding_service import EmbeddingReadService
from app.services.entity_resolver import ResolutionResult, resolve_references
from app.services.llm_provider import ChatResult, LLMProvider, get_provider, resolve_api_key
from app.schemas.ai_semantic_parser import (
    PARSE_RESULT_JSON_SCHEMA,
    PARSE_RESULT_SCHEMA_NAME,
    ParseResult,
    fallback_parse,
)
from app.services.user_service import UserPermissionService

logger = logging.getLogger(__name__)


def _mask_key(raw: str | None) -> str | None:
    if not raw:
        return None
    if len(raw) <= 8:
        return "****"
    return f"****{raw[-4:]}"


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_MAP = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
}
_BLOCK_BREAK_RE = re.compile(
    r"</(p|h[1-6]|li|blockquote|div|br|pre)>|<br\s*/?>|<li[^>]*>",
    flags=re.IGNORECASE,
)


def _html_to_text(raw: str | None) -> str:
    """Convert rich-text-editor HTML to a plain-text prompt suitable for LLMs.

    The frontend system-prompt editor stores HTML; the LLM consumes plain text.
    We turn block-level tag boundaries into newlines, strip remaining tags, and
    decode a small set of HTML entities. If the input has no tags, it is
    returned unchanged so legacy plain-text prompts keep working.
    """
    if not raw:
        return ""
    if "<" not in raw:
        return raw
    with_breaks = _BLOCK_BREAK_RE.sub("\n", raw)
    stripped = _HTML_TAG_RE.sub("", with_breaks)
    for entity, replacement in _HTML_ENTITY_MAP.items():
        stripped = stripped.replace(entity, replacement)
    lines = [line.rstrip() for line in stripped.splitlines()]
    collapsed: list[str] = []
    for line in lines:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()


# MCP tools that mutate state - suppressed during a prompt dry-run so a test
# turn can never persist a real complaint / PR / stock inquiry / ticket / link.
# Matched by name suffix/substring (the catalog names all end in the verb, e.g.
# `crm_forms_stock_inquiry_submit`, `crm_it_support_ticket_create`,
# `crm_forms_entity_attachments_link`). Read tools like `crm_portal_link_get`
# end in `_get`, so a plain `_link` suffix check is safe.
#
# Mutating verbs any write tool name ends in. MUST stay in sync with the
# write-confirm gate - a record-action tool (e.g. crm_complaint_close,
# crm_purchase_request_approve) whose suffix is missing here would execute
# WITHOUT confirmation. Read tools end in _list/_get/_dashboard/_summary/etc.,
# never these, so the suffix match cannot gate a read.
_WRITE_TOOL_SUFFIXES = (
    "_submit", "_create", "_link", "_close", "_cancel",
    "_approve", "_reject", "_update", "_delete", "_add", "_send",
)


# Write tools whose NAME does not end in a write verb, listed by hand.
#
# `crm_ideation_turn` is the whole reason this exists (security review S2b-S5,
# B1b). It ends in `_turn`, so the suffix rule read it as a read tool and BOTH
# controls below silently did not apply: the write-confirm gate never halted it,
# and the prompt dry-run suppression never stripped it - so testing a prompt in
# the admin registry could mint a real ideation draft against a real respond.io
# contact. The tool NAME is a published contract (D6: it is in the MCP catalog
# and n8n references it), so the name stays and write-ness is stated here.
#
# Add a name here only when it genuinely mutates state; a tool that ends in a
# verb is already covered and does not need a second declaration.
_WRITE_TOOL_NAMES = frozenset({"crm_ideation_turn"})


def _is_write_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower()
    if name in _WRITE_TOOL_NAMES:
        return True
    return name.endswith(_WRITE_TOOL_SUFFIXES) or "_ticket_create" in name


def _current_date_directive() -> str:
    """Today's date (Asia/Kuala_Lumpur) + relative-date resolution rule.

    Ports the n8n sub-semantic-parser technique: LLMs have no clock and will
    otherwise resolve "last week" against their training cutoff (we saw a query
    land in 2023). Injecting the real current date lets the model convert
    relative dates to absolute calendar dates before it calls any data tool.
    Explicit UTC+8 (matches n8n's `$now.toUTC(8*60)`) so it is correct
    regardless of the server's own timezone.
    """
    now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
    today = now.strftime("%A, %d %B %Y")
    return (
        f"CURRENT DATE: {today} (Asia/Kuala_Lumpur).\n"
        "When the user's message contains a relative date or period - e.g. "
        "\"today\", \"yesterday\", \"tomorrow\", \"last week\", \"this month\", "
        "\"next 3 days\", or an upcoming weekday - resolve it to absolute "
        "calendar dates RELATIVE TO THE CURRENT DATE ABOVE before stating any "
        "date or calling any tool. A single day uses the same start and end "
        "date; a \"week\" is Monday - Sunday. Never infer the current date from "
        "your training data - always use CURRENT DATE above."
    )


@dataclass
class MCPToolCallResult:
    tool_name: str
    ok: bool
    output: str


# Below this parser self-confidence, a non-capability intent is demoted to the
# agent loop (the safe default) rather than trusting a shaky classification.
_LOW_CONFIDENCE_FLOOR = 0.4


@dataclass
class _RouteDecision:
    """Deterministic router output (M0). ``kind`` names the processing branch:
    ``capability`` (zero-LLM catalog), ``record_answer`` (facts already loaded),
    ``ideate`` (product idea → ideation intake / web redirect), or ``agent``
    (the tool-using loop). Flags refine the agent branch."""
    kind: str  # "capability" | "record_answer" | "ideate" | "agent"
    is_how_to: bool = False   # agent: deterministically pre-fetch the user guide
    skip_rag: bool = False    # agent: no live data needed → skip tool selection


# MCP tools intake UUIDs, but the LLM reliably passes the entity NAME/code it was
# shown (e.g. customer_ids=["HANLIM TRADING SDN BHD"] → backend 400 INVALID_UUID).
# The entity resolver already mapped those names → UUIDs this turn, so we
# deterministically substitute them into these UUID-intake params at dispatch
# rather than hoping the model copies a UUID. Maps param name → resolver
# entity_type. Keep in sync with the tools' `<entity>_ids` filter params.
_UUID_PARAM_ENTITY_TYPES: dict[str, str] = {
    "customer_ids": "customer",
    "customer_id": "customer",
    "product_ids": "product",
    "product_id": "product",
    "transporter_ids": "transporter",
    "transporter_id": "transporter",
    "order_ids": "customer_order",
    "order_id": "customer_order",
    "warehouse_ids": "warehouse",
    "warehouse_id": "warehouse",
    "supplier_ids": "supplier",
    "supplier_id": "supplier",
    "promotion_ids": "promotion",
    "promotion_id": "promotion",
    # Project Sales (AC-K1). `developer_party_ids` maps to the shared party resolver, which
    # covers developers, architects and main contractors in one probe -- the LLM writes the
    # company name it was shown either way.
    "project_ids": "project",
    "project_id": "project",
    "developer_party_ids": "project_party",
    "developer_party_id": "project_party",
    # "my pipeline" / "what is Ali working on". The tool takes a user uuid, and since the
    # slimmed project payload no longer carries owner uuids, a NAME is the only thing the model
    # ever has to work with -- so without this entry the owner filter cannot be reached at all.
    "owner_user_ids": "user",
    "owner_user_id": "user",
}

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _norm_entity_key(value: str) -> str:
    """Normalize a name/code for matching against resolver output: lowercase +
    collapse internal whitespace. Kept deliberately light so exact debtor names
    (which the resolver stores verbatim) match - no dash/punctuation stripping
    that could merge two distinct account names."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _coerce_arg_to_list(raw: Any) -> list[str]:
    """Normalize a tool-arg value into a list of scalar strings. Handles a real
    list, a JSON-array string (``'["a","b"]'``), a CSV string, or a scalar."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None]
    s = str(raw).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x is not None]
        except Exception:
            pass
    if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]
    return [s]


class _TurnToolCache:
    """Turn-scoped, strictly-keyed memo for live tool / guide-content calls.

    A fresh instance is created per ``respond()`` invocation (one assistant
    turn): there is NO cross-turn, global, or module-level state. Within a turn,
    the SAME tool invoked with identical args is served from cache so the agent
    loop (and the deterministic guide pre-fetch) never repeat the same live MCP
    call. Volatile data (e.g. stock levels) can change between turns, so the
    cache deliberately dies at the end of the turn.

    SECURITY (UAC3b.2): the cache key is
    ``sha256(user_id ∥ conversation_id ∥ turn_id ∥ tool_name ∥ sha256(canonical_sorted_args))``.
    Because the principal (``user_id``), thread (``conversation_id``) and turn
    (``turn_id``) are baked into every key, two different users / contacts /
    turns can NEVER collide on a cache entry even if they call the identical
    tool with identical args - cross-principal sharing is structurally
    impossible, not merely avoided by convention.
    """

    _SEP = "\x1f"  # unit separator - cannot appear in JSON / identifiers

    def __init__(self, *, user_id: str | None, conversation_id: str | None, turn_id: str | None):
        self.user_id = str(user_id or "")
        self.conversation_id = str(conversation_id or "")
        self.turn_id = str(turn_id or "")
        self._store: dict[str, str] = {}
        # Observability (UAC3b.4): live_calls counts loader invocations (real
        # tool calls), hits counts cache-served calls.
        self.live_calls = 0
        self.hits = 0

    @staticmethod
    def _args_hash(args: dict[str, Any] | None) -> str:
        canonical = json.dumps(
            args or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def key(self, tool_name: str, args: dict[str, Any] | None) -> str:
        raw = self._SEP.join(
            [
                self.user_id,
                self.conversation_id,
                self.turn_id,
                str(tool_name or ""),
                self._args_hash(args),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_or_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        loader: Callable[[], str],
    ) -> str:
        """Return the cached result for ``(tool_name, args)`` within this turn,
        invoking ``loader`` (the live call) exactly once on the first miss."""
        k = self.key(tool_name, args)
        if k in self._store:
            self.hits += 1
            return self._store[k]
        out = loader()
        self.live_calls += 1
        self._store[k] = out
        return out


class MCPToolCallError(RuntimeError):
    """#1262 slice 1 (F2): the MCP server's own `isError: true` on a `tools/call`
    result - the tool ran and reported failure (an invalid uuid, a refused filter),
    which nobody was reading before this, so the raw text ("Error executing tool
    ...") reached WhatsApp verbatim. Raising it is what lets `run_fetch`'s existing
    `except Exception` take its error arm instead of returning the text as an answer,
    and it is what the in-app AI assistant's own tool loop already catches and feeds
    back to the model as a `tool_call_failed` result (AC-S1-4, unchanged by this)."""


class MCPRuntimeClient:
    """Minimal JSON-RPC client for FastMCP streamable HTTP endpoint."""

    def __init__(self, base_url: str, timeout_seconds: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        rid = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(
                self.base_url,
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            body = self._decode_streamable_body(resp)
        if isinstance(body, dict) and "error" in body:
            raise RuntimeError(str(body.get("error")))
        if not isinstance(body, dict):
            raise RuntimeError("Invalid MCP response payload")
        return body.get("result") or {}

    @staticmethod
    def _headers() -> dict[str, str]:
        headers = {
            # FastMCP streamable HTTP requires both content types in Accept.
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if settings.mcp_auth_token:
            headers["Authorization"] = f"Bearer {settings.mcp_auth_token}"
        return headers

    def _decode_streamable_body(self, resp: httpx.Response) -> dict[str, Any]:
        content_type = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" not in content_type:
            return resp.json()

        # Streamable HTTP MCP commonly returns SSE frames:
        # event: message
        # data: {...json-rpc...}
        data_lines = []
        for raw_line in resp.text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_lines.append(line[5:].strip())
        if not data_lines:
            raise RuntimeError("MCP SSE response missing data frame")
        try:
            return json.loads(data_lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid MCP SSE JSON payload: {exc}") from exc

    def list_tools(self) -> set[str]:
        return set(self.list_tools_with_schema().keys())

    def list_tools_with_schema(self) -> dict[str, dict[str, Any]]:
        """MCP tools/list: name -> {description, inputSchema} for LLM argument synthesis."""
        result = self._rpc("tools/list", {})
        tools = result.get("tools") or []
        out: dict[str, dict[str, Any]] = {}
        for tool in tools:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            name = str(tool["name"])
            schema = tool.get("inputSchema")
            if schema is None:
                schema = tool.get("input_schema") or {}
            out[name] = {
                "description": tool.get("description") or "",
                "inputSchema": schema if isinstance(schema, dict) else {},
            }
        return out

    def call_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        result = self._rpc("tools/call", {"name": tool_name, "arguments": args})
        text = ""
        if "content" in result and isinstance(result["content"], list):
            chunks = []
            for item in result["content"]:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
            if chunks:
                text = "\n".join(chunks)
        # #1262 slice 1 (F2): `isError` is the JSON-RPC result's own verdict on the
        # call, not something the joined text above says anything about - raise
        # rather than hand the tool's error text back as if it were an answer.
        if result.get("isError"):
            raise MCPToolCallError(text or json.dumps(result))
        return text or json.dumps(result)


class AIAssistantConfigService:
    def __init__(self, db: Session):
        self.db = db

    def _ensure_singleton(self) -> AIAssistantConfig:
        row = self.db.query(AIAssistantConfig).order_by(AIAssistantConfig.created_at.asc()).first()
        if row:
            return row
        row = AIAssistantConfig()
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get(self) -> AIAssistantConfig:
        return self._ensure_singleton()

    def update(self, data: AIAssistantConfigUpdate, user_id: str) -> AIAssistantConfig:
        row = self._ensure_singleton()
        row.provider = data.provider.strip()
        row.model = data.model.strip()
        row.temperature = data.temperature
        row.system_prompt = data.system_prompt or ""
        row.enabled_tools = list(data.enabled_tools or [])
        row.rag_enabled = bool(data.rag_enabled)
        row.is_enabled = bool(data.is_enabled)
        row.updated_by_user_id = user_id
        if data.api_key is not None and data.api_key.strip():
            # Stored in this field for compatibility with IJM schema naming.
            row.api_key_ciphertext = data.api_key.strip()
        if data.anthropic_api_key is not None and data.anthropic_api_key.strip():
            row.anthropic_api_key_ciphertext = data.anthropic_api_key.strip()
        if data.gemini_api_key is not None and data.gemini_api_key.strip():
            row.gemini_api_key_ciphertext = data.gemini_api_key.strip()
        self.db.commit()
        self.db.refresh(row)
        return row

    def to_response_dict(self, row: AIAssistantConfig) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "provider": row.provider,
            "model": row.model,
            "temperature": row.temperature,
            "system_prompt": row.system_prompt,
            "api_key_masked": _mask_key(row.api_key_ciphertext),
            "anthropic_api_key_masked": _mask_key(row.anthropic_api_key_ciphertext),
            "gemini_api_key_masked": _mask_key(row.gemini_api_key_ciphertext),
            "enabled_tools": list(row.enabled_tools or []),
            "rag_enabled": bool(row.rag_enabled),
            "is_enabled": bool(row.is_enabled),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


class AIAssistantGovernanceService:
    def __init__(self, db: Session):
        self.db = db

    def build_auth_context(self, user_id: str) -> AIAssistantAuthContext:
        perm_service = UserPermissionService(self.db)
        role_ids = perm_service.get_user_role_ids(user_id)
        perms = sorted(perm_service.get_user_permission_slugs(user_id))
        if tenant_has_any_module_row(self.db, DEFAULT_TENANT_ID):
            enabled_modules = sorted(get_enabled_module_keys(self.db, DEFAULT_TENANT_ID))
        else:
            enabled_modules = []
        return AIAssistantAuthContext(
            user_id=user_id,
            role_ids=role_ids,
            permission_slugs=perms,
            enabled_modules=enabled_modules,
        )

    def log_denial(
        self,
        user_id: str | None,
        conversation_id: str | None,
        event_type: str,
        *,
        module_key: str | None = None,
        permission_slug: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            AIAssistantGovernanceEvent(
                user_id=user_id,
                conversation_id=conversation_id,
                event_type=event_type,
                module_key=module_key,
                permission_slug=permission_slug,
                details=details or {},
            )
        )
        self.db.commit()


class AIAssistantChatService:
    def __init__(self, db: Session):
        self.db = db
        self.cfg = AIAssistantConfigService(db)
        self.gov = AIAssistantGovernanceService(db)
        # Per-turn prompt-registry state. ``_turn_prompt_versions`` accumulates
        # one {name, version} entry per resolver-backed LLM call so respond()
        # can stamp it onto the assistant message's metadata (UAC C2).
        # ``_prompt_overrides`` maps prompt-key -> version_id for the dry-run
        # test route (UAC D5); empty for normal chat.
        self._turn_prompt_versions: list[dict[str, Any]] = []
        self._prompt_overrides: dict[str, str] = {}
        # When True (dry-run), write-capable MCP tools are stripped from the turn.
        self._suppress_write_tools: bool = False
        # M2 per-turn trace collector (buffers spans, flushed post-turn).
        # None outside a respond() turn; deep call sites guard on truthiness.
        self._turn_trace: TurnTrace | None = None

    def _resolve_prompt(self, name: str, **variables: Any) -> str:
        """Resolve a registered prompt key through the registry, honoring any
        per-turn dry-run override, and record the used (name, version) for
        metadata stamping. Never raises on a DB error (falls back)."""
        override_id = self._prompt_overrides.get(name)
        text, version = ai_prompt_registry.render(
            self.db, name, override_version_id=override_id, **variables
        )
        self._turn_prompt_versions.append({"name": name, "version": version})
        return text

    def _prompt_version_for(self, name: str) -> int | None:
        """Latest resolved version for a prompt key this turn (M2 span bridge).
        None when the hardcoded fallback was used (registry unreachable)."""
        for entry in reversed(self._turn_prompt_versions):
            if entry.get("name") == name:
                return entry.get("version")
        return None

    def _trace_payload_cap(self) -> int:
        """Per-payload truncation cap (bytes) from system_settings; default 16KB.
        Best-effort - any read failure falls back to the default."""
        from app.services.ai_trace import DEFAULT_MAX_PAYLOAD_BYTES

        try:
            from app.models.user import SystemSetting

            row = self.db.query(SystemSetting).order_by(SystemSetting.id.asc()).first()
            val = getattr(row, "ai_trace_max_payload_bytes", None) if row else None
            return int(val) if val else DEFAULT_MAX_PAYLOAD_BYTES
        except Exception:
            return DEFAULT_MAX_PAYLOAD_BYTES

    def _role_split_enabled(self) -> bool:
        """M2.5: whether the agent loop runs the explicit planner +
        semantic_compressor nodes. Off by default (behavioral opt-in, PLAN Q7).
        Best-effort read of the system_settings singleton."""
        try:
            from app.models.user import SystemSetting

            row = self.db.query(SystemSetting).order_by(SystemSetting.id.asc()).first()
            return bool(getattr(row, "ai_assistant_role_split_enabled", False)) if row else False
        except Exception:
            return False

    def _run_planner(
        self,
        *,
        config: AIAssistantConfig,
        provider: LLMProvider,
        user_message: str,
        standalone_query: str,
        source_context: str,
    ) -> str | None:
        """M2.5 planner node: one LLM call that decomposes the request into an
        ordered tool plan. Returns the plan text (injected into the executor
        context) or None on failure. Own trace span."""
        try:
            system = self._resolve_prompt("planner")
        except Exception:
            return None
        user_block = (
            f"User request: {user_message}\n"
            f"Standalone query: {standalone_query}\n"
            f"Available tools:\n{source_context or 'None'}\n\n"
            "Output the ordered plan of tool steps (or state that no tool is needed)."
        )
        messages_in = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_block},
        ]
        started = time.perf_counter()
        try:
            result = provider.chat(
                messages_in, temperature=0.0, model=config.model, max_tokens=512
            )
            plan = (result.content or "").strip()
            if self._turn_trace is not None:
                self._turn_trace.add_llm_span(
                    name="planner",
                    model=config.model,
                    messages_in=messages_in,
                    result=result,
                    started_perf=started,
                    prompt_name="planner",
                    prompt_version=self._prompt_version_for("planner"),
                    temperature=0.0,
                    max_tokens=512,
                )
            return plan or None
        except Exception as exc:
            logger.warning("M2.5 planner call failed: %s", exc)
            if self._turn_trace is not None:
                self._turn_trace.add_llm_span(
                    name="planner",
                    model=config.model,
                    messages_in=messages_in,
                    result=None,
                    started_perf=started,
                    prompt_name="planner",
                    prompt_version=self._prompt_version_for("planner"),
                    status="error",
                    error=str(exc),
                )
            return None

    def _compress_tool_output(
        self,
        *,
        config: AIAssistantConfig,
        provider: LLMProvider,
        tool_name: str,
        raw_output: str,
    ) -> str:
        """M2.5 semantic_compressor node: raw tool JSON -> token-tight faithful
        sentences (Grafana 4x pattern). Returns the compressed text, or the raw
        output unchanged on failure / when compression is skipped.

        Skipped for ``user_guides_read`` (its markdown carries inline links that
        MUST reach the model verbatim - compressing would drop the clickable
        button links). Also skipped for small outputs where compression can't
        pay for itself. Own trace span when it runs."""
        # Never compress guide markdown - inline links are load-bearing.
        if "user_guides_read" in (tool_name or ""):
            return raw_output
        # Only worth compressing sizeable data payloads.
        if len(raw_output or "") < 400:
            return raw_output
        try:
            system = self._resolve_prompt("semantic_compressor")
        except Exception:
            return raw_output
        messages_in = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Tool: {tool_name}\nRaw result:\n{raw_output[:12000]}"},
        ]
        started = time.perf_counter()
        try:
            result = provider.chat(
                messages_in, temperature=0.0, model=config.model, max_tokens=768
            )
            compressed = (result.content or "").strip()
            if self._turn_trace is not None:
                self._turn_trace.add_llm_span(
                    name=f"semantic_compressor {tool_name}",
                    model=config.model,
                    messages_in=messages_in,
                    result=result,
                    started_perf=started,
                    prompt_name="semantic_compressor",
                    prompt_version=self._prompt_version_for("semantic_compressor"),
                    temperature=0.0,
                    max_tokens=768,
                )
            return compressed or raw_output
        except Exception as exc:
            logger.warning("M2.5 semantic_compressor failed for %s: %s", tool_name, exc)
            if self._turn_trace is not None:
                self._turn_trace.add_llm_span(
                    name=f"semantic_compressor {tool_name}",
                    model=config.model,
                    messages_in=messages_in,
                    result=None,
                    started_perf=started,
                    prompt_name="semantic_compressor",
                    prompt_version=self._prompt_version_for("semantic_compressor"),
                    status="error",
                    error=str(exc),
                )
            return raw_output

    def _finalize_trace(self, assistant_msg: AIAssistantMessage, *, status: str) -> None:
        """Flush the buffered turn trace and link it to the assistant message.
        Best-effort: a trace-write failure must never break a served answer."""
        trace = self._turn_trace
        if trace is None:
            return
        try:
            trace_id = trace.flush(self.db, message_id=str(assistant_msg.id), status=status)
            if trace_id:
                assistant_msg.trace_id = trace_id
                self.db.commit()
        except Exception:
            logger.exception("AI assistant trace finalize failed (best-effort)")
            try:
                self.db.rollback()
            except Exception:
                pass
        finally:
            self._turn_trace = None

    def list_conversations(
        self,
        user_id: str,
        *,
        q: str | None = None,
        limit: int = 100,
    ) -> list[AIAssistantConversation]:
        query = self.db.query(AIAssistantConversation).filter(
            AIAssistantConversation.user_id == user_id
        )
        if q and q.strip():
            like = f"%{q.strip()}%"
            query = query.filter(AIAssistantConversation.title.ilike(like))
        return (
            query.order_by(AIAssistantConversation.updated_at.desc())
            .limit(max(1, min(int(limit or 100), 200)))
            .all()
        )

    def list_mcp_tools(self) -> list[str]:
        """Fetch current tool names from MCP runtime for settings UI sync."""
        mcp = MCPRuntimeClient(settings.ai_assistant_mcp_url, timeout_seconds=settings.ai_assistant_mcp_timeout_seconds)
        tool_names = sorted(mcp.list_tools())
        logger.info("AI assistant MCP tool catalog fetched count=%s", len(tool_names))
        return tool_names

    def get_or_create_conversation(self, user_id: str, conversation_id: str | None, initial_message: str) -> AIAssistantConversation:
        if conversation_id:
            row = (
                self.db.query(AIAssistantConversation)
                .filter(AIAssistantConversation.id == conversation_id, AIAssistantConversation.user_id == user_id)
                .first()
            )
            if row:
                return row
        title = initial_message.strip()[:120] or "New chat"
        row = AIAssistantConversation(user_id=user_id, title=title)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list_messages(self, conversation_id: str, user_id: str) -> list[AIAssistantMessage]:
        conv = (
            self.db.query(AIAssistantConversation)
            .filter(AIAssistantConversation.id == conversation_id, AIAssistantConversation.user_id == user_id)
            .first()
        )
        if not conv:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return (
            self.db.query(AIAssistantMessage)
            .filter(AIAssistantMessage.conversation_id == conversation_id)
            .order_by(AIAssistantMessage.created_at.asc())
            .all()
        )

    def append_message(self, conversation_id: str, role: str, content: str, metadata_json: dict[str, Any] | None = None) -> AIAssistantMessage:
        msg = AIAssistantMessage(conversation_id=conversation_id, role=role, content=content, metadata_json=metadata_json or {})
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)
        return msg

    def respond(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        message: str,
        page_snapshot: PageSnapshotPayload | None = None,
        prompt_overrides: dict[str, str] | None = None,
        dry_run: bool = False,
        confirm_action: str | None = None,
    ) -> tuple[AIAssistantConversation, AIAssistantMessage]:
        request_started = time.perf_counter()
        # Reset per-turn prompt-registry state. Overrides (dry-run) apply for
        # THIS turn only and are never persisted onto a conversation.
        self._turn_prompt_versions = []
        self._prompt_overrides = dict(prompt_overrides or {})
        # M3a write-confirmation gate state (reset per turn). ``_pending_confirmation``
        # is set by the agent loop when it wants to run a write tool. The agent loop
        # ALWAYS gates writes (this stays False for it) - a Confirm click executes the
        # stored call directly via ``_resolve_pending_confirmation``, never by
        # re-running the loop, so a stale/duplicate confirm can never write un-gated.
        self._pending_confirmation: dict[str, Any] | None = None
        self._writes_confirmed = False
        # Dry-run safety: a prompt test must never persist real business data.
        # Even though the throwaway conversation is deleted afterwards, a crafted
        # message (all fields + a CONFIRM keyword) could otherwise drive a
        # `*_submit` / `*_create` MCP tool to write a real complaint/PR/ticket
        # that survives cleanup. Suppress write-capable tools for the turn.
        self._suppress_write_tools = bool(dry_run)
        if not settings.ai_assistant_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI assistant feature is disabled")
        config = self.cfg.get()
        if not config.is_enabled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="AI assistant is disabled")
        logger.info(
            "AI assistant request started user_id=%s conversation_id=%s message_len=%s",
            user_id,
            conversation_id,
            len(message or ""),
        )
        conv = self.get_or_create_conversation(user_id, conversation_id, message)

        # M2: start the per-turn trace. session_id = conversation id (multi-turn
        # grouping). Truncation cap sourced from system_settings (best-effort).
        self._turn_trace = TurnTrace(
            user_id=str(user_id) if user_id else None,
            conversation_id=str(conv.id),
            session_id=str(conv.id),
            env=getattr(settings, "environment", None) or getattr(settings, "app_env", None),
            max_payload_bytes=self._trace_payload_cap(),
        )

        recent_count = (
            self.db.query(AIAssistantMessage.id)
            .filter(
                AIAssistantMessage.conversation_id == conv.id,
                AIAssistantMessage.created_at >= (func.now() - text("interval '1 minute'")),
            )
            .count()
        )
        if recent_count > 20:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded, try again soon.")

        # Persist a truncated copy of the page snapshot on the user message for
        # later debugging / replay.
        user_meta: dict[str, Any] = {}
        if page_snapshot is not None:
            snapshot_meta: dict[str, Any] = {
                "path": (page_snapshot.path or "")[:500],
                "search": (page_snapshot.search or "")[:500],
                "title": (page_snapshot.title or "")[:255],
                "visible_text": (page_snapshot.visible_text or "")[:1000],
            }
            if page_snapshot.entity is not None:
                snapshot_meta["entity"] = {
                    "entity_type": page_snapshot.entity.entity_type,
                    "id": page_snapshot.entity.id,
                }
            user_meta["page_snapshot"] = snapshot_meta

        user_msg = self.append_message(conv.id, "user", message, metadata_json=user_meta or None)
        logger.info("AI assistant user message appended conversation_id=%s", conv.id)

        # M3a write-confirmation resume: the user clicked Confirm/Cancel on a prior
        # pending write. Look up the stored call on the last assistant message and
        # either execute it (confirm) or drop it (cancel). No parse/agent loop.
        if confirm_action in ("confirm", "cancel"):
            pending = self._load_pending_confirmation(conv.id)
            if pending is not None:
                return self._resolve_pending_confirmation(
                    conv=conv,
                    user_id=user_id,
                    config=config,
                    pending=pending,
                    action=confirm_action,
                    request_started=request_started,
                )
            # No pending call found (stale click) → fall through to a normal turn.

        # Item 3b: turn-scoped, strictly-keyed tool/guide result cache. Created
        # fresh per respond() so it can never outlive the turn or leak across
        # users/threads (key includes user_id + conversation_id + turn_id). The
        # turn id is this user message's id - unique per turn.
        turn_cache = _TurnToolCache(
            user_id=user_id,
            conversation_id=str(conv.id),
            turn_id=str(user_msg.id),
        )
        history_rows = (
            self.db.query(AIAssistantMessage)
            .filter(AIAssistantMessage.conversation_id == conv.id)
            .order_by(AIAssistantMessage.created_at.asc())
            .limit(20)
            .all()
        )
        logger.info("AI assistant history loaded conversation_id=%s history_count=%s", conv.id, len(history_rows))
        auth_ctx = self.gov.build_auth_context(user_id)
        if not auth_ctx.permission_slugs:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permissions available for AI assistant.")
        logger.info(
            "AI assistant auth context built user_id=%s permissions=%s",
            user_id,
            len(auth_ctx.permission_slugs),
        )

        # Semantic Parser (M0): ONE schema-forced LLM call understands the turn
        # and emits routing parameters. Replaces reformulator + record-class
        # router + the capability/guide keyword gates.
        parse_started = time.perf_counter()
        parse = self._parse_turn(
            config=config,
            history=history_rows,
            user_message=message,
        )
        standalone_query = (parse.standalone_query or "").strip() or message
        parse_ms = (time.perf_counter() - parse_started) * 1000
        logger.info(
            "AI assistant turn parsed conversation_id=%s intent=%s confidence=%.2f "
            "write=%s domain=%s form_target=%s elapsed_ms=%.1f standalone=%s",
            conv.id,
            parse.intent,
            parse.confidence,
            parse.signals.is_write_intent,
            parse.entities.domain,
            parse.form_target,
            parse_ms,
            (standalone_query or "")[:200],
        )

        # Capability intent → deterministic catalog answer, no answer-LLM. Gated
        # on the confidence floor: unlike the old tight keyword allowlist, the
        # parser is probabilistic, so a low-confidence "capability" guess must NOT
        # hijack a real question with the static catalog - let it fall through to
        # the agent loop (mirrors _route's floor for every other intent).
        if parse.intent == "capability" and parse.confidence >= _LOW_CONFIDENCE_FLOOR:
            return self._serve_capability_answer(
                conv=conv,
                user_id=user_id,
                config=config,
                message=message,
                request_started=request_started,
            )

        # Ideate intent → the in-app WEB brain has NO respond_contacts row / no
        # session_vars, so it CANNOT run the WhatsApp ideation intake (that lives
        # in the external ideation-turn endpoint). It never calls create_idea;
        # it just points the user at the Ideas board. Gated on the confidence
        # floor exactly like capability so a shaky ideate guess falls through to
        # the agent loop instead of hijacking a real CRM question (AC-04/AC-06).
        if parse.intent == "ideate" and parse.confidence >= _LOW_CONFIDENCE_FLOOR:
            return self._serve_ideate_redirect(
                conv=conv,
                user_id=user_id,
                config=config,
                message=message,
                request_started=request_started,
            )

        # M3a Clarifier: when the parser flags the turn as too ambiguous to answer
        # well (a wrong guess would be costly AND ambiguity changes the answer), ask
        # ONE question with enumerable options as chips instead of guessing. Max one
        # round - if the previous assistant turn already clarified, proceed with the
        # best assumption rather than looping.
        if (
            parse.signals.needs_clarification
            and (parse.signals.clarify_question or parse.signals.clarify_options)
            and not self._already_clarified(history_rows)
        ):
            return self._serve_clarify(
                conv=conv,
                user_id=user_id,
                config=config,
                parse=parse,
                request_started=request_started,
            )

        resolve_started = time.perf_counter()
        try:
            resolution = resolve_references(self.db, f"{message}\n{standalone_query or ''}")
        except Exception:
            logger.exception("Entity resolver failed; continuing without resolved references")
            resolution = ResolutionResult(tokens=[], resolutions=[], elapsed_ms=0.0)
        resolve_ms = (time.perf_counter() - resolve_started) * 1000
        if self._turn_trace is not None:
            self._turn_trace.add_span(
                kind=KIND_CHAIN,
                name="entity_resolution",
                input_json={"query": f"{message}\n{standalone_query or ''}"[:2000]},
                output_json=resolution.as_dict(),
                latency_ms=int(resolve_ms),
            )
        logger.info(
            "AI assistant entity resolution conversation_id=%s tokens=%s resolved=%s unresolved=%s elapsed_ms=%.1f",
            conv.id,
            resolution.tokens,
            [r.token for r in resolution.resolutions if r.resolved],
            resolution.unresolved_tokens,
            resolve_ms,
        )

        # Assemble the record context whenever the user is viewing a permitted
        # record - REGARDLESS of the route. The facts are injected into either
        # branch, so a record-ish agent turn is still grounded. RBAC parity with
        # the HTTP route is enforced inline (§3.5) since this is an internal
        # service call without the FastAPI dependency.
        record_ctx = None
        if page_snapshot is not None and page_snapshot.entity is not None:
            from app.services.record_context_service import (
                RecordContextService,
                record_context_view_permission,
            )

            try:
                # Per-entity RBAC parity with the HTTP route: when the entity
                # type requires a view permission, enforce it; None => any
                # authenticated bubble user may read the record context.
                required_slug = record_context_view_permission(
                    page_snapshot.entity.entity_type
                )
                permitted = required_slug is None or UserPermissionService(
                    self.db
                ).check_user_has_permission(user_id, required_slug)
                if permitted:
                    record_ctx = RecordContextService(self.db).assemble(
                        page_snapshot.entity.entity_type,
                        page_snapshot.entity.id,
                    )
            except HTTPException:
                # 400 (unsupported) / 404 (not found) → degrade gracefully.
                record_ctx = None
            except Exception:
                logger.exception(
                    "Record-context assembly failed; falling back to agent loop"
                )
                record_ctx = None

        # Deterministic router - pure switch on the parser's params (no LLM).
        # Replaces the record-class classifier + guide keyword gate.
        route = self._route(parse, record_available=record_ctx is not None)
        if self._turn_trace is not None:
            self._turn_trace.add_span(
                kind=KIND_CHAIN,
                name="route",
                input_json={
                    "intent": parse.intent,
                    "confidence": parse.confidence,
                    "targets_open_record": parse.signals.targets_open_record,
                    "record_available": record_ctx is not None,
                },
                output_json={
                    "kind": route.kind,
                    "is_how_to": route.is_how_to,
                    "skip_rag": route.skip_rag,
                },
            )
        logger.info(
            "AI assistant route decided conversation_id=%s intent=%s kind=%s is_how_to=%s skip_rag=%s",
            conv.id,
            parse.intent,
            route.kind,
            route.is_how_to,
            route.skip_rag,
        )

        # RAG tool selection - only for the agent branch that needs live data
        # (skipped for record_answer, smalltalk, definition).
        selected_tools: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        if route.kind == "agent" and not route.skip_rag:
            # Augment the query so the tool picker sees entity-type hints.
            rag_query = standalone_query
            query_hint = resolution.to_query_hint()
            if query_hint:
                rag_query = f"{standalone_query}\n{query_hint}"
            rag_started = time.perf_counter()
            selected_tools, sources = self._rag_select_tools(
                standalone_query=rag_query,
                enabled_tools=list(config.enabled_tools or []),
                top_k=max(1, int(getattr(settings, "ai_assistant_rag_top_k", 3))),
            )
            if self._suppress_write_tools:
                before = len(selected_tools)
                selected_tools = [t for t in selected_tools if not _is_write_tool(str(t.get("tool_name") or ""))]
                sources = [s for s in sources if not _is_write_tool(str(s.get("title") or ""))]
                if before != len(selected_tools):
                    logger.info(
                        "AI assistant dry-run suppressed %s write tool(s)",
                        before - len(selected_tools),
                    )
            rag_ms = (time.perf_counter() - rag_started) * 1000
            if self._turn_trace is not None:
                self._turn_trace.add_span(
                    kind=KIND_RETRIEVER,
                    name="rag_select_tools",
                    query=rag_query,
                    documents=[
                        {
                            "id": str(s.get("tool_name") or s.get("title") or ""),
                            "content": str(s.get("why_selected") or s.get("title") or ""),
                            "score": s.get("score"),
                        }
                        for s in (sources or [])
                    ],
                    top_k=max(1, int(getattr(settings, "ai_assistant_rag_top_k", 3))),
                    output_json={"selected_tools": [t.get("tool_name") for t in selected_tools]},
                    latency_ms=int(rag_ms),
                )
            logger.info(
                "AI assistant RAG phase finished conversation_id=%s selected_tools=%s elapsed_ms=%.1f",
                conv.id,
                len(selected_tools),
                rag_ms,
            )

        agent_started = time.perf_counter()
        if route.kind == "record_answer":
            response_text, tool_calls, token_usage = self._render_record_answer(
                config=config,
                history=history_rows,
                user_message=message,
                record_ctx=record_ctx,
                page_snapshot=page_snapshot,
                turn_cache=turn_cache,
            )
        else:
            response_text, tool_calls, token_usage = self._run_agent_loop(
                config=config,
                history=history_rows,
                user_message=message,
                standalone_query=standalone_query,
                selected_tools=selected_tools,
                sources=sources,
                resolution=resolution,
                page_snapshot=page_snapshot,
                user_id=user_id,
                conversation_id=str(conv.id),
                user_message_id=str(user_msg.id),
                record_ctx=record_ctx,
                turn_cache=turn_cache,
                is_how_to=route.is_how_to,
            )
        agent_ms = (time.perf_counter() - agent_started) * 1000
        logger.info(
            "AI assistant agent phase finished conversation_id=%s response_len=%s tool_calls=%s "
            "elapsed_ms=%.1f tool_cache_live=%s tool_cache_hits=%s",
            conv.id,
            len(response_text or ""),
            len(tool_calls),
            agent_ms,
            turn_cache.live_calls,
            turn_cache.hits,
        )
        # M3a write-confirmation gate: the agent loop halted because it wanted to
        # run a write tool. Persist the pending call on the assistant message and
        # return a Confirm/Cancel prompt instead of executing anything.
        if self._pending_confirmation is not None:
            return self._serve_pending_confirmation(
                conv=conv,
                config=config,
                pending=self._pending_confirmation,
                request_started=request_started,
            )
        # Post-process: when the agent rephrased a how-to answer and dropped
        # inline markdown links from the guide, re-wrap any bold menu paths
        # with their canonical FE route links. Cheap deterministic safety net
        # so the LLM doesn't have to be perfect about preserving links.
        # `extra_map` adds button-level deep links (e.g. `?guide_target=...`)
        # that the guide author wrote inline.
        guide_link_map = self._extract_guide_link_map(tool_calls)
        response_text = self._inject_route_links(response_text, guide_link_map)
        # Belt-and-suspenders: strip any internal Outline URL the model still
        # managed to emit (the guide tool-result is already redacted upstream,
        # but the model could regurgitate one from history). In-app route /
        # `?guide_target` links are untouched.
        response_text = self._strip_outline_urls(response_text)
        links = self._extract_links_from_text(response_text)

        # Smart suggestions: best-effort follow-up question generation. Always
        # returns a list (possibly empty); never blocks the assistant reply.
        suggestions = self._generate_suggestions(
            config=config,
            history=history_rows,
            user_message=message,
            assistant_reply=response_text,
        )

        # was_answered = reply non-empty AND not a deterministic fallback.
        is_fallback = self._is_fallback_reply(response_text, tool_calls)
        was_answered = bool((response_text or "").strip()) and not is_fallback

        meta = {
            "links": links,
            "sources": sources,
            "selected_tools": selected_tools,
            "tool_calls": [{"tool_name": c.tool_name, "ok": c.ok} for c in tool_calls],
            "entity_resolution": resolution.as_dict(),
            "suggestions": suggestions,
            # UAC C2: one entry per resolver-backed LLM call this turn
            # (reformulator + router + agent_system/synthesizer as applicable).
            # ``version`` is null when the hardcoded fallback was used.
            "prompt_versions": list(self._turn_prompt_versions),
        }
        if page_snapshot is not None:
            assistant_snapshot_meta: dict[str, Any] = {
                "path": (page_snapshot.path or "")[:500],
                "title": (page_snapshot.title or "")[:255],
                "visible_text": (page_snapshot.visible_text or "")[:1000],
            }
            if page_snapshot.entity is not None:
                assistant_snapshot_meta["entity"] = {
                    "entity_type": page_snapshot.entity.entity_type,
                    "id": page_snapshot.entity.id,
                }
            meta["page_snapshot"] = assistant_snapshot_meta
        assistant_msg = self.append_message(conv.id, "assistant", response_text, metadata_json=meta)
        total_ms = (time.perf_counter() - request_started) * 1000

        # Usage logging - best effort, never break the response on telemetry
        # failure.
        try:
            self.db.add(
                AIAssistantUsageLog(
                    user_id=user_id,
                    feature="ai_assistant",
                    conversation_id=str(conv.id),
                    message_id=str(assistant_msg.id),
                    model=config.model,
                    provider=config.provider,
                    prompt_tokens=int(token_usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(token_usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(token_usage.get("total_tokens", 0) or 0),
                    tool_calls_count=len(tool_calls or []),
                    response_time_ms=int(total_ms),
                    was_answered=was_answered,
                )
            )
            self.db.commit()
        except Exception:
            logger.exception("Failed to insert ai_assistant_usage_logs row")
            self.db.rollback()

        # Wishlist tagging when the turn was unanswered.
        if not was_answered:
            try:
                from app.services.ai_wishlist_service import AiWishlistService

                AiWishlistService(self.db).tag_unanswered(str(assistant_msg.id))
            except Exception:
                logger.exception("Failed to tag unanswered query message_id=%s", assistant_msg.id)

        # M2: flush the per-turn trace + link it to the assistant message.
        self._finalize_trace(assistant_msg, status="ok" if was_answered else "error")

        logger.info(
            "AI assistant request completed conversation_id=%s assistant_message_id=%s total_elapsed_ms=%.1f was_answered=%s",
            conv.id,
            assistant_msg.id,
            total_ms,
            was_answered,
        )
        return conv, assistant_msg

    # ---------- n8n-style pipeline: reformulate → RAG → agent loop ----------

    def _parse_turn(
        self,
        *,
        config: AIAssistantConfig,
        history: list[AIAssistantMessage],
        user_message: str,
    ) -> ParseResult:
        """Semantic Parser (M0): the single front-of-pipeline LLM node.

        Understands the latest turn (with a short history window) and emits a
        schema-forced ``ParseResult`` - routing PARAMETERS, not prose. Replaces
        the old reformulator (prose) + record-class router (YES/NO) + the two
        keyword gates. Provider structured-output guarantees valid JSON; on any
        failure we retry once, then degrade to ``fallback_parse`` (intent=unknown
        → agent loop, raw message as the RAG seed). NEVER raises on the hot path.
        """
        raw = (user_message or "").strip()
        if not raw:
            return fallback_parse(raw)
        api_key = resolve_api_key(config, config.provider)
        if not api_key:
            return fallback_parse(raw)

        turns = max(1, int(getattr(settings, "ai_assistant_reformulator_history_turns", 6)))
        convo_lines: list[str] = []
        for msg in history[-(turns * 2):]:
            if msg.role not in {"user", "assistant"}:
                continue
            text_piece = (msg.content or "").strip().replace("\n", " ")
            if not text_piece:
                continue
            convo_lines.append(f"{msg.role}: {text_piece[:500]}")

        system = self._resolve_prompt(
            "semantic_parser", current_date=_current_date_directive()
        )
        user_block = (
            "Conversation so far (may be empty):\n"
            f"{chr(10).join(convo_lines) if convo_lines else '(none)'}\n\n"
            f"Latest user turn:\n{raw}"
        )
        messages_in = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_block},
        ]

        last_exc: Exception | None = None
        result: ChatResult | None = None
        started = time.perf_counter()  # first-attempt clock - reused for the error span
        for _attempt in range(2):
            started = time.perf_counter()
            try:
                provider = get_provider(config.provider, api_key, config.model)
                result = provider.chat(
                    messages_in,
                    temperature=0.0,
                    model=config.model,
                    max_tokens=512,
                    json_schema=PARSE_RESULT_JSON_SCHEMA,
                    json_schema_name=PARSE_RESULT_SCHEMA_NAME,
                )
                # Empty/blank content = a failed structured emission (e.g. an
                # Anthropic max_tokens truncation with no tool_use) → retry rather
                # than silently validating "{}" into a confident intent=unknown.
                if not (result.content or "").strip():
                    raise ValueError("semantic parser returned empty content")
                parsed = ParseResult.model_validate_json(result.content)
                if not (parsed.standalone_query or "").strip():
                    parsed.standalone_query = raw
                # Stash token usage so callers that don't go through the agent
                # loop (the capability short-circuit) can bill the parser call.
                self._parse_token_usage = {
                    "prompt_tokens": int(getattr(result, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(result, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(result, "total_tokens", 0) or 0),
                }
                if self._turn_trace is not None:
                    self._turn_trace.add_llm_span(
                        name="semantic_parser",
                        model=config.model,
                        messages_in=messages_in,
                        result=result,
                        started_perf=started,
                        prompt_name="semantic_parser",
                        prompt_version=self._prompt_version_for("semantic_parser"),
                        temperature=0.0,
                        max_tokens=512,
                    )
                return parsed
            except Exception as exc:  # noqa: BLE001 - parse/provider error, retry then fall back
                last_exc = exc

        logger.warning("Semantic parser failed after retries; degrading to unknown (%s)", last_exc)
        if self._turn_trace is not None:
            self._turn_trace.add_llm_span(
                name="semantic_parser",
                model=config.model,
                messages_in=messages_in,
                result=result,
                started_perf=started,  # real elapsed of the last attempt, not ~0ms
                prompt_name="semantic_parser",
                prompt_version=self._prompt_version_for("semantic_parser"),
                status="error",
                error=str(last_exc),
            )
        return fallback_parse(raw)

    def _route(self, parse: ParseResult, *, record_available: bool) -> "_RouteDecision":
        """Deterministic router - pure switch on the parser's params. No LLM.

        Returns a ``_RouteDecision`` naming which processing branch runs. Low
        confidence on a non-capability intent demotes to the agent loop (the
        safe default, mirroring the old classifier's fail-open posture).
        """
        intent = parse.intent
        if intent != "capability" and parse.confidence < _LOW_CONFIDENCE_FLOOR:
            intent = "unknown"

        if intent == "capability":
            return _RouteDecision(kind="capability")
        # Product-idea / feature-wish turn → dedicated ideation branch (never
        # falls through to record_answer/capability/agent). On WhatsApp this is
        # the external ideation-turn endpoint (Group B); in the in-app web brain
        # respond() short-circuits to a friendly /ideas redirect (AC-06).
        if intent == "ideate":
            return _RouteDecision(kind="ideate")
        if intent == "record_question" and parse.signals.targets_open_record and record_available:
            return _RouteDecision(kind="record_answer")
        if intent == "how_to":
            return _RouteDecision(kind="agent", is_how_to=True)
        if intent == "smalltalk":
            # Greetings/thanks need no data - skip RAG cost. A mislabelled greeting
            # losing tools is harmless. (definition is NOT skipped: a term question
            # mislabelled from a real data lookup, e.g. "what is DO-123?", must
            # still be able to reach MCP read tools + user_guides_read.)
            return _RouteDecision(kind="agent", skip_rag=True)
        # definition / data_query / record_action / form_submit / unknown /
        # record_question without an open record → agent loop with RAG tools.
        return _RouteDecision(kind="agent")

    def _rag_select_tools(
        self,
        *,
        standalone_query: str,
        enabled_tools: list[str],
        top_k: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Top-K MCP tool candidates by embedding similarity on the reformulated query."""
        logger.info(
            "AI assistant RAG started query_len=%s enabled_tools=%s top_k=%s",
            len(standalone_query or ""),
            len(enabled_tools),
            top_k,
        )
        embed_started = time.perf_counter()
        query_embedding = self._embed_query(standalone_query)
        logger.info(
            "AI assistant embedding generated elapsed_ms=%.1f",
            (time.perf_counter() - embed_started) * 1000,
        )

        search_started = time.perf_counter()
        candidates = EmbeddingReadService(self.db).search_tool_candidates(
            query_embedding,
            query=standalone_query,
            top_k=max(top_k, 5),
            include_planned=False,
        )
        logger.info(
            "AI assistant tool candidates fetched count=%s elapsed_ms=%.1f",
            len(candidates),
            (time.perf_counter() - search_started) * 1000,
        )
        if enabled_tools:
            allowed = set(enabled_tools)
            candidates = [c for c in candidates if str(c.get("tool_name")) in allowed]
            logger.info("AI assistant candidates filtered by enabled tools remaining=%s", len(candidates))
        # Deterministic single-tool resolution (K=top_k, default 1): the top-`top_k`
        # candidates are BOUND to the agent; each carries is_current=True.
        selected_tools = candidates[:top_k]
        for c in selected_tools:
            c["is_current"] = True
        logger.info(
            "AI assistant selected tools=%s",
            [
                {
                    "tool_name": c.get("tool_name"),
                    "score": c.get("score"),
                    "is_current": c.get("is_current"),
                    "missing_params": c.get("missing_params") or [],
                    "why_selected": (str(c.get("why_selected") or "")[:120]),
                }
                for c in selected_tools
            ],
        )
        # Sources describe ONLY the bound tool(s) - they are injected into the
        # agent prompt, so listing unbound runners-up here would advertise tools
        # the agent can't call. Each bound source is is_current=True.
        sources = [
            {
                "title": c.get("tool_name"),
                "chunk_text": c.get("chunk_text"),
                "score": c.get("score"),
                "why_selected": c.get("why_selected"),
                "is_current": True,
            }
            for c in selected_tools
        ]
        return selected_tools, sources

    # ---------- Agent loop (OpenAI function calling → MCP tools) ----------

    def _mcp_schema_to_openai_tool(self, name: str, meta: dict[str, Any]) -> dict[str, Any]:
        """Wrap an MCP tool (name, description, inputSchema) as an OpenAI function tool.

        ``contact_id`` / ``space_id`` are MCP-server guard params required for
        n8n/WhatsApp callers but irrelevant for the in-app AI assistant - the
        runtime force-empties them in ``_run_agent_loop`` before calling MCP.
        Surfacing them to the LLM only causes it to ask the user for them
        instead of invoking the tool, so strip them from the schema here.
        """
        schema = meta.get("inputSchema") if isinstance(meta.get("inputSchema"), dict) else {}
        if not schema or not isinstance(schema, dict):
            parameters: dict[str, Any] = {"type": "object", "properties": {}}
        else:
            parameters = dict(schema)
            parameters.setdefault("type", "object")
            parameters.setdefault("properties", {})
        props = dict(parameters.get("properties") or {})
        for guard in ("contact_id", "space_id"):
            props.pop(guard, None)
        parameters["properties"] = props
        required = [r for r in (parameters.get("required") or []) if r not in ("contact_id", "space_id")]
        parameters["required"] = required
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": (meta.get("description") or "")[:4000],
                "parameters": parameters,
            },
        }

    def _serve_capability_answer(
        self,
        *,
        conv: Any,
        user_id: str,
        config: AIAssistantConfig,
        message: str,
        request_started: float,
    ) -> tuple[Any, AIAssistantMessage]:
        """Deterministic capability answer - routed here when the Semantic Parser
        classifies ``intent=capability``. Answered straight from the enriched
        capability catalog: NO answer-LLM round-trip, never hallucinated (the one
        parser call already ran to classify). Persists the assistant message +
        usage log and finalizes the trace, mirroring the main path."""
        capability_text = self._build_capability_answer()
        if self._turn_trace is not None:
            self._turn_trace.add_span(
                kind=KIND_CHAIN,
                name="capability_answer (deterministic)",
                input_json={"message": message[:2000]},
                output_json={"content": capability_text[:2000], "deterministic": True},
            )
        capability_meta: dict[str, Any] = {
            "links": self._extract_links_from_text(capability_text),
            "sources": [],
            "selected_tools": [],
            "tool_calls": [],
            "suggestions": [],
            "deterministic": "capability_overview",
            "prompt_versions": list(self._turn_prompt_versions),
        }
        assistant_msg = self.append_message(
            conv.id, "assistant", capability_text, metadata_json=capability_meta
        )
        total_ms = (time.perf_counter() - request_started) * 1000
        # The answer itself is zero-LLM, but the Semantic Parser call that routed
        # us here IS billed - count its tokens so capability turns don't undercount.
        parse_usage = getattr(self, "_parse_token_usage", None) or {}
        try:
            self.db.add(
                AIAssistantUsageLog(
                    user_id=user_id,
                    feature="ai_assistant",
                    conversation_id=str(conv.id),
                    message_id=str(assistant_msg.id),
                    model=config.model,
                    provider=config.provider,
                    prompt_tokens=int(parse_usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(parse_usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(parse_usage.get("total_tokens", 0) or 0),
                    tool_calls_count=0,
                    response_time_ms=int(total_ms),
                    was_answered=True,
                )
            )
            self.db.commit()
        except Exception:
            logger.exception("Failed to insert ai_assistant_usage_logs row (capability)")
            self.db.rollback()
        self._finalize_trace(assistant_msg, status="ok")
        logger.info(
            "AI assistant capability answer served deterministically "
            "conversation_id=%s assistant_message_id=%s total_elapsed_ms=%.1f",
            conv.id,
            assistant_msg.id,
            total_ms,
        )
        return conv, assistant_msg

    def _serve_ideate_redirect(
        self,
        *,
        conv: Any,
        user_id: str,
        config: AIAssistantConfig,
        message: str,
        request_started: float,
    ) -> tuple[Any, AIAssistantMessage]:
        """Deterministic redirect for an ``ideate`` turn in the in-app WEB brain.

        The web assistant is keyed by ``user_id`` + ``AIAssistantConversation``
        with NO ``respond_contacts`` row and NO ``session_vars`` - the state the
        WhatsApp ideation intake needs (D6/D8). So it MUST NOT call
        ``create_idea`` (proper intake is WhatsApp-first via the external
        ideation-turn endpoint). Instead it points the user at the Ideas board.
        Zero answer-LLM (the parser call already ran); mirrors the capability
        short-circuit's persist / usage-log / trace-finalize (AC-06)."""
        redirect_text = (
            "Love the idea! You can capture and track product ideas on the "
            "Ideas board - open it here: /ideas"
        )
        if self._turn_trace is not None:
            self._turn_trace.add_span(
                kind=KIND_CHAIN,
                name="ideate_redirect (deterministic)",
                input_json={"message": message[:2000]},
                output_json={"content": redirect_text, "deterministic": True},
            )
        ideate_meta: dict[str, Any] = {
            "links": self._extract_links_from_text(redirect_text),
            "sources": [],
            "selected_tools": [],
            "tool_calls": [],
            "suggestions": [],
            "deterministic": "ideate_redirect",
            "prompt_versions": list(self._turn_prompt_versions),
        }
        assistant_msg = self.append_message(
            conv.id, "assistant", redirect_text, metadata_json=ideate_meta
        )
        total_ms = (time.perf_counter() - request_started) * 1000
        # The redirect is zero-LLM, but the Semantic Parser call that routed us
        # here IS billed - count its tokens so ideate turns don't undercount.
        parse_usage = getattr(self, "_parse_token_usage", None) or {}
        try:
            self.db.add(
                AIAssistantUsageLog(
                    user_id=user_id,
                    feature="ai_assistant",
                    conversation_id=str(conv.id),
                    message_id=str(assistant_msg.id),
                    model=config.model,
                    provider=config.provider,
                    prompt_tokens=int(parse_usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(parse_usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(parse_usage.get("total_tokens", 0) or 0),
                    tool_calls_count=0,
                    response_time_ms=int(total_ms),
                    was_answered=True,
                )
            )
            self.db.commit()
        except Exception:
            logger.exception("Failed to insert ai_assistant_usage_logs row (ideate)")
            self.db.rollback()
        self._finalize_trace(assistant_msg, status="ok")
        logger.info(
            "AI assistant ideate redirect served conversation_id=%s "
            "assistant_message_id=%s total_elapsed_ms=%.1f",
            conv.id,
            assistant_msg.id,
            total_ms,
        )
        return conv, assistant_msg

    def _already_clarified(self, history: list[AIAssistantMessage]) -> bool:
        """True if the most recent assistant turn was itself a clarifying question.
        Enforces the one-round cap - after we've already asked, we answer with the
        best assumption rather than looping on ambiguity."""
        for msg in reversed(history):
            if msg.role != "assistant":
                continue
            meta = msg.metadata_json or {}
            return bool(meta.get("clarify"))
        return False

    def _serve_clarify(
        self,
        *,
        conv: Any,
        user_id: str,
        config: AIAssistantConfig,
        parse: ParseResult,
        request_started: float,
    ) -> tuple[Any, AIAssistantMessage]:
        """M3a Clarifier - ask ONE clarifying question instead of guessing. The
        parser already decided (needs_clarification) and produced the question +
        any enumerable options; we just render them. Enumerable options become FE
        chips (metadata.clarify.options); free-form ambiguity is a plain question.
        No agent loop, no data tools. Mirrors the capability short-circuit's
        persist/usage-log/trace-finalize."""
        question = (parse.signals.clarify_question or "").strip() or (
            "Could you give me a bit more detail so I can help accurately?"
        )
        options = [o for o in (parse.signals.clarify_options or []) if str(o).strip()]
        if self._turn_trace is not None:
            self._turn_trace.add_span(
                kind=KIND_CHAIN,
                name="clarify (ask-vs-guess)",
                input_json={"intent": parse.intent, "confidence": parse.confidence},
                output_json={"question": question, "options": options},
            )
        clarify_meta: dict[str, Any] = {
            "links": [],
            "sources": [],
            "selected_tools": [],
            "tool_calls": [],
            "suggestions": [],
            # FE renders these as clickable chips; a click sends the option text as
            # the next user turn. Empty options → plain follow-up question.
            "clarify": {"options": options},
            "prompt_versions": list(self._turn_prompt_versions),
        }
        assistant_msg = self.append_message(
            conv.id, "assistant", question, metadata_json=clarify_meta
        )
        total_ms = (time.perf_counter() - request_started) * 1000
        parse_usage = getattr(self, "_parse_token_usage", None) or {}
        try:
            self.db.add(
                AIAssistantUsageLog(
                    user_id=user_id,
                    feature="ai_assistant",
                    conversation_id=str(conv.id),
                    message_id=str(assistant_msg.id),
                    model=config.model,
                    provider=config.provider,
                    prompt_tokens=int(parse_usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(parse_usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(parse_usage.get("total_tokens", 0) or 0),
                    tool_calls_count=0,
                    response_time_ms=int(total_ms),
                    # A clarifying question is a valid, intentional turn - not a
                    # failure to answer.
                    was_answered=True,
                )
            )
            self.db.commit()
        except Exception:
            logger.exception("Failed to insert ai_assistant_usage_logs row (clarify)")
            self.db.rollback()
        self._finalize_trace(assistant_msg, status="ok")
        logger.info(
            "AI assistant clarifying question served conversation_id=%s options=%s",
            conv.id,
            len(options),
        )
        return conv, assistant_msg

    # ------------------------------------------------------------------ #
    # M3a write-confirmation gate                                         #
    # ------------------------------------------------------------------ #
    _CONFIRM_META_KEY = "pending_confirmation"

    def _log_short_turn_usage(
        self,
        *,
        conv: Any,
        user_id: str,
        config: AIAssistantConfig,
        assistant_msg: AIAssistantMessage,
        request_started: float,
        tool_calls_count: int = 0,
    ) -> None:
        """Usage-log a non-agent turn (clarify/confirm/cancel). Bills only the
        parser tokens spent this turn; best-effort like the clarify path."""
        total_ms = (time.perf_counter() - request_started) * 1000
        parse_usage = getattr(self, "_parse_token_usage", None) or {}
        try:
            self.db.add(
                AIAssistantUsageLog(
                    user_id=user_id,
                    feature="ai_assistant",
                    conversation_id=str(conv.id),
                    message_id=str(assistant_msg.id),
                    model=config.model,
                    provider=config.provider,
                    prompt_tokens=int(parse_usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(parse_usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(parse_usage.get("total_tokens", 0) or 0),
                    tool_calls_count=tool_calls_count,
                    response_time_ms=int(total_ms),
                    was_answered=True,
                )
            )
            self.db.commit()
        except Exception:
            logger.exception("Failed to insert ai_assistant_usage_logs row (short turn)")
            self.db.rollback()

    _WRITE_ACTION_VERBS = (
        "submit", "create", "close", "cancel", "approve", "reject",
        "link", "update", "delete", "add", "send",
    )
    # Real-user permission required to CONFIRM each write tool (checked against the
    # actual logged-in user before dispatch - see _resolve_pending_confirmation).
    # Keep in sync with the wrapped endpoints' own permission deps. Absent tool =
    # no extra permission (e.g. anyone may raise a support ticket).
    _WRITE_TOOL_PERMISSIONS = {
        "crm_complaint_close": "complaint_management.complaints.close",
        # Both hit POST /approval-decision, the approver's decision - so they take the
        # decision permission, NOT the triage one. Leaving these on send_for_approval
        # after the split would let a triage-only user approve via the assistant.
        "crm_purchase_request_approve": "procurement.purchase_requests.approve",
        "crm_purchase_request_reject": "procurement.purchase_requests.approve",
        # crm_order_cancel: no extra permission - `update_order` (the UI cancel
        # path) is gated only by authentication, so requiring more here would make
        # chat stricter than the UI. The Confirm click is the gate.
        #
        # The ideation turn endpoint itself is external-key gated and carries no
        # user permission of its own, so the module's only user-facing slug is the
        # gate: a user who cannot see the Ideas board must not be able to write to
        # it through the assistant. No new slug, so no grant sweep - migration 411
        # already grants this to superadmin and admin.
        "crm_ideation_turn": "ideation.board.view",
    }
    # Args that are plumbing, not user intent - never shown in a confirm summary.
    _WRITE_INTERNAL_ARGS = frozenset({
        "page", "limit", "sort", "dir", "space_id", "contact_id", "message_id",
        "actor_user_id", "source_channel", "source_conversation_id",
        "source_message_id", "payload_json",
    })

    def _summarize_write(self, tool_name: str, args: dict[str, Any]) -> str:
        """Human verb+object+label summary of a pending write, for the confirm
        prompt. Deterministic - never an LLM call. Derives the action verb from the
        tool-name suffix, and a human label from a nested payload_json title (or the
        first meaningful top-level arg), truncated and stripped of plumbing keys."""
        name = tool_name.replace("crm_", "")
        verb = "run"
        for v in self._WRITE_ACTION_VERBS:
            if name.endswith(f"_{v}") or name == v:
                verb = v
                name = name[: -(len(v) + 1)] if name != v else ""
                break
        obj = name.replace("_", " ").strip()

        # Prefer a title/name/subject from a nested payload_json blob.
        payload = args.get("payload_json") if isinstance(args, dict) else None
        pj: dict[str, Any] = {}
        if isinstance(payload, str):
            try:
                pj = json.loads(payload)
            except Exception:
                pj = {}
        elif isinstance(payload, dict):
            pj = payload
        label = ""
        for key in ("title", "name", "subject", "summary"):
            if pj.get(key):
                label = str(pj[key])
                break
        if not label:
            parts = [
                f"{k}={str(v)[:50]}"
                for k, v in (args or {}).items()
                if k not in self._WRITE_INTERNAL_ARGS and v not in (None, "", [])
            ]
            label = ", ".join(parts[:3])
        label = (label[:80] + "…") if len(label) > 80 else label
        head = f"{verb} {obj}".strip() or tool_name
        return head + (f": {label}" if label else "")

    def _serve_pending_confirmation(
        self,
        *,
        conv: Any,
        config: AIAssistantConfig,
        pending: dict[str, Any],
        request_started: float,
    ) -> tuple[Any, AIAssistantMessage]:
        """Render a Confirm/Cancel prompt for a halted write tool. The stored call
        lives in metadata.pending_confirmation; the FE renders two buttons that
        re-POST the same conversation with confirm_action=confirm|cancel. No write
        happens here - only on an explicit later Confirm."""
        summary = pending.get("summary") or "this action"
        question = (
            f"Just to confirm - you want me to **{summary}**?\n\n"
            "This will change data in the system. Click **Confirm** to proceed or "
            "**Cancel** to stop."
        )
        if self._turn_trace is not None:
            self._turn_trace.add_span(
                kind=KIND_GUARDRAIL,
                name="write-confirm prompt",
                input_json={"tool_name": pending.get("tool_name")},
                output_json={"summary": summary},
            )
        meta: dict[str, Any] = {
            "links": [],
            "sources": [],
            "selected_tools": [],
            "tool_calls": [],
            "suggestions": [],
            self._CONFIRM_META_KEY: {
                "tool_name": pending.get("tool_name"),
                "args": pending.get("args") or {},
                "summary": summary,
                "status": "pending",
            },
            "prompt_versions": list(self._turn_prompt_versions),
        }
        assistant_msg = self.append_message(conv.id, "assistant", question, metadata_json=meta)
        self._log_short_turn_usage(
            conv=conv,
            user_id=str(conv.user_id),
            config=config,
            assistant_msg=assistant_msg,
            request_started=request_started,
        )
        self._finalize_trace(assistant_msg, status="ok")
        logger.info(
            "AI assistant write-confirm prompt served conversation_id=%s tool=%s",
            conv.id,
            pending.get("tool_name"),
        )
        return conv, assistant_msg

    def _load_pending_confirmation(self, conversation_id: str) -> dict[str, Any] | None:
        """Find the most recent assistant turn carrying an UNRESOLVED pending write.
        Returns the stored call (+ the message id) or None. Only the latest
        assistant message counts - an older pending that was superseded by a normal
        turn is not resumable."""
        last_assistant = (
            self.db.query(AIAssistantMessage)
            .filter(
                AIAssistantMessage.conversation_id == conversation_id,
                AIAssistantMessage.role == "assistant",
            )
            .order_by(AIAssistantMessage.created_at.desc())
            .first()
        )
        if last_assistant is None:
            return None
        pc = (last_assistant.metadata_json or {}).get(self._CONFIRM_META_KEY)
        if pc and pc.get("status") == "pending":
            return {**pc, "_message_id": str(last_assistant.id)}
        return None

    def _mark_confirmation_resolved(self, message_id: str | None, status: str) -> None:
        """Flip the stored pending call's status so a repeated Confirm/Cancel click
        can't re-fire the write (idempotency backstop for the gate)."""
        if not message_id:
            return
        try:
            msg = (
                self.db.query(AIAssistantMessage)
                .filter(AIAssistantMessage.id == message_id)
                .first()
            )
            if msg is None:
                return
            meta = dict(msg.metadata_json or {})
            pc = dict(meta.get(self._CONFIRM_META_KEY) or {})
            pc["status"] = status
            meta[self._CONFIRM_META_KEY] = pc
            msg.metadata_json = meta
            flag_modified(msg, "metadata_json")
            self.db.commit()
        except Exception:
            logger.exception("Failed to mark write-confirmation resolved message_id=%s", message_id)
            self.db.rollback()

    def _resolve_pending_confirmation(
        self,
        *,
        conv: Any,
        user_id: str,
        config: AIAssistantConfig,
        pending: dict[str, Any],
        action: str,
        request_started: float,
    ) -> tuple[Any, AIAssistantMessage]:
        """Confirm → execute the stored write via MCP and report the result.
        Cancel → drop it. Either way the original prompt is marked resolved first
        so a double-click cannot double-write."""
        tool_name = str(pending.get("tool_name") or "")
        args = pending.get("args") or {}
        summary = pending.get("summary") or tool_name
        # Resolve the original prompt BEFORE doing the write, so a concurrent
        # duplicate click sees status!=pending and no-ops in _load_pending.
        self._mark_confirmation_resolved(pending.get("_message_id"), action)

        if action == "cancel":
            answer = "Okay - cancelled. I won't make that change. Anything else I can help with?"
            if self._turn_trace is not None:
                self._turn_trace.add_span(
                    kind=KIND_GUARDRAIL,
                    name="write-confirm cancelled",
                    input_json={"tool_name": tool_name},
                    output_json={"status": "cancelled"},
                )
            meta: dict[str, Any] = {
                "links": [], "sources": [], "selected_tools": [], "tool_calls": [],
                "suggestions": [],
                self._CONFIRM_META_KEY: {"tool_name": tool_name, "status": "cancelled"},
                "prompt_versions": list(self._turn_prompt_versions),
            }
            assistant_msg = self.append_message(conv.id, "assistant", answer, metadata_json=meta)
            self._log_short_turn_usage(
                conv=conv, user_id=user_id, config=config,
                assistant_msg=assistant_msg, request_started=request_started,
            )
            self._finalize_trace(assistant_msg, status="ok")
            logger.info("AI assistant write cancelled conversation_id=%s tool=%s", conv.id, tool_name)
            return conv, assistant_msg

        # action == "confirm".
        # Real-user RBAC gate (defense-in-depth): the write DISPATCHES through the
        # MCP as the shared act-as principal, so we verify the ACTUAL logged-in user
        # holds the action's permission before executing - a low-privilege assistant
        # user must not be able to trigger an admin-level write via chat.
        required_perm = self._WRITE_TOOL_PERMISSIONS.get(tool_name)
        if required_perm:
            from app.services.user_service import UserPermissionService

            if not UserPermissionService(self.db).check_user_has_permission(user_id, required_perm):
                if self._turn_trace is not None:
                    self._turn_trace.add_span(
                        kind=KIND_GUARDRAIL,
                        name=f"write denied (permission): {tool_name}",
                        input_json={"tool_name": tool_name, "required": required_perm},
                        output_json={"status": "denied"},
                        status="error",
                        error="permission_denied",
                    )
                answer = (
                    f"You don't have permission to **{summary}**. This action needs the "
                    f"`{required_perm}` permission - please ask an administrator, and nothing "
                    "was changed."
                )
                meta = {
                    "links": [], "sources": [], "selected_tools": [], "tool_calls": [],
                    "suggestions": [],
                    self._CONFIRM_META_KEY: {"tool_name": tool_name, "status": "denied"},
                    "prompt_versions": list(self._turn_prompt_versions),
                }
                assistant_msg = self.append_message(conv.id, "assistant", answer, metadata_json=meta)
                self._log_short_turn_usage(
                    conv=conv, user_id=user_id, config=config,
                    assistant_msg=assistant_msg, request_started=request_started,
                )
                self._finalize_trace(assistant_msg, status="ok")
                logger.info(
                    "AI assistant write DENIED (permission) conversation_id=%s tool=%s user=%s perm=%s",
                    conv.id, tool_name, user_id, required_perm,
                )
                return conv, assistant_msg

        # execute the stored call.
        mcp = MCPRuntimeClient(
            settings.ai_assistant_mcp_url,
            timeout_seconds=settings.ai_assistant_mcp_timeout_seconds,
        )
        call_started = time.perf_counter()
        try:
            output = mcp.call_tool(tool_name, args=args)
            is_error, error_summary = self._tool_output_is_error(output)
        except Exception as exc:  # noqa: BLE001
            output, is_error, error_summary = str(exc), True, str(exc)
            logger.exception("AI assistant confirmed write failed tool=%s", tool_name)
        call_ms = (time.perf_counter() - call_started) * 1000

        if self._turn_trace is not None:
            self._turn_trace.add_span(
                kind=KIND_TOOL,
                name=f"confirmed write {tool_name}",
                input_json={"tool_name": tool_name, "args": self._safe_args_for_log(args)},
                output_json={"output": output[:2000]},
                status="error" if is_error else "ok",
                error=error_summary if is_error else None,
                tool_name=tool_name,
                latency_ms=int(call_ms),
            )

        if is_error:
            answer = (
                f"I tried to **{summary}** but it didn't go through: {error_summary}. "
                "Nothing was changed. Want me to try again, or adjust the details?"
            )
        else:
            answer = self._phrase_write_result(summary, output)
        meta = {
            "links": [], "sources": [], "selected_tools": [],
            "tool_calls": [{"tool_name": tool_name, "ok": not is_error}],
            "suggestions": [],
            self._CONFIRM_META_KEY: {
                "tool_name": tool_name,
                "status": "failed" if is_error else "confirmed",
            },
            "prompt_versions": list(self._turn_prompt_versions),
        }
        assistant_msg = self.append_message(conv.id, "assistant", answer, metadata_json=meta)
        self._log_short_turn_usage(
            conv=conv, user_id=user_id, config=config,
            assistant_msg=assistant_msg, request_started=request_started,
            tool_calls_count=1,
        )
        self._finalize_trace(assistant_msg, status="error" if is_error else "ok")
        logger.info(
            "AI assistant confirmed write executed conversation_id=%s tool=%s ok=%s",
            conv.id, tool_name, not is_error,
        )
        return conv, assistant_msg

    def _phrase_write_result(self, summary: str, output: str) -> str:
        """Deterministic success phrasing for a completed write. Surfaces an id /
        reference from the tool output when present, without an LLM call."""
        ref = None
        try:
            data = json.loads(output) if isinstance(output, str) else output
            if isinstance(data, dict):
                for key in ("reference", "ref", "number", "code", "id", "ticket_id", "url", "link"):
                    if data.get(key):
                        ref = data[key]
                        break
        except Exception:
            ref = None
        base = f"Done - I've completed: **{summary}**."
        if ref:
            base += f"\n\nReference: `{ref}`"
        return base

    def _build_capability_answer(self) -> str:
        """Render the deterministic capability overview as markdown.

        Sourced from `build_novice_capability_overview()` (live catalog, enriched
        and admin-stripped) - never hallucinated, no LLM call. Returns a friendly
        fallback string if the catalog cannot be built for any reason.
        """
        from app.services.mcp_tool_capability_service import (
            build_novice_capability_overview,
        )

        try:
            overview = build_novice_capability_overview()
        except Exception:
            logger.exception("Failed to build novice capability overview")
            return (
                "I can help you look up products, stock, incoming shipments, "
                "orders and deliveries, promotions, documents, and forms - and "
                "help you file complaints, stock inquiries, and purchase "
                "requests. Just ask in plain language."
            )

        modules = overview.get("modules") or []
        if not modules:
            return (
                "I can help you find information across the system and submit "
                "requests. Just ask in plain language."
            )

        lines: list[str] = [str(overview.get("intro") or "Here's what I can help you with:"), ""]
        for mod in modules:
            name = str(mod.get("module") or "").strip()
            description = str(mod.get("description") or "").strip()
            lines.append(f"**{name}** - {description}")
            for q in mod.get("example_questions") or []:
                lines.append(f"- Try: \"{q}\"")
            lines.append("")
        closing = str(overview.get("closing") or "").strip()
        if closing:
            lines.append(closing)
        return "\n".join(lines).strip()

    def _cached_tool_call(
        self,
        turn_cache: _TurnToolCache | None,
        client: MCPRuntimeClient,
        tool_name: str,
        args: dict[str, Any],
    ) -> str:
        """Invoke an MCP tool, deduplicated within the turn via ``turn_cache``.

        When ``turn_cache`` is None (e.g. a direct caller that passes no cache),
        the call is made directly - behavior is identical to calling
        ``client.call_tool()``. The cache stores the RAW tool output so any
        downstream transform (e.g. Outline-URL redaction) runs identically on a
        cached or freshly-fetched result.
        """
        if turn_cache is None:
            return client.call_tool(tool_name, args=args)
        return turn_cache.get_or_call(
            tool_name, args, lambda: client.call_tool(tool_name, args=args)
        )

    def _build_uuid_lookup(
        self, resolution: "ResolutionResult | None"
    ) -> dict[str, dict[str, list[str]]]:
        """Index this turn's resolution as ``{entity_type: {norm_key: [uuid, ...]}}``.

        Keys are every human string the resolver knows for a match - its
        ``canonical_code`` plus any string values in ``display`` (e.g. debtor_name,
        debtor_code) - so whichever label the LLM echoed back into a tool arg maps
        to the UUID. A name that matched several rows (the 5 HANLIM accounts) keeps
        ALL their UUIDs, so an ambiguous name expands to every matching id."""
        out: dict[str, dict[str, list[str]]] = {}
        if resolution is None or not getattr(resolution, "resolutions", None):
            return out
        for tr in resolution.resolutions:
            for m in tr.matches:
                if not m.uuid:
                    continue
                bucket = out.setdefault(m.entity_type, {})
                keys = [m.canonical_code or ""]
                keys += [v for v in (m.display or {}).values() if isinstance(v, str)]
                for key in keys:
                    nk = _norm_entity_key(key)
                    if not nk:
                        continue
                    lst = bucket.setdefault(nk, [])
                    if m.uuid not in lst:
                        lst.append(m.uuid)
        return out

    def _coerce_uuid_args(
        self, args: dict[str, Any], resolution: "ResolutionResult | None"
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Substitute resolver UUIDs into UUID-intake params before dispatch.

        For each ``_UUID_PARAM_ENTITY_TYPES`` param present, every non-UUID value is
        looked up in this turn's resolution (``_build_uuid_lookup``); on a miss we
        do ONE focused ``resolve_references`` on that value (reusing the resolve
        endpoint, per the design) filtered to the param's entity type. Values that
        still don't resolve are passed through unchanged so the backend surfaces a
        clear error rather than us silently dropping a filter. Returns the coerced
        args plus a list of substitutions made (for the trace)."""
        if not args:
            return args, []
        turn_map = self._build_uuid_lookup(resolution)
        subs: list[dict[str, Any]] = []
        out = dict(args)
        for param, entity_type in _UUID_PARAM_ENTITY_TYPES.items():
            if param not in out:
                continue
            values = _coerce_arg_to_list(out[param])
            if not values:
                continue
            bucket = turn_map.get(entity_type, {})
            new_vals: list[str] = []
            changed = False
            for v in values:
                sv = v.strip()
                if not sv or _UUID_RE.match(sv):
                    new_vals.append(sv)
                    continue
                uuids = bucket.get(_norm_entity_key(sv))
                if not uuids:
                    uuids = self._resolve_value_to_uuids(sv, entity_type)
                if uuids:
                    new_vals.extend(uuids)
                    changed = True
                    subs.append({"param": param, "value": sv, "resolved_uuids": uuids})
                else:
                    new_vals.append(sv)  # unresolved → let the backend report it
            if changed:
                seen: set[str] = set()
                out[param] = [x for x in new_vals if not (x in seen or seen.add(x))]
        return out, subs

    def _resolve_value_to_uuids(self, value: str, entity_type: str) -> list[str]:
        """Fallback: resolve a single name/code to UUIDs of ``entity_type`` by
        reusing ``resolve_references``. Best-effort - any failure yields [].

        The value is passed as a TOKEN LIST, not as a query string. Handed a string,
        ``resolve_references`` runs `extract_candidate_tokens`, which only keeps code-like
        tokens (a digit AND a letter) plus names that follow a "customer is ..." marker -- so a
        bare person or company name tokenized to NOTHING and this fallback silently returned []
        for every name it was ever asked about. Here the value IS the token: the LLM already
        told us what to look up, there is nothing to extract.

        Embedding fallback is off deliberately. This substitutes a UUID into a filter, so a
        plausible-but-wrong semantic neighbour would answer confidently about the wrong
        salesperson or developer; leaving the value unresolved makes the backend say so.
        """
        try:
            res = resolve_references(
                self.db,
                [value],
                allowed_entity_types=[entity_type],
                enable_embedding_fallback=False,
            )
        except Exception:
            logger.exception("uuid-arg fallback resolve failed value=%s", value[:80])
            return []
        uuids: list[str] = []
        for tr in res.resolutions:
            for m in tr.matches:
                if m.entity_type == entity_type and m.uuid and m.uuid not in uuids:
                    uuids.append(m.uuid)
        return uuids

    def _run_agent_loop(
        self,
        *,
        config: AIAssistantConfig,
        history: list[AIAssistantMessage],
        user_message: str,
        standalone_query: str,
        selected_tools: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        resolution: ResolutionResult | None = None,
        page_snapshot: PageSnapshotPayload | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        user_message_id: str | None = None,
        record_ctx: dict[str, Any] | None = None,
        turn_cache: _TurnToolCache | None = None,
        is_how_to: bool = False,
    ) -> tuple[str, list[MCPToolCallResult], dict[str, int]]:
        """Orchestrator loop: LLM chooses/invokes MCP tools via function-calling, then answers.

        Returns ``(response_text, tool_calls, token_usage_dict)`` where
        ``token_usage_dict`` aggregates ``prompt_tokens`` / ``completion_tokens`` /
        ``total_tokens`` across every provider call made by this turn.
        """
        api_key = resolve_api_key(config, config.provider)
        tool_calls_log: list[MCPToolCallResult] = []
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        mcp = MCPRuntimeClient(
            settings.ai_assistant_mcp_url,
            timeout_seconds=settings.ai_assistant_mcp_timeout_seconds,
        )
        try:
            tool_catalog = mcp.list_tools_with_schema()
        except Exception as exc:
            logger.exception("Failed listing MCP tools")
            err_log = [MCPToolCallResult("mcp_error", False, str(exc))]
            return (
                self._deterministic_fallback(err_log),
                err_log,
                token_usage,
            )
        available = set(tool_catalog.keys())

        bound_tool_names: list[str] = []
        openai_tools: list[dict[str, Any]] = []
        for candidate in selected_tools:
            name = str(candidate.get("tool_name") or "")
            if not name or name not in available:
                continue
            openai_tools.append(self._mcp_schema_to_openai_tool(name, tool_catalog[name]))
            bound_tool_names.append(name)
        # Always make the user guide reader available - the USER GUIDE PROTOCOL
        # requires calling it for any "how do I / how to" question, but RAG does
        # not reliably rank it into the selected set, which left how-to answers
        # without the guide's steps + clickable button deep-links (and risked the
        # agent reaching for an unrelated tool). Bind it unconditionally.
        if "user_guides_read" in available and "user_guides_read" not in bound_tool_names:
            openai_tools.append(
                self._mcp_schema_to_openai_tool("user_guides_read", tool_catalog["user_guides_read"])
            )
            bound_tool_names.append("user_guides_read")
        logger.info(
            "AI assistant agent tools bound count=%s names=%s",
            len(bound_tool_names),
            bound_tool_names,
        )

        if not api_key:
            logger.warning("AI assistant agent has no API key; returning deterministic fallback")
            return self._deterministic_fallback(tool_calls_log), tool_calls_log, token_usage

        try:
            provider: LLMProvider = get_provider(config.provider, api_key, config.model)
        except Exception as exc:
            logger.exception("AI assistant agent failed to instantiate provider")
            err_log = [MCPToolCallResult("provider_error", False, str(exc))]
            return self._deterministic_fallback(err_log), err_log, token_usage

        # System prompt is sourced from the prompt registry (SoT). The deprecated
        # ``config.system_prompt`` column is no longer read (UAC C3). The former
        # ``_default_system_prompt()`` text is the ``agent_system`` fallback; the
        # answer policy (former user-guide protocol) is the ``synthesizer`` key,
        # appended idempotently so it isn't duplicated when a custom agent_system
        # already carries the header.
        system = self._resolve_prompt("agent_system")
        if "USER GUIDE PROTOCOL" not in system:
            system = system.rstrip() + "\n\n" + self._resolve_prompt("synthesizer")
        source_context = "\n".join(
            [f"- {s.get('title')}: {str(s.get('why_selected') or '')[:200]}" for s in sources]
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.append({"role": "system", "content": _current_date_directive()})
        if page_snapshot is not None:
            page_block = (
                "The user is currently viewing this page. Identify what KIND of record this "
                "is from the page title and path (e.g. a goods-received note, stock balance, "
                "promotion, product, form, attachment) and refer to it accurately - do not "
                "assume it is an order or sales document.\n"
                "IMPORTANT: for a question about the VALUE of a field shown below (a status, "
                "quantity, name, date, amount), answer DIRECTLY from the page - do NOT call a "
                "data/catalog tool to look it up. BUT if the user asks how to do something OR "
                "what a button / control / feature does or is for (e.g. 'how do I…', 'how "
                "to…', 'what does the Extend button do', 'what is this for', 'explain …'), you "
                "MUST call `user_guides_read` to ground the answer in the guide and include "
                "its clickable button links - do NOT answer such questions from general "
                "knowledge, even if the button is visible here. For the `query`, use a SHORT "
                "keyword phrase naming the button/feature plus its area (e.g. 'takeover task', "
                "'extend deadline task', 'reassign task', 'download complaint pdf') - NOT a "
                "full sentence; short keyword queries match the guides far better. If the "
                "first lookup returns no match, retry ONCE with just the key noun (e.g. "
                "'takeover').\n\n"
                f"--- Page: {page_snapshot.title} ({page_snapshot.path}{page_snapshot.search}) ---\n"
                f"{page_snapshot.visible_text}\n"
                "--- End page ---"
            )
            messages.append({"role": "system", "content": page_block})
        if record_ctx is not None:
            # The user is on a specific record. Inject its authoritative facts so
            # that even when this fallback path runs (e.g. the question wasn't
            # classified record-class), the model answers from real record data
            # instead of guessing or calling unrelated catalog tools.
            facts = json.dumps(record_ctx, ensure_ascii=False, indent=2)
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Authoritative facts for the record the user is viewing. "
                        "Prefer these over any tool when the question is about this "
                        "record; quote `display_ref`, never a UUID.\n"
                        f"--- Record facts ---\n{facts}\n--- End record facts ---"
                    ),
                }
            )
        # Deterministic guide pre-fetch. The LLM is unreliable about calling
        # user_guides_read for how-to / explain questions - it may skip it, or
        # expand the query into a verbose sentence Outline doesn't match - which
        # left such questions ungrounded and missing the clickable button links.
        # The user's RAW message matches the guides well, so fetch HERE and inject
        # it; the result is added to tool_calls_log so the deep links get
        # re-injected. A miss / error simply injects nothing (harmless).
        # Gated by the Semantic Parser's ``how_to`` intent (was a keyword gate).
        if is_how_to:
            pf_started = time.perf_counter()
            try:
                pf = MCPRuntimeClient(
                    settings.ai_assistant_mcp_url,
                    timeout_seconds=settings.ai_assistant_mcp_timeout_seconds,
                )
                pf_args = {"query": user_message, "contact_id": "", "space_id": ""}
                pf_out = self._cached_tool_call(
                    turn_cache,
                    pf,
                    "user_guides_read",
                    pf_args,
                )
                if self._turn_trace is not None:
                    self._turn_trace.add_tool_span(
                        tool_name="user_guides_read (pre-fetch)",
                        tool_call_id=None,
                        args=pf_args,
                        result_text=pf_out,
                        started_perf=pf_started,
                        ok=True,
                    )
                # Only inject a REAL guide hit. NO_MATCH / OUTLINE_ERROR are not
                # flagged by _tool_output_is_error, and injecting them would tell
                # the model "no guide available" - the opposite of the goal.
                pf_hit = False
                try:
                    pf_payload = json.loads(pf_out)
                    pf_hit = bool(pf_payload.get("id") and pf_payload.get("text")) and not pf_payload.get("code")
                except Exception:
                    pf_hit = False
                if pf_hit:
                    # Redact the internal Outline URL before the guide body ever
                    # reaches the model context, so it cannot be echoed back.
                    pf_clean = self._redact_guide_tool_output(pf_out)
                    tool_calls_log.append(MCPToolCallResult("user_guides_read", True, pf_clean))
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The user is asking how to do something or what a control "
                                "does. Answer from this guide and KEEP its inline markdown "
                                "links EXACTLY as written (they let the user click straight to "
                                "the button). Do NOT say no guide is available. Never mention "
                                "or link to any external documentation URL.\n"
                                f"--- Guide ---\n{pf_clean[:6000]}\n--- End guide ---"
                            ),
                        }
                    )
            except Exception:
                logger.warning("guide pre-fetch failed; agent may still call the tool")
        for msg in history[-6:]:
            if msg.role in {"user", "assistant"} and (msg.content or "").strip():
                messages.append({"role": msg.role, "content": msg.content})
        resolution_block = resolution.to_prompt_block() if resolution else ""
        unresolved_tokens = resolution.unresolved_tokens if resolution else []
        ambiguous_tokens = resolution.ambiguous_tokens if resolution else []
        resolution_section = (
            f"{resolution_block}\n\n" if resolution_block else ""
        )
        extra_rules = ""
        if unresolved_tokens:
            extra_rules += (
                "\nCRITICAL: The following reference codes in the user's message do NOT exist in "
                "the system: "
                + ", ".join(f'"{t}"' for t in unresolved_tokens)
                + ". Do NOT call any tool using these codes. Reply to the user that no record was "
                "found for those codes."
            )
        if ambiguous_tokens:
            extra_rules += (
                "\nCRITICAL: The following reference codes are AMBIGUOUS (multiple candidates): "
                + ", ".join(f'"{t}"' for t in ambiguous_tokens)
                + ". Do NOT call any tool yet. Ask the user to pick one of the candidates listed "
                "in the Ambiguous references block above."
            )
        selected_do_codes: list[str] = []
        if resolution:
            for tr in resolution.resolutions:
                if not tr.resolved:
                    continue
                for match in tr.matches:
                    if str(getattr(match, "entity_type", "")).strip() == "customer_order":
                        code = str(getattr(match, "canonical_code", "")).strip()
                        if code and code not in selected_do_codes:
                            selected_do_codes.append(code)
        if selected_do_codes:
            extra_rules += (
                "\nCRITICAL: Selected order(s) already resolved in context: "
                + ", ".join(f'"{c}"' for c in selected_do_codes)
                + ". In complaint flow, treat these as delivery order number(s) already captured. "
                "Do NOT ask the user to key in delivery order number again unless they ask to change it."
            )
        messages.append(
            {
                "role": "user",
                "content": (
                    f"User question (original):\n{user_message}\n\n"
                    f"Standalone query (reformulated):\n{standalone_query}\n\n"
                    f"{resolution_section}"
                    f"RAG-selected tools (already bound below):\n{source_context or 'None'}\n\n"
                    "Only call a bound tool if you actually need its data. Prefer minimal args "
                    "(pagination only) when the user asks for a general list. When the 'Resolved "
                    "references' block is present, TRUST it: use the canonical_code and entity_type "
                    "to pick the right tool and pass the canonical_code verbatim as the tool "
                    "argument (never invent UUIDs). Answer from tool results; if no tool is useful, "
                    "answer from context without calling tools."
                    + extra_rules
                ),
            }
        )

        max_iters = max(1, int(getattr(settings, "ai_assistant_agent_max_iterations", 6)))
        tool_call_budget = max(1, int(getattr(settings, "ai_assistant_tool_call_limit", 3)))
        tool_calls_made = 0

        # M2.5 role split (opt-in): run an explicit planner node up front and
        # compress raw tool JSON before feeding it back to the executor. Each is
        # its own prompt key + trace span; the default pipeline is unchanged.
        role_split = self._role_split_enabled()
        if role_split:
            plan = self._run_planner(
                config=config,
                provider=provider,
                user_message=user_message,
                standalone_query=standalone_query,
                source_context=source_context,
            )
            if plan:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "A planner has decomposed this request into the following ordered "
                            "tool plan. Follow it unless the tool results tell you otherwise; "
                            "you may stop early once the goal is reached.\n"
                            f"--- Plan ---\n{plan}\n--- End plan ---"
                        ),
                    }
                )

        for iteration in range(max_iters):
            tools_to_pass = openai_tools if (openai_tools and tool_calls_made < tool_call_budget) else None
            round_started = time.perf_counter()
            try:
                result: ChatResult = provider.chat(
                    messages,
                    tools=tools_to_pass,
                    temperature=float(config.temperature or 0),
                    model=config.model,
                    max_tokens=2048,
                )
            except Exception as exc:
                logger.exception("AI assistant agent chat completion failed iteration=%s", iteration)
                if self._turn_trace is not None:
                    self._turn_trace.add_llm_span(
                        name=f"agent_system round {iteration + 1}",
                        model=config.model,
                        messages_in=messages,
                        result=None,
                        started_perf=round_started,
                        prompt_name="agent_system",
                        prompt_version=self._prompt_version_for("agent_system"),
                        status="error",
                        error=str(exc),
                    )
                break

            if self._turn_trace is not None:
                self._turn_trace.add_llm_span(
                    name=f"agent_system round {iteration + 1}",
                    model=config.model,
                    messages_in=messages,
                    result=result,
                    started_perf=round_started,
                    prompt_name="agent_system",
                    prompt_version=self._prompt_version_for("agent_system"),
                    temperature=float(config.temperature or 0),
                    max_tokens=2048,
                )

            # Accumulate token usage across every provider call.
            token_usage["prompt_tokens"] += int(result.prompt_tokens or 0)
            token_usage["completion_tokens"] += int(result.completion_tokens or 0)
            token_usage["total_tokens"] += int(result.total_tokens or 0)

            requested_tool_calls = result.tool_calls or []

            if not requested_tool_calls:
                content = (result.content or "").strip()
                if content:
                    logger.info(
                        "AI assistant agent final answer iteration=%s response_len=%s",
                        iteration,
                        len(content),
                    )
                    return content, tool_calls_log, token_usage
                break

            # Echo the assistant tool-call message back into the running thread
            # using the OpenAI-style schema; the provider abstraction converts
            # this on the wire when needed.
            assistant_tool_calls = [
                {
                    "id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": call.get("name") or "",
                        "arguments": json.dumps(call.get("arguments") or {}),
                    },
                }
                for call in requested_tool_calls
            ]
            assistant_entry: dict[str, Any] = {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": assistant_tool_calls,
            }
            messages.append(assistant_entry)

            for call_idx, call in enumerate(requested_tool_calls):
                call_id = assistant_tool_calls[call_idx]["id"]
                tool_name = str(call.get("name") or "")
                parsed_args = call.get("arguments") or {}
                if not isinstance(parsed_args, dict):
                    parsed_args = {}
                # Deterministically map name/code → UUID for UUID-intake params, so
                # a tool call the LLM built with the entity NAME it was shown does
                # not 400 with INVALID_UUID. Reuses this turn's resolution.
                parsed_args, uuid_subs = self._coerce_uuid_args(parsed_args, resolution)
                if uuid_subs:
                    logger.info(
                        "AI assistant coerced %s name→uuid arg(s) for tool=%s: %s",
                        len(uuid_subs),
                        tool_name,
                        [s["value"] for s in uuid_subs],
                    )
                    if self._turn_trace is not None:
                        self._turn_trace.add_span(
                            kind=KIND_CHAIN,
                            name="resolve_tool_uuids",
                            input_json={"tool_name": tool_name, "substitutions": uuid_subs},
                            output_json={"args": parsed_args},
                            tool_name=tool_name or None,
                        )
                str_args = {
                    k: (v if isinstance(v, str) else json.dumps(v))
                    for k, v in parsed_args.items()
                    if v is not None
                }
                # AI assistant chat has no Respond.io contact context, so any
                # contact_id / space_id the LLM hallucinated as a placeholder
                # string would only mislead the MCP access guard. Force-empty
                # them; the access guard treats empty (contact_id, space_id)
                # as a system call and authorises by tool/agent linkage.
                str_args["contact_id"] = ""
                str_args["space_id"] = ""
                # IT-support intake tool: source_channel + actor_user_id are
                # always known here (we are the in-app AI assistant talking on
                # behalf of the logged-in user). The MCP tool schema only
                # surfaces (contact_id, space_id, payload_json) - flat extras
                # are stripped by FastMCP - so the only place these survive
                # the round trip is inside the payload_json body itself.
                if tool_name == "crm_it_support_ticket_create":
                    raw_payload = str_args.get("payload_json") or "{}"
                    try:
                        payload_obj = json.loads(raw_payload)
                        if not isinstance(payload_obj, dict):
                            payload_obj = {}
                    except Exception:
                        payload_obj = {}
                    payload_obj.setdefault("source_channel", "ai_assistant")
                    payload_obj["actor_user_id"] = str(user_id) if user_id else None
                    if conversation_id:
                        payload_obj["source_conversation_id"] = conversation_id
                    if user_message_id:
                        payload_obj["source_message_id"] = user_message_id
                    str_args["payload_json"] = json.dumps(payload_obj)

                if tool_name not in available:
                    output = json.dumps({"error": "tool_not_available", "tool_name": tool_name})
                    tool_calls_log.append(MCPToolCallResult(tool_name or "unknown", False, output))
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
                    if self._turn_trace is not None:
                        self._turn_trace.add_span(
                            kind=KIND_GUARDRAIL,
                            name=f"denied tool {tool_name or 'unknown'}",
                            input_json={"tool_name": tool_name, "args": str_args},
                            output_json={"error": "tool_not_available"},
                            status="error",
                            error="tool_not_available",
                            tool_name=tool_name or None,
                            tool_call_id=call_id,
                        )
                    continue

                if tool_calls_made >= tool_call_budget:
                    output = json.dumps({"error": "tool_call_budget_exceeded"})
                    tool_calls_log.append(MCPToolCallResult(tool_name, False, output))
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
                    if self._turn_trace is not None:
                        self._turn_trace.add_span(
                            kind=KIND_GUARDRAIL,
                            name=f"denied tool {tool_name} (budget)",
                            input_json={"tool_name": tool_name, "args": str_args},
                            output_json={"error": "tool_call_budget_exceeded"},
                            status="error",
                            error="tool_call_budget_exceeded",
                            tool_name=tool_name,
                            tool_call_id=call_id,
                        )
                    continue

                # M3a write-confirmation gate: a write tool must NOT execute
                # without explicit user confirmation. On first encounter, persist
                # the pending call and halt the whole loop so respond() renders
                # Confirm/Cancel. Suppressed dry-run tools never reach here (they
                # are stripped from the bound tool set), but this is the real
                # enforcement point regardless of RAG binding.
                if _is_write_tool(tool_name) and not self._writes_confirmed:
                    self._pending_confirmation = {
                        "tool_name": tool_name,
                        "args": str_args,
                        "summary": self._summarize_write(tool_name, str_args),
                    }
                    if self._turn_trace is not None:
                        self._turn_trace.add_span(
                            kind=KIND_GUARDRAIL,
                            name=f"write-confirm required: {tool_name}",
                            input_json={"tool_name": tool_name, "args": self._safe_args_for_log(str_args)},
                            output_json={"pending_confirmation": True},
                            tool_name=tool_name,
                            tool_call_id=call_id,
                        )
                    logger.info(
                        "AI assistant halted write tool for confirmation tool_name=%s",
                        tool_name,
                    )
                    return "", tool_calls_log, token_usage

                logger.info(
                    "AI assistant agent calling MCP tool iteration=%s tool_name=%s args=%s",
                    iteration,
                    tool_name,
                    self._safe_args_for_log(str_args),
                )
                call_started = time.perf_counter()
                is_error = False
                try:
                    output = self._cached_tool_call(turn_cache, mcp, tool_name, str_args)
                    if tool_name == "user_guides_read":
                        # Redact the internal Outline URL before the result is
                        # logged or fed back to the model.
                        output = self._redact_guide_tool_output(output)
                    call_ms = (time.perf_counter() - call_started) * 1000
                    is_error, error_summary = self._tool_output_is_error(output)
                    if is_error:
                        logger.warning(
                            "AI assistant agent tool returned error tool_name=%s elapsed_ms=%.1f error=%s",
                            tool_name,
                            call_ms,
                            error_summary,
                        )
                        tool_calls_log.append(MCPToolCallResult(tool_name, False, output[:2000]))
                    else:
                        logger.info(
                            "AI assistant agent tool success tool_name=%s elapsed_ms=%.1f",
                            tool_name,
                            call_ms,
                        )
                        tool_calls_log.append(MCPToolCallResult(tool_name, True, output[:2000]))
                    if self._turn_trace is not None:
                        self._turn_trace.add_tool_span(
                            tool_name=tool_name,
                            tool_call_id=call_id,
                            args=str_args,
                            result_text=output,
                            started_perf=call_started,
                            ok=not is_error,
                            error=error_summary if is_error else None,
                        )
                except Exception as exc:
                    logger.exception("AI assistant agent tool call failed tool_name=%s", tool_name)
                    output = json.dumps({"error": "tool_call_failed", "detail": str(exc)})
                    tool_calls_log.append(MCPToolCallResult(tool_name, False, output[:2000]))
                    if self._turn_trace is not None:
                        self._turn_trace.add_tool_span(
                            tool_name=tool_name,
                            tool_call_id=call_id,
                            args=str_args,
                            result_text=output,
                            started_perf=call_started,
                            ok=False,
                            error=str(exc),
                        )
                tool_calls_made += 1
                # M2.5: compress the raw tool JSON into token-tight sentences
                # before feeding it back to the executor (skips guide markdown +
                # small payloads + error outputs). Own trace span.
                fed_back = output
                if role_split and not is_error:
                    fed_back = self._compress_tool_output(
                        config=config,
                        provider=provider,
                        tool_name=tool_name,
                        raw_output=output,
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": fed_back[:6000],
                    }
                )

        logger.info(
            "AI assistant agent loop ended without explicit stop tool_calls_made=%s",
            tool_calls_made,
        )
        return self._deterministic_fallback(tool_calls_log), tool_calls_log, token_usage

    def _default_system_prompt(self) -> str:
        """DEPRECATED shim. The ReAct core prompt now lives in the prompt
        registry as the ``agent_system`` key (and the answer policy as
        ``synthesizer``); this method is kept only as a DB-unreachable fallback
        equivalent and delegates to the single source in
        ``app.services.ai_prompt_registry``."""
        base = ai_prompt_registry.PROMPT_KEYS["agent_system"].fallback()
        policy = ai_prompt_registry.PROMPT_KEYS["synthesizer"].fallback()
        return base.rstrip() + "\n\n" + policy

    def _user_guide_protocol_addendum(self) -> str:
        """DEPRECATED shim - the answer policy is now the ``synthesizer`` key.
        Delegates to the single source in ``app.services.ai_prompt_registry``."""
        return ai_prompt_registry.PROMPT_KEYS["synthesizer"].fallback()

    def _render_record_answer(
        self,
        *,
        config: AIAssistantConfig,
        history: list[AIAssistantMessage],
        user_message: str,
        record_ctx: dict[str, Any],
        page_snapshot: PageSnapshotPayload | None = None,
        turn_cache: _TurnToolCache | None = None,
    ) -> tuple[str, list[MCPToolCallResult], dict[str, int]]:
        """Render a grounded answer from the deterministic record-context facts.

        For pure fact questions the model answers with NO tool call → returns
        ``tool_calls=[]`` (satisfies UAC §3.1). For procedural / next-step intent
        (UAC A6 fusion) the model is allowed exactly ONE ``user_guides_read``
        call to fetch the per-state procedure, grounded by the record's current
        status. Same return shape as ``_run_agent_loop``.
        """
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        tool_calls_log: list[MCPToolCallResult] = []

        api_key = resolve_api_key(config, config.provider)
        if not api_key:
            return self._deterministic_fallback(tool_calls_log), tool_calls_log, token_usage
        try:
            provider: LLMProvider = get_provider(config.provider, api_key, config.model)
        except Exception:
            logger.exception("record-answer provider instantiation failed")
            return self._deterministic_fallback(tool_calls_log), tool_calls_log, token_usage

        facts_block = json.dumps(record_ctx, ensure_ascii=False, indent=2)
        # Human label for guide queries - snake_case entity_type ("sponsorship_form")
        # matches Outline poorly; the spelled-out form ("sponsorship form") hits.
        _ENTITY_LABELS = {
            "complaint": "complaint",
            "stock_inquiry": "stock inquiry",
            "purchase_request": "purchase request",
            "sponsorship_form": "sponsorship form",
        }
        entity_label = _ENTITY_LABELS.get(
            record_ctx.get("entity_type", ""), record_ctx.get("entity_type", "record")
        )
        cur_status = (record_ctx.get("current_state") or {}).get("status", "current")
        # Registry-sourced system prompt (SoT); ``config.system_prompt`` ignored
        # (UAC C3). agent_system + synthesizer (former user-guide protocol).
        system = self._resolve_prompt("agent_system")
        if "USER GUIDE PROTOCOL" not in system:
            system = system.rstrip() + "\n\n" + self._resolve_prompt("synthesizer")
        system = (
            system.rstrip()
            + "\n\n" + _current_date_directive()
            + "\n\nRECORD CONTEXT (deterministic, authoritative)\n"
            "Answer the user's question using ONLY these record facts. Be concise. Quote "
            "the human-readable `display_ref`, never a UUID. Times are already "
            "Asia/Kuala_Lumpur - present them as-is, do not re-convert. If a fact is null, "
            "say it is not set rather than inventing one.\n"
            "If the user is asking what to do next / which button / how to proceed, OR how "
            "this record's process / lifecycle / stages work, you MAY call "
            "`user_guides_read` EXACTLY ONCE to get the procedure, then ground your answer "
            "in `current_state.status` above. When you do, set `query` to the spelled-out "
            f"record type plus lifecycle/stage so the right guide is found - e.g. "
            f"\"{entity_label} lifecycle what to do at {cur_status} stage\" - NOT the "
            "user's bare words and NOT the snake_case type. For pure fact questions, answer "
            "directly with no tool call.\n\n"
            f"--- Record facts ---\n{facts_block}\n--- End record facts ---"
        )

        # Inject the on-screen page content too. The structured facts above are
        # authoritative for status / decision / SLA / audit / lead-time (often
        # off-screen, e.g. another tab). The visible text carries every other
        # field the user can SEE on the current screen (purpose, dates, line
        # items, addresses, …) so questions about ANY visible field are
        # answerable - without enumerating fields per entity. Facts win on
        # conflict; the page fills the gaps.
        page_text = (getattr(page_snapshot, "visible_text", "") or "").strip()
        if page_text:
            system = (
                system
                + "\n\nThe user is also looking at this screen right now. Use it to "
                "answer questions about any field they can see that is not in the "
                "structured facts above. If the two disagree, trust the structured "
                "facts.\n"
                f"--- Visible screen ---\n{page_text[:6000]}\n--- End visible screen ---"
            )

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for msg in history[-6:]:
            if msg.role in {"user", "assistant"} and (msg.content or "").strip():
                messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_message})

        # Bind ONLY user_guides_read (fusion path); pure-fact answers ignore it.
        mcp = MCPRuntimeClient(
            settings.ai_assistant_mcp_url,
            timeout_seconds=settings.ai_assistant_mcp_timeout_seconds,
        )
        openai_tools: list[dict[str, Any]] = []
        guide_tool_name = "user_guides_read"
        try:
            tool_catalog = mcp.list_tools_with_schema()
            if guide_tool_name in tool_catalog:
                openai_tools.append(
                    self._mcp_schema_to_openai_tool(
                        guide_tool_name, tool_catalog[guide_tool_name]
                    )
                )
        except Exception:
            # Guide tool unavailable → fact-only answering still works.
            logger.warning("record-answer could not list MCP tools; fact-only mode")

        rr_started = time.perf_counter()
        try:
            result: ChatResult = provider.chat(
                messages,
                tools=openai_tools or None,
                temperature=0.0,
                model=config.model,
                max_tokens=2048,
            )
        except Exception as exc:
            logger.exception("record-answer initial provider call failed")
            if self._turn_trace is not None:
                self._turn_trace.add_llm_span(
                    name="record_render (initial)",
                    model=config.model,
                    messages_in=messages,
                    result=None,
                    started_perf=rr_started,
                    prompt_name="agent_system",
                    prompt_version=self._prompt_version_for("agent_system"),
                    status="error",
                    error=str(exc),
                )
            return self._deterministic_fallback(tool_calls_log), tool_calls_log, token_usage
        if self._turn_trace is not None:
            self._turn_trace.add_llm_span(
                name="record_render (initial)",
                model=config.model,
                messages_in=messages,
                result=result,
                started_perf=rr_started,
                prompt_name="agent_system",
                prompt_version=self._prompt_version_for("agent_system"),
                temperature=0.0,
                max_tokens=2048,
            )
        token_usage["prompt_tokens"] += int(result.prompt_tokens or 0)
        token_usage["completion_tokens"] += int(result.completion_tokens or 0)
        token_usage["total_tokens"] += int(result.total_tokens or 0)

        # At most ONE user_guides_read call (A6 fusion). Pure-fact answers come
        # back with no tool_calls → tool_calls_log stays empty (UAC §3.1).
        requested_tool_calls = result.tool_calls or []
        if requested_tool_calls and openai_tools:
            assistant_tool_calls = [
                {
                    "id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": call.get("name") or "",
                        "arguments": json.dumps(call.get("arguments") or {}),
                    },
                }
                for call in requested_tool_calls
            ]
            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": assistant_tool_calls,
                }
            )
            for call_idx, call in enumerate(requested_tool_calls[:1]):
                call_id = assistant_tool_calls[call_idx]["id"]
                name = str(call.get("name") or "")
                parsed_args = call.get("arguments") or {}
                if not isinstance(parsed_args, dict):
                    parsed_args = {}
                str_args = {
                    k: (v if isinstance(v, str) else json.dumps(v))
                    for k, v in parsed_args.items()
                    if v is not None
                }
                str_args["contact_id"] = ""
                str_args["space_id"] = ""
                if name != guide_tool_name:
                    output = json.dumps({"error": "tool_not_allowed", "tool_name": name})
                    tool_calls_log.append(MCPToolCallResult(name or "unknown", False, output))
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
                    continue
                rr_tool_started = time.perf_counter()
                try:
                    output = self._cached_tool_call(turn_cache, mcp, name, str_args)
                    if name == guide_tool_name:
                        # Redact the internal Outline URL before it reaches the
                        # model context.
                        output = self._redact_guide_tool_output(output)
                    is_error, _ = self._tool_output_is_error(output)
                    tool_calls_log.append(MCPToolCallResult(name, not is_error, output))
                    if self._turn_trace is not None:
                        self._turn_trace.add_tool_span(
                            tool_name=name,
                            tool_call_id=call_id,
                            args=str_args,
                            result_text=output,
                            started_perf=rr_tool_started,
                            ok=not is_error,
                        )
                except Exception as exc:
                    output = json.dumps({"error": "tool_call_failed", "detail": str(exc)})
                    tool_calls_log.append(MCPToolCallResult(name, False, output))
                    if self._turn_trace is not None:
                        self._turn_trace.add_tool_span(
                            tool_name=name,
                            tool_call_id=call_id,
                            args=str_args,
                            result_text=output,
                            started_perf=rr_tool_started,
                            ok=False,
                            error=str(exc),
                        )
                messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
            rr_final_started = time.perf_counter()
            try:
                final = provider.chat(
                    messages, temperature=0.0, model=config.model, max_tokens=2048
                )
                token_usage["prompt_tokens"] += int(final.prompt_tokens or 0)
                token_usage["completion_tokens"] += int(final.completion_tokens or 0)
                token_usage["total_tokens"] += int(final.total_tokens or 0)
                answer = (final.content or "").strip()
                if self._turn_trace is not None:
                    self._turn_trace.add_llm_span(
                        name="record_render (synthesis)",
                        model=config.model,
                        messages_in=messages,
                        result=final,
                        started_perf=rr_final_started,
                        prompt_name="synthesizer",
                        prompt_version=self._prompt_version_for("synthesizer"),
                        temperature=0.0,
                        max_tokens=2048,
                    )
            except Exception as exc:
                logger.exception("record-answer follow-up provider call failed")
                answer = (result.content or "").strip()
                if self._turn_trace is not None:
                    self._turn_trace.add_llm_span(
                        name="record_render (synthesis)",
                        model=config.model,
                        messages_in=messages,
                        result=None,
                        started_perf=rr_final_started,
                        prompt_name="synthesizer",
                        prompt_version=self._prompt_version_for("synthesizer"),
                        status="error",
                        error=str(exc),
                    )
        else:
            answer = (result.content or "").strip()

        if not answer:
            answer = self._deterministic_fallback(tool_calls_log)
        return answer, tool_calls_log, token_usage

    def _deterministic_fallback(self, tool_calls: list[MCPToolCallResult]) -> str:
        if not tool_calls:
            return (
                "I could not produce an answer this turn. Please rephrase or provide more specific "
                "identifiers (product code, order number, etc.)."
            )
        lines = ["Here is what I found from MCP tools:"]
        for r in tool_calls:
            prefix = "OK" if r.ok else "FAILED"
            lines.append(f"- [{prefix}] {r.tool_name}: {r.output[:260]}")
        return "\n".join(lines)

    def _tool_output_is_error(self, output: str) -> tuple[bool, str]:
        if not output:
            return True, "empty output"
        snippet = output[:200]
        try:
            payload = json.loads(output)
        except Exception:
            return False, ""
        if isinstance(payload, dict) and payload.get("error"):
            return True, str(payload.get("error"))
        # Some backend routes return {"message": "...", "code": "..."} for failures
        if isinstance(payload, dict) and payload.get("code") in {"INTERNAL_ERROR", "VALIDATION_ERROR", "REQUEST_TIMEOUT"}:
            return True, str(payload.get("message") or payload.get("code"))
        if isinstance(payload, dict) and isinstance(payload.get("detail"), str) and "error" in payload.get("detail", "").lower():
            return True, str(payload.get("detail"))
        return False, snippet

    def _safe_args_for_log(self, args: dict[str, Any]) -> dict[str, Any]:
        redacted = dict(args)
        for key in ("payload_json", "body", "content", "message", "description"):
            if key in redacted:
                value = str(redacted.get(key) or "")
                redacted[key] = value[:120] + ("..." if len(value) > 120 else "")
        return redacted

    def _embed_query(self, query: str) -> list[float]:
        # Embeddings always go via OpenAI: the stored vectors were written by
        # that model, and a vector from another one is not comparable to them
        # whatever its dimensionality. So this builds `OpenAIProvider` below
        # unconditionally, and the key it needs is deliberately NOT read the way
        # the chat call sites read theirs. `api_key_ciphertext` holds the key
        # for whichever provider the assistant is configured on, so a
        # Gemini- or Anthropic-configured assistant would post that vendor's key
        # to OpenAI; a non-OpenAI configuration therefore falls through to the
        # env key, which is the only one known to belong to OpenAI.
        config = self.cfg.get()
        api_key = resolve_api_key(config, "openai")
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Embedding provider not configured",
            )
        try:
            from app.services.llm_provider import OpenAIProvider

            vector = OpenAIProvider(api_key, settings.embedding_model_name).embed(query)
        except Exception as exc:
            logger.exception("AI assistant embedding failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Embedding provider error: {exc}",
            ) from exc
        if not vector:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Embedding provider returned empty vector",
            )
        return vector

    # ---------- Smart suggestions + answered detection ----------

    def _generate_suggestions(
        self,
        *,
        config: AIAssistantConfig,
        history: list[AIAssistantMessage],
        user_message: str,
        assistant_reply: str,
    ) -> list[str]:
        """Mid-conversation follow-up suggestions are intentionally disabled.

        Previously this called the LLM for 5 contextual follow-ups per message.
        It added 1-2s of latency per turn for marginal value, so we now return
        an empty list. Static "starter" suggestions still appear on a new
        conversation via :py:meth:`generate_greeting`.
        """
        return []

    # Static starter pills shown on a fresh conversation. Curated to cover
    # the most common how-to flows backed by user-guides + a couple of
    # general data lookups. Editing this list ships immediately - no LLM
    # call, no per-user personalization, no latency.
    _GREETING_SUGGESTIONS: tuple[str, ...] = (
        "How do I upload a packing list?",
        "How do I submit a stock inquiry from the portal?",
        "How do I send a purchase request for approval?",
        "How do I upload a GRN?",
        "How does the project sales rep get the portal link?",
        "Show me pending stock inquiries",
    )

    def generate_greeting(self, user_id: str) -> dict:
        """Greeting + a static set of starter suggestions.

        Previously this hit the LLM to personalize 5 follow-ups based on the
        user's recent question history. The latency wasn't worth it for a
        first-paint surface, so the suggestions are now hardcoded. To change
        them, edit :py:attr:`_GREETING_SUGGESTIONS`.
        """
        return {
            "greeting": "Hi, how can I help you?",
            "suggestions": list(self._GREETING_SUGGESTIONS),
        }

    def _is_fallback_reply(self, reply: str, tool_calls: list[MCPToolCallResult]) -> bool:
        """True when the assistant reply matches a deterministic fallback string."""
        if not (reply or "").strip():
            return True
        fallback_no_tool = self._deterministic_fallback([])
        if reply.strip() == fallback_no_tool.strip():
            return True
        # The tool-result fallback always starts with this exact sentinel line.
        if reply.startswith("Here is what I found from MCP tools:"):
            return True
        return False

    # Canonical menu-path → FE route map. Mirrors
    # `scripts/annotate_user_guides_routes.py` so the agent's response gets
    # the same hyperlinks the source guide has, even when the LLM paraphrases.
    # Order matters: longer, more-specific paths first.
    _ROUTE_MAP: tuple[tuple[str, str], ...] = (
        ("Resource Management → Files", "/resource-management/attachment-directories"),
        ("Resource Management → Attachment Types", "/resource-management/attachment-types"),
        ("Resource Management → Trash", "/resource-management/trash"),
        ("Procurement → Stock Inquiries", "/procurement-management/stock-inquiries"),
        ("Procurement → Purchase Requests", "/procurement-management/purchase-requests"),
        ("Procurement → Sponsorship Forms", "/procurement-management/sponsorship-forms"),
        ("Procurement → SPO Allocations", "/procurement-management/spo-allocations"),
        ("Procurement → Packing Lists", "/procurement-management/packing-lists"),
        ("Procurement → GRN", "/procurement-management/grn"),
        ("Master Data Management → Products", "/master-data-management/products"),
        ("Delivery Order Management → Delivery Orders", "/order-management/orders"),
        ("Inventory Management → Warehouses", "/inventory-management/warehouses"),
        ("Inventory Management → Stock", "/inventory-management/stock"),
        ("Marketing Management → Promotion Products", "/marketing-management/promotion-products"),
        ("Marketing Management → Promotions", "/marketing-management/promotions"),
        ("Complaint Management → Complaints", "/complaint-management/complaints"),
        ("System Management → Integration Logs", "/system-management/integration-logs"),
        ("System Management → Import Jobs", "/system-management/import-jobs"),
        ("User Management → Contacts", "/user-management/contacts"),
    )

    _GUIDE_LINK_PATTERN = re.compile(r"\[([^\[\]]+?)\]\(([^)\s]+)\)")

    def _extract_guide_link_map(
        self, tool_calls: list[MCPToolCallResult]
    ) -> list[tuple[str, str]]:
        """Scan `user_guides_read` outputs for inline markdown links
        `[Label](URL)` (with or without bold markers) and return label→URL
        pairs. Lets us re-wrap UI-element deep links (e.g.
        `[**Upload**](/resource-management/attachment-directories?guide_target=...)`)
        that the LLM paraphrased away. Static `_ROUTE_MAP` only covers menu
        paths; this extends coverage to whatever the guide author wrote.
        """
        pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for call in tool_calls:
            if not call.ok or not call.output:
                continue
            for raw_label, url in self._GUIDE_LINK_PATTERN.findall(call.output):
                label = raw_label.strip().strip("*").strip()
                if not label or not url:
                    continue
                key = (label, url)
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(key)
        pairs.sort(key=lambda p: -len(p[0]))
        return pairs

    def _inject_route_links(
        self,
        text: str,
        extra_map: list[tuple[str, str]] | None = None,
    ) -> str:
        """Wrap bare bold menu paths (`**Resource Management → Files**`) and
        plain mentions (`Resource Management → Files`) with the canonical FE
        route link. Skips mentions that are already inside a markdown link.

        Also removes any "Full guide: <doc URL>" / "refer to the full guide
        here: ..." trailing lines because the inline links are sufficient
        and the bare doc URLs land on the doc.foundryx.my surface (which
        the user said they don't want).

        `extra_map` adds dynamic label→URL mappings extracted from the active
        tool outputs (see `_extract_guide_link_map`) so guide-authored
        button-level deep links survive paraphrase. Only the bold form is
        re-injected for `extra_map` entries to avoid wrapping common verbs
        like "Upload" in plain prose.
        """
        if not text:
            return text
        result = text
        # Pass replacements as plain-text via a lambda so backslashes / `\g`
        # / `\u` sequences inside the URL don't get parsed as regex
        # back-references in `re.sub`'s replacement template.
        # 1) Wrap bold menu paths (bias the LLM to use bold for menu mentions
        # via the prompt, and we cover both with/without bold below).
        for label, route in self._ROUTE_MAP:
            esc = re.escape(label)
            # `**label**` not already inside `](`/`)`.
            bold_pat = re.compile(
                r"(?<!\]\()(?<!`)\*\*" + esc + r"\*\*(?!\]\()"
            )
            replacement = f"[**{label}**]({route})"
            result = bold_pat.sub(lambda _m, r=replacement: r, result)
        # 2) Wrap PLAIN (non-bold, non-linked) menu paths with bold + link
        # so the LLM's "Resource Management → Files in your CRM" still ends
        # up clickable. Skip mentions already inside markdown link parens.
        for label, route in self._ROUTE_MAP:
            esc = re.escape(label)
            plain_pat = re.compile(
                r"(?<!\*)(?<!\]\()(?<!`)\b" + esc + r"\b(?!\]\()(?!\*)"
            )
            replacement = f"[**{label}**]({route})"
            result = plain_pat.sub(lambda _m, r=replacement: r, result)
        # 2b) Re-inject guide-authored deep links (button targets, etc.).
        # Bold-only - never plain - so a stray "upload" in prose does not
        # become a button deep link.
        if extra_map:
            for label, url in extra_map:
                esc = re.escape(label)
                bold_pat = re.compile(
                    r"(?<!\]\()(?<!`)\*\*" + esc + r"\*\*(?!\]\()"
                )
                replacement = f"[**{label}**]({url})"
                result = bold_pat.sub(lambda _m, r=replacement: r, result)
        # 3) Strip trailing "Full guide" / "refer to the full guide" footers.
        # These are noisy: the inline links cover the same ground and the
        # raw doc.foundryx.my URLs add nothing.
        footer_patterns = [
            re.compile(
                r"\n+For (?:more )?detailed instructions[^\n]*?(?:\n[^\n]*)*$",
                re.IGNORECASE,
            ),
            re.compile(
                r"\n+(?:You can )?(?:refer to|see) the full guide[^\n]*?(?:\n[^\n]*)*$",
                re.IGNORECASE,
            ),
            re.compile(r"\n+Full guide:[^\n]*?(?:\n[^\n]*)*$", re.IGNORECASE),
        ]
        for pat in footer_patterns:
            result = pat.sub("", result)
        return result.rstrip()

    def _extract_links_from_text(self, text: str) -> list[str]:
        if not text:
            return []
        pattern = r"(\/(?:[a-zA-Z0-9\-_]+\/?)+)"
        links = re.findall(pattern, text)
        seen: set[str] = set()
        out: list[str] = []
        for link in links:
            if len(link) < 4:
                continue
            if link in seen:
                continue
            seen.add(link)
            out.append(link)
        return out

    def _outline_host(self) -> str:
        """Host of the INTERNAL Outline knowledge base (e.g.
        ``doc.foundryx.my``), derived from ``settings.outline_base_url`` - never
        hardcoded. Empty string if it cannot be resolved (redaction then no-ops).
        """
        raw = (getattr(settings, "outline_base_url", "") or "").strip()
        if not raw:
            return ""
        try:
            netloc = urlparse(raw).netloc
        except Exception:
            netloc = ""
        if netloc:
            return netloc
        # Fallback for a host-only value (no scheme).
        return raw.replace("https://", "").replace("http://", "").split("/")[0]

    def _strip_outline_urls(self, text: str) -> str:
        """Remove every reference to the internal Outline base URL from a piece
        of text BEFORE it is returned to / persisted for the user.

      - A markdown link pointing at Outline (``[Guide](https://doc.foundryx.my/...)``,
          bold-wrapped or not) collapses to just its (de-bolded) label so the
          sentence still reads.
      - A bare Outline URL (optionally angle-bracketed) is removed outright.

        In-app links (``/resource-management/...``, ``?guide_target=...``) do NOT
        point at the Outline host, so they are left untouched.
        """
        if not text:
            return text
        host = self._outline_host()
        if not host:
            return text
        esc = re.escape(host)
        result = text
        # 1) Markdown link to Outline -> keep the de-bolded label only.
        md_link = re.compile(
            r"\[([^\]]+)\]\(\s*<?https?://" + esc + r"[^)>\s]*>?\s*\)"
        )
        result = md_link.sub(
            lambda m: m.group(1).strip().strip("*").strip(), result
        )
        # 2) Bare Outline URL -> drop it.
        bare = re.compile(r"<?https?://" + esc + r"[^\s)\]>]*>?")
        result = bare.sub("", result)
        # Tidy up residue ("see  ." double spaces, an emptied paren).
        result = re.sub(r"\(\s*\)", "", result)
        result = re.sub(r"[ \t]{2,}", " ", result)
        result = re.sub(r" +([.,;:])", r"\1", result)
        return result.rstrip()

    def _redact_guide_tool_output(self, output: str) -> str:
        """Strip the external Outline URL from a ``user_guides_read`` JSON result
        BEFORE it enters the LLM context, so the model can never echo it
        (the authoritative half of the belt-and-suspenders fix; the final
        answer is also post-filtered via ``_strip_outline_urls``).

        Removes the top-level ``url`` field and any ``alternative_titles[].url``,
        and scrubs the host out of string values (the markdown body, snippets).
        Internal ``?guide_target`` / route links inside the body survive (they
        don't point at the Outline host), so ``_extract_guide_link_map`` still
        re-injects them. ``url_id`` (an internal anchor, not a clickable URL) is
        kept.
        """
        if not output:
            return output
        try:
            payload = json.loads(output)
        except Exception:
            # Not JSON (shouldn't happen for this tool) - plain host strip.
            return self._strip_outline_urls(output)
        if isinstance(payload, dict):
            payload.pop("url", None)
            alts = payload.get("alternative_titles")
            if isinstance(alts, list):
                for alt in alts:
                    if isinstance(alt, dict):
                        alt.pop("url", None)
                        for k, v in list(alt.items()):
                            if isinstance(v, str):
                                alt[k] = self._strip_outline_urls(v)
            for k, v in list(payload.items()):
                if isinstance(v, str):
                    payload[k] = self._strip_outline_urls(v)
            return json.dumps(payload)
        return self._strip_outline_urls(output)
