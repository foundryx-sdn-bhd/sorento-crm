"""EVAL-BENCH CP0: recorded-state runner (`replay.live_rows` and `replay.py live`). Synthetic only.

The parser is stubbed at test level (it calls a fake provider so the usage counter has something to count), the MCP
client is a test-level recorder: the runner must NOT patch either of them. No network, no real model.

Requires from `scripts/chatbot_eval/replay.py`:
  live_rows(items, recorded_rows, *, session_factory, max_calls, max_usd, require_key_last4, on_row=None)
      -> list[dict]; require_key_last4 must be exactly 4 non-blank characters (ValueError before any item)
      item = {item_id, run, message, recorded_turn_id}; recorded row matched on row["id"] == recorded_turn_id.
      A row output per item: {item_id, run, provenance: {code_sha, prompt_sha256, prompt_version, model,
      reasoning_effort}, usage: {calls, prompt_tokens, cached_tokens, completion_tokens, usd},
      turn: {envelope, trace, response, branch_kind}}.
      Items not run because a cap was hit come back as {item_id, run, stopped: "max_calls" | "max_usd" | "wrong_key"}
      ("wrong_key": a provider or embedding key that does not end with require_key_last4; no real call goes out).
  CLI: replay.py live --items FILE --recorded FILE --out FILE --max-calls N --max-usd X --require-key-last4 XXXX
      (main(argv) -> int)
"""
from __future__ import annotations

import copy
import json
import re

import pytest
from sqlalchemy import text

from app.services import llm_provider
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from scripts.chatbot_eval import replay as replay_mod
from scripts.chatbot_eval.budget import cost_usd
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import _parser_output, seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay import ChatbotTurn, recorded, _record, _turn_count  # noqa: F401

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


# ------------------------------------------------------------------ review round 6 (the guard fails closed)
@pytest.mark.parametrize("bad", ["", "   ", "abc", "abcde"])
def test_cli_rejects_a_require_key_last4_that_is_not_four_non_blank_characters(tmp_path, session_factory, recorded,
                                                                              live_env, bad):
    items = tmp_path / "items.jsonl"
    items.write_text(json.dumps(items_for(recorded)[0]) + "\n")
    rec = tmp_path / "recorded.jsonl"
    rec.write_text(json.dumps(recorded) + "\n")
    with pytest.raises(SystemExit) as exc:
        replay_mod.main(["live", "--items", str(items), "--recorded", str(rec), "--out", str(tmp_path / "o.jsonl"),
                         "--max-calls", "10", "--max-usd", "5", "--require-key-last4", bad])
    assert exc.value.code == 2


@pytest.mark.parametrize("bad", ["", "Z", "   ", "abcde", None])
def test_live_rows_refuses_an_invalid_require_key_last4_before_any_call(session_factory, recorded, live_env,
                                                                        monkeypatch, bad):
    real_calls = []
    monkeypatch.setattr(llm_provider.OpenAIProvider, "chat", lambda self, messages, **kw: real_calls.append(1) or
                        llm_provider.ChatResult(content="{}", prompt_tokens=1, completion_tokens=1, total_tokens=2,
                                                cached_tokens=0))
    with pytest.raises(ValueError):
        replay_mod.live_rows(items_for(recorded, 2), [recorded], session_factory=session_factory,
                             max_calls=50, max_usd=10.0, require_key_last4=bad)
    assert real_calls == [], "a model call went out under an invalid key guard"


# ------------------------------------------------------------------ review round 7 (history as of the recorded turn)
PRIOR_1 = "PRIORREPLY-ONE synthetic earlier answer"
PRIOR_2 = "PRIORREPLY-TWO synthetic latest answer"
DECOY_LATER = "DECOYLATER-MARKER later prod turn"
DECOY_TEST = "DECOYTEST-MARKER is_test turn"
EARLY_PROJECT = "SYN-EARLY-PROJECT"
LATER_NOTE = "LATERNOTE-MARKER"
FRAME_SUMMARY = "Asked about stock for SRTWC-SYN and got an answer."


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _layer_tokens(trace):
    """{"L3": n, "L4": n} from the `context` trace record (found by its `layers` list)."""
    for rec in _walk(trace):
        layers = rec.get("layers")
        if isinstance(layers, list) and layers and all(isinstance(x, dict) and "layer" in x for x in layers):
            return {x["layer"]: x["est_tokens"] for x in layers}
    raise AssertionError("no context record with layers in the trace")


