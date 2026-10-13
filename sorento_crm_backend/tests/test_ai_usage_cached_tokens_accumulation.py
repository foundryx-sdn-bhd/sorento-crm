"""MODEL-PRICES CU-3: a turn that calls the provider more than once records the SUM of its cached_tokens.

Kill tests for the `+= cached_tokens` lines in ai_assistant_service (agent loop and record answer) and
document_extraction (per-page sum). Providers are stubs; no real LLM call.
"""
from __future__ import annotations

import pytest

from app.config import settings as app_settings
from app.models.ai_assistant import AIAssistantUsageLog
from app.services import document_extraction
from app.services.ai_assistant_service import AIAssistantChatService
from app.services.document_extraction import RenderedPage, extract_document
from app.services.llm_provider import ChatResult
from tests._pg_fixture import blank_session


class _GuideMCP:
    def __init__(self, *_a, **_k):
        pass

    def list_tools_with_schema(self):
        return {
            "crm_test_tool": {"description": "t", "inputSchema": {"type": "object", "properties": {}}},
            "user_guides_read": {"description": "g", "inputSchema": {"type": "object", "properties": {}}},
        }

    def call_tool(self, tool_name, args):  # noqa: ANN001
        return '{"result": "ok"}'


class _TwoCallProvider:
    """Call 1 asks for a tool (cached 100), call 2 answers (cached 250)."""

    def __init__(self, tool):
        self.tool, self.n = tool, 0

    def chat(self, _messages, tools=None, **_k):  # noqa: ANN001
        self.n += 1
        if self.n == 1:
            return ChatResult(content="", prompt_tokens=10, completion_tokens=1, total_tokens=11, cached_tokens=100,
                              tool_calls=[{"id": "c1", "name": self.tool, "arguments": {}}])
        return ChatResult(content="final answer", prompt_tokens=10, completion_tokens=1, total_tokens=11,
                          cached_tokens=250, tool_calls=[])


@pytest.fixture
def svc_and_cfg(monkeypatch):
    import app.services.ai_assistant_service as svc_module

    monkeypatch.setattr(svc_module, "MCPRuntimeClient", _GuideMCP)
    with blank_session() as db:
        svc = AIAssistantChatService(db)
        cfg = svc.cfg.get()
        cfg.api_key_ciphertext = "fake-key"
        cfg.provider, cfg.model, cfg.is_enabled = "openai", "gpt-4o-mini", True
        db.commit()
        yield svc_module, svc, cfg


def test_cu3_agent_loop_sums_cached_tokens_over_every_provider_call(svc_and_cfg, monkeypatch):
    svc_module, svc, cfg = svc_and_cfg
    monkeypatch.setattr(svc_module, "get_provider", lambda *_a, **_k: _TwoCallProvider("crm_test_tool"))
    _text, _calls, usage = svc._run_agent_loop(
        config=cfg, history=[], user_message="give me the test data", standalone_query="give me the test data",
        selected_tools=[{"tool_name": "crm_test_tool"}], sources=[], turn_cache=None,
    )
    assert usage["cached_tokens"] == 350


def test_cu3_record_answer_sums_cached_tokens_over_the_guide_round_trip(svc_and_cfg, monkeypatch):
    svc_module, svc, cfg = svc_and_cfg
    monkeypatch.setattr(svc_module, "get_provider", lambda *_a, **_k: _TwoCallProvider("user_guides_read"))
    _text, _calls, usage = svc._render_record_answer(
        config=cfg, history=[], user_message="what next?",
        record_ctx={"entity_type": "complaint", "current_state": {"status": "open"}},
    )
    assert usage["cached_tokens"] == 350


class _PageProvider:
    name = "stub"

    def __init__(self):
        self.n = 0

    def chat(self, messages, *, images=None, **kwargs):
        import re

        page = int(re.search(r"page (\d+) of", messages[0]["content"]).group(1))
        return ChatResult(content='{"ok": true}', prompt_tokens=100, completion_tokens=5, total_tokens=105,
                          cached_tokens={1: 10, 2: 20}[page])


def test_cu3_document_extraction_logs_the_sum_of_every_pages_cached_tokens(monkeypatch):
    monkeypatch.setattr(app_settings, "document_ai_provider", "gemini", raising=False)
    monkeypatch.setattr(app_settings, "gemini_api_key", "ZZT-key", raising=False)
    monkeypatch.setattr(document_extraction, "_render_pages_rich",
                        lambda *a, **k: [RenderedPage(image_b64=f"ZZT-{i}", image_mime="image/jpeg", text=None)
                                         for i in (1, 2)])
    monkeypatch.setattr(document_extraction, "get_provider", lambda *a, **k: _PageProvider())
    with blank_session() as db:
        result = extract_document(db, b"ZZT", "application/pdf", prompt_key="po_extractor")
        assert result.cached_tokens == 30
        row = db.query(AIAssistantUsageLog).filter(AIAssistantUsageLog.model.like("stub/%")).one()
        assert row.cached_tokens == 30
