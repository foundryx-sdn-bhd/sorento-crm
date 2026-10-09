"""CLAUDE-CONNECTOR S2: the five chat tools map to the S1 backend routes (card section 4.3).

Routes and payloads are pinned by sorento_crm_backend/tests/analytics_connector/test_datasets.py:
GET /datasets, GET /datasets/{dataset}, POST /query (JSON body), GET /messages/search, GET /conversation.
The backend audits chat calls itself, so the container posts NO audit row for them.
"""
from __future__ import annotations

import json

from conftest import AUDIT, BE, INTERNAL_KEY, call_tool


def _assert_auth(rec):
    assert rec.headers["authorization"] == "Bearer tok-A"
    assert rec.headers["x-analytics-connector-key"] == INTERNAL_KEY
    assert "x-api-key" not in rec.headers


def test_chat_list_datasets(client, backend):
    body = {"datasets": [{"key": "messages", "description": "d", "row_count": 5}]}
    backend.on("GET", f"{BE}/datasets", json_body=body)
    out = call_tool(client, "chat_list_datasets", {})
    assert out["isError"] is False
    assert json.loads(out["text"]) == body
    _assert_auth(backend.calls("GET", f"{BE}/datasets")[0])


def test_chat_describe_dataset(client, backend):
    body = {"columns": [{"key": "text", "type": "text"}], "date_column": "sent_at", "sample": {"rows": []}}
    backend.on("GET", f"{BE}/datasets/messages", json_body=body)
    out = call_tool(client, "chat_describe_dataset", {"dataset": "messages"})
    assert json.loads(out["text"]) == body
    _assert_auth(backend.calls("GET", f"{BE}/datasets/messages")[0])


def test_chat_query_posts_the_arguments_as_the_json_body(client, backend):
    reply = {"columns": ["direction", "count"], "rows": [["incoming", 2]], "row_count": 1,
             "truncated": False, "next_offset": None}
    backend.on("POST", f"{BE}/query", json_body=reply)
    args = {
        "dataset": "messages",
        "filters": [{"column": "direction", "op": "eq", "value": "incoming"}],
        "group_by": ["direction", "sent_at:month"],
        "measures": [{"fn": "count"}, {"fn": "count_distinct", "column": "contact_id"}],
        "period": {"from": "2026-09-01", "to": "2026-09-30"},
        "order_by": [{"column": "count", "dir": "desc"}],
        "limit": 50,
        "offset": 0,
    }
    out = call_tool(client, "chat_query", args)
    assert json.loads(out["text"]) == reply
    sent = backend.calls("POST", f"{BE}/query")[0]
    assert sent.json == args
    _assert_auth(sent)


def test_chat_query_omits_arguments_the_model_did_not_give(client, backend):
    backend.on("POST", f"{BE}/query", json_body={"columns": [], "rows": [], "row_count": 0,
                                                  "truncated": False, "next_offset": None})
    call_tool(client, "chat_query", {"dataset": "messages", "columns": ["id"]})
    assert backend.calls("POST", f"{BE}/query")[0].json == {"dataset": "messages", "columns": ["id"]}


def test_chat_search_messages_maps_to_query_params(client, backend):
    reply = {"columns": ["sent_at", "text", "contact_name"], "rows": [], "row_count": 0}
    backend.on("GET", f"{BE}/messages/search", json_body=reply)
    out = call_tool(client, "chat_search_messages", {
        "text": "ZZT-SKU", "direction": "incoming", "date_from": "2026-09-01",
        "date_to": "2026-09-30", "limit": 25, "offset": 5})
    assert json.loads(out["text"]) == reply
    sent = backend.calls("GET", f"{BE}/messages/search")[0]
    assert sent.query == {"text": "ZZT-SKU", "direction": "incoming", "date_from": "2026-09-01",
                          "date_to": "2026-09-30", "limit": "25", "offset": "5"}
    _assert_auth(sent)


def test_chat_get_conversation_maps_to_query_params(client, backend):
    reply = {"contact": {"id": "c1"}, "columns": ["sent_at", "text"], "rows": []}
    backend.on("GET", f"{BE}/conversation", json_body=reply)
    out = call_tool(client, "chat_get_conversation", {"contact": "ZZT Contact", "limit": 100})
    assert json.loads(out["text"]) == reply
    sent = backend.calls("GET", f"{BE}/conversation")[0]
    assert sent.query == {"contact": "ZZT Contact", "limit": "100"}
    _assert_auth(sent)


def test_a_backend_403_is_reported_to_the_model_not_raised(client, backend):
    """Q2: a user without system.chat_history.view gets 403 from the backend."""
    backend.on("GET", f"{BE}/datasets", status=403, json_body={"detail": "Permission required"})
    out = call_tool(client, "chat_list_datasets", {})
    assert "403" in out["text"]


def test_chat_tools_post_no_container_audit_row(client, backend):
    backend.on("GET", f"{BE}/datasets", json_body={"datasets": []})
    call_tool(client, "chat_list_datasets", {})
    assert backend.calls("POST", AUDIT) == []