def _prompt_text(trace):
    return next(rec["text"] for rec in _walk(trace) if isinstance(rec.get("text"), str) and "Recent conversations" in rec["text"]
                or False) if any(isinstance(r.get("text"), str) and "Recent conversations" in r["text"] for r in _walk(trace)) else ""


def _builder(session_factory, seeded, stub_access, monkeypatch, *, early_fact=False, later_fact=False, quote=False,
             frame_at_minutes=None):
    """Seed a synthetic contact at memory level `full` with two prior prod answers and one closed prod frame, record a
    prod turn on top of them, then add decoys (a LATER prod turn and an is_test turn, unique markers) and, when asked,
    a profile fact that only appears AFTER the recorded turn. `frame_at_minutes`: keep the frame in the DB with that
    last activity (minutes after the first prior turn); None deletes it, as the stack DB may lack it."""
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.models.conversation_frame import ConversationFrame
    from tests.chatbot import test_engine as te
    from tests.chatbot_eval import test_replay as tr

    cid = str(te.CONTACT_ID)
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    db = seeded
    db.execute(text("UPDATE respond_contacts SET chatbot_memory_level = 'full' WHERE respond_io_id = :c"), {"c": cid})
    if early_fact:
        db.execute(text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE respond_io_id = :c"),
                   {"c": cid, "p": json.dumps({"facts": [{"key": "project", "value": EARLY_PROJECT, "source": "staff",
                                                          "last_seen": None}]})})
    ids = {}

    def add_turn(tag, message, reply, minutes, is_test=False, with_state=False):
        env = te._envelope().model_dump(mode="json", exclude_none=False)
        env["message"]["message"]["messageId"] = f"SYN-{tag}"
        env["message"]["message"]["message"]["text"] = message
        trace = [{"kind": "state", "focus": {"products": [], "tag": tag}, "open_question": None, "turn_no": 1}] if with_state else []
        ids[tag] = str(uuid.uuid4())
        db.add(ChatbotTurn(id=ids[tag], contact_respond_id=cid, message_id=f"SYN-{tag}", envelope=env, status="done",
                           stage="sent", is_test=is_test, response={"reply": {"text": reply}}, trace=trace,
                           created_at=t0 + timedelta(minutes=minutes), started_at=t0 + timedelta(minutes=minutes)))

    add_turn("P1", "synthetic earlier question one", PRIOR_1, 0, with_state=quote)
    add_turn("P2", "synthetic earlier question two", PRIOR_2, 10, with_state=quote)
    frame_id = str(uuid.uuid4())
    db.add(ConversationFrame(id=frame_id, contact_id=cid, space_id="364817", channel="whatsapp", contact_respond_id=cid,
                             domain="inventory", summary=FRAME_SUMMARY, status="closed", is_test=False,
                             last_activity_at=datetime.now() - timedelta(days=3)))
    db.commit()
    if frame_at_minutes is not None:
        db.execute(text("UPDATE conversation_frames SET last_activity_at = "
                        "(CAST(:ts AS timestamptz) AT TIME ZONE current_setting('TimeZone')) WHERE id = :i"),
                   {"ts": (t0 + timedelta(minutes=frame_at_minutes)).isoformat(), "i": frame_id})
        db.commit()
    if quote:
        real = te._envelope

        def quoting(**kw):
            d = real(**kw).model_dump(mode="json", exclude_none=False)
            d["message"]["message"]["replyTo"] = {"id": "SYN-QUOTED-MSG", "sender": {"source": "workflow"},
                                                  "message": {"type": "text", "text": PRIOR_2}}
            return engine_mod.Envelope(**d)

        monkeypatch.setattr(tr, "_envelope", quoting)
    row = tr._record(session_factory, stub_access, monkeypatch, keep_turn=True)
    db = session_factory()
    if frame_at_minutes is None:
        db.query(ConversationFrame).delete()
    turn = db.query(ChatbotTurn).filter(ChatbotTurn.id == row["id"]).first()
    turn.created_at = t0 + timedelta(minutes=20)
    db.commit()
    seen = _layer_tokens(row["trace"])
    assert seen["L3"] > 0 and seen["L4"] > 0, f"the synthetic recording has no history layers: {seen}"
    assert PRIOR_2 in _prompt_text(row["trace"]) and "Recent conversations" in _prompt_text(row["trace"])
    decoy_reply = PRIOR_2 if quote else None
    for k, (marker, minutes, is_test) in enumerate([(DECOY_LATER, 30, False), (DECOY_TEST, 15, True)]):
        tag = f"D{k}"
        env = te._envelope().model_dump(mode="json", exclude_none=False)
        env["message"]["message"]["messageId"] = f"SYN-{tag}"
        env["message"]["message"]["message"]["text"] = marker
        ids[tag] = str(uuid.uuid4())
        db.add(ChatbotTurn(id=ids[tag], contact_respond_id=cid, message_id=f"SYN-{tag}", envelope=env, status="done",
                           stage="sent", is_test=is_test, response={"reply": {"text": decoy_reply or marker}},
                           trace=[{"kind": "state", "focus": {"products": [], "tag": tag}, "open_question": None,
                                   "turn_no": 1}] if quote else [],
                           created_at=t0 + timedelta(minutes=minutes), started_at=t0 + timedelta(minutes=minutes)))
    if later_fact:
        db.execute(text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE respond_io_id = :c"),
                   {"c": cid, "p": json.dumps({"facts": (
                       [{"key": "project", "value": EARLY_PROJECT, "source": "staff", "last_seen": None}] if early_fact else [])
                       + [{"key": "note", "value": LATER_NOTE, "source": "staff", "last_seen": None}]})})
    db.commit()
    row["_ids"] = ids
    return row


