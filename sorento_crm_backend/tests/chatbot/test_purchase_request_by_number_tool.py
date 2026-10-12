"""REF-PROBES AC-19: MCP tool `crm_purchase_request_by_number` is a catalogue read (`read_only`
True or GET) that is also in the chatbot's frozen read-only allow-list.

Lives under tests/chatbot/ because it imports `app.services.chatbot`, which
`test_import_boundary.py` forbids outside the module boundary. The route tests stay in
tests/test_purchase_request_by_number_route.py.
"""
from __future__ import annotations

from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS
from app.services.mcp_tool_capability_service import _load_catalog_specs

TOOL = "crm_purchase_request_by_number"


def test_mcp_tool_is_a_catalogue_read_and_in_the_chatbot_allow_list():
    specs = {s.name: s for s in _load_catalog_specs()}
    assert TOOL in specs, f"{TOOL} missing from the MCP catalogue"
    spec = specs[TOOL]
    assert str(spec.method).upper() == "GET" or spec.read_only is True
    assert "purchase-requests/by-number" in str(spec.path)
    assert TOOL in CHATBOT_READ_ONLY_TOOLS
