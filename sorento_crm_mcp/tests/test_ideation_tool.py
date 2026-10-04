"""AC-307 (second half): the ideation tool's registration, schema and request.

`crm_ideation_turn` is an `external=True` catalog spec, which means `create_mcp_app`
SKIPS the generic HTTP compile for it - a spec with no handler is a tool the server
never lists and every call fails "unknown tool". These tests hold three things:

1. it is actually registered on a real `FastMCP` (the whole catalog, not a fake), so a
   missing `register_ideation_tools` call is a red test rather than a dead lane;
2. `session_vars` is an OBJECT in the published schema. The generic template types every
   body param as `str`, and the ideation pointer is a nested object carried turn to turn;
3. the request it builds - path, method, and the optional arguments OMITTED rather than
   sent as null, because the endpoint reads an absent `media_selection` as "no media menu
   answer in this turn".
"""
from __future__ import annotations

import pytest

from sorento_crm_mcp.ideation import register_ideation_tools
from sorento_crm_mcp.server import create_mcp_app
from sorento_crm_mcp.settings import Settings

_PATH = "/api/v1/external/ideation/turn"


class _FakeSettings:
    crm_base_url = "http://crm.local"
    external_api_key = "test-key"


class _RecordingClient:
    """Captures the request the handler builds and returns a canned response."""

    def __init__(self, response: str = '{"status": "collecting"}') -> None:
        self.calls: list[dict] = []
        self._response = response

    async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "path_params": path_params,
                "body": body,
                "tool_name": tool_name,
            }
        )
        return self._response


class _FakeRC:
    def __init__(self, client):
        self.lifespan_context = {"client": client, "settings": _FakeSettings()}


class _FakeCtx:
    def __init__(self, client):
        self.request_context = _FakeRC(client)


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict = {}
        self.descriptions: dict = {}

    def add_tool(self, fn, name=None, description=None):
        self.tools[name] = fn
        self.descriptions[name] = description


def _register(response: str = '{"status": "collecting"}'):
    mcp = _FakeMCP()
    register_ideation_tools(mcp, _FakeSettings())
    client = _RecordingClient(response)
    return mcp, _FakeCtx(client), client


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


async def test_the_server_lists_the_ideation_tool():
    app = create_mcp_app(Settings(CRM_BASE_URL="http://crm.local", EXTERNAL_API_KEY="k"))
    names = [t.name for t in await app.list_tools()]
    assert "crm_ideation_turn" in names, (
        "crm_ideation_turn is external=True, so create_mcp_app skips the HTTP compile - "
        "it only appears when register_ideation_tools runs"
    )
    assert len(names) == len(set(names)), "a tool was registered twice"


async def test_the_description_comes_from_the_catalog_spec():
    from sorento_crm_mcp.catalog import CATALOG

    spec = next(s for s in CATALOG if s.name == "crm_ideation_turn")
    mcp, _, _ = _register()
    assert mcp.descriptions["crm_ideation_turn"] == spec.description


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


async def test_session_vars_is_an_object_and_the_two_ids_are_required():
    app = create_mcp_app(Settings(CRM_BASE_URL="http://crm.local", EXTERNAL_API_KEY="k"))
    tool = next(t for t in await app.list_tools() if t.name == "crm_ideation_turn")
    schema = tool.inputSchema

    assert sorted(schema["required"]) == ["message_text", "respond_io_id"]
    session_vars = schema["properties"]["session_vars"]
    types = {branch.get("type") for branch in session_vars.get("anyOf", [session_vars])}
    assert "object" in types, (
        "session_vars must be an object: the generic body-param template types it `str`, "
        "which would make the caller JSON-encode the ideation pointer by hand"
    )
    for optional in (
        "session_vars",
        "submitter_name",
        "media_selection",
        "is_new_idea",
        "is_test",
        "ask_reply",
    ):
        assert schema["properties"][optional].get("default", "missing") is None
    assert schema["properties"]["is_new_idea"] is not None