@pytest.fixture()
def recorded_with_history(session_factory, seeded, stub_access, monkeypatch):
    return _builder(session_factory, seeded, stub_access, monkeypatch)


def test_live_context_layers_equal_the_recorded_ones_chosen_exact_because_the_rows_and_times_are_fixed(
        session_factory, recorded_with_history, live_env):
    # Exact equality: the history rows, their timestamps and the recorded prompt are all fixed synthetic data,
    # and est_tokens is a pure function of the rendered text, so any difference is a real history difference.
    out = go(session_factory, recorded_with_history)[0]
    live = _layer_tokens(out["turn"]["trace"])
    rec = _layer_tokens(recorded_with_history["trace"])
    assert live["L3"] > 0 and live["L4"] > 0, live
    assert (live["L3"], live["L4"]) == (rec["L3"], rec["L4"])


def test_the_parser_input_has_the_prod_history_as_of_the_recorded_turn_and_no_decoys(
        session_factory, recorded_with_history, live_env):
    go(session_factory, recorded_with_history)
    block = live_env["parse_calls"][0]
    assert PRIOR_1 in block and PRIOR_2 in block
    assert FRAME_SUMMARY.split(" and ")[0] in block, "the recorded Recent conversations line is missing"
    assert DECOY_LATER not in block, "a prod turn created after the recorded turn leaked in"
    assert DECOY_TEST not in block, "an is_test turn leaked in"


def test_reading_the_history_writes_nothing(session_factory, recorded_with_history, live_env):
    before = _turn_count(session_factory)
    go(session_factory, recorded_with_history, n=2)
    assert _turn_count(session_factory) == before


# ------------------------------------------------------------------ review round 8
def _l5(block):
    m = re.search(r"About this contact:\n[^\n]*", block)
    return m.group(0) if m else None


@pytest.fixture()
def recorded_profile_before_and_after(session_factory, seeded, stub_access, monkeypatch):
    return _builder(session_factory, seeded, stub_access, monkeypatch, early_fact=True, later_fact=True)


@pytest.fixture()
def recorded_no_profile_then_a_later_fact(session_factory, seeded, stub_access, monkeypatch):
    return _builder(session_factory, seeded, stub_access, monkeypatch, later_fact=True)


@pytest.fixture()
def recorded_quoting(session_factory, seeded, stub_access, monkeypatch):
    return _builder(session_factory, seeded, stub_access, monkeypatch, quote=True)


@pytest.fixture()
def recorded_with_a_newer_frame(session_factory, seeded, stub_access, monkeypatch):
    return _builder(session_factory, seeded, stub_access, monkeypatch, frame_at_minutes=5)


def test_the_about_this_contact_block_is_the_recorded_one_not_the_profile_as_of_now(
        session_factory, recorded_profile_before_and_after, live_env):
    recorded_l5 = _l5(_prompt_text(recorded_profile_before_and_after["trace"]))
    assert recorded_l5 and EARLY_PROJECT in recorded_l5 and LATER_NOTE not in recorded_l5
    go(session_factory, recorded_profile_before_and_after)
    block = live_env["parse_calls"][0]
    assert LATER_NOTE not in block, "a profile fact added after the recorded turn leaked into the parser input"
    assert _l5(block) == recorded_l5


