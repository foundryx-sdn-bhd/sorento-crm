"""Board Simulate confirm (admin dry run): the guards that make "nothing is saved" a fact.

Plan `documentation/plans/scm/PLAN-board-simulate-confirm-12oct.md`. A simulation runs the
real per-order confirm and rolls it back, so the risk is anything that escapes the rollback: a
root `commit()` or a queued job. Both refuse loudly here instead of leaking a write.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import event
from sqlalchemy.orm import Session

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


def _refuse_root_commit(session: Session) -> None:
    # `before_commit` also fires for a savepoint's release in some SQLAlchemy versions, and
    # a savepoint commit is legitimate inside the write; only a root commit is refused.
    if session.info.get("confirm_simulate") and not session.in_nested_transaction():
        raise SimulationWouldCommit(
            "Simulation refused a commit: nothing may be saved during a simulated confirm."
        )


def install_commit_tripwire(session: Session) -> None:
    """Idempotently arm `session` so a root commit raises while `info["confirm_simulate"]`."""
    if not event.contains(session, "before_commit", _refuse_root_commit):
        event.listen(session, "before_commit", _refuse_root_commit)


def error_text(exc: BaseException) -> str:
    """The raw reason an order failed, capped. An `AppException` keeps its own message; any
    other exception leads with its class name, which is the whole point of a dry run."""
    if isinstance(exc, AppException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        text = detail.get("message") or str(exc)
    else:
        text = f"{type(exc).__name__}: {exc}"
    return text[:MAX_ERROR_CHARS]
