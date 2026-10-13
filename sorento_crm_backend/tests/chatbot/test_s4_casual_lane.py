"""RED tests for chatbot S4 - the low_signal (casual / clarification) lane.

Contract: documentation/plans/chatbot/chatbot-turn-engine-acceptance-criteria.md S4
(AC-401 to AC-404), D11, D14, D16, R4; PLAN section S4 ("lanes/casual.py: resolve-for-prompt
(in-process), construct-user-prompt port, prompt key chatbot_clarifier, central-exchange
fence-stripping. Error = failed turn + today's text (AC-403).").

Written BEFORE `app/services/chatbot/lanes/casual.py` exists (Phase 2 test-first). Every
test below fails today for ONE of three reasons, and each test says which:

1. `app.services.chatbot.lanes.casual` does not exist yet - `ModuleNotFoundError`. Most
   tests hit this via the lazily-imported `_casual()` helper (not a top-level import, so
   a missing module fails each test individually instead of erroring the whole file's
   collection).
2. `"low_signal"` is still in `contracts.DELEGATED_BRANCH_KINDS` (S1's default: every
   branch kind still hands off to n8n) - a plain assertion failure, no import needed.
3. `chatbot_clarifier` is not yet registered in `ai_prompt_registry.PROMPT_KEYS`.

Source behaviour ported (read-only, sibling n8n checkout):
`sorento_crm_n8n/n8n-workflows-init/export/sub-casual-llm-live/workflow.json` (the
`resolve-entity-clarification` httpRequest `jsonBody`, the `Basic LLM Chain` system
prompt inline in `messages.messageValues[0].message`, and its user prompt template),
`.../nodes/construct-user-prompt.js`, `.../nodes/mark-casual-error.js`, and
`sub-answer-live/nodes/central-exchange.js` (the fence-stripping parse both lanes share).
The LLM-failure reply text is `sub-error-logger/set-ran-query-formulator.js`'s
`` `There is some error encountered by the AI: ${error}` ``, read verbatim from
`export/sub-error-logger-live/workflow.json`.

**Design decisions this file bakes into the contract** (so the coder implements against
one shape, not a guess):

- `resolve_for_prompt(db, *, ctx)` - session-bound, calls `_resolve_input` (the function
  `app.api.v1.system.references.resolve_reference_post` calls) with the exact body
  `resolve-entity-clarification` posts today. `contact_id` / `space_id` ride on the n8n
  node's URL query string but neither is read by `_resolve_input` (grepped - no match),
  so they carry no signal worth threading through here.
  Patched at its DEFINITION (`app.api.v1.system.references._resolve_input`) rather than
  via a name bound inside `casual.py`, matching this package's existing lazy-import
  convention (`head/access.py`, `head/parser.py`) so a lazy import inside the function
  stays patchable at the source.
- `construct_user_prompt(ctx, resolved)` - pure, the six-field dict.
  `central_exchange(item)` - pure, the fence-stripping parse.
- `resolve_clarifier_config(db)` / `call_clarifier(config, user_prompt)` - the SAME
  session-bound-config / no-session-call split `head/parser.py` already uses for the
  semantic parser (`resolve_config` / `parse`), for the identical capacity reason (the
  96/100-connection incident; never hold a DB session across LLM I/O). `call_clarifier`
  raises `ClarifierError` on any provider failure; nothing here reaches a real LLM.
- `casual.DEFAULT_MODEL == "gpt-4.1-mini"` - the `OpenAI Chat Model` node's hard-coded
  model, used unless `ai_prompt_registry.agent_model(db, "chatbot_clarifier")` overrides
  it (the same per-agent override mechanism `head/parser.py` already reads).
- The engine wires `branch_kind == "low_signal"` to this lane in-process: `delegate` is
  `None`, the reply is the clarifier's `response` text (or the LLM-failure text on a
  `ClarifierError`), one `send_message` action, and the row's `branch_kind` stays
  `"low_signal"` even when the LLM call itself fails at `stage = "casual_llm"` (routing
  already succeeded by then - only the clarifier call failed). That means a
  `ClarifierError` raised near the lane call must be caught THERE, the same way
  `ParserError` / `ParserOutputError` are caught explicitly around the parser call in
  `engine._run_stages` - `run_turn`'s outer catch-all sets `branch_kind = None` and the
  GENERIC parser-error reply unconditionally, which is the wrong shape for this lane.

Nothing here reaches a real LLM, n8n, respond.io or the MCP server.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import CRM_COMPLETED_BRANCH_KINDS
from app.services.chatbot.delegate import delegate_for
from tests.chatbot import _corpus
from tests.chatbot.test_engine import (  # noqa: F401 - re-exported fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    _turn_row,
    seeded,
    stub_access,
    stub_parser,
)

CASUAL_MODULE = "app.services.chatbot.lanes.casual"

# The LIVE `Basic LLM Chain` system prompt (AC-401's "fallback = the inline n8n system
# prompt verbatim"), read from `export/sub-casual-llm-live/workflow.json` at
# test-authoring time (5 Sep 2026) and frozen here as a JSON string literal so the
# curly quotes, the en dash and the non-breaking hyphen it contains travel byte-for-byte
# rather than risking mangling through an editor's encoding. `json.loads` is the decode
# step. Every non-ASCII character is written as a `\uXXXX` escape and every backslash is
# doubled, so the literal is pure ASCII while decoding to exactly the live bytes - the
# repo forbids a literal en dash anywhere, and `CLARIFIER_LIVE_PROMPT_SHA256` below is
# what proves the escaping did not change a single byte.
_CLARIFIER_LIVE_PROMPT_JSON = '''"You are the Sorento Small Talk and Clarification Assistant.\\n\\nYou ONLY handle:\\n\\ncasual messages (greetings, thanks, small talk), and\\n\\nunclear or incomplete business requests that need clarification.\\n\\nThe main business assistant and MCP tools are handled by other agents.\\n\\nINPUT CONTEXT:\\n\\nmessage_type can be \\"clarification\\", \\"casual\\", \\"unknown\\", or \\"confirmation\\".\\n\\nintent_hint and domain_hint may be null when the request is vague.\\n\\nuser_goal is a brief summary of what the user seems to want.\\n\\nRULES:\\n\\nBe brief, friendly, and professional.\\n\\nDo NOT mention tools, workflows, or internal systems.\\n\\nDo NOT ask for IDs, order numbers, or any detailed business data.\\n\\nDo NOT give detailed product, promotion, stock, or order answers. Another agent will handle detailed answers.\\n\\nIf message_type is \\"clarification\\" OR intent_hint and domain_hint are both null, your MAIN job is to ask ONE short clarifying question so you understand what the user wants.\\n\\nOnly use a reply like \u201cthe system will check and respond shortly\u201d when the user\u2019s request is already clear (intent and domain are non\u2011null) and they are not asking anything else.\\n\\nIf the user just greets, greet back.\\n\\nIf the user says thanks, acknowledge politely and close the loop.\\n\\nKeep responses short: 1\u20133 short sentences.\\n\\nOUTPUT FORMAT:\\nReturn exactly one JSON object:\\n\\n{\\n  \\"response\\": \\"short natural-language message\\"\\n}"'''
CLARIFIER_LIVE_PROMPT = json.loads(_CLARIFIER_LIVE_PROMPT_JSON)
CLARIFIER_LIVE_PROMPT_SHA256 = "97f1d279793d6125574bc33866e0cc079935b1d4ecb69cd235ba3e78ed1d4afa"

# `set-ran-query-formulator.js` (sub-error-logger)'s old interpolation, which put the
# provider's exception text in the dealer's WhatsApp. Retired by chatbot memory S4
# (AC-MEM086, owner ruling Q10): the dealer reads the turn's own apology instead.
CLARIFIER_ERROR_PREFIX = "There is some error encountered by the AI: "

# AC-402: `construct-user-prompt` and `central-exchange` are registered in the SHARED
# `_corpus.NODE_SLUGS` (`tests/chatbot/_corpus.py`), not here. That registration
# deliberately excludes `sub-send-attachments{,-rs}` - `central-exchange` is the same
# name on two different node bodies, and only the `sub-answer` family is the
# fence-stripping parse this lane ports (see `_corpus.py`'s comment on that key for the
# measured 13/13 vs 1/4 agreement that justifies the exclusion). Nothing to add here.


def _casual():
    """Import the lane module lazily.

    A missing module then fails EACH test individually with a clear
    `ModuleNotFoundError` naming `app.services.chatbot.lanes.casual`, instead of
    erroring collection of the whole file (which a top-level import would do).
    """
    return importlib.import_module(CASUAL_MODULE)


# --------------------------------------------------------------------------- #
# Contract pin - no import needed, so this is the sharpest possible red.
# --------------------------------------------------------------------------- #


def test_low_signal_is_crm_completed_not_delegated():
    """AC-401 + the completed-lanes switch: `delegate_for` is a TWO-condition gate,
    both required - `CRM_COMPLETED_BRANCH_KINDS` (what the CODE can complete) AND
    `enabled_lanes` (what the OWNER has turned on via
    `system_settings.chatbot_completed_lanes`, default `[]`). Either alone is not
    enough, which is what makes a lane deployable ahead of its shadow window closing.
    """
    assert "low_signal" in CRM_COMPLETED_BRANCH_KINDS, (
        "S4 has not added 'low_signal' to contracts.CRM_COMPLETED_BRANCH_KINDS yet"
    )
    assert delegate_for("low_signal", frozenset({"low_signal"})) is None
    assert delegate_for("low_signal", frozenset()) == "low_signal", (
        "an owner who has not enabled the lane yet must still get n8n's answer - the "
        "code alone may not complete a turn"
    )


# --------------------------------------------------------------------------- #
# AC-401 - the clarifier prompt is a registry key, fallback = the live n8n text.
# --------------------------------------------------------------------------- #


class TestClarifierRegistry:
    def test_chatbot_clarifier_is_registered_with_the_live_fallback(self):
        from app.services import ai_prompt_registry

        spec = ai_prompt_registry.PROMPT_KEYS.get("chatbot_clarifier")
        assert spec is not None, (
            "'chatbot_clarifier' is not registered in ai_prompt_registry.PROMPT_KEYS yet"
        )
        fallback_text = spec.fallback()
        assert fallback_text == CLARIFIER_LIVE_PROMPT, (
            "the registered fallback text is not the live n8n system prompt, verbatim"
        )
        assert hashlib.sha256(fallback_text.encode("utf-8")).hexdigest() == (
            CLARIFIER_LIVE_PROMPT_SHA256
        )

    def test_default_model_is_gpt_4_1_mini(self):
        casual = _casual()
        assert casual.DEFAULT_MODEL == "gpt-4.1-mini", (
            "the OpenAI Chat Model node in sub-casual-llm hard-codes gpt-4.1-mini"
        )


# --------------------------------------------------------------------------- #
# AC-401 - resolve_for_prompt: the exact body `resolve-entity-clarification` posts.
# --------------------------------------------------------------------------- #


class TestResolveForPrompt:
    @staticmethod
    def _ctx(*, user_goal="checking a promo", entities=None, access_levels=None):
        return {
            "parse": {
                "output": {
                    "user_goal": user_goal,
                    "entities": (
                        entities
                        if entities is not None
                        else [
                            {
                                "raw": "promo123",
                                "hint": "promotion",
                                "canonical_code": None,
                                "current_message": True,
                                "confident": True,
                            }
                        ]
                    ),
                    "access_levels": access_levels if access_levels is not None else ["dealer"],
                }
            }
        }

    def test_builds_the_body_resolve_entity_clarification_posts(
        self, session_factory, monkeypatch
    ):
        casual = _casual()
        captured: dict[str, Any] = {}

        def fake_resolve_input(db, query, tokens, **kwargs):
            captured["query"] = query
            captured["tokens"] = tokens
            captured.update(kwargs)
            return {"resolutions": []}

        monkeypatch.setattr("app.api.v1.system.references._resolve_input", fake_resolve_input)
        db = session_factory()
        casual.resolve_for_prompt(db, ctx=self._ctx())

        assert captured["query"] == "checking a promo"
        assert captured["tokens"] == ["promo123"]
        assert captured["allowed_entity_types"] == ["promotion"]
        assert captured["match_mode"] == "or"
        assert captured["access_levels"] == ["dealer"]
        assert captured["fallback_to_all_types"] is True

    def test_falls_back_to_nothing_with_no_goal_or_entities(self, session_factory, monkeypatch):
        casual = _casual()
        captured: dict[str, Any] = {}

        def fake_resolve_input(db, query, tokens, **kwargs):
            captured["query"] = query
            captured["tokens"] = tokens
            captured["allowed_entity_types"] = kwargs.get("allowed_entity_types")
            return {"resolutions": []}

        monkeypatch.setattr("app.api.v1.system.references._resolve_input", fake_resolve_input)
        db = session_factory()
        ctx = self._ctx(user_goal=None, entities=[], access_levels=[])
        casual.resolve_for_prompt(db, ctx=ctx)

        assert captured["query"] == "nothing"
        assert captured["tokens"] == ["nothing"]
        assert captured["allowed_entity_types"] == ["nothing"]


# --------------------------------------------------------------------------- #
# AC-401 / AC-402 - construct_user_prompt: the six fields, verbatim.
# --------------------------------------------------------------------------- #


class TestConstructUserPrompt:
    @staticmethod
    def _ctx(*, message_type="unknown", intent_hint=None, domain_hint=None, user_goal="hi", text="hi"):
        return {
            "parse": {
                "output": {
                    "message_type": message_type,
                    "intent_hint": intent_hint,
                    "domain_hint": domain_hint,
                    "user_goal": user_goal,
                }
            },
            "session": {
                "session_vars": {"variables": {"response": "prev"}, "user_response": "prev"}
            },
            "text": {"message": {"message": {"text": text}}},
        }

    def test_builds_exactly_the_six_fields(self):
        casual = _casual()
        ctx = self._ctx(message_type="clarification")
        resolved = {
            "resolutions": [
                {
                    "token": "bd402",
                    "matches": [
                        {
                            "entity_type": "product",
                            "canonical_code": "SRTSCBD402",
                            "uuid": "2e6835c5-9dd3-4d11-98a4-76d22dde8d16",
                        }
                    ],
                }
            ]
        }
        out = casual.construct_user_prompt(ctx, resolved)
        assert set(out) == {
            "message_type",
            "intent_hint",
            "domain_hint",
            "session_vars",
            "entities",
            "user_goal",
        }
        # Only entity_type / canonical_code survive - the flatMap in the node body.
        assert out["entities"] == [{"entity_type": "product", "canonical_code": "SRTSCBD402"}]
        assert out["user_goal"] == "hi"

    def test_user_goal_falls_back_to_the_message_text(self):
        casual = _casual()
        ctx = self._ctx(user_goal=None, text="just checking")
        out = casual.construct_user_prompt(ctx, {"resolutions": []})
        assert out["user_goal"] == "just checking"

    def test_session_vars_blanked_for_casual_and_unknown(self):
        """AC-401: '...session vars blanked for casual / unknown'."""
        casual = _casual()
        for message_type in ("casual", "unknown"):
            ctx = self._ctx(message_type=message_type)
            out = casual.construct_user_prompt(ctx, {"resolutions": []})
            assert out["session_vars"] == {}, message_type

        for message_type in ("clarification", "business_query", "confirmation"):
            ctx = self._ctx(message_type=message_type)
            out = casual.construct_user_prompt(ctx, {"resolutions": []})
            assert out["session_vars"] == {
                "variables": {"response": "prev"},
                "user_response": "prev",
            }, message_type


# --------------------------------------------------------------------------- #
# AC-402 - central_exchange: the fence-stripping parse both lanes share.
# --------------------------------------------------------------------------- #


class TestCentralExchange:
    def test_fence_stripping_variants_agree(self):
        casual = _casual()
        expected = {"response": "ok"}

        fenced = {"output": '```json\n{"response": "ok"}\n```'}
        bare_text = {"output": 'Sure! {"response": "ok"} - glad to help.'}
        object_output = {"output": {"response": "ok"}}
        already_resolved = {"response": "ok"}

        assert casual.central_exchange(fenced) == expected
        assert casual.central_exchange(bare_text) == expected
        assert casual.central_exchange(object_output) == expected
        assert casual.central_exchange(already_resolved) == expected

    def test_garbage_yields_the_documented_fallback(self):
        """No `{` anywhere: the node returns the raw string unchanged. (The
        `output.quick_reply = input.quick_reply` line that follows it in the JS is a
        silent no-op - JS lets you assign a property onto a string primitive and
        discards it, so `output` stays the bare string.)
        """
        casual = _casual()
        garbage = {"output": "no json here at all"}
        assert casual.central_exchange(garbage) == "no json here at all"

    def test_empty_input_passes_through(self):
        """`raw` is empty (no `.output`, no `.text`): `output = input`."""
        casual = _casual()
        assert casual.central_exchange({}) == {}


def _run_construct_user_prompt(fixture: _corpus.Fixture) -> list:
    casual = _casual()
    ctx = fixture.first("build-ctx")["ctx"]
    resolved = fixture.first("resolve-entity-clarification")
    return [{"json": casual.construct_user_prompt(ctx, resolved)}]


def _run_central_exchange(fixture: _corpus.Fixture) -> list:
    casual = _casual()
    return [{"json": casual.central_exchange(item.get("json") or {})} for item in fixture.input]


_REPLAY_RUNNERS = {
    "construct-user-prompt": _run_construct_user_prompt,
    "central-exchange": _run_central_exchange,
}


@pytest.mark.parametrize("node", sorted(_REPLAY_RUNNERS))
def test_s4_vendored_subset_is_present(node: str) -> None:
    """AC-008: at least one committed fixture per ported node, so the replay gate
    runs in CI without the sibling n8n checkout present."""
    assert _corpus.vendored(node), (
        f"no vendored fixtures for {node} under tests/fixtures/chatbot/nodes/{node}/ - "
        "AC-008 needs a committed subset, not only the full corpus"
    )


def _replay(fixture: _corpus.Fixture) -> None:
    actual = _corpus.json_round_trip(_REPLAY_RUNNERS[fixture.node](fixture))
    expected = _corpus.json_round_trip(fixture.expected)
    assert actual == expected, (
        f"{fixture.node}/{fixture.name} diverges from the captured n8n output\n"
        f"fixture: {fixture.path}"
    )


# S3 of the review: grade BOTH shapes, the same split `test_replay.py` uses
# (`test_vendored_replay` / `test_full_corpus_replay`) - `full_corpus` alone reads
# only the sibling n8n checkout, which CI does not have, so a full_corpus-only
# parametrisation grades nothing there. `vendored()` is the always-on CI gate;
# `full_corpus()` is the richer local/owner-machine grading, skipped when absent.
@pytest.mark.parametrize(
    "fixture",
    _corpus.graded([f for node in _REPLAY_RUNNERS for f in _corpus.vendored(node)]),
    ids=lambda f: f"{f.node}/{f.name}",
)
def test_s4_vendored_replay(fixture: _corpus.Fixture) -> None:
    _replay(fixture)


@pytest.mark.parametrize(
    "fixture",
    _corpus.graded([f for node in _REPLAY_RUNNERS for f in _corpus.full_corpus(node)]) or [None],
    ids=lambda f: f"{f.node}/{f.name}" if f is not None else "corpus-absent",
)
def test_s4_full_corpus_replay(fixture) -> None:
    if fixture is None:
        pytest.skip(_corpus.corpus_skip_reason())
    _replay(fixture)


# --------------------------------------------------------------------------- #
# AC-401 / AC-403 / D14 / capacity - the lane wired into `engine.run_turn`.
# --------------------------------------------------------------------------- #


@pytest.fixture()
def low_signal_enabled(session_factory):
    """`system_settings.chatbot_completed_lanes = ["low_signal"]` - the owner's half
    of the two-condition switch (`delegate_for`'s `enabled_lanes`). Without this row
    the CRM has the code to complete `low_signal` but is not TOLD to yet, so the turn
    still delegates to n8n and the lane's own seams below are never called - the
    shadow-window default every freshly-shipped lane starts in."""
    from app.models.user import SystemSetting

    db = session_factory()
    db.add(SystemSetting(chatbot_completed_lanes=["low_signal"]))
    db.commit()


def _install_stub_lane(monkeypatch, casual, *, response_json="{\"response\": \"hi\"}", error=None):
    """Stub the lane's own seams so the engine test exercises CONTROL FLOW only -
    the resolve body, the registry fallback and the fence-stripping parse are each
    covered directly above."""
    monkeypatch.setattr(casual, "resolve_for_prompt", lambda db, *, ctx: {"resolutions": []})
    monkeypatch.setattr(casual, "resolve_clarifier_config", lambda db, **_: object())

    def fake_call_clarifier(config, user_prompt):
        if error is not None:
            raise error
        return response_json

    monkeypatch.setattr(casual, "call_clarifier", fake_call_clarifier)


class TestLowSignalLaneIntegration:
    """`engine.run_turn` end to end with the lane's own seams stubbed. Nothing here
    reaches an LLM, a DB write outside `chatbot.turns`, or a real resolve call."""

    def test_low_signal_finishes_in_turn(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch, low_signal_enabled
    ):
        """AC-401: delegate is None, reply.text is the clarifier's response, one
        send_message action, turn done."""
        casual = _casual()
        stub_parser(
            _parser_output(
                message_type="casual",
                domain_hint=None,
                intent_hint=None,
                user_goal="hi there",
                entities=[],
            )
        )
        stub_access()
        _install_stub_lane(
            monkeypatch, casual, response_json='{"response": "Hi! How can I help?"}'
        )

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "low_signal"
        assert result.delegate is None
        assert result.reply["text"] == "Hi! How can I help?"
        assert [a["kind"] for a in result.actions] == ["send_message"]
        assert result.actions[0]["text"] == "Hi! How can I help?"

        row = _turn_row(session_factory, result.turn_id)
        assert row.status == "done", row.error
        assert row.branch_kind == "low_signal"

    def test_clarifier_error_is_failed_stage(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch, low_signal_enabled
    ):
        """AC-403: a failed LLM call is a failed turn at stage=casual_llm, with
        today's sub-error-logger2 text, nothing else sent, no session write."""
        casual = _casual()
        stub_parser(_parser_output(message_type="casual", domain_hint=None, intent_hint=None))
        stub_access()
        _install_stub_lane(monkeypatch, casual, error=casual.ClarifierError("provider timeout"))

        db = session_factory()
        before = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        after = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        assert after == before, "the failed clarifier call must not write a session"

        assert result.delegate is None
        # AC-MEM086: the apology, never the exception text (the row keeps the reason).
        from app.services.chatbot_reply_copy import CHATBOT_TURN_ERROR_REPLY

        assert result.reply["text"] == CHATBOT_TURN_ERROR_REPLY
        assert "provider timeout" not in result.reply["text"]
        assert not result.reply["text"].startswith(CLARIFIER_ERROR_PREFIX)
        assert [a["kind"] for a in result.actions] == ["send_message"]

        row = _turn_row(session_factory, result.turn_id)
        assert row.status == "failed"
        assert row.stage == "casual_llm"
        assert row.branch_kind == "low_signal", (
            "routing already succeeded - only the clarifier call failed"
        )
        assert "provider timeout" in row.error

    def test_low_signal_dry_run_writes_nothing(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch, low_signal_enabled
    ):
        """D14: a test envelope for the low_signal lane writes ZERO rows outside
        `chatbot.turns`, and every action carries dry_run: true."""
        casual = _casual()
        stub_parser(_parser_output(message_type="casual", domain_hint=None, intent_hint=None))
        stub_access()
        _install_stub_lane(monkeypatch, casual, response_json='{"response": "hi"}')

        db = session_factory()
        before_session_vars = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        before_turns = session_factory().query(ChatbotTurn).count()

        envelope = _envelope(test_run_id="ZZT-s4-run-1")
        assert envelope.dry_run is True
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        after_session_vars = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        after_turns = session_factory().query(ChatbotTurn).count()

        assert after_session_vars == before_session_vars
        assert after_turns == before_turns + 1, "only the turn's own row may be written"
        assert result.actions
        assert all(a["dry_run"] is True for a in result.actions)
        # The TOP-LEVEL flag too: this arm builds its own TurnResult and used to omit
        # it, so the response said `is_test: false` on a turn that wrote nothing.
        assert result.is_test is True

    def test_no_session_across_clarifier_call(
        self,
        counting_session_factory,
        session_factory,
        seeded,
        stub_parser,
        stub_access,
        monkeypatch,
        low_signal_enabled,
    ):
        """Capacity rule: the same discipline `head/parser.py` follows for the
        semantic parser - never hold a DB session across LLM I/O. The
        96/100-connection incident is the evidence (see `engine.py`'s module
        docstring)."""
        casual = _casual()
        stub_parser(_parser_output(message_type="casual", domain_hint=None, intent_hint=None))
        stub_access()
        monkeypatch.setattr(casual, "resolve_for_prompt", lambda db, *, ctx: {"resolutions": []})
        monkeypatch.setattr(casual, "resolve_clarifier_config", lambda db, **_: object())

        observed: list[int] = []

        def fake_call_clarifier(config, user_prompt):
            observed.append(counting_session_factory.state["open"])
            return '{"response": "hi"}'

        monkeypatch.setattr(casual, "call_clarifier", fake_call_clarifier)

        engine_mod.run_turn(_envelope(), session_factory=counting_session_factory)

        assert observed == [0], (
            "a DB session was held across the clarifier call - close it before the "
            "provider I/O and reopen afterwards, the same discipline the semantic "
            "parser call already follows"
        )


# --------------------------------------------------------------------------- #
# D11 - no regex over the customer's raw text in this lane.
# --------------------------------------------------------------------------- #


def test_no_raw_text_regex_in_lane():
    """D11: everything after the parser is deterministic over STRUCTURED state -
    no regex or fuzzy match over the customer's raw text. `ctx.text` /
    `ctx.parse.output.user_goal` are the closest this lane gets to the customer's
    own words, and neither may be `re.search`/`re.match`ed."""
    path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "chatbot"
        / "lanes"
        / "casual.py"
    )
    assert path.exists(), (
        "app/services/chatbot/lanes/casual.py does not exist yet - S4 is not implemented"
    )
    source = path.read_text(encoding="utf-8")
    assert "re.search(" not in source and "re.match(" not in source, (
        "lanes/casual.py regex-matches over raw text (D11) - move that logic into "
        "the parser prompt or a deterministic lookup over structured state"
    )