def test_a_recorded_prompt_with_no_about_block_gets_none_even_if_a_fact_exists_now(
        session_factory, recorded_no_profile_then_a_later_fact, live_env):
    assert _l5(_prompt_text(recorded_no_profile_then_a_later_fact["trace"])) is None
    go(session_factory, recorded_no_profile_then_a_later_fact)
    block = live_env["parse_calls"][0]
    assert "About this contact:" not in block and LATER_NOTE not in block


def _quoted_turn_fact(trace):
    return next(rec["quoted_turn"] for rec in _walk(trace) if isinstance(rec, dict) and "quoted_turn" in rec)


def test_a_quoted_reply_resolves_to_the_prod_turn_before_the_recorded_turn(session_factory, recorded_quoting, live_env):
    # Observable: the `received` trace record's `quoted_turn` fact (found + turn_id), written by the engine for a quote.
    ids = recorded_quoting["_ids"]
    rec = _quoted_turn_fact(recorded_quoting["trace"])
    assert rec["found"] is True and rec["turn_id"] == ids["P2"], rec
    out = go(session_factory, recorded_quoting)[0]
    live = _quoted_turn_fact(out["turn"]["trace"])
    assert live["found"] is True, live
    assert live["turn_id"] == ids["P2"], f"resolved to {live['turn_id']}, a decoy or none, not the prod turn before the anchor"


def _capture_layers(monkeypatch):
    from app.services.chatbot.turn import context as context_mod

    seen = []
    real = context_mod.assemble

    def spy(layers):
        seen.append(layers)
        return real(layers)

    monkeypatch.setattr(context_mod, "assemble", spy)
    return seen


def test_a_turn_before_the_newest_read_frame_is_cut_off_from_earlier_messages(
        session_factory, recorded_with_a_newer_frame, live_env, monkeypatch):
    seen = _capture_layers(monkeypatch)
    go(session_factory, recorded_with_a_newer_frame)
    texts = [m["text"] for m in seen[0].earlier_messages]
    assert "synthetic earlier question two" in texts, texts
    assert "synthetic earlier question one" not in texts, "a turn older than the read frame was kept"


def test_the_live_turn_number_equals_the_recorded_one_despite_test_and_later_rows(
        session_factory, recorded_with_history, live_env):
    def turn_no(trace):
        return next(rec["turn_no"] for rec in _walk(trace) if isinstance(rec, dict) and "turn_no" in rec
                    and "quoted_turn" in rec)

    out = go(session_factory, recorded_with_history)[0]
    assert turn_no(out["turn"]["trace"]) == turn_no(recorded_with_history["trace"])


# ------------------------------------------------------------------ review round 9 (live L3 is the recorded prompt's block)
R9_BLOCK = ("Recent exchanges, oldest first:\nUser: SYN-R9-QUESTION\nAssistant: (the Previous response)\n"
            "Previous response: SYN-R9-ANSWER")
L3_START, L3_END = "Recent exchanges, oldest first:", "Product types:"


def _prompt_event(row):
    return next(e for e in row["trace"] if isinstance(e, dict) and e.get("kind") == "prompt_text")


def _l3_slice(text_):
    a = text_.index(L3_START)
    return text_[a:text_.index(L3_END, a)]


def _set_recorded_l3(row, new_slice, est):
    ev = _prompt_event(row)
    old = _l3_slice(ev["text"])
    ev["text"] = ev["text"].replace(old, new_slice, 1)
    for rec in _walk(row["trace"]):
        layers = rec.get("layers")
        if isinstance(layers, list) and any(isinstance(x, dict) and x.get("layer") == "L3" for x in layers):
            for x in layers:
                if x.get("layer") == "L3":
                    x["est_tokens"] = est


def _drop_db_history(session_factory, row):
    db = session_factory()
    db.query(ChatbotTurn).filter(ChatbotTurn.id != row["id"]).delete()
    db.commit()


