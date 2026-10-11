"""Phase 2 RED tests - REPORTS-STEP2 S3a live diagnosis, the lane halves: rpt-011 (a raw `outstanding`
order_status never projected through `document`) and rpt-032 (the receivables line for the emission the
improved prompt produces). Same harness as `test_reports_step2_s3a_lane.py`.
"""
from __future__ import annotations

import pytest

from tests.chatbot import test_reports_step2_s5a as s5a
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact
from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401  (autouse fixture by name)
from tests.reports_step2.test_s3a_shapes import COPY


def _reply(result) -> str:
    return (result.reply or {}).get("text") or ""


@pytest.mark.parametrize("document, scope", [(["SO"], "so"), (["DO"], "do")])
def test_rpt011_a_raw_outstanding_status_is_projected_through_the_document(
    session_factory, monkeypatch, document, scope
):
    """Emission: order_status "outstanding", `document` naming one document, `status` null, a product
    entity. The document axis decides the scope; the customer is not asked "which document"."""
    _seed_contact(session_factory, variables={})
    result, calls = _run_turn(
        session_factory, monkeypatch,
        qf=_qf(order_status="outstanding", document=document, status=None),
        text_body=f"SRTWT7445 {scope} outstanding", msg_id=f"ZZT-s3a-live-011-{scope}",
        attributes=s5a.OUT_ATTRS, matches=s5a.PRODUCT_MATCH, mcp_response=REPORT_HIT)
    sent = [c for c in calls if c[0] == "crm_report"]
    assert sent, (calls, _reply(result))
    assert sent[0][1].get("scope") == scope, sent[0][1]
    assert "which document" not in _reply(result).lower(), _reply(result)


@pytest.mark.parametrize("language, index", [(None, 0), ("zh", 1), ("ms", 2)])
def test_rpt032_message_type_unknown_with_the_receivables_domain_gets_the_receivables_line(
    session_factory, monkeypatch, language, index
):
    """Emission the improved prompt produces: message_type "unknown", domain_hint "receivables", no
    entities, no status. The line is the topic's, in the turn language, with no tool call."""
    _seed_contact(session_factory, variables={"reply_language": language} if language else {})
    qf = _parser_output(message_type="unknown", intent_hint=None, domain_hint="receivables", entities=[],
                        order_status=None, status=None,
                        escalation={"is_escalation_confirmation": False, "company_pick": None})
    result, calls = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body="what is my overdue balance",
        msg_id=f"ZZT-s3a-live-032-{language}", attributes=[], mcp_response=REPORT_HIT)
    assert calls == [], calls
    assert _reply(result).strip() == COPY["not_supported_receivables"][index], _reply(result)
