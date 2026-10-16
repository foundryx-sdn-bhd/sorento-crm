"""PROMPT-DIET stage 1 (AC1-AC14): pure text tests on the built parser prompt.

Red until `scripts/build_parser_diet.py` and its output file exist. No DB.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
V94 = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.v94.txt"
OUT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.diet-s1-20261016.txt"
RULE = "━" * 35

SKIPPED = {
    "C05", "C23", "C25", "C34", "C39a", "C39b", "C42a", "C42b", "C43a",
    "C43c", "C43d", "C44", "C45", "C47c", "C47d", "C53a", "C53b", "C57",
}
REMOVED_BANNERS = [
    "TWO MORE OUTPUT KEYS", "LAST PURCHASE COST", "SALES REPORT", "THE OPEN TASK",
    "SALES ANALYSIS", "SPECIFICATIONS", "QUANTITY", "KNOWN BRANDS", "TOP SELLING", "SELF REFERENCE",
]
KEPT_BANNERS = ["CURRENT DATE", "LOW STOCK REPORT FILTERS", "ESCALATION CONFIRMATION"]
MOVED_KEYS = [
    "group_by", "month", "year", "top_n", "sales_channel", "proceed_anyway", "sales_basis",
    "sales_company", "rank_by", "basis", "rank_group", "rank_direction", "self_reference",
]


def _diet():
    return importlib.import_module("scripts.build_parser_diet")


def _v94() -> str:
    return V94.read_text(encoding="utf-8")


def _out() -> str:
    if not OUT.exists():
        pytest.fail(f"built file missing: {OUT}")
    return OUT.read_text(encoding="utf-8")


def _banners(text: str) -> list[tuple[str, int]]:
    """(title, line index) for every title line sitting between two rule lines."""
    lines = text.split("\n")
    found = []
    for i in range(1, len(lines) - 1):
        if lines[i - 1].startswith("━") and lines[i + 1].startswith("━") and not lines[i].startswith("━"):
            found.append((lines[i].strip(), i))
    return found


def _titles(text: str) -> list[str]:
    return [t for t, _ in _banners(text)]


def _output_object(text: str) -> str:
    start = text.index("== OUTPUT (exactly these keys")
    end = text.index("Return exactly one JSON object.", start)
    return text[start:end]


# ---- AC1-AC3 ----

def test_ac1_build_reproduces_output_file() -> None:
    assert _diet().build(_v94()) == _out()


def test_ac2_missing_anchor_raises_naming_id(monkeypatch) -> None:
    mod = _diet()
    monkeypatch.setattr(mod, "STAGE1_EDITS", [("ZZ99", "no such anchor text xyzzy", "x")])
    with pytest.raises(ValueError, match="ZZ99"):
        mod.build(_v94())


def test_ac2_repeated_anchor_raises_naming_id(monkeypatch) -> None:
    mod = _diet()
    monkeypatch.setattr(mod, "STAGE1_EDITS", [("ZZ98", "the", "x")])
    with pytest.raises(ValueError, match="ZZ98"):
        mod.build(_v94())


def test_ac3_skipped_ids_exact() -> None:
    assert set(_diet().SKIPPED_ABLATION_IDS) == SKIPPED
    assert len(_diet().SKIPPED_ABLATION_IDS) == 18


def test_ac3_cuts_imported_not_copied() -> None:
    abl = importlib.import_module("scripts.build_parser_ablation")
    assert _diet().CUTS is abl.CUTS


def test_ac3_every_non_skipped_cut_applies_to_v94() -> None:
    mod = _diet()
    abl = importlib.import_module("scripts.build_parser_ablation")
    text = _v94()
    for cut_id, old, new in abl.CUTS:
        if cut_id in SKIPPED:
            continue
        assert text.count(old) == 1, f"cut {cut_id} does not match v94 exactly once"
        text = text.replace(old, new, 1)
    assert mod.SKIPPED_ABLATION_IDS  # module exists


def test_ac3_skipped_cut_that_starts_matching_raises(monkeypatch) -> None:
    mod = _diet()
    probe = "== CORRECTION / DOUBT =="  # occurs exactly once in v94
    cuts = list(mod.CUTS) + [("C05", probe, "")]
    monkeypatch.setattr(mod, "CUTS", cuts)
    with pytest.raises(ValueError, match="C05"):
        mod.build(_v94())


def test_ac3_non_skipped_cut_failing_raises(monkeypatch) -> None:
    mod = _diet()
    cuts = list(mod.CUTS) + [("C99", "anchor that is not in v94 qqq", "")]
    monkeypatch.setattr(mod, "CUTS", cuts)
    with pytest.raises(ValueError, match="C99"):
        mod.build(_v94())


# ---- AC4-AC8 ----

def test_ac4_current_date() -> None:
    out = _out()
    assert "before calling the MCP tool" not in out
    assert out.count("{{current_date}}") == 1


def test_ac7_reference_target_kept() -> None:
    v94, out = _v94(), _out()
    start = v94.index("== REFERENCE TARGET")
    end = v94.find("\n== ", start + 10)
    assert v94[start : end if end != -1 else len(v94)] in out
    line = next(l for l in v94.split("\n") if "reference_target" in l and l.lstrip().startswith('"reference_target"'))
    assert line in out
    assert out.count("see REFERENCE TARGET") == v94.count("see REFERENCE TARGET")


def test_ac8_bare_number_vs_typed_code() -> None:
    out = _out()
    assert "Never emit BOTH an entity and a reference_position for the same message." not in out
    assert "A CODE OR NAME FROM THE LIST IS ITS POSITION" in out
    assert "reference_positions AND the code or name in entities as usual" in out
    start = out.index("A CODE OR NAME FROM THE LIST IS ITS POSITION")
    section = out[start : out.index("\n== ", start + 10)].lower()
    assert "bare number" in section
    assert "reference_positions" in section
    assert (
        "A bare number reply (no code or name typed) gives reference_positions only "
        "and never also copies the picked option into entities."
    ) in out


# ---- AC9 ----

def test_ac9_missing_commas() -> None:
    out = _out()
    assert '"requested_attributes": [],' in out
    assert '"asks": [],' in out
    assert '"asks": [] one' not in out


# ---- AC11-AC14 ----

def test_ac11_find_filters_once_and_policy_block_identical() -> None:
    out, v94 = _out(), _v94()
    assert out.count("{{find_filters}}") == 1
    begin, end = "<<<CHATBOT POLICY BLOCKS>>>", "<<<END CHATBOT POLICY BLOCKS>>>"
    block = lambda t: t[t.index(begin) : t.index(end) + len(end)]
    assert block(out) == block(v94)


def test_ac12_required_keys_present() -> None:
    from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA

    out = _out()
    v94 = _v94()
    expected = [n for n in PARSE_OUTPUT_JSON_SCHEMA["required"] if f'"{n}"' in v94]
    assert expected
    for name in expected:
        assert f'"{name}"' in out, f"required key {name} lost"


def test_ac13_at_least_five_and_a_half_percent_shorter() -> None:
    assert len(_out()) <= 0.945 * len(_v94())


def test_ac14_no_dashes_in_new_text() -> None:
    edits = _diet().STAGE1_EDITS
    assert edits
    for edit_id, _old, new in edits:
        assert "\u2014" not in new and "\u2013" not in new, f"dash in edit {edit_id}"


def test_scope_held_edits_not_applied() -> None:
    v94, out = _v94(), _out()

    def section(text: str, heading: str) -> str:
        start = text.index(heading)
        end = text.find("\n== ", start + len(heading))
        return text[start : end if end != -1 else len(text)]

    for heading in ("== FLYER FLAG ==", "== CORRECTION / DOUBT =="):
        assert section(v94, heading) in out, f"{heading} section changed or removed"
    missing = [t for t in _titles(v94) if t not in _titles(out)]
    assert not missing, f"banner titles lost: {missing}"
    phrase = "IN ADDITION to the OUTPUT object"
    assert out.count(phrase) == v94.count(phrase)
