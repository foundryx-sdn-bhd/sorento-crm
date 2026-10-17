"""L1 replay: re-run recorded parses through THIS checkout's engine and diff the outcome.

usage (cwd = the backend dir of the checkout under test):
  python scripts/chatbot_eval/replay.py run --traces FILE... --out OUT.jsonl
  python scripts/chatbot_eval/replay.py live --items F --recorded F --out F --max-calls N --max-usd X --require-key-last4 XXXX [--prompt-version-id ID] [--chain]
      (--chain: items sharing `conv` run as one conversation in `conv_turn` order. Turn one starts from its recorded
      row, or from empty memory and no history when the item has `start: "empty"`; each later turn starts from the
      memory the previous live turn left (its dry-run session_patch, read as the console reads it) and sees this run's
      own exchanges as its history. A turn with no output turn skips the rest of its conversation ("chain_broken").)
      (--prompt-version-id pins the semantic parser prompt to that stored version on every live turn, replacing any recorded
      override; the output row's provenance then carries prompt_version_id)
      (history is read as of the recorded turn (prod world), not as of now: previous reply, recent exchanges, earlier
      messages, closed-frame summaries, the About this contact block, the quoted turn and the turn number. The
      carried-from-earlier line (engine.py:4108), the fallback history list (engine.py:4013 -> memory.py:381) and the
      handover summary (engine.py:4158 -> memory.py:435) still read the test world as of now; they change only the
      reply text on rare paths.)
      (live spends only when the provider key ends with --require-key-last4; any other key stops the run at "wrong_key")
      (the guard and meter see only this process: MCP tools run on the stack's backend server with its own key, e.g.
      order search embeds via app/api/v1/external/rag.py:36, and media work runs on the RQ worker; neither is metered here)
  python scripts/chatbot_eval/replay.py diff BASE.jsonl HEAD.jsonl [--intended FILE]

Imports only the stdlib at module level and `app.*` inside functions, so one copy of this
file runs under each worktree's own venv. Input rows are recorded `chatbot.turns` rows plus
`case_id`. Output is ids and hashes only: no message or reply text.

The turn runs with `is_test=True` (the envelope's dry-run switch), the recorded parser output as `mock_reformulator_output`,
the recorded memory as `previous_conversation_state`, the MCP tool replaced by the recorded
envelopes, every LLM call blocked, and the database inside a transaction that is rolled back.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import inspect
import json
import logging
import os
import re
import sys
from typing import Any, Callable, Iterator

STAGES = ("grounded", "decision", "tools", "reply")
VOLATILE_KEYS = ("_diagnostics",)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _canon(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def _args_sha(args: Any) -> str:
    return _sha(_canon(args))


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


def _recorded_parse(row: dict[str, Any]) -> dict[str, Any] | None:
    ctx = ((row.get("response") or {}).get("ctx") or {})
    raw = (ctx.get("parse") or {}).get("_parser_raw")
    return raw if isinstance(raw, dict) and raw else None


def _recorded_memory(row: dict[str, Any]) -> dict[str, Any]:
    """The contact memory the recorded turn started from (`received` record), harness shape."""
    from app.services.chatbot import session_state

    for rec in row.get("trace") or []:
        if isinstance(rec, dict) and rec.get("stage") == "received":
            outer = ((rec.get("raw") or {}).get("session_vars") or {})
            sv = outer.get("session_vars") if isinstance(outer.get("session_vars"), dict) else {}
            if any(k in sv for k in session_state.FIVE_KEYS):
                return {k: sv.get(k) for k in session_state.FIVE_KEYS}
            variables = sv.get("variables")
            return variables if isinstance(variables, dict) and variables else {}
    return {}


def _recorded_tools(row: dict[str, Any]) -> dict[tuple[str, str], Any]:
    out: dict[tuple[str, str], Any] = {}
    for rec in row.get("trace") or []:
        if isinstance(rec, dict) and rec.get("kind") == "tool" and rec.get("name"):
            out.setdefault((rec["name"], _args_sha(rec.get("args"))), rec.get("envelope"))
    return out


@contextlib.contextmanager
def _rolled_back(session_factory: Callable[[], Any]) -> Iterator[Callable[[], Any]]:
    """A session factory whose every session shares one outer transaction (inner commits
    become savepoints), rolled back on exit so the database is never written."""
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    probe = session_factory()
    bind = probe.get_bind()
    autoflush = probe.autoflush
    probe.close()
    if isinstance(bind, Engine):
        conn = bind.connect()
        outer = conn.begin()
    else:
        conn = bind
        outer = conn.begin_nested()
    opened: list[Any] = []

    def factory() -> Any:
        s = Session(bind=conn, join_transaction_mode="create_savepoint", autoflush=autoflush)
        opened.append(s)
        return s

    factory._rolled_back = True   # type: ignore[attr-defined]   # _replay_one refuses a factory without this mark
    try:
        yield factory
    finally:
        factory._rolled_back = False   # type: ignore[attr-defined]   # a factory reused after the block is refused
        for s in opened:
            with contextlib.suppress(Exception):
                s.close()
        with contextlib.suppress(Exception):
            outer.rollback()
        if isinstance(bind, Engine):
            conn.close()


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


class NetworkBlocked(OSError):
    pass


def _is_local(host: Any) -> bool:
    if host is None or isinstance(host, (bytes, bytearray)):
        host = host.decode() if host else ""
    return host in _LOCAL_HOSTS or str(host).startswith("127.")


def _guard_network(stack: contextlib.ExitStack, misses: list[dict[str, Any]], allow: tuple[str, ...] = ()) -> None:
    """Refuse every non-local connect and name lookup; localhost, ::1 and unix sockets (the DB) stay open,
    plus the hosts named in `allow` (live mode: the model host)."""
    import socket
    from unittest import mock

    real_connect, real_connect_ex, real_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    resolved: set[str] = set()  # the IPs an allowed host's lookup returned: the only public addresses admitted

    def refuse(host: Any) -> NetworkBlocked:
        misses.append({"network_blocked": str(host)})
        return NetworkBlocked(f"network_blocked: {host}")

    def check(sock: Any, addr: Any) -> None:
        if getattr(sock, "family", None) == getattr(socket, "AF_UNIX", None) or not isinstance(addr, tuple):
            return
        if not _is_local(addr[0]) and str(addr[0]) not in allow and str(addr[0]) not in resolved:
            raise refuse(addr[0])

    def connect(self: Any, addr: Any) -> Any:
        check(self, addr)
        return real_connect(self, addr)

    def connect_ex(self: Any, addr: Any) -> Any:
        check(self, addr)
        return real_connect_ex(self, addr)

    def getaddrinfo(host: Any, *a: Any, **k: Any) -> Any:
        if not _is_local(host) and str(host) not in allow:
            raise refuse(host)
        infos = real_gai(host, *a, **k)
        if str(host) in allow:
            resolved.update(str(info[4][0]) for info in infos)
        return infos

    stack.enter_context(mock.patch.object(socket.socket, "connect", connect))
    stack.enter_context(mock.patch.object(socket.socket, "connect_ex", connect_ex))
    stack.enter_context(mock.patch.object(socket, "getaddrinfo", getaddrinfo))


@contextlib.contextmanager
def _patched(recorded: dict[tuple[str, str], Any], misses: list[dict[str, Any]]) -> Iterator[None]:
    from unittest import mock

    from app.services import embedding_worker, llm_provider
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot import llm_call
    from app.services.chatbot.lanes import ideate

    def call_tool(self: Any, name: str, arguments: dict[str, Any]) -> str:
        key = (name, _args_sha(arguments))
        if key in recorded:
            return json.dumps(recorded[key] if recorded[key] is not None else {})
        misses.append({"tool": name, "args_sha": key[1]})
        return json.dumps({"ok": False, "error": "replay_miss", "items": []})

    def blocked(label: str) -> Callable[..., Any]:
        def fail(*a: Any, **k: Any) -> Any:
            misses.append({"llm_blocked": label})
            raise RuntimeError(f"llm_blocked: {label}")

        return fail

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(MCPRuntimeClient, "call_tool", call_tool))
        _guard_network(stack, misses)
        stack.enter_context(mock.patch.object(llm_call, "chat", blocked("llm_call.chat")))
        stack.enter_context(mock.patch.object(ideate, "call_ideation_tool", blocked("ideate")))
        stack.enter_context(mock.patch.object(embedding_worker, "_embed_text_chunks", blocked("embedding")))
        # Every provider's `chat` (spec-phrase understanding and the code reader call it through
        # `get_provider()`, not through `llm_call`): a replay never spends or depends on a live model.
        for name, cls in vars(llm_provider).items():
            if inspect.isclass(cls) and cls is not llm_provider.LLMProvider and name.endswith("Provider") and "chat" in vars(cls):
                stack.enter_context(mock.patch.object(cls, "chat", blocked(f"provider.{name}")))
        yield


def _entity_key(ents: list[Any]) -> list[list[Any]]:
    keys = [
        [e.get("hint"), e.get("canonical_code"), _sha(str(e.get("raw") or "").lower())[:12]]
        for e in ents
        if isinstance(e, dict)
    ]
    return sorted(keys, key=lambda k: tuple("" if x is None else str(x) for x in k))


def _entities(trace: list[dict[str, Any]]) -> list[list[Any]]:
    for rec in trace:
        if isinstance(rec, dict) and rec.get("kind") == "apply":
            return _entity_key((rec.get("verdict") or {}).get("entities") or [])
    return []


def _message_first_line(envelope: dict[str, Any]) -> str | None:
    m: Any = envelope
    for _ in range(6):
        if isinstance(m, dict) and isinstance(m.get("text"), str):
            return m["text"].split("\n", 1)[0]
        m = m.get("message") if isinstance(m, dict) else None
    return None


def _ground_one(row: dict[str, Any], parse: dict[str, Any], db: Any, vocab: Any) -> list[list[Any]]:
    """`grounding.ground()` alone, the way the engine calls it (held ranking words go back on after)."""
    from app.services.chatbot.head import grounding
    from app.services.chatbot.lanes.business import report_ask

    verdict, held = report_ask.hold_words(copy.deepcopy(parse))
    grounded, _notes = grounding.ground(db, verdict, vocab=vocab, message=_message_first_line(row.get("envelope") or {}))
    ents = grounded.get("entities") or []
    if held:
        ents = [*ents, *held]
    return _entity_key(ents)


def _decision(trace: list[dict[str, Any]], branch_kind: Any) -> list[Any]:
    lane = None
    plan: dict[str, Any] = {}
    for rec in trace:
        if not isinstance(rec, dict):
            continue
        if rec.get("stage") == "routed":
            lane = (rec.get("facts") or {}).get("lane")
        if rec.get("kind") == "apply":
            plan = rec.get("plan") or {}
    return [branch_kind, lane, plan.get("lane"), sorted(plan.get("fetch") or []), sorted(plan.get("denied") or [])]


def _tools(trace: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [rec["name"], _args_sha(rec.get("args"))]
        for rec in trace
        if isinstance(rec, dict) and rec.get("kind") == "tool" and rec.get("name")
    ]


def _require_rolled_back(factory: Callable[[], Any]) -> None:
    """Refuse, before any DB use, a factory that `_rolled_back` did not yield (or has since closed)."""
    if not getattr(factory, "_rolled_back", False):
        raise RuntimeError("a replay needs a factory from _rolled_back; call replay_rows or live_rows")


def _replay_one(row: dict[str, Any], factory: Callable[[], Any], stage: str | None = None,
                vocab_box: list[Any] | None = None) -> dict[str, Any]:
    _require_rolled_back(factory)
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import engine
    from app.services.chatbot.contracts import Envelope

    out: dict[str, Any] = {"case_id": row.get("case_id"), "turn_id": str(row.get("id")), "skipped": None,
                           "misses": [], "stages": {}}
    parse = _recorded_parse(row)
    if parse is None:
        out["skipped"] = "no_parse"
        return out
    from app.services.chatbot.head import parser as parser_mod

    parse = copy.deepcopy(parse)
    # A recorded parse written before a key joined the schema lacks it; the bypass refuses
    # an incomplete emission, so the missing declared keys are set to None (never invented).
    for key in parser_mod.DECLARED_KEYS - parser_mod.TOLERATED_ABSENT - set(parse):
        parse[key] = None
    if stage == "grounding":
        from app.services.chatbot.head import grounding

        db = factory()
        g_misses: list[dict[str, Any]] = []
        try:
            with _patched({}, g_misses):
                if vocab_box is not None and not vocab_box:
                    vocab_box.append(grounding.load_vocabulary(db))
                vocab = vocab_box[0] if vocab_box else None
                out["stages"] = {"grounded": _ground_one(row, parse, db, vocab)}
        except Exception as exc:  # noqa: BLE001 - the replay records, never raises
            g_misses.append({"error": type(exc).__name__})
        finally:
            db.rollback()
            db.close()
        out["misses"] = g_misses
        return out
    payload = copy.deepcopy(row.get("envelope") or {})
    payload.update(is_test=True, mock_reformulator_output=parse,
                   previous_conversation_state=_recorded_memory(row))
    # A fresh respond message id (deterministic per row, so base and head agree): the engine's
    # duplicate check is keyed on contact + message id and must never match the recorded turn.
    inner = payload.setdefault("message", {}) if isinstance(payload.get("message", {}), dict) else None
    if inner is not None and isinstance(inner.setdefault("message", {}), dict):
        inner["message"]["messageId"] = "replay-" + hashlib.sha256(str(row.get("id")).encode()).hexdigest()[:16]
    envelope = Envelope(**payload)
    misses: list[dict[str, Any]] = []
    with _patched(_recorded_tools(row), misses):
        try:
            result = engine.run_turn(envelope, session_factory=factory, offload=False)
        except Exception as exc:  # noqa: BLE001 - the replay records, never raises
            out["misses"] = misses + [{"error": type(exc).__name__}]
            return out
    if getattr(result, "duplicate", False):
        out["skipped"] = "duplicate"  # never read the recorded trace as if it had been replayed
        out["misses"] = misses
        return out
    db = factory()
    turn = db.query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    trace = list(turn.trace or []) if turn is not None else []
    db.close()
    out["misses"] = misses
    if getattr(result, "status", None) == "failed":   # the engine closed the turn as failed: a type and a stage, never text
        misses.append({"error": "failed", "stage": getattr(result, "stage", None)})
    out["stages"] = {
        "grounded": _entities(trace),
        "decision": _decision(trace, result.branch_kind),
        "tools": _tools(trace),
    }
    if not any("tool" in m for m in misses):
        text = (result.reply or {}).get("text") if isinstance(result.reply, dict) else None
        out["stages"]["reply"] = {"sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
                                  "branch_kind": result.branch_kind}
    return out


def replay_rows(rows: list[dict[str, Any]], *, session_factory: Callable[[], Any],
                stage: str | None = None, on_row: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    """`stage="grounding"` runs only `grounding.ground()` per row (vocabulary loaded once per call)
    and returns `stages.grounded` alone; None runs the whole turn."""
    results = []
    vocab_box: list[Any] = []
    # No log record may carry message or reply text (the engine and resolver log both on errors).
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        for row in rows:
            with _rolled_back(session_factory) as factory:
                results.append(_replay_one(row, factory, stage, vocab_box))
            if on_row is not None:
                on_row(results[-1])
    finally:
        logging.disable(previous_disable)
    return results


# --------------------------------------------------------------------------- #
# Live mode: the recorded prod state with the item's own message, parser and model live
# --------------------------------------------------------------------------- #

MODEL_HOSTS = ("api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com")
# MCP tools that write or send: low stock report (reorder run + download row + jobs), portal link (persists a token),
# ticket create, complaint close, order cancel, purchase request approve / reject, and the ideation turn (a model call
# outside the metered providers). A dry-run turn must never reach them.
SIDE_EFFECT_TOOLS = frozenset({
    "crm_low_stock_report", "crm_portal_link_get", "crm_it_support_ticket_create", "crm_complaint_close",
    "crm_sales_analysis", "crm_order_cancel", "crm_purchase_request_approve", "crm_purchase_request_reject", "crm_ideation_turn",
})
PROVENANCE_KEYS = ("code_sha", "prompt_sha256", "prompt_version", "model", "reasoning_effort")
PARSER_PROMPT_KEY = "chatbot_semantic_parser"  # console_service.PARSER_PROMPT_KEY, literal to skip the import


class _CapStop(BaseException):
    """A spend or call cap reached INSIDE an item: not an Exception, so the engine's own `except Exception`
    handlers cannot swallow it and turn it into a failed parse."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _check_caps(total: dict[str, Any]) -> None:
    """Before every model call: an unpriced model already seen, a call cap or a spend cap ends the item cleanly."""
    if total.get("stop"):
        raise _CapStop(total["stop"])
    if total.get("max_calls") is not None and total["calls"] >= total["max_calls"]:
        raise _CapStop("max_calls")
    if total.get("max_usd") is not None and total["usd"] >= total["max_usd"]:
        raise _CapStop("max_usd")


