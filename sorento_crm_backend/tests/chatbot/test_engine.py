"""`run_turn`: the head end to end, on Postgres, with the LLM stubbed at the seam.

Covers AC-101 (the response shape and the five stages), AC-105 (a failed parse is a failed
`understood` stage with no default routing), AC-106 (the R3 dual read), AC-107 (audio that
media intake did not patch), AC-108 (the human-intervened action), AC-007 (the trace),
D14 (dry run writes nothing outside `chatbot.turns`), D15 (a duplicate message runs once)
and the plan's capacity rule (no DB session is held across the parser call).

Nothing here reaches an LLM, n8n, respond.io or the MCP server.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import TURN_STAGES, Envelope
from app.services.chatbot.head import parser as parser_mod

# An INTEGER, the shape Respond.io actually puts on the wire (`contact.id` in the
# webhook body is a JSON number). Every envelope in this suite built it as a string
# until #874 shipped a `contact_id: str` schema field the lane fed this id into raw,
# and the whole business lane died in production with no test red. Anywhere this id
# has to reach a text COLUMN (`respond_contacts.respond_io_id`,
# `chatbot_turns.contact_respond_id` - the engine stringifies it on the way in) the
# call site wraps it in `str(...)`; the envelope keeps the wire shape.
CONTACT_ID = 900000008


def _parser_output(**overrides: Any) -> dict[str, Any]:
    """A well-formed parser emission: every declared key present, as the schema demands."""
    base = {
        "message_type": "business_query",
        "intent_hint": "check_product",
        "domain_hint": "master_products",
        "scope_intent": "specific",
        "is_affirmative": None,
        "user_goal": "checking a product",
        "access_levels": [],
        "broaden_axis": None,
        "date_mode": None,
        "date_filter_start": None,
        "date_filter_end": None,
        "match_mode": "and",
        "demand_qty": None,
        "entities": [
            {
                "raw": "SRTWC8517",
                "hint": "product",
                "canonical_code": None,
                "current_message": True,
                "confident": True,
            }
        ],
        "entity_op": "replace_combine",
        "scope_exclusive": False,
        "requested_attributes": [],
        "contains_flyer": False,
        "reference_positions": [],
        "reference_target": None,
        "person_mention": None,
        "is_active": None,
        "order_status": None,
        "correction": False,
        "routing": {"suggested_team": None, "suggested_agent": None, "team_source": None},
        "escalation": {"is_escalation_confirmation": False, "company_pick": None},
        # v3 schema keys (AC-1506/AC-1317, cluster 4) - `assert_emission` checks every
        # `DECLARED_KEYS` entry is present, so a bare mock without these fails at
        # `understood` with "parser emission missing ..." (coder 10, ba684f9a6: a harness
        # emission is now held to the same declared keys a real model answer is).
        "continuation": None,
        "group_by": None,
        "top_n": None,
        "document": None,
        "status": None,
        "answers_open_question": {"resolved": None, "picks": None, "answer": None},
        "anaphora": {"backward_reference": None},
    }
    base.update(overrides)
    return base


def _envelope(**overrides: Any) -> Envelope:
    payload: dict[str, Any] = {
        "contact": {
            "id": CONTACT_ID,
            "firstName": "ZZT",
            "custom_fields": [{"name": "is_human_intervened", "value": "false"}],
        },
        "message": {
            "event_type": "message.received",
            "contact": {"id": CONTACT_ID},
            "message": {
                "messageId": "ZZT-msg-1",
                "contactId": CONTACT_ID,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": "price for SRTWC8517"},
            },
        },
    }
    payload.update(overrides)
    return Envelope(**payload)


@pytest.fixture()
def seeded(session_factory):
    """A respond contact with empty session vars, plus the access agent the turn needs."""
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": str(CONTACT_ID), "phone": "+60000000009", "sv": json.dumps({"variables": {}})},
    )
    db.commit()
    return db


@pytest.fixture()
def stub_parser(monkeypatch):
    """Stub the provider call, the way `test_ideation_turn` stubs the ideate extractor."""

    def _install(output: dict[str, Any] | None = None, *, error: Exception | None = None, on_call=None):
        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub",
                prompt_version=1,
                provider="openai",
                model="gpt-test",
                api_key="sk-test",
            )

        def fake_parse(config, user_block):
            if on_call is not None:
                on_call(user_block)
            if error is not None:
                raise error
            return output if output is not None else _parser_output()

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", fake_parse)

    return _install


@pytest.fixture()
def stub_access(monkeypatch):
    def _install(allowed: bool = True, decision: str = "allow", attributes: list | None = None):
        # `attributes` = the contact's granted field-reveal keys (`ctx["access"]["attributes"]`);
        # None is the empty grant set, exactly as `head/access.py` reports it live.
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda db, *, agent_code, contact_id, space_id: {
                "allowed": allowed,
                "decision": decision,
                "agent_name": "General Enquiries",
                "attributes": attributes,
                "all_attributes_allowed": None,
            },
        )
        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    return _install


def _turn_row(session_factory, turn_id: str) -> ChatbotTurn:
    return session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()


class TestHappyPath:
    """Ported (AC-1592, S6c): `business_query` is in `CRM_COMPLETED_BRANCH_KINDS` and the
    switch that used to hold it back (`chatbot_completed_lanes`) no longer gates
    completion (contract line 73 superseded, `delegate.py` module docstring) - a bare
    default `_parser_output()` turn today runs end to end inside `run_turn` and closes
    `status="done"` at `stage="sent"`, never `"delegated"` at `"routed"`. Measured with a
    scratch probe, not guessed.
    """

    def test_returns_ctx_item_branch_and_no_delegate(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "business_query"
        assert result.delegate is None, "CRM_COMPLETED_BRANCH_KINDS covers business_query now"
        assert set(result.ctx) == {"contact", "text", "session", "parse", "access", "media"}
        # AC-101: `item` is what route-turn emits - the access response plus branch_kind.
        assert result.item["branch_kind"] == "business_query"
        assert result.item["allowed"] is True
        assert result.item["decision"] == "allow"

    def test_the_turn_row_is_recorded_as_done(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        row = _turn_row(session_factory, result.turn_id)
        assert row.status == "done", row.error
        assert row.stage == "sent"
        assert row.branch_kind == "business_query"
        assert row.message_id == "ZZT-msg-1"
        assert row.ingress == "webhook"
        assert row.error is None

    def test_the_trace_is_sentences_not_json(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """AC-007: `summary` and `why` are plain language; `raw` holds the payload."""
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        trace = [r for r in _turn_row(session_factory, result.turn_id).trace if r.get("stage")]
        assert [r["stage"] for r in trace] == list(TURN_STAGES)
        for record in trace:
            assert record["summary"] and record["why"]
            assert "{" not in record["summary"], record["summary"]
            assert "{" not in record["why"], record["why"]
            assert record["ms"] >= 0
            assert "raw" in record

    def test_every_stage_recorded_is_a_declared_turn_stage(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """Ported: the file-header split ("the four the HEAD owns, then hands off") is
        gone with the delegate architecture - `business_query` now runs every stage in
        one call. `looked_up`, `replied`, `remembered`, `sent` are no longer a lane's or
        a tail's own turn; they are `run_turn`'s."""
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        trace = [r for r in _turn_row(session_factory, result.turn_id).trace if r.get("stage")]
        assert all(r["stage"] in TURN_STAGES for r in trace)
        assert [r["stage"] for r in trace] == list(TURN_STAGES)


class TestParserFailure:
    def test_a_failed_parse_is_a_failed_understood_stage_with_no_routing(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """AC-105 / R5 / H44: no soft default, no default routing."""
        stub_parser(error=parser_mod.ParserError("provider timeout"))
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind is None
        assert result.delegate is None
        assert result.reply["text"] == parser_mod.PARSER_ERROR_REPLY

        row = _turn_row(session_factory, result.turn_id)
        assert row.status == "failed"
        assert row.stage == "understood"
        assert "provider timeout" in row.error
        assert row.trace[-1]["stage"] == "understood"
        assert row.trace[-1]["status"] == "failed"

    def test_the_caller_is_handed_the_error_reply_as_an_action(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        stub_parser(error=parser_mod.ParserError("boom"))
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert [a["kind"] for a in result.actions] == ["send_message"]
        assert result.actions[0]["text"] == parser_mod.PARSER_ERROR_REPLY


class TestMediaWithoutUrl:
    """AC-107 / H5, restated for the media-into-turn pipeline (captain ruling 23 Sep
    2026): an image/audio attachment that DECLARES its type but carries no url is
    UNREADABLE, not a plain-text fallthrough and not a silently vanished turn either.
    `media_intake.detect()` still catches it, `engine.py` never calls `media_intake.
    run()` for it (no url to fetch - no ledger row, no meter, no job, no worker call),
    and the turn closes the SAME arm a completed-but-failed extraction does (AC-1813):
    branch_kind `media_denied`, `wording.voice_unclear()` for audio (`nothing_read()`
    for image), turn status failed with Retry.
    """

    def test_a_voice_note_with_no_url_fails_at_intake_with_no_ledger_row(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        from app.services.media_extract import wording

        stub_parser()
        stub_access()
        envelope = _envelope()
        envelope.message["message"]["message"] = {"type": "audio", "attachment": {"type": "audio"}}

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert result.branch_kind == "media_denied"
        assert result.reply["text"] == wording.voice_unclear()
        row = _turn_row(session_factory, result.turn_id)
        assert row.status == "failed"
        assert row.stage == "media_intake"
        assert row.error == wording.voice_unclear()

        # No meter, no enqueue (captain's ruling) - there was nothing to fetch, so
        # neither the ledger nor a job row exists for this turn at all.
        from app.models.media import ContactMediaUsage, MediaExtractionJob

        db = session_factory()
        assert db.query(ContactMediaUsage).count() == 0
        assert db.query(MediaExtractionJob).count() == 0

    def test_it_never_reaches_the_parser(self, session_factory, seeded, stub_parser, stub_access):
        calls: list[str] = []
        stub_parser(on_call=calls.append)
        stub_access()
        envelope = _envelope()
        envelope.message["message"]["message"] = {"type": "audio", "attachment": {"type": "audio"}}
        engine_mod.run_turn(envelope, session_factory=session_factory)
        assert calls == []


class TestHumanIntervened:
    def test_the_flag_becomes_an_action_and_the_turn_continues(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """AC-108: today's `set-human-intervened` path, as an action the caller executes."""
        stub_parser()
        stub_access()
        envelope = _envelope()
        envelope.contact["custom_fields"] = [{"name": "is_human_intervened", "value": "true"}]

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert result.branch_kind == "business_query"  # the turn CONTINUES
        assert result.actions[0] == {
            "kind": "update_contact_fields",
            "fields": {"is_human_intervened": False},
            "dry_run": False,
        }

    def test_no_action_when_the_flag_is_not_set(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """Ported (AC-1592): `business_query` completes in-process now and always seals a
        `send_message` action, so `result.actions == []` is stale - the property this
        test actually protects is that NO `update_contact_fields` action appears when the
        flag was never set."""
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert "update_contact_fields" not in [a["kind"] for a in result.actions]


class TestAccessDenied:
    def test_a_refused_contact_routes_to_access_denied_with_the_tag_only_item(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        stub_parser()
        stub_access(allowed=False, decision="deny_no_access")
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert result.branch_kind == "access_denied"
        # `access_denied` is NOT a tag-only arm: it keeps the access response.
        assert result.item["decision"] == "deny_no_access"


class TestDryRun:
    def test_a_test_envelope_writes_nothing_outside_chatbot_turns(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """D14: `is_test` / `test_run_id` make the turn side-effect free."""
        stub_parser()
        stub_access()
        db = session_factory()
        before = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()

        envelope = _envelope(test_run_id="ZZT-run-1")
        assert envelope.dry_run is True
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        after = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        assert after == before
        assert _turn_row(session_factory, result.turn_id).is_test is True
        # Ported (AC-1592): S1's head-only turn wrote no session state, so there was
        # nothing to patch yet; `business_query` completes end to end today (D9), so a
        # dry run now DOES compute the five-key patch it would have written - and D14's
        # own contract for that is that it is populated ONLY on a dry run, never applied.
        # The "nothing written" guarantee above (`after == before`) is what this test is
        # really pinning; this assertion is the shape check for what a dry run PREVIEWS.
        assert result.session_patch is not None
        assert set(result.session_patch) == {
            "focus",
            "open_question",
            "ideation",
            "access_levels",
            "contains_flyer",
        }

    def test_every_action_carries_dry_run_true(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        stub_parser()
        stub_access()
        envelope = _envelope(is_test=True)
        envelope.contact["custom_fields"] = [{"name": "is_human_intervened", "value": "true"}]
        result = engine_mod.run_turn(envelope, session_factory=session_factory)
        assert result.actions
        assert all(a["dry_run"] is True for a in result.actions)
        assert result.is_test is True
        assert result.as_dict()["is_test"] is True

    @pytest.mark.parametrize(
        "overrides",
        [
            {"is_test": True},
            {"test_run_id": "ZZT-run"},
            {"mode": "uac"},
        ],
    )
    def test_all_three_test_markers_mean_dry_run(self, overrides):
        assert _envelope(**overrides).dry_run is True

    def test_a_live_envelope_is_not_a_dry_run(self):
        assert _envelope(mode="live").dry_run is False
        assert _envelope().dry_run is False


class TestIdempotency:
    def test_the_same_message_twice_runs_one_turn(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """D15: the webhook producer and the failover poller are two injectors, one message."""
        calls: list[str] = []
        stub_parser(on_call=calls.append)
        stub_access()

        first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        second = engine_mod.run_turn(
            _envelope(ingress="poller"), session_factory=session_factory
        )

        assert second.duplicate is True
        assert second.turn_id == first.turn_id
        assert second.branch_kind == first.branch_kind
        assert len(calls) == 1  # no second LLM call

        rows = (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.contact_respond_id == str(CONTACT_ID))
            .all()
        )
        assert len(rows) == 1

    def test_a_different_message_runs_normally(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        stub_parser()
        stub_access()
        engine_mod.run_turn(_envelope(), session_factory=session_factory)
        other = _envelope()
        other.message["message"]["messageId"] = "ZZT-msg-2"
        result = engine_mod.run_turn(other, session_factory=session_factory)
        assert result.duplicate is False
        assert result.branch_kind == "business_query"


class TestRetryReinjection:
    """S2b (AC-705, corrected 5 Sep): the trace screen's Retry re-posts the ORIGINAL
    respond.io webhook body to n8n's inject webhook, which re-enters the engine at
    `run_turn` with the SAME `message_id` a live message used. Without a marker, D15's
    dedup would read that re-delivery as the same duplicate a webhook/poller race
    produces and Retry would silently no-op. `retry_requested_at` (migration 474) is
    that marker, set by the retry endpoint and consumed here."""

    def test_retried_message_reruns_not_duplicate(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        stub_parser(error=parser_mod.ParserError("provider timeout"))
        stub_access()
        first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert first.duplicate is False
        assert first.status == "failed"

        db = session_factory()
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == first.turn_id).first()
        row.retry_requested_at = datetime.now(timezone.utc)  # what the S2b retry endpoint sets
        db.commit()

        stub_parser()  # the retried turn succeeds this time
        retried = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert retried.duplicate is False
        assert retried.turn_id != first.turn_id
        assert retried.branch_kind == "business_query"

        rows = (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.contact_respond_id == str(CONTACT_ID))
            # NOT created_at: two inserts in the same test can tie on Postgres's `now()`
            # within one transaction (see LESSONS-LEARNT), and `attempt` is the column
            # that actually orders these deterministically.
            .order_by(ChatbotTurn.attempt.asc())
            .all()
        )
        assert len(rows) == 2
        original_row, retried_row = rows
        assert original_row.id == first.turn_id
        assert original_row.attempt == 1
        assert retried_row.id == retried.turn_id
        assert retried_row.attempt == 2
        assert retried_row.ingress == "retry"
        # Consumed: a THIRD delivery of this message must be an ordinary duplicate, not
        # read as another requested re-run.
        assert original_row.retry_requested_at is None

    def test_a_failed_message_without_retry_requested_is_still_a_duplicate(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """The D15 guarantee still holds for an UNRETRIED failure: two deliveries of the
        same message racing on their own (webhook + poller) run once, whether the first
        one succeeded or failed. Nobody asked for a re-run, so it is not one."""
        stub_parser(error=parser_mod.ParserError("provider timeout"))
        stub_access()
        first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert first.status == "failed"

        second = engine_mod.run_turn(
            _envelope(ingress="poller"), session_factory=session_factory
        )
        assert second.duplicate is True
        assert second.turn_id == first.turn_id

        rows = (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.contact_respond_id == str(CONTACT_ID))
            .all()
        )
        assert len(rows) == 1


class TestSessionDiscipline:
    def test_no_db_session_is_open_during_the_parser_call(
        self, counting_session_factory, session_factory, seeded, stub_parser, stub_access
    ):
        """The plan's capacity rule. The 96/100-connection incident is the evidence."""
        observed: list[int] = []
        stub_parser(on_call=lambda _block: observed.append(counting_session_factory.state["open"]))
        stub_access()

        engine_mod.run_turn(_envelope(), session_factory=counting_session_factory)

        assert observed == [0], (
            "a DB session was held across the parser call - the engine must close it "
            "before provider I/O and reopen afterwards"
        )


class TestPendingMarkerRead:
    def test_the_parser_is_told_what_the_bot_is_waiting_for(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """R3: the ONE prompt-input change S1 makes (D16 slimming is S1b).

        Ported (AC-1592): the flat five-key shape's pending slot is `open_question`, not
        `pending` (`session_state.FIVE_KEYS`); `session_state.pending_of` reads it via
        `turn/pending.py::from_wire`, which needs only `{"kind": ...}` at minimum.
        """
        db = session_factory()
        db.execute(
            text(
                "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
                "WHERE respond_io_id = :c"
            ),
            {
                "c": str(CONTACT_ID),
                "sv": json.dumps({"open_question": {"kind": "escalation_offer"}}),
            },
        )
        db.commit()

        blocks: list[str] = []
        stub_parser(on_call=blocks.append)
        stub_access()
        engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert "Pending: the assistant is waiting for a escalation_offer reply." in blocks[0]

    def test_nothing_is_added_when_nothing_is_pending(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        blocks: list[str] = []
        stub_parser(on_call=blocks.append)
        stub_access()
        engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert "Pending:" not in blocks[0]


class TestLatestUserMessage:
    def test_it_is_the_two_line_shape_the_ported_blocks_split_back_apart(self):
        envelope = _envelope()
        assert engine_mod.build_latest_user_message(envelope) == "price for SRTWC8517\n\n"

    def test_a_quoted_reply_appends_the_reply_to_line(self):
        envelope = _envelope()
        envelope.message["message"]["replyTo"] = {
            "id": "ZZT-quoted",
            "message": {"text": "1. SRTWC8517"},
        }
        assert (
            engine_mod.build_latest_user_message(envelope)
            == "price for SRTWC8517\nreply to: 1. SRTWC8517\n"
        )

    def test_an_image_description_stands_in_for_missing_text(self):
        envelope = _envelope()
        envelope.message["message"]["message"] = {
            "type": "image",
            "attachment": {"type": "image", "description": "a photo of a basin"},
        }
        assert engine_mod.build_latest_user_message(envelope) == "a photo of a basin\n\n"


class TestStockDenialGateEndToEnd:
    """R1 / AC-306, wired end to end through `run_turn` (not just `route.decide`).

    `test_route_unit.py` already proves the pure predicate; this proves
    `engine._stock_denial_enabled` actually reads `system_settings.chatbot_stock_denial_enabled`
    off the row the fixture flips and hands it to `decide` unchanged, on the real Postgres
    fixture used everywhere else in this suite.
    """

    @staticmethod
    def _stock_envelope(*, message_id: str) -> Envelope:
        envelope = _envelope()
        envelope.message["message"]["messageId"] = message_id
        # S6 ruling (coordinator, 16 Sep 2026): stock allowance moved OFF the envelope's
        # own custom_fields onto `respond_contacts.chatbot_stock_allowed` - the envelope
        # is now IGNORED entirely by `_stock_check_denied`, so `custom_fields` here is
        # deliberately plain (the gate is set on the contact ROW instead, see
        # `_deny_stock_access` below).
        envelope.contact["custom_fields"] = [
            {"name": "is_human_intervened", "value": "false"},
        ]
        return envelope

    @staticmethod
    def _deny_stock_access(session_factory, *, contact_id) -> None:
        from sqlalchemy import text

        db = session_factory()
        db.execute(
            text("UPDATE respond_contacts SET chatbot_stock_allowed = false WHERE respond_io_id = :c"),
            {"c": str(contact_id)},
        )
        db.commit()

    def test_off_by_default_a_stock_check_still_answers_business_query(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access
    ):
        assert system_settings_row.chatbot_stock_denial_enabled in (False, None)
        stub_parser(
            _parser_output(
                intent_hint="check_stock",
                domain_hint="inventory",
                demand_qty=5,
            )
        )
        stub_access()

        result = engine_mod.run_turn(
            self._stock_envelope(message_id="ZZT-msg-stock-off"),
            session_factory=session_factory,
        )
        assert result.branch_kind == "business_query"

    def test_flipped_on_the_same_contact_is_denied(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access
    ):
        from app.models.user import SystemSetting

        db = session_factory()
        setting = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
        setting.chatbot_stock_denial_enabled = True
        db.commit()
        self._deny_stock_access(session_factory, contact_id=CONTACT_ID)

        stub_parser(
            _parser_output(
                intent_hint="check_stock",
                domain_hint="inventory",
                demand_qty=5,
            )
        )
        stub_access()

        result = engine_mod.run_turn(
            self._stock_envelope(message_id="ZZT-msg-stock-on"),
            session_factory=session_factory,
        )
        assert result.branch_kind == "stock_denied"


class TestCarriedDomainHintGuard:
    """F3, ported (AC-1592) onto `contracts.coerce_domain_hint` - engine's own
    `_drop_unknown_carried_domain(variables)` is gone (`AttributeError`, grep-confirmed:
    only a comment in `contracts.py` still names it). `coerce_domain_hint` is the ONE
    guard now, called at both entry points a domain reaches a turn from (the parser's own
    emission and the contact's stored memory) - a single value in, the same value or
    `None` out, rather than the old in-place dict mutation: `value if value in
    DOMAIN_HINTS else None`.

    `TestF3DomainHintNeverLeavesTheEnumEndToEnd` in `test_s6c_engine_paths.py` grades the
    same guard through a whole turn; these pin the function's own edges.
    """

    def test_a_team_name_is_coerced_to_none(self) -> None:
        from app.services.chatbot.contracts import coerce_domain_hint

        assert coerce_domain_hint("purchasing") is None, (
            "'purchasing' is a team name (lanes/escalation.ESCALATION_TEAMS), not a "
            "DOMAIN_HINTS member"
        )

    def test_a_declared_domain_survives(self) -> None:
        from app.services.chatbot.contracts import coerce_domain_hint

        assert coerce_domain_hint("inventory") == "inventory"

    def test_none_is_untouched(self) -> None:
        from app.services.chatbot.contracts import coerce_domain_hint

        assert coerce_domain_hint(None) is None

    def test_the_guard_survives_a_non_string(self) -> None:
        from app.services.chatbot.contracts import coerce_domain_hint

        assert coerce_domain_hint(123) is None  # no raise: not a DOMAIN_HINTS member either
