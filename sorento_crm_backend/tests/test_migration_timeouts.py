"""HA-MEM-DEPLOY-GUARD: every migration runs under a bounded lock wait and statement time.

A migration queued behind a long transaction queues every later query on that table behind
itself (Postgres grants locks in order), and a slow statement held the 13 Oct deploy. So
`alembic/env.py` sets `lock_timeout` (default 5s) and `statement_timeout` (default 180s) on
the migration connection, env-overridable, and puts them back after every migration so a
migration's own `SET LOCAL` override never leaks into the next one.

These tests run the REAL env.py through the alembic CLI against a throwaway database, with
probe migrations in a temporary versions directory that record what they saw.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.utils.migration_timeouts import (
    DEFAULT_LOCK_TIMEOUT,
    DEFAULT_STATEMENT_TIMEOUT,
    migration_timeouts,
)

BACKEND = Path(__file__).resolve().parents[1]


# --- settings parsing ------------------------------------------------------------


def test_defaults_are_5s_lock_and_180s_statement(monkeypatch):
    monkeypatch.delenv("MIGRATION_LOCK_TIMEOUT", raising=False)
    monkeypatch.delenv("MIGRATION_STATEMENT_TIMEOUT", raising=False)
    assert (DEFAULT_LOCK_TIMEOUT, DEFAULT_STATEMENT_TIMEOUT) == ("5s", "180s")
    assert migration_timeouts() == ("5s", "180s")


def test_env_overrides_both(monkeypatch):
    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT", "2500ms")
    monkeypatch.setenv("MIGRATION_STATEMENT_TIMEOUT", "30min")
    assert migration_timeouts() == ("2500ms", "30min")


@pytest.mark.parametrize("bad", ["5s'; DROP TABLE users; --", "five", "-1s", "5 parsecs"])
def test_a_malformed_value_is_refused_not_interpolated(monkeypatch, bad):
    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT", bad)
    with pytest.raises(ValueError):
        migration_timeouts()


def test_zero_disables_explicitly(monkeypatch):
    monkeypatch.setenv("MIGRATION_STATEMENT_TIMEOUT", "0")
    assert migration_timeouts()[1] == "0"


# --- the real env.py against a throwaway database --------------------------------

PROBE_TABLE = "zz_migration_timeout_probe"


def _probe(rev: str, down: str | None, body: str) -> str:
    lines = [
        "from alembic import op",
        "",
        f"revision = {rev!r}",
        f"down_revision = {down!r}",
        "branch_labels = None",
        "depends_on = None",
        "",
        "",
        "def _record(tag):",
        "    op.execute(",
        f"        \"INSERT INTO {PROBE_TABLE} (tag, lock_t, stmt_t) VALUES (\"",
        "        f\"'{tag}', current_setting('lock_timeout'), current_setting('statement_timeout'))\"",
        "    )",
        "",
        "",
        "def upgrade():",
        textwrap.indent(textwrap.dedent(body).strip("\n"), "    "),
        "",
        "",
        "def downgrade():",
        "    pass",
        "",
    ]
    return "\n".join(lines)


@pytest.fixture
def scratch_db():
    """A database of its own, so probe revisions never touch the suite's alembic_version."""
    base = make_url(os.environ.get("DATABASE_URL") or _settings_url())
    name = f"zz_migtimeout_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name)
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _settings_url() -> str:
    from app.config import settings

    return settings.database_url


@pytest.fixture
def probes(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    ini = tmp_path / "alembic.ini"
    real = (BACKEND / "alembic.ini").read_text()
    real = real.replace("script_location = alembic", f"script_location = {BACKEND / 'alembic'}", 1)
    real = real.replace("[alembic]", f"[alembic]\nversion_locations = {versions}", 1)
    # alembic.ini carries an inline comment on this line, which alembic only parses (and
    # chokes on) once version_locations is set.
    real = re.sub(r"(?m)^version_path_separator = .*$", "version_path_separator = os", real)
    ini.write_text(real)
    return versions, ini


def _alembic(ini: Path, url, *args, extra_env=None, timeout=120):
    env = dict(os.environ)
    env.pop("SORENTO_ENV_FILE", None)
    rendered = url.render_as_string(hide_password=False)
    env.update({"DATABASE_URL": rendered, "DIRECT_URL": rendered})
    env.pop("MIGRATION_LOCK_TIMEOUT", None)
    env.pop("MIGRATION_STATEMENT_TIMEOUT", None)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _rows(url):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return {r.tag: (r.lock_t, r.stmt_t) for r in c.execute(text(f"SELECT * FROM {PROBE_TABLE}"))}
    finally:
        eng.dispose()


def test_migrations_run_under_the_defaults_and_an_override_does_not_leak(scratch_db, probes):
    versions, ini = probes
    (versions / "p1_probe.py").write_text(
        _probe(
            "p1_probe",
            None,
            f"""
            op.execute("CREATE TABLE {PROBE_TABLE} (tag text primary key, lock_t text, stmt_t text)")
            _record("p1_default")
            op.execute("SET LOCAL statement_timeout = '30min'")
            op.execute("SET LOCAL lock_timeout = '1min'")
            _record("p1_override")
            """,
        )
    )
    (versions / "p2_probe.py").write_text(_probe("p2_probe", "p1_probe", '_record("p2_after")'))
    r = _alembic(ini, scratch_db, "upgrade", "p2_probe")
    assert r.returncode == 0, r.stdout + r.stderr
    rows = _rows(scratch_db)
    assert rows["p1_default"] == ("5s", "3min")  # Postgres prints 180s as 3min
    assert rows["p1_override"] == ("1min", "30min"), "a migration can raise its own limits"
    assert rows["p2_after"] == ("5s", "3min"), "the override must not leak into the next migration"


def test_env_vars_change_the_defaults(scratch_db, probes):
    versions, ini = probes
    (versions / "p1_probe.py").write_text(
        _probe(
            "p1_probe",
            None,
            f"""
            op.execute("CREATE TABLE {PROBE_TABLE} (tag text primary key, lock_t text, stmt_t text)")
            _record("p1")
            """,
        )
    )
    r = _alembic(
        ini,
        scratch_db,
        "upgrade",
        "p1_probe",
        extra_env={"MIGRATION_LOCK_TIMEOUT": "750ms", "MIGRATION_STATEMENT_TIMEOUT": "42s"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert _rows(scratch_db)["p1"] == ("750ms", "42s")


def test_a_migration_blocked_on_a_lock_fails_instead_of_queueing(scratch_db, probes):
    """The failure mode the timeout exists for: an open transaction holds the table, the
    migration wants ACCESS EXCLUSIVE. Without lock_timeout it waits forever (and queues every
    other query on the table behind it); with it, the upgrade fails and the deploy aborts."""
    versions, ini = probes
    (versions / "p1_probe.py").write_text(
        _probe("p1_probe", None, 'op.execute("ALTER TABLE zz_busy ADD COLUMN extra int")')
    )
    eng = create_engine(scratch_db)
    holder = eng.connect()
    try:
        holder.execute(text("CREATE TABLE zz_busy (id int)"))
        holder.commit()
        holder.execute(text("SELECT * FROM zz_busy"))  # ACCESS SHARE, held: transaction open
        r = _alembic(ini, scratch_db, "upgrade", "p1_probe", extra_env={"MIGRATION_LOCK_TIMEOUT": "1s"}, timeout=60)
    finally:
        holder.rollback()
        holder.close()
        eng.dispose()
    assert r.returncode != 0
    assert "lock timeout" in (r.stdout + r.stderr).lower(), r.stderr[-2000:]
