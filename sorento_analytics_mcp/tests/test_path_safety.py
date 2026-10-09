"""CLAUDE-CONNECTOR security round A: a tool argument can never steer the outgoing request path.

Root cause: sorento_crm_mcp http_client renders path params with a plain str.replace and httpx then
collapses dot segments, so dataset="../../users/me" reaches /api/v1/users/me with the user's bearer.
The fix belongs in UserBearerClient (the n8n client is untouched): percent-encode each param with
safe="" or refuse the call.
"""
from __future__ import annotations

import pytest

from conftest import AUDIT, BE, ME, call_tool

EVIL = ["../../users/me", "..", "a/../../../users/me", "..%2f..%2fusers%2fme", "x?y=1#z"]
DATASETS = f"{BE}/datasets"
PROJECTS = "/api/v1/project-sales/projects"


def _outgoing(backend):
    return [r for r in backend.requests if r.path not in (ME, AUDIT)]


def _assert_contained(backend, prefix: str):
    for r in _outgoing(backend):
        assert r.path.startswith(prefix), f"request left its template: {r.path}"
        assert "/../" not in r.raw_path and not r.raw_path.endswith("/.."), r.raw_path
        assert not r.query, r.query  # nothing smuggled into the query string


@pytest.mark.parametrize("evil", EVIL)
def test_chat_describe_dataset_cannot_escape_its_path(client, backend, evil):
    call_tool(client, "chat_describe_dataset", {"dataset": evil})
    _assert_contained(backend, DATASETS)
    assert not [r for r in backend.requests if r.path in ("/api/v1/users/me", "/api/v1/analytics-connector/me/../users/me")]


def test_chat_describe_dataset_unknown_key_is_refused_without_a_backend_call(client, backend):
    out = call_tool(client, "chat_describe_dataset", {"dataset": "not_a_dataset"})
    assert out["isError"] is True or "unknown" in out["text"].lower() or "error" in out["text"].lower()
    assert [r for r in backend.requests if r.path.startswith(DATASETS)] == []


@pytest.mark.parametrize("tool,suffix", [("crm_project_detail", ""), ("crm_project_quotations_list", "/quotations")])
@pytest.mark.parametrize("evil", EVIL)
def test_reused_path_param_tools_cannot_escape_their_path(client, backend, tool, suffix, evil):
    call_tool(client, tool, {"project_id": evil})
    _assert_contained(backend, PROJECTS)
    assert not [r for r in backend.requests if r.path.startswith("/api/v1/users")]


def test_a_normal_project_id_still_reaches_the_template(client, backend):
    pid = "3f2b8c1e-5d4a-4c6b-9e7f-1a2b3c4d5e6f"
    backend.on("GET", f"{PROJECTS}/{pid}", json_body={"id": pid})
    out = call_tool(client, "crm_project_detail", {"project_id": pid})
    assert out["isError"] is False
    assert len(backend.calls("GET", f"{PROJECTS}/{pid}")) == 1
