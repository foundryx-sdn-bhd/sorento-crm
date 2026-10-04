"""O2: the three harness keys a DRY-RUN envelope may carry (AC-112).

Agreed with the n8n side so the fail-closed clone and the chat console can drive a turn
without an LLM and without the contact's real memory. Named after the n8n guards they
replace: **G6** is the reformulator bypass, **G8** is the session injection.

The rule has two halves and both are the contract:

* on a DRY RUN (`is_test`, a `test_run_id`, or `mode != live`) the keys are HONOURED;
* on a LIVE envelope they are IGNORED, and the fact that one was present is recorded as
  `harness_keys_ignored` on the `received` trace record - silence there would let a
  harness envelope reach a real customer and answer them from a mock.

Postgres only, blank schema, via `tests/chatbot/conftest.py`. Nothing here reaches an LLM:
the point of G6 is that there is no provider call to reach.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    _turn_row,
    seeded,
    stub_access,
    stub_parser,
)


def _record(trace: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    """The one STAGE record for `stage`, never an event.

    AC-1592 port: `Trace.persisted()` now interleaves stage records (`{"stage": ...}`)
    with sub-events a stage's own `add()` calls append (`{"kind": ...}`, no `"stage"`
    key at all - `trace.py::add`'s own docstring). A bare `r["stage"]` scan over the
    combined list raised `KeyError` on the first event; `.get` reads "not a stage
    record" as "not this one" instead.
    """
    rows = [r for r in trace if r.get("stage") == stage]
    assert len(rows) == 1, f"expected exactly one {stage!r} record, got {len(rows)}: {trace}"
    return rows[0]


def _mock_output(**overrides: Any) -> dict[str, Any]:
    """A parser emission the harness supplies INSTEAD of the model's."""
    return _parser_output(user_goal="supplied by the harness", **overrides)


@pytest.fixture()
def exploding_parser(monkeypatch):
    """`parser.parse` must NEVER be called on a bypassed turn - so make it fatal.

    A stub that merely records the call would let a bypass that silently fell through to
    the model still pass, because the stub's output is shaped like a real one.
    """

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub",
            prompt_version=1,
            provider="openai",
            model="gpt-test",
            api_key="sk-test",
        )

    def never(config, user_block):  # pragma: no cover - the assertion IS that it never runs
        raise AssertionError(
            "the parser was called on a turn the harness bypassed: G6 did not take effect"
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", never)


def _session_vars(session_factory) -> Any:
    return (
        session_factory()
        .execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        )
        .scalar()
    )


