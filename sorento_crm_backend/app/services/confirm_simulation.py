"""Board Simulate confirm (admin dry run): the guards that make "nothing is saved" a fact.

Plan `documentation/plans/scm/PLAN-board-simulate-confirm-12oct.md`. A simulation runs the
real Confirm path inside a savepoint that is always rolled back, so the risk is anything that
escapes it: a real root commit on another connection or a queued job. Both refuse loudly here
instead of leaking a write.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models.base import (
    get_brand_scope,
    get_company_scope,
    set_brand_scope,
    set_company_scope,
)

from app.services.error_handler import AppException

#: Cap on every error string a simulation hands back (AC-S8).
MAX_ERROR_CHARS = 1000

_SIMULATING: ContextVar[bool] = ContextVar("confirm_simulating", default=False)


class SimulationWouldCommit(Exception):
    """Something inside a simulated confirm tried to save or enqueue."""


def is_simulating() -> bool:
    return _SIMULATING.get()


@contextmanager
def simulating() -> Iterator[None]:
    token = _SIMULATING.set(True)
    try:
        yield
    finally:
        _SIMULATING.reset(token)


@event.listens_for(Engine, "commit")
def _refuse_real_commit(conn) -> None:  # noqa: ANN001
    """A REAL root commit on any connection while simulating. A savepoint release does not
    fire this event, so the simulation's own per-order commits pass; a drain that opens a
    fresh `SessionLocal()` and commits does not."""
    if is_simulating():
        # Raising from this event leaves SQLAlchemy believing the transaction is closed, so
        # the pool would take the connection back still inside its DBAPI transaction and the
        # next checkout would fail its ping. Roll the DBAPI connection back ourselves first.
        try:
            conn.connection.rollback()
        except Exception:  # noqa: BLE001 - the refusal below is what matters
            pass
        raise SimulationWouldCommit(
            "Simulation refused a commit: nothing may be saved during a simulated confirm."
        )


@contextmanager
def simulation_session(request_db: Session) -> Iterator[Session]:
    """A session whose commits are savepoint releases inside an outer savepoint that is
    ALWAYS rolled back, so the real Confirm code can run end to end and leave nothing.

    It rides the request session's own connection (not a new one): that is the connection
    that can see the caller's rows and carries its transaction-local settings. Company and
    brand scope are copied across; `confirm_preview` makes the post-commit drains skip.
    """
    conn = request_db.connection()
    outer = conn.begin_nested()
    sim = Session(
        bind=conn, autoflush=request_db.autoflush, join_transaction_mode="create_savepoint"
    )
    set_company_scope(sim, get_company_scope(request_db))
    set_brand_scope(sim, get_brand_scope(request_db))
    sim.info["confirm_preview"] = True
    try:
        yield sim
    finally:
        sim.close()
        if outer.is_active:
            outer.rollback()


def error_text(exc: BaseException) -> str:
    """The raw reason an order failed, capped. An `AppException` keeps its own message; any
    other exception leads with its class name, which is the whole point of a dry run."""
    if isinstance(exc, AppException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        text = detail.get("message") or str(exc)
    else:
        text = f"{type(exc).__name__}: {exc}"
    return text[:MAX_ERROR_CHARS]
