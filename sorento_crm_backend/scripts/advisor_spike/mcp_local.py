"""The existing MCP tools, in process, no server on any port.

v1 reaches the same tools over HTTP (`services._mcp_call` -> `MCPRuntimeClient` -> `ai_assistant_mcp_url`). Here
the SAME worktree FastMCP app (`sorento_crm_mcp.server.create_mcp_app`) is connected through an in-memory MCP
session, so the tools, presenters and narrowing rules are byte-identical. The tools still read the backend
over HTTP, but the HTTP is in process too: the CRMClient is pointed at THIS worktree's FastAPI app through
httpx.ASGITransport (no lifespan, so no startup hooks or scheduler). Nothing listens on any port, and no other
lane's stack is touched. `patch_v1_mcp()` routes the chatbot's own MCPRuntimeClient (business lane, ideate) to
the same session, so v1 and the advisor run identical tools on identical code.
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

    import httpx

    import app.main as backend_main
    from sorento_crm_mcp import server as mcp_server
    from sorento_crm_mcp.http_client import CRMClient

    class AsgiCRMClient(CRMClient):
        def __init__(self, settings: Any) -> None:
            super().__init__(settings)
            self._client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=backend_main.app),
                base_url=env.CRM_BASE_URL,
                headers={"X-API-Key": settings.external_api_key, "Accept": "application/json"},
                timeout=httpx.Timeout(settings.request_timeout_seconds),
                follow_redirects=True,
            )

    mcp_server.CRMClient = AsgiCRMClient  # type: ignore[misc]
    settings = Settings(
        crm_base_url=env.CRM_BASE_URL,
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


def patch_v1_mcp() -> None:
    """Route `MCPRuntimeClient` (services._mcp_call, ideate) to the in-process session instead of HTTP."""
    from app.services import ai_assistant_service as svc

    if getattr(svc.MCPRuntimeClient, "_advisor_spike_patched", False):
        return

    def _rpc(self: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if method == "tools/call":
            res = _run(_state_session().call_tool(params["name"], params.get("arguments") or {}))
            return {"content": [{"type": "text", "text": c.text} for c in res.content if getattr(c, "text", None)],
                    "isError": bool(getattr(res, "isError", False))}
        if method == "tools/list":
            tools = _all_tools()
            return {"tools": [{"name": n, "description": t.description or "", "inputSchema": t.inputSchema}
                              for n, t in tools.items()]}
        raise RuntimeError(f"in-process MCP: unsupported method {method}")

    svc.MCPRuntimeClient._rpc = _rpc  # type: ignore[method-assign]
    svc.MCPRuntimeClient._advisor_spike_patched = True  # type: ignore[attr-defined]
