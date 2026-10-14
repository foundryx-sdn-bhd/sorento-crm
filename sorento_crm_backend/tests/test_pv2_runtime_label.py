"""PV2-FIX r8a item 6: the Prompts screen reads and saves the label the runtime reads.

The v2 parser reads its STAGING label (`pv2.index.PUBLISH_LABEL`); the card read and saved 'production', so
Save answered 404 "has no published production version to configure". The key row exposes `runtime_label`
(v2 -> "staging", every other key -> "production") and the per-label values come from that label."""
from __future__ import annotations

from types import SimpleNamespace

from app.models.ai_prompt import AIPromptLabel
from app.services.chatbot.pv2 import call as pv2_call
from tests.test_ai_prompt_registry import api, db, seeded  # noqa: F401 - fixtures

V2 = "chatbot_semantic_parser_v2"
URL = f"/api/v1/system/ai-assistant/prompts/{V2}/model"
LIST = "/api/v1/system/ai-assistant/prompts"


def _staging(seeded_db, value=None):
    prod = seeded_db.query(AIPromptLabel).filter(AIPromptLabel.name == V2, AIPromptLabel.label == "production").first()
    row = seeded_db.query(AIPromptLabel).filter(AIPromptLabel.name == V2, AIPromptLabel.label == "staging").first()
    if row is None:
        row = AIPromptLabel(name=V2, label="staging", version_id=prod.version_id)
        seeded_db.add(row)
    row.reasoning_effort = value
    seeded_db.commit()
    return row


def _row(client, name=V2):
    return next(r for r in client.get(LIST).json() if r["name"] == name)


def test_the_key_row_exposes_the_label_the_runtime_reads(api, seeded):
    client, allow = api
    allow.add("system.ai_assistant_settings.view")
    rows = {r["name"]: r for r in client.get(LIST).json()}
    assert rows[V2]["runtime_label"] == "staging"
    assert {r["runtime_label"] for n, r in rows.items() if n != V2} == {"production"}


def test_the_v2_row_shows_the_runtime_labels_value(api, seeded):
    client, allow = api
    allow.add("system.ai_assistant_settings.view")
    _staging(seeded, "high")
    assert _row(client)["reasoning_effort"] == "high"


def test_saving_on_the_runtime_label_stores_the_value_and_the_parser_reads_it(api, seeded, monkeypatch):
    import app.services.ai_assistant_service as aas
    import app.services.llm_provider as lp

    client, allow = api
    allow.update({"system.ai_assistant_settings.view", "system.ai_assistant_settings.edit"})
    _staging(seeded, None)
    res = client.post(URL, json={"label": "staging", "provider": "openai", "model": "gpt-5.4-mini",
                                 "reasoning_effort": "medium"})
    assert res.status_code == 200, res.text
    assert _row(client)["reasoning_effort"] == "medium"
    monkeypatch.setattr(lp, "resolve_api_key", lambda config, provider: "k")
    monkeypatch.setattr(aas, "AIAssistantConfigService",
                        lambda db: SimpleNamespace(get=lambda: SimpleNamespace(provider="openai", model="gpt-x")))
    assert pv2_call.resolve_v2_config(seeded).reasoning_effort == "medium"