def _valid_last4(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 4 and not any(c.isspace() for c in value)


def _last4_arg(value: str) -> str:
    if not _valid_last4(value):
        raise argparse.ArgumentTypeError("must be exactly 4 non-blank characters")
    return value


def _check_key(total: dict[str, Any], key: Any) -> None:
    """Before every real call: only the key whose last four characters were required may spend. The key itself
    is never stored or printed."""
    want = total.get("require_key_last4")
    if not _valid_last4(want) or not str(key or "").endswith(want):
        total["stop"] = "wrong_key"
        raise _CapStop("wrong_key")


@contextlib.contextmanager
def _metered(usage: dict[str, Any], total: dict[str, Any]) -> Iterator[None]:
    """Pass-through wrapper on every provider `chat`: checks the caps BEFORE the call, then counts calls and tokens
    and prices them (`LLM_MODEL_OVERRIDE` wins over the `model` kwarg, as in the engine). A model with no price row
    is counted at 0 and stops the run (`total["stop"]`) after the item."""
    from unittest import mock

    from app.services import llm_provider
    from scripts.chatbot_eval.budget import PRICES, cost_usd

    def wrap(original: Callable[..., Any]) -> Callable[..., Any]:
        def chat(self: Any, *args: Any, **kwargs: Any) -> Any:
            _check_caps(total)
            _check_key(total, getattr(self, "api_key", ""))
            res = original(self, *args, **kwargs)
            prompt = int(getattr(res, "prompt_tokens", 0) or 0)
            cached = int(getattr(res, "cached_tokens", 0) or 0)
            out = int(getattr(res, "completion_tokens", 0) or 0)
            model = os.environ.get("LLM_MODEL_OVERRIDE") or kwargs.get("model") or getattr(self, "default_model", None) \
                or "gpt-5.4-mini"
            try:
                usd = max(0.0, cost_usd({"prompt": prompt, "cached": cached, "output": out}, model))
            except KeyError:
                usd = 0.0
                total["stop"] = "unpriced_model"
            for bucket in (usage, total):
                bucket["calls"] += 1
                bucket["prompt_tokens"] += prompt
                bucket["cached_tokens"] += cached
                bucket["completion_tokens"] += out
                bucket["usd"] += usd
            return res

        return chat

    def wrap_embed(original: Callable[..., Any]) -> Callable[..., Any]:
        def embed(chunks: Any) -> Any:  # counted, never capped: a query embedding is tiny next to a chat call
            from app.config import settings

            if getattr(settings, "openai_api_key", ""):  # empty: the original raises before any request goes out
                _check_key(total, settings.openai_api_key)
            res = original(chunks)

            tokens = sum(len(str(c)) for c in chunks) // 4  # an estimate: the endpoint's own count is not returned
            model = str(getattr(settings, "embedding_model_name", ""))
            usd = max(0.0, cost_usd({"prompt": tokens, "cached": 0, "output": 0}, model)) if model in PRICES else 0.0
            for bucket in (usage, total):
                bucket["embed_calls"] += 1  # its own bucket: never consumes max_calls
                bucket["prompt_tokens"] += tokens
                bucket["usd"] += usd
            return res

        return embed

    from app.services import embedding_worker

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(embedding_worker, "_embed_text_chunks",
                                              wrap_embed(embedding_worker._embed_text_chunks)))
        for name, cls in vars(llm_provider).items():
            if inspect.isclass(cls) and cls is not llm_provider.LLMProvider and name.endswith("Provider") and "chat" in vars(cls):
                stack.enter_context(mock.patch.object(cls, "chat", wrap(cls.chat)))
        yield


