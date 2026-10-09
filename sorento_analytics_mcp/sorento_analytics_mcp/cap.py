"""The output cap (card 5.1): claude.ai drops tool results near 150,000 characters."""
from __future__ import annotations

import json
from typing import Any

MAX_OUTPUT_CHARS = 120_000


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def cap_text(text: str, limit: int | None = None) -> str:
    """``text`` unchanged when it fits; otherwise the largest list in the JSON is cut from the tail
    until it fits and the answer says ``truncated``. Non-JSON text is cut with a marker."""
    limit = limit or MAX_OUTPUT_CHARS
    if len(text) <= limit:
        return text
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, list):
        obj = {"items": obj}
    if isinstance(obj, dict):
        lists = [k for k, v in obj.items() if isinstance(v, list)]
        if lists:
            key = max(lists, key=lambda k: len(_dumps(obj[k])))
            full = obj[key]

            def build(n: int) -> dict:
                out = dict(obj)
                out[key] = full[:n]
                out["truncated"] = True
                out["truncated_note"] = f"Output cut at {n} of {len(full)} {key}; narrow the filters or page."
                if isinstance(obj.get("next_offset"), int):  # a chat page: resume where the cut fell
                    out["next_offset"] = obj["next_offset"] - len(full) + n
                return out

            lo, hi = 0, len(full)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(_dumps(build(mid))) <= limit:
                    lo = mid
                else:
                    hi = mid - 1
            if len(_dumps(build(lo))) <= limit:
                return _dumps(build(lo))
    marker = ' ...[truncated: output cut at the size limit]'
    return text[: limit - len(marker)] + marker


def count_rows(text: str) -> int | None:
    """Rows in a tool answer, for the audit row: a list, or the first of data/rows/items."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        for key in ("data", "rows", "items", "datasets"):
            if isinstance(obj.get(key), list):
                return len(obj[key])
    return None
