"""DISCONTINUED-ASK: the LIVE parser reads "is X discontinued?" (any phrasing, any language,
or a quoted stock line + "mean discontinued model?") as `master_products` with
`requested_attributes == ["discontinued"]` (PR #1535 card).

The cases are `console_cases/2026-10-08-discontinued-ask.yaml`, graded with the console
check's own `_grade_parser` (imported from the script, never a second copy), against the
shipped `SEMANTIC_PARSER_PROMPT`. No addendum exists for this lane: the vocabulary already
names "discontinued" (`chatbot_parser_prompt.py`, REQUESTED ATTRIBUTES), so this file is the
evidence that the reply layer can key on it. If a case is red, the fix is a prompt example
published as a new unlabelled version, and this file then grows the BASE/PATCHED split the
sibling files use.

Each case runs RUNS times (default 3, `DISCONTINUED_ASK_RUNS`) and prints one line
`DISCONTINUED-ASK-EVAL case=<name> pass=<k>/<n>`.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker; model
`DISCONTINUED_ASK_LIVE_MODEL`, default `gpt-5.4-mini`, the production parser model; key
`OPENAI_API_KEY`, or a placeholder where an egress proxy injects it). Same pattern as
`test_canonical_code_live_parser.py`.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.live_external

TODAY = "Thursday, 08 October 2026"
RUNS = int(os.environ.get("DISCONTINUED_ASK_RUNS", "3"))
HERE = Path(__file__).parent
SEED = HERE / "fixtures" / "prompt_blocks_seed.txt"
CASES = HERE / "console_cases" / "2026-10-08-discontinued-ask.yaml"
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


def _system_text() -> str:
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END, SEMANTIC_PARSER_PROMPT

    blocks = SEED.read_text(encoding="utf-8")
    text = f"{SEMANTIC_PARSER_PROMPT.rstrip()}\n\n{BLOCKS_BEGIN}\n{blocks}{BLOCKS_END}\n"
    return text.replace("{{current_date}}", TODAY)


def _config():
    from app.services.chatbot.head import parser

    return parser.ParserConfig(
        system_prompt=_system_text(),
        prompt_version=0,
        provider="openai",
        model=os.environ.get("DISCONTINUED_ASK_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )


def _parse(case: dict[str, Any]) -> dict[str, Any]:
    from app.services.chatbot.head import parser

    # The engine's own two-line message shape (`engine.build_latest_user_message`): the
    # typed text, then `reply to: <quoted bot text>` when the user quoted a message. The
    # quoted turn's focus is the Current subject (`engine._quoted_context`).
    message = f"{case['text']}\n" + (f"reply to: {case['quoted']}\n" if case.get("quoted") else "\n")
    focus = None
    if case.get("subject"):
        code = str(case["subject"])
        focus = SimpleNamespace(
            domains=["inventory"],
            customers=[],
            products=[{"raw": code, "name": code, "canonical_code": code}],
            document=None,
            status=None,
            date_window=None,
        )
    block = parser.build_user_block(
        previous_response=case.get("quoted") or "(none)",
        latest_user_message=message,
        pending_kind=None,
        brands=BRANDS,
        focus=focus,
    )
    out = dict(parser.parse(_config(), block))
    print(json.dumps({
        "case": case["name"], "domain_hint": out.get("domain_hint"),
        "requested_attributes": out.get("requested_attributes"),
        "entities": [
            {k: e.get(k) for k in ("raw", "hint", "canonical_code", "quantity")}
            for e in (out.get("entities") or []) if isinstance(e, dict)
        ],
    }, ensure_ascii=False))
    return out


_ALL = _cases()


@pytest.mark.parametrize("case", _ALL, ids=[c["name"] for c in _ALL])
def test_a_discontinued_question_reads_as_the_discontinued_attribute(case: dict[str, Any]) -> None:
    grade = _console_check()._grade_parser
    wanted = (case.get("expect") or {}).get("parser") or {}
    failures: list[str] = []
    for _ in range(RUNS):
        bad = grade(wanted, _parse(case))
        if bad:
            failures.append("; ".join(bad))
    passed = RUNS - len(failures)
    print(f"DISCONTINUED-ASK-EVAL case={case['name']!r} pass={passed}/{RUNS}")
    assert not failures, f"{case['name']}: {passed}/{RUNS} passed; first failure: {failures[0]}"
