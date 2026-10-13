import httpx
import pytest

from scripts.chatbot_eval import engine as eng_mod
from scripts.chatbot_eval.engine import HttpTurnEngine, load_engine
from tests.chatbot_eval.fakes import FakeEngine
from tests.chatbot_eval.test_runner import mk_case


def make_case(reply_to=None):
    c = mk_case(text="final q")
    c.contact = {"respond_id": "9001", "custom_fields": [{"name": "k", "value": "v"}]}
    if reply_to:
        c.message = {"text": "final q", "reply_to": reply_to}
    return c


def http():
    return HttpTurnEngine("http://fake.invalid", "key-1", 5)


def test_envelope_core_fields():
    env = http().build_envelope(make_case(), "final q", None, "run-7")
    assert env["is_test"] is True
    assert env["test_run_id"] == "run-7"
    assert env["ingress"] == "console"
    assert env["contact"]["id"] == "9001"
    assert env["contact"]["custom_fields"] == [{"name": "k", "value": "v"}]
    assert env["message"]["message"]["message"]["text"] == "final q"
    assert "previous_conversation_state" not in env


def test_envelope_state_carried():
    env = http().build_envelope(make_case(), "final q", {"a": 1}, "r")
    assert env["previous_conversation_state"] == {"a": 1}


def test_reply_to_only_on_final_message():
    case = make_case(reply_to={"id": "m1", "text": "quoted"})
    final = http().build_envelope(case, "final q", None, "r")
    earlier = http().build_envelope(case, "an earlier turn", None, "r")
    assert "replyTo" in final["message"]["message"]
    assert "replyTo" not in earlier["message"]["message"]


def test_no_reply_to_when_case_has_none():
    env = http().build_envelope(make_case(), "final q", None, "r")
    assert "replyTo" not in env["message"]["message"]


def test_turn_maps_response(monkeypatch):
    seen = {}

    def fake_send(self, request, **kw):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        body = {
            "turn_id": "t1", "is_test": True, "branch_kind": "answer",
            "reply": {"text": "hello there"},
            "actions": [{"kind": "send", "dry_run": True}, {"kind": "log", "dry_run": True}],
            "session_patch": {"s": 1},
        }
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    out = http().turn(make_case(), "final q", None, "r")
    assert seen["url"].endswith("/api/v1/external/chat/turn")
    assert seen["key"] == "key-1"
    assert out.reply_text == "hello there"
    assert out.branch_kind == "answer"
    assert out.state == {"s": 1}
    assert out.dry_run is True
    assert out.tool_errors == []


def test_turn_not_dry_when_an_action_is_live(monkeypatch):
    def fake_send(self, request, **kw):
        body = {"turn_id": "t", "is_test": True, "reply": {"text": "x"},
                "actions": [{"kind": "send", "dry_run": True}, {"kind": "send", "dry_run": False}]}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    assert http().turn(make_case(), "q", None, "r").dry_run is False


def test_load_engine_http():
    assert isinstance(load_engine("http", base_url="http://x.invalid", api_key="k", timeout=3), HttpTurnEngine)


def test_load_engine_module_class():
    e = load_engine("tests.chatbot_eval.fakes:FakeEngine")
    assert isinstance(e, FakeEngine)
