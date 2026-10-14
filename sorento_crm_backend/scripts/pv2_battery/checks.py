"""Deterministic per-turn checks (UAC-4). Pure functions; a failure here is the verdict."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_URL_RE = re.compile(r"https?://\S+|\S+@\S+\.\w+")
_UPPER_SNAKE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}\b")
_LOWER_SNAKE = re.compile(r"\b[a-z]{2,}(?:_[a-z]+)+\b(?!\.[A-Za-z0-9])")
_BRACES = re.compile(r"\{\{|\{[a-z_]+\}")
_BARE_NULL = re.compile(r"\b(?:None(?!\s+[a-z])|null|undefined)\b")
_GAP = re.compile(r"([A-Za-z0-9][A-Za-z0-9\-]{2,})\s*(?:has no|have no|has not|tiada|没有|沒有)\s*([^.。\n]*)", re.IGNORECASE)


@dataclass
class CheckFail:
    check: str
    detail: str


@dataclass
class TurnRecord:
    text: str
    reply: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "v2"
    pv2_event: bool = True
    prompt_version: str | None = None
    expected_version: str | None = None
    tool_errors: list[str] = field(default_factory=list)


def find_uuids(text: str) -> list[str]:
    return UUID_RE.findall(text or "")


def find_internal_keys(text: str) -> list[str]:
    bare = _URL_RE.sub(" ", text or "")
    found: list[str] = []
    for rx in (_UPPER_SNAKE, _LOWER_SNAKE, _BRACES, _BARE_NULL):
        found.extend(m.group(0) for m in rx.finditer(bare))
    return found


def sent_files(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in actions or []:
        if not isinstance(a, dict) or a.get("kind") != "send_attachments":
            continue
        for item in a.get("attachments_src") or a.get("attachments") or []:
            out.append(item if isinstance(item, dict) else {"url": str(item)})
    return out


def _has_text_message(actions: list[dict[str, Any]]) -> bool:
    return any(isinstance(a, dict) and a.get("kind") == "send_message" and str(a.get("text") or "").strip()
               for a in actions or [])


def run_checks(turn: TurnRecord, expect: dict[str, Any], prev_reply: str | None) -> list[CheckFail]:
    fails: list[CheckFail] = []
    reply = turn.reply or ""
    low = reply.lower()

    if find_uuids(reply):
        fails.append(CheckFail("uuid_leak", f"reply holds {find_uuids(reply)[0]}"))
    keys = find_internal_keys(reply)
    if keys:
        fails.append(CheckFail("internal_key_leak", f"reply holds {keys[0]!r}"))
    if not _has_text_message(turn.actions):
        fails.append(CheckFail("silent", "no send_message action with text"))

    files = sent_files(turn.actions)
    for m in _GAP.finditer(reply):
        code, types_part = m.group(1).lower(), m.group(2).lower()
        hit = next((f for f in files
                    if str(f.get("attachmentType") or "").strip()
                    and str(f["attachmentType"]).strip().lower() in types_part
                    and code in f"{f.get('filename') or ''} {f.get('url') or ''}".lower()), None)
        if hit is not None:
            fails.append(CheckFail("false_gap", f"says {m.group(1)} has no {hit['attachmentType']} "
                                                f"but {hit.get('filename') or hit.get('url')} was sent"))
            break

    if prev_reply is not None and not expect.get("allow_repeat") and reply.strip() == prev_reply.strip():
        fails.append(CheckFail("stuck", "same reply as the previous turn"))

    for item in expect.get("must") or []:
        if str(item).lower() not in low:
            fails.append(CheckFail("must", f"reply lacks {item!r}"))
    for group in expect.get("must_any") or []:
        if not any(str(item).lower() in low for item in group):
            fails.append(CheckFail("must_any", f"reply holds none of {list(group)!r}"))
    for item in expect.get("must_not") or []:
        if str(item).lower() in low:
            fails.append(CheckFail("must_not", f"reply holds {item!r}"))

    want = expect.get("attachments")
    if want is True and not files:
        fails.append(CheckFail("attachments", "files expected, none sent"))
    elif want is False and files:
        fails.append(CheckFail("attachments", f"no files expected, {len(files)} sent"))

    if turn.mode == "v2" and not turn.pv2_event:
        fails.append(CheckFail("v1_fallback", "v2 run but the trace has no pv2_turn event"))
    if turn.prompt_version and turn.expected_version and str(turn.prompt_version) != str(turn.expected_version):
        fails.append(CheckFail("version_mismatch",
                               f"ran {turn.prompt_version}, pinned {turn.expected_version}"))
    if turn.tool_errors:
        fails.append(CheckFail("tool_error", "; ".join(str(e) for e in turn.tool_errors)[:300]))
    return fails
