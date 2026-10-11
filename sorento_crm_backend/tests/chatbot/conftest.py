"""Shared fixtures for the engine tests: a blank Postgres schema and a stubbed parser.

Postgres only, via `tests/_pg_fixture.py`. The engine's own tests run against the BLANK
schema rather than the shared database because they count rows to prove the dry run wrote
nothing (D14, AC-702's shape), and a shared prod-copy database makes "nothing was written"
unprovable.

The parser is stubbed at the same seam `tests/test_ideation_turn.py` uses for the ideation
extractor: the function that makes the provider call. No test in this suite reaches an LLM,
n8n or respond.io.
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator

import pytest

from app.models.user import SystemSetting
from tests._pg_fixture import blank_schema_engine


@pytest.fixture()
def session_factory() -> Iterator[Any]:
    """A factory of independent sessions over ONE blank schema, all discarded at teardown.

    The engine opens and closes several sessions per turn on purpose (it must not hold one
    across the LLM call), so a single fixture session would not exercise the real shape.
    Every session shares one connection inside one outer transaction, which is rolled back
    here, so nothing survives the test.
    """
    from sqlalchemy.orm import Session

    connection = blank_schema_engine().connect()
    transaction = connection.begin()
    from tests import _pg_fixture

    name = _pg_fixture._BLANK["name"]
    connection.exec_driver_sql(
        f'SET LOCAL search_path TO "{name}", "{name}_scm", "{name}_dealer_kit", '
        f'"{name}_chatbot", "{name}_projects", "public"'
    )
    opened: list[Session] = []

    def factory() -> Session:
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        opened.append(session)
        return session

    factory.opened = opened  # type: ignore[attr-defined]
    try:
        yield factory
    finally:
        for session in opened:
            with contextlib.suppress(Exception):
                session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def counting_session_factory(session_factory):
    """The same factory, but it tracks how many sessions are OPEN right now.

    This is what makes "never hold a DB session across the LLM call" testable through the
    real seam rather than through a production hook: the stubbed parser asserts the count
    is zero while it runs.
    """
    from sqlalchemy.orm import Session

    state = {"open": 0}

    def factory() -> Session:
        session = session_factory()
        state["open"] += 1
        original_close = session.close

        def close() -> None:
            if not getattr(session, "_counted_closed", False):
                session._counted_closed = True
                state["open"] -= 1
            original_close()

        session.close = close  # type: ignore[method-assign]
        return session

    factory.state = state  # type: ignore[attr-defined]
    return factory


@pytest.fixture()
def system_settings_row(session_factory):
    """The singleton `system_settings` row, so the R1 flag can be flipped in a test."""
    db = session_factory()
    row = SystemSetting()
    db.add(row)
    db.commit()
    return row


def set_chatbot_switches(
    session_factory: Any,
    *,
    business_lane: bool | None = None,
    ordering: bool | None = None,
) -> None:
    """Set the two chatbot switches on the `system_settings` singleton (AC-810).

    They were `app.config.settings` flags until S8, and every test that wanted one on did
    `monkeypatch.setattr(settings, "chatbot_ordering_enabled", True)`. They are columns
    now, read per turn, so the ROW is the only lever and this is the one place that pulls
    it. Creates the singleton when the test has not seeded one, so a caller does not have
    to also depend on `system_settings_row`; `None` leaves a switch alone.
    """
    db = session_factory()
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
    if business_lane is not None:
        row.chatbot_business_lane_enabled = business_lane
    if ordering is not None:
        row.chatbot_ordering_enabled = ordering
    db.commit()


def validating_resolve_entity(fake: Any) -> Any:
    """Wrap a `resolve_entity` stub so the lane-built body still meets the ROUTE's
    own schema (`ResolveReferenceRequest`).

    Why this exists: in production the lane's `resolve_entity` seam IS that route
    function (`app/services/chatbot/lanes/business/services.py::_resolve_entity`),
    so the body is validated on every real turn. Every stub here replaced the seam
    with a bare lambda, so the schema never ran over a lane-built body and #874's
    integer `contact_id` reached production green (every `business_query` turn then
    died on `contact_id  Input should be a valid string ... input_type=int`).

    `fake` is either a callable taking the body, or a constant to return as is.
    """

    def _resolve_entity(body: dict[str, Any]) -> Any:
        from app.api.v1.system.references import ResolveReferenceRequest

        ResolveReferenceRequest(**body)
        return fake(body) if callable(fake) else fake

    return _resolve_entity


@pytest.fixture(autouse=True)
def _no_real_mcp_calls(monkeypatch):
    """No test under `tests/chatbot/` may reach the machine-wide MCP server on :8765.

    Measured (coordinator ruling, 16 Sep 2026): `test_rearch_s3_attribute_first.py`'s
    two `product_attachment` tests never stubbed `MCPRuntimeClient.call_tool`, so
    `_probe`/`services.py`'s `call_tool` seam reached the REAL local MCP server, which
    reads whatever database that process happens to be pointed at - NOT this worktree's
    private test DB. The qualifying COUNT in the reply was always right (it comes from
    `resolve_product_set`'s own direct SQL, never MCP), but the rendered page was
    silently empty ("Showing 0") because the real server's answer for a `ZZT-*` test
    code is genuinely nothing - a defect that reads as an engine bug and is actually a
    missing test stub.

    Autouse, so a NEW test in this tree gets the guard for free rather than having to
    remember it: `call_tool` raises unless the test itself monkeypatches it back (test
    bodies patch it AFTER this fixture runs, so their own `monkeypatch.setattr` wins -
    pytest fixtures execute before the test function).
    """
    from app.services.ai_assistant_service import MCPRuntimeClient

    def _forbidden(self, name: str, arguments: dict[str, Any]) -> str:
        raise AssertionError(
            f"tests/chatbot/ may never call the real MCP server (:8765) - tool={name!r} "
            f"arguments={arguments!r} was not stubbed. Monkeypatch "
            "app.services.ai_assistant_service.MCPRuntimeClient.call_tool in the test."
        )

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", _forbidden)


@pytest.fixture(autouse=True)
def _stub_casual_llm(request, monkeypatch):
    """T3 (coordinator ruling, 16 Sep 2026): no test under `tests/chatbot/` may reach a
    real LLM provider through the `low_signal` lane's clarifier.

    Measured root cause of the largest single cluster in the 16 Sep full-suite triage
    (`documentation/plans/chatbot/evidence/turn-rearch/full-suite-triage-16sep.md`,
    ~78 occurrences): `lanes.casual.resolve_clarifier_config` raises `ClarifierError(
    "no API key configured for provider ...")` in every test environment (the private
    test DB starts with no `ai_assistant_config` row), which `engine.py`'s own setup-
    error branch turns into the SAME generic `CLARIFIER_UNAVAILABLE_REPLY` ("Sorry, I
    can't reply to that right now...") a real provider outage would produce - 42 of
    those in `test_worlds.py` alone (`... failed at casual_llm: None`), the rest as
    that literal sentence surfacing where `test_outstanding_lane.py`,
    `test_pass5_item2_member_offer_business_query_filter_route.py` and the replay gate
    (`test_turn_replay.py`) expected a real reply.

    Stubs BOTH halves of the call - config resolution AND the provider round trip
    itself, which `resolve_clarifier_config`'s own docstring treats as one logical
    call split only so the DB session can close before the provider I/O - with a
    deterministic fake that never touches a real provider or the AI-assistant config
    table. Returns a PLAIN STRING with no `{` in it (`central_exchange`'s own fourth
    arm: no brace anywhere returns the raw string unchanged), so `reply_text` hands it
    straight back as the reply text - a real, inspectable value a test can assert on,
    not a placeholder that happens to render blank.

    A test that means to exercise the clarifier's OWN setup or call failure paths (the
    unavailable-reply sentence, `ClarifierError` handling) opts out with
    `@pytest.mark.real_casual_llm` - it still reaches no real network (nothing else in
    this fixture changes), it just does not get the deterministic success stub.
    """
    if request.node.get_closest_marker("real_casual_llm") is not None:
        yield None
        return

    from app.services.chatbot.lanes import casual as casual_mod

    calls: list[dict[str, Any]] = []

    def _fake_resolve_clarifier_config(db, *, override_version_id=None):
        return casual_mod.ClarifierConfig(
            system_prompt="ZZT stub clarifier system prompt",
            prompt_version=None,
            provider="zzt-stub",
            model="zzt-stub-model",
            api_key="ZZT-stub-api-key",
        )

    def _fake_call_clarifier(config, user_prompt):
        calls.append({"config": config, "user_prompt": user_prompt})
        return "ZZT stubbed casual reply."

    monkeypatch.setattr(casual_mod, "resolve_clarifier_config", _fake_resolve_clarifier_config)
    monkeypatch.setattr(casual_mod, "call_clarifier", _fake_call_clarifier)
    yield calls
