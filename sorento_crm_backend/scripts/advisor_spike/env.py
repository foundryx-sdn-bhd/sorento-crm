"""Environment bootstrap for the spike: the dev stack's env file, the DB guard, the dead outbound proxy.

The worktree's own .env points at an older DB, so the spike reads the env file of the lane whose stack
sits on sorento_cagent_stack (read only; never edited). Override with ADVISOR_SPIKE_ENV_FILE.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_ENV_FILE = Path("/Users/tehjayson/Documents/foundryx/sorento_crm-pv2-fix/sorento_crm_backend/.env")
#: The running backend of that stack; the in-process MCP tools read it over HTTP (GETs only).
DEFAULT_CRM_BASE_URL = "http://localhost:8113"
REQUIRED_DB = "sorento_cagent_stack"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
NO_PROXY_HOSTS = "localhost,127.0.0.1,.localhost,api.openai.com,api.anthropic.com"


class SpikeGuardError(RuntimeError):
    pass


def check_database_url(url: str | None) -> None:
    p = urlparse(url or "")
    if p.hostname not in _LOOPBACK or (p.path or "").lstrip("/") != REQUIRED_DB:
        raise SpikeGuardError(
            f"refusing: DATABASE_URL must be localhost/{REQUIRED_DB}, got host={p.hostname!r} db={p.path!r}"
        )


def bootstrap(env_file: str | Path | None = None) -> Path:
    """Load the env file into os.environ (override), point app.config at it, enforce the DB guard.
    Must run before the first `import app...`."""
    path = Path(env_file or os.environ.get("ADVISOR_SPIKE_ENV_FILE") or DEFAULT_ENV_FILE)
    if not path.exists():
        raise SpikeGuardError(f"env file {path} does not exist")
    from dotenv import load_dotenv

    os.environ["SORENTO_ENV_FILE"] = str(path)
    load_dotenv(path, override=True)
    check_database_url(os.environ.get("DATABASE_URL"))
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    return path


def harden_proxy() -> None:
    """Everything except localhost and the two model APIs goes to a dead proxy (nothing else leaves)."""
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        os.environ[key] = "http://127.0.0.1:9"
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = NO_PROXY_HOSTS


def crm_base_url() -> str:
    return os.environ.get("ADVISOR_SPIKE_CRM_BASE_URL", DEFAULT_CRM_BASE_URL)
