"""Unknown fields on the external ingest are DROPPED, never refused (owner decision, 1 Oct 2026).

The shared service maps a new AutoCount column the day it ships, and Sorento may not know the
field yet. Under `extra="forbid"` that failed every record of the entity until both repos
deployed in a fixed order. Every canonical ingest schema now takes its config from here:

* `extra="ignore"`: the record is ingested from the keys Sorento declares. Dropping is not
  writing - an undeclared key reaches no column, so a payload naming a CRM-owned annotation
  still cannot set it.
* `note_unknown_fields`: the NAMES of the dropped keys (never their values) are collected for
  the current request, and the ingest route logs them once (`collect_unknown_fields`).

Known fields stay strict: a wrong type on a declared field still fails the record.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional, Set

from pydantic import ConfigDict

INGEST_MODEL_CONFIG = ConfigDict(extra="ignore", str_strip_whitespace=True)

_unknown: ContextVar[Optional[Set[str]]] = ContextVar("ingest_unknown_fields", default=None)


def note_unknown_fields(model: Any, data: Any) -> None:
    """Record `data`'s keys that `model` does not declare, as `Model.key`, when a request is
    collecting. Called from each schema's own `mode="before"` validator."""
    seen = _unknown.get()
    if seen is None or not isinstance(data, dict):
        return
    known = set(model.model_fields)
    known |= {f.alias for f in model.model_fields.values() if f.alias}
    for key in data:
        if key not in known:
            seen.add(f"{model.__name__}.{key}")


@contextmanager
def collect_unknown_fields() -> Iterator[Set[str]]:
    """Collect the unknown field names noted while the block runs (one ingest request)."""
    seen: Set[str] = set()
    token = _unknown.set(seen)
    try:
        yield seen
    finally:
        _unknown.reset(token)
