"""PROMPT_GATES: the one table of what a reveal grant switches off for a contact.

PARSER-PER-AUDIENCE (`documentation/plans/chatbot/PLAN-parser-per-audience-2oct.md`).
Keyed by `(domain, part)`, each row names the grant it needs, the prompt tag the parser
template wraps its text in, the block titles the Field reveals card shows, and the asks
refused without the grant. The prompt render and the refusal seams read this table, so the
trim and the refusal cannot drift. Hiding a block is a size and focus change; the refusal
in `run_fetch` / the engine stays the control.

A template marks a gated span `{{#only <tag>}}...{{/only}}`: on lines of their own for a
block, or around a span inside a shared line. `render_for_audience` keeps the span for a
contact holding the row's grant and removes it (markers included) otherwise. The inverse,
`{{#without <tag>}}...{{/without}}`, is kept only for a contact WITHOUT the grant: the
one-line stub that tells the parser a gated domain still exists (owner ruling 5 Oct 2026:
the trim strips the heavy blocks, never the classification vocabulary). An unknown,
nested or unbalanced marker keeps its text, drops the marker and logs a warning.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptGate:
    domain: str
    part: str | None
    grant: str
    tag: str
    blocks: tuple[str, ...]
    forbidden_terms: tuple[str, ...]
    refused_domains: tuple[str, ...] = ()
    refused_intents: tuple[str, ...] = ()
    refused_order_statuses: tuple[str, ...] = ()
    # The domain words that name this gate's domain or intent (word -> domain). The
    # registry lists keep them for every audience (owner ruling 5 Oct 2026: the trim never
    # strips classification vocabulary); this is the map, read by the field reveals card.
    words: tuple[str, ...] = ()


PROMPT_GATES: tuple[PromptGate, ...] = (
    PromptGate(
        domain="purchase_cost",
        part=None,
        grant="purchase_orders.cost",
        tag="purchase_cost",
        blocks=("LAST PURCHASE COST",),
        forbidden_terms=(
            "purchase_cost", "check_po_cost", "purchase cost", "last purchase cost",
            "LAST PURCHASE COST", "buying price", "cost price",
        ),
        refused_domains=("purchase_cost",),
        words=("purchase cost", "last purchase cost", "buying price", "cost price"),
    ),
    PromptGate(
        domain="purchase_order",
        part=None,
        grant="purchase_orders.placed",
        tag="purchase_order",
        blocks=("PURCHASE ORDERS",),
        forbidden_terms=(
            "purchase_order", "check_po", "purchase order", "PO", "PURCHASE ORDERS",
            "PO placed", "supplier order",
        ),
        refused_domains=("purchase_order",),
        words=("PO", "purchase order", "purchase orders", "supplier order"),
    ),
    PromptGate(
        domain="spo_allocation",
        part=None,
        grant="purchase_orders.placed",
        tag="spo_allocation",
        blocks=("SPO LAST RECEIPT",),
        forbidden_terms=("spo_allocation", "check_spo", "SPO LAST RECEIPT", "last received"),
        refused_domains=("spo_allocation",),
        words=("SPO", "last in", "last received"),
    ),
    PromptGate(
        domain="sales",
        part=None,
        grant="sales_orders.sales_report",
        tag="sales",
        blocks=("SALES REPORT", "SALES ANALYSIS", "TOP SELLING"),
        forbidden_terms=(
            "sales_report", "sales_analysis", "top_selling", "SALES REPORT", "SALES ANALYSIS",
            "TOP SELLING", "sales_channel", "rank_by", "sales_basis",
        ),
        refused_domains=("sales",),
        refused_order_statuses=("sales_report", "sales_analysis", "top_selling", "sales_ranking"),
        words=("sales report", "sales analysis", "top selling"),
    ),
    PromptGate(
        domain="inventory",
        part="low_stock_report",
        grant="scm.low_stock_report",
        tag="low_stock_report",
        blocks=("LOW STOCK REPORT",),
        forbidden_terms=("low_stock_report", "LOW STOCK REPORT", "reorder report"),
        refused_intents=("low_stock_report",),
        words=("low stock", "reorder report", "below level"),
    ),
)

_GRANT_BY_TAG = {g.tag: g.grant for g in PROMPT_GATES}


def gate_for(domain: str, part: str | None = None) -> PromptGate | None:
    for gate in PROMPT_GATES:
        if gate.domain == domain and gate.part == part:
            return gate
    return None


def grant_for_domain(domain: str | None) -> str | None:
    """The grant a whole domain is refused without, or None."""
    for gate in PROMPT_GATES:
        if domain and domain in gate.refused_domains:
            return gate.grant
    return None


def grant_for_intent(intent: str | None) -> str | None:
    for gate in PROMPT_GATES:
        if intent and intent in gate.refused_intents:
            return gate.grant
    return None


def grant_for_order_status(status: str | None) -> str | None:
    for gate in PROMPT_GATES:
        if status and status in gate.refused_order_statuses:
            return gate.grant
    return None


def hidden_tags(grants: Iterable[str] | None) -> frozenset[str]:
    """The tags a contact holding `grants` does not see (None is no grants): what makes two
    contacts' prompts differ, so the parser's prompt cache key carries it."""
    held = set(grants or ())
    return frozenset(g.tag for g in PROMPT_GATES if g.grant not in held)


def prompt_blocks_for(grant: str) -> list[str]:
    """Every block title a grant removes from the prompt, in table order."""
    return [title for gate in PROMPT_GATES if gate.grant == grant for title in gate.blocks]


_MARKER_RE = re.compile(
    r"\{\{\s*#(only|without)\s+([A-Za-z0-9_]+)\s*\}\}|\{\{\s*/(only|without)\s*\}\}"
)


@dataclass
class _Marker:
    start: int
    end: int
    tag: str | None  # None for a close marker
    kind: str = "only"  # "only" | "without"


def _line_bounds(text: str, marker: _Marker) -> tuple[int, int] | None:
    """(line start, line end including its newline) when the marker is alone on its line."""
    line_start = text.rfind("\n", 0, marker.start) + 1
    nl = text.find("\n", marker.end)
    line_end = len(text) if nl == -1 else nl + 1
    before = text[line_start : marker.start]
    after = text[marker.end : line_end].rstrip("\n")
    if before.strip() or after.strip():
        return None
    return line_start, line_end


def render_for_audience(template: str, grants: Iterable[str] | None) -> str:
    """The template as a contact holding `grants` is shown it. None means no grants."""
    if "{{" not in template or ("only" not in template and "without" not in template):
        return template
    held = set(grants or ())
    markers = [
        _Marker(m.start(), m.end(), m.group(2), m.group(1))
        if m.group(1)
        else _Marker(m.start(), m.end(), None, m.group(3))
        for m in _MARKER_RE.finditer(template)
    ]
    if not markers:
        return template

    # Pair opens with closes. A pair that holds, or sits in, another pair is nested.
    pairs: list[tuple[_Marker, _Marker]] = []
    stack: list[_Marker] = []
    keep: list[_Marker] = []  # markers to drop while keeping the text between
    nested: set[int] = set()
    for marker in markers:
        if marker.tag is not None:
            stack.append(marker)
            continue
        if not stack:
            logger.warning("prompt_gates: stray {{/%s}} at offset %s dropped", marker.kind, marker.start)
            keep.append(marker)
            continue
        opener = stack.pop()
        if opener.kind != marker.kind:
            logger.warning("prompt_gates: {{#%s}} closed by {{/%s}} keeps its block", opener.kind, marker.kind)
            keep.extend((opener, marker))
            continue
        pairs.append((opener, marker))
    for opener in stack:
        logger.warning("prompt_gates: unclosed {{#%s %s}} keeps its block", opener.kind, opener.tag)
        keep.append(opener)
    for a_open, a_close in pairs:
        for b_open, b_close in pairs:
            if a_open.start < b_open.start and b_close.end < a_close.end:
                nested.add(a_open.start)
                nested.add(b_open.start)
    deletions: list[tuple[int, int]] = []
    for opener, closer in pairs:
        if opener.start in nested:
            logger.warning("prompt_gates: nested {{#%s %s}} keeps its block", opener.kind, opener.tag)
            keep.extend((opener, closer))
        elif opener.tag not in _GRANT_BY_TAG:
            logger.warning("prompt_gates: unknown tag %r keeps its block", opener.tag)
            keep.extend((opener, closer))
        elif (_GRANT_BY_TAG[opener.tag] in held) == (opener.kind == "only"):
            keep.extend((opener, closer))
        else:
            open_line = _line_bounds(template, opener)
            close_line = _line_bounds(template, closer)
            if open_line and close_line:
                deletions.append((open_line[0], close_line[1]))
            else:
                deletions.append((opener.start, closer.end))
    for marker in keep:
        line = _line_bounds(template, marker)
        deletions.append(line if line else (marker.start, marker.end))

    out = template
    for start, end in sorted(set(deletions), reverse=True):
        out = out[:start] + out[end:]
    return out