# --------------------------------------------------------------------------- #
# CHAT-TURN-COST (Q2 a): the clarifier's tokens bill the turn, live turns only.
# The provider is faked BELOW `call_clarifier`, so the engine's real capture path runs.
# --------------------------------------------------------------------------- #


def _install_fake_provider(monkeypatch, casual, content='{"response": "Hi! How can I help?"}'):
    from types import SimpleNamespace

    from app.services.llm_provider import ChatResult

    class FakeProvider:
        def chat(self, messages, model=None, **kw):
            return ChatResult(content=content, prompt_tokens=900, completion_tokens=40, total_tokens=940,
                              tool_calls=[], raw=None, cached_tokens=512)

    monkeypatch.setattr(casual, "resolve_for_prompt", lambda db, *, ctx: {"resolutions": []})
    monkeypatch.setattr(
        casual, "resolve_clarifier_config",
        lambda db, **_: SimpleNamespace(system_prompt="sys", prompt_version=1, provider="openai",
                                        model="gpt-5.4-mini", api_key="k"),
    )
    monkeypatch.setattr("app.services.llm_provider.get_provider", lambda *a, **k: FakeProvider())


def _clarifier_rows(session_factory, turn_id):
    return session_factory().execute(
        text("SELECT feature, model, prompt_tokens, cached_tokens, completion_tokens FROM ai_assistant_usage_logs "
             "WHERE chatbot_turn_id = :t AND feature = 'chatbot_clarifier'"),
        {"t": str(turn_id)},
    ).all()


