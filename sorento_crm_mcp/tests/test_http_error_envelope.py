"""CHATBOT-SELFREF-SCOPE B3: a non-2xx route answer is an error envelope, never a line.

Production, 30 Sep 2026: `/api/v1/sales/analysis` answered 403 (the n8n principal lacked
`sales.reports.view`) and the presenter rendered the body as "Could not run the sales
report right now." with `has_result: true`, so the chatbot's trace showed a successful
tool call and `error: null`. `http_client.http_error_body` now stamps the status onto the
body, and `present_response` turns any body carrying `status_code >= 400` into an error
envelope for every presenter tool.
"""
from __future__ import annotations

import json

from sorento_crm_mcp.http_client import http_error_body
from sorento_crm_mcp.presenters import PRESENTER_TOOLS, present_response

ROUTE_403 = '{"success": false, "message": "Permission required", "code": "PERMISSION_DENIED"}'


def test_http_error_body_keeps_the_routes_keys_and_stamps_the_status():
    body = json.loads(http_error_body(ROUTE_403, status_code=403, path="/api/v1/sales/analysis", method="GET"))
    assert body["status_code"] == 403
    assert body["code"] == "PERMISSION_DENIED" and body["message"] == "Permission required"
    assert body["error"] == "GET /api/v1/sales/analysis returned HTTP 403: PERMISSION_DENIED"
    assert body["http_error"] == body["error"]
    assert body["path"] == "/api/v1/sales/analysis" and body["method"] == "GET"


def test_http_error_body_keeps_an_error_the_route_named():
    body = json.loads(http_error_body('{"error": "ACCESS_DENIED"}', status_code=403, path="/x", method="GET"))
    assert body["error"] == "ACCESS_DENIED"
    assert body["http_error"] == "GET /x returned HTTP 403: ACCESS_DENIED"
    assert body["status_code"] == 403


def test_http_error_body_wraps_a_non_json_body():
    body = json.loads(http_error_body("<html>Bad gateway</html>", status_code=502, path="/x", method="GET"))
    assert body["status_code"] == 502
    assert body["detail"] == "<html>Bad gateway</html>"
    assert body["error"] == "GET /x returned HTTP 502"


def test_every_presenter_tool_renders_a_non_2xx_as_an_error_envelope():
    raw = http_error_body(ROUTE_403, status_code=403, path="/api/v1/sales/analysis", method="GET")
    assert PRESENTER_TOOLS
    for tool in PRESENTER_TOOLS:
        env = json.loads(present_response(tool, raw))
        assert env["has_result"] is False, (tool, env)
        assert env["status_code"] == 403, (tool, env)
        assert "HTTP 403" in env["error"] and "PERMISSION_DENIED" in env["error"], (tool, env)
        assert env["response"] == "" and env["answers"] == [], (tool, env)
        assert env["detail"]["code"] == "PERMISSION_DENIED", (tool, env)
        assert "Could not run" not in json.dumps(env), (tool, env)


def test_a_200_route_status_error_still_renders_the_routes_own_line():
    """The route's OWN `{status: error}` (200) is a terminal answer and keeps its line."""
    env = json.loads(present_response("crm_sales_analysis", '{"status": "error", "message": "x"}'))
    assert env["has_result"] is True
    assert env["response"] == "Could not run the sales report right now."
