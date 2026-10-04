"""S3 - key-free memory replay + ablation - tester-first RED, from the UAC and the lane
A contract (section 8.1 "Key-free turn replay").

Covers AC-MEM067, AC-MEM070 (via case d), AC-MEM076.

**Harness choice, stated explicitly (captain's brief asked which path was taken):** this
is a SMALL DEDICATED RUNNER in this file, NOT a drive through `tests/chatbot/
test_turn_replay.py::test_replay`. That shared harness's case shape has no `given.frames`
/ `given.profile_facts` / `given.live_turns` / `given.memory_level` and no
`expected.prompt_contains` / `expected.context_caps` at all today - it is read by
hundreds of OTHER domains' recorded cases, and teaching it these new keys is core
`turn/context.py`-adjacent work the coder owns, not a shared-file edit a tester should
make unreviewed. This runner reuses the SAME seeding/stub primitives every other S2/S3
test file in this lane already uses (`_turn_helpers.verdict`/`entity`, `test_engine.py`'s
`_envelope`/`stub_access`, direct `ConversationFrame`/`chatbot_profile` seeding) over the
real `engine.run_turn`, so it is exercising the real engine end to end, not a fixture of
its own invention.

**Genuinely red today** (not import-error red): nothing wires a frame summary, a live
episode's earlier message, or a `usual_products` fact into the parser's user block yet
(`context.py` does not exist, and today's `build_user_block` has no such inputs at all),
so every `needs_memory: true` case's `prompt_contains` assertion fails on a real
substring-not-found check.

Postgres only (`tests/chatbot/conftest.py::session_factory`, blank scratch schema).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.models.conversation_frame import ConversationFrame
from tests.chatbot._turn_helpers import verdict as verdict_defaults
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access  # noqa: F401

#: Coordinator fix, 26 Sep 2026: NOT under `replay_turns/` - `test_turn_replay.py`'s
#: own `REPLAY_ROOT.rglob("*.json")` picks up every json file under that tree
#: unconditionally and holds each one to ITS shape (a recorded `turns` list with a
#: real envelope/verdict/expected per step), which these cases are not. A sibling
#: directory keeps this runner's own case shape out of that glob without touching
#: the shared harness file.
CASES_DIR = Path(__file__).parent / "replay_memory"
CASE_FILES = sorted(CASES_DIR.glob("*.json"))


def _load_case(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_contact(session_factory, cid: str, *, memory_level: str | None) -> None:
    # `chatbot_recall_enabled` is retired (round 3, AC-MEM054, merged 5b110df8) -
    # dropped from the table outright, so this seed no longer names it.
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": cid, "phone": f"+6011{uuid.uuid4().hex[:8]}", "sv": json.dumps({"variables": {}})},
    )
    db.commit()
    if memory_level:
        db.execute(
            text("UPDATE respond_contacts SET chatbot_memory_level = :lvl WHERE respond_io_id = :cid"),
            {"lvl": memory_level, "cid": cid},
        )
        db.commit()


def _seed_frames(session_factory, cid: str, frames: list[dict[str, Any]]) -> None:
    db = session_factory()
    for f in frames:
        when = datetime.now() - timedelta(days=f.get("days_ago", 1))
        db.add(ConversationFrame(
            contact_id=cid, contact_respond_id=cid, space_id="0", channel="whatsapp",
            domain="inventory", status="closed", close_reason="topic_switch",
            summary=f["summary"], entities=f.get("entities") or {},
            turn_ids=[f"ZZT-memreplay-frame-{uuid.uuid4().hex[:6]}"],
            started_at=when, opened_at=when, last_activity_at=when, closed_at=when,
        ))
    db.commit()


def _seed_profile_facts(session_factory, cid: str, facts: list[dict[str, Any]]) -> None:
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE respond_io_id = :cid"),
        {"cid": cid, "p": json.dumps({"facts": facts})},
    )
    db.commit()


def _run_turn(session_factory, stub_access, *, message: str, verdict_overrides: dict, message_id: str):
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.head import parser as parser_mod

    stub_access()
    v = verdict_defaults(**verdict_overrides)

    def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    captured: list[str] = []

    def fake_parse(config, user_block):
        captured.append(user_block)
        return v

    from unittest import mock

    with mock.patch.object(parser_mod, "resolve_config", fake_resolve_config), \
         mock.patch.object(parser_mod, "parse", fake_parse):
        envelope = _envelope()
        envelope.message["message"]["messageId"] = message_id
        envelope.message["message"]["message"]["text"] = message
        result = engine_mod.run_turn(envelope, session_factory=session_factory)
    return result, captured[-1] if captured else ""


def _play_case(session_factory, stub_access, case: dict[str, Any], *, ablate: bool) -> tuple[Any, str]:
    """`ablate` replays the SAME history (frames, facts, live turns) with the contact
    at level `off`: only the level differs, so the ablation isolates memory from the
    conversation history and focus a real Off contact still has (reviewer pass at
    d89110c0, S19)."""
    cid = str(CONTACT_ID)
    given = case.get("given") or {}
    memory_level = "off" if ablate else given.get("memory_level")
    _seed_contact(session_factory, cid, memory_level=memory_level)

    if given.get("frames"):
        _seed_frames(session_factory, cid, given["frames"])
    if given.get("profile_facts"):
        _seed_profile_facts(session_factory, cid, given["profile_facts"])
    for i, live in enumerate(given.get("live_turns") or []):
        _run_turn(
            session_factory, stub_access, message=live["message"],
            verdict_overrides=live.get("verdict") or {},
            message_id=f"ZZT-memreplay-{case['name']}-live-{i}",
        )

    turn = case["turn"]
    result, prompt = _run_turn(
        session_factory, stub_access, message=turn["message"],
        verdict_overrides=turn.get("verdict") or {},
        message_id=f"ZZT-memreplay-{case['name']}-final",
    )
    return result, prompt


# --------------------------------------------------------------------------- #
# AC-MEM067: each needs_memory case passes WITH memory
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case_path", CASE_FILES, ids=[p.stem for p in CASE_FILES])
def test_memory_case_with_memory(case_path: Path, session_factory, stub_access) -> None:
    case = _load_case(case_path)
    result, prompt = _play_case(session_factory, stub_access, case, ablate=False)
    expected = case.get("expected") or {}

    if "prompt_contains" in expected:
        for phrase in expected["prompt_contains"]:
            assert phrase in prompt, (
                f"{case_path.name}: expected {phrase!r} in the assembled prompt, got:\n{prompt}"
            )

    if "focus_products_only" in expected:
        from app.models.chatbot_turn import ChatbotTurn

        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        memory_records = [r for r in (row.trace or []) if isinstance(r, dict) and r.get("kind") == "memory"]
        focus_after = json.dumps((memory_records[-1].get("focus") or {}).get("after") or {}) if memory_records else "{}"
        wanted = expected["focus_products_only"]
        for code in wanted:
            assert code in focus_after, f"{case_path.name}: expected {code} in focus, got {focus_after}"
        forbidden = {"SRTWB1455", "M486-75-BL"} - set(wanted)
        for code in forbidden:
            assert code not in focus_after, (
                f"{case_path.name}: {code} must NOT be carried into focus alongside {wanted}: {focus_after}"
            )


# --------------------------------------------------------------------------- #
# AC-MEM067: the SAME needs_memory:true case FAILS when memory is ablated
# --------------------------------------------------------------------------- #


NEEDS_MEMORY_CASES = [p for p in CASE_FILES if _load_case(p).get("needs_memory") is True]


@pytest.mark.parametrize("case_path", NEEDS_MEMORY_CASES, ids=[p.stem for p in NEEDS_MEMORY_CASES])
def test_needs_memory_case_fails_under_ablation(case_path: Path, session_factory, stub_access) -> None:
    case = _load_case(case_path)
    expected = case.get("expected") or {}
    assert "prompt_contains" in expected, (
        f"{case_path.name}: needs_memory:true cases in this runner are graded by "
        f"prompt_contains - a case with none is not proven to need memory at all"
    )
    _result, prompt = _play_case(session_factory, stub_access, case, ablate=True)

    still_all_present = all(phrase in prompt for phrase in expected["prompt_contains"])
    assert not still_all_present, (
        f"{case_path.name}: marked needs_memory:true but its prompt_contains phrases "
        f"are ALL still present with memory ablated - this case does not test memory "
        f"(the PRINCIPLES kill test, applied to the corpus)"
    )