class TestHarnessInjectionsG6:
    """`mock_reformulator_output` replaces the parser LLM call, on a dry run only."""

    def test_a_dry_run_bypasses_the_parser_and_post_processes_the_mock(
        self, session_factory, seeded, exploding_parser, stub_access
    ) -> None:
        stub_access()
        envelope = _envelope(
            test_run_id="ZZT-o2-g6",
            # `asks`/`topic_reset` join DECLARED_KEYS (coder 14's last item, 4427bb6bb) -
            # `_mock_output`'s base template (`_parser_output`) predates them, so a
            # harness-supplied emission needs them named explicitly or `assert_emission`
            # fails the bypassed turn at `understood`.
            mock_reformulator_output=_mock_output(
                domain_hint="inventory", asks=None, topic_reset=None
            ),
        )
        assert envelope.dry_run is True

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        # The mock went through the SAME post-processing a real parse takes, so the turn
        # routes off derived state, not off whatever the harness happened to type.
        assert result.ctx["parse"]["output"]["domain_hint"] == "inventory"
        assert result.ctx["parse"]["output"]["user_goal"] == "supplied by the harness"
        assert result.ctx["parse"]["_parser_raw"]["domain_hint"] == "inventory"
        assert result.branch_kind is not None

    def test_the_understood_record_says_the_parser_was_bypassed(
        self, session_factory, seeded, exploding_parser, stub_access
    ) -> None:
        stub_access()
        result = engine_mod.run_turn(
            _envelope(
                is_test=True,
                # Same DECLARED_KEYS gap as the test above.
                mock_reformulator_output=_mock_output(asks=None, topic_reset=None),
            ),
            session_factory=session_factory,
        )
        record = _record(_turn_row(session_factory, result.turn_id).trace, "understood")
        assert record["summary"] == "Parser bypassed by harness."
        assert record["facts"]["parser_bypassed"] is True
        # Still a legible sentence, like every other record (AC-007).
        assert " " in record["summary"] and " " in record["why"]

    def test_a_malformed_mock_is_a_failed_understood_stage_not_a_crash(
        self, session_factory, seeded, exploding_parser, stub_access
    ) -> None:
        """The harness handed us something that is not a parser emission.

        It takes the SAME path a malformed model answer takes (R5 / H44): a failed turn at
        `understood` with today's error reply, never a soft default and never a 500.
        """
        stub_access()
        result = engine_mod.run_turn(
            _envelope(is_test=True, mock_reformulator_output="not an object"),
            session_factory=session_factory,
        )
        assert result.status == "failed"
        assert result.stage == "understood"
        assert _turn_row(session_factory, result.turn_id).status == "failed"

    def test_a_mock_that_is_an_object_but_not_an_emission_names_the_missing_key(
        self, session_factory, seeded, exploding_parser, stub_access
    ) -> None:
        """The production case, 5 Sep 2026: `mock_reformulator_output = {"nope": true}`.

        It IS an object, so the "did not emit a JSON object" guard passes it, and the first
        hard subscript hundreds of lines into `post_process` used to raise a bare
        `KeyError: 'reference_positions'`. The turn failed at the right stage, but the row
        carried a Python type name as its explanation - which reads as a CRM fault rather
        than a malformed emission, and says nothing about what to fix.

        The stage and status are unchanged (R5 / H44); what this pins is that the ERROR
        names the key, and does so for a real model answer too, since both go through the
        same check.

        **Confirmed ENGINE DEFECT, kept red, NOT retired (16 Sep 2026, tester):** measured
        directly - the current engine no longer rejects a malformed verdict at `understood`
        at all. `{"nope": True}` is coerced with defaults for every missing field (rather
        than the old hard subscript failing fast) and the turn runs all the way through to
        `status == "done"`, reaching entity resolution with a garbage token ("nothing",
        apparently a stray default value) along the way. R5/H44's own rule - a malformed
        parser emission fails the turn at `understood`, the same path a malformed MODEL
        answer takes, never a soft default - is still the stated contract nothing in this
        session's reading of the PLAN/UAC retires; this reads as a real validation gap in
        the post-rearch verdict pipeline (APPLY's tolerant defaulting swallowing what the
        old hard KeyError used to catch), not a stale assertion. Flagged for a coder/captain
        look, not silently softened or invented around.
        """
        stub_access()
        result = engine_mod.run_turn(
            _envelope(is_test=True, mock_reformulator_output={"nope": True}),
            session_factory=session_factory,
        )

        assert result.status == "failed"
        assert result.stage == "understood"
        row = _turn_row(session_factory, result.turn_id)
        assert row.status == "failed"
        assert row.stage == "understood"

        error = row.error or ""
        assert "parser emission missing" in error, error
        assert "'reference_positions'" in error, error
        # The old message, and nothing like it: a bare exception type on the trace is the
        # defect this closes.
        assert "KeyError" not in error, error
        record = _record(row.trace, "understood")
        assert record["status"] == "failed"
        assert "parser emission missing" in (record["error"] or "")

    def test_a_live_envelope_ignores_the_mock_and_says_so(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        """The model is asked, the mock is not used, and the stray key is VISIBLE."""
        stub_parser(_parser_output(domain_hint="order"))
        stub_access()
        envelope = _envelope(mock_reformulator_output=_mock_output(domain_hint="inventory"))
        assert envelope.dry_run is False

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert result.ctx["parse"]["output"]["domain_hint"] == "order", (
            "a live turn must answer from the model, never from an envelope key"
        )
        received = _record(_turn_row(session_factory, result.turn_id).trace, "received")
        assert received["facts"]["harness_keys_ignored"] == ["mock_reformulator_output"]
        understood = _record(_turn_row(session_factory, result.turn_id).trace, "understood")
        assert understood["facts"].get("parser_bypassed") is not True

    def test_a_live_turn_without_harness_keys_records_an_empty_list(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        """Absent must not be indistinguishable from ignored: the key is always present."""
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        received = _record(_turn_row(session_factory, result.turn_id).trace, "received")
        assert received["facts"]["harness_keys_ignored"] == []


class TestHarnessInjectionsG8:
    """`previous_conversation_state` / `referenced_result_set` replace the stored memory."""

    CARRIED = {
        "domain_hint": "incoming",
        "selection_context": "disambiguation",
        "last_result_set": [{"idx": 1, "code": "ZZT-1"}],
    }
    REFERENCED = [{"idx": 1, "code": "ZZT-QUOTED"}]

    def test_a_dry_run_uses_the_injected_state_instead_of_the_stored_one(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        seen: dict[str, Any] = {}

        def _capture(user_block):
            seen["user_block"] = user_block

        stub_parser(on_call=_capture)
        stub_access()

        result = engine_mod.run_turn(
            _envelope(
                is_test=True,
                previous_conversation_state=self.CARRIED,
                referenced_result_set=self.REFERENCED,
            ),
            session_factory=session_factory,
        )

        session = result.ctx["session"]["session_vars"]
        assert session["variables"] == self.CARRIED
        assert session["referenced_result_set"] == self.REFERENCED
        assert seen["user_block"], "the parser still runs on an injected-state turn"

    def test_the_injected_state_is_never_written_back(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        """D14's zero-writes rule, at the one place O2 could have broken it."""
        stub_parser()
        stub_access()
        before = _session_vars(session_factory)

        engine_mod.run_turn(
            _envelope(
                test_run_id="ZZT-o2-g8",
                previous_conversation_state=self.CARRIED,
                referenced_result_set=self.REFERENCED,
            ),
            session_factory=session_factory,
        )

        assert _session_vars(session_factory) == before

    def test_zero_rows_outside_chatbot_turns_on_an_injected_dry_run(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        stub_parser()
        stub_access()
        db = session_factory()
        turns_before = db.query(ChatbotTurn).count()
        contacts_before = db.execute(text("SELECT COUNT(*) FROM respond_contacts")).scalar()

        engine_mod.run_turn(
            _envelope(is_test=True, previous_conversation_state=self.CARRIED),
            session_factory=session_factory,
        )

        after = session_factory()
        assert after.query(ChatbotTurn).count() == turns_before + 1, "the turn row IS written"
        assert (
            after.execute(text("SELECT COUNT(*) FROM respond_contacts")).scalar()
            == contacts_before
        )

    def test_a_live_envelope_ignores_the_injection_and_says_so(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        stub_parser()
        stub_access()
        envelope = _envelope(
            previous_conversation_state=self.CARRIED, referenced_result_set=self.REFERENCED
        )
        assert envelope.dry_run is False

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        session = result.ctx["session"]["session_vars"]
        assert session["variables"] == {}, (
            "a live turn reads the CONTACT's memory; an envelope key must not replace it"
        )
        received = _record(_turn_row(session_factory, result.turn_id).trace, "received")
        assert received["facts"]["harness_keys_ignored"] == [
            "previous_conversation_state",
            "referenced_result_set",
        ]

    def test_all_three_ignored_keys_are_listed_in_a_stable_order(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        """One list, in the order the contract declares them, so a diff of two traces is
        readable rather than order-dependent."""
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(
            _envelope(
                mock_reformulator_output=_mock_output(),
                previous_conversation_state=self.CARRIED,
                referenced_result_set=self.REFERENCED,
            ),
            session_factory=session_factory,
        )
        received = _record(_turn_row(session_factory, result.turn_id).trace, "received")
        assert received["facts"]["harness_keys_ignored"] == [
            "mock_reformulator_output",
            "previous_conversation_state",
            "referenced_result_set",
        ]


# --------------------------------------------------------------------------- #
# Fix 3 (PLAN-chatbot-order-status-all-orders-23sep.md, console contact 900000008,
# turns 20:41:40 / 20:41:51 +09, 23 Sep 2026). `_inject_harness_session` wrote the
# harness `previous_conversation_state` into `session_vars["variables"]` only, never
# touching the stored row's own top-level `focus`/`open_question`/etc -
# `session_state.five_keys` returns the STORED top-level keys the moment ANY of them
# is present and never looks at `variables` in that case, so injection silently did
# nothing for any contact whose stored row was already in the new five-key shape
# (every contact since #952): a `customer_pick` the console showed one turn earlier
# was invisible, and a numbered reply resolved against the contact's REAL stored
# question instead.
# --------------------------------------------------------------------------- #


class TestFix3HarnessFiveKeyStateReplacesTheStoredFiveKeys:
    """Unit-level on `_inject_harness_session` + `turn_runtime.load_state`, the same
    two functions a real turn calls in sequence (`engine.py` ~1205 then ~1218)."""

    #: A stored row already in the NEW five-key shape - the case injection used to be
    #: unable to override at all.
    STORED_SESSION_VARS: dict[str, Any] = {
        "focus": {
            "customers": [{"uuid": "zzt-stored-customer", "canonical_code": "ZZTSTORED"}],
        },
        "open_question": {
            "kind": "outstanding_detail",
            "options": [],
            "expects": None,
            "team": None,
            "asked_at_turn": 3,
            "payload": {},
        },
        "ideation": None,
        "access_levels": [],
        "contains_flyer": False,
    }

    def _session_block(self) -> dict[str, Any]:
        return {"respond_io_id": "zzt-fix3", "session_vars": dict(self.STORED_SESSION_VARS)}

    def test_ac_1868_harness_five_key_state_replaces_the_stored_five_keys(self) -> None:
        """The measured defect: the stored row has an open `outstanding_detail`
        question and a stored customer; the harness names a DIFFERENT customer and a
        `customer_pick` question. Before the fix, `load_state` still returned the
        stored `outstanding_detail` pending and the stored customer - RED."""
        from app.services.chatbot import turn_runtime
        from app.services.chatbot.turn.state import Profile

        harness_state = {
            "focus": {
                "customers": [{"uuid": "zzt-harness-customer", "canonical_code": "ZZTHARNESS"}],
            },
            "open_question": {
                "kind": "customer_pick",
                "options": [
                    {"position": 1, "label": "Customer A", "entity_type": "customer"},
                    {"position": 2, "label": "Customer B", "entity_type": "customer"},
                ],
                "expects": "pick",
                "team": None,
                "asked_at_turn": 5,
                "payload": {},
            },
        }
        envelope = _envelope(is_test=True, previous_conversation_state=harness_state)

        injected = engine_mod._inject_harness_session(self._session_block(), envelope)
        state = turn_runtime.load_state(injected, profile=Profile(), turn_no=1)

        assert state.pending is not None and state.pending.kind == "customer_pick", (
            f"the harness question must win over the stored outstanding_detail one: {state.pending}"
        )
        assert [o.get("label") for o in state.pending.options] == ["Customer A", "Customer B"]
        assert [c.get("uuid") for c in state.focus.customers] == ["zzt-harness-customer"], (
            "the harness focus customer must win over the stored one"
        )

    def test_ac_1869_empty_harness_state_erases_the_stored_five_keys_for_the_turn(self) -> None:
        """`{}` is membership, not truthiness (`_harness_keys_present`'s own rule): the
        harness is saying "this contact remembers nothing", not "I said nothing"."""
        from app.services.chatbot import turn_runtime
        from app.services.chatbot.turn.state import Profile

        envelope = _envelope(is_test=True, previous_conversation_state={})

        injected = engine_mod._inject_harness_session(self._session_block(), envelope)
        state = turn_runtime.load_state(injected, profile=Profile(), turn_no=1)

        assert state.pending is None, "an empty harness state remembers no open question"
        assert state.focus.customers == [], "an empty harness state remembers no focus"

    def test_ac_1870_legacy_flat_harness_shape_still_projects_through_variables(self) -> None:
        """The legacy flat shape (`contracts.LegacyVariables`, no five-key names) keeps
        today's behaviour - it lands in `variables` - but the stored top-level five
        keys must be stripped so `session_state.five_keys` falls through to the
        legacy projection instead of returning the stored ones untouched."""
        from app.services.chatbot import turn_runtime
        from app.services.chatbot.turn.state import Profile

        legacy_state = {
            "pending": {"kind": "escalation_offer", "team": "customer_service", "ttl": 2},
            "domain_hint": "order",
        }
        envelope = _envelope(is_test=True, previous_conversation_state=legacy_state)

        injected = engine_mod._inject_harness_session(self._session_block(), envelope)

        assert injected["session_vars"]["variables"] == legacy_state, (
            "unchanged behaviour: a legacy flat shape still lands in `variables`"
        )
        assert "focus" not in injected["session_vars"], (
            "the stored top-level five keys must be stripped so `five_keys` falls "
            "through to the legacy projection instead of leaking the stored ones"
        )

        state = turn_runtime.load_state(injected, profile=Profile(), turn_no=1)
        assert state.pending is not None and state.pending.kind == "team_pick", (
            f"the legacy nest's own projection must win, not the stored outstanding_detail: {state.pending}"
        )
        assert state.focus.customers == [], (
            "the stored customer must not leak through a legacy-shaped harness value"
        )
