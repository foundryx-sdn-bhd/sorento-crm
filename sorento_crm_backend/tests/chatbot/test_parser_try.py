"""SPEC-REGISTRY-UI Phase 2 red tests: `POST /api/v1/system/chatbot/parser-try` (contract
C5, UAC AC-5.2 to AC-5.4).

The route runs the REAL parser path with the provider stubbed at the three module seams
the contract names (`parser.resolve_config`, `parser.build_user_block`, `parser.parse`),
turns each emission entity into a pick, and writes nothing.

Red until the route exists: every request below answers 404 today (route not mounted),
which is the right reason. A fixture bug would fail some other way (import error, 500).

Fixture shape copied from `test_turns_admin_api.py`: blank Postgres schema through
`session_factory`, permission lookup monkeypatched on `UserPermissionService`.
"""
from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.audit import AuditLog
from app.models.chatbot_turn import ChatbotTurn
from app.models.product_spec import ProductSpecRegistry
from app.services.chatbot.head import parser as parser_mod
from app.services.user_service import UserPermissionService

URL = "/api/v1/system/chatbot/parser-try"
FIXED_MESSAGE = "The chatbot could not read that message right now."
EDIT = "master_data.spec_registry.edit"

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Parser Try Tester"}


@pytest.fixture()
def db(session_factory):
    return session_factory()


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.add(EDIT)
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


def _flush_limit() -> None:
    from app.services.queue_service import redis_conn

    for key in redis_conn.scan_iter(f"rate_limit:v1:parser_try:{_ACTOR['id']}*"):
        redis_conn.delete(key)


@pytest.fixture()
def client(db):
    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    _ACTOR["id"] = str(uuid.uuid4())
    _flush_limit()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()
        _flush_limit()


def _entity(**kwargs: Any) -> dict:
    base = {
        "raw": "",
        "hint": "specification",
        "canonical_code": None,
        "current_message": True,
        "confident": True,
        "hint_confident": True,
        "quantity": None,
        "spec_key": None,
        "spec_value": None,
        "account": None,
    }
    base.update(kwargs)
    return base


def _registry_row(db, spec_key: str, **kwargs) -> ProductSpecRegistry:
    row = ProductSpecRegistry(
        spec_key=spec_key,
        label=kwargs.pop("label", spec_key),
        data_type=kwargs.pop("data_type", "numeric"),
        unit=kwargs.pop("unit", "mm"),
        allowed_values=kwargs.pop("allowed_values", []),
        synonyms={},
        user_synonyms={},
        user_values=[],
        applies_when={},
        is_active=True,
        source="user",
        **kwargs,
    )
    db.add(row)
    db.commit()
    return row


class _Seams:
    """The three stubbed parser seams, recording what the route handed them."""

    def __init__(self, entities: list[dict], prompt_version: int | None = 63) -> None:
        self.entities = entities
        self.prompt_version = prompt_version
        self.resolve_calls: list[dict] = []
        self.block_calls: list[dict] = []
        self.parse_calls: list[tuple] = []

    def install(self, monkeypatch) -> "_Seams":
        def resolve_config(db, *, current_date, override_version_id=None):
            self.resolve_calls.append({"current_date": current_date})
            return SimpleNamespace(prompt_version=self.prompt_version)

        def build_user_block(**kwargs):
            self.block_calls.append(kwargs)
            return "USER BLOCK"

        def parse(config, user_block):
            self.parse_calls.append((config, user_block))
            return {"entities": self.entities}

        monkeypatch.setattr(parser_mod, "resolve_config", resolve_config)
        monkeypatch.setattr(parser_mod, "build_user_block", build_user_block)
        monkeypatch.setattr(parser_mod, "parse", parse)
        return self


