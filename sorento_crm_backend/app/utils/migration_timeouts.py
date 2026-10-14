"""Lock and statement limits for every alembic migration (HA-MEM-DEPLOY-GUARD).

`alembic/env.py` applies them to the migration connection, and again after each migration,
so a migration's own override never leaks into the next one. Overriding inside one migration:

    op.execute("SET LOCAL statement_timeout = '30min'")   # transactional part
    op.execute("SET statement_timeout = '30min'")         # before an autocommit_block()

Deploy-wide: MIGRATION_LOCK_TIMEOUT / MIGRATION_STATEMENT_TIMEOUT in the backend env
("0" disables). Runbook: documentation/runbooks/memory-and-deploy-guards.md.
"""
from __future__ import annotations

import os
import re

DEFAULT_LOCK_TIMEOUT = "5s"
# Below blue/green's 4-minute health wait, so a runaway statement fails the migration with
# its own error instead of the deploy timing out around it.
DEFAULT_STATEMENT_TIMEOUT = "180s"

# The value is interpolated into SET (it cannot be a bind parameter), so only a plain
# Postgres duration gets through.
_DURATION = re.compile(r"^\d+(ms|s|min|h)?$")


def _read(name: str, default: str) -> str:
    value = (os.environ.get(name) or default).strip()
    if not _DURATION.fullmatch(value):
        raise ValueError(f"{name}={value!r} is not a Postgres duration like 5s, 2500ms, 30min or 0")
    return value


def migration_timeouts() -> tuple[str, str]:
    """(lock_timeout, statement_timeout) for migrations."""
    return (
        _read("MIGRATION_LOCK_TIMEOUT", DEFAULT_LOCK_TIMEOUT),
        _read("MIGRATION_STATEMENT_TIMEOUT", DEFAULT_STATEMENT_TIMEOUT),
    )


def apply_migration_timeouts(connection) -> None:
    """Session-level SET, so it also covers work in an autocommit_block(). In a transaction it
    replaces any SET LOCAL a previous migration made."""
    if connection.dialect.name != "postgresql":
        return
    lock, statement = migration_timeouts()
    connection.exec_driver_sql(f"SET lock_timeout = '{lock}'")
    connection.exec_driver_sql(f"SET statement_timeout = '{statement}'")


def reset_after_migration(ctx, step, heads, run_args) -> None:  # noqa: ARG001 - alembic hook signature
    """`on_version_apply` hook: back to the defaults after every migration step."""
    apply_migration_timeouts(ctx.connection)