@contextlib.contextmanager
def _tool_capture(into: list[dict[str, Any]], misses: list[dict[str, Any]] | None = None) -> Iterator[None]:
    """Pass-through on `MCPRuntimeClient.call_tool`: records name, args and the FULL result (no cap). A tool with a
    side effect is refused (recorded in `misses`) and never reaches the client."""
    from unittest import mock

    from app.services.ai_assistant_service import MCPRuntimeClient

    original = MCPRuntimeClient.call_tool

    def call_tool(self: Any, name: str, arguments: dict[str, Any]) -> Any:
        if name in SIDE_EFFECT_TOOLS:
            if misses is not None:
                misses.append({"blocked_tool": name})
            raise RuntimeError(f"blocked_tool: {name}")
        result = original(self, name, arguments)
        into.append({"name": name, "args": copy.deepcopy(dict(arguments)), "result": result})
        return result

    with mock.patch.object(MCPRuntimeClient, "call_tool", call_tool):
        yield


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _uuid_codes(db: Any, args_list: list[Any]) -> dict[str, str]:
    """product uuid -> product_code for the uuids in the tool args (read-only, inside the rolled-back session)."""
    from sqlalchemy import text

    ids = sorted({u.lower() for a in args_list for u in _UUID_RE.findall(json.dumps(a, default=str))})
    if not ids:
        return {}
    rows = db.execute(text("select id::text, product_code from products where id::text = any(:ids)"), {"ids": ids})
    return {r[0]: r[1] for r in rows}


