"""H58 / AC-813: the chatbot's tool allow-list, pinned to the real MCP catalogue.

The chatbot picks one tool per turn by cosine similarity over the shared `mcp_tool`
embedding pool and calls the single top hit (`lanes/business/fetch.py::tool_filter`), so
any tool it can retrieve is a tool a customer's phrasing can cause to be CALLED. Six write
tools live in that pool: `crm_complaint_close`, `crm_order_cancel`,
`crm_purchase_request_approve`, `crm_purchase_request_reject`,
`crm_it_support_ticket_create` and `crm_ideation_turn`.

**They stay in the pool.** The in-app AI assistant retrieves from the same pool and
`record_action_bootstrap` puts the four record actions there deliberately so its Tool-RAG
can find them; it gates each behind a user confirmation (`_is_write_tool`) and a permission
check. Filtering the pool would have taken the assistant's write tools away from it. The
read-only rule belongs to the CHATBOT, which has no user to confirm with, and it is applied
on the chatbot's own retrieval and call seams.

**The allow-list is a frozen set in the backend, not a walk of the catalogue.** The
deployed backend image has no copy of `sorento_crm_mcp` (compose builds it with
`context: ./sorento_crm_backend`, `mcp` is not in `requirements.txt`, no volume mounts it),
so a catalogue read on the turn path would raise in every container and `run_fetch`'s broad
`except` would turn every live business turn into "MCP tool X failed".

This file is where the frozen set and the catalogue are held together. It imports the
catalogue, which IS available in CI and in a checkout, and asserts the set equals the
catalogue's own answer, so drift in either direction fails here rather than in production.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS
from app.services.mcp_tool_capability_service import (
    _load_catalog_specs,
    build_capability_documents,
)

# The six the audit found. Named, so a regression says WHICH one came back rather than just
# that a count moved, and so flipping one to `read_only=True` in the catalogue cannot pass
# review by accident.
WRITE_TOOLS = (
    "crm_it_support_ticket_create",
    "crm_complaint_close",
    "crm_order_cancel",
    "crm_purchase_request_approve",
    "crm_purchase_request_reject",
    "crm_ideation_turn",
)

# The POST tools that only READ. Each carries `read_only=True` in the catalogue with a
# comment saying why. They are in the chatbot's allow-list on purpose: method is transport,
# and their bodies exist because the input does not fit in a query string.
READ_ONLY_POST_TOOLS = (
    "crm_lookup_resolve",
    "crm_portal_link_get",
    "user_guides_read",
)


# Catalogue READS the chatbot deliberately does NOT hold, with the reason. A read tool
# that cannot be called correctly is worse than none: it wins the semantic pick on the
# phrasing it was written for and then answers empty. Each row names the condition for
# putting it back.
UNCALLABLE_READS = {
    # 8 Sep 2026 (turns 87694182 / 0b10a4c0 / 11932963): `metric` is a required query
    # param and the fetch lane never maps one from the parser. Back in when it does.
    "crm_order_analytics": "requires `metric`; the lane never sends it",
}


def _catalog_by_name() -> dict[str, Any]:
    return {spec.name: spec for spec in _load_catalog_specs()}


def _reads(spec: Any) -> bool:
    """The catalogue's own answer: a GET, or a POST the spec declares `read_only`."""
    return str(getattr(spec, "method", "GET")).upper() == "GET" or bool(
        getattr(spec, "read_only", False)
    )


def test_the_allow_list_equals_the_catalogues_own_answer() -> None:
    """THE guardrail. The frozen set exists because the container has no catalogue; this is
    what keeps it honest, and it fails in CI where the catalogue is readable.

    Both directions are checked and both matter: a name in the catalogue but missing from
    the set is a read tool the chatbot silently stopped being able to use, and a name in the
    set but no longer a read in the catalogue is a WRITE the chatbot would still call.
    """
    catalog_reads = {s.name for s in _load_catalog_specs() if _reads(s)}
    for name in UNCALLABLE_READS:
        assert name in catalog_reads, f"{name} is no longer a catalogue read; drop its UNCALLABLE_READS row"
        assert name not in CHATBOT_READ_ONLY_TOOLS, f"{name} is back in the pool; drop its UNCALLABLE_READS row"

    missing = sorted(catalog_reads - CHATBOT_READ_ONLY_TOOLS - set(UNCALLABLE_READS))
    extra = sorted(CHATBOT_READ_ONLY_TOOLS - catalog_reads)

    assert not missing, (
        "the MCP catalogue declares these tools READ but "
        "fetch.CHATBOT_READ_ONLY_TOOLS does not list them, so the chatbot can no longer "
        f"use them: {', '.join(missing)}"
    )
    assert not extra, (
        "fetch.CHATBOT_READ_ONLY_TOOLS lists these, but the MCP catalogue no longer "
        "declares them reads - the chatbot would call a tool that can WRITE: "
        f"{', '.join(extra)}"
    )


def test_the_catalogue_still_carries_writing_tools() -> None:
    """The guard's own precondition: if nothing in the catalogue could write, every
    exclusion test below would pass for the wrong reason. Asserted rather than assumed, so
    a catalogue change that removed the write tools announces itself instead of quietly
    turning them into tautologies."""
    writers = [s.name for s in _load_catalog_specs() if not _reads(s)]

    assert writers, (
        "the MCP catalogue no longer holds a single writing tool - the exclusion tests in "
        "this file now prove nothing; retire them or re-point them"
    )


