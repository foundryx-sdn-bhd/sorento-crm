"""STOCK-QTY-PARTIAL-FILL: the trap-specs parser eval file's SHAPE (AC-14).

The cases themselves hit the real parser LLM and are run by crew on the Mac, never in CI.
This pins what the grader needs: every case `live_only: true`, every graded turn carries
`text` and `expect.parser.entities` with a `spec_key` / `spec_value` per specification
entity, the #1509 control carries `expected_red_until`, and the two prod sentences of 6 Oct
are present verbatim.
"""
from __future__ import annotations

import pathlib

import yaml

FIXTURE = pathlib.Path(__file__).resolve().parent / "console_cases" / "2026-10-06-trap-specs.yaml"


def _load() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _turns(case: dict) -> list[dict]:
    return case["turns"] if "turns" in case else [case]


def test_the_file_parses_and_every_case_is_live_only() -> None:
    doc = _load()
    assert doc["contact"] == "550337739"
    assert len(doc["cases"]) == 10, [c.get("name") for c in doc["cases"]]
    for case in doc["cases"]:
        assert case.get("live_only") is True, case.get("name")
        assert case.get("cold") is True, case.get("name")


def test_every_graded_turn_names_its_spec_entities() -> None:
    for case in _load()["cases"]:
        graded = [t for t in _turns(case) if "expect" in t]
        assert graded, case.get("name")
        for turn in graded:
            assert turn.get("text"), case.get("name")
            parser = (turn.get("expect") or {}).get("parser") or {}
            entities = parser.get("entities")
            assert isinstance(entities, list) and entities, case.get("name")
            for entity in entities:
                assert entity.get("hint"), (case.get("name"), entity)
                if entity["hint"] == "specification":
                    assert entity.get("spec_key"), (case.get("name"), entity)
                    assert entity.get("spec_value") is not None, (case.get("name"), entity)


def test_the_two_prod_sentences_of_6_oct_expect_the_same_parse() -> None:
    by_text = {t["text"]: t for case in _load()["cases"] for t in _turns(case) if "expect" in t}
    lower = by_text["Any WC with s trap 300mm available?"]["expect"]["parser"]
    upper = by_text["Any WC with S trap 300mm available"]["expect"]["parser"]
    assert lower == upper
    specs = {e["spec_key"]: e["spec_value"] for e in lower["entities"] if e["hint"] == "specification"}
    assert specs == {"trap_type": "s_trap", "trap_length": 300}


def test_the_brand_sentence_is_the_1509_control() -> None:
    (case,) = [c for c in _load()["cases"] if c.get("text") == "FIND S TRAP 200MM SORENTO WC"]
    assert case["expected_red_until"] == "#1509"
    assert any(e.get("hint") == "brand" for e in case["expect"]["parser"]["entities"])


def test_no_numeric_spec_value_is_a_string() -> None:
    for case in _load()["cases"]:
        for turn in _turns(case):
            for entity in ((turn.get("expect") or {}).get("parser") or {}).get("entities") or []:
                if entity.get("spec_key") in ("trap_length", "dim_width"):
                    assert isinstance(entity.get("spec_value"), int), (case.get("name"), entity)
