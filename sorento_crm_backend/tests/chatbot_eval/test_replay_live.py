"""EVAL-BENCH CP0: recorded-state runner (`replay.live_rows` and `replay.py live`). Synthetic only.

The parser is stubbed at test level (it calls a fake provider so the usage counter has something to count), the MCP
client is a test-level recorder: the runner must NOT patch either of them. No network, no real model.

Requires from `scripts/chatbot_eval/replay.py`:
  live_rows(items, recorded_rows, *, session_factory, max_calls, max_usd, on_row=None) -> list[dict]
      item = {item_id, run, message, recorded_turn_id}; recorded row matched on row["id"] == recorded_turn_id.
      A row output per item: {item_id, run, provenance: {code_sha, prompt_sha256, prompt_version, model,
      reasoning_effort}, usage: {calls, prompt_tokens, cached_tokens, completion_tokens, usd},
      turn: {envelope, trace, response, branch_kind}}.
      Items not run because a cap was hit come back as {item_id, run, stopped: "max_calls" | "max_usd"}.
  CLI: replay.py live --items FILE --recorded FILE --out FILE --max-calls N --max-usd X   (main(argv) -> int)
"""
from __future__ import annotations

import copy
import json

import pytest

from app.services import llm_provider
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from scripts.chatbot_eval import replay as replay_mod
from scripts.chatbot_eval.budget import cost_usd
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import _parser_output, seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay import ChatbotTurn, recorded, _turn_count  # noqa: F401

VARIANT = "price of the white one please"
PER_CALL = cost_usd({"prompt": 1000, "cached": 800, "output": 100}, "gpt-5.4-mini")
PROV_KEYS = {"code_sha", "prompt_sha256", "prompt_version", "model", "reasoning_effort"}


@pytest.fixture()
def live_env(monkeypatch):
    """Test-level stubs the runner must leave alone: the parser, the MCP client and the provider's `chat`."""
    seen = {"parse_calls": [], "mcp_calls": [], "envelopes": []}

    def fake_chat(self, messages, **kw):
        return llm_provider.ChatResult(content="{}", prompt_tokens=1000, completion_tokens=100, total_tokens=1100,
                                       cached_tokens=800)

    def fake_parse(config, user_block):
        seen["parse_calls"].append(user_block)
        llm_provider.OpenAIProvider.chat(object(), [{"role": "user", "content": "x"}], model="gpt-5.4-mini")
        return _parser_output()

    def fake_tool(self, name, arguments):
        seen["mcp_calls"].append(name)
        return json.dumps({"ok": True, "items": []})

    real_run = engine_mod.run_turn

    def spy(envelope, **kw):
        seen["envelopes"].append(envelope)
        return real_run(envelope, **kw)

    monkeypatch.setattr(llm_provider.OpenAIProvider, "chat", fake_chat)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_tool)
    monkeypatch.setattr(engine_mod, "run_turn", spy)
    seen["fake_chat"] = fake_chat
    return seen


def items_for(recorded_row, n=1, run=2):
    return [{"item_id": f"it-{i}", "run": run, "message": VARIANT, "recorded_turn_id": recorded_row["id"]}
            for i in range(n)]


def go(session_factory, recorded_row, n=1, max_calls=50, max_usd=10.0):
    return replay_mod.live_rows(items_for(recorded_row, n), [recorded_row], session_factory=session_factory,
                                max_calls=max_calls, max_usd=max_usd)


def text_of(envelope):
    node = envelope.model_dump() if hasattr(envelope, "model_dump") else envelope
    for _ in range(8):
        if isinstance(node, dict) and isinstance(node.get("text"), str):
            return node["text"]
        node = node.get("message") if isinstance(node, dict) else None
    return None


def test_envelope_has_the_item_text_a_fresh_id_the_state_and_no_parser_bypass(session_factory, recorded, live_env):
    go(session_factory, recorded)
    env = live_env["envelopes"][0]
    extras = env.model_extra or {}
    assert "mock_reformulator_output" not in extras
    assert text_of(env) == VARIANT
    recorded_env = engine_mod.Envelope(**copy.deepcopy(recorded["envelope"]))
    assert engine_mod._message_id(env) != engine_mod._message_id(recorded_env)
    assert env.dry_run is True
    assert extras.get("previous_conversation_state") == replay_mod._recorded_memory(recorded)


