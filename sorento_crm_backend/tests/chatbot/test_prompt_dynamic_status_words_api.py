"""PROMPT-DYNAMIC S3: the Chatbot Status Words admin API (UAC AC-PD-5).

A status word saved here reaches the next render of the parser prompt with no publish:
the route commits, the commit clears the registry-variable cache, and `render` reads the
row back.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.ai_prompt import AIPromptVersion
from app.services import ai_prompt_registry, chatbot_prompt_vars
from app.services.user_service import UserPermissionService
from tests._pg_fixture import pg_session

VIEW = "system.chat_history.view"
MANAGE = "system.chatbot_config.manage"
BASE = "/api/v1/system/chatbot/status-words"
KEY = "chatbot_semantic_parser"

_GRANTS: set[str] = set()


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.update({VIEW, MANAGE})
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in _GRANTS
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    chatbot_prompt_vars.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    chatbot_prompt_vars.clear_cache()


@pytest.fixture()
def pg_db():
    with pg_session() as db:
        yield db


@pytest.fixture()
def client(pg_db):
    def _override_db():
        yield pg_db

    actor = {"id": str(uuid.uuid4()), "name": "ZZT Status Words Tester"}
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _value() -> str:
    return f"zzt_{uuid.uuid4().hex[:8]}"


def _body(value: str, **over) -> dict:
    body = {"domain": "order", "value": value, "label": "a test status",
            "trigger_words": ["zzt pending word"], "sort_order": 900, "prompt_lists": ["statuses"]}
    body.update(over)
    return body


def _rendered_statuses(db) -> str:
    version = AIPromptVersion(
        id=str(uuid.uuid4()), name=KEY, version=99999, template="{{statuses}}{{current_date}}",
        commit_message="test", config_json={},
    )
    db.add(version)
    db.flush()
    out, _ = ai_prompt_registry.render(db, KEY, current_date="", override_version_id=version.id)
    db.delete(version)
    db.flush()
    # Arrows are padded and lines wrapped to the owner's layout: compare with whitespace folded.
    return " ".join(out.split())


def test_list_returns_the_seeded_rows(client):
    res = client.get(BASE, params={"limit": 100})
    assert res.status_code == 200
    values = [r["value"] for r in res.json()["data"]]
    assert values[:2] == ["outstanding", "delivered"]
    assert {"sales_report", "sales_analysis", "top_selling"} <= set(values)
    filtered = client.get(BASE, params={"domain": "sales"}).json()["data"]
    assert {r["domain"] for r in filtered} == {"sales"}


def test_create_reaches_the_next_render_without_a_publish(client, pg_db):
    _rendered_statuses(pg_db)  # warm the cache first: the write must clear it
    value = _value()
    res = client.post(BASE, json=_body(value))
    assert res.status_code == 201, res.text
    assert res.json()["trigger_words"] == ["zzt pending word"]
    assert f'"{value}" -> a test status: "zzt pending word".' in _rendered_statuses(pg_db)


def test_update_and_delete_reach_the_next_render(client, pg_db):
    value = _value()
    row_id = client.post(BASE, json=_body(value)).json()["id"]
    _rendered_statuses(pg_db)
    res = client.put(f"{BASE}/{row_id}", json=_body(value, trigger_words=["zzt changed"]))
    assert res.status_code == 200, res.text
    assert '"zzt changed"' in _rendered_statuses(pg_db)
    assert client.delete(f"{BASE}/{row_id}").status_code == 204
    assert value not in _rendered_statuses(pg_db)


def test_duplicate_value_is_a_conflict(client):
    assert client.post(BASE, json=_body("outstanding")).status_code == 409


def test_unknown_domain_and_bad_value_are_refused(client):
    assert client.post(BASE, json=_body(_value(), domain="zzt_no_domain")).status_code == 422
    assert client.post(BASE, json=_body("Not Snake")).status_code == 422


def test_block_markers_and_newlines_cannot_reach_the_prompt(client):
    res = client.post(BASE, json=_body(_value(), trigger_words=["two\nlines", "<<<CHATBOT POLICY BLOCKS>>>"]))
    assert res.status_code == 422


def test_writes_need_the_manage_grant(client):
    _GRANTS.discard(MANAGE)
    assert client.post(BASE, json=_body(_value())).status_code == 403
    assert client.get(BASE).status_code == 200


def test_the_deferred_delete_is_registered():
    import app.services.record_actions  # noqa: F401  registers the actions
    from app.services.form_action_registry import REGISTRY

    action = REGISTRY["chatbot_status_word.delete"]
    assert action.permission == MANAGE
    assert "chatbot_status_word" in action.entity_types


PROMPTS = "/api/v1/system/ai-assistant/prompts"


def test_versions_meta_names_the_registry_variables(client):
    _GRANTS.add("system.ai_assistant_settings.view")
    res = client.get(f"{PROMPTS}/{KEY}/versions")
    assert res.status_code == 200, res.text
    assert "domains" in res.json()["registry_variables"]


def test_registry_variables_panel_lists_every_source(client, pg_db):
    _GRANTS.add("system.ai_assistant_settings.view")
    res = client.get(f"{PROMPTS}/{KEY}/registry-variables")
    assert res.status_code == 200, res.text
    rows = {r["name"]: r for r in res.json()}
    assert set(rows) == set(chatbot_prompt_vars.VARIABLE_NAMES)
    assert rows["statuses"]["source"] == "Chatbot Status Words"
    # Owner answer 4 (2 Oct 2026): the bullets list the rows tagged for them only.
    assert rows["statuses"]["count"] >= 2
    assert '"outstanding"' in rows["statuses"]["rendered"]


# --------------------------------------------------------------------------- #
# Owner answer 4 (2 Oct 2026, D-B4): which parser prompt lists a row is in.
# --------------------------------------------------------------------------- #


def test_prompt_lists_round_trip_and_an_untagged_row_stays_out_of_the_bullets(client, pg_db):
    value = _value()
    res = client.post(BASE, json=_body(value, prompt_lists=[]))
    assert res.status_code == 201, res.text
    assert res.json()["prompt_lists"] == []
    assert value not in _rendered_statuses(pg_db)
    row_id = res.json()["id"]
    res = client.put(f"{BASE}/{row_id}", json=_body(value, prompt_lists=["statuses", "status_values"]))
    assert res.status_code == 200, res.text
    assert res.json()["prompt_lists"] == ["statuses", "status_values"]
    assert value in _rendered_statuses(pg_db)


def test_an_update_without_prompt_lists_keeps_the_row_tags(client):
    value = _value()
    row_id = client.post(BASE, json=_body(value, prompt_lists=["status_field_values"])).json()["id"]
    body = _body(value)
    body.pop("prompt_lists")
    res = client.put(f"{BASE}/{row_id}", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["prompt_lists"] == ["status_field_values"]


def test_an_unknown_prompt_list_is_refused(client):
    res = client.post(BASE, json=_body(_value(), prompt_lists=["zzt_not_a_list"]))
    assert res.status_code == 422, res.text
