"""EVAL-BENCH D2 (AC2.1, 2.2, 2.4 in miniature, 2.6, 2.7): L1 replay of recorded parses.

Names the tests require from `scripts/chatbot_eval/replay.py`:
    replay_rows(rows: list[dict], *, session_factory) -> list[dict]     one output dict per row
    diff_rows(base: list[dict], head: list[dict], intended: set[tuple[str, str]] | None = None) -> dict
        {"diffs": [(case_id, turn_id, stage)], "unintended": [(case_id, turn_id, stage)],
         "counts": {"stage": {stage: n}, "skipped": {kind: n}}}
    CLI: `python scripts/chatbot_eval/replay.py diff BASE.jsonl HEAD.jsonl [--intended FILE]`
A recorded row is {"case_id", "id" (turn id), "envelope", "trace", "response"} (a chatbot.turns row).
Row built by running ONE synthetic turn through the real engine; no real data, no LLM.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from scripts.chatbot_eval import replay as replay_mod
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import _envelope, _parser_output, _turn_row, seeded, stub_access  # noqa: F401

BACKEND = Path(__file__).resolve().parents[2]
MESSAGE = "price for SRTWC8517"
TOOL_ENV = {"ok": True, "items": [{"code": "SRTWC8517", "label": "synthetic recorded product row"}], "total": 1}


@pytest.fixture()
def recorded(session_factory, seeded, stub_access, monkeypatch):
    """One recorded row, plus a DB left with no chatbot.turns rows so a replay may run the same message id."""

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="k")

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: json.dumps(TOOL_ENV))
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    turn = _turn_row(session_factory, result.turn_id)
    row = {
        "case_id": "case-syn-1",
        "id": str(turn.id),
        "envelope": copy.deepcopy(turn.envelope),
        "trace": copy.deepcopy(turn.trace),
        "response": copy.deepcopy(turn.response),
    }
    db = session_factory()
    db.execute(text("DELETE FROM chatbot.turns"))
    db.commit()
    # the replay must not reach a live parser or the real MCP server
    monkeypatch.setattr(parser_mod, "parse", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live parser called")))
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: (_ for _ in ()).throw(AssertionError("live MCP")))
    return row


def _turn_count(session_factory) -> int:
    return session_factory().execute(text("SELECT count(*) FROM chatbot.turns")).scalar()


def _leaves(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _leaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from _leaves(v)


def test_ac2_2_replaying_the_same_row_twice_is_identical_and_diff_is_empty(session_factory, recorded):
    a = replay_mod.replay_rows([recorded], session_factory=session_factory)
    b = replay_mod.replay_rows([recorded], session_factory=session_factory)
    assert a == b
    assert a[0]["case_id"] == "case-syn-1" and a[0]["turn_id"] == recorded["id"]
    assert not a[0].get("skipped")
    assert set(a[0]["stages"]) >= {"grounded", "decision", "tools", "reply"}
    res = replay_mod.diff_rows(a, b)
    assert res["diffs"] == [] and res["unintended"] == []


def test_ac2_7_row_without_parse_is_skipped_not_diffed(session_factory, recorded):
    row = copy.deepcopy(recorded)
    row["response"]["ctx"]["parse"].pop("_parser_raw", None)
    out = replay_mod.replay_rows([row], session_factory=session_factory)
    assert out[0]["skipped"] == "no_parse"
    res = replay_mod.diff_rows(out, out)
    assert res["diffs"] == []
    assert res["counts"]["skipped"] == {"no_parse": 1}


def test_ac2_6_unmatched_recorded_tool_args_record_a_miss_and_no_reply(session_factory, recorded):
    row = copy.deepcopy(recorded)
    tools = [e for e in row["trace"] if e.get("kind") == "tool" or (e.get("name") and "args" in e)]
    assert tools, "synthetic turn recorded no tool call"
    for e in tools:
        e["args"] = {"__changed__": "x"}
    out = replay_mod.replay_rows([row], session_factory=session_factory)[0]
    assert out["misses"], "a head tool call with no recorded match must be recorded as a miss"
    assert "reply" not in out["stages"]


def test_ac2_6_diff_reports_tools_stage_for_a_row_with_a_miss():
    base = {"case_id": "c1", "turn_id": "t1", "skipped": None, "misses": [],
            "stages": {"grounded": "g", "decision": "d", "tools": [["crm_x", "h1"]], "reply": {"sha256": "r", "branch_kind": "b"}}}
    head = copy.deepcopy(base)
    head["misses"] = [{"tool": "crm_x", "args_sha": "h2"}]
    head["stages"].pop("reply")
    assert replay_mod.diff_rows([base], [head])["diffs"] == [("c1", "t1", "tools")]


def test_output_carries_no_message_or_reply_text(session_factory, recorded):
    out = json.dumps(replay_mod.replay_rows([recorded], session_factory=session_factory))
    assert MESSAGE not in out
    reply_text = [s for s in _leaves(recorded["response"].get("reply")) if len(s) >= 12]
    assert reply_text, "synthetic turn produced no reply text to check"
    for s in reply_text:
        assert s not in out


def test_ac2_4_replay_leaves_the_turns_table_unchanged(session_factory, recorded):
    before = _turn_count(session_factory)
    replay_mod.replay_rows([recorded], session_factory=session_factory)
    assert _turn_count(session_factory) == before


def _write(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def _cli_diff(base, head, *extra):
    return subprocess.run(
        [sys.executable, str(BACKEND / "scripts/chatbot_eval/replay.py"), "diff", base, head, *extra],
        capture_output=True, text=True, cwd=BACKEND,
    )


def _row(grounded):
    return {"case_id": "c9", "turn_id": "t9", "skipped": None, "misses": [],
            "stages": {"grounded": grounded, "decision": "d", "tools": [], "reply": {"sha256": "r", "branch_kind": "b"}}}


def test_ac2_1_diff_cli_identical_prints_no_diff_lines_and_exits_0(tmp_path):
    b = _write(tmp_path / "b.jsonl", [_row("g1")])
    p = _cli_diff(b, _write(tmp_path / "h.jsonl", [_row("g1")]))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "c9 t9" not in p.stdout


def test_ac2_1_diff_cli_grounded_diff_exits_1_unless_intended(tmp_path):
    b = _write(tmp_path / "b.jsonl", [_row("g1")])
    h = _write(tmp_path / "h.jsonl", [_row("g2")])
    p = _cli_diff(b, h)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "c9 t9 grounded" in p.stdout.splitlines()
    intended = tmp_path / "intended.txt"
    intended.write_text("c9 grounded\n")
    q = _cli_diff(b, h, "--intended", str(intended))
    assert q.returncode == 0, q.stdout + q.stderr
    assert "c9 t9 grounded" in q.stdout.splitlines()
