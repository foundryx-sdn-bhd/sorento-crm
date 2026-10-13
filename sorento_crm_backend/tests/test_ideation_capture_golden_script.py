"""IDEA-CAPTURE: structural check of the golden script's multi-turn SEQUENCES (owner rule: every
chatbot golden set covers a narrowing follow-up, a short reply to every question the bot asks,
and one topic switch and back). The script is loaded by path; no LLM is called.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

SHORT_REPLIES = {"sorento", "yes", "2026"}


@pytest.fixture(scope="module")
def sequences() -> list[tuple[str, list[dict]]]:
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ideation_capture_golden.py"
    spec = importlib.util.spec_from_file_location("_ideation_golden_sequences_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seqs = getattr(mod, "SEQUENCES", None)
    assert seqs, "SEQUENCES missing or empty"
    return seqs


def _norm(text: str) -> str:
    return (text or "").strip().strip(".,!?").lower()


def test_sequences_are_well_formed(sequences):
    names = [name for name, _turns in sequences]
    assert len(set(names)) == len(names)
    for name, turns in sequences:
        assert isinstance(name, str) and name and turns, name
        for turn in turns:
            assert isinstance(turn["text"], str) and turn["text"].strip(), (name, turn)
            assert turn.get("lane", "ideate") in ("ideate", "other"), (name, turn)
            assert isinstance(turn.get("ask_reply", False), bool), (name, turn)
            assert isinstance(turn.get("similar", []), list), (name, turn)


@pytest.mark.parametrize("word", sorted(SHORT_REPLIES))
def test_each_short_reply_answers_the_ask_back(sequences, word):
    assert any(
        t.get("ask_reply") is True and _norm(t["text"]) == word
        for _n, turns in sequences
        for t in turns
    ), f"no ask_reply turn {word!r}"


def _after_offer_replies(sequences) -> list[str]:
    out = []
    for _n, turns in sequences:
        for i, t in enumerate(turns[:-1]):
            if t.get("similar"):
                nxt = turns[i + 1]
                if nxt.get("lane", "ideate") == "ideate":
                    out.append(_norm(nxt["text"]))
    return out


def test_similar_offer_is_answered_by_a_short_reply(sequences):
    assert set(_after_offer_replies(sequences)) & SHORT_REPLIES


def test_similar_offer_is_answered_by_a_valid_number(sequences):
    found = False
    for _n, turns in sequences:
        for i, t in enumerate(turns[:-1]):
            reply = _norm(turns[i + 1]["text"])
            if t.get("similar") and reply.isdigit() and 1 <= int(reply) <= len(t["similar"]):
                found = True
    assert found


def test_similar_offer_is_answered_by_new(sequences):
    assert "new" in _after_offer_replies(sequences)


def test_a_sequence_narrows_a_created_idea(sequences):
    ok = [
        n for n, turns in sequences
        if "narrow" in n.lower()
        and len(turns) >= 2
        and not turns[0].get("similar")
        and turns[0].get("lane", "ideate") == "ideate"
        and turns[1].get("lane", "ideate") == "ideate"
    ]
    assert ok, "no 'narrow' sequence: an idea, then a follow-up turn that narrows it"


def test_a_sequence_switches_topic_and_comes_back(sequences):
    ok = []
    for n, turns in sequences:
        lanes = [t.get("lane", "ideate") for t in turns]
        if "switch" in n.lower() and "other" in lanes:
            first_other = lanes.index("other")
            if "ideate" in lanes[:first_other] and "ideate" in lanes[first_other + 1:]:
                ok.append(n)
    assert ok, "no 'switch' sequence with ideate, other, ideate turns"
