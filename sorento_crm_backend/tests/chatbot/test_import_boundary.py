"""AC-002: nothing outside the module's own doorways imports `app.services.chatbot`.

The package is module-private with ONE public entry point. It may import core services
freely; core must never import it. That asymmetry is the entire "liftable later" story -
the day the named trigger fires, the package moves behind an HTTP boundary and only the
files listed here have to change.

The check is a source scan, not an import graph walk: an importer that is never executed
by the suite is still an importer, and the failure message has to name the file so the
fix is obvious.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# The only files allowed to reach into the package.
#
# `app/api/v1/system/chatbot.py` joined at S2b, and the UAC's AC-002 was amended in the
# same change rather than this list being widened quietly. The reason it belongs: the turn
# trace screen is the chatbot module's own read surface, and its Retry calls the module's
# retry seam (`dispatch.reinject_envelope`). AC-257 names this exact path as where that
# endpoint lives, so the doorway is in the contract; AC-002 simply predated it.
#
# The asymmetry that matters is unchanged: core still never imports the package. Every
# entry here is a chatbot-module surface that happens to be mounted in a shared router.
ALLOWED = {
    "app/api/v1/external/chat.py",
    "app/api/v1/system/chatbot.py",
    "app/tasks/chat_turns.py",
    # A developer tool, not core, and not on any request path: it sends real turns through
    # BOTH prompt versions and diffs them (AC-153), which needs `head.parser` and
    # `head.output_exchange` directly and has no meaningful route through `/chat/turn`.
    # It is listed rather than exempted by directory: the rule this file protects is "core
    # must never import the package", and the day the package moves behind an HTTP
    # boundary this script is exactly the kind of file that has to be updated with it - so
    # it belongs in the list that says so.
    "scripts/chatbot_parser_parity.py",
    # Chatbot memory lane A (contract section 3/5.3): a maintenance script, not core, not
    # on any request path - it seeds closed episodes over EXISTING history by calling
    # `turn.memory.write_episode_for_reset` (the SAME writer the live engine calls, so a
    # backfilled frame and a live one can never disagree, AC-MEM029) and needs it directly.
    "scripts/backfill_chatbot_episodes.py",
    # CHATBOT-QUEUE-FIX hand test (1 Oct 2026): replays the per-contact queue incident
    # against a redis by driving `dispatch` directly. A dry-run turn takes no ticket
    # (engine H57), so there is no route through `/chat/turn` that exercises the queue.
    # Not core, not on any request path.
    "scripts/chatbot_queue_replay.py",
    # Chatbot memory lane A S2 (contract section 4/5): the contact's memory/facts
    # surface is a `user_management.contacts` route, so it cannot live under
    # `app/services/chatbot/`, yet its vocabulary, precedence and row-lock rules
    # are exactly `turn/profile_facts.py`'s own - reusing them directly is the
    # alternative to a second, drifting copy of that logic in core. The same
    # doorway shape as `app/api/v1/system/chatbot.py` above: a core surface that
    # happens to need one chatbot-module read/write path.
    "app/services/contact_service.py",
}
ALLOWED_PREFIXES = (
    "app/services/chatbot/",
    "app/modules/chatbot/",
    "tests/chatbot/",
)

# Four ways in, not two. `from app.services import chatbot` and an `importlib` call both
# reach the package without the dotted path ever appearing at the start of an import line,
# which is how a guardrail like this quietly stops guarding.
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+app\.services\.chatbot\b"
    r"|import\s+app\.services\.chatbot\b"
    r"|from\s+app\.services\s+import\s+(?:[^\n]*\b)?chatbot\b)"
    r"|import_module\(\s*['\"]app\.services\.chatbot"
    r"|__import__\(\s*['\"]app\.services\.chatbot",
    re.MULTILINE,
)


def _scanned_files() -> list[Path]:
    roots = [BACKEND_ROOT / "app", BACKEND_ROOT / "tests", BACKEND_ROOT / "scripts"]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(p for p in root.rglob("*.py") if "venv" not in p.parts)
    return files


def test_no_module_outside_the_boundary_imports_the_chatbot_package() -> None:
    offenders = []
    for path in _scanned_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in ALLOWED or rel.startswith(ALLOWED_PREFIXES):
            continue
        if _IMPORT_RE.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert not offenders, (
        "these files import app.services.chatbot from outside the module boundary: "
        + ", ".join(sorted(offenders))
        + " - route through app/api/v1/external/chat.py or app/tasks/chat_turns.py instead"
    )


def test_the_package_exports_only_its_public_entry_points() -> None:
    import app.services.chatbot as pkg

    assert set(pkg.__all__) <= {"run_turn", "complete_turn"}, (
        "app/services/chatbot/__init__.py must export run_turn / complete_turn only; "
        f"found {sorted(pkg.__all__)}"
    )


def test_the_endpoint_keeps_its_session_factory_patchable() -> None:
    """The engine's session seam must stay a MODULE-LEVEL name in `chat.py`.

    The engine takes a session FACTORY because it must not hold a session across the LLM
    call, so it cannot use the request's `Depends(get_db)` session. Whatever name that
    factory has in `chat.py` is the ONE thing a test patches to keep the engine off the
    shared prod-copy database - `is_test` suppresses writes, not the connection.

    Inline it (`session_factory=app.database.SessionLocal`) and there is nothing to patch:
    every endpoint test would keep passing while quietly writing to the real database. The
    failure would look like flaky unrelated tests, days later.
    """
    import app.api.v1.external.chat as chat_module

    source = (BACKEND_ROOT / "app" / "api" / "v1" / "external" / "chat.py").read_text(
        encoding="utf-8"
    )
    assert "session_factory=SessionLocal" in source, (
        "chat.py must pass the module-level `SessionLocal` name to run_turn; an inlined "
        "or attribute-qualified factory cannot be patched and sends test traffic to the "
        "shared database"
    )
    assert hasattr(chat_module, "SessionLocal"), (
        "app.api.v1.external.chat.SessionLocal is the patch target every endpoint test "
        "relies on"
    )
