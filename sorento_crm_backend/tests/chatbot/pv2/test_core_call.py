"""PV2-CORE: the parser v2 call and record mode (CONTRACT-PV2-13oct.md sections 2 and 6).

No model is called: `llm_call.chat` is replaced by a stub that records what it was sent.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.chatbot import llm_call
from app.services.chatbot.pv2 import call as pv2_call
from app.services.chatbot.pv2 import index as pv2_index
from app.services.chatbot.pv2.schema import PV2_JSON_SCHEMA
from tests.chatbot.pv2.conftest import minimal_ask, minimal_output


class _Stub:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def __call__(self, provider, api_key, model, messages, **kwargs):
        self.calls.append({"provider": provider, "model": model, "messages": messages, **kwargs})
        return SimpleNamespace(
            content=self.content if isinstance(self.content, str) else json.dumps(self.content),
            prompt_tokens=100, completion_tokens=20, total_tokens=120, cached_tokens=80,
        )


def _config(template: str, version=7, version_id="vid") -> pv2_call.V2Config:
    body = pv2_index.index_of(template)
    import hashlib

    return pv2_call.V2Config(
        system_prompt=template,
        prompt_version=version,
        version_id=version_id,
        index_sha=hashlib.sha1(body.encode()).hexdigest(),
        keys=pv2_index.parse_index_keys(template),
        provider="openai",
        model="gpt-x",
        api_key="k",
    )


TEMPLATE = (
    "HEAD\n\n## DOMAINS\ninventory = stock\nproduct_attachment = product attachments\n\n"
    "## ENTITY KINDS\nproduct = product\nbrand = brand\n\n## BRANDS\nSORENTO = Sorento\n\n"
    "## ATTACHMENT TYPES (key = name | aka)\ntechnical_specifications = Technical Specifications\n\n"
    "## PRODUCT CLASSES\n\n## SPEC ATTRIBUTES\n\n## REPORTS\n\n## TEAMS\nwarehouse = warehouse\n"
)


def test_messages_layout_system_history_memory(monkeypatch):
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    cfg = _config(TEMPLATE)
    history = [("td SRTWC286", "Here is the TD"), ("thanks", "You're welcome")]
    pv2_call.parse_v2(cfg, history=history, memory_block="<memory>\nTODAY: x\n</memory>", text="stock?")
    msgs = stub.calls[0]["messages"]
    assert msgs[0] == {"role": "system", "content": TEMPLATE}
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert msgs[1]["content"] == "td SRTWC286" and msgs[2]["content"] == "Here is the TD"
    assert msgs[-1]["content"] == "<memory>\nTODAY: x\n</memory>\n<message>stock?</message>"


def test_history_capped_to_last_three_exchanges(monkeypatch):
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    history = [(f"u{i}", f"b{i}") for i in range(6)]
    pv2_call.parse_v2(_config(TEMPLATE), history=history, memory_block="<memory></memory>", text="x")
    users = [m["content"] for m in stub.calls[0]["messages"][1:-1] if m["role"] == "user"]
    assert users == ["u3", "u4", "u5"]
    assert pv2_call.PV2_HISTORY_EXCHANGES == 3


def test_strict_schema_and_cache_key(monkeypatch):
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    cfg = _config(TEMPLATE, version=7)
    pv2_call.parse_v2(cfg, history=[], memory_block="<memory></memory>", text="x")
    kw = stub.calls[0]
    assert kw["json_schema"] is PV2_JSON_SCHEMA
    assert kw["temperature"] == 0.0
    assert kw["prompt_cache_key"] == f"chatbot_semantic_parser_v2:v7:{cfg.index_sha[:8]}"


def test_unpinned_key_nulled_and_reported(monkeypatch):
    out = minimal_output(minimal_ask(domain="inventory", attachment_type="cad_files", entities=[
        {"kind": "brand", "key": "NOPE", "phrase": "nope", "op": "set", "refers_to_held": False}]))
    monkeypatch.setattr(llm_call, "chat", _Stub(out))
    res = pv2_call.parse_v2(_config(TEMPLATE), history=[], memory_block="<memory></memory>", text="x")
    assert res.output.asks[0].attachment_type is None
    assert res.output.asks[0].entities[0].key is None
    assert {d.value for d in res.dropped} == {"cad_files", "NOPE"}
    assert res.usage["cached_tokens"] == 80 and res.usage["model"] == "gpt-x"


@pytest.mark.parametrize("content", ["", "not json", "[1]", json.dumps({"asks": []})])
def test_bad_emission_raises_with_usage(monkeypatch, content):
    monkeypatch.setattr(llm_call, "chat", _Stub(content))
    with pytest.raises(pv2_call.ParserV2Error) as err:
        pv2_call.parse_v2(_config(TEMPLATE), history=[], memory_block="<memory></memory>", text="x")
    if content:
        assert err.value.usage["total_tokens"] == 120


def test_provider_failure_raises(monkeypatch):
    def boom(*a, **k):
        raise llm_call.RateLimited("slow down")

    monkeypatch.setattr(llm_call, "chat", boom)
    with pytest.raises(pv2_call.ParserV2Error) as err:
        pv2_call.parse_v2(_config(TEMPLATE), history=[], memory_block="<memory></memory>", text="x")
    assert err.value.rate_limited is True


def test_record_mode_appends_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("PV2_RECORD_DIR", str(tmp_path))
    monkeypatch.setattr(llm_call, "chat", _Stub(minimal_output()))
    cfg = _config(TEMPLATE)
    pv2_call.parse_v2(cfg, history=[], memory_block="<memory>M</memory>", text="hello")
    pv2_call.parse_v2(cfg, history=[], memory_block="<memory>M</memory>", text="again")
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [r["text"] for r in rows] == ["hello", "again"]
    assert rows[0]["index_sha"] == cfg.index_sha and rows[0]["version"] == 7
    assert rows[0]["memory"] == "<memory>M</memory>"
    assert rows[0]["parser_out"]["asks"][0]["act"] == "new_topic"


def test_record_mode_off_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("PV2_RECORD_DIR", raising=False)
    monkeypatch.setattr(llm_call, "chat", _Stub(minimal_output()))
    pv2_call.parse_v2(_config(TEMPLATE), history=[], memory_block="<memory></memory>", text="x")
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- config


def _ai_config(db):
    from app.models.ai_assistant import AIAssistantConfig

    db.add(AIAssistantConfig(provider="openai", model="gpt-test", api_key_ciphertext="sk-test"))
    db.flush()


def test_resolve_config_pins_published_staging_version(session_factory):
    db = session_factory()
    _ai_config(db)
    vid = pv2_index.publish_v2(db, "HEAD V2")
    cfg = pv2_call.resolve_v2_config(db)
    body, sha = pv2_index.render_index(db)
    assert cfg.version_id == vid and cfg.prompt_version == 1
    assert cfg.system_prompt == "HEAD V2\n\n" + body
    assert cfg.index_sha == sha
    assert cfg.keys == pv2_index.parse_index_keys(body)
    assert cfg.cache_key == f"chatbot_semantic_parser_v2:v1:{sha[:8]}"


def test_resolve_config_override_version_id(session_factory):
    db = session_factory()
    _ai_config(db)
    first = pv2_index.publish_v2(db, "HEAD A")
    pv2_index.publish_v2(db, "HEAD B")
    cfg = pv2_call.resolve_v2_config(db, override_version_id=first)
    assert cfg.system_prompt.startswith("HEAD A") and cfg.version_id == first


def test_resolve_config_unpublished_uses_file_head_plus_live_index(session_factory):
    db = session_factory()
    _ai_config(db)
    cfg = pv2_call.resolve_v2_config(db)
    body, sha = pv2_index.render_index(db)
    assert cfg.prompt_version is None and cfg.version_id is None
    assert cfg.system_prompt.endswith("\n\n" + body)
    assert cfg.system_prompt.startswith("You are the Sorento chatbot's reader.")
    assert cfg.index_sha == sha
    assert cfg.cache_key == f"chatbot_semantic_parser_v2:vfallback:{sha[:8]}"


def test_prompt_head_fits_caps():
    from app.services.ai_prompt_registry import PROMPT_KEYS

    spec = PROMPT_KEYS["chatbot_semantic_parser_v2"]
    head = spec.fallback()
    assert spec.variables == [] and spec.active is False
    assert 0 < len(head) <= pv2_index.HEAD_CAP_CHARS
    assert "{{" not in head
    assert not any(line.rstrip() in pv2_index.SECTIONS for line in head.splitlines())
