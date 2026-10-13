"""PV2-FIX r2 batch F: the engine half of the hand-over fix, and the null-dataset sales grant."""
from __future__ import annotations

from tests.chatbot.pv2.test_core_engine_hook import (  # noqa: F401 - fixtures used by name
    FakeResult,
    _set_contact_mode,
    fake_turn,
)
from tests.chatbot.test_engine import (  # noqa: F401
    _envelope,
    _parser_output,
    _turn_row,
    seeded,
    stub_access,
    stub_parser,
)

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.escalation import ROUTED_TO_PIC_REPLY


def _escalation_lane(monkeypatch):
    def _fake(ctx, item, *, dry_run, session_factory):
        return {
            "arm": "assign",
            "actions": [
                {"kind": "assign_conversation", "respond_user_id": "zzt-round-robin", "dry_run": dry_run},
                {"kind": "send_message", "text": ROUTED_TO_PIC_REPLY, "quick_replies": None,
                 "result_set": [], "dry_run": dry_run},
            ],
            "pending": None,
        }

    monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake)


def _hand_over_result(route):
    verdict = _parser_output(message_type="request_for_help", intent_hint=None, domain_hint=None,
                             entities=[], user_goal="talk to a person",
                             asks=None, topic_reset=None)
    return FakeResult(verdict=verdict, trace={"index_sha": "abc12345", "version": 3,
                                              "pv2_turn": {"route": route}})


def _run(session_factory, monkeypatch, route):
    _escalation_lane(monkeypatch)
    _set_contact_mode(session_factory, "v2")
    return engine_mod.run_turn(_envelope(), session_factory=session_factory)


def test_a_pv2_hand_over_turn_replies_with_the_escalation_sentence(
    session_factory, seeded, stub_parser, stub_access, fake_turn, monkeypatch
):
    fake_turn(_hand_over_result("hand_over"))
    stub_parser(error=AssertionError("v1 parser must not run on a v2 turn"))
    stub_access()
    result = _run(session_factory, monkeypatch, "hand_over")
    assert result.reply["text"] == ROUTED_TO_PIC_REPLY
    row = _turn_row(session_factory, result.turn_id)
    assert row.response["reply"]["text"] == ROUTED_TO_PIC_REPLY


def test_a_non_hand_over_escalation_turn_keeps_v1s_blank_reply_text(
    session_factory, seeded, stub_parser, stub_access, fake_turn, monkeypatch
):
    fake_turn(_hand_over_result("lookup"))
    stub_parser(error=AssertionError("v1 parser must not run on a v2 turn"))
    stub_access()
    result = _run(session_factory, monkeypatch, "lookup")
    assert not ((result.reply or {}).get("text") or "").strip()
    assert any(a.get("kind") == "send_message" and a.get("text") == ROUTED_TO_PIC_REPLY for a in result.actions)


# ------------------------------------------------------------------ null-dataset sales report grant


def test_a_sales_report_with_a_null_dataset_is_refused_without_the_grant():
    from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE
    from app.services.chatbot.pv2 import turn
    from app.services.chatbot.pv2.history import MemoryProfile
    from app.services.chatbot.pv2.state import dump_state
    from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask as _base_ask, mk_state, out
    from app.services.chatbot.pv2.schema import Ask

    report = {"dataset": None, "measures": ["amount"], "group_by": ["product"], "top_n_per_group": None,
              "filters": [], "period": None, "compare_to": None, "compare_period": None, "grain": None,
              "threshold": None, "sort": None, "top_n": None, "basis": None}
    base = _base_ask().model_dump()
    base.update(domain="sales", report=report)
    parsed = out(Ask.model_validate(base))
    calls = []
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="staff"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [],
        report_answer=lambda *a, **k: calls.append(a), granted_reveals=frozenset(),
    )
    r = turn._run(ctx)
    assert r.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE
    assert calls == []

