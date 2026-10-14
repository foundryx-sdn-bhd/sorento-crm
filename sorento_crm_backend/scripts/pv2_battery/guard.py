"""Nothing leaves (UAC-6): refuse every RQ enqueue and Respond send in this process, check the env
before the first turn, and shape the console envelope so the stock-ask notify returns early."""
from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlparse


class GuardError(RuntimeError):
    """The environment or an envelope is not safe for a battery turn."""


class OutboundBlocked(RuntimeError):
    """A send or an enqueue was attempted while the guard is installed."""


_LOCK = threading.Lock()
_BLOCKED: list[tuple[int, str]] = []
#: (module name, attribute, original) for every patched binding, so uninstall restores them all.
_PATCHED: list[tuple[Any, str, Any]] = []
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
DEAD_PROXY_PORT = 9


def check_env(env: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or ""
    parsed = urlparse(proxy) if proxy else None
    if not proxy:
        problems.append("HTTPS_PROXY is not set; the run needs the dead proxy (http://127.0.0.1:9)")
    elif parsed is None or parsed.hostname not in _LOOPBACK or parsed.port != DEAD_PROXY_PORT:
        problems.append(f"HTTPS_PROXY {proxy!r} is not the dead proxy (http://127.0.0.1:{DEAD_PROXY_PORT})")
    lower = env.get("https_proxy")
    if lower and lower != proxy:
        problems.append("https_proxy differs from HTTPS_PROXY")
    for key in ("NO_PROXY", "no_proxy"):
        if "*" in (env.get(key) or ""):
            problems.append(f"{key} holds '*', which lets every host bypass the dead proxy")
    db = urlparse(env.get("DATABASE_URL") or "")
    if db.hostname not in _LOOPBACK:
        problems.append(f"DATABASE_URL host {db.hostname!r} is not localhost")
    if "prod" in (db.path or "").lower() or "prod" in (db.hostname or "").lower():
        problems.append("DATABASE_URL looks like a production database")
    return problems


NO_PROXY_HOSTS = ("localhost,127.0.0.1,.localhost,api.openai.com,api.anthropic.com,"
                  "generativelanguage.googleapis.com,amazonaws.com,r2.cloudflarestorage.com,r2.dev,"
                  "cloudfront.net")


def harden_env(env: Any) -> Any:
    """All four proxy vars on the dead proxy, NO_PROXY limited to the model and storage hosts (never '*')."""
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        env[key] = f"http://127.0.0.1:{DEAD_PROXY_PORT}"
    for key in ("NO_PROXY", "no_proxy"):
        env[key] = NO_PROXY_HOSTS
    return env


def check_ideation_url(url: str | None) -> None:
    if not url:
        return
    if urlparse(url).hostname not in _LOOPBACK:
        raise GuardError(f"ideation service {urlparse(url).hostname!r} is not local; refusing the run")


def blocked_events() -> list[str]:
    """Every attempt refused since install (an engine that swallows the exception still shows here)."""
    with _LOCK:
        return [label for _, label in _BLOCKED]


def take_blocked() -> list[str]:
    """The refusals raised on the calling thread, removed; a turn runs on one thread."""
    me = threading.get_ident()
    with _LOCK:
        mine = [label for ident, label in _BLOCKED if ident == me]
        _BLOCKED[:] = [(i, l) for i, l in _BLOCKED if i != me]
        return mine


def _blocker(label: str) -> Callable[..., Any]:
    def _blocked(*args: Any, **kwargs: Any) -> Any:
        with _LOCK:
            _BLOCKED.append((threading.get_ident(), label))
        raise OutboundBlocked(f"{label} refused: the battery sends nothing")

    _blocked.__name__ = f"blocked_{label.rsplit('.', 1)[-1]}"
    return _blocked


def _patch_everywhere(original: Any, replacement: Callable[..., Any]) -> None:
    """Replace `original` in every loaded module that holds it by name (a `from x import f` binding)."""
    for mod in list(sys.modules.values()):
        ns = getattr(mod, "__dict__", None)
        if not isinstance(ns, dict):
            continue
        for name, value in list(ns.items()):
            if value is original:
                _PATCHED.append((mod, name, original))
                setattr(mod, name, replacement)


def install(env: Mapping[str, str]) -> None:
    problems = check_env(env)
    if problems:
        raise GuardError("; ".join(problems))
    if _PATCHED:
        return
    import rq

    from app.services import queue_service, respond_messaging_service

    originals = [
        (rq.Queue, "enqueue_call", rq.Queue.enqueue_call, "rq.Queue.enqueue_call"),
        (None, "", queue_service.enqueue_job, "enqueue_job"),
        (None, "", respond_messaging_service.send_text_or_template, "send_text_or_template"),
    ]
    for owner, attr, original, label in originals:
        replacement = _blocker(label)
        if owner is not None:
            _PATCHED.append((owner, attr, original))
            setattr(owner, attr, replacement)
        else:
            _patch_everywhere(original, replacement)


def uninstall() -> None:
    while _PATCHED:
        owner, attr, original = _PATCHED.pop()
        setattr(owner, attr, original)
    with _LOCK:
        _BLOCKED.clear()


def patch_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """The console envelope without `console_origin`, so `_chat_console` is false and the stock-ask
    notify returns early. Refuses an envelope that is not a test envelope."""
    if envelope.get("is_test") is not True:
        raise GuardError("envelope is not is_test; refusing to run it")
    out = dict(envelope)
    out.pop("console_origin", None)
    return out


@contextmanager
def force_parser_mode(mode: str) -> Iterator[None]:
    """`pv2_flag.parser_mode` returns `mode` for every call in this process while the block runs."""
    if mode not in ("v1", "v2"):
        raise GuardError(f"parser mode must be v1 or v2, not {mode!r}")
    from app.services.chatbot.pv2 import flag

    original = flag.parser_mode
    flag.parser_mode = lambda *args, **kwargs: mode  # type: ignore[assignment]
    try:
        yield
    finally:
        flag.parser_mode = original  # type: ignore[assignment]