def _new_usage() -> dict[str, Any]:
    return {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "usd": 0.0, "embed_calls": 0}


def _set_text(payload: dict[str, Any], text: str) -> bool:
    node: Any = payload
    for _ in range(8):
        if isinstance(node, dict) and isinstance(node.get("text"), str):
            node["text"] = text
            return True
        node = node.get("message") if isinstance(node, dict) else None
    return False


def _trace_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in row.get("trace") or [] if isinstance(x, dict)]


_L3_STARTS = ("Earlier in this conversation", "Recent exchanges", "Previous response")
# the first line of the layers after L3: L2 (brands and kinds, subject, tasks, question, pending) and L1
_AFTER_L3 = ("Known brands:", "Product types:", "Product details:", "Promotion kinds:", "Current subject:", "Open task:",
             "Open question", "Pending:", "reply to:", "Current user message:")


def _recorded_l3(row: dict[str, Any]) -> tuple[str, bool] | None:
    """The recorded prompt's L3 block verbatim and the recorded L3 `dropped` flag; None without a `prompt_text` event."""
    events = _trace_events(row)
    prompt = next((e for e in events if e.get("kind") == "prompt_text" and isinstance(e.get("text"), str)), None)
    if prompt is None:
        return None
    lines = prompt["text"].split("\n")
    start = next((i for i, x in enumerate(lines) if x.startswith(_L3_STARTS)), None)
    if start is None:
        return "", False
    end = next((i for i in range(start, len(lines)) if lines[i].startswith(_AFTER_L3)), len(lines))
    if any(x.startswith(_L3_STARTS) for x in lines[end:]):
        return None  # an old layout (L3 lines after L2/L1): the as-of DB history serves instead
    dropped = False
    for e in events:
        layers = e.get("layers") if isinstance(e.get("layers"), list) else []
        for layer in layers:
            if isinstance(layer, dict) and layer.get("layer") == "L3":
                dropped = bool(layer.get("dropped"))
    return "\n".join(lines[start:end]), dropped


