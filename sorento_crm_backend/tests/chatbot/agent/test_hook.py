"""AGENT-LOOP-SPIKE hook tests (AC-1 .. AC-4, AC-20/21 end to end, AC-22, AC-24 trace shape).

Drives the real `engine.run_turn` over the blank Postgres schema (`tests/chatbot/conftest.py`),
reusing `test_engine.py`'s harness. The agent's model, MCP, resolver and SO lookup are injected
fakes; nothing reaches an LLM or the MCP server. RED until the package and flag exist.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from app.config import settings
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod

from tests.chatbot.agent._fakes import CATALOG, FakeChat, McpRecorder, MARKER, Recorder, result, tool_call
from tests.chatbot.test_engine import CONTACT_ID, _envelope, seeded, stub_access, stub_parser  # noqa: F401


def _agent_pkg():
    import app.services.chatbot.agent as agent_pkg

    return agent_pkg


@pytest.fixture()
def agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chatbot_agent_engine", True)


@pytest.fixture()
def agent_calls(monkeypatch):
    """Replace `run_agent_stages` with a recorder; a call is the failure signal for AC-1/2/4."""
    pkg = _agent_pkg()
    calls: list[dict[str, Any]] = []

    def recorder(envelope, **kwargs):
        calls.append(kwargs)
        raise AssertionError("the agent must not have been called")

    monkeypatch.setattr(pkg, "run_agent_stages", recorder)
    monkeypatch.setattr(engine_mod, "run_agent_stages", recorder, raising=False)
    return calls


@pytest.fixture()
def fakes(monkeypatch):
    """Wrap the real `run_agent_stages`, injecting the scripted chat + fake MCP + catalog."""
    pkg = _agent_pkg()
    real = pkg.run_agent_stages
    box: dict[str, Any] = {"chat": FakeChat(), "mcp": McpRecorder(), "resolve": Recorder(), "so": Recorder()}

    def wrapper(envelope, **kwargs):
        kwargs.update(
            chat=box["chat"], mcp_call=box["mcp"], resolve_call=box["resolve"], so_lookup=box["so"], catalog=CATALOG
        )
        return real(envelope, **kwargs)

    monkeypatch.setattr(pkg, "run_agent_stages", wrapper)
    monkeypatch.setattr(engine_mod, "run_agent_stages", wrapper, raising=False)
    return box


def _row(session_factory, turn_id) -> ChatbotTurn:
    return session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()


def _reply_text(res) -> str:
    return " ".join(str(a.get("text") or "") for a in (res.actions or []) if a.get("kind") == "send_message") or str(
        (res.reply or {}).get("text") or ""
    )


# ----------------------------- Switch: AC-1 .. AC-4 -----------------------------


def test_ac1_setting_defaults_off_and_hook_needs_both_flags(monkeypatch):
    assert settings.chatbot_agent_engine is False
    assert engine_mod._agent_engine_on(True) is False
    monkeypatch.setattr(settings, "chatbot_agent_engine", True)
    assert engine_mod._agent_engine_on(True) is True
    assert engine_mod._agent_engine_on(False) is False


def test_ac1_setting_off_dry_run_runs_todays_stages(session_factory, seeded, stub_parser, stub_access, agent_calls):
    parser_calls: list[Any] = []
    stub_parser(on_call=parser_calls.append)
    stub_access()
    res = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    assert agent_calls == []
    assert parser_calls  # today's parser ran
    assert res.branch_kind != "agent"


def test_ac2_setting_on_live_turn_runs_todays_stages(session_factory, seeded, stub_parser, stub_access, agent_on, agent_calls):
    parser_calls: list[Any] = []
    stub_parser(on_call=parser_calls.append)
    stub_access()
    res = engine_mod.run_turn(_envelope(mode="live"), session_factory=session_factory)
    assert agent_calls == []
    assert parser_calls
    assert res.is_test is False
    assert res.branch_kind != "agent"


def test_ac3_setting_on_dry_run_the_agent_answers(session_factory, seeded, stub_parser, stub_access, agent_on, fakes):
    parser_calls: list[Any] = []
    stub_parser(on_call=parser_calls.append)
    stub_access()
    fakes["chat"] = FakeChat([
        result(calls=[tool_call("crm_inventory_stock_balance_list", {"product_ids": ["SRT-TEST-1"]})]),
        result("Here you go\n{{block:1}}"),
    ])
    res = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    assert parser_calls == []  # the old head never ran
    assert res.branch_kind == "agent"
    assert res.is_test is True
    assert res.actions and all(a["dry_run"] is True for a in res.actions)
    body = _reply_text(res)
    assert "Here you go" in body and "SRT-TEST-1" in body and "{{" not in body
    row = _row(session_factory, res.turn_id)
    assert row.status == "done" and row.branch_kind == "agent" and row.is_test is True


def test_ac4_ai_not_allowed_contact_stays_silent_with_agent_on(session_factory, seeded, stub_parser, stub_access, agent_on, agent_calls):
    stub_parser()
    stub_access()
    db = session_factory()
    db.execute(text("UPDATE respond_contacts SET chatbot_allowed = false WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)})
    db.commit()
    res = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    assert agent_calls == []
    assert res.branch_kind == "ai_not_allowed"
    assert res.actions == []


# ----------------------------- Memory end to end: AC-20 / AC-21 -----------------------------


def test_ac20_ac21_patch_then_next_turn_prompt_holds_prior_messages_and_records(
    session_factory, seeded, stub_parser, stub_access, agent_on, fakes
):
    stub_parser()
    stub_access()
    fakes["chat"] = FakeChat([
        result(calls=[tool_call("crm_master_products_list", {"query": "SRT-TEST-1"})]),
        result("Found it\n{{block:1}}"),
    ])
    first = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    patch = first.session_patch
    assert patch and "agent" in patch
    agent_state = patch["agent"]
    assert set(agent_state) >= {"messages", "records", "offered"}
    assert any(m.get("content") == "price for SRTWC8517" for m in agent_state["messages"])

    fakes["chat"] = FakeChat([result("Same one, yes.")])
    env2 = _envelope(is_test=True, previous_conversation_state=patch)
    env2.message["message"]["messageId"] = "ZZT-msg-2"
    env2.message["message"]["message"]["text"] = "and the one before?"
    engine_mod.run_turn(env2, session_factory=session_factory)
    prompt = fakes["chat"].prompt_text(0)
    assert "price for SRTWC8517" in prompt  # prior user message
    assert "Found it" in prompt  # prior bot reply
    assert "and the one before?" in prompt


# ----------------------------- Writes: AC-22 -----------------------------


def _row_counts(session_factory) -> dict[str, int]:
    db = session_factory()
    tables = db.execute(
        text(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname = ANY (current_schemas(false)) "
            "AND schemaname NOT IN ('pg_catalog', 'information_schema')"
        )
    ).all()
    counts: dict[str, int] = {}
    for schema, table in tables:
        counts[f"{schema}.{table}"] = db.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar()
    return counts


def test_ac22_ac23_test_turn_writes_only_the_turn_row_even_with_portal_link(
    session_factory, seeded, stub_parser, stub_access, agent_on, fakes
):
    stub_parser()
    stub_access()
    fakes["chat"] = FakeChat([
        result(calls=[
            tool_call("portal_link", {"submission_type": "order"}, "c1"),
            tool_call("crm_master_products_list", {"query": "x"}, "c2"),
        ]),
        result("{{block:1}}\n{{block:2}}"),
    ])
    before = _row_counts(session_factory)
    res = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    after = _row_counts(session_factory)
    assert res.branch_kind == "agent"
    changed = {t for t in after if after[t] != before.get(t)}
    assert all("chatbot_turns" in t or t.endswith(".turns") for t in changed), changed
    assert changed, "the turn row itself must exist"
    assert "crm_portal_link_get" not in [c[0] for c in fakes["mcp"].calls]


# ----------------------------- Trace: AC-24 -----------------------------


def test_ac24_trace_carries_summed_tokens_and_tool_envelopes(
    session_factory, seeded, stub_parser, stub_access, agent_on, fakes
):
    stub_parser()
    stub_access()
    fakes["mcp"] = McpRecorder(raises={"crm_master_brands_list": RuntimeError("boom-brands")})
    fakes["chat"] = FakeChat([
        result(
            calls=[
                tool_call("crm_master_products_list", {"query": "SRT-TEST-1"}, "c1"),
                tool_call("crm_master_brands_list", {}, "c2"),
            ],
            prompt=100, cached=40, out=10,
        ),
        result("Done {{block:1}}", prompt=150, cached=60, out=20),
    ])
    res = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    row = _row(session_factory, res.turn_id)
    assert row.status == "done" and row.branch_kind == "agent"
    trace = [r for r in (row.trace or []) if isinstance(r, dict)]
    understood = next(r for r in trace if r.get("stage") == "understood")
    facts = understood["facts"]
    assert facts["prompt_tokens"] == 250
    assert facts["cached_tokens"] == 100
    assert facts["completion_tokens"] == 30
    assert facts["rounds"] == 2
    looked = next(r for r in trace if r.get("stage") == "looked_up")
    envelopes = looked["raw"]["envelopes"]
    by_tool = {e["tool"]: e for e in envelopes}
    assert set(by_tool) == {"crm_master_products_list", "crm_master_brands_list"}
    assert not by_tool["crm_master_products_list"].get("error")
    assert "boom-brands" in str(by_tool["crm_master_brands_list"]["error"])
    json.dumps(trace)  # persisted shape is JSON-safe


def test_ac10_rate_limit_end_to_end_closes_the_row_with_the_busy_reply(
    session_factory, seeded, stub_parser, stub_access, agent_on, fakes
):
    from app.services.chatbot.llm_call import RATE_LIMITED_REPLY, RateLimited

    stub_parser()
    stub_access()
    fakes["chat"] = FakeChat([RateLimited("429")])
    res = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    assert RATE_LIMITED_REPLY in _reply_text(res)
    assert _row(session_factory, res.turn_id).status == "done"
