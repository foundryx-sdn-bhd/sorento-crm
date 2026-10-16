"""PARSER-GEMINI-LITE console cases: the crew's conversation rule holds for every answer type.

Owner rule, 13 Oct 2026: every chatbot golden set / live-check file includes, for each
answer type a lane touches, (1) a follow-up that narrows the last answer (channel, brand,
period or measure), (2) a short reply to every question the bot asks ("sorento", "yes",
"2026"), and (3) one topic switch and back. This lane changes the parser's model path, so
the file covers every answer type whose schema field changed on Gemini (stock quantity,
product attribute asks, the sales ranking and report, escalation, profile statements).

The SHAPE is graded here and never calls a model. The cases themselves run by hand against
a crew test copy (`scripts/chatbot_console_check.py ... --live`), once per parser model.
Each turn names what it covers with a `covers:` tag the console runner ignores.
"""
from __future__ import annotations

import pathlib

import yaml

FIXTURE = (
    pathlib.Path(__file__).resolve().parent
    / "console_cases"
    / "2026-10-13-parser-gemini-lite.yaml"
)
TAGS = {"opening", "narrow", "short_reply", "switch", "back", "statement"}


def _cases() -> list[dict]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _turns(case: dict) -> list[dict]:
    return case["turns"] if "turns" in case else [case]


def test_every_turn_is_tagged_and_graded_positively():
    """A turn graded only by negatives passes on the generic error reply
    (`documentation/agents/chatbot-verification.md`)."""
    for case in _cases():
        assert case.get("live_only") is True, case["name"]
        assert case.get("answer_type"), case["name"]
        for turn in _turns(case):
            tags = set(turn.get("covers") or [])
            assert tags and tags <= TAGS, (case["name"], turn.get("text"), tags)
            expect = turn.get("expect") or {}
            assert expect.get("reply_contains") or expect.get("parser"), (case["name"], turn["text"])


def test_each_answer_type_narrows_answers_short_and_switches_and_back():
    by_type: dict[str, list[list[set[str]]]] = {}
    for case in _cases():
        by_type.setdefault(case["answer_type"], []).append(
            [set(turn.get("covers") or []) for turn in _turns(case)]
        )
    assert by_type
    for answer_type, cases in by_type.items():
        tags = [tag for case in cases for turn in case for tag in turn]
        if "statement" in tags:
            # A statement asks nothing and has no answer to narrow; it is graded on the
            # parser emission (the array branch the anyOf fix opened), switch and back.
            assert {"switch", "back"} <= set(tags), answer_type
            continue
        assert "narrow" in tags, f"{answer_type}: no follow-up narrows the last answer"
        assert "short_reply" in tags, f"{answer_type}: no short reply to a bot question"
        # The switch and the return are in ONE conversation, return after the switch.
        assert any(
            "switch" in {t for turn in case for t in turn}
            and [i for i, turn in enumerate(case) if "back" in turn]
            and min(i for i, turn in enumerate(case) if "switch" in turn)
            < max(i for i, turn in enumerate(case) if "back" in turn)
            for case in cases
        ), f"{answer_type}: no topic switch and back inside one conversation"


def test_the_owners_three_short_replies_are_each_sent():
    short = {
        str(turn["text"]).strip().lower()
        for case in _cases()
        for turn in _turns(case)
        if "short_reply" in (turn.get("covers") or [])
    }
    assert {"sorento", "yes", "2026"} <= short, short
    # Short means short: a word or a number, as a dealer types it.
    assert all(len(text.split()) <= 2 for text in short), short
