"""Phase 2 RED tests - the `crm_report_ask` tool text carries the ONE top N ceiling (REPORT-ENGINE).

Owner ruling 30 Sep 2026 ("remove the cap"): the backend's single ceiling is
`sales_report_service.TOP_SELLING_N_CEILING` (1000). This package cannot import the backend, so the
number is a literal here, pinned the same way `presenters.TOP_SELLING_N_CEILING` is (a copy).
"""
from __future__ import annotations

from sorento_crm_mcp.catalog import CATALOG

CEILING = 1000


def _description() -> str:
    return next(s for s in CATALOG if s.name == "crm_report_ask").description


def test_the_tool_text_no_longer_says_1_to_100():
    text = _description()
    assert "1 to 100" not in text, "the old cap is still in the tool description"
    assert "1-100" not in text and "1..100" not in text, text


def test_the_tool_text_names_the_ceiling():
    text = _description()
    assert f"{CEILING}" in text or f"{CEILING:,}" in text, "the tool description never names the 1000 ceiling"


def test_the_tool_text_does_not_advertise_a_default_basis_or_measure_and_says_to_ask():
    """Owner ruling 4 Oct 2026: rankings clarify, never default. The text tells the caller to ask."""
    import re

    text = " ".join(_description().split())
    basis = re.search(r"`basis`[^.]*", text)
    assert basis is not None, text
    assert "default" not in basis.group(0).lower(), basis.group(0)
    measure = re.search(r"`measure`.*?\(what the ranking sorts by\)", text)
    assert measure is not None and "default" not in measure.group(0).lower(), text
    assert re.search(r"ask[^.]*\bbasis\b|\bbasis\b[^.]*\bask", text, re.IGNORECASE), text
    assert re.search(r"ask[^.]*\bmeasure\b|\bmeasure\b[^.]*\bask", text, re.IGNORECASE), text
