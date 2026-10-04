"""D14 audit (tester-first, RED): a dry run must be ZERO writes to business state, and it
is not today. `Envelope.dry_run` is `is_test or test_run_id or (mode not live)`
(`contracts.py::Envelope.dry_run`) and the engine's OWN write (`chatbot.turns`) already
stamps `is_test` correctly - what is missing is everywhere ELSE a turn touches that never
checks the flag at all. Six findings, six RED sections below, all in one file per the
coordinator's brief so the audit lands as one commit.

Every test here is expected to FAIL against the code as it stands on this branch
(`fix/chatbot-dry-run-isolation`, `= origin/main` at 749248eb3) for the reason stated in
its own docstring - an assertion, not a fixture error, an ImportError, or a collection
failure. That is what makes it a correct RED: the coder's job is to make each assertion
true, not to make the test merely stop erroring.

Findings, each traced to the production line it audits:

1. `TestIntegrationLogWritesRegardlessOfDryRun` - `app/api/v1/external/chat.py:230-249`
   (`/turn`) and `:378-421` (`/complete`'s `_log_complete_call`) write ONE `integration_log`
   row per call, carrying the real contact id and the customer's text, with no `is_test`
   branch at all. D14 says a dry run is zero writes outside `chatbot.turns`; this table is
   outside it. **Design tension, stated rather than hidden**: `test_chat_turn_endpoint.py
   ::TestDryRunEndpointZeroWrites` already asserts the OPPOSITE - that the call log DOES
   gain a row on a dry run - matching this file's own docstring ("every call writes an
   integration_log on success AND failure ... regardless of is_test"). The two tests
   cannot both be green; this file takes the position the brief asked for (skip the write
   entirely on a dry run) and says so here rather than silently overriding the existing
   test's contract. If the team instead prefers TAGGING (write the row, but stamp
   `is_test = true` so it can be filtered out downstream), that needs a new column on
   `integration_log` - none exists today (`app/models/integration.py::IntegrationLog` has
   no `is_test` field) - and this file's two tests below would need rewriting to assert
   the tag instead of the absence. That decision belongs to whoever resolves this finding,
   not to this test file.

2. `TestOrderingKeysSkipDryRunTurns` - `app/services/chatbot/dispatch.py:129/133/137`
   build `chatbot:seq:{contact}` / `chatbot:done:{contact}` / `chatbot:running:{contact}`
   from the contact id alone, with no test/live namespace. `engine.py`'s S7 branch
   (~line 747 onward) takes a ticket, waits, marks running and marks done for EVERY turn
   once `chatbot_ordering_enabled` is on, dry run or not - so a chat-console test turn for
   a real contact takes a real ticket in the same queue a live WhatsApp message from that
   contact is about to wait on.

3. `TestD15DedupRespectsIsTest` - `_select_turn` (engine.py ~416-444) and
   `_existing_turn` (~475-483) match a turn by `(contact_respond_id, message_id)` alone.
   A test-marked row therefore SHADOWS a later live delivery of the same `messageId`:
   `run_turn` (~742-747) returns `_duplicate_result(existing)` and the live message is
   answered from the test row's canned response, with `duplicate: true` telling the caller
   to send nothing at all.

4. `TestOperatorSurfacesHideDryRunRows` - `app/api/v1/system/chatbot.py`: `failed_contacts`
   (~185-243) aggregates over `status == 'failed'` with no `is_test` filter; `list_turns`
   (~123-171) likewise; `retry_turn` (~277-386) has no `is_test` branch at all and will
   happily re-post an `is_test` row's original message to the live n8n ingress via
   `reinject_envelope`.

5. `TestMcpToolPickRefusesWriteTools` - `lanes/business/fetch.py::tool_filter` (~87-124)
   picks the single highest-`similarity` candidate with no allow-list check at all. The
   embedded tool catalogue (`mcp_tool_capability_service.py`) includes write tools -
   `crm_it_support_ticket_create`, `crm_complaint_close`, `crm_order_cancel`,
   `crm_purchase_request_approve`, `crm_purchase_request_reject` - and nothing stops one of
   them from being the top hit for an ordinary business question.

6. `TestLiveTailSessionPatchAbsentIsPreserved` - `engine.py::run_tail`, the line
   `session_patch = sealed.get("session_patch") or {}` (~2758), followed by an
   unconditional `overwrite_for_contact(db, ..., state=session_patch)` on a live turn
   (~2786-2788) whenever `write_session` is true. An ABSENT `session_patch` key (the
   sealed reply carries no memory to save) collapses to the same `{}` an EXPLICIT reset
   would produce, and the unconditional overwrite then WIPES the customer's remembered
   state for no reason the turn actually gave. See `tail/compose.py:73` (`sealed =
   composed.get("reply") or {}`) and `tail/compile_state.py:132` (`seal()`, where
   `session_patch` is always the compiled `patch` verbatim in production - which is WHY
   this file has to STUB the compile step to produce the absent-key shape at all; nothing
   in the real compiler emits it today, but the engine's contract must hold regardless of
   which lane composed the reply).

Postgres only, via `tests/_pg_fixture.py` (`session_factory`) and, for the two ordering
tests that must observe real per-contact ticket keys, a real local Redis
(`settings.redis_url`, the same substrate `test_s7_ordering_and_offload.py` uses).
Everything else is stubbed at the seams `test_engine.py` / `test_s3_switch_and_complete_
by_body.py` / `test_s6b_fetch_lane.py` already established - nothing here reaches an LLM,
n8n, respond.io or a live MCP server.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.config import settings
from app.models.chatbot_turn import ChatbotTurn
from app.models.integration import IntegrationLog
from app.services.chatbot import dispatch
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes import business as business_mod
from app.services.chatbot.lanes.business.services import FetchServices

from tests.chatbot.test_chat_turn_endpoint import (
    api_key,
    client as external_chat_client,
    seeded_contact,
    stub_engine_seams,
)
from tests.chatbot.test_engine import (
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_turns_admin_api import (
    _contact,
    _GRANTS,
    _seed_turn,
    BASE,
    client,
    db,
    MANAGE,
    VIEW,
)
from app.services.user_service import UserPermissionService

_TURN_URL = "/api/v1/external/chat/turn"


def _redis_client():
    import redis

    return redis.from_url(settings.redis_url, decode_responses=True)


# --------------------------------------------------------------------------- #
# Finding 1 - the integration_log row a dry-run call still writes
# --------------------------------------------------------------------------- #


class TestIntegrationLogWritesRegardlessOfDryRun:
    """D14: the endpoint's OWN write must respect the flag too, not just the engine's."""

    def test_dry_run_turn_writes_no_integration_log(
        self, external_chat_client, api_key, session_factory, seeded_contact, stub_engine_seams
    ):
        before = session_factory().execute(
            text("SELECT COUNT(*) FROM integration_log WHERE external_reference = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()

        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-msg-dry-run-no-log"
        payload = {
            "envelope": {
                **json.loads(envelope.model_dump_json()),
                "test_run_id": "ZZT-run-no-log",
            }
        }

        resp = external_chat_client.post(_TURN_URL, json=payload, headers={"X-API-Key": api_key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["duplicate"] is False

        after = session_factory().execute(
            text("SELECT COUNT(*) FROM integration_log WHERE external_reference = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        assert after == before, (
            "a dry-run /chat/turn call must not write an integration_log row carrying "
            "the real contact id and the customer's message text (D14). It does today: "
            "app/api/v1/external/chat.py:230-249 writes one on every call regardless of "
            "is_test/test_run_id. (Alternative the team may prefer instead of skipping "
            "the write: keep the row but stamp it is_test=True - that needs a new column "
            "on IntegrationLog, which does not exist today, so this test's SKIP framing "
            "would need rewriting to a TAG assertion if that direction is chosen.)"
        )

    def test_dry_run_complete_writes_no_integration_log(
        self, external_chat_client, api_key, session_factory, monkeypatch
    ):
        from app.api.v1.external import chat as chat_router_mod

        turn = ChatbotTurn(
            id=str(uuid.uuid4()),
            contact_respond_id=str(CONTACT_ID),
            message_id="ZZT-msg-dry-complete-no-log",
            ingress="webhook",
            envelope={
                "message": {"messageId": "ZZT-msg-dry-complete-no-log"},
                "contact": {"id": CONTACT_ID},
            },
            status="delegated",
            is_test=True,
            attempt=1,
            trace=[],
        )
        db_session = session_factory()
        db_session.add(turn)
        db_session.commit()
        turn_id = turn.id

        class _FakeCompleted:
            def as_dict(self) -> dict[str, Any]:
                return {
                    "turn_id": turn_id,
                    "reply": {
                        "text": "ok",
                        "quick_replies": None,
                        "result_set": None,
                        "attachments_src": None,
                    },
                    "actions": [{"kind": "send_message", "text": "ok", "dry_run": True}],
                    "session_patch": None,
                }

        monkeypatch.setattr(chat_router_mod, "complete_turn", lambda *a, **k: _FakeCompleted())

        before = (
            session_factory()
            .query(IntegrationLog)
            .filter(IntegrationLog.business_id == turn_id)
            .count()
        )

        resp = external_chat_client.post(
            f"/api/v1/external/chat/turn/{turn_id}/complete",
            json={"item": {"branch_kind": "clarify_menu"}},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, resp.text

        after = (
            session_factory()
            .query(IntegrationLog)
            .filter(IntegrationLog.business_id == turn_id)
            .count()
        )
        assert after == before, (
            "a dry-run turn's /complete call must not write an integration_log row "
            "either (D14). It does today: _log_complete_call "
            "(app/api/v1/external/chat.py:378-421) is called unconditionally, with no "
            "read of the row's own is_test flag."
        )


# --------------------------------------------------------------------------- #
# Chatbot growth r1 addendum (AC-982, review 7 Sep 2026): a table added since this
# file's original findings, held to the same D14 posture as Finding 1's
# `integration_log` before/after count.
# --------------------------------------------------------------------------- #


class TestDryRunTurnLeavesContactFieldRevealsUntouched:
    """`contact_field_reveals` (chatbot growth r1, Slice C) is admin-managed only -
    written by `contact_field_reveal_service.set_granted_keys`, called from the
    `/system/chatbot/contacts/{id}/field-reveals` PUT and nowhere on the turn path.
    A dry run must leave its row count unchanged, same D14 posture as every other
    table this file counts."""

    def test_dry_run_turn_writes_no_contact_field_reveals_row(
        self, external_chat_client, api_key, session_factory, seeded_contact, stub_engine_seams
    ):
        before = session_factory().execute(
            text("SELECT COUNT(*) FROM contact_field_reveals")
        ).scalar()

        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-msg-dry-run-no-field-reveal"
        payload = {
            "envelope": {
                **json.loads(envelope.model_dump_json()),
                "test_run_id": "ZZT-run-no-field-reveal",
            }
        }

        resp = external_chat_client.post(_TURN_URL, json=payload, headers={"X-API-Key": api_key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["duplicate"] is False

        after = session_factory().execute(
            text("SELECT COUNT(*) FROM contact_field_reveals")
        ).scalar()
        assert after == before, (
            "a dry-run /chat/turn call must not write a contact_field_reveals row "
            "(AC-982) - nothing on the turn path imports the write side of "
            "contact_field_reveal_service, so this is a structural guarantee "
            "restated as a count."
        )


# --------------------------------------------------------------------------- #
# Finding 2 - per-contact ordering keys are shared with live traffic
# --------------------------------------------------------------------------- #


class TestOrderingKeysSkipDryRunTurns:
    """S7 mode's redis ticket keys (`dispatch.seq_key` / `done_key` / `alive_key`) are
    keyed on the contact id alone - a dry run must not take a place in the SAME queue a
    live message from that contact is about to wait on."""

    def test_dry_run_turn_never_touches_live_ordering_keys(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ):
        monkeypatch.setattr(engine_mod, "_s7_mode", lambda *a, **k: True)
        monkeypatch.setattr(settings, "chatbot_queue_wait_seconds", 5.0, raising=False)
        stub_parser()
        stub_access()

        # A contact id of this run's own, NOT the shared `CONTACT_ID`. Redis is the one
        # substrate in this suite that is not rolled back per test and not per worktree:
        # `settings.redis_url` is the same instance every lane on this machine uses, and
        # the `delete` below is a real delete. Keyed on CONTACT_ID it would wipe another
        # lane's in-flight ordering keys (and be wiped by them), which is a flake in both
        # directions. The database rows this turn writes are still scratch-schema.
        contact_id = f"ZZT-ordering-{uuid.uuid4().hex[:8]}"
        db_session = session_factory()
        db_session.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
            ),
            {"cid": contact_id, "phone": "+60000000008", "sv": json.dumps({"variables": {}})},
        )
        db_session.commit()

        redis_client = _redis_client()
        keys = (
            dispatch.seq_key(contact_id),
            dispatch.done_key(contact_id),
            dispatch.alive_key(contact_id, 1),
        )
        redis_client.delete(*keys)
        try:
            envelope = _envelope(test_run_id="ZZT-run-ordering-dry")
            envelope.contact = {**envelope.contact, "id": contact_id}
            envelope.message["contact"]["id"] = contact_id
            envelope.message["message"]["contactId"] = contact_id
            envelope.message["message"]["messageId"] = f"ZZT-msg-{contact_id}"
            assert envelope.dry_run is True

            result = engine_mod.run_turn(envelope, session_factory=session_factory)

            assert result.is_test is True
            for key in keys:
                assert redis_client.exists(key) == 0, (
                    f"a dry-run turn touched the LIVE ordering key {key!r}, shared with "
                    "real traffic for this contact (dispatch.py:129/133/137). D14 says a "
                    "test envelope writes nothing outside chatbot.turns; a dry-run turn "
                    "must bypass per-contact ordering entirely (or use a "
                    "'chatbot:test:...' namespace of its own) rather than take a real "
                    "ticket in the queue a live message from this contact is about to "
                    "wait on."
                )
        finally:
            redis_client.delete(*keys)
            redis_client.close()


# --------------------------------------------------------------------------- #
# Finding 3 - D15 dedup ignores is_test
# --------------------------------------------------------------------------- #


class TestD15DedupRespectsIsTest:
    """`_select_turn` matches `(contact_respond_id, message_id)` alone, so a test-marked
    row shadows a later LIVE delivery of the same respond message id."""

    MESSAGE_ID = "ZZT-msg-shadow-1"

    def _seed_test_row(self, session_factory) -> ChatbotTurn:
        row = ChatbotTurn(
            id=str(uuid.uuid4()),
            contact_respond_id=str(CONTACT_ID),
            message_id=self.MESSAGE_ID,
            ingress="console",
            envelope={"message": {"messageId": self.MESSAGE_ID}, "contact": {"id": CONTACT_ID}},
            status="done",
            branch_kind="clarify_menu",
            is_test=True,
            attempt=1,
            trace=[],
            response={
                "reply": {
                    "text": "TEST-ONLY CANNED REPLY - must never reach a real customer",
                    "quick_replies": None,
                },
                "actions": [
                    {
                        "kind": "send_message",
                        "text": "TEST-ONLY CANNED REPLY - must never reach a real customer",
                        "dry_run": True,
                    }
                ],
            },
        )
        db_session = session_factory()
        db_session.add(row)
        db_session.commit()
        return row

    def test_a_live_delivery_of_a_test_shadowed_message_runs_as_its_own_turn(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        test_row = self._seed_test_row(session_factory)
        stub_parser()
        stub_access()

        live_envelope = _envelope()
        live_envelope.message["message"]["messageId"] = self.MESSAGE_ID
        assert live_envelope.dry_run is False

        result = engine_mod.run_turn(live_envelope, session_factory=session_factory)

        assert result.duplicate is False, (
            "a LIVE message must never be answered as a duplicate of a TEST row for the "
            "same (contact, messageId) - _select_turn (engine.py ~429-444) orders by "
            "attempt/created_at only and never checks is_test, so today this returns "
            "_duplicate_result(test_row) and the customer is answered from the canned "
            "test reply with duplicate:true (nothing sent at all)."
        )
        assert result.turn_id != test_row.id, (
            "the live delivery must create its OWN row, not reuse the test row's id"
        )
        if result.reply is not None:
            assert "TEST-ONLY CANNED REPLY" not in (result.reply.get("text") or ""), (
                "the live turn's reply must not be the test row's canned text"
            )

        rows = (
            session_factory()
            .query(ChatbotTurn)
            .filter(
                ChatbotTurn.contact_respond_id == str(CONTACT_ID),
                ChatbotTurn.message_id == self.MESSAGE_ID,
            )
            .all()
        )
        assert len(rows) == 2, (
            f"expected the test row plus a new live row for this message, found "
            f"{len(rows)}: {[(r.id, r.is_test) for r in rows]}"
        )
        assert any(r.is_test is False for r in rows), (
            "a new is_test=False row must exist for the live delivery"
        )

    def test_a_live_redelivery_of_a_live_message_still_dedups(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """The mirror, green today - kept as the guard the fix must not break: two LIVE
        deliveries of the same message still run exactly once (D15's original contract,
        already covered by test_engine.py::TestIdempotency, restated here beside the bug
        it must not regress)."""
        stub_parser()
        stub_access()

        first = engine_mod.run_turn(_envelope(), session_factory=session_factory)
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


# --------------------------------------------------------------------------- #
# Finding 4 - the operator screens count and act on test rows
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _system_chatbot_permissions(monkeypatch):
    """A class-scoped copy of `test_turns_admin_api._permissions`, applied only to this
    class via `usefixtures` rather than imported as a module-wide `autouse` fixture: the
    original is `autouse=True` at its OWN definition (baked into the function object, not
    the importing module), so importing it here would apply it to every OTHER class in
    this file too - which broke `TestIntegrationLogWritesRegardlessOfDryRun`'s real,
    DB-seeded `integration.chat_turn.submit` grant the first time this was tried (it
    mocks `UserPermissionService.check_user_has_permission` down to `slug in _GRANTS`,
    and `_GRANTS` never contains that slug)."""
    _GRANTS.clear()
    _GRANTS.add(VIEW)
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


class TestOperatorSurfacesHideDryRunRows:
    """`GET /turns/failed-contacts`, `GET /turns` and `POST /turns/{id}/retry` all read
    `chatbot.turns` with no `is_test` awareness."""

    pytestmark = pytest.mark.usefixtures("_system_chatbot_permissions")

    def test_failed_contacts_excludes_contacts_whose_only_failures_are_test_turns(
        self, client, db
    ):
        live_contact = _contact("live-failed")
        test_only_contact = _contact("test-only-failed")
        _seed_turn(db, contact_respond_id=live_contact, status="failed", stage="looked_up")
        test_turn = _seed_turn(
            db, contact_respond_id=test_only_contact, status="failed", stage="looked_up"
        )
        test_turn.is_test = True
        db.commit()

        resp = client.get(f"{BASE}/failed-contacts")
        assert resp.status_code == 200, resp.text
        ids = {item["contact_respond_id"] for item in resp.json()["items"]}

        assert live_contact in ids
        assert test_only_contact not in ids, (
            "a contact whose ONLY failed turn is a dry-run (is_test) turn must not "
            "surface on the operator's failed-contacts list (app/api/v1/system/"
            "chatbot.py::failed_contacts, ~line 209) - it never happened to a real "
            "customer and nothing there needs an operator's attention."
        )

    def test_list_turns_excludes_test_rows_by_default(self, client, db):
        contact = _contact("list-mixed")
        live_turn = _seed_turn(db, contact_respond_id=contact, status="done", message_id="ZZT-msg-list-live")
        test_turn = _seed_turn(db, contact_respond_id=contact, status="done", message_id="ZZT-msg-list-test")
        test_turn.is_test = True
        db.commit()

        resp = client.get(BASE, params={"contact_respond_id": contact})
        assert resp.status_code == 200, resp.text
        ids = {item["id"] for item in resp.json()["items"]}

        assert live_turn.id in ids
        assert test_turn.id not in ids, (
            "GET /turns must exclude is_test rows by default (app/api/v1/system/"
            "chatbot.py::list_turns, ~line 145) - an operator reading Chat History for "
            "a real customer should not see the dry-run turns run against them from the "
            "Prompts screen's Test button (AC-807) or the chat console."
        )

        resp2 = client.get(BASE, params={"contact_respond_id": contact, "include_test": "true"})
        assert resp2.status_code == 200, resp2.text
        ids2 = {item["id"] for item in resp2.json()["items"]}
        assert test_turn.id in ids2, (
            "include_test=true (name assumed - the coder may rename the query param) "
            "must still surface the dry-run rows for whoever needs to audit them"
        )

    def test_retry_on_a_test_turn_is_refused_and_posts_nothing(self, client, db, monkeypatch):
        _GRANTS.add(MANAGE)
        contact = _contact("retry-test-row")
        turn = _seed_turn(
            db,
            contact_respond_id=contact,
            status="failed",
            stage="looked_up",
            message_id="ZZT-msg-retry-test-row",
        )
        turn.is_test = True
        db.commit()

        reinject_calls: list[Any] = []
        monkeypatch.setattr(
            "app.api.v1.system.chatbot.reinject_envelope",
            lambda db_arg, row: reinject_calls.append(row.id),
        )

        resp = client.post(f"{BASE}/{turn.id}/retry")

        assert resp.status_code == 409, resp.text
        assert (
            resp.json().get("detail", {}).get("code") == "test_turn_not_retryable"
            or "test_turn_not_retryable" in resp.text
        ), (
            "retry_turn (app/api/v1/system/chatbot.py::retry_turn, ~line 277) has no "
            "is_test check today and will happily claim + re-inject a dry-run turn's "
            "original message at the live n8n ingress"
        )
        assert reinject_calls == [], (
            "a dry-run turn's retry must never reach the ingress: reinject_envelope was "
            f"called for {reinject_calls!r}"
        )

        db.expire_all()
        reread = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn.id).one()
        assert reread.retry_requested_at is None, (
            "a refused retry must not leave the row claimed - the same rule "
            "test_retry_unavailable_without_url already holds the endpoint to"
        )


# --------------------------------------------------------------------------- #
# Finding 5 - MCP tool pick has no read-only allow-list
# --------------------------------------------------------------------------- #


class TestMcpToolPickRefusesWriteTools:
    """`tool_filter` (fetch.py) picks the single candidate with no allow-list check of its
    own. The finding was that the candidate came from an embedded catalogue containing
    write tools - `crm_it_support_ticket_create`, `crm_complaint_close`,
    `crm_order_cancel`, `crm_purchase_request_approve`, `crm_purchase_request_reject` -
    so any one of them could be the top hit for an ordinary business question.

    That door is shut at the source since the tool RAG was dropped: the candidate is
    `DOMAIN_SPEC[domain].tools[0]`, and no domain lists a write tool. The EGRESS check is
    what this class still grades, because it is the one that holds however the name
    arrived, so the write tool is injected at the pick to reach it."""

    def test_a_post_tool_top_hit_is_never_called(self, monkeypatch):
        from app.services.chatbot.lanes.business import fetch as fetch_mod

        # The state `select_tool` can no longer produce, forced, so the check downstream
        # of it is still exercised rather than merely believed.
        monkeypatch.setattr(
            fetch_mod,
            "select_tool",
            lambda domain: [{"name": "crm_order_cancel", "similarity": 1.0}],
        )

        mcp_call = MagicMock(return_value='{"answers": []}')
        services = FetchServices(mcp_call=mcp_call)
        payload = {
            "_exit_kind": "continue",
            "gate": {
                "compatible_entities": [
                    {
                        "uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310",
                        "entity_type": "product",
                        "code": "SRTWB7096",
                    }
                ]
            },
        }

        fragment = business_mod.run_fetch(payload, services=services, dry_run=False)

        assert mcp_call.call_args_list == [], (
            "the read-only chatbot must never call a write (POST) MCP tool: mcp_call "
            f"was invoked with {mcp_call.call_args_list!r} - tool_filter picked "
            "'crm_order_cancel' with no allow-list check "
            "(lanes/business/fetch.py::tool_filter)"
        )
        reason = json.dumps(fragment, default=str)
        assert fragment.get("kind") != "result", (
            f"the turn must not read as an ordinary answered result when the only "
            f"candidate tool was a write tool refused before the call: {fragment!r}"
        )
        assert "crm_order_cancel" in reason or "not_allowed" in reason or "refus" in reason.lower(), (
            "the fetch fragment must name the refusal (a 'tool_not_allowed' reason, or "
            "equivalent) so an operator reading the trace can see why nothing answered: "
            f"{fragment!r}"
        )


# --------------------------------------------------------------------------- #
# Finding 6 - RETIRED (AC-1592). `TestLiveTailSessionPatchAbsentIsPreserved` audited an
# OLD `run_tail` shape: a lane composed a "sealed" reply via `tail/compile_state.py`'s
# `compile_current_state`/`seal()`, and the write site read `sealed.get("session_patch")
# or {}` - a blob that could be ABSENT (bug: treated as a reset) or EXPLICITLY `{}` (a
# real reset). The rewritten `run_tail` (`engine.py::run_tail`, measured this session,
# `app/services/chatbot/engine.py` ~2990-3017) has no "sealed reply" and no
# "session_patch" concept at all: it builds `payload` directly and unconditionally from
# `state.focus` / `turn_pending.to_wire(question)` / the carried `before` dict - every
# one of the five keys is a real value on every call, never an absent-vs-explicit-empty
# blob read off someone else's composed reply. The absent/explicit distinction this
# class existed to prove cannot occur in the new write site, so there is nothing left
# for it to guard - RETIRE, not port (`tail/compile_state.py` itself no longer exists in
# this worktree, confirmed via `ls app/services/chatbot/tail/`). D14 itself (a dry run
# writes nothing) is still covered by this file's OTHER dry-run assertions elsewhere and
# by `test_turn_replay.py`'s own `is_test=True` runs.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Owner console defect J (part b): a dry-run escalation's `assign_conversation` preview
# must carry the assignee the LIVE turn would pick (respond_user_id + name), with
# `preview: true` - never `respond_user_id: null` (today's placeholder, pinned by
# `test_s5_escalation_lane.py::test_dry_run_never_reaches_next_assignee`, which this fix
# is expected to flip red - the coder amends that test's AC-507 assertion once this
# lands). The real round-robin arithmetic is proven separately, against real Postgres, by
# `test_team_hierarchy_and_round_robin.py::test_preview_next_assignee_does_not_advance_the_cursor`;
# this test proves the ESCALATION LANE actually wires that preview into the dry-run
# action, and that neither the LIVE draw (`next_assignee`) nor the SLA write
# (`sla_create`) ever fires on a dry run, and no round-robin cursor row exists afterwards.
# --------------------------------------------------------------------------- #


class TestDryRunEscalationPreviewsTheRealNextAssignee:
    def test_assign_conversation_preview_carries_the_would_be_assignee(
        self, session_factory
    ) -> None:
        from app.models.access import AgentTeamRoundRobinCursor
        from app.models.sla import ConversationSLATracking
        from app.services.chatbot.lanes.escalation import run
        from tests.chatbot.test_s5_escalation_lane import (
            _assignment_ctx_and_item,
            _services,
        )

        ctx, item = _assignment_ctx_and_item()
        services = _services()
        # The contract does not mandate this exact seam name (the coder may rename it),
        # but SOME seam on the bundle must be what a dry run reads the preview assignee
        # from - `next_assignee` itself stays untouched (H37).
        preview_assignee = {
            "assignee_id": "usr-member-2",
            "assignee_name": "Member Two",
            "assignee_respond_user_id": "respond-usr-member-2",
        }
        services.preview_assignee = MagicMock(return_value=preview_assignee)

        result = run(ctx, item, services=services, dry_run=True)

        assign = next(a for a in result["actions"] if a["kind"] == "assign_conversation")
        assert assign.get("respond_user_id") == "respond-usr-member-2", (
            "the dry-run preview must name the assignee the live turn WOULD pick, not "
            f"leave respond_user_id null: {assign!r}"
        )
        assert assign.get("preview") is True
        assert assign.get("dry_run") is True

        services.next_assignee.assert_not_called()
        services.sla_create.assert_not_called()

        db = session_factory()
        assert db.query(AgentTeamRoundRobinCursor).count() == 0, (
            "a dry-run preview must never create or advance a round-robin cursor row"
        )
        assert db.query(ConversationSLATracking).count() == 0


# --------------------------------------------------------------------------- #
# Owner console defect L, the GUARDRAIL (prod turn 9dd4cd0f, 6 Sep 2026).
# --------------------------------------------------------------------------- #


class TestWordsComposedAreWordsSent:
    """"The executor executes `actions[]` and NOTHING else" (plan section 5b, ruling
    5 Sep 2026), so a lane whose words are only on `reply.text` is a customer left in
    silence. Three escalation clarify branches each independently forgot that, and the
    per-lane tests could not have caught it: each one asserted what ITS branch composes.

    This is the invariant said once, over every branch kind the CRM finishes, so the
    fourth branch that forgets fails here rather than in production. It is deliberately
    STRUCTURAL rather than a walk of every possible turn: the coverage set is asserted
    against `CRM_COMPLETED_BRANCH_KINDS` itself, so a NEW completed kind that nobody
    wired in fails this test on the day it is added.
    """

    # Every completed kind, and the TEST that holds this invariant for it. A NAME, not a
    # tick: the test below imports each one and reads its source, so a home that is
    # renamed, deleted, or that stops asserting on `send_message` fails here. The first
    # version of this guardrail was a hand-maintained set of kind names and asserted only
    # that somebody had written a name down, which is bookkeeping, not a guardrail
    # (reviewer, #705).
    COVERED_BY: dict[str, tuple[str, str]] = {
        # `access_denied` is refused before anything is stubbed; its own test (session-
        # write is the interesting property there) rather than the parametrised table.
        # Re-pinned 17 Sep 2026 (tester): was the same shared entry as the seven below
        # until `TestCannedBranchesFinishInTurn` was retired at `c830e002a` (16 Sep);
        # `TestAccessDeniedNoSessionWrite` already asserted the strict `actions == [...]`
        # shape independently and needed no change, only this dict entry did.
        "access_denied": (
            "tests.chatbot.test_s3_canned_and_ideate",
            "TestAccessDeniedNoSessionWrite.test_access_denied_sends_without_session_write",
        ),
        # One parametrised test over the other seven canned kinds; it asserts the action
        # list EQUALS one `send_message` carrying the reply text. Re-pinned 17 Sep 2026
        # (tester): `TestCannedBranchesFinishInTurn` was retired at `c830e002a` (16 Sep,
        # "engine investigation, not a mechanical port") and restored here after
        # re-measuring each kind directly - 4 of 7 now pass outright (the engine moved
        # since 16 Sep morning), the other 3 (`escalate_offer`, `demand_qty`,
        # `offer_hold`) are `xfail(strict=True)` there with the measured branch_kind, so
        # this guardrail still has a live, honest home for every kind rather than a
        # class that does not exist.
        **{
            kind: (
                "tests.chatbot.test_s3_canned_and_ideate",
                "TestCannedBranchesFinishInTurn.test_canned_branches_finish_in_turn",
            )
            for kind in (
                "escalate_offer",
                "escalation_declined",
                "clarify_menu",
                "not_supported",
                "demand_qty",
                "offer_hold",
                "ideate",
            )
        },
        # The clarifier stamps its own `send_message` BEFORE the tail runs (the one
        # measured shape difference the plan records, D15).
        "low_signal": (
            "tests.chatbot.test_s4_casual_lane",
            "TestLowSignalLaneIntegration.test_low_signal_finishes_in_turn",
        ),
        # The assignment arm's four actions in order; the three CLARIFY arms are the
        # second test in this class.
        "out_of_scope": (
            "tests.chatbot.test_s5_escalation_lane",
            "test_assignment_actions_in_order",
        ),
        # The business arms compose through the same tail seal; the exit matrix asserts a
        # `send_message` on every cell the CRM completes.
        **{
            kind: (
                "tests.chatbot.test_s6_s7_integration",
                "TestBusinessQueryExitMatrixAc715.test_exit_matrix_cell_ac715",
            )
            for kind in ("business_query", "check_promotion", "stock_denied")
        },
    }

    @staticmethod
    def _source_of(module_path: str, dotted: str) -> str:
        import importlib
        import inspect

        target: Any = importlib.import_module(module_path)
        for part in dotted.split("."):
            target = getattr(target, part)
        return inspect.getsource(target)

    def test_every_completed_branch_kind_has_a_test_that_asserts_the_action(self) -> None:
        from app.services.chatbot.contracts import CRM_COMPLETED_BRANCH_KINDS

        missing = CRM_COMPLETED_BRANCH_KINDS - set(self.COVERED_BY)
        assert not missing, (
            "these branch kinds finish inside the CRM and nothing says their words reach "
            f"the customer as an ACTION: {sorted(missing)}. Write the assertion where the "
            "lane is tested and name that test here."
        )
        stale = set(self.COVERED_BY) - CRM_COMPLETED_BRANCH_KINDS
        assert not stale, (
            f"these are no longer completed by the CRM: {sorted(stale)} - drop them"
        )

        for kind, (module_path, dotted) in sorted(self.COVERED_BY.items()):
            try:
                source = self._source_of(module_path, dotted)
            except AttributeError as missing_attr:  # noqa: PERF203 - the message is the point
                raise AssertionError(
                    f"{kind}: its declared home {module_path}::{dotted} no longer exists. "
                    "A renamed or deleted test is exactly the case this guardrail is for."
                ) from missing_attr
            assert "send_message" in source, (
                f"{kind}: {module_path}::{dotted} is named as the test that proves its "
                "words are SENT, and it no longer mentions `send_message` at all"
            )
            assert '"text"' in source or "actions[0]" in source or "== expected_text" in source, (
                f"{kind}: {module_path}::{dotted} looks at the action KIND but never at "
                "its text, so a send_message carrying the wrong words would pass"
            )

    def test_the_escalation_lane_never_composes_words_it_does_not_send(self) -> None:
        """The lane this defect was found in, over EVERY arm it can return.

        Driven through `run` rather than by reading the source: an arm that composes
        `clarify_text` and returns no `send_message` is the defect, whatever branch it
        came down.
        """
        from app.services.chatbot.lanes.escalation import run
        from tests.chatbot.test_s5_escalation_lane import _ctx, _item, _services

        cases = [
            (
                "ambiguous person",
                _ctx(
                    routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries"},
                    person_mention="Nurain",
                    text="escalate to Nurain",
                    parser_raw={"routing": {"suggested_team": None, "suggested_agent": None}},
                ),
                _item(team="customer_service"),
            ),
            (
                "no team, one carried",
                _ctx(
                    routing={"suggested_team": None, "suggested_agent": "general_enquiries"},
                    prev_variables={"routing": {"suggested_team": "purchasing"}},
                ),
                _item(team=None),
            ),
            (
                "multi company unpicked",
                _ctx(
                    routing={"suggested_team": "customer_service"},
                    prev_variables={
                        "routing": {"suggested_team": "customer_service"},
                        "selection_context": "member_offer",
                        "last_result_set": [{"uuid": "m1"}],
                        "routing_roster_plan": [
                            {"company_id": "c-1", "company_name": "Mocha"},
                            {"company_id": "c-2", "company_name": "Sorento"},
                        ],
                    },
                ),
                _item(
                    brand_code=None,
                    company_id=None,
                    company_name=None,
                    routing_source="multi_company_unpicked",
                    team="customer_service",
                ),
            ),
        ]

        def staff(_person):
            return [
                {
                    "user_id": "u-1",
                    "user_name": "Nurain A",
                    "respond_user_id": "r-1",
                    "team_code": code,
                    "team_name": name,
                }
                for code, name in (
                    ("do_customer_service", "DO Customer Service"),
                    ("project_customer_service", "Project Customer Service"),
                )
            ]

        for label, ctx, item in cases:
            for dry_run in (False, True):
                services = _services()
                services.staff_lookup = staff
                result = run(ctx, item, services=services, dry_run=dry_run)
                clarify = result.get("clarify") or {}
                words = (clarify.get("clarify_text") or "").strip()
                if not words:
                    continue
                sends = [
                    a
                    for a in (result.get("actions") or [])
                    if a.get("kind") == "send_message" and a.get("text") == words
                ]
                assert sends, (
                    f"{label} (dry_run={dry_run}) composed {words!r} and returned no "
                    f"send_message carrying it: {result.get('actions')!r}"
                )
                assert sends[0]["dry_run"] is dry_run
