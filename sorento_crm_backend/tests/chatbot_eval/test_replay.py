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
import os
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.chatbot_turn import ChatbotTurn
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from scripts.chatbot_eval import replay as replay_mod
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import _envelope, _parser_output, _turn_row, seeded, stub_access  # noqa: F401

BACKEND = Path(__file__).resolve().parents[2]
MESSAGE = "price for SRTWC8517"
TOOL_ENV = {"ok": True, "items": [{"code": "SRTWC8517", "label": "synthetic recorded product row"}], "total": 1}


def _record(session_factory, stub_access, monkeypatch, *, keep_turn: bool):

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
    if not keep_turn:
        db = session_factory()
        db.query(ChatbotTurn).delete()  # ORM, so the fixture schema_translate_map applies
        db.commit()
    # the replay must not reach a live parser or the real MCP server
    monkeypatch.setattr(parser_mod, "parse", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live parser called")))
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: (_ for _ in ()).throw(AssertionError("live MCP")))
    return row


@pytest.fixture()
def recorded(session_factory, seeded, stub_access, monkeypatch):
    """One recorded row, plus a DB left with no chatbot.turns rows so a replay may run the same message id."""
    return _record(session_factory, stub_access, monkeypatch, keep_turn=False)


@pytest.fixture()
def recorded_kept(session_factory, seeded, stub_access, monkeypatch):
    """One recorded row while the DB STILL holds that turn (same contact + message id)."""
    return _record(session_factory, stub_access, monkeypatch, keep_turn=True)


def _turn_count(session_factory) -> int:
    return session_factory().query(ChatbotTurn).count()  # ORM, so the fixture schema_translate_map applies


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


def test_grounding_tier_returns_only_the_grounded_stage_equal_to_the_whole_turn(session_factory, recorded):
    whole = replay_mod.replay_rows([recorded], session_factory=session_factory)[0]
    tier = replay_mod.replay_rows([recorded], session_factory=session_factory, stage="grounding")[0]
    assert set(tier["stages"]) == {"grounded"}
    assert "tools" not in tier["stages"] and "reply" not in tier["stages"]
    assert tier["stages"]["grounded"] == whole["stages"]["grounded"]
    assert tier["case_id"] == whole["case_id"] and tier["turn_id"] == whole["turn_id"]


def test_replay_blocks_every_provider_chat_and_records_it():
    import inspect

    from app.services import llm_provider

    classes = {
        n: c for n, c in vars(llm_provider).items()
        if inspect.isclass(c) and c is not llm_provider.LLMProvider and n.endswith("Provider") and "chat" in vars(c)
    }
    assert {"OpenAIProvider", "AnthropicProvider", "GeminiProvider"} <= set(classes)
    misses: list = []
    with replay_mod._patched({}, misses):
        for name, cls in classes.items():
            with pytest.raises(RuntimeError, match=f"llm_blocked: provider.{name}"):
                cls.chat(object())
    assert {m.get("llm_blocked") for m in misses} >= {f"provider.{n}" for n in classes}


@pytest.fixture()
def no_real_network(monkeypatch):
    """Safety net UNDER the replay guard: nothing here may leave the machine, guard or no guard.

    Non-local names resolve to a TEST-NET address and a non-local connect is recorded, then refused, so a
    missing guard shows up as `reached` being non-empty instead of a packet.
    """
    import socket

    reached: list = []
    local = {"127.0.0.1", "localhost", "::1"}
    real_connect, real_gai = socket.socket.connect, socket.getaddrinfo

    def gai(host, *a, **k):
        if host in local:
            return real_gai(host, *a, **k)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", a[0] if a else 0))]

    def connect(self, addr):
        if addr[0] in local:
            return real_connect(self, addr)
        reached.append(addr[0])
        raise OSError("sentinel: would have reached the network")

    monkeypatch.setattr(socket, "getaddrinfo", gai)
    monkeypatch.setattr(socket.socket, "connect", connect)
    return reached


def _blocked_text(call):
    try:
        call()
    except (RuntimeError, OSError) as exc:
        return str(exc)
    return None


def test_replay_blocks_non_local_network_and_allows_localhost(no_real_network):
    import socket

    misses: list = []
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        with replay_mod._patched({}, misses):
            t1 = _blocked_text(lambda: socket.create_connection(("api.openai.com", 443), timeout=1))
            raw = socket.socket()
            try:
                t2 = _blocked_text(lambda: raw.connect(("8.8.8.8", 53)))
            finally:
                raw.close()
            local = socket.create_connection(("127.0.0.1", port), timeout=1)  # the DB path must keep working
            local.close()
    finally:
        server.close()
    assert t1 and t1.startswith("network_blocked:"), t1
    assert t2 and t2.startswith("network_blocked:"), t2
    assert no_real_network == [], "a connect got past the replay guard"
    hosts = {m.get("network_blocked") for m in misses}
    assert {"api.openai.com", "8.8.8.8"} <= hosts
    assert "127.0.0.1" not in hosts


def test_entity_key_sorts_none_hints_next_to_str_hints_in_any_order():
    ents = [{"hint": None, "canonical_code": "X1", "raw": "a"}, {"hint": "product", "canonical_code": None, "raw": "b"}]
    forward = replay_mod._entity_key(ents)
    assert forward == replay_mod._entity_key(list(reversed(ents)))
    assert len(forward) == 2


