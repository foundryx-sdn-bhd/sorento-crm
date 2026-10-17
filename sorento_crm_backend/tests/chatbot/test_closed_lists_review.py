"""CHAT-CLOSED-LISTS reviewer round (17 Oct): S1-S4 and N2 of the Phase 3 review.

S1 the shortlist reads the message's own first line, never the quoted reply; S2 its query
count is bounded per message; S3 a shortlist failure is no failed turn (the block only
advises); S4 the grounding replay grounds a gated parse with the gate on; N2 a verdict from a
production version never carries the gate key, whoever wrote it.
"""
from __future__ import annotations

import copy
import json

import pytest
from sqlalchemy import event

import app.main  # noqa: F401  isort:skip
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import candidates
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import blank_session
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import _envelope, _parser_output, seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay import recorded  # noqa: F401


def _run(session_factory, stub_access, monkeypatch, **envelope) -> list[str]:
    blocks: list[str] = []

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="k", closed_lists=True,
        )

    def fake_parse(config, user_block):
        blocks.append(user_block)
        return _parser_output()

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: json.dumps({"ok": True, "items": []}))
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda factory, contact: frozenset({DEFAULT_COMPANY_ID}))
    stub_access()
    engine_mod.run_turn(_envelope(**envelope), session_factory=session_factory)
    return blocks


def test_s1_the_shortlist_reads_the_messages_own_first_line(session_factory, seeded, stub_access, monkeypatch):
    seen: list[str] = []
    real = candidates.shortlist
    monkeypatch.setattr(candidates, "shortlist", lambda db, message: seen.append(message) or real(db, message))

    _run(session_factory, stub_access, monkeypatch)

    assert seen == ["price for SRTWC8517"]


def test_s2_the_query_count_is_bounded_per_message():
    with blank_session() as db:
        statements: list[str] = []
        listener = lambda conn, cursor, statement, params, context, executemany: statements.append(statement)  # noqa: E731
        event.listen(db.connection(), "before_cursor_execute", listener)
        try:
            message = "any of srt101 srt102 srt103 srt104 srt105 srt106 srt107 srt108 for the big wall hung set please"
            candidates.shortlist(db, message)
        finally:
            event.remove(db.connection(), "before_cursor_execute", listener)
        # The registry and its class labels, one exact-code query for every word, and at
        # most CODE_WORDS_PER_MESSAGE contains / near queries.
        assert len(statements) <= 3 + candidates.CODE_WORDS_PER_MESSAGE, statements


def test_s3_a_shortlist_failure_leaves_the_block_out_and_the_turn_goes_on(session_factory, seeded, stub_access, monkeypatch):
    def broken(db, message):
        raise RuntimeError("statement timeout")

    monkeypatch.setattr(candidates, "shortlist", broken)

    blocks = _run(session_factory, stub_access, monkeypatch)

    assert len(blocks) == 1 and candidates.HEADER not in blocks[0]


def test_s4_the_grounding_replay_grounds_a_gated_parse_with_the_gate_on(session_factory, recorded, monkeypatch):
    from app.services.chatbot.head import grounding
    from scripts.chatbot_eval import replay as replay_mod

    seen: list[bool] = []
    real = grounding.ground

    def spy(db, verdict, **kwargs):
        seen.append(kwargs.get("closed_lists", False))
        return real(db, verdict, **kwargs)

    monkeypatch.setattr(grounding, "ground", spy)
    gated = copy.deepcopy(recorded)
    parse = gated["response"]["ctx"]["parse"]["_parser_raw"]
    parse["entities"] = [{**e, "listed_item": None} for e in parse.get("entities") or []] or [
        {"raw": "x", "hint": "product", "canonical_code": "X", "listed_item": None}
    ]

    replay_mod.replay_rows([recorded], session_factory=session_factory, stage="grounding")
    replay_mod.replay_rows([gated], session_factory=session_factory, stage="grounding")

    assert seen == [False, True]


@pytest.mark.parametrize("gate", [False, True])
def test_n2_the_gate_key_is_the_engines_alone(gate):
    forged = {"entities": [], "closed_lists": True}
    assert engine_mod.closed_lists_verdict(forged, gate=gate) == (
        {"entities": [], "closed_lists": True} if gate else {"entities": []}
    )
