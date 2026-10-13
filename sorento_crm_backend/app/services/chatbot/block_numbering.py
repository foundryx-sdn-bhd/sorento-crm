"""Block numbering for card replies: one 1..N run across every section of a reply."""
from __future__ import annotations

import re
from typing import Any

from app.services.chatbot import label_catalog

_NUMBERED_FIRST = re.compile(r"^\d+\. \*[^*\n]+:\*[ \t]*\S")
_LABEL_LINE = re.compile(r"^\*[^*\n]+:\*")
_HEAD_LABELS = ("Company", "Product Code", "Order Number", "SPO Number", "PO Number", "Shipment", "Container")


def _block_head(localizer: Any) -> re.Pattern[str]:
    words = label_catalog.label_words(*_HEAD_LABELS, localizer=localizer)
    return re.compile(r"^\*(?:" + "|".join(re.escape(w) for w in words) + r"):\*")


def is_block(paragraph: str, localizer: Any = None) -> bool:
    """A card block: a numbered label line followed by a label line, or an unnumbered one
    that opens with a known identity label and has more than one line. A picker, a history
    list, a one-line note and a report header are not blocks."""
    lines = paragraph.split("\n")
    if len(lines) < 2:
        return False
    if _NUMBERED_FIRST.match(lines[0]):
        return bool(_LABEL_LINE.match(lines[1]))
    return bool(_block_head(localizer).match(lines[0]))


def renumber(groups: list[list[str]], start: int | None = None, localizer: Any = None) -> list[list[str]]:
    """Number the block paragraphs across all `groups` when there is more than one, on from
    `start` (else the first block's own number, else 1, so a counted set's continuation page
    keeps its offset); a lone block, and every non-block paragraph, is left as it is."""
    if sum(1 for g in groups for p in g if is_block(p, localizer)) < 2:
        return groups
    if start is None:
        first = next(p for g in groups for p in g if is_block(p, localizer))
        found = re.match(r"^(\d+)\. ", first)
        start = int(found.group(1)) if found else 1
    n = start - 1
    out: list[list[str]] = []
    for g in groups:
        done: list[str] = []
        for p in g:
            if is_block(p, localizer):
                n += 1
                p = f"{n}. " + re.sub(r"^\d+\. ", "", p)
            done.append(p)
        out.append(done)
    return out
