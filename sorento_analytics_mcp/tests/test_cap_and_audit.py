"""CLAUDE-CONNECTOR S2: output cap (card 5.1) and the audit post (card 6)."""
from __future__ import annotations

import json

from conftest import AUDIT, BE, INTERNAL_KEY, call_tool

BRANDS = "/api/v1/master-data/brands"
CAP = 120_000


# --------------------------------------------------------------------------- output cap


def test_an_oversized_reused_tool_answer_is_capped_and_says_truncated(client, backend):
    rows = [{"id": str(i), "name": "x" * 1000} for i in range(300)]  # ~310k chars
    backend.on("GET", BRANDS, json_body={"data": rows, "total": 300})
    out = call_tool(client, "crm_master_brands_list", {})
    assert len(out["text"]) <= CAP
    assert "truncated" in out["text"].lower()


def test_an_oversized_chat_answer_is_capped_too(client, backend):
    rows = [["x" * 1000] for _ in range(300)]
    backend.on("POST", f"{BE}/query", json_body={"columns": ["text"], "rows": rows, "row_count": 300,
                                                  "truncated": False, "next_offset": None})
    out = call_tool(client, "chat_query", {"dataset": "messages", "columns": ["text"]})
    assert len(out["text"]) <= CAP
    assert "truncated" in out["text"].lower()


def test_an_answer_under_the_cap_is_untouched(client, backend):
    rows = [{"id": str(i), "name": "x" * 1000} for i in range(100)]  # ~110k chars, under 120k
    backend.on("GET", BRANDS, json_body={"data": rows, "total": 100})
    out = call_tool(client, "crm_master_brands_list", {})
    assert len(out["text"]) <= CAP
    parsed = json.loads(out["text"])
    assert parsed["data"] == rows
    assert "truncated" not in out["text"].lower()


# --------------------------------------------------------------------------- audit


def test_a_reused_tool_call_posts_one_audit_row_after_it_returns(client, backend):
    rows = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}, {"id": "3", "name": "c"}]
    backend.on("GET", BRANDS, json_body={"data": rows, "total": 3})
    out = call_tool(client, "crm_master_brands_list", {"limit": "5"})
    posts = backend.calls("POST", AUDIT)
    assert len(posts) == 1
    sent = posts[0]
    assert sent.headers["authorization"] == "Bearer tok-A"
    assert sent.headers["x-analytics-connector-key"] == INTERNAL_KEY
    assert "x-api-key" not in sent.headers
    body = sent.json
    assert set(body) >= {"tool", "args", "status", "ms", "rows", "chars"}
    assert body["tool"] == "crm_master_brands_list"
    assert body["args"].get("limit") in ("5", 5)
    assert body["status"] == "ok"
    assert isinstance(body["ms"], int) and body["ms"] >= 0
    assert body["rows"] == 3
    assert body["chars"] == len(out["text"])
    # It is sent AFTER the tool's own backend call.
    order = [r.path for r in backend.requests]
    assert order.index(AUDIT) > order.index(BRANDS)


def test_audit_chars_is_the_size_after_the_cap(client, backend):
    rows = [{"id": str(i), "name": "x" * 1000} for i in range(300)]
    backend.on("GET", BRANDS, json_body={"data": rows, "total": 300})
    out = call_tool(client, "crm_master_brands_list", {})
    assert backend.calls("POST", AUDIT)[0].json["chars"] == len(out["text"]) <= CAP


def test_a_backend_error_is_audited_as_an_error(client, backend):
    backend.on("GET", BRANDS, status=403, json_body={"detail": "Permission required"})
    call_tool(client, "crm_master_brands_list", {})
    assert backend.calls("POST", AUDIT)[0].json["status"] == "error"


def test_each_call_is_audited_separately(client, backend):
    backend.on("GET", BRANDS, json_body={"data": [], "total": 0})
    call_tool(client, "crm_master_brands_list", {})
    call_tool(client, "crm_master_brands_list", {})
    assert len(backend.calls("POST", AUDIT)) == 2


def test_an_audit_failure_does_not_break_the_tool_answer(client, backend):
    backend.on("GET", BRANDS, json_body={"data": [{"id": "1"}], "total": 1})
    backend.on("POST", AUDIT, status=500, json_body={"detail": "down"})
    out = call_tool(client, "crm_master_brands_list", {})
    assert out["isError"] is False
    assert json.loads(out["text"])["data"] == [{"id": "1"}]


def test_listing_tools_is_not_audited(client, backend):
    from conftest import list_tools

    list_tools(client)
    assert backend.calls("POST", AUDIT) == []


# --------------------------------------------------------------------------- escalation side call


def test_an_empty_result_makes_no_escalation_side_call(client, backend):
    """The stock template asks /api/v1/system/mcp-routing with the shared key on an empty result.

    Card 4.1: that call is neutralised, so the only backend calls are the tool's own GET and
    the audit POST, and none of them carries X-API-Key.
    """
    backend.on("GET", BRANDS, json_body={"data": [], "total": 0})
    backend.on("GET", "/api/v1/system/mcp-routing", json_body={"team": "x"})
    out = call_tool(client, "crm_master_brands_list", {})
    assert "suggested_escalation" not in out["text"]
    paths = [r.path for r in backend.requests]
    assert not [p for p in paths if "mcp-routing" in p], paths
    assert all("x-api-key" not in r.headers for r in backend.requests)
