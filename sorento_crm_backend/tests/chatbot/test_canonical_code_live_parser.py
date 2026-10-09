"""RESOLVE-SEMANTIC-FALLBACK: the LIVE parser fills `canonical_code` with the bare code
when a type / colour / brand word sits beside it (owner ruling, PR #1511 card Q1 = a).

The cases are the YAML file the owner's console check also runs
(`console_cases/2026-10-06-canonical-code-type-word.yaml`), graded with the console check's
own `_grade_parser` (imported from the script, never a second copy), against two texts:

  * BASE: `SEMANTIC_PARSER_PROMPT` without `CANONICAL_CODE_ADDENDUM`, expected RED on the
    target cases (xfail, not strict) and green on the controls;
  * PATCHED: the shipped `SEMANTIC_PARSER_PROMPT` (addendum included), expected GREEN on
    every case.

Each case runs RUNS times (default 3, `CANONICAL_CODE_RUNS`) and prints one line
`CANONICAL-CODE-EVAL case=<name> prompt=<base|patched> pass=<k>/<n>` for the plan's table.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker; model
`CANONICAL_CODE_LIVE_MODEL`, default `gpt-5.4-mini`, the production parser model; key
`OPENAI_API_KEY`, or a placeholder where an egress proxy injects it). Same pattern as
`test_low_stock_live_parser.py` and `test_rogue_convo_live_parser.py`.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

pytestmark = pytest.mark.live_external

TODAY = "Tuesday, 06 October 2026"
RUNS = int(os.environ.get("CANONICAL_CODE_RUNS", "3"))
HERE = Path(__file__).parent
SEED = HERE / "fixtures" / "prompt_blocks_seed.txt"
CASES = HERE / "console_cases" / "2026-10-06-canonical-code-type-word.yaml"
CONSOLE_CHECK = HERE.parents[1] / "scripts" / "chatbot_console_check.py"

BRANDS = [{"brand_name": "Sorento", "brand_code": "SRT"}, {"brand_name": "Mocha", "brand_code": "MCH"}]


def _console_check():
    spec = importlib.util.spec_from_file_location("chatbot_console_check", CONSOLE_CHECK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _cases() -> list[dict[str, Any]]:
    import yaml

    data = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    return [c for c in data["cases"] if isinstance(c, dict)]


def _system_text(label: str) -> str:
    from app.services.chatbot_parser_prompt import (
        BLOCKS_BEGIN,
        BLOCKS_END,
        CANONICAL_CODE_ADDENDUM,
        SEMANTIC_PARSER_PROMPT,
    )

    body = SEMANTIC_PARSER_PROMPT
    if label == "base":
        assert body.count(CANONICAL_CODE_ADDENDUM) == 1, "the addendum is stacked exactly once"
        body = body.replace(CANONICAL_CODE_ADDENDUM, "")
    blocks = SEED.read_text(encoding="utf-8")
    text = f"{body.rstrip()}\n\n{BLOCKS_BEGIN}\n{blocks}{BLOCKS_END}\n"
    return text.replace("{{current_date}}", TODAY)


def _config(label: str):
    from app.services.chatbot.head import parser

    return parser.ParserConfig(
        system_prompt=_system_text(label),
        prompt_version=0,
        provider="openai",
        model=os.environ.get("CANONICAL_CODE_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )


def _parse(label: str, message: str) -> dict[str, Any]:
    from app.services.chatbot.head import parser

    block = parser.build_user_block(
        previous_response="(none)",
        latest_user_message=message,
        pending_kind=None,
        brands=BRANDS,
    )
    out = dict(parser.parse(_config(label), block))
    print(json.dumps({
        "prompt": label, "message": message, "domain_hint": out.get("domain_hint"),
        "entities": [
            {k: e.get(k) for k in ("raw", "hint", "canonical_code", "quantity")}
            for e in (out.get("entities") or []) if isinstance(e, dict)
        ],
    }, ensure_ascii=False))
    return out


def _eval(label: str, case: dict[str, Any], grade: Callable[[dict, dict], list[str]]) -> None:
    wanted = (case.get("expect") or {}).get("parser") or {}
    failures: list[str] = []
    for _ in range(RUNS):
        out = _parse(label, str(case["text"]))
        bad = grade(wanted, out)
        if bad:
            failures.append("; ".join(bad))
    passed = RUNS - len(failures)
    print(f"CANONICAL-CODE-EVAL case={case['name']!r} prompt={label} pass={passed}/{RUNS}")
    assert not failures, f"{case['name']} on {label}: {passed}/{RUNS} passed; first failure: {failures[0]}"


_ALL = _cases()
TARGETS = [c for c in _ALL if not str(c["name"]).startswith("control")]
CONTROLS = [c for c in _ALL if str(c["name"]).startswith("control")]
BASE = pytest.param("base", marks=pytest.mark.xfail(reason="the base text has no rule for a word beside a code", strict=False))
PATCHED = pytest.param("patched")


@pytest.mark.parametrize("label", [BASE, PATCHED])
@pytest.mark.parametrize("case", TARGETS, ids=[c["name"] for c in TARGETS])
def test_a_word_beside_a_code_gives_the_bare_canonical_code(label: str, case: dict[str, Any]) -> None:
    _eval(label, case, _console_check()._grade_parser)


@pytest.mark.parametrize("label", ["base", "patched"])
@pytest.mark.parametrize("case", CONTROLS, ids=[c["name"] for c in CONTROLS])
def test_controls_hold_on_both_texts(label: str, case: dict[str, Any]) -> None:
    _eval(label, case, _console_check()._grade_parser)