def test_a_console_turn_with_no_db_history_gets_the_recorded_l3_block_verbatim(session_factory, recorded_with_history,
                                                                              live_env):
    from app.services.chatbot.turn.context import est_tokens

    row = copy.deepcopy(recorded_with_history)
    orig = _l3_slice(_prompt_event(row)["text"])
    norm = next(f for f in (lambda s: s, lambda s: s.rstrip("\n")) if est_tokens(f(orig)) == _layer_tokens(row["trace"])["L3"])
    new_slice = R9_BLOCK + "\n"
    _set_recorded_l3(row, new_slice, est_tokens(norm(new_slice)))
    row["envelope"]["is_test"] = True
    _drop_db_history(session_factory, row)   # the stack DB holds none of the console history
    out = go(session_factory, row)[0]
    block = live_env["parse_calls"][0]
    assert R9_BLOCK in block, "the recorded L3 block is missing from the live parser input"
    assert _layer_tokens(out["turn"]["trace"])["L3"] == _layer_tokens(row["trace"])["L3"]


def test_a_recorded_prompt_with_no_l3_block_wins_over_prior_turns_in_the_db(session_factory, recorded_with_history,
                                                                           live_env):
    row = copy.deepcopy(recorded_with_history)
    _set_recorded_l3(row, "", 0)
    go(session_factory, row)   # the DB still holds both prior prod turns
    block = live_env["parse_calls"][0]
    assert PRIOR_1 not in block and PRIOR_2 not in block, "DB history was used although the recorded prompt had none"
    assert "Recent exchanges" not in block and "Previous response:" not in block


def test_a_recorded_turn_without_a_prompt_text_event_still_uses_the_as_of_db_history(session_factory,
                                                                                      recorded_with_history, live_env):
    row = copy.deepcopy(recorded_with_history)
    row["trace"] = [e for e in row["trace"] if not (isinstance(e, dict) and e.get("kind") == "prompt_text")]
    go(session_factory, row)
    block = live_env["parse_calls"][0]
    assert PRIOR_1 in block and PRIOR_2 in block
    assert DECOY_LATER not in block and DECOY_TEST not in block


# ------------------------------------------------------------------ review round 10 (old prompt layout)
def test_an_old_layout_prompt_with_exchanges_after_the_current_message_falls_back_to_the_as_of_history(
        session_factory, recorded_with_history, live_env):
    row = copy.deepcopy(recorded_with_history)
    _prompt_event(row)["text"] = (
        f"Previous response: {PRIOR_2}\\n".replace("\\n", "\n")
        + "Product types: Tap, Shower\n"
        + "Current user message: price for SRTWC8517\n"
        + f"Recent exchanges, oldest first:\nUser: synthetic earlier question one\nAssistant: {PRIOR_1}\n")
    go(session_factory, row)
    block = live_env["parse_calls"][0]
    assert PRIOR_1 in block, "the exchanges after the current message were lost (truncated slice used)"
    assert "Recent exchanges" in block


# ------------------------------------------------------------------ prompt pin (--prompt-version-id)
PIN_ID = "00000000-0000-4000-8000-0000000000aa"
PARSER_KEY = "chatbot_semantic_parser"


def _seed_prompt_version(session_factory, *, name=PARSER_KEY, version_id=PIN_ID):
    """A real ai_prompt_versions row, written inside the fixture's rolled-back transaction."""
    from app.models.ai_prompt import AIPromptVersion

    db = session_factory()
    db.add(AIPromptVersion(id=version_id, name=name, version=901, type="text", template="synthetic parser prompt"))
    db.commit()


def _cli_live(tmp_path, session_factory, row, monkeypatch, extra):
    import app.database

    monkeypatch.setattr(app.database, "SessionLocal", session_factory)
    items = tmp_path / "items.jsonl"
    items.write_text("".join(json.dumps(i) + "\n" for i in items_for(row)))
    rec = tmp_path / "recorded.jsonl"
    rec.write_text(json.dumps(row) + "\n")
    out = tmp_path / "out.jsonl"
    rc = replay_mod.main(["live", "--items", str(items), "--recorded", str(rec), "--out", str(out),
                          "--max-calls", "10", "--max-usd", "5", "--require-key-last4", LAST4, *extra])
    return rc, [json.loads(l) for l in out.read_text().splitlines()]