def _recorded_history(db: Any, row: dict[str, Any], envelope: Any) -> dict[str, Any] | None:
    """The prod history the recorded turn saw, read as of that turn: the engine's own history reads are scoped to the
    live world (`is_test`) and the newest rows as of now, so a live (dry-run) turn would see none of it, or later turns.
    Anchor = the recorded turn's `created_at` (else the `at` of its `prompt_text` event). Read-only. None = no anchor."""
    from datetime import datetime

    from sqlalchemy import func

    from app.models.chatbot_turn import ChatbotTurn
    from app.models.conversation_frame import ConversationFrame
    from app.services.chatbot import engine, turn_runtime
    from app.services.chatbot.turn import memory as memory_mod

    events = _trace_events(row)
    prompt = next((e for e in events if e.get("kind") == "prompt_text" and isinstance(e.get("text"), str)), None)
    recorded = db.query(ChatbotTurn.created_at, ChatbotTurn.contact_respond_id).filter(ChatbotTurn.id == row.get("id")).first()
    anchor, contact = (recorded[0], recorded[1]) if recorded else (None, None)
    if anchor is None and prompt is not None and prompt.get("at"):
        anchor = datetime.fromisoformat(str(prompt["at"]))
    if anchor is None:
        return None
    contact = contact or engine._contact_respond_id(envelope)
    console = str(getattr(envelope, "ingress", "") or "") == turn_runtime._CONSOLE_INGRESS

    def prior(*cols: Any) -> Any:
        q = db.query(*cols).filter(ChatbotTurn.contact_respond_id == str(contact), ChatbotTurn.is_test.is_(False),
                                   ChatbotTurn.created_at < anchor, ChatbotTurn.id != row.get("id"))
        return q

    def scoped(q: Any) -> Any:
        q = q.filter(ChatbotTurn.status == "done")
        return q.filter(ChatbotTurn.ingress == "console") if console else q.filter(ChatbotTurn.ingress != "console")

    order = turn_runtime._newest_turn_first(ChatbotTurn)
    first = scoped(prior(ChatbotTurn.response)).order_by(*order).first()
    reply = (first[0] or {}).get("reply") if first is not None and isinstance(first[0], dict) else None
    previous_reply = str(reply.get("text")) if isinstance(reply, dict) and reply.get("text") else None
    pairs: list[tuple[str, str]] = []
    for env, resp in scoped(prior(ChatbotTurn.envelope, ChatbotTurn.response)).order_by(*order).limit(6).all():
        rep = resp.get("reply") if isinstance(resp, dict) else None
        answer = rep.get("text") if isinstance(rep, dict) else None
        if isinstance(answer, str) and answer.strip():
            pairs.append((turn_runtime._envelope_text(env) or "(media)", answer))
            if len(pairs) == 3:
                break
    recent = list(reversed(pairs))

    # the closed-frame summaries are the recorded "Recent conversations" lines (oldest first in the prompt, newest first here)
    lines: list[str] = []
    if prompt is not None:
        block = prompt["text"].split("Recent conversations:", 1)
        if len(block) == 2:
            for line in block[1].split("\n")[1:]:
                if not line.startswith("- "):
                    break
                lines.append(line[2:])
    summaries = list(reversed(lines))
    mem = next((e for e in events if isinstance(e.get("episodes"), dict)), {})
    read_ids = [i for i in (mem.get("episodes") or {}).get("read") or [] if i is not None]
    read_frames = [{"id": i} for i in read_ids]

    frame = db.query(ConversationFrame).filter(ConversationFrame.id.in_(read_ids)).order_by(
        ConversationFrame.last_activity_at.desc()).first() if read_ids else None
    earlier_q = prior(ChatbotTurn)  # the engine's rule: after the newest closed frame, newest 3, oldest first
    if console:
        earlier_q = earlier_q.filter(ChatbotTurn.ingress == "console")
    if frame is not None:
        earlier_q = earlier_q.filter(ChatbotTurn.created_at > frame.last_activity_at)
    earlier = [{"created_at": engine._short_day_time(x.created_at), "text": t}
               for x in reversed(earlier_q.order_by(ChatbotTurn.created_at.desc()).limit(3).all())
               if (t := memory_mod._turn_message_text(x))]
    # the engine counts this turn's own row too: the prod turns before the anchor, plus one
    before = db.query(func.count(ChatbotTurn.id)).filter(ChatbotTurn.contact_respond_id == str(contact),
                                                         ChatbotTurn.is_test.is_(False),
                                                         ChatbotTurn.created_at < anchor).scalar()
    about = re.search(r"About this contact:\n[^\n]*", prompt["text"]) if prompt is not None else None
    return {"l3": _recorded_l3(row), "anchor": anchor, "about": about.group(0) if about else "", "turn_number": int(before or 0) + 1,
            "previous_reply": previous_reply, "recent": recent, "summaries": summaries, "read_frames": read_frames,
            "earlier_messages": earlier}


def _history_patches(stack: Any, history: dict[str, Any] | None) -> None:
    """Make the engine's history reads return the recorded turn's prod history (see `_recorded_history`)."""
    if history is None:
        return
    from unittest import mock

    from app.services.chatbot import engine, turn_runtime

    stack.enter_context(mock.patch.object(turn_runtime, "previous_reply_text", lambda *a, **k: history["previous_reply"]))
    stack.enter_context(mock.patch.object(turn_runtime, "recent_exchanges", lambda *a, **k: list(history["recent"])))
    from app.services.chatbot import quoted
    from app.services.chatbot.turn import context as context_mod

    # profile facts are read as of now; the L5 block is the recorded one verbatim (empty when the recorded prompt had none;
    # None, a chained turn with no recorded anchor, leaves the as-of-now block)
    if history["about"] is not None:
        stack.enter_context(mock.patch.object(context_mod, "_render_l5", lambda facts: (history["about"], False)))
    if history["l3"] is not None:  # L3 is the recorded prompt's block verbatim; without one the as-of DB reads above serve
        stack.enter_context(mock.patch.object(context_mod, "_render_l3", lambda *a, **k: history["l3"]))
    real_quoted = quoted.resolve_quoted_turn

    def quoted_as_of(db: Any, **kwargs: Any) -> Any:
        return real_quoted(db, **{**kwargs, "is_test": False, "before": history["anchor"]})

    stack.enter_context(mock.patch.object(quoted, "resolve_quoted_turn", quoted_as_of))
    stack.enter_context(mock.patch.object(turn_runtime, "turn_number", lambda *a, **k: history["turn_number"]))
    real_intake = engine._memory_intake

    def intake(db: Any, **kwargs: Any) -> Any:
        res = real_intake(db, **kwargs)
        level = res.get("effective_level")
        if level in ("episodes", "full"):
            res["summaries"], res["read_frames"] = list(history["summaries"]), list(history["read_frames"])
        if level in ("conversation", "episodes", "full"):
            res["earlier_messages"] = list(history["earlier_messages"])
        return res

    stack.enter_context(mock.patch.object(engine, "_memory_intake", intake))


