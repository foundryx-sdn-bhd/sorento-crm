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
KEY = "sk-synthetic-not-a-real-key-WXYZ"
LAST4 = "WXYZ"
WRONG_LAST4 = "ABCD"


class _Keyed:
    """Stands in for a provider instance: the metering wrapper reads `self.api_key`."""
    api_key = KEY


PROV_KEYS = {"code_sha", "prompt_sha256", "prompt_version", "model", "reasoning_effort"}


@pytest.fixture()
def live_env(monkeypatch):
    """Test-level stubs the runner must leave alone: the parser, the MCP client and the provider's `chat`."""
    seen = {"parse_calls": [], "mcp_calls": [], "envelopes": [], "kwargs": []}

    def fake_chat(self, messages, **kw):
        return llm_provider.ChatResult(content="{}", prompt_tokens=1000, completion_tokens=100, total_tokens=1100,
                                       cached_tokens=800)

    def fake_parse(config, user_block):
        seen["parse_calls"].append(user_block)
        llm_provider.OpenAIProvider.chat(_Keyed(), [{"role": "user", "content": "x"}], model="gpt-5.4-mini")
        return _parser_output()

    def fake_tool(self, name, arguments):
        seen["mcp_calls"].append(name)
        return json.dumps({"ok": True, "items": []})

    real_run = engine_mod.run_turn

    def spy(envelope, **kw):
        seen["envelopes"].append(envelope)
        seen["kwargs"].append(kw)
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
                                max_calls=max_calls, max_usd=max_usd,
                                require_key_last4=LAST4)


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
                          "--max-calls", "10", "--max-usd", "5",
                          "--require-key-last4", LAST4])
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


# ------------------------------------------------------------------ review round 1
def test_live_network_guard_allows_the_model_host_and_the_ip_it_resolved_to_only(monkeypatch):
    import contextlib
    import socket

    reached = []

    def sentinel_connect(self, addr):
        reached.append(addr[0])
        raise OSError("sentinel: reached the real connect")

    monkeypatch.setattr(socket.socket, "connect", sentinel_connect)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 443))])
    misses = []
    with contextlib.ExitStack() as stack:
        replay_mod._guard_network(stack, misses, replay_mod.MODEL_HOSTS)
        assert socket.getaddrinfo("api.openai.com", 443)
        s = socket.socket()
        try:
            with pytest.raises(OSError, match="sentinel"):
                s.connect(("203.0.113.7", 443))            # the IP the model host resolved to: allowed
            with pytest.raises(OSError, match="network_blocked"):
                s.connect(("198.51.100.5", 443))           # another public IP: blocked
        finally:
            s.close()
    assert reached == ["203.0.113.7"]


def test_live_mode_blocks_the_ideation_tool_and_runs_the_engine_without_offload(session_factory, recorded, live_env, monkeypatch):
    from app.services.chatbot.lanes import ideate

    reached, caught = [], []
    monkeypatch.setattr(ideate, "call_ideation_tool", lambda **kw: reached.append(kw) or {})
    real_parse = parser_mod.parse

    def parse_then_ideate(config, user_block):
        out = real_parse(config, user_block)
        try:
            ideate.call_ideation_tool(text="x")
        except Exception as exc:  # noqa: BLE001
            caught.append(str(exc))
        return out

    monkeypatch.setattr(parser_mod, "parse", parse_then_ideate)
    out = go(session_factory, recorded)[0]
    assert reached == [] and caught and caught[0].startswith("llm_blocked")
    assert {"llm_blocked": "ideate"} in out.get("misses", [])
    assert live_env["kwargs"][0].get("offload") is False


def _parse_with(monkeypatch, model, calls_per_turn=1):
    def fake_parse(config, user_block):
        for _ in range(calls_per_turn):
            llm_provider.OpenAIProvider.chat(_Keyed(), [{"role": "user", "content": "x"}], model=model)
        return _parser_output()

    monkeypatch.setattr(parser_mod, "parse", fake_parse)


def test_a_model_with_no_price_row_stops_the_run(session_factory, recorded, live_env, monkeypatch):
    _parse_with(monkeypatch, "mystery-model")
    out = go(session_factory, recorded, n=3)
    assert out[1]["stopped"] == "unpriced_model" and out[2]["stopped"] == "unpriced_model"


def test_llm_model_override_is_the_priced_model_when_set(session_factory, recorded, live_env, monkeypatch):
    monkeypatch.setenv("LLM_MODEL_OVERRIDE", "gpt-5.4-mini")
    _parse_with(monkeypatch, "mystery-model")
    out = go(session_factory, recorded, n=2)
    assert "turn" in out[0] and "turn" in out[1] and "stopped" not in out[1]
    assert out[0]["usage"]["usd"] == pytest.approx(PER_CALL)