def test_prompt_pin_cli_flag_puts_the_override_on_every_live_payload(tmp_path, session_factory, recorded, live_env,
                                                                     monkeypatch):
    _seed_prompt_version(session_factory)
    rc, _rows = _cli_live(tmp_path, session_factory, recorded, monkeypatch, ["--prompt-version-id", PIN_ID])
    assert rc == 0
    extras = live_env["envelopes"][0].model_extra or {}
    assert extras.get("prompt_overrides") == {PARSER_KEY: PIN_ID}


def test_prompt_pin_cli_value_wins_over_a_recorded_override(tmp_path, session_factory, recorded, live_env, monkeypatch):
    row = copy.deepcopy(recorded)
    row["envelope"]["prompt_overrides"] = {PARSER_KEY: "recorded-version", "other_key": "x"}
    _seed_prompt_version(session_factory)
    rc, _rows = _cli_live(tmp_path, session_factory, row, monkeypatch, ["--prompt-version-id", PIN_ID])
    assert rc == 0
    extras = live_env["envelopes"][0].model_extra or {}
    assert extras.get("prompt_overrides") == {PARSER_KEY: PIN_ID}


def test_prompt_pin_absent_flag_leaves_no_prompt_overrides_key(tmp_path, session_factory, recorded, live_env,
                                                              monkeypatch):
    row = copy.deepcopy(recorded)
    row["envelope"]["prompt_overrides"] = {PARSER_KEY: "recorded-version"}
    rc, rows = _cli_live(tmp_path, session_factory, row, monkeypatch, [])
    assert rc == 0
    assert "prompt_overrides" not in (live_env["envelopes"][0].model_extra or {})
    assert "prompt_version_id" not in rows[0]["provenance"]


def test_prompt_pin_provenance_records_the_pinned_version_id(tmp_path, session_factory, recorded, live_env, monkeypatch):
    _seed_prompt_version(session_factory)
    rc, rows = _cli_live(tmp_path, session_factory, recorded, monkeypatch, ["--prompt-version-id", PIN_ID])
    assert rc == 0
    assert rows[0]["provenance"]["prompt_version_id"] == PIN_ID


def _assert_failed_closed(tmp_path, live_env, exc_info):
    assert exc_info.type in (ValueError, SystemExit)
    out = tmp_path / "out.jsonl"
    assert not out.exists() or out.read_text() == "", "an output row was written under an unresolved pin"
    assert live_env["envelopes"] == [] and live_env["parse_calls"] == [], "the engine ran under an unresolved pin"


def test_prompt_pin_an_unknown_id_fails_the_cli_before_any_item_runs(tmp_path, session_factory, recorded, live_env,
                                                                    monkeypatch):
    with pytest.raises((ValueError, SystemExit)) as exc:
        _cli_live(tmp_path, session_factory, recorded, monkeypatch, ["--prompt-version-id", PIN_ID])
    _assert_failed_closed(tmp_path, live_env, exc)


def test_prompt_pin_an_id_of_another_prompt_name_fails_the_cli_before_any_item_runs(
        tmp_path, session_factory, recorded, live_env, monkeypatch):
    _seed_prompt_version(session_factory, name="chatbot_router")
    with pytest.raises((ValueError, SystemExit)) as exc:
        _cli_live(tmp_path, session_factory, recorded, monkeypatch, ["--prompt-version-id", PIN_ID])
    _assert_failed_closed(tmp_path, live_env, exc)


def test_prompt_pin_live_rows_raises_value_error_on_an_unresolved_pin_before_any_item(session_factory, recorded,
                                                                                     live_env):
    with pytest.raises(ValueError):
        replay_mod.live_rows(items_for(recorded, 2), [recorded], session_factory=session_factory, max_calls=50,
                             max_usd=10.0, require_key_last4=LAST4, prompt_version_id=PIN_ID)
    assert live_env["envelopes"] == [] and live_env["parse_calls"] == []


def test_prompt_pin_a_turn_whose_prompt_version_is_none_records_no_prompt_version_id(
        tmp_path, session_factory, recorded, live_env, monkeypatch):
    _seed_prompt_version(session_factory)

    def no_version_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(system_prompt="stub", prompt_version=None, provider="openai",
                                       model="gpt-test", api_key="k")

    monkeypatch.setattr(parser_mod, "resolve_config", no_version_config)
    rc, rows = _cli_live(tmp_path, session_factory, recorded, monkeypatch, ["--prompt-version-id", PIN_ID])
    assert rc == 0
    assert rows[0]["provenance"]["prompt_version"] is None
    assert "prompt_version_id" not in rows[0]["provenance"]
