"""Ensure every catalog entry compiles to a Context-aware tool.

MCP tools are NOT access-gated at this layer - access control lives in the n8n
flow, not the MCP server. So compiled tools take only their declared
path/query/body params; there is no contact_id/space_id guard injection.
"""
from __future__ import annotations

import pytest

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.scope import is_scoped
from sorento_crm_mcp.server import (
    TOOL_REQUIRED_NARROWING_FILTERS,
    TOOL_REQUIRED_QUERY_HINTS,
    _compile_tool,
)


class _FakeSettings:
    crm_base_url = "http://crm.local"
    external_api_key = "test-key"


class _FakeRC:
    def __init__(self, client):
        self.lifespan_context = {"client": client, "settings": _FakeSettings()}


class _FakeCtx:
    def __init__(self, client):
        self.request_context = _FakeRC(client)


class _FakeClient:
    async def get(self, path, path_params=None, query=None, tool_name=None):
        # Include is_active=true so any promotion-activity precheck treats the row as active.
        return (
            f'{{"path":"{path}","ok":true,"is_active":true,'
            f'"query":{list((query or {}).keys())!r}}}'
        )

    async def post(self, path, path_params=None, query=None, body=None, tool_name=None):
        return f'{{"path":"{path}","ok":true,"method":"POST"}}'

    async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
        return (
            f'{{"path":"{path}","ok":true,"is_active":true,'
            f'"method":"{method}","query":{list((query or {}).keys())!r}}}'
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", list(CATALOG))
async def test_tool_runs_with_fake_context(spec):
    fn = _compile_tool(spec)
    _PP_UUID = "11111111-1111-4111-8111-111111111111"
    kwargs: dict[str, str] = {p: _PP_UUID for p in spec.path_params}
    # SEC-MCP-AUTH: a company-scoped tool refuses a call without the calling contact.
    if is_scoped(spec):
        kwargs.setdefault("contact_id", "900000008")
        kwargs.setdefault("space_id", "364817")
    for b in spec.body_params:
        kwargs.setdefault(b, "{}")
    # Parent-relation tools (orders_by_product, incoming_stock_by_product, GRN,
    # promotion_products, promotion_attachments, product_attachments) short-circuit
    needed = TOOL_REQUIRED_NARROWING_FILTERS.get(spec.name)
    if needed:
        kwargs.setdefault(needed[0], _PP_UUID)
    # A query param in TOOL_REQUIRED_QUERY_HINTS is promoted to a NO-DEFAULT argument by
    # `_compile_tool` (that is the whole point - the generated JSON schema then marks it
    # `required: true` so the LLM stops skipping it), so calling the tool without it is a
    # TypeError, not a tool that returns an empty page. The table was an empty dict until
    # `crm_low_stock_report` used it, which is why this loop had no reason to exist
    # before.
    for q in TOOL_REQUIRED_QUERY_HINTS.get(spec.name, ()):
        kwargs.setdefault(q, "x")
    out = await fn(_FakeCtx(_FakeClient()), **kwargs)  # type: ignore[arg-type]
    assert spec.path in out
    assert "ok" in out


def test_catalog_covers_all_prefix_domains():
    """Smoke: catalog is non-empty and names are unique."""
    names = [s.name for s in CATALOG]
    assert len(names) == len(set(names))
    assert len(CATALOG) >= 20


def test_orders_list_uses_actual_delivery_date_only():
    """orders_list exposes ONLY `actual_delivery_date_from` / `_to` for date
    filtering. Whatever timeframe the user gives is always translated to
    actual_delivery_date - no order_date params.
    """
    spec = next(s for s in CATALOG if s.name == "crm_order_management_orders_list")
    desc = spec.description
    assert "actual_delivery_date_from" in desc
    assert "actual_delivery_date_to" in desc
    assert "order_date_from" not in desc
    assert "order_date_to" not in desc
    assert "order_date_from" not in spec.query_params
    assert "order_date_to" not in spec.query_params
    assert "actual_delivery_date_from" in spec.query_params
    assert "actual_delivery_date_to" in spec.query_params


def test_no_freetext_query_on_data_list_tools():
    """UUID-first contract: entity list tools must not expose a fuzzy `query`
    param. (user_guides_read is a documentation search and lookup_resolve takes
    `raw` - those are not entity filters and are exempt.)
    """
    exempt = {"user_guides_read", "crm_lookup_resolve"}
    for spec in CATALOG:
        if spec.name in exempt:
            continue
        assert "query" not in spec.query_params, f"{spec.name} still exposes free-text query"


def test_project_tools_are_read_only():
    """AC-K2. No write-capable project tool ships in v1.

    AI-assisted quotation editing is a later slice with its own grill: a confirm-gate, a diff
    preview, version semantics and price-floor enforcement on AI-written lines are each a
    decision. A tool that let the agent POST a price before those exist would be the fastest
    route to a quotation nobody agreed to.
    """
    project_tools = [s for s in CATALOG if s.module == "projects"]
    assert project_tools, "the project tools disappeared from the catalog"
    for spec in project_tools:
        assert spec.method == "GET", f"{spec.name} is not read-only"
        assert not spec.body_params, f"{spec.name} takes a body"


def test_project_list_filters_are_uuid_or_stable_key():
    """The list tool must not tempt the model into free-text matching.

    `status_key` is the one non-UUID filter and that is deliberate: `key` is the documented
    stable identity per entity_type (grill finding G3), so "tendering" is an identifier, not
    a search term.
    """
    spec = next(s for s in CATALOG if s.name == "crm_projects_list")
    assert "project_ids" in spec.query_params
    assert "owner_user_ids" in spec.query_params
    assert "developer_party_ids" in spec.query_params
    assert "status_key" in spec.query_params
    assert "query" not in spec.query_params
    assert "title" not in spec.query_params
    assert "developer_name" not in spec.query_params


def test_the_forecast_tool_refuses_to_offer_a_total():
    """The three numbers are never blended (AC-I1), and the tool description is where that
    rule reaches the model. If the description ever starts describing a total, an agent will
    happily add them up in prose and the report becomes fiction."""
    spec = next(s for s in CATALOG if s.name == "crm_project_forecast")
    assert "NEVER BE ADDED TOGETHER" in spec.description
    assert "SPECULATIVE" in spec.description
    assert "undated" in spec.description
def test_product_attachments_accepts_certificate_ids_as_a_narrower():
    """`certificate_ids` must be BOTH a query param and a recognised narrowing
    key. Listed as one but not the other, "the files for this certificate"
    returns an empty page while the filter itself works - a silent wrong answer,
    not an error.
    """
    spec = next(s for s in CATALOG if s.name == "crm_master_product_attachments_list")
    assert "certificate_ids" in spec.query_params
    assert "certificate_ids" in TOOL_REQUIRED_NARROWING_FILTERS[spec.name]
    # And the agent has to be told, or it will never pass it.
    assert "certificate_ids" in spec.description


@pytest.mark.asyncio
async def test_certificate_ids_alone_reaches_the_backend():
    """Not short-circuited: the request must actually carry the filter."""
    spec = next(s for s in CATALOG if s.name == "crm_master_product_attachments_list")
    fn = _compile_tool(spec)
    out = await fn(
        _FakeCtx(_FakeClient()),
        contact_id="900000008",
        space_id="364817",
        certificate_ids="11111111-1111-4111-8111-111111111111",
    )  # type: ignore[arg-type]
    assert "certificate_ids" in out


def test_contact_id_alone_is_not_a_narrowing_filter():
    """Asking for nothing must return nothing.

    `contact_id` scopes the answer to that contact's entitlements, which bounds
    the result to a handful of files - but a document request has to name a
    DOCUMENT. Returning everything a contact may have is a directory listing, not
    an answer, so the contact is a scope and never the filter.
    """
    spec = next(s for s in CATALOG if s.name == "crm_resource_attachments_list")
    assert "contact_id" in spec.query_params, "still accepted, as a scope"
    assert "contact_id" not in TOOL_REQUIRED_NARROWING_FILTERS[spec.name]


def test_resource_attachments_type_filter_takes_singular_and_plural():
    """Both forms are accepted, and BOTH count as a narrower.

    A plural form missing from the narrowing list is worse than one missing from
    query_params: the filter reaches the backend but the tool short-circuits to a
    silent empty page first.
    """
    spec = next(s for s in CATALOG if s.name == "crm_resource_attachments_list")
    for param in (
        "attachment_type_id",
        "attachment_type_ids",
        "attachment_type_code",
        "attachment_type_codes",
    ):
        assert param in spec.query_params
        assert param in TOOL_REQUIRED_NARROWING_FILTERS[spec.name]


@pytest.mark.asyncio
async def test_attachment_type_ids_alone_reaches_the_backend():
    spec = next(s for s in CATALOG if s.name == "crm_resource_attachments_list")
    fn = _compile_tool(spec)
    out = await fn(
        _FakeCtx(_FakeClient()),
        contact_id="900000008",
        space_id="364817",
        attachment_type_ids="11111111-1111-4111-8111-111111111111,22222222-2222-4222-8222-222222222222",
    )  # type: ignore[arg-type]
    assert "attachment_type_ids" in out


@pytest.mark.asyncio
async def test_attachment_type_codes_alone_reaches_the_backend():
    spec = next(s for s in CATALOG if s.name == "crm_resource_attachments_list")
    fn = _compile_tool(spec)
    out = await fn(
        _FakeCtx(_FakeClient()),
        contact_id="900000008",
        space_id="364817",
        attachment_type_codes="Container Status,catalogue",
    )  # type: ignore[arg-type]
    assert "attachment_type_codes" in out


@pytest.mark.asyncio
async def test_contact_id_alone_is_short_circuited():
    """No document named -> empty page, and the backend is never called."""
    spec = next(s for s in CATALOG if s.name == "crm_resource_attachments_list")
    fn = _compile_tool(spec)
    out = await fn(_FakeCtx(_FakeClient()), contact_id="rio_10532f", space_id="364817")  # type: ignore[arg-type]
    assert "rio_10532f" not in out
