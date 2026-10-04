"""S8a hardening: the tester-pass gap list handed to the coder after AC-804 / AC-806 /
AC-807 landed (`app/services/outbound_url_guard.py`, the respond-workspace retry-key
write-only contract, the Prompts screen "Run a turn" dry run, `_assert_emission`'s
container-type check, D14/D15/R5).

Each class below is one item from that list. Several tests find real, MEASURED gaps
(checked against the current code before writing the assertion, not assumed) and are
RED-first per this lane's own convention (`tests/chatbot/test_s8_retry_config.py`'s
docstring: "RED-first: none of this exists yet... an assertion mismatch"). Not fixed
here per the tester brief - each red test's docstring says what it found and where the
fix belongs.

Postgres only. `TestOutboundUrlGuardHardening` is a pure-function suite (no DB, no
network - `socket.getaddrinfo` is patched). Everything else uses `tests/chatbot/
conftest.py`'s blank-schema `session_factory`, or `tests/_pg_fixture.blank_session` for
the plain-model/route tests that do not need the engine's multi-session shape.
"""
from __future__ import annotations

import json
import socket
import uuid
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.main import app
from app.models.ai_prompt import AIPromptVersion
from app.models.chatbot_turn import ChatbotTurn
from app.models.respond_workspace import RespondWorkspace
from app.schemas.respond_workspace import RespondWorkspaceCreate, RespondWorkspaceUpdate
from app.services.ai_prompt_service import AIPromptService
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.outbound_url_guard import OutboundUrlRejected, assert_safe_outbound_url
from app.services.respond_workspace_service import RespondWorkspaceService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    _turn_row,
    seeded,
    stub_access,
    stub_parser,
)

VALID_RETRY_URL = "https://automate-sorento.foundryx.my/webhook/sorento-main-inject"
PLAIN_RETRY_KEY = "ZZT-plain-retry-key-s8a"
WORKSPACES_BASE = "/api/v1/system/respond-workspaces"
TURNS_BASE = "/api/v1/system/chatbot/turns"
PROMPT_TEST_URL = "/api/v1/system/ai-assistant/prompts/{name}/test"


def _resolve_ok(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 443))]


# --------------------------------------------------------------------------- #
# 1. SSRF guard (`app/services/outbound_url_guard.py`, AC-804).               #
# Pure functions: no DB, no network - `socket.getaddrinfo` is patched.       #
# --------------------------------------------------------------------------- #


