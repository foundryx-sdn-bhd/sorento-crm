"""Record/replay of LLM calls for TEST runs (LLM-TEST-COST, 4 Oct 2026).

Scripted scenario runs and cloud browser passes were paying full price for the same parse
over and over (2 Oct: 271 scripted test runs, about $14 at list price). This module lets a
run answer from a recording instead:

    LLM_CASSETTE_MODE=off      (default, and the only value production ever sees) - live
    LLM_CASSETTE_MODE=record   call live, then write the response to a cassette file
    LLM_CASSETTE_MODE=replay   answer from the cassette; a miss RAISES `CassetteMiss`,
                               naming the key. It never falls through to a live call.

The seam is `llm_provider.OpenAIProvider` (`chat` and `embed`), so every caller of
`get_provider` is covered without knowing this module exists. Anthropic and Gemini are not
recorded: in replay mode they refuse (`CassetteError`) rather than spend, and so do the
direct-HTTP transcription and market-research calls (`refuse_uncovered`). The direct-HTTP
embedding calls (`embedding_worker._embed_text_chunks`, `external/rag`) record and replay
like `OpenAIProvider.embed`, so a replayed turn's semantic matching behaves as it did live.

**Key.** sha256 over the request's model, messages, response format (the strict schema)
and tools - what decides the answer. Temperature and the token ceiling do not change the
key. The system prompt carries the date (`{{current_date}}`), so a recording is valid for
the day it was made and the prompt version it was made with; a new day re-records.

**Storage.** `LLM_CASSETTE_DIR`, default `~/.cache/sorento-llm-cassettes`, and a directory
inside any git checkout is refused: a recording holds the model's answer to a real
message, which is customer data, and the repository is public. Files are written 0600 in
a 0700 directory, hold only the RESPONSE (never the request text), and a response that
contains the API key is not written at all.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

MODE_ENV = "LLM_CASSETTE_MODE"
DIR_ENV = "LLM_CASSETTE_DIR"
MODES = ("off", "record", "replay")
DEV_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "ci"})

# The request fields that decide the answer. Everything else (temperature, the ceiling,
# `tool_choice`, timeouts) is left out so a harmless tweak keeps the recordings.
_CHAT_KEY_FIELDS = ("model", "messages", "response_format", "tools")


T = TypeVar("T")


class CassetteError(RuntimeError):
    """The cassette layer refused: bad directory, uncovered provider, unsafe content."""


class CassetteMiss(CassetteError):
    """Replay mode had no recording for this request. Never retried, never live."""


def mode() -> str:
    """The current mode, read per call so a test or a runner can switch it.

    Anything but `off` runs only in a dev/test `ENVIRONMENT` (an allowlist, so a staging or
    prod copy under another name is refused too): `record` there would write the model's
    answers to real customers into an unmanaged file, and `replay` would fail every turn.
    """
    value = (os.environ.get(MODE_ENV) or "off").strip().lower()
    if value not in MODES:
        raise ValueError(f"{MODE_ENV}={value!r} is not one of {', '.join(MODES)}")
    if value != "off":
        from app.config import settings

        environment = (settings.environment or "").strip().lower()
        if environment not in DEV_ENVIRONMENTS:
            raise CassetteError(
                f"{MODE_ENV}={value} is refused in ENVIRONMENT={environment!r} "
                f"(allowed only in {', '.join(sorted(DEV_ENVIRONMENTS))}; never production)"
            )
    return value


def pinned_date() -> str | None:
    """`LLM_CASSETTE_DATE`, honoured only while recording or replaying (so never in
    production, which refuses those modes). Pins the parser's "today" so a cassette keyed
    on a dated request stays valid past midnight, and a replayed answer's relative dates
    stay the ones it was recorded with."""
    if mode() == "off":
        return None
    return (os.environ.get("LLM_CASSETTE_DATE") or "").strip() or None


def mode_label() -> str:
    """`mode()` for a trace record: never raises, so a bad value names itself there
    instead of failing a turn that made no model call (a harness-bypassed parse)."""
    try:
        return mode()
    except (ValueError, CassetteError) as exc:
        return f"invalid: {exc}"


def runner_mode(*, live: bool, record: bool) -> str:
    """The mode a scripted runner starts its backend in: replay unless told otherwise."""
    if live and record:
        raise ValueError("--live and --record are mutually exclusive")
    if record:
        return "record"
    return "off" if live else "replay"


def configure_runner(argv: list[str]) -> str:
    """Set the mode for a scripted runner's backend process from its own `argv`.

    `--live` spends for real, `--record` spends and records; neither means replay, unless
    `LLM_CASSETTE_MODE` was already exported, which is then respected.
    """
    live, record = "--live" in argv, "--record" in argv
    if live or record:
        os.environ[MODE_ENV] = runner_mode(live=live, record=record)
    else:
        os.environ.setdefault(MODE_ENV, "replay")
    return mode()


def cassette_dir() -> Path:
    raw = os.environ.get(DIR_ENV)
    # Default computed here, not at import: the engine imports this module, and a
    # container without a home directory must still import the engine.
    path = Path(raw).expanduser() if raw else Path.home() / ".cache" / "sorento-llm-cassettes"
    resolved = path.resolve()
    # ANY git checkout, not just this one: a worktree's primary checkout or a second clone
    # of the same public repository is one `git add -A` away from publishing a recording.
    for directory in (resolved, *resolved.parents):
        if (directory / ".git").exists():
            raise CassetteError(
                f"{DIR_ENV}={path} is inside the repository at {directory}; recordings "
                "hold customer data and must live outside git"
            )
    return resolved


def _require_private(directory: Path) -> None:
    """Ours and closed to others, or refused: anyone who can write here can plant a
    replayed answer, and anyone who can read here can read customer data."""
    info = directory.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise CassetteError(
            f"cassette directory {directory} must be owned by this user and mode 0700 "
            f"(found uid {info.st_uid}, mode {oct(info.st_mode & 0o777)})"
        )


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def chat_key(kwargs: dict[str, Any]) -> str:
    return _digest({"kind": "chat", **{f: kwargs.get(f) for f in _CHAT_KEY_FIELDS}})


def embed_key(model: str, text: str) -> str:
    return _digest({"kind": "embed", "model": model, "input": text})


def _path(key: str) -> Path:
    return cassette_dir() / key[:2] / f"{key}.json"


def _write(key: str, kind: str, model: str, response: Any, secret: str) -> None:
    body = json.dumps(
        {
            "key": key,
            "kind": kind,
            "model": model,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "response": response,
        },
        ensure_ascii=False,
        default=str,
    )
    if secret and secret in body:
        raise CassetteError(f"refusing to write cassette {key}: the response contains the API key")
    path = _path(key)
    # Created 0700 at every level this code creates; an existing directory keeps its mode.
    for directory in (cassette_dir(), path.parent):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require_private(directory)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read(key: str, kind: str, model: str) -> Any:
    path = _path(key)
    for directory in (cassette_dir(), path.parent):
        if directory.is_dir():
            _require_private(directory)
    if not path.is_file():
        raise CassetteMiss(
            f"LLM cassette miss: no {kind} recording for key {key} (model {model}) in "
            f"{path.parent.parent}. Record it with LLM_CASSETTE_MODE=record, or run live "
            "(--live / LLM_CASSETTE_MODE=off)."
        )
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["response"]


def through(
    *,
    kind: str,
    key: str,
    model: str,
    secret: str,
    live: Callable[[], T],
    dump: Callable[[T], Any],
    load: Callable[[Any], T],
) -> T:
    """Run `live` per the current mode: as is, recording its result, or not at all."""
    current = mode()
    if current == "off":
        return live()
    if current == "replay":
        logger.debug("llm cassette: replay %s %s", kind, key)
        return load(_read(key, kind, model))
    # Fail on a bad directory BEFORE paying for the call.
    cassette_dir()
    result = live()
    _write(key, kind, model, dump(result), secret)
    logger.info("llm cassette: recorded %s %s", kind, key)
    return result


def refuse_uncovered(provider: str) -> None:
    """Replay must never spend: a provider this layer does not record cannot run in it."""
    if mode() == "replay":
        raise CassetteError(
            f"LLM_CASSETTE_MODE=replay covers the OpenAI provider only; refusing a live "
            f"{provider} call. Run live (--live / LLM_CASSETTE_MODE=off) for this lane."
        )
