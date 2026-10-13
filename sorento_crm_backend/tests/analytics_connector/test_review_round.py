"""CLAUDE-CONNECTOR r3 review round (PR #1547): the reviewers' should-fixes, pinned red-first.

Contract: `documentation/plans/analytics/CONTRACT-claude-connector-ss-09oct.md`.

Resolutions pinned here:
* Parameter allowlist: a signed call whose query carries a key that is not in the called tool's
  manifest `query_params` is refused 403 `param_not_allowed` before the route runs (no audit row,
  section 8.3). The listed params still pass.
* Tool-route binding: `claims["tool"]` must name a manifest tool whose (method, path_template) is
  the route FastAPI resolved; anything else is 403 `route_not_allowed`, even with a matching
  `req_hash`. The allowlist is the NAMED tool's (two tools share the attachments route with
  different params). The manifest route itself is exempt (`tool` may be empty there).
* CANONICAL_QUERY keeps the sent order (no sorting): `limit=1&limit=500` != `limit=500&limit=1`.
* Audit status: a served call that answers non-2xx records that HTTP status (an int), not "ok".
* Body: the token is verified before any body parsing; a garbage `Connector` token with a 2 MB body
  is 401 `invalid_signature`. Chosen cap: a body over 1 MB (1 048 576 bytes) is refused 413 even
  with a valid token (status only; the code is the coder's), and writes no audit row.
* `claude_connector` is not a `user_sessions.auth_method` (nothing mints it since r3): gone from
  AUTH_METHODS, from the model's CHECK, and acc_0001 no longer touches the user_sessions CHECK.
"""
from __future__ import annotations

import inspect
import json
import uuid

import pytest

from tests._pg_fixture import blank_session
from tests.analytics_connector._helpers import (
    BASE,
    BRANDS,
    BRANDS_VIEW,
    CHAT_VIEW,
    USE,
    assert_error,
    call_token,
    client_for,
    error_code,
    make_user,
    signed,
    tool_call_rows,
)

DATASETS = f"{BASE}/datasets"
QUERY = f"{BASE}/query"
SEARCH = f"{BASE}/messages/search"
CERTS = "/api/v1/master-data/certificates/"
ATTACHMENTS = "/api/v1/resource-management/attachments/"
PROJECT = "/api/v1/project-sales/projects/{}"
CERTS_VIEW = "master_data.certificates.view"
PROJECTS_VIEW = "projects.projects.view"


