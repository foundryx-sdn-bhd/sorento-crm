"""Everything that can go wrong AFTER the turn row exists is recorded, never dropped.

Review B1: `parser.resolve_config`, `check_access` and `decide()` all sat outside the one
try/except, so a provider error or the stock predicate throwing on a contact with no
`is_allowed_stock` field escaped `run_turn` entirely - the row stayed `processing` with a
null error and no trace, and the endpoint's generic handler turned it into a 500. That is
H32's dropped turn, arriving by a different route than the one H32 named.

Review S2: a duplicate delivery returned `ctx: null` / `item: null`, which n8n's AC-110
re-emitters (`$('build-ctx').first().json.ctx.<key>`) throw on. The turn's answer is now
persisted and replayed.

Reuses the fixtures in `tests/chatbot/test_engine.py` rather than restating them - the
envelope shape and the stubbing seams are the same, and two copies would drift.
"""
from __future__ import annotations

import pytest

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import Envelope, TurnRequest
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import (  # noqa: F401  - fixtures are used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)


def _row(session_factory, turn_id: str) -> ChatbotTurn:
    return session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()


def _only_row(session_factory) -> ChatbotTurn:
    rows = (
        session_factory()
        .query(ChatbotTurn)
        .filter(ChatbotTurn.contact_respond_id == str(CONTACT_ID))
        .all()
    )
    assert len(rows) == 1, f"expected exactly one turn row, found {len(rows)}"
    return rows[0]


class TestNothingEscapesRunTurn:
    """B1. Each of these used to leave a `processing` row and raise out of `run_turn`."""

    def test_resolve_config_failing_is_a_recorded_failed_turn(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        stub_parser()
        stub_access()

        def _boom(db, *, current_date, override_version_id=None, grants=None):
            raise parser_mod.ParserError("AI assistant configuration is not set")

        monkeypatch.setattr(parser_mod, "resolve_config", _boom)

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "failed"
        assert result.reply["text"] == parser_mod.PARSER_ERROR_REPLY
        row = _only_row(session_factory)
        assert row.status == "failed"
        assert row.stage == "received"
        assert "configuration is not set" in row.error
        assert row.trace and row.trace[-1]["status"] == "failed"

    def test_the_access_service_failing_is_a_recorded_failed_turn(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        stub_parser()
        stub_access()
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("access service down")),
        )

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "failed"
        row = _only_row(session_factory)
        assert row.status == "failed"
        assert row.stage == "access"
        assert "access service down" in row.error

    def test_the_stock_predicate_throwing_is_a_recorded_failed_turn(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        """S8's real consequence: with the flag ON and no `is_allowed_stock` field, live's
        own expression throws. The port reproduces the throw; this proves the turn is
        recorded rather than dropped when it does."""
        qf = {
            **_parser_output(),
            "intent_hint": "check_stock",
            "domain_hint": "inventory",
        }
        stub_parser(qf)
        stub_access()
        monkeypatch.setattr(engine_mod, "_stock_denial_enabled", lambda db: True)

        envelope = _envelope()
        envelope.contact["custom_fields"] = []  # no is_allowed_stock at all

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert result.status == "failed"
        row = _only_row(session_factory)
        assert row.status == "failed"
        assert row.stage == "routed"
        assert row.error

    def test_the_error_reply_is_still_handed_to_the_caller_to_send(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        stub_parser()
        stub_access()
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert [a["kind"] for a in result.actions] == ["send_message"]
        assert result.actions[0]["text"] == parser_mod.PARSER_ERROR_REPLY

    def test_the_human_intervened_action_survives_a_later_failure(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The caller must still clear the flag even though the turn failed after it."""
        stub_parser()
        stub_access()
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        envelope = _envelope()
        envelope.contact["custom_fields"] = [{"name": "is_human_intervened", "value": "true"}]

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert [a["kind"] for a in result.actions] == ["update_contact_fields", "send_message"]


class TestDuplicateReplaysTheAnswer:
    """S2. n8n's re-emitters read `response.ctx.<key>`; a null there throws."""

    def test_the_second_delivery_gets_the_original_ctx_and_item(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        stub_parser()
        stub_access()
        first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        second = engine_mod.run_turn(
            _envelope(ingress="poller"), session_factory=session_factory
        )

        assert second.duplicate is True
        assert second.ctx == first.ctx
        assert second.item == first.item
        assert second.branch_kind == first.branch_kind
        assert second.delegate == first.delegate

    def test_the_stored_response_is_the_answer_not_a_summary_of_it(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        stub_parser()
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        stored = _row(session_factory, result.turn_id).response
        # AC-1592 port: `delegate_payload`/`delegate_error` (the n8n shadow-lane replay
        # contract, S6a) are gone - measured directly, the stored keys are now `{ctx,
        # item, actions, reply}`. S3's rewiring means the CRM composes and stores the
        # REPLY itself (no second n8n lane ever ran alongside it to disagree with), so
        # a duplicate delivery replays `reply` directly rather than a delegate payload
        # for n8n to re-render.
        assert set(stored) == {"ctx", "item", "actions", "reply"}
        assert stored["reply"] == result.reply
        assert set(stored["ctx"]) == {"contact", "text", "session", "parse", "access", "media"}
        assert stored["item"]["branch_kind"] == result.branch_kind

    def test_a_duplicate_of_a_FAILED_turn_replays_the_failure_not_a_null_ctx(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        """A failed turn stores no answer, so `ctx` is legitimately absent - but the
        caller still learns it is a duplicate and still gets the branch_kind (null) and
        status, rather than a fresh LLM call."""
        stub_parser(error=parser_mod.ParserError("boom"))
        stub_access()
        first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        second = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert first.status == "failed"
        assert second.duplicate is True
        assert second.turn_id == first.turn_id
        assert second.status == "failed"
        assert second.ctx is None


class TestStageIsNamedOnEveryFailure:
    @pytest.mark.parametrize(
        ("seam", "expected_stage"),
        [
            ("resolve_config", "received"),
            ("access", "access"),
        ],
    )
    def test_the_recorded_stage_is_where_it_actually_stopped(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch, seam, expected_stage
    ) -> None:
        stub_parser()
        stub_access()
        if seam == "resolve_config":
            monkeypatch.setattr(
                parser_mod,
                "resolve_config",
                lambda db, *, current_date, grants=None: (_ for _ in ()).throw(RuntimeError("x")),
            )
        else:
            monkeypatch.setattr(
                engine_mod,
                "check_access",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
            )

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert result.stage == expected_stage
        assert _only_row(session_factory).stage == expected_stage


class TestEnvelopeValidation:
    """A malformed envelope is the CALLER's mistake, so it must read as 422, not 500."""

    def test_a_contact_without_an_id_is_refused_at_the_schema(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError) as excinfo:
            Envelope(message={}, contact={"firstName": "ZZT"})
        error = excinfo.value.errors()[0]
        assert error["loc"] == ("contact",)
        assert "contact.id is required" in error["msg"]

    @pytest.mark.parametrize("contact", [{}, {"id": None}, {"id": ""}])
    def test_every_empty_form_of_the_id_is_refused(self, contact: dict) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            Envelope(message={}, contact=contact)

    def test_a_real_id_passes(self) -> None:
        assert Envelope(message={}, contact={"id": "900000009"}).contact["id"] == "900000009"

    def test_the_endpoint_answers_422_and_names_the_field(self) -> None:
        """The whole point: an operator reading the response learns what to fix.

        Built on a bare app around the real request model, so the assertion is about
        `TurnRequest`'s validation rather than about auth or the module guard, which have
        their own tests in `test_module_and_endpoint.py`.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        api = FastAPI()

        @api.post("/turn")
        def turn(payload: TurnRequest):  # pragma: no cover - never reached
            return {"ok": True}

        client = TestClient(api, raise_server_exceptions=False)
        response = client.post(
            "/turn", json={"envelope": {"message": {}, "contact": {"firstName": "ZZT"}}}
        )
        assert response.status_code == 422
        body = response.text
        assert "contact" in body
        assert "contact.id is required" in body


class TestDuplicateWhileTheFirstTurnIsStillRunning:
    """The LIKELY timing, not the edge case.

    `response` is written when a turn CLOSES, so a duplicate arriving mid-turn has nothing
    to replay. Two injectors seconds apart against a turn that takes seconds means this is
    the common shape, and the engine does NOT try to solve it: waiting would buy nothing,
    because the caller must not answer twice either way. What it MUST do is say so clearly,
    so `status` tells a caller "not finished yet" apart from "failed, nothing to say".
    """

    def test_it_returns_duplicate_with_status_processing_and_null_ctx(
        self, session_factory, seeded
    ) -> None:
        from app.models.chatbot_turn import ChatbotTurn

        db = session_factory()
        row = ChatbotTurn(
            contact_respond_id=str(CONTACT_ID),
            message_id="ZZT-msg-1",
            ingress="webhook",
            envelope={},
            is_test=False,
            status="processing",   # inserted, not yet closed
            stage="received",
            attempt=1,
            trace=[],
        )
        db.add(row)
        db.commit()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.duplicate is True
        assert result.status == "processing"
        assert result.ctx is None and result.item is None
        assert result.branch_kind is None

    def test_a_finished_duplicate_is_distinguishable_from_an_in_flight_one(
        self, session_factory, seeded, stub_parser, stub_access
    ) -> None:
        stub_parser()
        stub_access()
        engine_mod.run_turn(_envelope(), session_factory=session_factory)
        finished = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert finished.duplicate is True
        # AC-1592 port: a business_query turn completes in-process now (S3 rewiring,
        # `CRM_COMPLETED_BRANCH_KINDS` covers all 13 branch kinds) - measured, `done`,
        # never `delegated`. The property this test is FOR - a finished duplicate reads
        # differently from an in-flight one - is unaffected by which terminal status
        # that finish is.
        assert finished.status == "done"
        assert finished.ctx is not None, (
            "a duplicate of a CLOSED turn must replay the stored answer; only the "
            "in-flight and failed cases legitimately hand back nulls"
        )


class TestTheBusinessLaneOnFetchFailure:
    """S6c round 2, ported (AC-1592, 16 Sep 2026): what a turn does when the fetch step
    breaks, on the CURRENT `turn/fetch.py` + `turn/compose.py` pipeline.

    RETIRED, not ported (named here so the rule is not lost): the "lane OFF" half of
    every original cell (`_enable(..., [])` / asserting `result.delegate == "business_query"`
    and `status == "delegated"`) - measured this session, `contracts.CRM_COMPLETED_
    BRANCH_KINDS` now covers all 13 `BRANCH_KINDS` (S7's own completion, already
    reached), so `chatbot_completed_lanes` no longer gates completion at all and no
    branch kind `run_turn` can route to is EVER left delegated - the identical finding
    `test_complete_turn.py`'s own header already documents for `complete_turn` itself.
    Also retired: `engine.decide` (the old dispatcher this class monkeypatched to force
    `business_query` - gone, S3 rewired; `business_query` is the real default route for
    `stub_parser()`'s own bare `_parser_output()` already, so nothing needs forcing) and
    `engine.business.run_until_exit` / `complete_answer` (the old lane-exit seams this
    class stubbed to grade the miss/failure split - `turn/fetch.py::run_fetch` +
    `turn_runtime.make_tool_runner` replace them, still calling the SAME kept
    `lanes/business.run_fetch`, which is what these tests now stub directly).

    Three cells PORTED, each measured directly this session (not guessed) by driving a
    real `run_turn` with `lanes.business.run_fetch` patched:

    * the resolver RAISES - `turn_runtime.resolve_kinds` catches it internally
      ("a resolver that cannot answer is not a failed turn: reconciliation simply has
      nothing to say", its own docstring) - the turn proceeds as if nothing resolved,
      landing on `business_query` / `done` with a bare miss reply. No delegate exists to
      send it to instead.
    * the fetch RETURNS an error fragment (`business._error_fragment`, any `outcome`,
      including `not_found`) - `turn_runtime.envelope_of` carries the fragment's `error`
      straight onto the envelope with no Python exception raised, so `engine.run_turn`'s
      fetch try/except never fires: the turn still completes `done`, answered by the
      ordinary miss composer (the SAME bare-header shape `TestUnknownContactFailsClosed`
      in `test_engine_company_scope.py` measures) - a genuine absence is answered, never
      left silent, whether or not the fragment names an `outcome`.
    * the fetch RAISES (a real MCP/infrastructure break, not a returned error fragment) -
      THIS is what still reaches `engine.run_turn`'s fetch try/except: `status="failed"`
      at stage `"looked_up"`, `GENERIC_ERROR_REPLY`, the branch_kind preserved. The
      customer is never told "not found" for an outage; only a genuinely RAISED
      exception, not any returned fragment shape, produces this outcome now - a real
      architecture change from the old `_fetch_arm == "error" and outcome is None`
      dispatch, confirmed by direct measurement, not inferred from the old contract.
    """

    @staticmethod
    def _wire_resolver(monkeypatch, *, resolve_entity=None, raises: bool = False):
        from app.services.chatbot.lanes.business.services import ResolveGateServices

        def _default_resolve_entity(body):
            return {"tokens": [], "resolutions": [], "unresolved_tokens": []}

        def _raising_resolve_entity(body):
            raise RuntimeError("resolver unavailable")

        entity_fn = (
            _raising_resolve_entity
            if raises
            else (resolve_entity or _default_resolve_entity)
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "production_services",
            lambda db, *, space_id=None: ResolveGateServices(
                access_types=lambda **_: [],
                resolve_entity=(
                    entity_fn if raises else validating_resolve_entity(entity_fn)
                ),
                probe=lambda **_: None,
            ),
        )

    def test_a_resolver_failure_is_a_graceful_miss_not_a_crash(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        set_chatbot_switches(session_factory, business_lane=True)
        self._wire_resolver(monkeypatch, raises=True)
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "done", (
            "a resolver that cannot answer is not a failed turn - it has nothing to "
            "reconcile, not a broken read"
        )
        assert result.branch_kind == "business_query"
        assert result.reply is not None, "a silent turn"

    def test_a_returned_error_fragment_is_answered_as_a_miss_not_a_failure(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        from app.services.chatbot.lanes import business as business_lane_mod

        set_chatbot_switches(session_factory, business_lane=True)
        self._wire_resolver(monkeypatch)
        monkeypatch.setattr(
            engine_mod.business,
            "run_fetch",
            lambda *a, **k: business_lane_mod._error_fragment(
                "no MCP tool matched this question", outcome="not_found"
            ),
        )
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "done", (
            "H11 / AC-604: a genuine absence is answered, not left silent - and not a "
            "failed turn either"
        )
        assert result.reply is not None
        assert result.branch_kind == "business_query"

    def test_an_mcp_failure_that_raises_is_a_failed_turn_not_a_not_found(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        set_chatbot_switches(session_factory, business_lane=True)
        self._wire_resolver(monkeypatch)
        monkeypatch.setattr(
            engine_mod.business,
            "run_fetch",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("MCP tool crm_master_products_list failed: timeout")
            ),
        )
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "failed", (
            "an outage must not be told to the customer as 'not found' - that asserts "
            "an absence the read never established"
        )
        assert result.stage == "looked_up"
        assert result.reply["text"] == engine_mod.GENERIC_ERROR_REPLY
        assert result.actions[-1]["kind"] == "send_message"
        assert result.branch_kind == "business_query", "the turn keeps where it got to"
        row = _only_row(session_factory)
        assert row.status == "failed"
        assert row.stage == "looked_up"
        assert "timeout" in (row.error or "")


class TestTheRowKeepsTheFirstOutcome:
    """`_close_turn` is write-once for a TERMINAL status, and first-write-wins.

    The sequence is real, not hypothetical: a failure inside the tail closes the row
    itself (`failed` at `remembered`, where it actually stopped) and re-raises, and the
    lane handler that called it catches the same exception and closes again (`failed` at
    `replied`). The second write names the CALLER's stage, so letting it win loses the
    only fact an operator needs. `delegated` is deliberately not terminal: it is the
    handover `close_turn_for_tail` writes and `complete_turn` supersedes with `done`.
    """

    def test_a_second_terminal_close_is_refused(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        stub_parser()
        stub_access()
        monkeypatch.setattr(
            parser_mod,
            "resolve_config",
            lambda db, *, current_date, grants=None: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        row = _only_row(session_factory)
        assert (row.status, row.stage) == ("failed", "received")
        first_error, first_finished = row.error, row.finished_at

        db = session_factory()
        engine_mod._close_turn(
            db,
            result.turn_id,
            status="failed",
            stage="replied",
            branch_kind="business_query",
            error="the caller's own message",
            records=[],
            response={"reply": {"text": "later"}},
        )

        row = _only_row(session_factory)
        assert (row.status, row.stage) == ("failed", "received"), (
            "the first close records where the turn actually stopped; a later one names "
            "the stage of whoever caught the exception"
        )
        assert row.error == first_error
        assert row.finished_at == first_finished
        assert row.response is None

    def test_the_delegated_handover_is_still_superseded_by_the_tail(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The two-phase close every completed lane makes must keep working: `delegated`
        at `routed` first, `done` at `remembered` when the tail has folded the result in.
        """
        from app.services.chatbot.lanes import casual

        from tests.chatbot.test_s4_casual_lane import _install_stub_lane

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
        monkeypatch.setattr(
            engine_mod, "_enabled_lanes", lambda db, row=None: frozenset({"low_signal"})
        )
        _install_stub_lane(monkeypatch, casual, response_json='{"response": "Hi there!"}')

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "done", result.error
        row = _only_row(session_factory)
        assert (row.status, row.stage) == ("done", "remembered"), (
            "the tail's own close must still supersede the delegated handover"
        )


