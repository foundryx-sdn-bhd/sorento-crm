"""Battery loader, validator and coverage rules (UAC-1 to UAC-3). Pure, no database."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DOMAINS = ("stock", "eta", "attachments", "order_status", "outstanding", "reports",
           "promotions", "ideas", "handover", "mixed")
AUDIENCES = ("staff", "dealer", "dealer_nogrant", "unlinked")
SHAPES = ("refine", "reply_to_question", "number_pick", "yes_no", "free_text_reply", "topic_switch",
          "switch_back", "point_into_answer", "multi_product", "mixed_types", "qty_carried",
          "language_mix", "typo", "particle", "small_talk", "unclear", "permission")
KINDS = ("answer", "ask", "handover", "refuse", "any")
SOURCES = ("real", "improvised")

BATTERY_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "chatbot" / "pv2" / "battery"
_NOT_BATTERY = {"contacts.yaml", "calibration.yaml"}
_ID_RE = re.compile(r"^[a-z0-9-]+$")

MIN_CONVERSATIONS = 60
MIN_REAL = 15
MIN_PER_DOMAIN = 5
MIN_PER_AUDIENCE = 5
MIN_PER_SHAPE = 3


class BatteryError(ValueError):
    """A battery file that breaks the UAC-1 shape; the message names the file and conversation."""


@dataclass
class Turn:
    text: str
    expect: dict[str, Any]


@dataclass
class Conversation:
    id: str
    source: str
    origin: str | None
    domain: str
    audience: str
    shapes: list[str]
    turns: list[Turn]
    file: Any = field(default="")


def _fail(file: Path | str, cid: str | None, msg: str) -> BatteryError:
    where = f"{Path(str(file)).name}" + (f" [{cid}]" if cid else "")
    return BatteryError(f"{where}: {msg}")


def _parse_expect(file: Path, cid: str, n: int, expect: Any) -> dict[str, Any]:
    if not isinstance(expect, dict):
        raise _fail(file, cid, f"turn {n}: expect must be a mapping")
    if expect.get("kind") not in KINDS:
        raise _fail(file, cid, f"turn {n}: unknown kind {expect.get('kind')!r}, one of {KINDS}")
    if not str(expect.get("about") or "").strip():
        raise _fail(file, cid, f"turn {n}: expect.about is required")
    return expect


def _parse_conversation(file: Path, raw: dict[str, Any], file_domain: str | None) -> Conversation:
    cid = str(raw.get("id") or "")
    if not _ID_RE.match(cid):
        raise _fail(file, None, f"conversation id {cid!r} must match [a-z0-9-]+")
    domain = raw.get("domain") or file_domain
    if domain not in DOMAINS:
        raise _fail(file, cid, f"unknown domain {domain!r}, one of {DOMAINS}")
    audience = raw.get("audience")
    if audience not in AUDIENCES:
        raise _fail(file, cid, f"unknown audience {audience!r}, one of {AUDIENCES}")
    shapes = raw.get("shapes") or []
    if not shapes:
        raise _fail(file, cid, "at least one shape is required")
    for s in shapes:
        if s not in SHAPES:
            raise _fail(file, cid, f"unknown shape {s!r}")
    source = raw.get("source")
    if source not in SOURCES:
        raise _fail(file, cid, f"source must be one of {SOURCES}")
    origin = raw.get("origin")
    if source == "real" and not str(origin or "").strip():
        raise _fail(file, cid, "a real conversation needs an origin")
    raw_turns = raw.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise _fail(file, cid, "turns must be a non-empty list")
    turns: list[Turn] = []
    for n, t in enumerate(raw_turns, 1):
        if not isinstance(t, dict) or not str(t.get("text") or "").strip():
            raise _fail(file, cid, f"turn {n}: text is required")
        turns.append(Turn(str(t["text"]), _parse_expect(file, cid, n, t.get("expect"))))
    return Conversation(id=cid, source=source, origin=origin, domain=domain, audience=audience,
                        shapes=list(shapes), turns=turns, file=file)


def load_battery(directory: Path = BATTERY_DIR) -> list[Conversation]:
    directory = Path(directory)
    out: list[Conversation] = []
    seen: dict[str, str] = {}
    for file in sorted(directory.glob("*.yaml")):
        if file.name in _NOT_BATTERY:
            continue
        doc = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict) or not isinstance(doc.get("conversations"), list):
            raise _fail(file, None, "top level must hold a conversations list")
        for raw in doc["conversations"]:
            conv = _parse_conversation(file, raw if isinstance(raw, dict) else {}, doc.get("domain"))
            if conv.id in seen:
                raise _fail(file, conv.id, f"duplicate id (also in {seen[conv.id]})")
            seen[conv.id] = file.name
            out.append(conv)
    return out


def validate_coverage(convs: list[Conversation]) -> list[str]:
    """The UAC-1 coverage floor; an empty list means the battery is wide enough."""
    problems: list[str] = []
    if len(convs) < MIN_CONVERSATIONS:
        problems.append(f"need at least {MIN_CONVERSATIONS} conversations, have {len(convs)}")
    real = sum(1 for c in convs if c.source == "real")
    if real < MIN_REAL:
        problems.append(f"need at least {MIN_REAL} real conversations, have {real}")
    for d in DOMAINS:
        if d == "mixed":
            continue
        n = sum(1 for c in convs if c.domain == d)
        if n < MIN_PER_DOMAIN:
            problems.append(f"domain {d}: need at least {MIN_PER_DOMAIN}, have {n}")
    for a in AUDIENCES:
        n = sum(1 for c in convs if c.audience == a)
        if n < MIN_PER_AUDIENCE:
            problems.append(f"audience {a}: need at least {MIN_PER_AUDIENCE}, have {n}")
    for s in SHAPES:
        n = sum(1 for c in convs if s in c.shapes)
        if n < MIN_PER_SHAPE:
            problems.append(f"shape {s}: need at least {MIN_PER_SHAPE}, have {n}")
    return problems


def load_contacts(path: Path = BATTERY_DIR / "contacts.yaml") -> dict[str, str]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    block = doc.get("audiences") if isinstance(doc, dict) else None
    block = block if isinstance(block, dict) else {}
    out: dict[str, str] = {}
    for a in AUDIENCES:
        entry = block.get(a)
        rid = str((entry or {}).get("respond_id") or "").strip() if isinstance(entry, dict) else ""
        if not rid:
            raise BatteryError(f"{Path(path).name}: audience {a} has no respond_id")
        out[a] = rid
    return out


def load_calibration(path: Path = BATTERY_DIR / "calibration.yaml") -> list[dict[str, Any]]:
    rows = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    if not isinstance(rows, list):
        raise BatteryError(f"{Path(path).name}: top level must be a list")
    return rows