class TestOutboundUrlGuardHardening:
    def test_two_addresses_only_the_second_private_is_rejected(self, monkeypatch):
        """The docstring's own claim ('every resolved address is checked, not just the
        first') - graded directly rather than trusted."""

        def two_addresses(host, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("198.51.100.7", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 443)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", two_addresses)
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host")

        with pytest.raises(OutboundUrlRejected) as excinfo:
            assert_safe_outbound_url("https://multi-answer.example.com/hook")
        assert excinfo.value.rule == "private"

    def test_an_ipv4_mapped_ipv6_answer_is_rejected(self, monkeypatch):
        """`::ffff:10.0.0.1` is a private IPv4 address wearing an IPv6 hat - the guard's
        own comment says `address.ipv4_mapped` unwraps it before the range check runs."""

        def mapped_answer(host, *args, **kwargs):
            return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::ffff:10.0.0.1", 443, 0, 0))]

        monkeypatch.setattr(socket, "getaddrinfo", mapped_answer)
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host")

        with pytest.raises(OutboundUrlRejected) as excinfo:
            assert_safe_outbound_url("https://mapped.example.com/hook")
        assert excinfo.value.rule == "private"

    def test_a_hostname_that_fails_to_resolve_is_rejected_clearly(self, monkeypatch):
        def fails(host, *args, **kwargs):
            raise socket.gaierror("nodename nor servname provided")

        monkeypatch.setattr(socket, "getaddrinfo", fails)
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host")

        with pytest.raises(OutboundUrlRejected) as excinfo:
            assert_safe_outbound_url("https://nowhere.example.com/hook")
        assert excinfo.value.rule == "unresolvable"
        assert "does not resolve" in excinfo.value.message

    def test_a_url_that_passed_at_save_time_fails_at_use_time_when_dns_changes(
        self, monkeypatch
    ):
        """The TOCTOU window the guard's own docstring names: resolved once at save
        (public), resolved again at use (private, simulating a changed DNS answer or a
        rebind) - `socket.getaddrinfo` is patched to a DIFFERENT answer between the two
        calls rather than once for the whole test."""
        url = "https://webhook.example.com/hook"
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host")

        monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **kw: _resolve_ok("198.51.100.7"))
        saved = assert_safe_outbound_url(url, label="The chatbot retry webhook URL")
        assert saved == url

        monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **kw: _resolve_ok("10.0.0.9"))
        with pytest.raises(OutboundUrlRejected) as excinfo:
            assert_safe_outbound_url(url, label="The chatbot retry webhook URL")
        assert excinfo.value.rule == "private"

    def test_userinfo_in_the_url_is_rejected(self, monkeypatch):
        """Found as a gap during this hardening pass (measured directly against
        `assert_safe_outbound_url` before writing this assertion: `urlsplit(...).hostname`
        silently strips userinfo, so `https://user:pw@host/webhook` against an ordinary
        public host was ACCEPTED with the credential pair simply discarded - a
        URL-confusion / credential-leak class an operator paste could sail through). Fixed
        concurrently in the same lane (`assert_safe_outbound_url` now rejects `parts.
        username or parts.password` under the `"userinfo"` rule) - this is now the
        regression guard for that rule, not an open finding.
        """
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **kw: _resolve_ok("198.51.100.7"))
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host")

        with pytest.raises(OutboundUrlRejected):
            assert_safe_outbound_url("https://user:pw@host.example.com/webhook")

    def test_plain_http_is_rejected(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **kw: _resolve_ok("198.51.100.7"))
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host")

        with pytest.raises(OutboundUrlRejected) as excinfo:
            assert_safe_outbound_url("http://plain.example.com/hook")
        assert excinfo.value.rule == "https"

    def test_a_public_ip_literal_is_accepted(self, monkeypatch):
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host")
        # No getaddrinfo patch needed: a literal IP is recognised by `ipaddress.ip_address`
        # before any DNS lookup happens.
        assert assert_safe_outbound_url("https://203.0.113.10/hook") == "https://203.0.113.10/hook"


# --------------------------------------------------------------------------- #
# 2. Respond-workspace route permissions: add / delete / set-default must NOT #
#    widen the way PUT did (AC-804 widens only the update route).            #
# --------------------------------------------------------------------------- #

_GRANTS: set[str] = set()
_ACTOR: dict[str, Any] = {"id": None, "name": "ZZT S8a Hardening Tester"}


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: set(_GRANTS)
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def session():
    with blank_session() as s:
        yield s


@pytest.fixture()
def client(session):
    def _override_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    _ACTOR["id"] = str(uuid.uuid4())
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def default_workspace(session) -> RespondWorkspace:
    svc = RespondWorkspaceService(session)
    return svc.create(
        RespondWorkspaceCreate(space_id="ZZT-space-s8a-default", api_key="k", is_default=True)
    )


class TestWorkspaceRoutePermissionsNotWidened:
    """`user_management.settings.edit` is a widening the plan grants ONLY to the update
    route (AC-804's own words: 'editable ... under user_management.settings.edit', naming
    the field, not the resource). Add, delete and set-default change WHICH workspace the
    whole install talks to and keep their own single slug."""

    def test_add_refuses_settings_edit_alone(self, client):
        _GRANTS.add("user_management.settings.edit")
        resp = client.post(WORKSPACES_BASE, json={"space_id": "ZZT-s8a-add", "api_key": "k"})
        assert resp.status_code == 403, resp.text

    def test_delete_refuses_settings_edit_alone(self, client, default_workspace):
        _GRANTS.add("user_management.settings.edit")
        resp = client.delete(f"{WORKSPACES_BASE}/{default_workspace.id}")
        assert resp.status_code == 403, resp.text

    def test_set_default_refuses_settings_edit_alone(self, client, default_workspace):
        _GRANTS.add("user_management.settings.edit")
        resp = client.post(f"{WORKSPACES_BASE}/{default_workspace.id}/set-default")
        assert resp.status_code == 403, resp.text

    def test_add_succeeds_with_its_own_slug(self, client):
        """Sanity control: the 403s above are the permission gate, not a body/route bug."""
        _GRANTS.add("system.respond_workspaces.add")
        resp = client.post(WORKSPACES_BASE, json={"space_id": "ZZT-s8a-add-ok", "api_key": "k"})
        assert resp.status_code == 201, resp.text


