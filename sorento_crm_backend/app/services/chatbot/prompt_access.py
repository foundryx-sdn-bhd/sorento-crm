"""Render a prompt for one contact's access (lane ACCESS-MODEL, slice S7).

Grammar: a block is `{{#only TAG}}` alone on a line ... `{{/only}}` alone on a line; an inline
span is `{{#only TAG}}span{{/only}}`. TAG is a domain name (granted iff in `access.domains`) or a
field/report key (granted iff in `access.attributes`); comma-separated tags grant if ANY is
granted. Untagged text always stays. Pure: no I/O.

Malformed input never raises and fails closed: an unknown tag, a missing close or an inline span
running over a line break drops the guarded content; a stray close marker is dropped; each logs
a warning.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_OPEN_LINE = re.compile(r"^\{\{#only ([^}]*)\}\}$")
_CLOSE_LINE = re.compile(r"^\{\{/only\}\}$")
_MARKER = re.compile(r"\{\{#only ([^}]*)\}\}|\{\{/only\}\}")


def _tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def _granted(tags: list[str], access: Any, known: frozenset[str]) -> bool:
    domains = getattr(access, "domains", frozenset())
    attributes = set(getattr(access, "attributes", ()))
    ok = False
    for tag in tags:
        if tag not in known:
            logger.warning("prompt_access: unknown tag %r, guarded content dropped", tag)
        elif tag in attributes or ("." not in tag and tag in domains):
            ok = True
    return ok


def render_for_access(text: str, access: Any, *, known_tags: Iterable[str]) -> str:
    known = frozenset(known_tags)

    # Inline spans may nest and may run over a line break (that fails closed): one stack of
    # "this span is granted" flags for the whole text; text is kept only while every open span
    # is granted, so an outer denied span drops everything inside it.
    spans: list[bool] = []

    def inline_line(line: str) -> str:
        kept: list[str] = []
        pos = 0
        for m in _MARKER.finditer(line):
            if all(spans):
                kept.append(line[pos : m.start()])
            pos = m.end()
            if m.group(1) is not None:
                spans.append(_granted(_tags(m.group(1)), access, known))
            elif spans:
                spans.pop()
            # a close with nothing open is a stray marker: dropped
        if all(spans):
            kept.append(line[pos:])
        if spans:
            logger.warning("prompt_access: inline {{#only}} not closed on its line, guarded content dropped")
        return "".join(kept)

    lines = text.split("\n")
    # Openers with no close drop everything to the end of the text: find them with a pre-pass.
    pending: list[int] = []
    for i, line in enumerate(lines):
        if _OPEN_LINE.match(line):
            pending.append(i)
        elif _CLOSE_LINE.match(line) and pending:
            pending.pop()
    unclosed = set(pending)

    out: list[str] = []
    # Each stack entry: whether this block (and all its parents) keeps its content.
    stack: list[bool] = []
    for i, line in enumerate(lines):
        open_m = _OPEN_LINE.match(line)
        if open_m:
            if i in unclosed:
                logger.warning("prompt_access: {{#only %s}} never closed, guarded content dropped", open_m.group(1))
                stack.append(False)
                continue
            parent = stack[-1] if stack else True
            stack.append(parent and _granted(_tags(open_m.group(1)), access, known))
            continue
        if _CLOSE_LINE.match(line):
            if stack:
                stack.pop()
            else:
                logger.warning("prompt_access: stray {{/only}} dropped")
            continue
        if stack and not stack[-1]:
            continue
        out.append(inline_line(line))
    if text.endswith("\n") and out and out[-1] != "":
        out.append("")  # an unclosed block swallowed the trailing newline
    return "\n".join(out)


def tag_index(text: str) -> dict[str, list[str]]:
    """Each tag -> the first line of every block it guards (for the admin screen)."""
    index: dict[str, list[str]] = {}
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = _OPEN_LINE.match(line)
        if not m:
            continue
        first = next((l for l in lines[i + 1 :] if not _OPEN_LINE.match(l) and not _CLOSE_LINE.match(l)), "")
        for tag in _tags(m.group(1)):
            index.setdefault(tag, []).append(first)
    return index
