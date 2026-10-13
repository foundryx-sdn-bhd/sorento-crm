"""REPORTS-STEP2 owner hand test: the company question carries its options as a structured field."""
from __future__ import annotations

from sorento_crm_mcp.presenters import _sales_analysis_envelope


def test_h37_the_company_question_hands_on_its_options() -> None:
    out = _sales_analysis_envelope({"status": "clarify", "message": "Zorvex or Mochi?", "companies": ["Zorvex", "Mochi"]})
    assert out["response"] == "Zorvex or Mochi?" and out["company_options"] == ["Zorvex", "Mochi"], out


def test_h37b_a_clarify_without_options_carries_none() -> None:
    out = _sales_analysis_envelope({"status": "clarify", "message": "Which one?"})
    assert not out.get("company_options"), out
