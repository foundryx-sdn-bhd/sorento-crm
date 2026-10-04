"""S6 RED tests - the MCP catalog entry for the low stock report (#892).

`documentation/plans/scm/PLAN-low-stock-report.md` section S6;
`low-stock-report-acceptance-criteria.md` AC-60, AC-62.

Written before the catalog entry exists, so `test_catalog_lists_low_stock_report_tool`
fails with `StopIteration` - no such tool name in `CATALOG` - which is the right reason.

**Why the tool is a GET even though it mints a file.** The compiler injects the opt-in
`view=render` parameter ONLY on presenter tools with no `body_params`
(`server.py::_compile_tool`), and the chatbot lane sends `view=render` on every call - so a
POST-with-body tool would never reach the presenter and the lane would get the raw route
body. `ToolSpec.read_only`'s own docstring already says method is transport, not semantics,
and `crm_portal_link_get` is the standing precedent for a tool that mints an artefact and
sits on the read list.
"""
from __future__ import annotations

import inspect

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.presenters import PRESENTER_TOOLS
from sorento_crm_mcp.server import TOOL_REQUIRED_QUERY_HINTS, _compile_tool

TOOL = "crm_low_stock_report"
REVEAL_PAIR = ("scm.low_stock_report", "Low stock report over chat (staff: full workbook incl. Dealer o/s, PO and SPO numbers)")
QUERY_PARAMS = (
    "warehouse_codes", "product_codes", "date_from", "date_to",
    "contact_id", "space_id",
    # LOWSTOCK-FILTER-ASK: the workbook filters the chat settles before it runs.
    "categories", "suppliers", "split",
    # LOWSTOCK-FILTER-ASK tester finding: a console (dry run) turn never pushes the file.
    "dry_run",
)


def _spec():
    return next(s for s in CATALOG if s.name == TOOL)


# --------------------------------------------------------------------- AC-60


def test_catalog_lists_low_stock_report_tool():
    """AC-60: the whole ToolSpec, field by field.

    `query_params` is asserted as an EXACT tuple rather than a membership check: the
    compiler turns this list into the tool's Python signature, and an extra parameter is
    a surface the LLM will try to fill. Ten is the contract (LOWSTOCK-FILTER-ASK added
    the three workbook filters and `dry_run`).
    """
    spec = _spec()
    assert spec.method == "GET"
    assert spec.path == "/api/v1/scm/low-stock-report"
    assert tuple(spec.query_params) == QUERY_PARAMS, (
        f"exactly the ten query params, in order: {spec.query_params}"
    )
    assert spec.body_params == (), (
        "a body param would stop the compiler injecting `view`, and the chatbot lane "
        "sends view=render on every call"
    )
    assert spec.module == "scm"
    assert spec.domain == "inventory"
    assert spec.escalation_team == "warehouse"


def test_low_stock_tool_declares_the_reveal_key():
    """AC-60 / AC-63: `restricted_fields` is what `mcp_tool_registry_service.sync_catalog`
    copies into `mcp_tools.restricted_fields`, which is what the admin Contacts > Access >
    Field reveals card lists. Static, never read off a live response.
    """
    spec = _spec()
    restricted = getattr(spec, "restricted_fields", None) or ()
    assert tuple(restricted) == (REVEAL_PAIR,), (
        f"{TOOL}'s restricted_fields must be exactly {(REVEAL_PAIR,)}: {restricted}"
    )


def test_low_stock_tool_description_states_the_three_rules():
    """AC-60: the description is the only instruction the LLM gets, and three things about
    this tool are counter-intuitive enough that leaving them out produces wrong calls:
    every call runs a FRESH plan (so it is not a cheap lookup to retry), a location word
    must be resolved to exact codes first (the tool takes codes, not tokens), and both
    contact_id and space_id must travel (the route 422s without them, and the company
    scope resolver reads them).
    """
    description = _spec().description
    lowered = description.lower()
    assert "fresh" in lowered, "the description must say every call runs a fresh plan"
    assert "contact_id" in description and "space_id" in description, (
        "the description must tell the caller to pass both ids"
    )
    assert "COMPANY SCOPE" in description, (
        "the standard COMPANY SCOPE paragraph, verbatim from crm_outstanding_report"
    )


# --------------------------------------------------------------------- AC-62


def test_low_stock_tool_compiles_with_view_param():
    """AC-62: the compiled tool takes `view`, which is the whole reason this is a
    query-param tool (see the module docstring). Asserted on the COMPILED signature, not
    on `PRESENTER_TOOLS` alone, because the injection also depends on `body_params` being
    empty - two conditions, one observable outcome.
    """
    spec = _spec()
    assert TOOL in PRESENTER_TOOLS, (
        "the tool must be a presenter tool or `view` is never injected"
    )
    params = inspect.signature(_compile_tool(spec)).parameters
    assert "view" in params, (
        f"the compiled tool must accept `view`: {sorted(params)}"
    )
    for name in QUERY_PARAMS:
        assert name in params, f"the compiled tool is missing {name}: {sorted(params)}"


def test_contact_and_space_are_required_query_hints():
    """AC-40's other half, declared here: `TOOL_REQUIRED_QUERY_HINTS` is what promotes a
    query param to a no-default argument, so the generated JSON schema marks it
    `required: true`. Without it the LLM reads them as optional and skips them - and the
    route answers 422, which the customer sees as the bot breaking.
    """
    assert TOOL_REQUIRED_QUERY_HINTS.get(TOOL) == ("contact_id", "space_id"), (
        f"got {TOOL_REQUIRED_QUERY_HINTS.get(TOOL)!r}"
    )