def test_the_parser_runs_live_on_the_item_text(session_factory, recorded, live_env):
    go(session_factory, recorded)
    assert len(live_env["parse_calls"]) == 1
    assert VARIANT in live_env["parse_calls"][0]


def test_mcp_is_not_patched_by_the_runner(session_factory, recorded, live_env):
    go(session_factory, recorded)
    assert live_env["mcp_calls"], "the test-level MCP stub never saw a call, so the runner patched it away"


def test_usage_counts_calls_tokens_and_usd_from_the_provider(session_factory, recorded, live_env):
    out = go(session_factory, recorded)[0]
    u = out["usage"]
    assert (u["calls"], u["prompt_tokens"], u["cached_tokens"], u["completion_tokens"]) == (1, 1000, 800, 100)
    assert u["usd"] == pytest.approx(PER_CALL)
    assert llm_provider.OpenAIProvider.chat is live_env["fake_chat"], "the provider wrapper was left installed"


def test_output_row_shape_and_the_full_turn(session_factory, recorded, live_env):
    out = go(session_factory, recorded)[0]
    assert set(out) >= {"item_id", "run", "provenance", "usage", "turn"}
    assert out["item_id"] == "it-0" and out["run"] == 2
    assert set(out["provenance"]) == PROV_KEYS
    turn = out["turn"]
    assert set(turn) >= {"envelope", "trace", "response", "branch_kind"}
    assert turn["trace"] and "reply" in turn["response"]


def test_run_stops_at_max_calls_with_a_reason(session_factory, recorded, live_env):
    out = go(session_factory, recorded, n=3, max_calls=2)
    assert len(out) == 3
    assert "turn" in out[0] and "turn" in out[1]
    assert out[2]["stopped"] == "max_calls" and out[2]["item_id"] == "it-2" and "turn" not in out[2]


def test_run_stops_at_max_usd_with_a_reason(session_factory, recorded, live_env):
    out = go(session_factory, recorded, n=3, max_usd=PER_CALL * 1.5)
    assert "turn" in out[0] and "turn" in out[1]
    assert out[2]["stopped"] == "max_usd"


def test_the_db_is_never_written(session_factory, recorded, live_env):
    before = _turn_count(session_factory)
    go(session_factory, recorded, n=2)
    assert _turn_count(session_factory) == before


def test_cli_live_writes_the_file_and_prints_no_message_text(tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):
    import app.database

    monkeypatch.setattr(app.database, "SessionLocal", session_factory)
    items = tmp_path / "items.jsonl"
    items.write_text("".join(json.dumps(i) + "\n" for i in items_for(recorded, n=2)))
    rec = tmp_path / "recorded.jsonl"
    rec.write_text(json.dumps(recorded) + "\n")
    out = tmp_path / "out.jsonl"
    rc = replay_mod.main(["live", "--items", str(items), "--recorded", str(rec), "--out", str(out),
                          "--max-calls", "10", "--max-usd", "5"])
    cap = capsys.readouterr()
    assert rc == 0
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(rows) == 2 and all("turn" in r for r in rows)
    shown = (cap.out + cap.err).lower()
    assert VARIANT not in shown and "price of the white" not in shown


def test_output_row_carries_every_tool_call_with_its_full_result_and_restores_the_client(session_factory, recorded, live_env, monkeypatch):
    big = json.dumps({"ok": True, "items": [{"code": "SRTWC8517", "note": "x" * 9000, "qty": 123456}]})
    calls = []

    def big_tool(self, name, arguments):
        calls.append((name, dict(arguments)))
        return big

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", big_tool)
    out = go(session_factory, recorded)[0]
    assert calls, "the test-level MCP stub never saw a call (the capture must be pass-through)"
    results = out["tool_results"]
    assert [r["name"] for r in results] == [c[0] for c in calls]
    assert [r["args"] for r in results] == [c[1] for c in calls]
    text = results[0]["result"] if isinstance(results[0]["result"], str) else json.dumps(results[0]["result"])
    assert len(text) > 8192 and "123456" in text and text.count("x") >= 9000, "the result was capped"
    assert MCPRuntimeClient.call_tool is big_tool, "the capture wrapper was left installed"
