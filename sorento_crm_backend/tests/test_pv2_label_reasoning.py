"""PV2-FIX r7h (owner ruling 14 Oct): reasoning_effort lives on the prompt LABEL.

Column `ai_prompt_labels.reasoning_effort` (none|low|medium|high, NULL = provider default, nothing
sent), edited on the Prompts screen through `POST /ai-assistant/prompts/{name}/model`, read by the PV2
parser call, honoured only by keys whose registry row says `supports_reasoning_effort`.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.models.ai_prompt import AIPromptLabel
from app.services.chatbot import llm_call
from app.services.chatbot.head import parser as v1_parser
from app.services.chatbot.pv2 import call as pv2_call
from tests._pg_fixture import blank_session
from tests.chatbot.pv2.conftest import minimal_output
from tests.chatbot.pv2.test_core_call import _Stub
from tests.test_ai_prompt_registry import api, db, seeded  # noqa: F401 - fixtures

V2 = "chatbot_semantic_parser_v2"
URL = f"/api/v1/system/ai-assistant/prompts/{V2}/model"
LIST = "/api/v1/system/ai-assistant/prompts"


def _row(client, name=V2):
    return next(r for r in client.get(LIST).json() if r["name"] == name)


# ------------------------------------------------------------------ 1 API round trip


def test_the_reasoning_effort_round_trips_through_the_model_endpoint(api):
    client, allow = api
    allow.update({"system.ai_assistant_settings.view", "system.ai_assistant_settings.edit"})
    res = client.post(URL, json={"label": "production", "provider": "openai", "model": "gpt-5.4-mini",
                                 "reasoning_effort": "medium"})
    assert res.status_code == 200, res.text
    assert res.json().get("reasoning_effort") == "medium"
    assert _row(client)["reasoning_effort"] == "medium"


def test_an_invalid_reasoning_effort_is_422(api):
    client, allow = api
    allow.update({"system.ai_assistant_settings.view", "system.ai_assistant_settings.edit"})
    res = client.post(URL, json={"label": "production", "reasoning_effort": "extreme"})
    assert res.status_code == 422, res.text


def test_null_clears_the_reasoning_effort(api):
    client, allow = api
    allow.update({"system.ai_assistant_settings.view", "system.ai_assistant_settings.edit"})
    client.post(URL, json={"label": "production", "reasoning_effort": "high"})
    res = client.post(URL, json={"label": "production", "reasoning_effort": None})
    assert res.status_code == 200, res.text
    assert _row(client)["reasoning_effort"] is None


def test_setting_it_needs_the_edit_permission(api):
    client, allow = api
    allow.add("system.ai_assistant_settings.view")
    res = client.post(URL, json={"label": "production", "reasoning_effort": "low"})
    assert res.status_code == 403, res.text


def test_only_the_pv2_parser_key_supports_reasoning_effort(api):
    client, allow = api
    allow.add("system.ai_assistant_settings.view")
    rows = {r["name"]: r for r in client.get(LIST).json()}
    assert rows[V2]["supports_reasoning_effort"] is True
    assert [n for n, r in rows.items() if r.get("supports_reasoning_effort") and n != V2] == []


# ------------------------------------------------------------------ 2 PV2 reads the label value


def _stage_label(seeded_db, value):
    """A staging label row for the v2 key (what `resolve_v2_config` reads) carrying `value`."""
    prod = seeded_db.query(AIPromptLabel).filter(AIPromptLabel.name == V2, AIPromptLabel.label == "production").first()
    row = seeded_db.query(AIPromptLabel).filter(AIPromptLabel.name == V2, AIPromptLabel.label == "staging").first()
    if row is None:
        row = AIPromptLabel(name=V2, label="staging", version_id=prod.version_id)
        seeded_db.add(row)
    row.reasoning_effort = value
    seeded_db.commit()


def _resolve(seeded_db, monkeypatch):
    import app.services.ai_assistant_service as aas
    import app.services.llm_provider as lp

    monkeypatch.setattr(lp, "resolve_api_key", lambda config, provider: "k")
    monkeypatch.setattr(aas, "AIAssistantConfigService",
                        lambda db: SimpleNamespace(get=lambda: SimpleNamespace(provider="openai", model="gpt-x")))
    return pv2_call.resolve_v2_config(seeded_db)


def test_resolve_v2_config_reads_the_labels_reasoning_effort(seeded, monkeypatch):
    _stage_label(seeded, "medium")
    assert _resolve(seeded, monkeypatch).reasoning_effort == "medium"


def test_a_null_label_value_means_not_sent(seeded, monkeypatch):
    _stage_label(seeded, None)
    config = _resolve(seeded, monkeypatch)
    assert config.reasoning_effort is None
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    pv2_call.parse_v2(config, history=[], memory_block="<memory>\n</memory>", text="x")
    assert "reasoning_effort" not in stub.calls[0]


def test_the_outbound_request_carries_the_labels_value(seeded, monkeypatch):
    _stage_label(seeded, "high")
    config = _resolve(seeded, monkeypatch)
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    pv2_call.parse_v2(config, history=[], memory_block="<memory>\n</memory>", text="x")
    assert stub.calls[0]["reasoning_effort"] == "high"


def test_the_v1_parser_call_is_unchanged(monkeypatch):
    stub = _Stub({})
    monkeypatch.setattr(llm_call, "chat", stub)
    config = v1_parser.ParserConfig(system_prompt="s", prompt_version=1, provider="openai", model="gpt-x", api_key="k")
    try:
        v1_parser.parse(config, "user block")
    except Exception:  # noqa: BLE001 - only the outbound call is under test
        pass
    assert stub.calls and "reasoning_effort" not in stub.calls[0]


# ------------------------------------------------------------------ 5 migration


def _script_directory() -> ScriptDirectory:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_alembic_has_a_single_head_and_it_descends_from_the_alias_seed():
    script = _script_directory()
    heads = script.get_heads()
    assert len(heads) == 1, heads
    revs = {r.revision for r in script.walk_revisions()}
    assert "pv2_0003_label_reasoning" in revs
    assert script.get_revision("pv2_0003_label_reasoning").down_revision == "pv2_0002_alias_seed"


def _run(db, direction):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "pv2_0003_label_reasoning.py"
    spec = importlib.util.spec_from_file_location("zzt_pv2_0003", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection=db.connection())):
        getattr(module, direction)()


def _label(db, name, label):
    version = str(uuid.uuid4())
    db.execute(text("INSERT INTO ai_prompt_versions (id, name, version, type, template, variables) "
                    "VALUES (:id, :n, 9999, 'text', 't', CAST('[]' AS jsonb))"), {"id": version, "n": name})
    db.execute(text("DELETE FROM ai_prompt_labels WHERE name = :n AND label = :l"), {"n": name, "l": label})
    db.execute(text("INSERT INTO ai_prompt_labels (id, name, label, version_id) VALUES (:id, :n, :l, :v)"),
               {"id": str(uuid.uuid4()), "n": name, "l": label, "v": version})


def test_the_migration_adds_the_column_and_sets_the_v2_labels_to_low():
    with blank_session() as db:
        db.execute(text("ALTER TABLE ai_prompt_labels DROP COLUMN IF EXISTS reasoning_effort"))
        _label(db, V2, "staging")
        _label(db, "semantic_parser", "staging")
        _run(db, "upgrade")
        cols = {r[0] for r in db.execute(text("SELECT column_name FROM information_schema.columns "
                                              "WHERE table_schema = current_schema() AND table_name = 'ai_prompt_labels'"))}
        assert "reasoning_effort" in cols
        rows = dict(db.execute(text("SELECT name, reasoning_effort FROM ai_prompt_labels "
                                    "WHERE label = 'staging' AND name IN (:a, :b)"), {"a": V2, "b": "semantic_parser"}).all())
        assert rows[V2] == "low"
        assert rows["semantic_parser"] is None


def test_a_set_model_call_without_reasoning_effort_keeps_the_stored_value(api):
    client, allow = api
    allow.update({"system.ai_assistant_settings.view", "system.ai_assistant_settings.edit"})
    client.post(URL, json={"label": "production", "provider": "openai", "model": "gpt-5.4-mini",
                           "reasoning_effort": "low"})
    res = client.post(URL, json={"label": "production", "provider": "openai", "model": "gpt-5.4"})
    assert res.status_code == 200, res.text
    assert _row(client)["reasoning_effort"] == "low"
    assert _row(client)["model"] == "gpt-5.4"
