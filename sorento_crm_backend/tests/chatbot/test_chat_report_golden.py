"""CA-3 RED tests: the golden set for the chat report parser route (UAC-24).

`golden/chat_report_golden.json` holds synthetic messages (en / zh / ms / mixed) with the parser
fields each must produce. This file is the OFFLINE check: the file is well formed, covers enough
variety, and every expected value is one the schema can emit. The LIVE check (3 runs per message
against the provider) is a separate script run before the hand test, not pytest.

Red today because the schema has no `chat_report` object yet.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA

GOLDEN = Path(__file__).parent / "golden" / "chat_report_golden.json"
LANGUAGES = {"en", "zh", "ms", "mixed"}
MEASURES = {"asks", "failed", "failure_rate", "contacts"}


def _cases() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def _chat_report_props() -> dict:
    prop = PARSE_OUTPUT_JSON_SCHEMA["properties"]["chat_report"]
    obj = prop if "properties" in prop else next(b for b in prop["anyOf"] if b.get("type") == "object")
    return obj["properties"]


def test_the_golden_file_has_at_least_twelve_cases_with_unique_ids_and_messages() -> None:
    cases = _cases()
    assert len(cases) >= 12, len(cases)
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["message"] for c in cases}) == len(cases)


def test_every_case_has_the_expected_parser_fields() -> None:
    for case in _cases():
        assert set(case) >= {"id", "language", "message", "expect"}, case
        assert case["language"] in LANGUAGES, case
        expect = case["expect"]
        assert expect["domain_hint"] == "chat_analytics" and expect["intent_hint"] == "chat_report", case
        assert set(expect["chat_report"]) == {"group_by", "measure", "subject"}, case
        assert isinstance(expect["has_period"], bool), case
        assert expect["top_n"] is None or (isinstance(expect["top_n"], int) and 1 <= expect["top_n"] <= 1000), case


def test_the_set_covers_enough_languages_groupings_and_measures() -> None:
    cases = _cases()
    assert len({c["language"] for c in cases}) >= 3
    groupings = {c["expect"]["chat_report"]["group_by"] for c in cases}
    assert len(groupings - {None}) >= 5, groupings
    assert {c["expect"]["chat_report"]["measure"] for c in cases} == MEASURES


def test_the_set_covers_the_asking_back_cases() -> None:
    cases = _cases()
    assert any(c["expect"]["top_n"] is None and not c["expect"]["has_period"] for c in cases), "no count and no period"
    assert any(c["expect"]["top_n"] is None and c["expect"]["has_period"] for c in cases), "period but no count"
    assert any(c["expect"]["top_n"] and not c["expect"]["has_period"] for c in cases), "count but no period"
    assert any(c["expect"]["chat_report"]["subject"] for c in cases), "a subject filter"


def test_every_expected_value_is_in_the_schema_enums() -> None:
    props = _chat_report_props()
    group_by = {v for v in props["group_by"]["enum"]}
    measure = {v for v in props["measure"]["enum"]}
    for case in _cases():
        report = case["expect"]["chat_report"]
        assert report["group_by"] in group_by, case
        assert report["measure"] in measure, case


def test_the_golden_file_is_synthetic_and_dash_free() -> None:
    text = GOLDEN.read_text(encoding="utf-8")
    assert chr(0x2014) not in text and chr(0x2013) not in text
    assert not re.search(r"\+?\d[\d\s-]{8,}\d", text), "no phone-like numbers in a public repo"
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text), "no ids"