# --------------------------------------------------------------------------- #
# Picks
# --------------------------------------------------------------------------- #
def test_a_numeric_spec_entity_picks_the_row_label_and_value_with_unit(client, db, monkeypatch):
    _registry_row(db, "zzt_trap_length", label="Trap outlet length", data_type="numeric", unit="mm")
    _Seams([_entity(raw="300mm", spec_key="zzt_trap_length", spec_value=300)]).install(monkeypatch)

    response = client.post(URL, json={"text": "basin trap 300mm"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["picks"] == [
        {
            "raw": "300mm",
            "kind": "specification",
            "field_label": "Trap outlet length",
            "value": "300 mm",
        }
    ]


def test_an_enum_spec_entity_shows_the_value_label_else_the_display_text(client, db, monkeypatch):
    _registry_row(
        db,
        "zzt_material",
        label="Material",
        data_type="enum",
        unit=None,
        allowed_values=["pp", "brushed_brass"],
        value_labels={"pp": "PP polymer"},
    )
    _Seams(
        [
            _entity(raw="pp", spec_key="zzt_material", spec_value="pp"),
            _entity(raw="brass", spec_key="zzt_material", spec_value="brushed_brass"),
        ]
    ).install(monkeypatch)

    response = client.post(URL, json={"text": "pp or brass"})

    assert response.status_code == 200, response.text
    picks = response.json()["picks"]
    assert [p["value"] for p in picks] == ["PP polymer", "Brushed brass"]
    assert all(p["field_label"] == "Material" and p["kind"] == "specification" for p in picks)


def test_an_unknown_spec_key_falls_back_to_the_key_as_the_label(client, monkeypatch):
    _Seams([_entity(raw="x", spec_key="zzt_no_such_row", spec_value="x")]).install(monkeypatch)

    response = client.post(URL, json={"text": "x"})

    assert response.status_code == 200, response.text
    assert response.json()["picks"][0]["field_label"] == "zzt_no_such_row"
    assert response.json()["picks"][0]["kind"] == "specification"


def test_a_non_spec_entity_is_a_hint_pick_with_no_field_label(client, monkeypatch):
    _Seams(
        [
            _entity(raw="the 5727", hint="product", canonical_code="MWT5727SS-CR"),
            _entity(raw="test report", hint="attachment_type", listed_item="Test report"),
            _entity(raw="wall hung", hint="category"),
        ]
    ).install(monkeypatch)

    response = client.post(URL, json={"text": "the 5727 test report wall hung"})

    assert response.status_code == 200, response.text
    assert response.json()["picks"] == [
        {"raw": "the 5727", "kind": "product", "field_label": None, "value": "MWT5727SS-CR"},
        {"raw": "test report", "kind": "attachment_type", "field_label": None, "value": "Test report"},
        {"raw": "wall hung", "kind": "category", "field_label": None, "value": "wall hung"},
    ]


def test_the_response_carries_the_prompt_version(client, monkeypatch):
    _Seams([], prompt_version=63).install(monkeypatch)

    body = client.post(URL, json={"text": "hello"}).json()

    assert body["prompt_version"] == 63
    assert body["picks"] == []


def test_the_route_hands_the_seams_the_contracted_arguments(client, monkeypatch):
    seams = _Seams([]).install(monkeypatch)

    response = client.post(URL, json={"text": "  300mm trap  "})

    assert response.status_code == 200, response.text
    assert seams.resolve_calls == [{"current_date": date.today().isoformat()}]
    assert seams.block_calls == [
        {"previous_response": None, "latest_user_message": "300mm trap", "pending_kind": None}
    ]
    assert len(seams.parse_calls) == 1
    assert seams.parse_calls[0][1] == "USER BLOCK"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
def test_a_parser_error_from_parse_is_a_422_with_a_readable_code(client, monkeypatch):
    _Seams([]).install(monkeypatch)

    def boom(config, user_block):
        raise parser_mod.ParserError("parser provider call failed: timeout")

    monkeypatch.setattr(parser_mod, "parse", boom)

    response = client.post(URL, json={"text": "300mm"})

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "parser_try_unavailable"
    assert body["message"] == FIXED_MESSAGE
    assert "timeout" not in response.text
    assert body["details"] == {"reason": "provider_error"}


def test_a_parser_error_from_resolve_config_is_the_same_422(client, monkeypatch):
    _Seams([]).install(monkeypatch)

    def no_key(db, *, current_date, override_version_id=None):
        raise parser_mod.ParserError("no API key configured for provider 'openai'")

    monkeypatch.setattr(parser_mod, "resolve_config", no_key)

    response = client.post(URL, json={"text": "300mm"})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "parser_try_unavailable"


def test_any_other_exception_from_parse_is_also_parser_try_unavailable(client, monkeypatch):
    _Seams([]).install(monkeypatch)

    def boom(config, user_block):
        raise RuntimeError("socket closed")

    monkeypatch.setattr(parser_mod, "parse", boom)

    response = client.post(URL, json={"text": "300mm"})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "parser_try_unavailable"


@pytest.mark.parametrize("text", ["", "     ", "\n\t"])
def test_empty_text_is_refused_422_without_calling_the_parser(client, monkeypatch, text):
    seams = _Seams([]).install(monkeypatch)

    response = client.post(URL, json={"text": text})

    assert response.status_code == 422, response.text
    assert seams.parse_calls == [] and seams.resolve_calls == []


def test_text_over_500_characters_is_refused_422(client, monkeypatch):
    seams = _Seams([]).install(monkeypatch)

    response = client.post(URL, json={"text": "x" * 501})

    assert response.status_code == 422, response.text
    assert seams.parse_calls == []


def test_text_of_exactly_500_characters_is_accepted(client, monkeypatch):
    _Seams([]).install(monkeypatch)

    response = client.post(URL, json={"text": "x" * 500})

    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# Permission and zero writes
# --------------------------------------------------------------------------- #
def test_a_caller_without_spec_registry_edit_is_refused_403(client, monkeypatch):
    seams = _Seams([]).install(monkeypatch)
    _GRANTS.clear()
    # view alone is no longer enough: only editors test their edits
    _GRANTS.add("master_data.spec_registry.view")

    response = client.post(URL, json={"text": "300mm"})

    assert response.status_code == 403, response.text
    assert seams.parse_calls == []


def test_a_successful_try_writes_no_turn_and_no_audit_row(client, db, monkeypatch):
    _registry_row(db, "zzt_trap_length", label="Trap outlet length")
    _Seams([_entity(raw="300mm", spec_key="zzt_trap_length", spec_value=300)]).install(monkeypatch)
    turns_before = db.query(ChatbotTurn).count()
    audits_before = db.query(AuditLog).count()

    response = client.post(URL, json={"text": "300mm"})

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.query(ChatbotTurn).count() == turns_before
    assert db.query(AuditLog).count() == audits_before


def test_a_failed_try_writes_no_turn_and_no_audit_row_either(client, db, monkeypatch):
    _Seams([]).install(monkeypatch)

    def boom(config, user_block):
        raise parser_mod.ParserError("parser returned non-JSON content")

    monkeypatch.setattr(parser_mod, "parse", boom)
    turns_before = db.query(ChatbotTurn).count()
    audits_before = db.query(AuditLog).count()

    response = client.post(URL, json={"text": "300mm"})

    assert response.status_code == 422, response.text
    db.expire_all()
    assert db.query(ChatbotTurn).count() == turns_before
    assert db.query(AuditLog).count() == audits_before


# --------------------------------------------------------------------------- #
# Fix round 2: fixed error text, reasons, rate limit, connection release
# --------------------------------------------------------------------------- #
def test_no_internal_text_leaks_and_the_reason_is_not_configured(client, monkeypatch):
    _Seams([]).install(monkeypatch)

    def no_key(db, *, current_date, override_version_id=None):
        raise parser_mod.ParserError("no API key configured for provider 'openai' sk-secret")

    monkeypatch.setattr(parser_mod, "resolve_config", no_key)

    response = client.post(URL, json={"text": "300mm"})

    assert response.status_code == 422
    body = response.json()
    assert body["message"] == FIXED_MESSAGE
    assert body["details"] == {"reason": "not_configured"}
    assert "sk-secret" not in response.text and "openai" not in response.text


def test_a_rate_limited_provider_is_reported_as_rate_limited(client, monkeypatch):
    _Seams([]).install(monkeypatch)

    def boom(config, user_block):
        raise parser_mod.ParserError("provider said 429 internal", rate_limited=True)

    monkeypatch.setattr(parser_mod, "parse", boom)

    body = client.post(URL, json={"text": "300mm"}).json()

    assert body["details"] == {"reason": "rate_limited"}
    assert "internal" not in body["message"]


def test_an_unexpected_exception_is_provider_error_without_its_text(client, monkeypatch):
    _Seams([]).install(monkeypatch)

    def boom(config, user_block):
        raise RuntimeError("socket closed to 10.0.0.5")

    monkeypatch.setattr(parser_mod, "parse", boom)

    response = client.post(URL, json={"text": "300mm"})

    assert response.json()["details"] == {"reason": "provider_error"}
    assert "10.0.0.5" not in response.text


def test_the_eleventh_try_in_a_minute_is_429(client, monkeypatch):
    _Seams([]).install(monkeypatch)

    for _ in range(10):
        assert client.post(URL, json={"text": "300mm"}).status_code == 200
    response = client.post(URL, json={"text": "300mm"})

    assert response.status_code == 429, response.text
    body = response.json()
    assert body["code"] == "parser_try_rate_limited"
    assert body["message"] == "Too many tries, wait a minute."


def test_the_limit_is_per_user(client, monkeypatch):
    _Seams([]).install(monkeypatch)
    for _ in range(11):
        client.post(URL, json={"text": "300mm"})
    _ACTOR["id"] = str(uuid.uuid4())

    assert client.post(URL, json={"text": "300mm"}).status_code == 200


def test_redis_down_fails_open(client, monkeypatch):
    from app.services import rate_limit

    _Seams([]).install(monkeypatch)
    monkeypatch.setattr(rate_limit, "_redis_conn", lambda: None)

    for _ in range(12):
        assert client.post(URL, json={"text": "300mm"}).status_code == 200


def test_the_db_connection_is_released_before_the_provider_call(client, db, monkeypatch):
    seen: dict = {}
    seams = _Seams([_entity(raw="x", hint="category")])
    seams.install(monkeypatch)

    def parse(config, user_block):
        seen["in_transaction"] = db.in_transaction()
        return {"entities": seams.entities}

    monkeypatch.setattr(parser_mod, "parse", parse)

    assert client.post(URL, json={"text": "x"}).status_code == 200
    assert seen["in_transaction"] is False
