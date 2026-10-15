"""Environment bootstrap for the spike: this worktree's env with the DB swapped, the DB guard, the dead proxy."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BACKEND_DIR.parent
#: Read ONLY for OPENAI_API_KEY (the dev key, suffix bG4A); the worktree's own key may be another one.
OPENAI_KEY_SOURCE = Path("/Users/tehjayson/Documents/foundryx/sorento_crm-pv2-fix/sorento_crm_backend/.env")
GENERATED_ENV = Path("/Users/tehjayson/Documents/foundryx/crew/state/eval/advisor-spike/.spike.env")
REQUIRED_DB = "sorento_cagent_stack"
SPIKE_REDIS_URL = "redis://127.0.0.1:1/0"  # dead on purpose: no shared index is ever touched; the chatbot path fails open
CRM_BASE_URL = "http://asgi.local"  # the in-process FastAPI app (httpx.ASGITransport); no listener involved
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


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def bootstrap() -> Path:
    """Build the process env from THIS worktree's .env with DATABASE_URL's db swapped to sorento_cagent_stack,
    the dev OpenAI key and a spike redis index; write it to a private generated file (no .env is edited) that
    SORENTO_ENV_FILE points at, so app.config and app.main both read it. Enforce the DB guard.
    Must run before the first `import app...`."""
    from urllib.parse import urlunparse

    base = _parse(BACKEND_DIR / ".env")
    u = urlparse(base["DATABASE_URL"])
    if u.hostname not in _LOOPBACK:
        raise SpikeGuardError(f"worktree DATABASE_URL host {u.hostname!r} is not localhost")
    base["DATABASE_URL"] = urlunparse(u._replace(path=f"/{REQUIRED_DB}"))
    if OPENAI_KEY_SOURCE.exists():
        k = _parse(OPENAI_KEY_SOURCE).get("OPENAI_API_KEY")
        if k:
            base["OPENAI_API_KEY"] = k
    base["REDIS_URL"] = SPIKE_REDIS_URL
    base["ENABLE_SCHEDULER"] = "false"
    for drop in ("AI_ASSISTANT_MCP_URL", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        base.pop(drop, None)
    GENERATED_ENV.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_ENV.write_text("\n".join(f"{k}={v}" for k, v in base.items()) + "\n", encoding="utf-8")
    GENERATED_ENV.chmod(0o600)
    from dotenv import load_dotenv

    os.environ["SORENTO_ENV_FILE"] = str(GENERATED_ENV)
    load_dotenv(GENERATED_ENV, override=True)
    check_database_url(os.environ.get("DATABASE_URL"))
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    return GENERATED_ENV


def harden_proxy() -> None:
    """Everything except localhost and the two model APIs goes to a dead proxy (nothing else leaves)."""
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        os.environ[key] = "http://127.0.0.1:9"
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = NO_PROXY_HOSTS
