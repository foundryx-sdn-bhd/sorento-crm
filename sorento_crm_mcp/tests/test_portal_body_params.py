"""crm_portal_link_get takes discrete body fields, not a wrapped payload_json blob.

Required: contact_id, space_id. Optional: submission_type, base_url. And scalar
string body values (numeric-looking ids) must NOT be coerced to numbers.
"""
import asyncio
import json

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.server import _compile_tool


def _spec(name):
    return next(s for s in CATALOG if s.name == name)


def test_portal_spec_uses_direct_body_fields():
    spec = _spec("crm_portal_link_get")
    assert "payload_json" not in spec.body_params
    assert set(spec.body_params) == {"contact_id", "space_id", "submission_type", "base_url"}


def test_portal_impl_signature_required_and_optional():
    impl = _compile_tool(_spec("crm_portal_link_get"))
    import inspect

    params = inspect.signature(impl).parameters
    # required (no default)
    assert params["contact_id"].default is inspect._empty
    assert params["space_id"].default is inspect._empty
    # optional (default None)
    assert params["submission_type"].default is None
    assert params["base_url"].default is None
    assert "payload_json" not in params


def test_numeric_string_id_not_coerced_to_int():
    """The body decoder only JSON-parses object/array strings; a bare numeric
    string id stays a string so the backend's str field accepts it."""
    spec = _spec("crm_portal_link_get")
    impl = _compile_tool(spec)

    captured = {}

    class _Client:
        async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
            captured["body"] = body
            captured["method"] = method
            return json.dumps({"token": "X", "portal_url": "http://x", "expires_at": "2026-01-01"})

    class _Ctx:
        class request_context:
            class lifespan_context(dict):
                pass
        def __init__(self):
            self.request_context = type("RC", (), {"lifespan_context": {"client": _Client(), "settings": type("S", (), {"crm_base_url": "http://x", "external_api_key": "k"})()}})()

    asyncio.run(impl(_Ctx(), contact_id="900000008", space_id="364817"))
    assert captured["method"] == "POST"
    assert captured["body"]["contact_id"] == "900000008"  # still a string
    assert captured["body"]["space_id"] == "364817"
    assert "submission_type" not in captured["body"]  # omitted optional dropped
