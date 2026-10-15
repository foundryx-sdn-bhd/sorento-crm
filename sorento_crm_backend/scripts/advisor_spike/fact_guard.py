"""Fact guard: every code, number and date in an advisor reply must come from tool results or the user's words.
Contract: PLAN-chat-advisor-spike-16oct.md "fact_guard.py". Pure, no DB."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH_RE = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
             r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE = re.compile(
    rf"(?P<iso>\b(\d{{4}})-(\d{{2}})-(\d{{2}})\b)"
    rf"|(?P<dmy>\b(\d{{1,2}})/(\d{{1,2}})/(\d{{4}})\b)"
    rf"|(?P<txt>\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\b(?:\s+(\d{{4}})\b)?)",
    re.IGNORECASE,
)
_ENUM = re.compile(r"(?m)^[ \t]*\d+[.)](?=\s)")
_RM = re.compile(r"\bRM\s?(?=\d)", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-/.][A-Za-z0-9]+)*")
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_UNIT_SUFFIX = re.compile(r"^(\d+(?:\.\d+)?)(?:pcs|pc|kg|mm|cm|sqm|sqft|ft|x|pax|m)$", re.IGNORECASE)


@dataclass
class GuardResult:
    ok: bool
    unsupported: list[str] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", "")).normalize()
    except InvalidOperation:
        return None


def _dates(text: str) -> list[tuple[int, str, tuple[int, int, int | None]]]:
    out = []
    for m in _DATE.finditer(text):
        if m.group("iso"):
            y, mo, d = int(m.group(2)), int(m.group(3)), int(m.group(4))
            out.append((m.start(), m.group(0), (mo, d, y)))
        elif m.group("dmy"):
            d, mo, y = int(m.group(6)), int(m.group(7)), int(m.group(8))
            out.append((m.start(), m.group(0), (mo, d, y)))
        else:
            d, mon = int(m.group(10)), _MONTHS[m.group(11)[:3].lower()]
            y = int(m.group(12)) if m.group(12) else None
            out.append((m.start(), m.group(0), (mon, d, y)))
    return out


def _is_code(tok: str) -> bool:
    return len(tok) >= 3 and any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok)


def check_reply(reply: str, evidence: list[str], user_texts: list[str]) -> GuardResult:
    pool = "\n".join(list(evidence) + list(user_texts))
    leaks: list[str] = []
    for m in _UUID.finditer(reply or ""):
        if m.group(0) not in leaks:
            leaks.append(m.group(0))
    text = _UUID.sub(" ", reply or "")
    pool_nouuid = pool

    found: list[tuple[int, str, str, object]] = []  # (pos, display, kind, key)

    # dates (masked out so their digits are not re-counted)
    ev_dates = [d[2] for d in _dates(pool_nouuid)]
    for pos, shown, (mo, d, y) in _dates(text):
        found.append((pos, shown, "date", (mo, d, y)))
    text = _DATE.sub(lambda m: " " * len(m.group(0)), text)
    text = _ENUM.sub(lambda m: " " * len(m.group(0)), text)
    text = _RM.sub(lambda m: " " * len(m.group(0)), text)

    # codes (masked out so their digits are not re-counted)
    ev_codes = {t.lower() for t in _TOKEN.findall(pool_nouuid) if _is_code(t)}
    ev_codes |= {t.lower().rstrip(".") for t in _TOKEN.findall(pool_nouuid)}
    masked = list(text)
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if not _is_code(tok):
            continue
        for i in range(m.start(), m.end()):
            masked[i] = " "
        u = _UNIT_SUFFIX.match(tok)
        if u:
            found.append((m.start(), u.group(1), "num", _dec(u.group(1))))
        else:
            found.append((m.start(), tok, "code", tok.lower()))
    text = "".join(masked)

    # numbers
    ev_nums: set[Decimal] = set()
    for m in _NUMBER.finditer(pool_nouuid):
        v = _dec(m.group(0))
        if v is not None:
            ev_nums.add(v)
        if "," in m.group(0):
            for piece in re.findall(r"\d+(?:\.\d+)?", m.group(0)):
                pv = _dec(piece)
                if pv is not None:
                    ev_nums.add(pv)
    for m in _NUMBER.finditer(text):
        found.append((m.start(), m.group(0), "num", _dec(m.group(0))))

    unsupported: list[str] = []
    seen: set[str] = set()
    for pos, shown, kind, key in sorted(found, key=lambda f: f[0]):
        if kind == "code":
            ok = key in ev_codes
        elif kind == "num":
            ok = key in ev_nums
        else:
            mo, d, y = key  # type: ignore[misc]
            ok = any(em == mo and ed == d and (y is None or ey == y) for em, ed, ey in ev_dates)
        if ok:
            continue
        label = shown.strip()
        if label.lower() not in seen:
            seen.add(label.lower())
            unsupported.append(label)
    return GuardResult(ok=not unsupported and not leaks, unsupported=unsupported, leaks=leaks)
