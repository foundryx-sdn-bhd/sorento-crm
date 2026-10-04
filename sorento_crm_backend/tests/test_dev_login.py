"""DEV-LOGIN-BYPASS: the dev auto-login endpoints and every guard in front of them.

Plan: documentation/plans/identity/PLAN-dev-login-bypass-03oct.md (UAC alongside).

The happy path runs only when EVERY guard holds. Each kill test below turns exactly one
guard off and proves the route then answers the same plain 404 an absent route gives, so a
single dropped check fails a test instead of silently opening a passwordless sign-in.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.user import User
from app.services import dev_login
from tests._pg_fixture import blank_session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

LOCAL_BASE = "http://localhost:8123"
LOOPBACK_PEER = ("127.0.0.1", 50123)
SECRET = "s3cret-shared-with-the-fe-server"


@pytest.fixture()
def db_bind():
    with blank_session() as db:
        bind = db.get_bind()

        def _override():
            yield db

        app.dependency_overrides[get_db] = _override
        try:
            yield db, bind
        finally:
            app.dependency_overrides.pop(get_db, None)


def _client(*, base_url: str = LOCAL_BASE, peer=LOOPBACK_PEER, secret: str | None = SECRET) -> TestClient:
    """What the frontend SERVER sends: the shared secret header, from loopback, Host localhost."""
    c = TestClient(app, base_url=base_url, client=peer)
    if secret is not None:
        c.headers["X-Dev-Login-Secret"] = secret
    return c


def _make_user(bind, *, email: str, status: str = "ACTIVE", trashed: bool = False) -> str:
    db = Session(bind=bind, join_transaction_mode="create_savepoint")
    try:
        user = User(id=str(uuid.uuid4()), email=email, name=f"Dev {email.split('@')[0]}", status=status)
        if trashed:
            user.is_trashed = True
        db.add(user)
        db.commit()
        return str(user.id)
    finally:
        db.close()


@pytest.fixture()
def enabled(monkeypatch, db_bind):
    """Every guard on, two allowlisted ACTIVE users. Kill tests flip one thing off."""
    _db, bind = db_bind
    admin = f"devadmin-{uuid.uuid4().hex[:8]}@example.com"
    viewer = f"devview-{uuid.uuid4().hex[:8]}@example.com"
    _make_user(bind, email=admin)
    _make_user(bind, email=viewer)
    monkeypatch.setattr(settings, "dev_auto_login", True)
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "dev_auto_login_users", f"{admin}, {viewer.upper()}")
    monkeypatch.setattr(settings, "dev_auto_login_secret", SECRET)
    return {"admin": admin, "viewer": viewer, "bind": bind}


def _dev_login(client: TestClient, email: str):
    return client.post("/api/v1/auth/dev-login", json={"email": email})


# --------------------------------------------------------------------------- happy path


def test_dev_login_mints_a_real_session_that_authenticates(enabled):
    with _client() as c:
        res = _dev_login(c, enabled["admin"])
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["email"] == enabled["admin"]
        token = body["token"]
        assert token

        # The same opaque session the password login issues: get_current_user resolves it.
        me = c.get("/api/v1/test-auth", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text

    from app.models.user_session import UserSession

    db = Session(bind=enabled["bind"], join_transaction_mode="create_savepoint")
    try:
        row = db.query(UserSession).filter(UserSession.token == token).one()
        assert row.auth_method == "dev_login"
        assert row.rolling is True
    finally:
        db.close()


def test_dev_login_users_lists_only_the_allowlist_default_first(enabled):
    with _client() as c:
        res = c.get("/api/v1/auth/dev-login/users")
    assert res.status_code == 200, res.text
    emails = [u["email"] for u in res.json()["users"]]
    assert emails == [enabled["admin"], enabled["viewer"]]
    assert set(res.json()["users"][0]) == {"email", "name", "role_name"}


def test_email_match_is_case_insensitive(enabled):
    with _client() as c:
        assert _dev_login(c, enabled["viewer"].upper()).status_code == 200


@pytest.mark.parametrize("host", ["http://localhost:3101", "http://lane-a.localhost:8101", "http://127.0.0.1:8000"])
def test_each_local_host_form_is_accepted(enabled, host):
    with _client(base_url=host) as c:
        assert _dev_login(c, enabled["admin"]).status_code == 200


# --------------------------------------------------------------------------- kill tests


def _assert_404_both(c: TestClient, email: str):
    assert _dev_login(c, email).status_code == 404
    assert c.get("/api/v1/auth/dev-login/users").status_code == 404


def test_kill_flag_off(enabled, monkeypatch):
    monkeypatch.setattr(settings, "dev_auto_login", False)
    with _client() as c:
        _assert_404_both(c, enabled["admin"])


def test_flag_defaults_off(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("DEV_AUTO_LOGIN", raising=False)

    fresh = Settings(database_url="postgresql://x:x@localhost/x", jwt_secret="x", _env_file=None)
    assert fresh.dev_auto_login is False


@pytest.mark.parametrize("env", ["production", "PRODUCTION", "prod", "staging", "", "qa", "productionish"])
def test_kill_environment_not_allowlisted(enabled, monkeypatch, env):
    monkeypatch.setattr(settings, "environment", env)
    with _client() as c:
        _assert_404_both(c, enabled["admin"])


@pytest.mark.parametrize(
    "host",
    [
        "http://sorento.example.com",
        "http://localhost.evil.com",
        "http://evillocalhost",
        "http://127.0.0.2",
        "http://10.0.0.5:8000",
        "http://backend:8000",
    ],
)
def test_kill_host_not_local(enabled, host):
    with _client(base_url=host) as c:
        _assert_404_both(c, enabled["admin"])


@pytest.mark.parametrize("peer", [("10.0.0.7", 5000), ("172.18.0.3", 5000), ("testclient", 5000)])
def test_kill_peer_not_loopback(enabled, peer):
    with _client(peer=peer) as c:
        _assert_404_both(c, enabled["admin"])


def test_kill_forwarded_for_does_not_fake_loopback(enabled):
    with _client(peer=("10.0.0.7", 5000)) as c:
        res = c.post(
            "/api/v1/auth/dev-login",
            json={"email": enabled["admin"]},
            headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
        )
    assert res.status_code == 404


@pytest.mark.parametrize("presented", [None, "", "wrong-secret-but-long-enough", SECRET + "x", SECRET[:-1]])
def test_kill_secret_header_missing_or_wrong(enabled, presented):
    with _client(secret=presented) as c:
        _assert_404_both(c, enabled["admin"])


@pytest.mark.parametrize("configured", ["", "   ", "short-secret"])
def test_kill_secret_not_configured_on_the_backend(enabled, monkeypatch, configured):
    """An unset or short backend secret refuses even a request presenting that same value."""
    monkeypatch.setattr(settings, "dev_auto_login_secret", configured)
    with _client(secret=configured) as c:
        _assert_404_both(c, enabled["admin"])


def test_kill_browser_request_relayed_by_the_next_dev_rewrite(enabled):
    """Security review B1: `next dev` relays a browser's /api/v1 call with Host rewritten to the
    backend's localhost address, from a 127.0.0.1 peer, adding X-Forwarded-Host. The browser has
    no secret, so it fails; and even with the secret, X-Forwarded-Host alone refuses."""
    relayed = {"X-Forwarded-Host": "tehs-mac-mini.tailnet.ts.net:3101"}
    with _client(secret=None) as c:
        assert c.post("/api/v1/auth/dev-login", json={"email": enabled["admin"]}, headers=relayed).status_code == 404
        assert c.get("/api/v1/auth/dev-login/users", headers=relayed).status_code == 404
    with _client() as c:
        assert c.post("/api/v1/auth/dev-login", json={"email": enabled["admin"]}, headers=relayed).status_code == 404
        assert c.get("/api/v1/auth/dev-login/users", headers=relayed).status_code == 404
        # An HTTP/1.0 browser request with no Host reaches the backend with an EMPTY
        # X-Forwarded-Host (Next sends `req.headers.host || ''`); presence alone refuses.
        empty = {"X-Forwarded-Host": ""}
        assert c.post("/api/v1/auth/dev-login", json={"email": enabled["admin"]}, headers=empty).status_code == 404
        assert c.get("/api/v1/auth/dev-login/users", headers=empty).status_code == 404


def test_kill_email_not_allowlisted(enabled):
    other = f"notlisted-{uuid.uuid4().hex[:8]}@example.com"
    _make_user(enabled["bind"], email=other)
    with _client() as c:
        assert _dev_login(c, other).status_code == 404


def test_kill_empty_allowlist(enabled, monkeypatch):
    monkeypatch.setattr(settings, "dev_auto_login_users", "")
    with _client() as c:
        _assert_404_both(c, enabled["admin"])


@pytest.mark.parametrize("kind", ["inactive", "trashed"])
def test_kill_allowlisted_but_not_signable(enabled, monkeypatch, kind):
    email = f"{kind}-{uuid.uuid4().hex[:8]}@example.com"
    _make_user(enabled["bind"], email=email, status="INACTIVE" if kind == "inactive" else "ACTIVE",
               trashed=kind == "trashed")
    monkeypatch.setattr(settings, "dev_auto_login_users", f"{enabled['admin']},{email}")
    with _client() as c:
        assert _dev_login(c, email).status_code == 404
        listed = [u["email"] for u in c.get("/api/v1/auth/dev-login/users").json()["users"]]
    assert email not in listed


# --------------------------------------------------------------------------- guard unit truth table


@pytest.mark.parametrize(
    "host,ok",
    [
        ("localhost", True),
        ("localhost:3101", True),
        ("LocalHost:3101", True),
        ("lane-a.localhost:3101", True),
        ("127.0.0.1:8000", True),
        ("", False),
        (None, False),
        ("localhost.", False),
        ("localhost.evil.com", False),
        ("evil.com:localhost", False),
        ("a.b.localhost.com", False),
        ("[::1]:8000", False),
        ("0.0.0.0", False),
    ],
)
def test_is_local_host(host, ok):
    assert dev_login.is_local_host(host) is ok


@pytest.mark.parametrize(
    "ip,ok",
    [("127.0.0.1", True), ("::1", True), ("10.0.0.1", False), ("testclient", False), ("", False), (None, False)],
)
def test_is_loopback_peer(ip, ok):
    assert dev_login.is_loopback_peer(ip) is ok


# --------------------------------------------------------------------------- startup assertion

SAFE_START = dict(
    enabled=True,
    environment="development",
    environment_explicit=True,
    secret=SECRET,
    under_gunicorn=False,
    in_container=False,
)


@pytest.mark.parametrize("env", ["production", "prod", "staging", "", "qa"])
def test_startup_assertion_refuses_non_dev_environment(env):
    with pytest.raises(RuntimeError, match="DEV_AUTO_LOGIN"):
        dev_login.assert_safe_startup(**{**SAFE_START, "environment": env})


@pytest.mark.parametrize(
    "override",
    [
        {"environment_explicit": False},
        {"secret": ""},
        {"secret": "short"},
        {"under_gunicorn": True},
        {"in_container": True},
    ],
    ids=["environment_defaulted", "no_secret", "short_secret", "gunicorn", "container"],
)
def test_kill_startup_assertion_refuses_each_unsafe_run(override):
    """Security review S1: the default ENVIRONMENT ('development') must not count, and the
    production entrypoint (gunicorn, a container) can never run the bypass."""
    with pytest.raises(RuntimeError, match="DEV_AUTO_LOGIN"):
        dev_login.assert_safe_startup(**{**SAFE_START, **override})


def test_startup_assertion_passes_when_off_in_production():
    dev_login.assert_safe_startup(
        enabled=False, environment="production", environment_explicit=True,
        secret="", under_gunicorn=True, in_container=True,
    )


def test_startup_assertion_warns_loudly_when_active(caplog):
    with caplog.at_level("WARNING"):
        dev_login.assert_safe_startup(**SAFE_START)
    assert any("DEV_AUTO_LOGIN" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


def _hermetic_env(tmp_path, **extra: str) -> dict[str, str]:
    """A child env that ignores the developer's own .env: SORENTO_ENV_FILE points Settings and
    app.main at a file holding only the required connection settings, and no ENVIRONMENT or
    DEV_AUTO_LOGIN* value is inherited from this process."""
    env_file = tmp_path / ".env.dev-login-test"
    env_file.write_text(
        f"DATABASE_URL={settings.database_url}\nJWT_SECRET=dev-login-test\n", encoding="utf-8"
    )
    env = {
        k: v for k, v in os.environ.items()
        if k.upper() != "ENVIRONMENT" and not k.upper().startswith("DEV_AUTO_LOGIN")
    }
    env.update({"SORENTO_ENV_FILE": str(env_file), "DEV_AUTO_LOGIN": "true", "DEV_AUTO_LOGIN_SECRET": SECRET})
    env.update(extra)
    return env


def test_kill_app_import_crashes_with_flag_in_production(tmp_path):
    """The real process, not the helper: importing app.main must die."""
    env = _hermetic_env(tmp_path, ENVIRONMENT="production")
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode != 0
    assert "DEV_AUTO_LOGIN" in (proc.stderr + proc.stdout)


def test_kill_app_import_crashes_with_flag_and_defaulted_environment(tmp_path):
    env = _hermetic_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode != 0
    assert "ENVIRONMENT is not set explicitly" in (proc.stderr + proc.stdout)


def test_running_under_gunicorn_reads_sys_modules(monkeypatch):
    monkeypatch.delitem(sys.modules, "gunicorn", raising=False)
    assert dev_login.running_under_gunicorn() is False
    monkeypatch.setitem(sys.modules, "gunicorn", object())
    assert dev_login.running_under_gunicorn() is True


@pytest.mark.parametrize("marker", ["/.dockerenv", "/run/.containerenv"])
def test_running_in_container_reads_the_marker_files(monkeypatch, marker):
    monkeypatch.setattr(dev_login.os.path, "exists", lambda p: p == marker)
    assert dev_login.running_in_container() is True
    monkeypatch.setattr(dev_login.os.path, "exists", lambda p: False)
    assert dev_login.running_in_container() is False


def test_kill_app_import_crashes_under_gunicorn(tmp_path):
    """Reviewer R1: app.main must actually hand the gunicorn detection to the assertion.
    Otherwise-safe dev settings, but gunicorn already imported, as under start.sh's preload."""
    proc = subprocess.run(
        [sys.executable, "-c", "import gunicorn, app.main"],
        cwd=BACKEND_ROOT,
        env=_hermetic_env(tmp_path, ENVIRONMENT="development"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode != 0
    assert "runs under gunicorn" in (proc.stderr + proc.stdout)


def test_kill_app_import_crashes_in_a_container(tmp_path):
    """Reviewer R1: same for the container check, with the marker file faked in the child."""
    code = (
        "import os; _real = os.path.exists; "
        "os.path.exists = lambda p: p == '/.dockerenv' or _real(p); "
        "import app.main"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        env=_hermetic_env(tmp_path, ENVIRONMENT="development"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode != 0
    assert "runs inside a container" in (proc.stderr + proc.stdout)


def test_app_import_succeeds_for_a_safe_local_dev_run(tmp_path):
    """The control: the same hermetic child with nothing unsafe starts fine, so the two kill
    tests above fail for the reason they name."""
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_ROOT,
        env=_hermetic_env(tmp_path, ENVIRONMENT="development"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "DEV_AUTO_LOGIN is ACTIVE" in (proc.stderr + proc.stdout)


# --------------------------------------------------------------------------- deploy guard


def _deploy_files() -> list[Path]:
    files: list[Path] = []
    files += sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
    files += sorted(REPO_ROOT.glob("**/docker-compose*.y*ml"))
    files += sorted(REPO_ROOT.glob("**/Dockerfile*"))
    for pattern in ("**/.env.production*", "**/.env.prod*", "**/.env.staging*"):
        files += sorted(REPO_ROOT.glob(pattern))
    files += sorted((REPO_ROOT / "scripts").glob("*deploy*"))
    files += sorted((REPO_ROOT / "sorento_crm").glob("*.sh"))
    files += [
        BACKEND_ROOT / "start.sh",
        BACKEND_ROOT / "run.sh",
        BACKEND_ROOT / "gunicorn.conf.py",
    ]
    # Every committed env file: none of them is a crew test copy's (crew sets the flag only
    # through its own env_overrides, outside the repo).
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.splitlines()
    files += [REPO_ROOT / t for t in tracked if Path(t).name.startswith(".env")]
    skip = ("node_modules", "/venv/", "/.next/")
    return [f for f in dict.fromkeys(files) if f.is_file() and not any(s in str(f) for s in skip)]


def test_deploy_files_never_mention_the_flag():
    files = _deploy_files()
    assert any(f.name == "docker-compose.yml" for f in files)
    assert any(f.parent.name == "workflows" for f in files)
    names = {f.name for f in files}
    assert {"blue_green_deploy.sh", "start.sh", "gunicorn.conf.py", ".env.browse"} <= names, names
    offenders = [str(f.relative_to(REPO_ROOT)) for f in files if re.search(r"DEV_AUTO_LOGIN", f.read_text(errors="ignore"))]
    assert offenders == [], f"DEV_AUTO_LOGIN must never be set by a deploy/CI file: {offenders}"