@pytest.mark.parametrize("tool_name", WRITE_TOOLS)
def test_the_six_write_tools_are_named_and_refused(tool_name: str) -> None:
    """By name, and asserted NOT flagged read-only in the catalogue either.

    A future edit that added `read_only=True` to one of these would be declaring that
    cancelling an order is a read, and the equality test above would then happily follow it
    into the allow-list. This is the case that says no.
    """
    catalog = _catalog_by_name()
    spec = catalog.get(tool_name)
    assert spec is not None, f"{tool_name} is no longer in the MCP catalogue - retire this case"
    assert not getattr(spec, "read_only", False), (
        f"{tool_name} is declared read_only=True in sorento_crm_mcp/catalog.py. It WRITES; "
        "the flag is for a POST whose body is transport, never for a side effect."
    )

    assert tool_name not in CHATBOT_READ_ONLY_TOOLS


@pytest.mark.parametrize("tool_name", WRITE_TOOLS)
def test_the_write_tools_stay_in_the_embedding_pool(tool_name: str) -> None:
    """The other half of the ruling, and the one a well-meaning fix breaks.

    The pool is SHARED with the in-app AI assistant, whose Tool-RAG only ever sees what is
    embedded (`ai_assistant_service._rag_select_tools`), and `record_action_bootstrap`
    exists solely to make these retrievable for it. Filtering the pool would take the
    assistant's write tools away while doing nothing the chatbot-side allow-list does not
    already do.
    """
    embedded = {doc.source_key for doc in build_capability_documents(include_planned=False)}

    assert tool_name in embedded, (
        f"{tool_name} is no longer embedded, so the in-app AI assistant can never retrieve "
        "it. The read-only rule is the CHATBOT's and belongs on its retrieval seam "
        "(lanes/business/services.py::_tool_search), not on the shared pool."
    )


@pytest.mark.parametrize("tool_name", READ_ONLY_POST_TOOLS)
def test_a_post_tool_that_declares_read_only_is_allowed(tool_name: str) -> None:
    """The reason the rule is not "method == GET".

    These three take a POST body because their input does not fit in a query string. They
    have no customer-visible side effect, and excluding them would cost the chatbot three
    useful reads for a transport detail.
    """
    catalog = _catalog_by_name()
    spec = catalog.get(tool_name)
    assert spec is not None, f"{tool_name} is no longer in the MCP catalogue - retire this case"
    assert str(spec.method).upper() != "GET", (
        f"{tool_name} is a GET now, so it proves nothing about the read_only flag - "
        "re-point this case at a POST tool that only reads"
    )
    assert spec.read_only is True

    assert tool_name in CHATBOT_READ_ONLY_TOOLS


def test_the_turn_path_never_reads_the_catalogue() -> None:
    """B2, as a source scan: `sorento_crm_mcp` is not in the deployed backend image, so an
    import of it from the chatbot package or the external API would raise in every
    container and take every live business turn down through `run_fetch`'s broad `except`.

    **Two doors, not one.** `mcp_tool_capability_service` is banned here as well, because
    it is the back way to the same place: `_load_catalog_specs` inside it is what actually
    imports the package, so a turn-path file that reaches for that module has the identical
    failure with none of the obvious spelling. It is a perfectly good import everywhere
    else - the seed script and the embedding backfill both use it - just never on a path a
    customer's message runs down.

    A source scan rather than a mock, for `test_import_boundary.py`'s reason: an importer
    that no test happens to execute is still an importer. It matches IMPORTS, not the name:
    naming either module in a comment (this rule's own explanation lives in one) is fine,
    and the ways in are the same ones that file enumerates.
    """
    import re
    from pathlib import Path

    banned = r"(?:sorento_crm_mcp|mcp_tool_capability_service)"
    import_re = re.compile(
        rf"^\s*from\s+[\w.]*\b{banned}\b"
        rf"|^\s*import\s+[\w.]*\b{banned}\b"
        rf"|^\s*from\s+[\w.]+\s+import\s+(?:[^\n]*\b)?{banned}\b"
        rf"|import_module\(\s*['\"][\w.]*{banned}"
        rf"|__import__\(\s*['\"][\w.]*{banned}",
        re.MULTILINE,
    )
    backend_root = Path(__file__).resolve().parents[2]
    roots = [
        backend_root / "app" / "services" / "chatbot",
        backend_root / "app" / "api" / "v1" / "external",
    ]
    offenders = [
        str(path.relative_to(backend_root))
        for root in roots
        for path in root.rglob("*.py")
        if import_re.search(path.read_text(encoding="utf-8"))
    ]

    assert not offenders, (
        "these turn-path files reach the MCP catalogue - directly, or through "
        "mcp_tool_capability_service, which loads it. The package does not exist in the "
        "deployed backend image (compose builds it with context: ./sorento_crm_backend), "
        "so every live business turn would fail: " + ", ".join(sorted(offenders))
    )


def test_reports_step2_ac9_crm_report_is_on_the_chatbot_allow_list_and_the_old_six_stay():
    """REPORTS-STEP2 AC-9: the new tool is callable by the chatbot; no old report tool is lost."""
    assert "crm_report" in CHATBOT_READ_ONLY_TOOLS
    for old in (
        "crm_report_ask", "crm_sales_report", "crm_top_selling_report", "crm_outstanding_report",
        "crm_low_stock_report", "crm_sales_analysis",
    ):
        assert old in CHATBOT_READ_ONLY_TOOLS, old