@pytest.mark.real_casual_llm
def test_a_live_low_signal_turn_logs_the_clarifier_call(
    session_factory, seeded, stub_parser, stub_access, monkeypatch, low_signal_enabled
):
    casual = _casual()
    stub_parser(_parser_output(message_type="casual", domain_hint=None, intent_hint=None))
    stub_access()
    _install_fake_provider(monkeypatch, casual)

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert result.reply["text"] == "Hi! How can I help?"
    rows = _clarifier_rows(session_factory, result.turn_id)
    assert [tuple(r) for r in rows] == [("chatbot_clarifier", "gpt-5.4-mini", 900, 512, 40)]


@pytest.mark.real_casual_llm
def test_a_dry_run_low_signal_turn_logs_no_clarifier_call(
    session_factory, seeded, stub_parser, stub_access, monkeypatch, low_signal_enabled
):
    casual = _casual()
    stub_parser(_parser_output(message_type="casual", domain_hint=None, intent_hint=None))
    stub_access()
    _install_fake_provider(monkeypatch, casual)

    result = engine_mod.run_turn(_envelope(test_run_id="ZZT-ctc-dry"), session_factory=session_factory)

    assert result.actions and all(a["dry_run"] is True for a in result.actions)
    assert _clarifier_rows(session_factory, result.turn_id) == []


@pytest.mark.real_casual_llm
def test_a_failing_usage_write_never_fails_the_turn(
    session_factory, seeded, stub_parser, stub_access, monkeypatch, low_signal_enabled
):
    casual = _casual()
    stub_parser(_parser_output(message_type="casual", domain_hint=None, intent_hint=None))
    stub_access()
    _install_fake_provider(monkeypatch, casual)

    def boom(*a, **k):
        raise RuntimeError("usage table unavailable")

    monkeypatch.setattr(engine_mod, "record_turn_llm_usage", boom)

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert result.reply["text"] == "Hi! How can I help?"
    assert _turn_row(session_factory, result.turn_id).status == "done"