def test_usd_is_never_negative_when_cached_exceeds_prompt(session_factory, recorded, live_env, monkeypatch):
    monkeypatch.setattr(llm_provider.OpenAIProvider, "chat", lambda self, messages, **kw: llm_provider.ChatResult(
        content="{}", prompt_tokens=100, completion_tokens=0, total_tokens=100, cached_tokens=500))
    out = go(session_factory, recorded)[0]
    assert out["usage"]["usd"] >= 0


def test_the_cap_is_checked_before_each_model_call_not_only_between_items(session_factory, recorded, live_env, monkeypatch):
    actual = []
    monkeypatch.setattr(llm_provider.OpenAIProvider, "chat", lambda self, messages, **kw: actual.append(1) or llm_provider.ChatResult(
        content="{}", prompt_tokens=1000, completion_tokens=100, total_tokens=1100, cached_tokens=800))
    _parse_with(monkeypatch, "gpt-5.4-mini", calls_per_turn=2)
    out = go(session_factory, recorded, n=1, max_calls=1)
    assert len(actual) == 1, "a second model call went out after the cap"
    assert out[0].get("stopped") == "max_calls"


def test_an_item_that_raises_is_recorded_by_type_and_the_run_continues(session_factory, recorded, live_env, monkeypatch, capsys):
    real = engine_mod.run_turn
    calls = []

    def flaky(envelope, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError(f"boom with {VARIANT}")
        return real(envelope, **kw)

    monkeypatch.setattr(engine_mod, "run_turn", flaky)
    out = go(session_factory, recorded, n=2)
    assert out[0]["item_id"] == "it-0" and out[0]["error"] == "ValueError" and "turn" not in out[0]
    assert "turn" in out[1]
    cap = capsys.readouterr()
    assert VARIANT not in cap.out + cap.err and "boom" not in cap.out + cap.err


def test_a_recorded_envelope_without_a_text_slot_is_skipped(session_factory, recorded, live_env):
    row = copy.deepcopy(recorded)
    node = row["envelope"]
    while isinstance(node, dict):
        if "text" in node:
            node.pop("text")
            break
        node = node.get("message")
    out = replay_mod.live_rows(items_for(row), [row], session_factory=session_factory, max_calls=5, max_usd=5, require_key_last4=LAST4)
    assert out[0].get("skipped") == "no_text_slot"


def test_recorded_prompt_overrides_and_result_set_keys_are_stripped(session_factory, recorded, live_env):
    row = copy.deepcopy(recorded)
    row["envelope"]["prompt_overrides"] = {"parser": "some-version"}
    row["envelope"]["referenced_result_set"] = {"rows": [1]}
    replay_mod.live_rows(items_for(row), [row], session_factory=session_factory, max_calls=5, max_usd=5, require_key_last4=LAST4)
    extras = live_env["envelopes"][0].model_extra or {}
    assert "prompt_overrides" not in extras and "referenced_result_set" not in extras


# ------------------------------------------------------------------ review round 2
@pytest.mark.parametrize("blocked_name", ["crm_low_stock_report", "crm_portal_link_get", "crm_sales_analysis"])
def test_live_mode_refuses_mcp_tools_with_side_effects(session_factory, recorded, live_env, monkeypatch, blocked_name):
    real_parse = parser_mod.parse

    def parse_then_call(config, user_block):
        out = real_parse(config, user_block)
        for name in (blocked_name, "crm_inventory_list"):
            try:
                MCPRuntimeClient.call_tool(object(), name, {})
            except Exception:  # noqa: BLE001
                pass
        return out

    monkeypatch.setattr(parser_mod, "parse", parse_then_call)
    out = go(session_factory, recorded)[0]
    assert blocked_name not in live_env["mcp_calls"], "a side-effect tool reached the MCP client"
    assert "crm_inventory_list" in live_env["mcp_calls"], "a read tool must still pass through"
    assert {"blocked_tool": blocked_name} in out.get("misses", [])


def test_query_embeddings_in_a_live_run_are_counted_as_calls(session_factory, recorded, live_env, monkeypatch):
    from app.services import embedding_worker

    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", lambda chunks: [[0.0] for _ in chunks])

    def embed_only(config, user_block):
        embedding_worker._embed_text_chunks(["price of the white one"])
        return _parser_output()

    monkeypatch.setattr(parser_mod, "parse", embed_only)
    out = go(session_factory, recorded)[0]
    assert out["usage"]["embed_calls"] >= 1, "the embedding call was not metered"
    assert out["usage"]["calls"] == 0, "embeddings must not count as model calls"


def test_an_unpriced_model_call_stops_the_item_before_a_second_model_call(session_factory, recorded, live_env, monkeypatch):
    actual = []
    monkeypatch.setattr(llm_provider.OpenAIProvider, "chat", lambda self, messages, **kw: actual.append(1) or llm_provider.ChatResult(
        content="{}", prompt_tokens=1000, completion_tokens=100, total_tokens=1100, cached_tokens=800))
    _parse_with(monkeypatch, "mystery-model", calls_per_turn=2)
    go(session_factory, recorded, n=1)
    assert len(actual) == 1, "a second model call went out after an unpriced one"


def test_embeddings_use_their_own_bucket_and_do_not_consume_max_calls(session_factory, recorded, live_env, monkeypatch):
    from app.services import embedding_worker

    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", lambda chunks: [[0.0] for _ in chunks])

    def embed_then_chat(config, user_block):
        for _ in range(3):
            embedding_worker._embed_text_chunks(["x"])
        llm_provider.OpenAIProvider.chat(_Keyed(), [{"role": "user", "content": "x"}], model="gpt-5.4-mini")
        return _parser_output()

    monkeypatch.setattr(parser_mod, "parse", embed_then_chat)
    out = go(session_factory, recorded, n=1, max_calls=1)[0]
    assert "stopped" not in out and "turn" in out, out
    assert out["usage"]["embed_calls"] >= 3 and out["usage"]["calls"] == 1


# ------------------------------------------------------------------ review round 5 (only the dev key may spend)
def test_cli_live_without_require_key_last4_exits_2(tmp_path, session_factory, recorded, live_env):
    items = tmp_path / "items.jsonl"
    items.write_text(json.dumps(items_for(recorded)[0]) + "\n")
    rec = tmp_path / "recorded.jsonl"
    rec.write_text(json.dumps(recorded) + "\n")
    with pytest.raises(SystemExit) as exc:
        replay_mod.main(["live", "--items", str(items), "--recorded", str(rec), "--out", str(tmp_path / "o.jsonl"),
                         "--max-calls", "10", "--max-usd", "5"])
    assert exc.value.code == 2


def _go_with(session_factory, recorded_row, last4, n=2):
    return replay_mod.live_rows(items_for(recorded_row, n), [recorded_row], session_factory=session_factory,
                                max_calls=50, max_usd=10.0, require_key_last4=last4)


def test_a_provider_key_that_does_not_match_never_reaches_the_real_chat(session_factory, recorded, live_env, monkeypatch):
    real_calls = []
    monkeypatch.setattr(llm_provider.OpenAIProvider, "chat", lambda self, messages, **kw: real_calls.append(1) or
                        llm_provider.ChatResult(content="{}", prompt_tokens=1, completion_tokens=1, total_tokens=2,
                                                cached_tokens=0))
    out = _go_with(session_factory, recorded, WRONG_LAST4)
    assert real_calls == [], "a model call went out on a key that is not the dev key"
    assert out[0].get("stopped") == "wrong_key"
    assert out[1].get("stopped") == "wrong_key"


def test_an_embedding_key_that_does_not_match_never_reaches_the_real_embed(session_factory, recorded, live_env, monkeypatch):
    from app.config import settings
    from app.services import embedding_worker

    real_embeds = []
    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", lambda chunks: real_embeds.append(1) or [[0.0]])
    monkeypatch.setattr(settings, "openai_api_key", "sk-synthetic-other-key-QQQQ")

    def parse_embeds(config, user_block):
        embedding_worker._embed_text_chunks(["x"])
        return _parser_output()

    monkeypatch.setattr(parser_mod, "parse", parse_embeds)
    out = _go_with(session_factory, recorded, LAST4)
    assert real_embeds == [], "an embedding call went out on a key that is not the dev key"
    assert out[0].get("stopped") == "wrong_key"


def test_a_matching_key_runs_the_item_with_no_stop(session_factory, recorded, live_env):
    out = _go_with(session_factory, recorded, LAST4, n=1)
    assert "turn" in out[0] and "stopped" not in out[0]


def test_the_key_never_appears_in_output_rows_or_printed_lines(session_factory, recorded, live_env, monkeypatch, capsys):
    ok = _go_with(session_factory, recorded, LAST4, n=1)
    bad = _go_with(session_factory, recorded, WRONG_LAST4, n=1)
    cap = capsys.readouterr()
    assert KEY not in json.dumps(ok, default=str) + json.dumps(bad, default=str)
    assert KEY not in cap.out + cap.err
    assert LAST4 not in json.dumps(bad, default=str).replace("wrong_key", "")