def test_replay_run_cli_writes_no_message_or_reply_text_to_stdout_or_stderr(tmp_path, recorded):
    phrase = "zqx probe phrase undermount"
    row = copy.deepcopy(recorded)
    # every place the recorded message appears, so the engine can only know the phrase from this row
    node = row["envelope"]
    while isinstance(node, dict) and "text" not in node:
        node = node.get("message")
    node["text"] = phrase
    raw = row["response"]["ctx"]["parse"]["_parser_raw"]
    for e in raw.get("entities") or []:
        e["raw"] = phrase
    traces = tmp_path / "rows.jsonl"
    traces.write_text(json.dumps(row) + "\n")
    out = tmp_path / "out.jsonl"
    env = dict(os.environ)
    p = subprocess.run([sys.executable, "scripts/chatbot_eval/replay.py", "run", "--traces", str(traces),
                        "--out", str(out)], capture_output=True, text=True, cwd=BACKEND, env=env)
    assert p.returncode == 0, p.stderr[-500:]
    assert out.read_text().strip(), "the run wrote no row"
    seen = (p.stdout + p.stderr).lower()
    for token in [phrase, *phrase.split()]:
        assert token.lower() not in seen, f"{token!r} leaked into the logs"
    reply_text = [s for s in _leaves(recorded["response"].get("reply")) if len(s) >= 12]
    for s in reply_text:
        assert s.lower() not in seen


def test_replay_rows_emits_no_log_record_carrying_the_message_words(session_factory, recorded, caplog):
    import logging

    phrase = "zqx probe phrase undermount"
    row = copy.deepcopy(recorded)
    node = row["envelope"]
    while isinstance(node, dict) and "text" not in node:
        node = node.get("message")
    node["text"] = phrase
    for e in row["response"]["ctx"]["parse"]["_parser_raw"].get("entities") or []:
        e["raw"] = phrase
    with caplog.at_level(logging.DEBUG):
        out = replay_mod.replay_rows([row], session_factory=session_factory)
    assert not out[0].get("skipped")
    leaked = [r.getMessage() for r in caplog.records if any(t in r.getMessage().lower() for t in phrase.split())]
    assert leaked == [], f"log lines carry the message words: {leaked[:1]}"


def test_replay_runs_the_engine_even_when_the_db_still_holds_the_recorded_turn(session_factory, recorded_kept):
    # precondition: the DB really would short-circuit this message as a duplicate
    assert engine_mod.run_turn(_envelope(), session_factory=session_factory).duplicate
    row = copy.deepcopy(recorded_kept)
    bogus = [{"hint": "product", "canonical_code": "BOGUSCODE9", "raw": "bogus-recorded-entity"}]
    for rec in row["trace"]:
        if rec.get("kind") == "apply":
            rec["verdict"]["entities"] = bogus
    out = replay_mod.replay_rows([row], session_factory=session_factory)[0]
    grounded = out["stages"]["grounded"]
    assert grounded != replay_mod._entity_key(bogus), "the replay returned the recorded trace, not the engine's"
    assert any(e[2] == replay_mod._sha("srtwc8517")[:12] for e in grounded), grounded


def test_grounding_stage_runs_inside_the_llm_block(session_factory, recorded, monkeypatch):
    from app.services import llm_provider
    from app.services.chatbot.head import grounding

    def ground_calling_a_model(*a, **k):
        llm_provider.OpenAIProvider.chat(object())

    monkeypatch.setattr(grounding, "ground", ground_calling_a_model)
    out = replay_mod.replay_rows([recorded], session_factory=session_factory, stage="grounding")[0]
    assert {"llm_blocked": "provider.OpenAIProvider"} in out["misses"], out["misses"]


def test_head_only_rows_are_counted_missing_in_base_and_unintended():
    base = {"case_id": "c1", "turn_id": "t1", "skipped": None, "misses": [],
            "stages": {"grounded": "g", "decision": "d", "tools": [], "reply": {"sha256": "r", "branch_kind": "b"}}}
    extra = dict(base, case_id="c2", turn_id="t2")
    res = replay_mod.diff_rows([base], [base, extra])
    assert res["counts"]["skipped"].get("missing_in_base") == 1
    assert ("c2", "t2", "missing_in_base") in res["unintended"]


def test_replay_reports_a_duplicate_turn_as_skipped_never_as_an_equal_replay(session_factory, recorded_kept, monkeypatch):
    existing = _turn_row(session_factory, recorded_kept["id"])
    monkeypatch.setattr(engine_mod, "_existing_turn", lambda db, **kw: existing)
    out = replay_mod.replay_rows([recorded_kept], session_factory=session_factory)[0]
    assert out["skipped"] == "duplicate", out
    res = replay_mod.diff_rows([out], [out])
    assert res["diffs"] == []
    assert res["counts"]["skipped"].get("duplicate") == 1


def test_replay_hands_the_engine_a_fresh_message_id(session_factory, recorded_kept, monkeypatch):
    from app.services.chatbot.contracts import Envelope

    seen: list = []
    real = engine_mod.run_turn

    def spy(envelope, **kw):
        seen.append(engine_mod._message_id(envelope))
        return real(envelope, **kw)

    monkeypatch.setattr(engine_mod, "run_turn", spy)
    replay_mod.replay_rows([recorded_kept], session_factory=session_factory)
    recorded_id = engine_mod._message_id(Envelope(**copy.deepcopy(recorded_kept["envelope"])))
    assert seen and seen[0] != recorded_id, "the replay reused the recorded message id"