# --------------------------------------------------------------------------- #
# 3. Chatbot retry key: write-only, end to end (AC-804).                     #
# --------------------------------------------------------------------------- #


def _seed_turn(session, *, contact_respond_id: str) -> ChatbotTurn:
    turn = ChatbotTurn(
        id=str(uuid.uuid4()),
        contact_respond_id=contact_respond_id,
        message_id=f"ZZT-msg-{uuid.uuid4().hex[:8]}",
        ingress="webhook",
        envelope={
            "message": {"messageId": "ZZT-msg-1", "message": {"type": "text", "text": "hi"}},
            "contact": {"id": contact_respond_id},
        },
        status="failed",
        stage="understood",
        error="parser timed out",
        attempt=1,
        trace=[],
    )
    session.add(turn)
    session.commit()
    return turn


class TestChatbotRetryKeyWriteOnly:
    """`tests/chatbot/test_s8_retry_config.py::TestClearingTurnsRetryOff` already covers
    the omit-leaves-alone / explicit-blank-clears contract for `PUT .../chatbot-retry`
    thoroughly (both fields, both directions) - not repeated here. What is not covered
    there: the WIRE response body (not the dict-builder `to_response_dict` unit, which
    `test_s8_retry_config.py::TestModelAndServiceEncryption.
    test_response_dict_never_echoes_the_key_only_a_bool` already checks) and the retry
    endpoint's response/trace."""

    def test_get_response_body_never_contains_the_plaintext_key_anywhere(
        self, client, session, default_workspace
    ):
        svc = RespondWorkspaceService(session)
        svc.update(
            default_workspace.id,
            RespondWorkspaceUpdate(
                chatbot_retry_ingress_url=VALID_RETRY_URL,
                chatbot_retry_ingress_key=PLAIN_RETRY_KEY,
            ),
        )

        _GRANTS.add("system.respond_workspaces.view")
        resp = client.get(f"{WORKSPACES_BASE}/{default_workspace.id}")
        assert resp.status_code == 200, resp.text
        # Searched over the WHOLE raw body, not a parsed field: a masked-hint field this
        # test does not know the name of would still fail this the way a named-field
        # assertion could not.
        assert PLAIN_RETRY_KEY not in resp.text

    def test_retry_response_and_trace_never_contain_the_key(
        self, client, session, default_workspace, monkeypatch
    ):
        svc = RespondWorkspaceService(session)
        svc.update(
            default_workspace.id,
            RespondWorkspaceUpdate(
                chatbot_retry_ingress_url=VALID_RETRY_URL,
                chatbot_retry_ingress_key=PLAIN_RETRY_KEY,
            ),
        )
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda h, *a, **kw: _resolve_ok("198.51.100.7")
        )
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host")
        monkeypatch.setattr(
            httpx,
            "post",
            MagicMock(
                return_value=httpx.Response(200, request=httpx.Request("POST", VALID_RETRY_URL))
            ),
        )

        _GRANTS.add("system.chat_history.manage")
        turn = _seed_turn(session, contact_respond_id="ZZT-contact-s8a-key")
        resp = client.post(f"{TURNS_BASE}/{turn.id}/retry")
        assert resp.status_code == 200, resp.text
        assert PLAIN_RETRY_KEY not in resp.text

        row = session.query(ChatbotTurn).filter(ChatbotTurn.id == turn.id).first()
        assert PLAIN_RETRY_KEY not in json.dumps(row.trace or [])


# --------------------------------------------------------------------------- #
# 4. AC-807: the Prompts screen "Run a turn", and the wall around it.        #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def prompts_client(session_factory):
    def _override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: {"id": "u-zzt-s8a-807"}
    app.dependency_overrides[get_current_user_or_api_key] = lambda: {"id": "u-zzt-s8a-807"}
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def _ai_assistant_edit_permission(monkeypatch):
    # S8a review S3: the chatbot-key branch of the Prompts Test action ALSO requires
    # system.chat_history.view (it reads a caller-named contact's remembered state and
    # hands back the trace) - see tests/chatbot/test_s8_prompt_run_a_turn.py's
    # `test_the_chat_history_view_slug_is_required_as_well`. Both slugs are granted here
    # since these tests are about the version-key contract, not this permission gate.
    granted = {"system.ai_assistant_settings.edit", "system.chat_history.view"}
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in granted,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


