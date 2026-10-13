import httpx

from scripts.chatbot_eval.engine import HttpTurnEngine
from scripts.chatbot_eval.trace import DbTraceReader, parse_trace
from tests.chatbot_eval.test_runner import mk_case


def understood(p, c, o):
    return {"stage": "understood", "status": "ok", "facts": {"prompt_tokens": p, "cached_tokens": c, "completion_tokens": o}}


def test_tokens_summed_over_entries():
    tokens, errs, _notes = parse_trace([understood(100, 10, 5), {"stage": "routed", "status": "ok"}, understood(50, 0, 7)])
    assert tokens["prompt"] == 150 and tokens["cached"] == 10 and tokens["output"] == 12
    assert errs == []


def test_failed_stage_with_error():
    _, errs, _notes = parse_trace([{"stage": "resolved", "status": "failed", "error": "BOOM-1"}])
    assert len(errs) == 1 and "resolved" in errs[0] and "BOOM-1" in errs[0]


def test_failed_without_error_ignored():
    assert parse_trace([{"stage": "x", "status": "failed", "error": None}])[1] == []


def test_looked_up_envelope_error():
    entry = {"stage": "looked_up", "status": "ok", "raw": {"envelopes": [{"tool": "get_stock", "error": "TIMEOUT-9"}, {"tool": "ok_tool", "error": None}]}}
    _, errs, _notes = parse_trace([entry])
    assert len(errs) == 1 and "TIMEOUT-9" in errs[0]


def test_lane_text_error_marker():
    entry = {"stage": "looked_up", "status": "ok", "raw": {"envelopes": [{"tool": "get_stock", "lane_text": "Error executing tool get_stock: INVALID_UUID"}, {"tool": "t2", "lane_text": "fine"}]}}
    _, errs, _notes = parse_trace([entry])
    assert len(errs) == 1 and "Error executing tool" in errs[0]


def test_ok_entries_yield_nothing():
    entry = {"stage": "looked_up", "status": "ok", "raw": {"envelopes": [{"tool": "t", "error": None, "lane_text": "fine"}]}}
    assert parse_trace([entry])[1] == []


def test_empty_and_none():
    for t in ([], None):
        tokens, errs, _notes = parse_trace(t)
        assert errs == []
        assert not any(tokens.get(k) for k in ("prompt", "cached", "output"))


class FakeReader:
    def __init__(self, trace):
        self.trace, self.asked = trace, []

    def fetch(self, turn_id):
        self.asked.append(turn_id)
        return self.trace


def _turn(monkeypatch, body, reader):
    monkeypatch.setattr(httpx.Client, "send", lambda self, request, **kw: httpx.Response(200, json=body, request=request))
    eng = HttpTurnEngine("http://fake.invalid", "k", 5, trace_reader=reader)
    return eng.turn(mk_case(), "q", None, "r")


BODY = {"is_test": True, "reply": {"text": "hi"}, "actions": [{"kind": "send", "dry_run": True}]}


def test_reader_merges_tokens_and_errors(monkeypatch):
    reader = FakeReader([understood(100, 10, 5), {"stage": "r", "status": "failed", "error": "BOOM-2"}])
    out = _turn(monkeypatch, {**BODY, "turn_id": "t-1"}, reader)
    assert reader.asked == ["t-1"]
    assert out.tokens["prompt"] == 100 and out.tokens["output"] == 5
    assert any("BOOM-2" in e for e in out.tool_errors)


def test_reader_ignored_without_turn_id(monkeypatch):
    reader = FakeReader([understood(100, 0, 5)])
    out = _turn(monkeypatch, BODY, reader)
    assert reader.asked == []
    assert out.tool_errors == []


def test_reader_returning_none_ok(monkeypatch):
    out = _turn(monkeypatch, {**BODY, "turn_id": "t-2"}, FakeReader(None))
    assert out.tool_errors == []


def test_db_reader_constructible_without_connecting():
    DbTraceReader("postgresql://nobody:x@127.0.0.1:1/none")
