"""S8a Prompts screen "Run a turn" (AC-807): for `chatbot_semantic_parser` and
`chatbot_clarifier`, `POST /api/v1/system/ai-assistant/prompts/{name}/test` must run a
DRY-RUN CHATBOT TURN instead of the assistant chat pipeline (today, `PROMPT_KEYS[name].
dry_runnable` is False for both, so the route 400s before it even looks up the version -
see `app/api/v1/system/ai_assistant.py` lines 390-420 and
`app/services/ai_prompt_registry.py`'s `PROMPT_KEYS` entries for these two names).

RED-first: none of this exists yet. Every test here is expected to fail on the SAME 400
("... is not part of the assistant pipeline ...") the route already gives for these two
keys, or on an assertion mismatch once that changes - never an import error in this file.

Reuses the chatbot engine's own test seams (`tests/chatbot/test_engine.py`'s
`stub_parser` / `stub_access` / `seeded` / `CONTACT_ID`, `tests/chatbot/
test_s4_casual_lane.py`'s `_install_stub_lane` shape, and `tests/chatbot/
test_chat_turn_endpoint.py`'s `app.api.v1.external.chat.SessionLocal` patch) so the turn
that runs is the REAL engine, not a second mock of it - only the LLM calls are stubbed.

ASSUMPTIONS, flagged for the coder (AC-002 forbids `app/api/v1/system/ai_assistant.py`
from importing `app.services.chatbot` directly - only `app/api/v1/external/chat.py`,
`app/tasks/chat_turns.py`, `app/api/v1/system/chatbot.py` and the module itself may - so
whatever this route calls has to be a function that itself lives in one of those files,
or a small helper `ai_assistant.py` imports FROM one of them):

1. The engine's `resolve_config` (parser) / `resolve_clarifier_config` (clarifier) gain
   an `override_version_id: str | None = None` kwarg, mirroring `ai_prompt_registry.
   render`'s own parameter name, and the endpoint threads `payload.version_id` through
   to it for the ONE prompt key being tested (never both at once). This is the natural,
   minimal extension - `get_prompt(..., override_version_id=...)` already exists and does
   exactly this for the assistant's own dry run.
2. The dev contact to run the synthetic turn against is passed as `contact_respond_id`
   on `DryRunRequest` (added here for chatbot keys) OR resolved from the workspace as the
   plan prose describes ("dev contact from the workspace") - this file seeds a real
   `respond_contacts` row at `CONTACT_ID` (from `test_engine`) either way, so whichever
   the coder builds, a resolvable contact exists.
3. The response for these two keys carries `turn_id`, `status`, `branch_kind` and `trace`
   (a list of the engine's own stage records) - fields `DryRunResponse` does not declare
   today, so a naive implementation that keeps `response_model=DryRunResponse` would
   silently drop them (`LESSONS-LEARNT`: "response_model silently drops undeclared
   fields"). This file asserts them straight off the parsed JSON body, so that drop shows
   up as a missing key, not a passing test.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.main import app
from app.services.ai_prompt_service import AIPromptService
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.lanes import casual
from app.services.user_service import UserPermissionService
from tests.chatbot.test_engine import CONTACT_ID, _parser_output, seeded, stub_access, stub_parser  # noqa: F401

EDIT = "system.ai_assistant_settings.edit"
# S8a review S3: the Prompts Test action for the two chatbot keys runs a dry-run turn for
# a CALLER-NAMED contact and hands back its trace, so it requires the slug the Chat
# History trace screen uses to show that trace, on top of the prompt-editing one.
TRACE_VIEW = "system.chat_history.view"
_GRANTS = {EDIT, TRACE_VIEW}
TEST_URL = "/api/v1/system/ai-assistant/prompts/{name}/test"


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


@pytest.fixture()
def client(session_factory):
    def _override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: {"id": "u-zzt-807"}
    app.dependency_overrides[get_current_user_or_api_key] = lambda: {"id": "u-zzt-807"}
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def chatbot_engine_wired_to_test_db(monkeypatch, session_factory):
    """Same real-DB-write guard `test_chat_turn_endpoint.py` established: whatever route
    ends up calling `run_turn` MUST use the patched `SessionLocal`, or a "test" click on
    the Prompts screen writes a real turn into the shared dev database."""
    monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)


def _create_version(db, name: str, *, template: str = "TEST VERSION, no tokens") -> dict:
    return AIPromptService(db).save_version(
        name, template=template, commit_message="ZZT S8a test version", user_id=None
    )


class TestChatbotSemanticParserRunsATurnNotAssistantChat:
    def test_dry_run_turn_response_shape(
        self, client, session_factory, seeded, stub_access, monkeypatch, chatbot_engine_wired_to_test_db
    ):
        db = session_factory()
        version = _create_version(db, "chatbot_semantic_parser")
        db.commit()

        captured: dict = {}

        def fake_resolve_config(dbx, *, current_date, override_version_id=None, grants=None):
            captured["override_version_id"] = override_version_id
            resolved_number = version["version"] if override_version_id == version["id"] else 1
            return parser_mod.ParserConfig(
                system_prompt="stub",
                prompt_version=resolved_number,
                provider="openai",
                model="gpt-test",
                api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
        stub_access()

        resp = client.post(
            TEST_URL.format(name="chatbot_semantic_parser"),
            json={
                "message": "price for SRTWC8517",
                "version_id": version["id"],
                "contact_respond_id": str(CONTACT_ID),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body.get("turn_id"), f"no turn_id in response: {body}"
        assert body.get("status") in ("done", "delegated"), body
        assert body.get("branch_kind"), f"no branch_kind in response: {body}"
        trace = body.get("trace")
        assert isinstance(trace, list) and trace, f"no trace in response: {body}"

        assert captured.get("override_version_id") == version["id"], (
            "the endpoint did not thread payload.version_id into resolve_config's "
            "override_version_id - the parser ran on whatever version is currently "
            "published, not the one the operator is testing"
        )
        understood = next((rec for rec in trace if rec.get("stage") == "understood"), None)
        assert understood is not None, f"no 'understood' stage in trace: {trace}"
        assert understood.get("facts", {}).get("prompt_version") == version["version"], (
            "the trace's prompt_version fact does not match the version being tested"
        )

        # D14: a dry-run turn writes ZERO rows outside chatbot.turns.
        after = session_factory()
        session_vars = after.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        assert session_vars == json.dumps({"variables": {}}) or session_vars == {
            "variables": {}
        }, "a dry-run prompt test must not write respond_contacts.session_vars"

    def test_dormant_or_missing_version_still_404s(self, client, chatbot_engine_wired_to_test_db):
        resp = client.post(
            TEST_URL.format(name="chatbot_semantic_parser"),
            json={"message": "hi", "version_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 404, resp.text

    def test_a_non_uuid_version_id_is_404_not_500(
        self, client, chatbot_engine_wired_to_test_db
    ):
        """S8a review N6: `ai_prompt_versions.id` is a `uuid` column, so an unvalidated
        string reached Postgres and raised - a 500 where the route promises a 404."""
        resp = client.post(
            TEST_URL.format(name="chatbot_semantic_parser"),
            json={"message": "hi", "version_id": "not-a-uuid"},
        )
        assert resp.status_code == 404, resp.text

    def test_the_chat_history_view_slug_is_required_as_well(
        self, client, chatbot_engine_wired_to_test_db, monkeypatch
    ):
        """S8a review S3: `system.ai_assistant_settings.edit` is a prompt-editing slug and
        says nothing about who may read a customer's remembered conversation state. A
        caller holding only it must not be able to run a turn for a contact they name."""
        monkeypatch.setattr(
            UserPermissionService,
            "check_user_has_permission",
            lambda self, uid, slug: slug == EDIT,
        )
        resp = client.post(
            TEST_URL.format(name="chatbot_semantic_parser"),
            json={
                "message": "hi",
                "version_id": "00000000-0000-0000-0000-000000000000",
                "contact_respond_id": str(CONTACT_ID),
            },
        )
        assert resp.status_code == 403, resp.text
        assert "system.chat_history.view" in resp.text


class TestChatbotClarifierRunsATurnNotAssistantChat:
    def test_dry_run_turn_response_shape(
        self,
        client,
        session_factory,
        seeded,
        stub_parser,
        stub_access,
        monkeypatch,
        chatbot_engine_wired_to_test_db,
    ):
        db = session_factory()
        version = _create_version(db, "chatbot_clarifier", template="Small talk only.")
        db.commit()

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
        db2 = session_factory()
        from app.models.user import SystemSetting

        db2.add(SystemSetting(chatbot_completed_lanes=["low_signal"]))
        db2.commit()

        captured: dict = {}
        monkeypatch.setattr(casual, "resolve_for_prompt", lambda dbx, *, ctx: {"resolutions": []})

        def fake_resolve_clarifier_config(dbx, *, override_version_id=None):
            captured["override_version_id"] = override_version_id
            resolved_number = version["version"] if override_version_id == version["id"] else 1
            return type(
                "ClarifierConfigStub",
                (),
                {"prompt_version": resolved_number, "model": "gpt-test"},
            )()

        monkeypatch.setattr(casual, "resolve_clarifier_config", fake_resolve_clarifier_config)
        monkeypatch.setattr(
            casual, "call_clarifier", lambda config, user_prompt: '{"response": "Hi there!"}'
        )

        resp = client.post(
            TEST_URL.format(name="chatbot_clarifier"),
            json={
                "message": "hi",
                "version_id": version["id"],
                "contact_respond_id": str(CONTACT_ID),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("turn_id"), f"no turn_id in response: {body}"
        assert body.get("branch_kind") == "low_signal", body
        trace = body.get("trace")
        assert isinstance(trace, list) and trace, f"no trace in response: {body}"

        assert captured.get("override_version_id") == version["id"]


class TestNonChatbotKeyKeepsTheAssistantDryRun:
    def test_a_regular_assistant_key_is_unaffected(self, client):
        """`router`/`semantic_parser`/etc keep running through
        `AIAssistantChatService.respond(..., dry_run=True)` - this file's change must be
        scoped to the two chatbot keys, never a router-wide behaviour swap."""
        resp = client.post(
            TEST_URL.format(name="judge"),
            json={"message": "hi", "version_id": "any"},
        )
        # `judge` is dormant (`active=False`) - the assistant dry-run path's OWN 400, not
        # a 200 from an accidentally-shared chatbot code path.
        assert resp.status_code == 400, resp.text
        assert "dormant" in resp.text.lower()

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
