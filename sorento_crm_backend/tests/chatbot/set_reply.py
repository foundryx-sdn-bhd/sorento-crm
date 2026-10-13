"""Readers for a counted-set reply, shared by the set tests.

Round 4 on PR #833 changed the reply's shape: R2 put each filter on its own bold line
("*Brand:* Sorento", "*Product type:* Wash basin") above the count sentence, and R3 made a
row "N. <name> (<code>)" plus at most one line of facts. The round 1 to 3 tests assert on
what the reply SAYS (the filters, the count, the codes); these readers let them keep
saying it in the words they were written in, read off the new layout.
"""
from __future__ import annotations

import re

_FILTER_RE = re.compile(r"^\*([^*]+):\* (.+)$")
_ROW_RE = re.compile(r"^(\d+)\. (?:(.+) \((\S+)\)|(\S+))(?: \*\(Discontinued\)\*)?$")
#: Round 8 on PR #833: a set row IS the product-code row ("1. *Product Code:* GB3006C").
_CODE_ROW_RE = re.compile(r"^(?:(\d+)\. )?\*Product Code:\* (\S+)")


def legacy_lines(text: str) -> list[str]:
    """The reply's lines with the header block read as ONE line again: "Brand: Sorento,
    Product type: Wash basin. 3 wash basins have stock." (round 2/3 wording). A reply with
    no filter lines is its lines unchanged."""
    lines = (text or "").splitlines()
    filters: list[str] = []
    i = 0
    while i < len(lines):
        m = _FILTER_RE.match(lines[i])
        if not m:
            break
        filters.append(f"{m.group(1)}: {m.group(2)}")
        i += 1
    if not filters or i >= len(lines):
        return lines
    return [f"{', '.join(filters)}. {lines[i]}", *lines[i + 1 :]]


def one_line_header(text: str) -> str:
    lines = legacy_lines(text)
    return lines[0] if lines else ""


def row_codes(text: str) -> list[str]:
    """The product codes of the rows, in order."""
    out = []
    for line in (text or "").splitlines():
        code_row = _CODE_ROW_RE.match(line)
        if code_row:
            out.append(code_row.group(2))
            continue
        m = _ROW_RE.match(line)
        if m:
            out.append(m.group(3) or m.group(4))
    return out


def row_blocks(text: str) -> list[list[str]]:
    """Each row as its lines: line 1 "N. name (code)", then its facts line, if any."""
    out: list[list[str]] = []
    current: list[str] | None = None
    for line in (text or "").splitlines():
        if _CODE_ROW_RE.match(line) or _ROW_RE.match(line):
            current = [line]
            out.append(current)
        elif current is not None and line.strip():
            current.append(line)
        else:
            current = None
    return out


#: A snake_case token: lowercase words joined by "_", not part of a file name, a path, an
#: address or a code (those are bounded by ".", "/", "@" or "-"). Round 4 R7 on PR #833:
#: no stored value, domain key or field key ever reaches a reply.
SNAKE_RE = re.compile(r"(?<![\w./@-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w./@-])")


def snake_tokens(text: str) -> list[str]:
    return SNAKE_RE.findall(text or "")


_PRODUCT_CODE_RE = re.compile(r"^(?:\d+\. )?\*Product Code:\* (\S+)")


def full_row_codes(text: str) -> list[str]:
    """The product codes of a set answer in the normal (full field) structure, one per
    row: the "*Product Code:*" line each row opens with. Round 7 on PR #833 (owner hand
    test of rounds 4 to 6, items 1 and 7): an incoming, certificate or attachment set
    renders its rows the way the normal ETA or attachment ask does."""
    return [m.group(1) for m in (_PRODUCT_CODE_RE.match(line) for line in (text or "").splitlines()) if m]
