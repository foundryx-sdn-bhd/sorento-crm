"""Shared helpers for the PROMPT-DIET red tests. Not a test file.

Everything here reads files and splits text with the TEST's own code, never with
`prompt_diet.parse_cards`, so a bug in the module under test cannot hide itself in the
expectation that grades it.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND.parent
V50 = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.v50.txt"
HEAD_DIR = BACKEND / "app" / "services" / "chatbot" / "head"
DATA_DIR = HEAD_DIR / "prompt_diet_data"
CORE_TXT = DATA_DIR / "core.txt"
CARDS_TXT = DATA_DIR / "cards.txt"
MODULE_PY = HEAD_DIR / "prompt_diet.py"
SEED_SCRIPT = BACKEND / "scripts" / "prompt_diet_seed_examples.py"
CUT_SCRIPT = BACKEND / "scripts" / "prompt_diet_cut.py"

HEADING = re.compile(r"^==+ .* ==+$")
CARD_MARKER = re.compile(r"^<<card:([A-Za-z0-9_]+)>>$")

# The 14 cards the plan's "Cut" table names.
CARD_NAMES = {
    "open_question",
    "escalation",
    "account",
    "master_products",
    "sales",
    "order",
    "promotion",
    "resource_attachment",
    "purchase_order",
    "spo_allocation",
    "purchase_cost",
    "inventory",
    "ideate",
    "product_attachment",
}


def split_sections(text: str) -> list[str]:
    """Heading line starts a section; the preamble is the text before the first heading.

    Each section is returned stripped of surrounding blank lines; empty ones are dropped
    (a card body has no preamble).
    """
    sections: list[list[str]] = [[]]
    for line in text.split("\n"):
        if HEADING.match(line):
            sections.append([line])
        else:
            sections[-1].append(line)
    out = ["\n".join(lines).strip() for lines in sections]
    return [s for s in out if s]


def split_cards(text: str) -> dict[str, str]:
    """`<<card:NAME>>` marker lines; text before the first marker is ignored."""
    cards: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        m = CARD_MARKER.match(line)
        if m:
            current = m.group(1)
            assert current not in cards, f"duplicate card {current!r} in cards.txt"
            cards[current] = []
        elif current is not None:
            cards[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in cards.items()}


def card_marker(name: str) -> str:
    """A line that appears in the card's text and nowhere else: its first heading line."""
    body = split_cards(CARDS_TXT.read_text(encoding="utf-8"))[name]
    first = body.splitlines()[0]
    assert HEADING.match(first), f"card {name!r} does not start with a section heading: {first!r}"
    return first
