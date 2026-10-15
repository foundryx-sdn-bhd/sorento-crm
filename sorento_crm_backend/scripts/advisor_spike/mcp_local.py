"""The existing MCP tools, in process, no server on any port.

v1 reaches the same tools over HTTP (`services._mcp_call` -> `MCPRuntimeClient` -> `ai_assistant_mcp_url`). Here
the SAME worktree FastMCP app (`sorento_crm_mcp.server.create_mcp_app`) is connected through an in-memory MCP
session, so the tools, presenters and narrowing rules are byte-identical. The tools still read the backend
over HTTP (`CRM_BASE_URL`, GET only, the dev stack's backend), which is also what v1's MCP server does.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import os
import sys
import threading
from pathlib import Path
from typing import Any

from . import env

SCOPE_PARAMS = ("contact_id", "space_id")
_TIMEOUT_S = 60

_lock = threading.Lock()
_state: dict[str, Any] = {}


def ensure_worktree_mcp() -> Path:
    """The shared venv's editable install points at the primary checkout; import THIS worktree's package."""
    root = env.REPO_ROOT / "sorento_crm_mcp"
    if sys.path[:1] != [str(root)]:
        if str(root) in sys.path:
            sys.path.remove(str(root))
        sys.path.insert(0, str(root))
    for name in [n for n in sys.modules if n == "sorento_crm_mcp" or n.startswith("sorento_crm_mcp.")]:
        file = getattr(sys.modules[name], "__file__", None)
        if not file or root not in Path(file).resolve().parents:
            del sys.modules[name]
    importlib.invalidate_caches()
    mod = importlib.import_module("sorento_crm_mcp.server")
    path = Path(mod.__file__).resolve()
    if root not in path.parents:
        raise RuntimeError(f"sorento_crm_mcp resolves to {path}, outside the worktree {root}")
    return path


async def _serve(ready: threading.Event, stop: asyncio.Event) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    from sorento_crm_mcp.server import create_mcp_app
    from sorento_crm_mcp.settings import Settings

    settings = Settings(
        crm_base_url=env.crm_base_url(),
        external_api_key=os.environ["EXTERNAL_API_KEY"],
        mcp_host="127.0.0.1",
    )  # type: ignore[call-arg]
    app = create_mcp_app(settings)
    try:
        async with create_connected_server_and_client_session(app._mcp_server) as session:
            _state["session"] = session
            ready.set()
            await stop.wait()
    except BaseException as exc:  # noqa: BLE001
        _state["error"] = exc
        ready.set()
        raise


def _start() -> None:
    with _lock:
        if "session" in _state:
            return
        ensure_worktree_mcp()
        ready = threading.Event()
        loop = asyncio.new_event_loop()
        stop_holder: dict[str, asyncio.Event] = {}

        def run() -> None:
            asyncio.set_event_loop(loop)
            stop_holder["stop"] = asyncio.Event()
            try:
                loop.run_until_complete(_serve(ready, stop_holder["stop"]))
            except BaseException:  # noqa: BLE001
                pass

        t = threading.Thread(target=run, name="mcp-local", daemon=True)
        t.start()
        ready.wait(60)
        if "session" not in _state:
            raise RuntimeError(f"in-process MCP failed to start: {_state.get('error')!r}")
        _state["loop"] = loop


def _run(coro: Any) -> Any:
    _start()
    return asyncio.run_coroutine_threadsafe(coro, _state["loop"]).result(_TIMEOUT_S)


def _all_tools() -> dict[str, Any]:
    if "tools" not in _state:
        result = _run(_state_session().list_tools())
        _state["tools"] = {t.name: t for t in result.tools}
    return _state["tools"]


def _state_session() -> Any:
    _start()
    return _state["session"]


def _strip_scope(schema: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(schema or {"type": "object", "properties": {}})
    props = out.get("properties") or {}
    for p in SCOPE_PARAMS:
        props.pop(p, None)
    out["properties"] = props
    if isinstance(out.get("required"), list):
        out["required"] = [r for r in out["required"] if r not in SCOPE_PARAMS]
        if not out["required"]:
            out.pop("required")
    return out


def list_tool_schemas(names: list[str]) -> list[dict[str, Any]]:
    """`[{name, description, input_schema}]` from the MCP tool definitions, scope params removed.
    Unknown or non-ADVISOR_READ_ONLY names are skipped."""
    from .subset import ADVISOR_READ_ONLY

    tools = _all_tools()
    out = []
    for n in names:
        t = tools.get(n)
        if t is None or n not in ADVISOR_READ_ONLY:
            continue
        out.append({"name": n, "description": t.description or "", "input_schema": _strip_scope(t.inputSchema)})
    return out


def call_tool(name: str, args: dict[str, Any], scope: dict[str, Any]) -> str:
    """One read-only tool call with the scope stamped by code (fetch.py:1295: contact_id as a stripped
    string, space_id with the workspace default). The model's own contact_id/space_id are overwritten."""
    from app.services.chatbot.lanes.business.fetch import space_id_or_default

    from .subset import ADVISOR_READ_ONLY

    if name not in ADVISOR_READ_ONLY:
        return f"ERROR: tool {name} is not allowed (read-only tools only)"
    call_args = {k: v for k, v in (args or {}).items() if k not in SCOPE_PARAMS}
    call_args["contact_id"] = str(scope.get("contact_id") or "").strip()
    call_args["space_id"] = space_id_or_default(scope.get("space_id"))
    result = _run(_state_session().call_tool(name, call_args))
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    if getattr(result, "isError", False):
        return f"ERROR: {text}"
    return text
