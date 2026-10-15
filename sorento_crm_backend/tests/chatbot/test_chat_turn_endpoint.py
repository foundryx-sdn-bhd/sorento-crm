"""`POST /api/v1/external/chat/turn` through the real FastAPI app + a valid integration
key - the seam `response_model` silently drops an undeclared field at
(`LESSONS-LEARNT.md`: "response_model silently drops undeclared fields. Assert the field
in a test"), and the seam D14/AC-702's "zero writes" claim actually has to be measured
against, not just asserted from inside `run_turn` (`test_engine.py`'s `TestDryRun` proves
the engine writes nothing outside `chatbot.turns`; this file proves the ENDPOINT - which
writes an `integration_log` on every LIVE call and none at all on a dry run (H57) - does
not silently touch anything else either).

Auth mirrors `test_module_and_endpoint.py`'s `key`/`db` fixtures (a real integration, a
real issued `X-API-Key`, a role holding only `integration.chat_turn.submit`) but against
the full `app.main` app and the blank-schema `session_factory` from `conftest.py`, so the
whole router chain (module guard, permission guard, the route itself) runs for real.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_db
from app.models.chatbot_turn import ChatbotTurn
from app.models.integration import Integration, IntegrationApiKey
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from app.services.integration_key_service import IntegrationKeyService
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod

from app.api.v1.external.chat import MAX_LOGGED_PAYLOAD_BYTES

SLUG = "integration.chat_turn.submit"
_TURN_URL = "/api/v1/external/chat/turn"


@pytest.fixture()
def api_key(session_factory):
    """A real integration whose role holds exactly `integration.chat_turn.submit`."""
    db = session_factory()
    user = User(
        email="chatbot-endpoint@integrations.local",
        name="Integration: chatbot endpoint test",
        status="ACTIVE",
        is_integration=True,
    )
    db.add(user)
    db.flush()
    role = UserRole(slug="integration_chatbot_endpoint_test", name="Integration: chatbot (test)")
    db.add(role)
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    permission = UserPermission(slug=SLUG, name="Submit chatbot turns")
    db.add(permission)
    db.flush()
    db.add(UserRolePermission(role_id=role.id, permission_id=permission.id))
    integration = Integration(
        name="n8n-chatbot-endpoint-test", type="n8n", act_as_user_id=user.id, is_active=True
    )
    db.add(integration)
    db.flush()
    issued = IntegrationKeyService(db).issue_key(integration)
    db.commit()
    return issued


@pytest.fixture()
def client(session_factory):
    def _override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def seeded_contact(session_factory):
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
def stub_engine_seams(monkeypatch, session_factory):
    """**Real-DB-write finding (tester):** `app/api/v1/external/chat.py` hardcodes
    `SessionLocal` - the actual `DATABASE_URL` engine, not the request's `Depends(get_db)`
    session - as the engine's own session factory (its own docstring: "which is why it
    takes SessionLocal rather than this one"). `dry_run`/`is_test` only suppresses WRITES
    inside the engine's business logic; it does not change which database the turn row
    lands in. A first version of this test hit the real endpoint without patching this and
    left a real, half-written `chatbot.turns` row (status `processing`, contact
    `ZZT-contact-900000009`) in the shared dev database - found and deleted by hand. Any
    FUTURE test that calls this endpoint through `TestClient` must patch
    `app.api.v1.external.chat.SessionLocal` the same way, or it writes to the real DB too.
    """
    monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)

    def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General Enquiries",
            "attributes": None,
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")


class TestResponseModelSurvival:
    """`TurnResponse` (`response_model=TurnResponse`) must carry every field the caller
    needs to execute the turn - a field present on `TurnResult.as_dict()` but absent from
    the Pydantic model is silently dropped from the HTTP body, not an error."""

    def test_every_response_field_survives_serialisation(self, client, api_key, monkeypatch):
        canned = {
            "turn_id": "11111111-1111-1111-1111-111111111111",
            "ctx": {"contact": {"id": CONTACT_ID}, "text": {}, "session": {}, "parse": {}, "access": {}, "media": None},
            "item": {"branch_kind": "business_query", "allowed": True, "decision": "allow"},
            "branch_kind": "business_query",
            "delegate": "business_query",
            # S6a: the business lane's resolve+gate result. It is what n8n's `resolve-arm`
            # runs on, so a `response_model` that dropped it would leave the caller
            # entering that Switch with no `_exit_kind` and every presence gate false.
            "delegate_payload": {
                "_exit_kind": "continue",
                "resolved": {"tokens": []},
                "gate": {"gate_passed": True},
                "ctx_resolved": {"ctx": {}},
                "aggregate": None,
                "tier_gate": None,
                "annotate_incoming": None,
            },
            "reply": None,
            "actions": [{"kind": "send_message", "text": "hi", "dry_run": True}],
            "session_patch": {"variables": {"pending": None}},
            "duplicate": True,
        }

        class _FakeResult:
            def as_dict(self) -> dict[str, Any]:
                return canned

            status = "delegated"
            error = None

        monkeypatch.setattr("app.api.v1.external.chat.run_turn", lambda *a, **k: _FakeResult())

        resp = client.post(
            _TURN_URL,
            json={"envelope": {"message": {}, "contact": {"id": CONTACT_ID}}},
            headers={"X-API-Key": api_key},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in (
            "duplicate",
            "session_patch",
            "delegate",
            "delegate_payload",
            "branch_kind",
            "ctx",
            "item",
            "actions",
        ):
            assert key in body, f"{key!r} missing from the response body: {body}"
        assert body["duplicate"] is True
        assert body["session_patch"] == canned["session_patch"]
        assert body["delegate"] == "business_query"
        assert body["delegate_payload"] == canned["delegate_payload"], (
            "every key of the sub's output item must survive the wire, nested ones "
            "included - n8n's stand-in chain reads all six by name"
        )
        assert body["branch_kind"] == "business_query"
        # The wire shape survives the round trip: the id stays the NUMBER respond.io sent.
        assert body["ctx"]["contact"]["id"] == CONTACT_ID
        assert body["item"]["branch_kind"] == "business_query"
        assert body["actions"] and body["actions"][0]["dry_run"] is True


class TestDryRunEndpointZeroWrites:
    """AC-702 measured through the whole request, not just `run_turn` - the endpoint's
    OWN write, the integration log, included.

    **Amended by H57 (this PR).** This class used to assert `after_log == before_log + 1`,
    reading the module docstring's "every call writes an integration_log" as covering a
    dry run too. It does not any more, and the change is the fix rather than a weakened
    test: `integration_log` is business state an operator reads per customer (real contact
    id, the customer's message text, the endpoint that was called), so a row written
    because somebody pressed Test on the Prompts screen describes a call that never
    happened to that customer. Nothing is lost by skipping it - the turn's own
    `chatbot.turns` row is written with `is_test = true` and carries the envelope, the
    response and the whole trace, which is strictly more than the log line held. The LIVE
    path still logs on success AND failure; `TestTheCallLogNeverStoresACredentialOrAn
    UnboundedBody` below covers it.
    """

    def _count(self, session_factory, table: str, *, where: str | None = None) -> int:
        """Unqualified table name only - resolved via the isolated schema's `search_path`
        (`SET LOCAL search_path` in `session_factory`). A schema-QUALIFIED name (e.g.
        `chatbot.turns`) bypasses `search_path` entirely and Postgres resolves it against
        the REAL `chatbot` schema instead of the translated scratch one - see
        `_turns_count` below, which learned this the hard way (see its own docstring)."""
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return session_factory().execute(text(sql)).scalar()

    def _turns_count(self, session_factory) -> int:
        """`chatbot.turns` via the ORM model, not raw SQL.

        **Real-DB-read finding (tester):** a first version of this test counted with
        raw SQL `SELECT COUNT(*) FROM chatbot.turns`. That schema-qualified name is NOT
        rewritten by `blank_schema_engine()`'s `schema_translate_map` (which only rewrites
        ORM/`Table` constructs) and is NOT resolved via `search_path` either (Postgres
        skips `search_path` once the schema is named explicitly), so it silently counted
        the REAL, shared `chatbot.turns` table - the same real-DB-write finding
        `stub_engine_seams` documents, from the read side. `ChatbotTurn.__table__` carries
        `schema="chatbot"` as an ORM construct, so querying through the model IS translated
        correctly.
        """
        return (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.contact_respond_id == str(CONTACT_ID))
            .count()
        )

    def test_dry_run_touches_only_chatbot_turns(
        self, client, api_key, session_factory, seeded_contact, stub_engine_seams
    ):
        before_session_vars = session_factory().execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        before_sla = self._count(session_factory, "conversation_sla_tracking")
        before_chat_history = self._count(session_factory, "chat_histories")
        before_log = self._count(
            session_factory, "integration_log", where=f"external_reference = '{CONTACT_ID}'"
        )
        before_turns = self._turns_count(session_factory)

        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-msg-dry-run-endpoint"
        payload = {"envelope": {**json.loads(envelope.model_dump_json()), "test_run_id": "ZZT-run-endpoint"}}

        resp = client.post(_TURN_URL, json=payload, headers={"X-API-Key": api_key})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["duplicate"] is False

        after_session_vars = session_factory().execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        after_sla = self._count(session_factory, "conversation_sla_tracking")
        after_chat_history = self._count(session_factory, "chat_histories")
        after_log = self._count(
            session_factory, "integration_log", where=f"external_reference = '{CONTACT_ID}'"
        )
        after_turns = self._turns_count(session_factory)

        assert after_session_vars == before_session_vars, "session_vars must be untouched on a dry run"
        assert after_sla == before_sla == 0
        assert after_chat_history == before_chat_history == 0
        # H57: ZERO, not one. The call log is outside `chatbot.turns`, so D14's "a dry
        # run writes nothing outside that table" covers it too.
        assert after_log == before_log == 0
        # chatbot.turns gains exactly the one row for this turn, flagged is_test.
        assert after_turns == before_turns + 1
        row = (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.id == body["turn_id"])
            .first()
        )
        assert row.is_test is True


class TestTheCallLogNeverStoresACredentialOrAnUnboundedBody:
    """`integration_logs` is written once per customer message and never pruned, so
    anything that lands in it lands there forever (security review S2/S3)."""

    @staticmethod
    def _latest_log(session_factory, endpoint_suffix: str):
        row = (
            session_factory()
            .execute(
                text(
                    "SELECT request_headers, request_payload FROM integration_log "
                    "WHERE endpoint LIKE :e ORDER BY created_at DESC LIMIT 1"
                ),
                {"e": f"%{endpoint_suffix}"},
            )
            .fetchone()
        )
        assert row is not None, f"no integration_log row for {endpoint_suffix}"
        return row

    def test_no_credential_header_reaches_the_log(
        self, client, api_key, session_factory, seeded_contact, stub_engine_seams
    ):
        """`X-API-Key` was masked and nothing else was. Both it and `Authorization`
        are accepted by `get_current_user_or_api_key`, so the caller's own choice of
        header decided whether the secret was persisted."""
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-msg-header-mask"
        payload = {"envelope": json.loads(envelope.model_dump_json())}

        resp = client.post(
            _TURN_URL,
            json=payload,
            headers={
                "X-API-Key": api_key,
                "Authorization": "Bearer ZZT-BEARER-SECRET",
                "Cookie": "session=ZZT-COOKIE-SECRET",
                "X-Chatbot-Retry-Key": "ZZT-RETRY-SECRET",
            },
        )
        assert resp.status_code == 200, resp.text

        headers = self._latest_log(session_factory, "/chat/turn").request_headers
        for secret in (api_key, "ZZT-BEARER-SECRET", "ZZT-COOKIE-SECRET", "ZZT-RETRY-SECRET"):
            assert secret not in headers, f"{secret!r} was persisted in an integration log"
        assert headers.count("***") >= 4

    def test_an_oversized_complete_body_is_logged_as_a_note_not_a_copy(
        self, client, api_key, session_factory, seeded_contact
    ):
        """`/turn` bounds what a caller can push into the log; `/complete` did not, and
        its REFUSAL path logs too - so an unknown turn with a huge body wrote an
        unbounded row. Refusing is what makes this the cheapest place to measure it:
        no turn row is needed and the log row is written before the tail could run."""
        big = "z" * (MAX_LOGGED_PAYLOAD_BYTES + 1024)
        body = {
            "item": {"branch_kind": "business_query"},
            "ctx": {
                "contact": {"id": CONTACT_ID},
                "text": {"message": {"messageId": "ZZT-msg-no-such-turn"}},
            },
            "result": {"rows": [big]},
        }

        resp = client.post(
            "/api/v1/external/chat/turn/complete", json=body, headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 404, resp.text

        stored = self._latest_log(session_factory, "/chat/turn/complete").request_payload
        assert len(stored) < MAX_LOGGED_PAYLOAD_BYTES
        assert big not in stored
        assert json.loads(stored)["note"].startswith("payload omitted")
        assert json.loads(stored)["reference"] == str(CONTACT_ID)
