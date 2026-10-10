"""AC-5, AC-6, AC-7 (file half): the cut is move-only and the data files are reproducible.

Red now: the `alembic/data` parser core and cards files and `scripts/prompt_diet_cut.py` do not exist, and
`prompt_diet.parse_cards` is not importable.
"""
from __future__ import annotations

import subprocess
import sys
from collections import Counter

import pytest

from tests.chatbot.prompt_diet._common import (
    BACKEND,
    CARD_NAMES,
    CARDS_TXT,
    CORE_TXT,
    CUT_SCRIPT,
    HEADING,
    V50,
    split_cards,
    split_sections,
)


def _heading(section: str) -> str:
    first = section.splitlines()[0]
    return first if HEADING.match(first) else "(preamble) " + first[:60]


def test_ac5_v50_has_87_sections() -> None:
    """The baseline pin: 86 headings plus the preamble."""
    assert len(split_sections(V50.read_text(encoding="utf-8"))) == 87


def test_ac5_every_v50_section_appears_exactly_once_across_core_and_cards() -> None:
    v50 = Counter(split_sections(V50.read_text(encoding="utf-8")))

    core_sections = split_sections(CORE_TXT.read_text(encoding="utf-8"))
    areas = [s for s in core_sections if s.splitlines()[0].strip() == "== AREAS =="]
    assert len(areas) == 1, "core carries exactly one added '== AREAS ==' block (the area list)"
    core_rest = [s for s in core_sections if s not in areas]

    card_sections: list[str] = []
    for name, body in split_cards(CARDS_TXT.read_text(encoding="utf-8")).items():
        sections = split_sections(body)
        assert sections, f"card {name!r} is empty"
        assert HEADING.match(sections[0].splitlines()[0]), f"card {name!r} has text before its first heading"
        card_sections.extend(sections)

    recovered = Counter(core_rest + card_sections)
    lost = [_heading(s) for s in (v50 - recovered).elements()]
    extra = [_heading(s) for s in (recovered - v50).elements()]
    assert not lost and not extra, f"move-only violated. lost or reworded: {lost}; added or duplicated: {extra}"


def test_ac5_the_area_list_names_the_retrievable_areas() -> None:
    core_sections = split_sections(CORE_TXT.read_text(encoding="utf-8"))
    block = next(s for s in core_sections if s.splitlines()[0].strip() == "== AREAS ==")
    for area in ("master_products", "sales", "order", "promotion", "inventory"):
        assert area in block


def test_ac5_the_cards_file_holds_exactly_the_planned_cards() -> None:
    assert set(split_cards(CARDS_TXT.read_text(encoding="utf-8"))) == CARD_NAMES


def test_ac5_decisive_domain_terms_is_kept_verbatim_in_core() -> None:
    """Move only: the section is NOT deleted this step (plan, 'Cut')."""
    core = split_sections(CORE_TXT.read_text(encoding="utf-8"))
    assert any(s.splitlines()[0].startswith("== DECISIVE DOMAIN TERMS") for s in core)


def test_ac7_the_cut_script_reproduces_the_committed_files(tmp_path) -> None:
    """The committed data files are the script's output, not hand edits."""
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(CUT_SCRIPT), "--src", str(V50), "--out-dir", str(out)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "chatbot_parser_core.v1.txt").read_text(encoding="utf-8") == CORE_TXT.read_text(encoding="utf-8")
    assert (out / "chatbot_parser_cards.v1.txt").read_text(encoding="utf-8") == CARDS_TXT.read_text(encoding="utf-8")


def test_ac6_parse_cards_returns_one_entry_per_marker() -> None:
    from app.services.chatbot.head import prompt_diet

    text = (
        "ignored preface\n"
        "<<card:alpha>>\n"
        "== A1 ==\nline a1\n"
        "<<card:beta>>\n"
        "== B1 ==\nline b1\n== B2 ==\nline b2\n"
    )
    cards = prompt_diet.parse_cards(text)
    assert set(cards) == {"alpha", "beta"}
    assert cards["alpha"].strip() == "== A1 ==\nline a1"
    assert cards["beta"].strip() == "== B1 ==\nline b1\n== B2 ==\nline b2"
    assert "ignored preface" not in "".join(cards.values())
    assert "<<card:" not in "".join(cards.values())


def test_ac6_duplicate_card_names_raise() -> None:
    from app.services.chatbot.head import prompt_diet

    with pytest.raises(ValueError):
        prompt_diet.parse_cards("<<card:alpha>>\none\n<<card:alpha>>\ntwo\n")


def test_ac6_parse_cards_matches_the_committed_cards_file() -> None:
    from app.services.chatbot.head import prompt_diet

    text = CARDS_TXT.read_text(encoding="utf-8")
    parsed = prompt_diet.parse_cards(text)
    assert {k: v.strip() for k, v in parsed.items()} == split_cards(text)
