"""PV2-EVAL: AnthropicClient reads the text block even when a thinking block comes first."""
from __future__ import annotations

import httpx
import pytest

from scripts.pv2_battery import judge

VERDICT = '{"verdict":"pass","classes":[],"reason":"ok"}'


class FakeResp:
    status_code = 200

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"content": self._content}


def test_complete_skips_thinking_block(monkeypatch):
    content = [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": VERDICT}]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(content))
    assert judge.AnthropicClient("k").complete("p") == VERDICT


def test_complete_without_text_block_raises(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp([{"type": "thinking", "thinking": "x"}]))
    with pytest.raises(RuntimeError):
        judge.AnthropicClient("k").complete("p")
