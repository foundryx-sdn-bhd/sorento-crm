"""Phase 2 RED tests - REPORTS-STEP2 S3a review round (lane half): several asks count DISTINCT domains
(finding 3), and the unsupported-topic line uses the message's detected language first (finding 4)."""
from __future__ import annotations

from tests.chatbot import test_reports_step2_s5a as s5a
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact
from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401  (autouse fixture by name)
from tests.reports_step2.test_s3a_shapes import COPY


def _reply(result) -> str:
    return (result.reply or {}).get("text") or ""


def test_review3_two_asks_in_the_same_domain_keep_the_single_ask_scope_question(session_factory, monkeypatch):
    """Two asks, one domain: not a multi-domain message, so an outstanding ask naming no document is
    asked which one, exactly as a single ask is, and no report runs."""
    _seed_contact(session_factory, variables={})
    result, calls = _run_turn(
        session_factory, monkeypatch,
        qf=_qf(order_status="outstanding", document=[], status="outstanding",
               asks=[{"domain": "order", "intent": "check_order"}, {"domain": "order", "intent": "check_order"}]),
        text_body="SRTWT7445 outstanding and again outstanding", msg_id="ZZT-s3a-rev3",
        attributes=s5a.OUT_ATTRS, matches=s5a.PRODUCT_MATCH, mcp_response=REPORT_HIT)
    assert not [c for c in calls if c[0] == "crm_report"], calls
    assert "Outstanding for which document?" in _reply(result), _reply(result)


def test_review4_a_first_contact_writing_in_chinese_gets_the_zh_unsupported_line(session_factory, monkeypatch):
    """Nothing remembered (no saved language, no earlier turn): the language is the message's own."""
    _seed_contact(session_factory, variables={})
    qf = _parser_output(message_type="business_query", intent_hint=None, domain_hint="receivables", entities=[],
                        order_status=None, status=None,
                        escalation={"is_escalation_confirmation": False, "company_pick": None})
    result, calls = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body="我的逾期款项是多少", msg_id="ZZT-s3a-rev4",
        attributes=[], mcp_response=REPORT_HIT)
    assert calls == [], calls
    assert _reply(result).strip() == COPY["not_supported_receivables"][1], _reply(result)