def _live_one(item: dict[str, Any], row: dict[str, Any], factory: Callable[[], Any],
              total: dict[str, Any], prompt_version_id: str | None = None,
              carry: dict[str, Any] | None = None, sink: dict[str, Any] | None = None) -> dict[str, Any]:
    """One live turn on the recorded row's state, or, for a chained turn, on `carry` ({memory, history}: what the
    previous live turn left). `sink` receives what the next chained turn needs (memory, history, message, reply)."""
    _require_rolled_back(factory)
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import console_service, engine
    from app.services.chatbot.contracts import Envelope

    out: dict[str, Any] = {"item_id": item["item_id"], "run": item.get("run")}
    payload = copy.deepcopy(row.get("envelope") or {})
    memory = _recorded_memory(row) if carry is None else carry["memory"]
    payload.update(is_test=True, previous_conversation_state=memory)
    for key in ("mock_reformulator_output", "prompt_overrides", "referenced_result_set"):
        payload.pop(key, None)  # recorded harness keys never carry into a live item
    if prompt_version_id:
        payload["prompt_overrides"] = {PARSER_PROMPT_KEY: prompt_version_id}  # replaces any recorded value whole
    if not _set_text(payload, item["message"]):
        return {**out, "skipped": "no_text_slot"}
    inner = payload.setdefault("message", {}) if isinstance(payload.get("message", {}), dict) else None
    if inner is not None and isinstance(inner.setdefault("message", {}), dict):
        key = f"{item['item_id']}|{item.get('run')}"
        inner["message"]["messageId"] = "live-" + hashlib.sha256(key.encode()).hexdigest()[:16]
        if carry is not None:  # a chained turn is this run's message: it quotes nothing the recorded prod turn quoted
            inner["message"].pop("replyTo", None)
            inner["message"].pop("reply_to", None)
    envelope = Envelope(**payload)
    usage, misses, tool_results = _new_usage(), [], []
    with contextlib.ExitStack() as stack:
        _guard_network(stack, misses, MODEL_HOSTS)
        stack.enter_context(_metered(usage, total))
        stack.enter_context(_tool_capture(tool_results, misses))
        from unittest import mock

        from app.services.chatbot.lanes import ideate

        def ideation_blocked(**kwargs: Any) -> Any:
            misses.append({"llm_blocked": "ideate"})
            raise RuntimeError("llm_blocked: ideate")

        stack.enter_context(mock.patch.object(ideate, "call_ideation_tool", ideation_blocked))
        hist_db = factory()
        history = _recorded_history(hist_db, row, envelope)
        if carry is not None:  # this run's conversation replaces the recorded one; the contact and anchor stay
            history = {**(history or _NO_HISTORY), **carry["history"]}
        _history_patches(stack, history)
        try:
            result = engine.run_turn(envelope, session_factory=factory, offload=False)
        except _CapStop as stop:
            total["stop"] = total.get("stop") or stop.reason
            return {**out, "stopped": stop.reason}
    if getattr(result, "duplicate", False):
        return {**out, "skipped": "duplicate"}
    if sink is not None:
        left = console_service._next_state({"reply": result.reply, "session_patch": result.session_patch})
        reply = result.reply.get("text") if isinstance(result.reply, dict) else None
        sink.update(memory=memory if left is None else left, history=history, message=item["message"],
                    reply=reply if isinstance(reply, str) else None)
    db = factory()
    turn = db.query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    trace = list(turn.trace or []) if turn is not None else []
    if sink is not None:
        sink["turn_no"] = _turn_no(trace)
    uuid_codes = _uuid_codes(db, [t.get("args") for t in trace if isinstance(t, dict) and t.get("kind") == "tool"]
                             + [t.get("args") for t in tool_results])
    understood = next(((r.get("facts") or {}) for r in trace if isinstance(r, dict) and r.get("stage") == "understood"), {})
    out.update(
        provenance={**{k: understood.get(k) for k in PROVENANCE_KEYS},
                    **({"prompt_version_id": prompt_version_id}
                       if prompt_version_id and understood.get("prompt_version") is not None else {})},
        usage=usage, tool_results=tool_results, uuid_codes=uuid_codes,
        turn={"envelope": copy.deepcopy(turn.envelope) if turn is not None else None, "trace": trace,
              "response": copy.deepcopy(turn.response) if turn is not None else None,
              "branch_kind": result.branch_kind, "status": getattr(result, "status", None)},
    )
    if misses:
        out["misses"] = misses
    db.close()
    return out


def _pin_resolves(session_factory: Callable[[], Any], version_id: str) -> bool:
    from app.models.ai_prompt import AIPromptVersion

    db = session_factory()
    try:
        return db.query(AIPromptVersion.id).filter(AIPromptVersion.id == version_id,
                                                   AIPromptVersion.name == PARSER_PROMPT_KEY).first() is not None
    except Exception:  # noqa: BLE001 - a malformed id or an unreadable table is an unresolved pin
        return False
    finally:
        db.close()


# a chained turn whose recorded row has no anchor: no recorded history to start from, the profile block as of now
_NO_HISTORY: dict[str, Any] = {"l3": None, "anchor": None, "about": None, "turn_number": 0, "previous_reply": None,
                               "recent": [], "summaries": [], "read_frames": [], "earlier_messages": []}
# `start: "empty"`: a new conversation, nothing remembered and nothing said before it
_FRESH_START: dict[str, Any] = {"memory": {}, "history": {"l3": None, "previous_reply": None, "recent": [], "summaries": [],
                                                          "read_frames": [], "earlier_messages": [], "turn_number": 1}}