@pytest.fixture()
def chatbot_engine_wired_to_test_db(monkeypatch, session_factory):
    """Same guard `test_s8_prompt_run_a_turn.py` established: whatever the route calls
    must use the patched `SessionLocal`, or a Test click writes a real turn into the
    shared dev database."""
    monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)


def _create_version(db, name: str, *, template: str = "S8A HARDENING TEST VERSION") -> dict:
    return AIPromptService(db).save_version(
        name, template=template, commit_message="ZZT S8a hardening test version", user_id=None
    )


class TestPromptOverridesAC807:
    def test_live_envelope_ignores_prompt_overrides_and_uses_the_production_version(
        self, session_factory, seeded, stub_access, monkeypatch
    ):
        """AC-807: a LIVE envelope (`is_test` false) carrying `prompt_overrides` in its
        extras must be answered from the PUBLISHED prompt - the harness key is a dry-run
        contract (`_prompt_override` returns None whenever `dry_run` is false) - so the
        trace's `prompt_version` fact must read the production label's version, never the
        override, however the envelope got the key.

        `resolve_config` itself is stubbed here (not `stub_parser`'s fixed version) so the
        override id ACTUALLY THREADS THROUGH to `override_version_id` if the engine ever
        stopped gating it - a fixed-return stub would pass this test for the wrong reason.
        """
        stub_access()
        captured: dict[str, Any] = {}

        def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
            captured["override_version_id"] = override_version_id
            # 1 = "the production label's version"; 999 = "the override, if it leaked
            # through" - deliberately far apart so a wrong value cannot be a typo.
            version = 999 if override_version_id else 1
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=version, provider="openai",
                model="gpt-test", api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())

        envelope = _envelope(prompt_overrides={parser_mod.PROMPT_KEY: "unpublished-version-zzt"})
        assert envelope.is_test is False
        assert envelope.dry_run is False

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert captured["override_version_id"] is None, (
            "a live envelope's prompt_overrides reached resolve_config's "
            "override_version_id - it must be dropped before the parser is asked"
        )
        trace = _turn_row(session_factory, result.turn_id).trace
        understood = next(r for r in trace if r["stage"] == "understood")
        assert understood["facts"]["prompt_version"] == 1, (
            "the trace's prompt_version fact must be the production label's version "
            f"(1), not the override (999): {understood['facts']}"
        )

    def test_dry_run_override_for_a_version_belonging_to_a_different_key_is_refused(
        self, prompts_client, session_factory, seeded, stub_access, chatbot_engine_wired_to_test_db,
        _ai_assistant_edit_permission,
    ):
        """A version created for `chatbot_clarifier` handed to the `chatbot_semantic_parser`
        Test button must 404, exactly as a nonexistent id does - `_test_chatbot_prompt_version`
        filters on `AIPromptVersion.name == name` (`app/api/v1/system/ai_assistant.py`), so a
        real id for the WRONG key finds no row either. This is the cross-key case
        specifically, distinct from `test_dormant_or_missing_version_still_404s`
        (`test_s8_prompt_run_a_turn.py`), which only tries a random UUID that matches no
        row at all."""
        db = session_factory()
        other_key_version = _create_version(db, "chatbot_clarifier")
        db.commit()

        resp = prompts_client.post(
            PROMPT_TEST_URL.format(name="chatbot_semantic_parser"),
            json={
                "message": "price for SRTWC8517",
                "version_id": other_key_version["id"],
                "contact_respond_id": str(CONTACT_ID),
            },
        )
        assert resp.status_code == 404, resp.text

    def test_a_test_click_leaves_every_table_except_chatbot_turns_unchanged(
        self, session_factory, seeded, stub_access, chatbot_engine_wired_to_test_db, monkeypatch,
    ):
        """D14, counted rather than spot-checked: `ai_prompt_versions` (the version being
        tested must not be mutated or duplicated), `integration_log` (a Test click is not
        an outbound integration call) and `respond_contacts` (row count AND the seeded
        contact's `session_vars`) are all unchanged; `chatbot.turns` gains exactly one row.
        There is no local ideation table to count (ideation is an MCP call to the shared
        service, not a row in this database - confirmed against `information_schema.tables`
        before writing this test, not assumed).

        Calls `run_prompt_dry_run_turn` directly rather than through the HTTP route
        (`test_dry_run_override_for_a_version_belonging_to_a_different_key_is_refused`
        above covers the route's own 404 gate). **Measured, not assumed**: FastAPI/Starlette
        runs a sync `def` route via a threadpool worker thread, and re-querying this
        fixture's savepoint-bound session from the ORIGINAL test thread afterwards showed
        zero rows in EVERY table including `chatbot.turns` (both a raw
        `SELECT COUNT(*) FROM turns` and the ORM query, checked directly before writing this
        test) even though the HTTP response itself carried the fully-populated turn - a
        cross-thread visibility gap in this fixture's connection-sharing, not in
        `app/api/v1/external/chat.py`. Calling the same function in-thread is what the two
        `engine_mod.run_turn(...)` tests elsewhere in this file already do successfully.
        """
        db = session_factory()
        version = _create_version(db, "chatbot_semantic_parser")
        db.commit()

        def fake_resolve_config(dbx, *, current_date, override_version_id=None, grants=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai",
                model="gpt-test", api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
        stub_access()

        before = session_factory()
        prompt_versions_before = before.query(AIPromptVersion).count()
        integration_log_before = before.execute(
            text("SELECT COUNT(*) FROM integration_log")
        ).scalar()
        contacts_before = before.execute(text("SELECT COUNT(*) FROM respond_contacts")).scalar()
        session_vars_before = before.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        turns_before = before.query(ChatbotTurn).count()

        from app.api.v1.external.chat import run_prompt_dry_run_turn

        result = run_prompt_dry_run_turn(
            prompt_key="chatbot_semantic_parser",
            version_id=version["id"],
            message="price for SRTWC8517",
            contact_respond_id=str(CONTACT_ID),
        )
        assert result["status"] in ("done", "delegated"), result

        after = session_factory()
        assert after.query(AIPromptVersion).count() == prompt_versions_before
        assert (
            after.execute(text("SELECT COUNT(*) FROM integration_log")).scalar()
            == integration_log_before
        )
        assert (
            after.execute(text("SELECT COUNT(*) FROM respond_contacts")).scalar()
            == contacts_before
        )
        assert (
            after.execute(
                text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
                {"c": str(CONTACT_ID)},
            ).scalar()
            == session_vars_before
        )
        assert after.query(ChatbotTurn).count() == turns_before + 1


# --------------------------------------------------------------------------- #
# 5. `_assert_emission`'s container-type check, and a real broaden_axis-absent#
#    capture replayed (AC-806-adjacent hardening on the head's post-processor).#
# --------------------------------------------------------------------------- #


# AC-1592: `TestPostProcessEmissionValidation` is REMOVED here, split two ways.
#
# `test_a_real_capture_with_no_broaden_axis_key_replays_cleanly` replayed ONE real
# `runData` capture through `output_exchange`'s own post-processor - exactly what
# `test_turn_replay.py` (S6 deliverable 1-2, `f38ae3f52`) now does for the whole
# corpus end to end through `engine.run_turn`. RETIRED, superseded (contract line 98
# territory, same rule as the `test_replay.py` family's doomed node runners above).
#
# `test_a_right_key_wrong_container_type_fails_naming_key_and_type` asserted
# `_assert_emission`'s own per-key type loop (`ParserOutputError`, named key + type
# in the message). Grepped this session: neither `_assert_emission` nor
# `ParserOutputError` exists anywhere in `app/services/chatbot/` - the S3 rewrite
# passes `json_schema=PARSE_OUTPUT_JSON_SCHEMA` straight to the LLM provider
# (`head/parser.py::parse`), so a REAL call cannot return a malformed container type
# at all (the schema is enforced server-side). Probed `turn/apply.py::apply()`
# directly with a malformed `entities` dict this session: an EMPTY dict degrades
# silently to `[]` (`verdict.get("entities") or []` - `{}` is falsy) with no error at
# all; a NON-empty malformed dict raises `AttributeError: 'str' object has no
# attribute 'get'` - unclear, unnamed, but still a loud failure, not a silent wrong
# answer. Ported below as RED (the clear-message contract is gone, not fully
# replaced) - low severity relative to the other findings this triage pass surfaced
# (only reachable via a test-harness-injected malformed mock, not a real LLM
# response), flagged rather than escalated.
class TestMalformedEntitiesContainerNoLongerNamesKeyAndType:
    def test_a_non_empty_malformed_entities_dict_fails_with_a_named_message(self) -> None:
        from tests.chatbot._turn_helpers import build_policy, verdict
        from app.services.chatbot.turn.apply import apply
        from app.services.chatbot.turn.state import Focus, Profile, State

        state = State(focus=Focus(), pending=None, profile=Profile())
        v = verdict(entities={"hint": "product"})

        with pytest.raises(Exception) as excinfo:
            apply(state, v, build_policy())

        message = str(excinfo.value)
        assert "entities" in message and (
            "array" in message or "list" in message
        ), (
            "the old `_assert_emission` guard named the bad KEY and the expected TYPE "
            f"('entities' ... 'array' ... 'dict'); the new failure is unnamed - got "
            f"{type(excinfo.value).__name__}: {excinfo.value!r}. Neither "
            "_assert_emission nor ParserOutputError exists anywhere in "
            "app/services/chatbot/ (grepped this session); head/parser.py now relies "
            "on the LLM provider's own json_schema enforcement instead, which cannot "
            "catch a test-harness-injected malformed mock the way a real LLM call "
            "would never produce one."
        )


# --------------------------------------------------------------------------- #
# 6. Duplicate path: a TEST envelope does NOT duplicate a LIVE message (D15). #
# --------------------------------------------------------------------------- #


class TestATestEnvelopeIsNeverADuplicateOfALiveTurn:
    """**Rewritten by H57 (dry-run isolation PR).**

    This class used to assert the opposite: that a TEST envelope for a message that had
    already run LIVE came back `duplicate: true` carrying the live row's `is_test: false`
    and its live-flagged actions. That followed from `_existing_turn` matching on
    `(contact, message_id)` alone, and it was the SAME defect the audit found from the
    other side - a test row shadowing a live delivery, which answered a real customer with
    a canned test reply and `duplicate: true`, i.e. with silence
    (`test_dry_run_isolation.py::TestD15DedupRespectsIsTest`). One lookup cannot be
    world-aware in one direction only, so the fix narrows both: the dedup question is now
    "has this message already been turned into a turn IN THIS WORLD".

    What that buys, beyond the defect: replaying a real customer's message from the
    Prompts screen's Test button is the whole point of that button, and until now it
    answered `duplicate: true` and ran nothing at all for any message the bot had already
    handled - which is every message worth testing against.
    """

    def test_a_test_envelope_for_an_already_live_message_runs_its_own_turn(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        stub_parser()
        stub_access()

        live_envelope = _envelope()
        assert live_envelope.is_test is False
        first = engine_mod.run_turn(live_envelope, session_factory=session_factory)
        assert first.duplicate is False
        assert first.is_test is False

        test_envelope = _envelope(is_test=True)
        second = engine_mod.run_turn(test_envelope, session_factory=session_factory)

        assert second.duplicate is False, (
            "a TEST envelope must not be answered from a LIVE row: the two are different "
            "turns and the dedup lookup is narrowed to the envelope's own is_test (H57)"
        )
        assert second.turn_id != first.turn_id
        assert second.is_test is True

    def test_a_second_test_delivery_of_one_message_still_dedups(
        self, session_factory, seeded, stub_parser, stub_access
    ):
        """D15 is not weakened, only scoped: WITHIN one world the second delivery of a
        message is still a duplicate and still runs nothing."""
        stub_parser()
        stub_access()

        first = engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
        second = engine_mod.run_turn(
            _envelope(is_test=True, ingress="poller"), session_factory=session_factory
        )

        assert second.duplicate is True
        assert second.turn_id == first.turn_id
        assert second.is_test is True
