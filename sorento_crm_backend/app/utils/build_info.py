"""The short sha of the code this process runs, read once at import.

Shared by `/health` (app.main) and the chatbot trace, so a turn names the same sha the
stack check reports. Importing app.main from the engine would be heavy and circular.
"""
from __future__ import annotations

from os import environ
from pathlib import Path


def _git_sha() -> str:
    """`GIT_SHA` (set by the container build) wins; else read the checkout beside this tree.

    Never per request: this shells out.
    """
    env_sha = (environ.get("GIT_SHA") or "").strip()
    if env_sha:
        return env_sha
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (out.stdout or "").strip() or "unknown"
    except Exception:  # noqa: BLE001 - a health probe never fails over its own metadata
        return "unknown"


GIT_SHA = _git_sha()