@pytest.fixture()
def env(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            yield db, client


def _analyst(db, label, extra=()):
    return make_user(db, label, perms=(USE, CHAT_VIEW, BRANDS_VIEW, *extra))


# ------------------------------------------------------------------ 1a. parameter allowlist


@pytest.mark.parametrize(
    "tool,url",
    [
        ("crm_certificates_list", f"{CERTS}?resolve_signed_urls=true"),
        ("crm_certificates_list", f"{CERTS}?limit=5&resolve_signed_urls=true"),
        ("crm_master_brands_list", f"{BRANDS}?contact_id=x"),
        ("crm_master_brands_list", f"{BRANDS}?contact_id=x&space_id=1"),
        ("crm_master_brands_list", f"{BRANDS}?page=1&zzt_unknown=1"),
        ("chat_search_messages", f"{SEARCH}?text=x&space_id=1"),
        ("chat_list_datasets", f"{DATASETS}?contact_id=x"),
    ],
)
def test_a_query_key_outside_the_tools_manifest_params_is_param_not_allowed(env, tool, url):
    db, client = env
    user = _analyst(db, f"prm-{uuid.uuid4().hex[:6]}", extra=(CERTS_VIEW,))
    assert_error(signed(client, "GET", url, user.id, tool=tool), 403, "param_not_allowed")
    assert tool_call_rows(db, user.id) == []  # refused by sorento's own checks: no audit row


@pytest.mark.parametrize(
    "tool,url",
    [
        ("crm_master_brands_list", f"{BRANDS}?page=1&limit=5"),
        ("crm_certificates_list", f"{CERTS}?page=1&limit=5&status=active"),
        ("chat_search_messages", f"{SEARCH}?text=x&limit=5"),
    ],
)
def test_the_tools_listed_params_still_pass(env, tool, url):
    db, client = env
    user = _analyst(db, f"prmok-{uuid.uuid4().hex[:6]}", extra=(CERTS_VIEW,))
    resp = signed(client, "GET", url, user.id, tool=tool)
    assert resp.status_code == 200, resp.text


def test_the_allowlist_is_the_named_tools_not_the_routes_union(env):
    """`directory_id` is a `crm_resource_attachments_list` param, not a `..._catalogue` one; both bind
    the same route."""
    db, client = env
    user = _analyst(db, "prmshared")
    url = f"{ATTACHMENTS}?directory_id={uuid.uuid4()}"
    assert_error(signed(client, "GET", url, user.id, tool="crm_resource_attachments_catalogue"), 403,
                 "param_not_allowed")
    resp = signed(client, "GET", url, user.id, tool="crm_resource_attachments_list")
    assert error_code(resp) not in ("param_not_allowed", "route_not_allowed"), resp.text
    assert resp.status_code != 401, resp.text


# ------------------------------------------------------------------ 1b. tool-route binding


@pytest.mark.parametrize(
    "method,url,body,tool",
    [
        ("GET", BRANDS, None, "chat_list_datasets"),  # a real tool, another route
        ("GET", DATASETS, None, "crm_master_brands_list"),
        ("GET", BRANDS, None, "crm_certificates_list"),
        ("GET", BRANDS, None, "zzt_not_a_tool"),
        ("GET", BRANDS, None, ""),
        ("POST", QUERY, {"dataset": "messages", "limit": 1}, "crm_lookup_resolve"),  # POST tool, other POST
        ("GET", f"{DATASETS}/messages", None, "chat_list_datasets"),  # sibling chat route
    ],
)
def test_a_token_for_one_tool_cannot_call_another_tools_route(env, method, url, body, tool):
    """`req_hash` matches (signed() hashes what it sends); only the `tool` claim is wrong."""
    db, client = env
    user = _analyst(db, f"bind-{uuid.uuid4().hex[:6]}")
    assert_error(signed(client, method, url, user.id, json_body=body, tool=tool), 403, "route_not_allowed")
    assert tool_call_rows(db, user.id) == []


def test_the_matching_tool_claim_passes(env):
    db, client = env
    user = _analyst(db, "bindok")
    assert signed(client, "GET", BRANDS, user.id, tool="crm_master_brands_list").status_code == 200
    assert signed(client, "GET", DATASETS, user.id, tool="chat_list_datasets").status_code == 200
    assert signed(client, "GET", f"{DATASETS}/messages", user.id, tool="chat_describe_dataset").status_code == 200


def test_the_manifest_route_needs_no_tool(env):
    _db, client = env
    resp = signed(client, "GET", f"{BASE}/manifest", "", tool="")
    assert resp.status_code == 200, resp.text


# ------------------------------------------------------------------ 1c. manifest text


def test_no_manifest_tool_mentions_the_dropped_chatbot_params():
    from app.services.analytics_connector.manifest import build_manifest

    for t in build_manifest()["tools"]:
        for word in ("resolve_signed_urls", "contact_id", "space_id"):
            assert word not in t["description"], (t["name"], word)
            assert word not in json.dumps(t["input_schema"]), (t["name"], word)
            assert word not in t["http"]["query_params"], (t["name"], word)


# ------------------------------------------------------------------ 2. ordered canonical query


def test_a_repeated_key_hashed_in_one_order_is_refused_in_the_other(env):
    db, client = env
    user = _analyst(db, "ord")
    tok = call_token(user.id, "GET", BRANDS, "limit=1&limit=500")
    assert_error(signed(client, "GET", f"{BRANDS}?limit=500&limit=1", user.id, token=tok), 401,
                 "invalid_signature")


def test_a_repeated_key_in_the_hashed_order_passes(env):
    db, client = env
    user = _analyst(db, "ordok")
    tok = call_token(user.id, "GET", BRANDS, "limit=1&limit=500")
    resp = signed(client, "GET", f"{BRANDS}?limit=1&limit=500", user.id, token=tok)
    assert resp.status_code == 200, resp.text


# ------------------------------------------------------------------ 3. audit status


def test_a_served_404_is_audited_with_404(env):
    db, client = env
    user = _analyst(db, "a404", extra=(PROJECTS_VIEW,))
    resp = signed(client, "GET", PROJECT.format(uuid.uuid4()), user.id, tool="crm_project_detail")
    assert resp.status_code == 404, resp.text
    rows = tool_call_rows(db, user.id)
    assert len(rows) == 1
    assert rows[0].new_values["status"] == 404, rows[0].new_values


def test_a_served_permission_refusal_is_audited_with_403(env):
    db, client = env
    user = make_user(db, "a403", perms=(USE,))
    resp = signed(client, "GET", BRANDS, user.id, tool="crm_master_brands_list")
    assert_error(resp, 403, "permission_required")
    rows = tool_call_rows(db, user.id)
    assert len(rows) == 1
    assert rows[0].new_values["status"] == 403, rows[0].new_values


def test_every_audited_status_equals_the_response_status(env):
    db, client = env
    user = _analyst(db, "aeq", extra=(PROJECTS_VIEW,))
    statuses = [
        signed(client, "GET", BRANDS, user.id).status_code,
        signed(client, "GET", PROJECT.format(uuid.uuid4()), user.id).status_code,
        signed(client, "GET", f"{DATASETS}/zzt_no_such_dataset", user.id).status_code,
        signed(client, "GET", PROJECT.format("zzt-not-a-uuid"), user.id).status_code,
        signed(client, "GET", f"{BRANDS}?limit=abc", user.id).status_code,
        signed(client, "GET", f"{SEARCH}?limit=abc", user.id).status_code,
    ]
    assert any(c >= 400 for c in statuses), statuses
    rows = tool_call_rows(db, user.id)
    assert len(rows) == len(statuses), (statuses, [r.new_values for r in rows])
    for code, row in zip(statuses, rows):
        expected = "ok" if 200 <= code < 300 else code
        assert row.new_values["status"] == expected, (code, row.new_values)


@pytest.mark.parametrize("code", [401, 409, 410, 413])
def test_a_route_status_the_gate_does_not_translate_is_still_audited_as_that_status(env, monkeypatch, code):
    """A route's own HTTPException outside the section 5 table (here forced on the project detail
    route) reaches the client with its status; the audit row records that status, not 500 or "ok"."""
    from fastapi import HTTPException

    from app.services import project_service

    def _raise(_db, _project_id):
        raise HTTPException(status_code=code, detail="zzt forced")

    monkeypatch.setattr(project_service, "get_project_or_404", _raise)
    db, client = env
    user = _analyst(db, f"a{code}", extra=(PROJECTS_VIEW,))
    resp = signed(client, "GET", PROJECT.format(uuid.uuid4()), user.id, tool="crm_project_detail")
    assert resp.status_code == code, resp.text
    rows = tool_call_rows(db, user.id)
    assert len(rows) == 1
    assert rows[0].new_values["status"] == code, rows[0].new_values


# ------------------------------------------------------------------ 4. body read order + cap


def test_a_garbage_connector_token_with_a_2mb_body_is_401_before_parsing(env):
    """A non-JSON 2 MB body: a 401 (not 422/413) shows the token is refused before body parsing."""
    _db, client = env
    resp = client.post(QUERY, content=b"x" * (2 * 1024 * 1024),
                       headers={"Authorization": "Connector garbage", "Content-Type": "application/json"})
    assert_error(resp, 401, "invalid_signature")


def test_a_body_over_1mb_is_413_even_with_a_valid_token(env):
    db, client = env
    user = _analyst(db, "big")
    body = {"dataset": "messages", "limit": 1, "zzt_pad": "x" * (1024 * 1024 + 1)}
    resp = signed(client, "POST", QUERY, user.id, json_body=body, tool="chat_query")
    assert resp.status_code == 413, resp.text
    assert tool_call_rows(db, user.id) == []


# ------------------------------------------------------------------ 5. auth_method claude_connector dropped


def test_claude_connector_is_not_a_session_auth_method():
    from app.services.user_session_service import AUTH_METHODS

    assert "claude_connector" not in AUTH_METHODS


def test_user_session_check_does_not_allow_claude_connector():
    from sqlalchemy import CheckConstraint

    from app.models.user_session import UserSession

    checks = [str(c.sqltext) for c in UserSession.__table__.constraints if isinstance(c, CheckConstraint)]
    assert any("auth_method" in c for c in checks), checks
    assert not any("claude_connector" in c for c in checks), checks


def test_acc_0001_does_not_alter_the_user_sessions_check():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "acc_0001_connector.py"
    spec = importlib.util.spec_from_file_location("acc_0001_connector_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for fn in (mod.upgrade, mod.downgrade):
        src = inspect.getsource(fn)
        assert "user_sessions" not in src, fn.__name__
        assert "auth_method" not in src, fn.__name__
    assert "claude_connector" not in path.read_text()