def _conversations(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Items grouped by `conv` in order of first appearance, each group in `conv_turn` order; no `conv` = alone."""
    groups: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for n, item in enumerate(items):
        key = ("conv", item["conv"], item.get("run")) if item.get("conv") is not None else ("item", n, None)
        groups.setdefault(key, []).append(item)   # each run of a conversation is its own chain
    return [sorted(group, key=lambda item: item.get("conv_turn") or 0) for group in groups.values()]


def _turn_no(trace: list[Any]) -> int | None:
    """The turn number the engine recorded for a live turn (the trace record carrying `turn_no` and `quoted_turn`)."""
    queue: list[Any] = list(trace or [])
    while queue:
        node = queue.pop(0)
        if isinstance(node, dict):
            if "quoted_turn" in node and isinstance(node.get("turn_no"), int):
                return node["turn_no"]
            queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)
    return None


def _next_carry(sink: dict[str, Any]) -> dict[str, Any]:
    """What the next chained turn starts from: the memory this turn left and this turn added to the history it saw
    (L3 is then rendered from those reads, not taken verbatim from a recorded prompt). The turn number counts on from
    the number this turn ran with (its trace), so a first turn with no recorded anchor still counts right."""
    from app.services.chatbot import engine

    seen = sink.get("history") or _NO_HISTORY
    message, reply = sink["message"], sink.get("reply")
    at = engine._short_day_time(seen["anchor"]) if seen.get("anchor") is not None else ""
    recent = list(seen.get("recent") or []) + ([(message, reply)] if reply else [])
    earlier = list(seen.get("earlier_messages") or []) + [{"created_at": at, "text": message}]
    ran = sink.get("turn_no") if isinstance(sink.get("turn_no"), int) else int(seen.get("turn_number") or 0)
    return {"memory": sink["memory"], "history": {
        "l3": None, "previous_reply": reply, "recent": recent[-3:], "earlier_messages": earlier[-3:],
        "turn_number": ran + 1, "summaries": list(seen.get("summaries") or []),
        "read_frames": list(seen.get("read_frames") or [])}}


LIVE_ERROR_RATE = 0.02


def _live_errored(res: dict[str, Any]) -> bool:
    """An item that raised, or whose turn came back with no trace and no response."""
    if "error" in res:
        return True
    turn = res.get("turn")
    return isinstance(turn, dict) and (turn.get("status") == "failed" or (not turn.get("trace") and not turn.get("response")))


def live_rows(items: list[dict[str, Any]], recorded_rows: list[dict[str, Any]], *,
              session_factory: Callable[[], Any], max_calls: int, max_usd: float,
              require_key_last4: str, on_row: Callable[[dict[str, Any]], None] | None = None,
              prompt_version_id: str | None = None, chain: bool = False) -> list[dict[str, Any]]:
    """Run each item's message through the engine on its recorded state; stop cleanly at a call or spend cap,
    or at "wrong_key" when a provider or embedding key does not end with `require_key_last4` (exactly 4 non-blank
    characters, else ValueError before any item). A `prompt_version_id` that is not a stored version of the parser
    prompt is a ValueError before any item (the registry would otherwise fall back silently).
    `chain`: items sharing `conv` run as one conversation (module docstring, --chain); each turn is still rolled
    back on its own, the conversation lives in the carried memory and history only."""
    if not _valid_last4(require_key_last4):
        raise ValueError("require_key_last4 must be exactly 4 non-blank characters")
    if prompt_version_id and not _pin_resolves(session_factory, prompt_version_id):
        raise ValueError("prompt_version_id is not a stored version of the parser prompt")
    by_id = {str(r["id"]): r for r in recorded_rows}
    total = _new_usage()
    total.update(max_calls=max_calls, max_usd=max_usd, stop=None, require_key_last4=require_key_last4)
    results: list[dict[str, Any]] = []
    stopped: str | None = None
    error_ids: list[Any] = []
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        halted = False
        for group in _conversations(items) if chain else [[item] for item in items]:
            if halted:
                break
            carry = copy.deepcopy(_FRESH_START) if chain and group[0].get("start") == "empty" else None
            broken = False
            for item in group:
                stopped = stopped or total.get("stop")
                if stopped is None:
                    if total["calls"] >= max_calls:
                        stopped = "max_calls"
                    elif total["usd"] >= max_usd:
                        stopped = "max_usd"
                if stopped:
                    res = {"item_id": item["item_id"], "run": item.get("run"), "stopped": stopped}
                elif broken:
                    res = {"item_id": item["item_id"], "run": item.get("run"), "skipped": "chain_broken"}
                elif str(item.get("recorded_turn_id")) not in by_id:
                    res = {"item_id": item["item_id"], "run": item.get("run"), "skipped": "no_recorded_row"}
                else:
                    sink: dict[str, Any] = {}
                    chained = {"carry": carry, "sink": sink} if chain else {}   # unchained: the call is unchanged
                    try:
                        with _rolled_back(session_factory) as factory:
                            res = _live_one(item, by_id[str(item["recorded_turn_id"])], factory, total,
                                            prompt_version_id, **chained)
                    except Exception as exc:  # noqa: BLE001 - record the type only; never message text
                        res = {"item_id": item["item_id"], "run": item.get("run"), "error": type(exc).__name__}
                    if chain and "turn" in res:
                        carry = _next_carry(sink)
                if chain:
                    broken = broken or "turn" not in res
                    if item.get("conv") is not None:
                        res = {**res, "conv": item["conv"], "conv_turn": item.get("conv_turn")}
                results.append(res)
                if on_row is not None:
                    on_row(res)
                if _live_errored(res):
                    error_ids.append(item["item_id"])
                if len(error_ids) > max(2, LIVE_ERROR_RATE * len(items)):   # (never on a lone error in a short list) the bench is broken, not the bot: stop spending
                    res = {"stopped": "error_rate", "errors": len(error_ids), "first_ids": error_ids[:5]}
                    results.append(res)
                    if on_row is not None:
                        on_row(res)
                    halted = True
                    break
    finally:
        logging.disable(previous_disable)
    return results


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #


def _stage_value(row: dict[str, Any], stage: str) -> Any:
    stages = row.get("stages") or {}
    if stage == "tools":
        return [stages.get("tools"), row.get("misses") or []]
    return stages.get(stage)


def _broken_reason(r: dict[str, Any]) -> str | None:
    """Why a replay row is no evidence: the engine errored, a run row has no stages, or the turn was a duplicate."""
    if any(isinstance(m, dict) and "error" in m for m in r.get("misses") or []):
        return "error"
    if r.get("skipped") == "duplicate":
        return "duplicate"
    if not r.get("skipped") and not r.get("stages"):
        return "empty_stages"
    return None


def diff_rows(base: list[dict[str, Any]], head: list[dict[str, Any]],
              intended: set[tuple[str, str]] | None = None) -> dict[str, Any]:
    intended = intended or set()
    head_by = {(r["case_id"], r["turn_id"]): r for r in head}
    diffs: list[tuple[str, str, str]] = []
    stage_counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for b in base:
        key = (b["case_id"], b["turn_id"])
        h = head_by.get(key)
        if h is None:
            skipped["missing_in_head"] = skipped.get("missing_in_head", 0) + 1
            continue
        kind = b.get("skipped") or h.get("skipped")
        if kind:
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        for stage in STAGES:
            if stage == "reply" and ("reply" not in (b.get("stages") or {}) or "reply" not in (h.get("stages") or {})):
                continue
            if _stage_value(b, stage) != _stage_value(h, stage):
                diffs.append((key[0], key[1], stage))
                stage_counts[stage] = stage_counts.get(stage, 0) + 1
                break
    base_keys = {(b["case_id"], b["turn_id"]) for b in base}
    for h in head:
        key = (h["case_id"], h["turn_id"])
        if key not in base_keys:
            skipped["missing_in_base"] = skipped.get("missing_in_base", 0) + 1
            diffs.append((key[0], key[1], "missing_in_base"))
    unintended = [d for d in diffs if d[2] == "missing_in_base" or (d[0], d[2]) not in intended]
    head_keys = {(h["case_id"], h["turn_id"]): h for h in head}
    compared = sum(1 for b in base if (b["case_id"], b["turn_id"]) in head_keys
                   and not b.get("skipped") and not head_keys[(b["case_id"], b["turn_id"])].get("skipped"))
    broken: list[tuple[str, str, str]] = []
    for r in (*base, *head):   # a row broken in either arm counts once, even when both arms match
        key = (r["case_id"], r["turn_id"])
        reason = _broken_reason(r)
        if reason and key not in {b[:2] for b in broken}:
            broken.append((*key, reason))
    return {"diffs": diffs, "unintended": unintended, "broken": broken, "compared": compared, "counts": {"stage": stage_counts, "skipped": skipped}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _read(path: str) -> list[dict[str, Any]]:
    out = []
    with open(path) as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except ValueError:  # a run killed mid-write leaves a cut last line
                continue
    return out


def _db_revisions(session_factory: Callable[[], Any]) -> set[str]:
    from sqlalchemy import text

    db = session_factory()
    try:
        return {r[0] for r in db.execute(text("SELECT version_num FROM alembic_version"))}
    finally:
        db.close()


def _code_heads(backend_dir: str) -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def _revision_known(backend_dir: str, rev: str) -> bool:
    """Whether this arm's migration scripts contain `rev` (False: the DB was migrated by another lane)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    try:
        return ScriptDirectory.from_config(cfg).get_revision(rev) is not None
    except Exception:  # noqa: BLE001 - an unresolvable revision is unknown to this arm
        return False


def _preflight(session_factory: Callable[[], Any]) -> int | None:
    """Refuse (2) a replay DB that is not at this checkout's alembic head: a stale schema fails every turn quietly."""
    current, heads = _db_revisions(session_factory), _code_heads(os.getcwd())
    if current == heads:
        return None
    if not current:
        print(f"refused: replay DB has no alembic_version row (unstamped); run: "
              f"SORENTO_ENV_FILE={os.environ.get('SORENTO_ENV_FILE') or '.env'} alembic upgrade head", file=sys.stderr)
        return 2
    ahead = {r for r in current - heads if not _revision_known(os.getcwd(), r)}   # the DB is ahead: another lane migrated it
    if current - heads == ahead:
        print(f"warning: replay DB alembic {' '.join(sorted(ahead))} is unknown to this checkout (DB ahead)", file=sys.stderr)
        return None
    env = os.environ.get("SORENTO_ENV_FILE") or ".env"
    print(f"refused: replay DB alembic {' '.join(sorted(current))} != code head {' '.join(sorted(heads))}; "
          f"run: SORENTO_ENV_FILE={env} alembic upgrade head", file=sys.stderr)
    return 2


def _cmd_run(args: argparse.Namespace) -> int:
    import app

    if not os.path.abspath(app.__file__).startswith(os.getcwd() + os.sep):
        print(f"refused: app is imported from {app.__file__}, not from the cwd", file=sys.stderr)
        return 2
    from app.database import SessionLocal

    refused = _preflight(SessionLocal)
    if refused:
        return refused
    rows = [r for path in args.traces for r in _read(path)]
    # One line per finished row, flushed, so a run killed at a time budget leaves its finished rows.
    with open(args.out, "w") as fh:
        def write(r: dict[str, Any]) -> None:
            fh.write(json.dumps(r) + "\n")
            fh.flush()

        replay_rows(rows, session_factory=SessionLocal, stage=args.stage, on_row=write)
    return 0


def _cmd_live(args: argparse.Namespace) -> int:
    import app

    if not os.path.abspath(app.__file__).startswith(os.getcwd() + os.sep):
        print(f"refused: app is imported from {app.__file__}, not from the cwd", file=sys.stderr)
        return 2
    from app.database import SessionLocal

    refused = _preflight(SessionLocal)
    if refused:
        return refused
    items, recorded = _read(args.items), _read(args.recorded)
    n = {"run": 0, "stopped": 0, "skipped": 0}
    last: dict[str, Any] = {}
    with open(args.out, "w") as fh:
        os.chmod(args.out, 0o600)

        def write(r: dict[str, Any]) -> None:
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            n["stopped" if "stopped" in r else "skipped" if "skipped" in r else "run"] += 1
            last.update(r)

        live_rows(items, recorded, session_factory=SessionLocal, max_calls=args.max_calls, max_usd=args.max_usd,
                  require_key_last4=args.require_key_last4, on_row=write,
                  prompt_version_id=args.prompt_version_id, chain=args.chain)
    print(f"live items {len(items)} run {n['run']} stopped {n['stopped']} skipped {n['skipped']}")
    if last.get("stopped") == "error_rate":
        print(f"error_rate stop: {last['errors']} errored items, first {' '.join(str(i) for i in last['first_ids'])}")
        return 3
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    intended: set[tuple[str, str]] = set()
    if args.intended:
        for line in open(args.intended):
            parts = line.split()
            if len(parts) == 2:
                intended.add((parts[0], parts[1]))
    res = diff_rows(_read(args.base), _read(args.head), intended)
    for cid, tid, stage in res["diffs"]:
        print(f"{cid} {tid} {stage}")
    print(f"stages {json.dumps(res['counts']['stage'], sort_keys=True)}")
    print(f"skipped {json.dumps(res['counts']['skipped'], sort_keys=True)}")
    print(f"unintended {len(res['unintended'])}")
    print(f"compared {res['compared']}")
    if res["broken"]:
        print(f"BROKEN: broken {len(res['broken'])} {' '.join(str(b[1]) for b in res['broken'][:5])}".rstrip())
    if res["broken"] or res["compared"] == 0:   # checked first: broken or nothing compared is no evidence, not "no diff"
        return 3
    return 1 if res["unintended"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--traces", nargs="+", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--stage", choices=["grounding"], default=None)
    live = sub.add_parser("live")
    live.add_argument("--items", required=True)
    live.add_argument("--recorded", required=True)
    live.add_argument("--out", required=True)
    live.add_argument("--max-calls", type=int, required=True)
    live.add_argument("--max-usd", type=float, required=True)
    live.add_argument("--require-key-last4", type=_last4_arg, required=True)
    live.add_argument("--prompt-version-id", default=None)
    live.add_argument("--chain", action="store_true")
    diff = sub.add_parser("diff")
    diff.add_argument("base")
    diff.add_argument("head")
    diff.add_argument("--intended")
    args = ap.parse_args(argv)
    return {"run": _cmd_run, "live": _cmd_live, "diff": _cmd_diff}[args.cmd](args)


if __name__ == "__main__":
    sys.path.insert(0, os.getcwd())
    sys.exit(main())
