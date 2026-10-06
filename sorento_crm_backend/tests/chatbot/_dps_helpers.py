"""DO-PERIOD-SKIP test helper: run a real `engine.run_turn` with `ctx.access.skip_period_questions`
set, over the shared `_run_turn` harness (which stubs `check_access` with a fixed dict)."""
from __future__ import annotations

from typing import Any


class SkipSwitchMonkeypatch:
    """Delegates to the real monkeypatch, but wraps any `check_access` stub so the access dict it
    returns carries `skip_period_questions`."""

    def __init__(self, monkeypatch: Any, skip: bool) -> None:
        self._mp = monkeypatch
        self._skip = skip

    def setattr(self, target: Any, name: Any = None, value: Any = None, *a: Any, **kw: Any) -> None:
        if name == "check_access":
            inner = value
            skip = self._skip

            def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
                out = dict(inner(*args, **kwargs))
                out["skip_period_questions"] = skip
                return out

            value = wrapped
        self._mp.setattr(target, name, value, *a, **kw)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._mp, item)
