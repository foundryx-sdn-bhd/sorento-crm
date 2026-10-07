"""S3's own two pieces of new behaviour: the lane switch, and `/turn/complete`.

`test_s3_canned_and_ideate.py` (the tester's) pins what each lane SAYS. This file pins the
two things that decide whether a lane runs at all and how n8n reaches the tail:

* **`system_settings.chatbot_completed_lanes`** - the DATA half of the completion rule.
  The code half is `lanes.canned.COMPLETED_BRANCH_KINDS`, and a lane needs BOTH, so the
  cutover is a data change the owner makes after a shadow window and the rollback is
  editing the list. The column ships EMPTY, which is why the tester's integration tests
  see a delegated turn: nothing completes until somebody says so.
* **`POST /chat/turn/complete`** - the same tail call as `/turn/{id}/complete`, with the
  turn identified from the body instead of the path. Agreed with the n8n side so their cut
  touches one workflow: `sub-output` holds the `ctx` and not the turn id.

Postgres only, through the blank schema; the parser, the access check and (for ideate) the
MCP call are stubbed at their own seams, so nothing here reaches an LLM, n8n or a live MCP
server.

Retired 16 Sep 2026 (AC-1592, tester ruling, this session) - three methods in
`TestTheCompletedLaneSwitch` predate the S3 rearch's own D9 decision, documented in
`engine.py`'s own comment right above where `completes_here` is computed: "no engine
switch. The re-architected turn IS the engine, so a lane the CODE can complete is
completed here - `system_settings.chatbot_completed_lanes` no longer gates it" (contract
line 73 superseded, `delegate.py`'s own module docstring says the same). Measured directly:
`chatbot_completed_lanes` is read into `enabled_lanes_from(...)` and NEVER passed to
`delegate_for` or consulted anywhere in `run_turn` - `completes_here = branch_kind in
CRM_COMPLETED_BRANCH_KINDS` alone decides, and that frozenset now covers all 13
`BRANCH_KINDS` (S6c's own business-arm addition reached full coverage), so `result.delegate`
is `None` unconditionally today, whatever the switch is set to. Retired, not ported:
- `test_a_lane_the_owner_has_not_switched_on_still_delegates` - asserted the switch OFF
  still delegates `clarify_menu`; it does not, `clarify_menu` is CRM-completed regardless.
- `test_a_lane_the_code_cannot_finish_is_never_completed_however_it_is_configured` - same
  theme for `business_query`; the docstring's own premise (a second `chatbot_business_lane_
  enabled` switch still gates the business arms) does not hold either - `_business_lane_
  enabled` is defined in `engine.py` but never called from `run_turn` (grep-confirmed), so
  that switch is dead code too, not a replacement gate.
- `test_switching_a_lane_off_again_is_the_rollback` - same theme, the "rollback by editing
  the list" mechanism this pins no longer exists; a rollback is a blue/green redeploy now
  (S7's own D9 note).
No replacement is named: the property these three protected (a lane can be held back from
answering) is gone by design, not moved elsewhere - `test_the_column_ships_empty_so_nothing_
completes_on_deploy` (the column's own default) and `test_a_lane_the_owner_switched_on_is_
completed` (still green, does not depend on the OFF case) and `test_the_two_halves_are_
declared_once_each` (still green, `delegate_for` as a pure function is unchanged, only its
caller stopped consulting `enabled_lanes`) are the surviving coverage of this class.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.models.chatbot_turn import ChatbotTurn
from app.models.user import SystemSetting
from app.services.chatbot import engine as engine_mod
from app.api.v1.external.chat import TAIL_ERROR_REPLY
from app.services.chatbot.lanes import canned as canned_lanes
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_chat_turn_endpoint import api_key, client  # noqa: F401 - fixtures
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures reused by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)

_BY_BODY_URL = "/api/v1/external/chat/turn/complete"
MESSAGE_ID = "ZZT-msg-1"


def _set_completed_lanes(session_factory, lanes: list[str]) -> None:
    db = session_factory()
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
    row.chatbot_completed_lanes = lanes
    db.commit()


def _session_vars_raw(session_factory) -> Any:
    return session_factory().execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": str(CONTACT_ID)},
    ).scalar()


def _seed_session_variables(session_factory, variables: dict[str, Any]) -> None:
    """Legacy nested `{"variables": {...}}` shape - `session_state.py` still falls back to
    it once (S3 ruling), and this is the shape `_setup_escalate_offer`'s own precedent in
    `test_s3_canned_and_ideate.py` seeds with, reused verbatim rather than re-derived."""
    db = session_factory()
    db.execute(
        text(
            "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
            "WHERE respond_io_id = :c"
        ),
        {"c": str(CONTACT_ID), "sv": json.dumps({"variables": variables})},
    )
    db.commit()


class TestTheCompletedLaneSwitch:
    """BOTH halves, and neither alone is enough."""

    def test_the_column_ships_empty_so_nothing_completes_on_deploy(self, session_factory):
        db = session_factory()
        row = SystemSetting()
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.chatbot_completed_lanes == [], (
            "the CRM must ship inert: a lane that completed the moment the code landed "
            "would change what a customer reads before anyone decided to"
        )

    def test_a_lane_the_owner_switched_on_is_completed(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_completed_lanes(session_factory, ["clarify_menu"])
        stub_parser(
            _parser_output(
                message_type="clarification", domain_hint=None, user_goal="checking stock"
            )
        )
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "clarify_menu"
        assert result.delegate is None
        assert result.reply["text"].startswith("I see you're checking stock, Let me understand more.")
        assert result.actions[-1]["kind"] == "send_message"

    def test_the_two_halves_are_declared_once_each(self):
        """ONE place answers "does this turn complete here": `delegate_for`, reading the
        code half (`contracts.CRM_COMPLETED_BRANCH_KINDS`) and the data half (the settings
        list) together. `lanes.canned` only says which of them IT knows how to compose."""
        from app.services.chatbot.contracts import (
            CRM_COMPLETED_BRANCH_KINDS,
            SELF_CLOSING_BRANCH_KINDS,
        )
        from app.services.chatbot.delegate import delegate_for

        # The kinds with a lane module of their own come off: `low_signal` (S4),
        # `out_of_scope` (S5) and, once S6c landed, the three business arms. The eight
        # below are still exactly what this module composes, which is the assertion that
        # matters - and it is the one that catches a new lane leaking into the canned
        # composer, which is how S6c first broke this file.
        assert (
            canned_lanes.COMPLETED_BRANCH_KINDS
            == CRM_COMPLETED_BRANCH_KINDS - SELF_CLOSING_BRANCH_KINDS
        )
        assert canned_lanes.COMPLETED_BRANCH_KINDS == {
            "access_denied",
            "escalate_offer",
            "escalation_declined",
            "clarify_menu",
            "not_supported",
            "demand_qty",
            "offer_hold",
            "ideate",
        }
        # A lane the CODE cannot finish is delegated however the list is configured. After
        # S6c that is no longer `business_query` (the lane shipped), so the assertion moves
        # to a kind no build claims - `delegate_for` reads the code half either way.
        assert delegate_for("a_lane_from_the_future", frozenset({"a_lane_from_the_future"})) == (
            "a_lane_from_the_future"
        )
        # `business_query` now passes the CODE half; the engine's second switch
        # (`chatbot_business_lane_enabled`) is what still delegates it, and the end-to-end
        # test above is where that is graded.
        assert delegate_for("business_query", frozenset({"business_query"})) is None
        # A lane the code CAN finish is still delegated until the list names it.
        assert delegate_for("clarify_menu", frozenset()) == "clarify_menu"
        assert delegate_for("clarify_menu", frozenset({"clarify_menu"})) is None

class TestAccessDeniedNeverWritesTheSession:
    """The one completed lane that answers WITHOUT the tail (and so without a write)."""

    def test_access_denied_answers_and_remembers_nothing(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        _set_completed_lanes(session_factory, ["access_denied"])
        stub_parser(_parser_output())
        stub_access(allowed=False, decision="deny_unknown_agent")
        before = _session_vars_raw(session_factory)

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "access_denied"
        assert result.delegate is None
        assert result.reply["text"].startswith("Sorry, you are not allowed to access ")
        assert _session_vars_raw(session_factory) == before, (
            "a contact refused the agent must not have the turn written into their memory"
        )
        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).one()
        assert row.status == "done"
        # A sub-event row in `row.trace` has no `stage` key at all (only `kind`) -
        # `Trace.persisted()` interleaves them with stage records now; `.get(...)`
        # skips them instead of raising (trace-shape theme, same fix as
        # `test_harness_injections.py`, tester 11).
        assert [r["stage"] for r in row.trace if r.get("stage")][-1] == "sent"


class TestCompleteByBody:
    """`/turn/complete`: same tail, turn identified from the body."""

    @pytest.fixture()
    def delegated_turn(self, session_factory, seeded, stub_parser, stub_access, monkeypatch):
        """`CRM_COMPLETED_BRANCH_KINDS` covers all 13 branch kinds today (S6c reached full
        coverage), so no real branch kind `run_turn` can route to is ever left delegated -
        `chatbot_completed_lanes` plays no part (see the file header's retirement note).
        `/turn/complete` itself is still live code (`delegate` stays on the response "for
        the kinds no lane here can finish", AC-1507) - a future 14th branch kind, or a
        rollback that shrinks the frozenset - so this fixture forces exactly that seam
        directly, the same way the file's OWN `test_the_two_halves_are_declared_once_each`
        already exercises `delegate_for` on `"a_lane_from_the_future"`. This grades
        `/turn/complete`'s OWN mechanics (identify by body, 404/409, replay, retry-wins),
        not the routing/completion decision, which the rest of this file already covers.
        """
        monkeypatch.setattr(engine_mod, "CRM_COMPLETED_BRANCH_KINDS", frozenset())
        stub_parser(_parser_output(message_type="business_query", domain_hint="master_products"))
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert result.delegate, "the fixture needs a DELEGATED turn to complete"
        return result

    @staticmethod
    def _body(result) -> dict[str, Any]:
        return {
            "item": {"branch_kind": "not_supported"},
            "ctx": result.ctx,
            "result": None,
            "resolved": None,
            "gate": None,
            "offer_hold": None,
            "suggest_offer": None,
            "not_found": None,
            "incoming_picker": None,
            "access_choice": None,
            "crossdomain_render": None,
            "answer": None,
            "clarify": None,
        }

    def _post(self, client, api_key, body):
        return client.post(_BY_BODY_URL, json=body, headers={"X-API-Key": api_key})

    def test_happy_path_completes_the_turn_the_body_names(
        self, client, api_key, session_factory, monkeypatch, delegated_turn
    ):
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)

        resp = self._post(client, api_key, self._body(delegated_turn))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["turn_id"] == delegated_turn.turn_id
        assert body["reply"]["text"], "the tail composed no reply"
        row = (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.id == delegated_turn.turn_id)
            .one()
        )
        assert row.status == "done"
        assert row.stage == "remembered"

    def test_no_matching_turn_is_a_404_that_names_the_pair(
        self, client, api_key, session_factory, monkeypatch, seeded
    ):
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)

        class _Fake:
            ctx = {
                "contact": {"id": CONTACT_ID},
                "text": {"message": {"messageId": "ZZT-msg-nobody"}},
            }
            turn_id = "unused"

        resp = self._post(client, api_key, self._body(_Fake()))

        assert resp.status_code == 404, resp.text
        detail = json.dumps(resp.json())
        assert "ZZT-msg-nobody" in detail and str(CONTACT_ID) in detail

    def test_a_turn_that_never_delegated_is_a_409(
        self, client, api_key, session_factory, monkeypatch, seeded, stub_parser, stub_access
    ):
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        from app.services.chatbot.head import parser as parser_mod

        _set_completed_lanes(session_factory, [])
        stub_parser(error=parser_mod.ParserError("boom"))
        stub_access()
        failed = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert failed.status == "failed"

        class _Fake:
            ctx = {
                "contact": {"id": CONTACT_ID},
                "text": {"message": {"messageId": MESSAGE_ID}},
            }

        resp = self._post(client, api_key, self._body(_Fake()))

        assert resp.status_code == 409, resp.text
        assert "CHATBOT_TURN_NOT_DELEGATED" in json.dumps(resp.json())

    def test_the_highest_attempt_wins(
        self, client, api_key, session_factory, monkeypatch, delegated_turn
    ):
        """R4's manual retry: completing the OLDER attempt would fold the lane's result
        into the row nobody is watching, and leave the live one delegated forever.

        Two rows for one message is a shape production CAN produce since S2b: the unique
        key is `(contact_respond_id, message_id, attempt)`, so a retry of a failed message
        is a legal second row rather than a collision. The second row is therefore written
        here as the retry writes it, with no constraint surgery.
        """
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        db = session_factory()
        first = db.query(ChatbotTurn).filter(ChatbotTurn.id == delegated_turn.turn_id).one()
        retry = ChatbotTurn(
            contact_respond_id=first.contact_respond_id,
            message_id=first.message_id,
            ingress="retry",
            envelope=first.envelope,
            is_test=False,
            status="delegated",
            stage="routed",
            attempt=2,
            trace=[],
            response=first.response,
        )
        db.add(retry)
        db.commit()

        resp = self._post(client, api_key, self._body(delegated_turn))

        assert resp.status_code == 200, resp.text
        assert resp.json()["turn_id"] == str(retry.id), "the older attempt was completed"

    def test_every_response_field_survives_serialisation(
        self, client, api_key, session_factory, monkeypatch, delegated_turn
    ):
        """`response_model` silently DROPS an undeclared field, so each one is asserted."""
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        canned = {
            "turn_id": delegated_turn.turn_id,
            "reply": {
                "text": "hi",
                "quick_replies": "a,b",
                "result_set": [{"idx": 1}],
                "attachments_src": [{"url": "s3://x"}],
            },
            "actions": [{"kind": "send_message", "text": "hi", "dry_run": False}],
            "session_patch": {"variables": {"pending": None}},
        }

        class _Fake:
            def as_dict(self):
                return canned

        monkeypatch.setattr("app.api.v1.external.chat.complete_turn", lambda *a, **k: _Fake())

        resp = self._post(client, api_key, self._body(delegated_turn))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in ("turn_id", "reply", "actions", "session_patch"):
            assert key in body, f"{key!r} missing from the response body: {body}"
        assert body["reply"]["attachments_src"] == [{"url": "s3://x"}]
        assert body["reply"]["result_set"] == [{"idx": 1}]
        assert body["actions"][0]["kind"] == "send_message"

    def test_the_path_form_is_unchanged(
        self, client, api_key, session_factory, monkeypatch, delegated_turn
    ):
        """The id-less route is an ADDITION; `/turn/{id}/complete` keeps working."""
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)

        resp = client.post(
            f"/api/v1/external/chat/turn/{delegated_turn.turn_id}/complete",
            json=self._body(delegated_turn),
            headers={"X-API-Key": api_key},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["turn_id"] == delegated_turn.turn_id

    def test_a_finished_turn_replays_instead_of_409(
        self, client, api_key, session_factory, monkeypatch, delegated_turn
    ):
        """B3: `done` with a stored response is the REPLAY, not a refusal.

        `complete_turn` answers a duplicate delivery with the answer it already composed
        (D15) - and the guard on this route used to 409 every status that was not
        `delegated`, which made that branch unreachable from the route n8n actually calls.
        A duplicate `sub-output` call must get the same answer back, not an error.
        """
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)

        first = self._post(client, api_key, self._body(delegated_turn))
        assert first.status_code == 200, first.text

        again = self._post(client, api_key, self._body(delegated_turn))

        assert again.status_code == 200, again.text
        assert again.json()["reply"] == first.json()["reply"]
        assert again.json()["turn_id"] == delegated_turn.turn_id

    def test_a_processing_turn_is_still_a_409(
        self, client, api_key, session_factory, monkeypatch, delegated_turn
    ):
        """The replay exception is `done` WITH a response, and nothing wider: a turn still
        running has no lane result to fold in and no answer to replay."""
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        db = session_factory()
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == delegated_turn.turn_id).one()
        row.status = "processing"
        db.commit()

        resp = self._post(client, api_key, self._body(delegated_turn))

        assert resp.status_code == 409, resp.text
        assert "CHATBOT_TURN_NOT_DELEGATED" in json.dumps(resp.json())

    def test_a_done_turn_with_nothing_stored_is_still_a_409(
        self, client, api_key, session_factory, monkeypatch, delegated_turn
    ):
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        db = session_factory()
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == delegated_turn.turn_id).one()
        row.status = "done"
        row.response = None
        db.commit()

        resp = self._post(client, api_key, self._body(delegated_turn))

        assert resp.status_code == 409, resp.text

    @pytest.mark.parametrize(
        "message_id,expected_status",
        [("ZZT-msg-nobody", 404), (MESSAGE_ID, 409)],
    )
    def test_a_refusal_is_logged_like_every_other_call(
        self,
        client,
        api_key,
        session_factory,
        monkeypatch,
        seeded,
        stub_parser,
        stub_access,
        message_id,
        expected_status,
    ):
        """Every call to this endpoint writes an `integration_log`, refusals included.

        A 404 / 409 here is the call an operator goes looking for when n8n reports a turn
        that was never finished, and it is decided BEFORE the tail runs - so without its
        own log line it was the one call that left no trace at all.
        """
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        from app.models.integration import IntegrationLog
        from app.services.chatbot.head import parser as parser_mod

        _set_completed_lanes(session_factory, [])
        stub_parser(error=parser_mod.ParserError("boom"))
        stub_access()
        engine_mod.run_turn(_envelope(), session_factory=session_factory)

        class _Fake:
            ctx = {
                "contact": {"id": CONTACT_ID},
                "text": {"message": {"messageId": message_id}},
            }

        before = (
            session_factory()
            .query(IntegrationLog)
            .filter(IntegrationLog.endpoint == _BY_BODY_URL)
            .count()
        )

        resp = self._post(client, api_key, self._body(_Fake()))

        assert resp.status_code == expected_status, resp.text
        logs = (
            session_factory()
            .query(IntegrationLog)
            .filter(IntegrationLog.endpoint == _BY_BODY_URL)
            .all()
        )
        assert len(logs) == before + 1, "the refusal wrote no integration_log"
        written = logs[-1]
        assert written.status_code == expected_status
        assert written.status == "failed"
        assert written.error_message, "a refusal with no reason is a log nobody can use"

    def test_the_row_is_test_flag_travels_on_both_routes(
        self, client, api_key, session_factory, monkeypatch, seeded, stub_parser, stub_access
    ):
        """F2: n8n's test-guard reads `is_test` off the completion, not off its memory of
        what `/turn` said two calls ago."""
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        _set_completed_lanes(session_factory, [])
        stub_parser(_parser_output(message_type="business_query", domain_hint="master_products"))
        stub_access()

        live = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        resp = client.post(
            f"/api/v1/external/chat/turn/{live.turn_id}/complete",
            json=self._body(live),
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_test"] is False

        dry = engine_mod.run_turn(
            _envelope(
                test_run_id="ZZT-run-is-test",
                message={
                    **_envelope().message,
                    "message": {
                        **_envelope().message["message"],
                        "messageId": "ZZT-msg-dry",
                    },
                },
            ),
            session_factory=session_factory,
        )
        resp = self._post(client, api_key, self._body(dry))

        assert resp.status_code == 200, resp.text
        assert resp.json()["is_test"] is True

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_a_failed_tail_answers_with_the_error_reply_and_its_action(
        self,
        client,
        api_key,
        session_factory,
        monkeypatch,
        seeded,
        stub_parser,
        stub_access,
        dry_run,
    ):
        """F5: a tail that raises is an ANSWERED call, never a null reply.

        The caller executes `actions` and nothing else, so an answer whose words live only
        on `reply.text` is a customer left in silence. The row is still `failed` at
        `remembered` with the reason on it - the failure is recorded, not swallowed.

        `CRM_COMPLETED_BRANCH_KINDS` is forced empty (same seam as `delegated_turn`
        above): `business_query` completes in-process today, so an un-forced turn is
        already `status="done"` with a stored reply by the time `/turn/complete` is
        posted, and B3's own replay rule (`test_a_finished_turn_replays_instead_of_409`)
        would hand back the ORIGINAL composed answer instead of ever calling `run_tail` -
        which is what this test needs to fail.
        """
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        monkeypatch.setattr(engine_mod, "CRM_COMPLETED_BRANCH_KINDS", frozenset())
        stub_parser(_parser_output(message_type="business_query", domain_hint="master_products"))
        stub_access()
        overrides: dict[str, Any] = {"test_run_id": "ZZT-run-tail-fail"} if dry_run else {}
        turn = engine_mod.run_turn(_envelope(**overrides), session_factory=session_factory)

        def _boom(*args, **kwargs):
            raise RuntimeError("the tail could not finish")

        monkeypatch.setattr(engine_mod, "run_tail", _boom)

        resp = self._post(client, api_key, self._body(turn))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reply"] == {
            "text": TAIL_ERROR_REPLY,
            "quick_replies": None,
            "result_set": None,
            "attachments_src": None,
        }
        assert body["actions"] == [
            {
                "kind": "send_message",
                "text": TAIL_ERROR_REPLY,
                "quick_replies": None,
                "result_set": None,
                "dry_run": dry_run,
            }
        ]
        assert body["is_test"] is dry_run
        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn.turn_id).one()
        assert (row.status, row.stage) == ("failed", "remembered")
        assert "the tail could not finish" in (row.error or "")


class TestSendActionShape:
    """The action fields the CALLER executes, agreed with the n8n executor.

    The rule is that an action carries the SEALED reply's own values verbatim. That is not
    a style choice: `quick_replies` is `compile-current-state`'s `quick_reply`, which is
    n8n's comma-joined STRING or null, and `sub-sendmsg`'s `quick_reply` input has never
    been handed a list. Normalising it here would break the half of the send path that did
    not move into the CRM.

    **Measured, and it is why the quick-reply case below is a unit test:** no canned lane
    can produce quick replies today. `compile-current-state` sets `quickReply` from
    `access-level-choice-message` or `build-suggest-offer` only, and a canned lane supplies
    neither fragment - checked across `escalate_offer`, `clarify_menu` and `offer_hold`,
    all three seal no `quick_replies` at all. So the lane test below asserts the
    PASS-THROUGH (the action carries whatever the reply sealed, absent included) and the
    unit test asserts the shape when there IS something to carry.
    """

    def test_a_canned_lane_action_carries_the_sealed_values_not_a_normalised_copy(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        # Ported (AC-1592): the OLD `is_explicit_correction()` / `output_exchange.
        # derive_routing` route to `escalate_offer` is gone (that whole module deleted,
        # AC-1594) - measured directly, `correction`/`routing.suggested_team` are not
        # read anywhere `apply.py` builds a plan.
        #
        # Re-pinned again (owner ruling, hand pass 3, 17 Sep 2026 - `answers_open_
        # question` retired): the OLD `resolved is False` re-ask branch this test
        # exercised is gone with the key. A message naming nothing over an open
        # `team_pick` offer is now simply "not an answer" - `_picked_positions` returns
        # None, the pending is carried untouched (not re-printed), and the message runs
        # as itself, which for an unrouted `message_type: "unknown"` with no entities
        # falls through to the casual/`low_signal` lane rather than re-surfacing the
        # offer as `escalate_offer`. Measured directly: `result.branch_kind ==
        # "low_signal"`, the stubbed casual reply, one `send_message` action, no
        # `quick_replies`.
        stub_parser(
            _parser_output(
                message_type="unknown",
                domain_hint=None,
                is_affirmative=None,
                answers_open_question={"resolved": False, "picks": None, "answer": None},
                escalation={"is_escalation_confirmation": False, "company_pick": None},
            )
        )
        _seed_session_variables(
            session_factory,
            {
                "open_question": {
                    "kind": "team_pick",
                    "options": [{"position": 1, "label": "purchasing", "team": "purchasing"}],
                    "team": "purchasing",
                    "expects": "pick",
                }
            },
        )
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "low_signal"
        assert result.status == "done"
        send = result.actions[-1]
        assert send["kind"] == "send_message"
        assert send["text"] == result.reply["text"]
        # IDENTITY with the sealed value, not `[]`: this lane seals no quick replies, and
        # an action that invented an empty list would be hiding that from the sender.
        assert send["quick_replies"] == result.reply.get("quick_replies")
        # The low_signal lane's OWN hand-built action (`_run_casual_lane`, the mid-flight
        # list appended before the clarifier call resolves) never carries a `result_set`
        # key at all - measured directly, not the `_send_actions` shape the docstring
        # above describes for the canned lanes. There is nothing to number on a casual
        # reply, so there is no key pretending there might be.
        assert "result_set" not in send
        assert send["dry_run"] is False
        # Nothing to attach, so there is no second action.
        assert [a["kind"] for a in result.actions] == ["send_message"]

    def test_quick_replies_and_attachments_pass_through_untouched(self):
        """The shape when the reply DOES carry both. `send_attachments` comes second, for
        the reason n8n wires it that way: the text explains the files."""
        reply = {
            "text": "Here are the closest matches:",
            "quick_replies": "IBKS7245-NG-BL,Yes escalate,No it's okay",
            "result_set": [{"idx": 1, "label": "IBKS7245-NG-BL"}],
            "attachments_src": [{"url": "s3://spec.pdf"}],
        }

        actions = engine_mod._send_actions(reply, dry_run=False)

        assert [a["kind"] for a in actions] == ["send_message", "send_attachments"]
        send, attach = actions
        assert send["quick_replies"] == "IBKS7245-NG-BL,Yes escalate,No it's okay", (
            "the comma-joined string is what `sub-sendmsg`'s own input takes"
        )
        assert send["result_set"] == reply["result_set"]
        assert attach["attachments_src"] == reply["attachments_src"]
        assert attach["reply"] == reply, "`sub-send-attachments` reads more than one field"
        assert send["dry_run"] is False and attach["dry_run"] is False

    def test_a_dry_run_flags_every_action(self):
        actions = engine_mod._send_actions(
            {"text": "x", "quick_replies": None, "result_set": [], "attachments_src": [{"u": 1}]},
            dry_run=True,
        )
        assert len(actions) == 2
        assert all(a["dry_run"] is True for a in actions)

    def test_every_completed_lane_pins_quick_replies_string_or_null_never_a_list(
        self, session_factory, seeded, system_settings_row, stub_parser, stub_access, monkeypatch
    ):
        """AC-507/D9's executor contract, walked across every branch kind the CRM
        completes today rather than pinned lane by lane: the eight canned/ideate/
        offer-hold kinds (`_CANNED_SCENARIOS` plus `access_denied`), the casual lane
        (`low_signal`, S4), the escalation lane (`out_of_scope`, S5), the three business
        arms on their APOLOGY path (`business_query`, `check_promotion`, `stock_denied`,
        S6c), and a FAILED turn (`_failed_result`) - the fallback shape every other lane
        drops into on an unhandled error, and the one a live n8n parity check actually
        caught a list on.

        The business arms are walked on the failure path specifically. `_run_business_answer`
        hand-builds its own reply and action there instead of going through
        `_send_actions`, so it is the one CRM-completed site the type is not derived at,
        and it landed as `[]` because S6c wrote it in parallel with the lane's pin. That
        is precisely the class of defect this walk exists to catch, so the walk has to
        reach it.

        Measured against 61 live sub-output tail captures (c32698c1): 60 non-empty
        strings, 1 null, 0 empty strings, 0 lists - so the assertion below is the same
        shape as what n8n's `sub-sendmsg` has ever actually been handed. `dry_run` is
        checked a bool for the identical reason: a stray truthy/falsy value is the same
        class of type drift the executor cannot coerce.
        """
        from tests.chatbot.test_s3_canned_and_ideate import (
            _CANNED_SCENARIOS,
            _build_scenario,
            _enable_stock_denial,
        )
        from tests.chatbot.test_s4_casual_lane import _casual, _install_stub_lane

        _set_completed_lanes(
            session_factory,
            [
                *_CANNED_SCENARIOS,
                "access_denied",
                "low_signal",
                "out_of_scope",
                "business_query",
                "check_promotion",
                "stock_denied",
            ],
        )

        all_actions: list[dict[str, Any]] = []

        for index, kind in enumerate(_CANNED_SCENARIOS):
            if kind == "demand_qty":
                _enable_stock_denial(session_factory, system_settings_row)
            envelope, parser_overrides, _expected = _build_scenario(kind, session_factory, monkeypatch)
            envelope.message["message"]["messageId"] = f"ZZT-contract-{index}"
            stub_parser(parser_overrides)
            stub_access()
            result = engine_mod.run_turn(envelope, session_factory=session_factory)
            assert result.status != "failed", f"{kind}: {result.error}"
            all_actions.extend(result.actions)

        # `access_denied` is answered before the tail runs (no fragment table entry),
        # so it is not one of `_CANNED_SCENARIOS` - pinned separately the same way
        # `TestAccessDeniedNoSessionWrite` does.
        stub_parser(
            _parser_output(
                message_type="request_for_help",
                routing={"suggested_team": None, "suggested_agent": "general-enquiries"},
            )
        )
        stub_access(allowed=False, decision="deny_unknown_agent")
        access_denied_envelope = _envelope()
        access_denied_envelope.message["message"]["messageId"] = "ZZT-contract-access-denied"
        result = engine_mod.run_turn(access_denied_envelope, session_factory=session_factory)
        assert result.status != "failed", result.error
        all_actions.extend(result.actions)

        # `low_signal` (S4): the clarifier lane, its own seams stubbed the way
        # `TestLowSignalLaneIntegration` does.
        casual = _casual()
        stub_parser(
            _parser_output(message_type="casual", domain_hint=None, intent_hint=None, entities=[])
        )
        stub_access()
        _install_stub_lane(monkeypatch, casual, response_json='{"response": "Hi! How can I help?"}')
        low_signal_envelope = _envelope()
        low_signal_envelope.message["message"]["messageId"] = "ZZT-contract-low-signal"
        result = engine_mod.run_turn(low_signal_envelope, session_factory=session_factory)
        assert result.status != "failed", result.error
        all_actions.extend(result.actions)

        # `out_of_scope` (S5): the escalation lane, faked at its own seam the way
        # `test_out_of_scope_finishes_in_turn` does - the shape under test is the
        # engine's sealing, not the lane's own assignment logic.
        lane_actions = [
            {"kind": "send_message", "text": "Your request is out of scope...", "dry_run": False},
            {"kind": "assign_conversation", "respond_user_id": "respond-usr-1", "dry_run": False},
            {
                "kind": "add_comment",
                "text": "Team: customer_service",
                "mention_user_ids": ["respond-usr-1"],
                "dry_run": False,
            },
            {"kind": "send_message", "text": "This inquiry has been routed...", "dry_run": False},
        ]

        def fake_run_escalation_lane(ctx, item, *, dry_run=False, session_factory=None):
            return {"arm": "human-intervention", "clarify": None, "actions": lane_actions, "pending": None}

        monkeypatch.setattr(engine_mod, "run_escalation_lane", fake_run_escalation_lane)
        stub_parser(
            _parser_output(
                message_type="request_for_help",
                user_goal="wants a human",
                entities=[],
                routing={
                    "suggested_team": "customer_service",
                    "suggested_agent": "general_enquiries",
                    "team_source": "inferred",
                },
                escalation={"is_escalation_confirmation": True, "company_pick": None},
            )
        )
        stub_access()
        out_of_scope_envelope = _envelope()
        out_of_scope_envelope.message["message"]["messageId"] = "ZZT-contract-out-of-scope"
        result = engine_mod.run_turn(out_of_scope_envelope, session_factory=session_factory)
        assert result.status != "failed", result.error
        all_actions.extend(result.actions)

        # A FAILED turn (`_failed_result`): a prod parity check against the live n8n
        # sendmsg node found a list here reads as a type error on the executor's own
        # typed input ("'quick_reply' expects a string but we got array"), which means
        # every failed turn was reaching the customer as silence - the highest-stakes
        # shape in this walk, since it is the one every OTHER lane falls back to.
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        failed_envelope = _envelope()
        failed_envelope.message["message"]["messageId"] = "ZZT-contract-failed-turn"
        result = engine_mod.run_turn(failed_envelope, session_factory=session_factory)
        assert result.status == "failed"
        all_actions.extend(result.actions)

        # The business arms that FETCH (S6c), on the FETCH-RAISES failure path.
        # `engine.decide` and `lanes.business.run_until_exit` / `complete_answer` are gone
        # (S3 rewired the dispatcher into `run_turn`'s own inline flow); the shape under
        # test is still the ACTION the engine hands the caller when the lane cannot
        # finish, not the lane's own rendering, so the fake seam moves to where a lane
        # failure is produced today - `lanes.business.run_fetch` (the ONE seam
        # `turn_runtime.make_tool_runner` still calls, confirmed by
        # `test_engine_failure_paths.py`'s own `TestTheBusinessLaneOnFetchFailure`, tester
        # 11, this session): a RAISED exception is what still reaches `run_turn`'s fetch
        # try/except and produces `status="failed"` at `looked_up` with
        # `GENERIC_ERROR_REPLY`; a RETURNED error fragment does not raise and is answered
        # as an ordinary miss instead, which is why this walk needs the raise, not a
        # returned `_error_fragment(...)`. `_business_lane_enabled` is dropped: defined in
        # `engine.py` but never called from `run_turn` (grep-confirmed) - the arms
        # complete unconditionally today, the same as every other CRM-completed kind.
        # `stock_denied` is NOT in this walk: measured directly (scratch probe) that it
        # never reaches the fetch step at all (`branch_kind in ("business_query",
        # "check_promotion")` gates the fetch block, `stock_denied` is deliberately
        # excluded), so `lanes.business.run_fetch` raising can never reach it - see the
        # dedicated `test_stock_denied_answers_something_instead_of_silence` below for
        # what a real `stock_denied` turn does instead (a confirmed defect, kept red).
        set_chatbot_switches(session_factory, business_lane=True)
        # ROGUE-CONVO-REVIEW PR-1: the resolver PLACES the typed code. With the default
        # empty resolver `SRTWC8517` was unplaced and the tool still ran with no filter
        # at all; that zero-scope `master_products` read is now refused before any tool
        # call (`gate.REFUSES_EMPTY_FETCH`), so the raise this arm is about can only
        # happen on a scoped fetch (the same adjustment as `test_engine_failure_paths`).
        import uuid as _uuid

        from app.services.chatbot.lanes.business.services import ResolveGateServices
        from tests.chatbot.conftest import validating_resolve_entity

        placed = {
            "entity_type": "product",
            "uuid": str(_uuid.uuid4()),
            "canonical_code": "SRTWC8517",
            "match_tier": "exact",
            "display": {"product_name": "ZZT SRTWC8517"},
        }
        real_production_services = engine_mod.business_services.production_services

        def placing_services(db, *, space_id=None):
            # Only the resolver is replaced; the promotion arm still needs the real
            # access types (its tier gate) to reach its fetch at all.
            real = real_production_services(db, space_id=space_id)
            return ResolveGateServices(
                access_types=real.access_types,
                resolve_entity=validating_resolve_entity(
                    lambda body: {
                        "tokens": ["SRTWC8517"],
                        "resolutions": [
                            {"token": "SRTWC8517", "resolved": True, "ambiguous": False, "matches": [placed]}
                        ],
                        "unresolved_tokens": [],
                        "intersection": [placed],
                        "by_entity_type": {"product": [placed]},
                    }
                ),
                probe=real.probe,
            )

        monkeypatch.setattr(engine_mod.business_services, "production_services", placing_services)
        monkeypatch.setattr(
            engine_mod.business,
            "run_fetch",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the answer half fell over")),
        )
        for kind in ("business_query",):
            monkeypatch.setattr(engine_mod, "turn_route", lambda plan, _kind=kind: _kind)
            stub_parser(_parser_output())
            stub_access()
            business_envelope = _envelope()
            business_envelope.message["message"]["messageId"] = f"ZZT-contract-{kind}"
            result = engine_mod.run_turn(business_envelope, session_factory=session_factory)
            assert result.status == "failed", (
                f"{kind}: the answer half was made to raise, so this arm must be the "
                f"lane's own failure (got {result.status!r})"
            )
            assert result.branch_kind == kind, (
                f"{kind}: a lane failure keeps its branch kind, so the trace says which "
                f"lane broke (got {result.branch_kind!r})"
            )
            assert result.delegate is None, (
                f"{kind}: the CRM owns this turn - handing back a lane here is the ghost "
                f"S7 mode has nobody left to answer"
            )
            all_actions.extend(result.actions)

        # The promotion arm (ROGUE-CONVO-REVIEW PR-1): it used to reach `run_fetch` only
        # because the default parse (a `master_products` ask re-routed by name) left its
        # one fetch leg the zero-scope product read, which is now refused before any tool
        # call. In this world a promotion ask settles its tier first (the "Which one do
        # you mean? dealer / office / end user" picker) and the picked tier recomposes to
        # no entitlement (Security B1, fail closed), so no promotion fetch is reachable
        # here and the raise cannot be staged for it. What the walk CAN measure for this
        # lane is the CRM-completed message it does send: the tier ask, whose
        # `quick_replies` shape is the contract under test.
        monkeypatch.setattr(engine_mod, "turn_route", lambda plan: "check_promotion")
        stub_parser(
            _parser_output(
                domain_hint="promotion",
                intent_hint="check_promotion",
                entities=[],
                user_goal="any promo running",
            )
        )
        stub_access()
        promotion_envelope = _envelope()
        promotion_envelope.message["message"]["messageId"] = "ZZT-contract-check_promotion"
        result = engine_mod.run_turn(promotion_envelope, session_factory=session_factory)
        assert result.status != "failed", result.error
        assert result.branch_kind == "check_promotion"
        assert result.delegate is None
        all_actions.extend(result.actions)

        send_messages = [a for a in all_actions if a.get("kind") == "send_message"]
        assert len(send_messages) >= 13, (
            f"only {len(send_messages)} send_message actions collected - one lane's setup "
            "did not run, so this is not the full walk the test name promises"
        )
        for action in send_messages:
            quick = action.get("quick_replies")
            assert quick is None or (isinstance(quick, str) and quick != ""), (
                f"quick_replies must be a non-empty string or null, got {quick!r} "
                f"(action: {action})"
            )
            assert isinstance(action.get("dry_run"), bool), (
                f"dry_run must be a bool, got {action.get('dry_run')!r} (action: {action})"
            )

    def test_stock_denied_answers_something_instead_of_silence(
        self,
        session_factory,
        seeded,
        system_settings_row,
        stub_parser,
        stub_access,
        monkeypatch,
    ):
        """FIXED (coder 11, `2453e64d0`) - was a CONFIRMED ENGINE DEFECT: a real
        `stock_denied` turn used to close `status="done"` with `reply=None` and
        `actions=[]` (`stock_denied` reaches neither the fetch block, `canned_lanes.
        COMPLETED_BRANCH_KINDS`, nor `_ASK_BRANCH_KINDS`, deliberately). `engine.py`'s
        own "the REFUSAL: a denied stock check is an answer, not silence" block now
        composes contract 61's refusal sentence whenever nothing else answered the
        turn - this test's own assertions were already forward-looking (`reply is not
        None`, a `send_message` action) and pass unchanged against the fix; only this
        docstring needed updating (queue item 2's coordinator message, 16 Sep 2026).
        """
        from tests.chatbot.test_s3_canned_and_ideate import _enable_stock_denial

        _enable_stock_denial(session_factory, system_settings_row)
        monkeypatch.setattr(engine_mod, "_stock_check_denied", lambda *a, **k: True)
        monkeypatch.setattr(engine_mod, "_demand_qty_missing", lambda *a, **k: False)
        stub_parser(_parser_output())
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "stock_denied"
        assert result.status == "done"
        assert result.reply is not None, (
            "a customer denied a stock check must be told so, not answered with nothing"
        )
        assert any(a.get("kind") == "send_message" for a in result.actions), (
            "no send_message action was produced for a completed stock_denied turn"
        )