# --------------------------------------------------------------------------- #
# The request
# --------------------------------------------------------------------------- #


async def test_a_first_turn_posts_the_two_required_fields_only():
    mcp, ctx, client = _register()
    out = await mcp.tools["crm_ideation_turn"](
        ctx, respond_io_id="42", message_text="i have an idea"
    )
    assert out == '{"status": "collecting"}'
    (call,) = client.calls
    assert call["method"] == "POST"
    assert call["path"] == _PATH
    assert call["tool_name"] == "crm_ideation_turn"
    assert call["body"] == {"respond_io_id": "42", "message_text": "i have an idea"}


async def test_the_ideation_pointer_travels_as_an_object():
    mcp, ctx, client = _register()
    pointer = {"ideation": {"draft_id": "abc", "pending_media": True}}
    await mcp.tools["crm_ideation_turn"](
        ctx,
        respond_io_id="42",
        message_text="1,3",
        session_vars=pointer,
        submitter_name="Ali",
        media_selection="1,3",
    )
    (call,) = client.calls
    assert call["body"]["session_vars"] == pointer
    assert call["body"]["submitter_name"] == "Ali"
    assert call["body"]["media_selection"] == "1,3"


@pytest.mark.parametrize(
    "omitted",
    ["session_vars", "submitter_name", "media_selection", "is_new_idea", "is_test", "ask_reply"],
)
async def test_an_unset_optional_is_omitted_never_sent_as_null(omitted):
    mcp, ctx, client = _register()
    await mcp.tools["crm_ideation_turn"](ctx, respond_io_id="42", message_text="hi")
    (call,) = client.calls
    assert omitted not in call["body"], (
        f"{omitted} must be absent, not null: the endpoint reads an absent media_selection "
        "as 'no media menu answer this turn', and the n8n node omits it for that reason"
    )


async def test_is_new_idea_travels_as_a_boolean():
    mcp, ctx, client = _register()
    await mcp.tools["crm_ideation_turn"](
        ctx, respond_io_id="42", message_text="different idea", is_new_idea=True
    )
    (call,) = client.calls
    assert call["body"]["is_new_idea"] is True


@pytest.mark.parametrize("flag", [True, False])
async def test_is_test_travels_as_a_boolean(flag):
    """#1179 AC-9: the chatbot's dry run sends true, a live turn sends false; both reach
    the endpoint as the boolean they were given."""
    mcp, ctx, client = _register()
    await mcp.tools["crm_ideation_turn"](
        ctx, respond_io_id="42", message_text="an idea", is_test=flag
    )
    (call,) = client.calls
    assert call["body"]["is_test"] is flag


@pytest.mark.parametrize("flag", [True, False])
async def test_ask_reply_travels_as_a_boolean(flag):
    """IDEATION-CAPTURE: the ideate lane marks the answer to its "what's your idea?" ask, so a
    second message with no idea ends the ask instead of asking again."""
    mcp, ctx, client = _register()
    await mcp.tools["crm_ideation_turn"](
        ctx, respond_io_id="42", message_text="want to submit idea", ask_reply=flag
    )
    (call,) = client.calls
    assert call["body"]["ask_reply"] is flag


async def test_a_numeric_contact_id_is_coerced_to_a_string():
    mcp, ctx, client = _register()
    await mcp.tools["crm_ideation_turn"](ctx, respond_io_id=99, message_text="hi")
    (call,) = client.calls
    assert call["body"]["respond_io_id"] == "99"


async def test_a_backend_error_propagates_verbatim():
    err = '{"detail": "shared service unavailable", "status_code": 503}'
    mcp, ctx, client = _register(response=err)
    out = await mcp.tools["crm_ideation_turn"](ctx, respond_io_id="42", message_text="hi")
    assert out == err
    assert len(client.calls) == 1
