"""answer.build_result routing (CONTRACT-PV2-13oct.md section 5)."""
from __future__ import annotations

from datetime import date

from app.services.chatbot.pv2 import answer, clarify_copy
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import ReportSpec
from app.services.chatbot.pv2.state import (
    ClarifyAct,
    HandOver,
    Held,
    Lookup,
    OfferHandover,
    Option,
    SmallTalk,
)
from tests.chatbot.pv2.carry_fakes import FakeResolver, ask, entity, held, mk_state, option, out, pending

TODAY = date(2026, 10, 13)
OPTS = [option("dim_length", "Length"), option("dim_height", "Height")]


def _clarify(i=0, reask=False, asked="Which 600 mm do you mean?"):
    return ClarifyAct(ask_index=i, about="spec_attribute", pending=pending("clarify_spec_attribute", OPTS, asked=asked), reask=reask)


def _lookup(i, domain, h, **kw):
    return Lookup(ask_index=i, domain=domain, held=h, **kw)


def _build(actions, state=None, o=None, **kw):
    state = state or mk_state(held("inventory", {"product": ["SRTWC286"]}))
    o = o or out(*[ask() for _ in actions])
    return answer.build_result(state, actions, o, today=TODAY, **kw)


def _spec():
    return ReportSpec(dataset="sales", measures=["amount"], group_by=["customer"], top_n_per_group=None, filters=[],
                      period=None, compare_to=None, compare_period=None, grain=None, threshold=None, sort=None,
                      top_n=10, basis="delivered")


def test_clarify_is_a_direct_reply_with_no_verdict():
    r = _build([_clarify()])
    assert r.route == "clarify" and r.verdict is None and r.focus is None
    assert r.direct_reply == clarify_copy.render(_clarify().pending, "en")
    assert "Length" in r.direct_reply and "dim_length" not in r.direct_reply


def test_reask_prefix_present_on_a_miss():
    r = _build([_clarify(reask=True)])
    assert r.direct_reply.endswith(clarify_copy.render(_clarify().pending, "en"))
    assert r.direct_reply != clarify_copy.render(_clarify().pending, "en")


def test_clarify_beats_a_lookup_in_the_same_message():
    h = held("inventory", {"product": ["A"]})
    r = _build([_lookup(0, "inventory", h), _clarify(1)])
    assert r.route == "clarify" and r.verdict is None


def test_offer_handover_is_a_direct_reply_with_yes_no_labels():
    q = pending("offer_handover", clarify_copy.handover_options("en"), asked=clarify_copy.text("offer_handover", "en"))
    r = _build([OfferHandover(ask_index=0, pending=q)])
    assert r.route == "clarify" and r.verdict is None
    assert "Yes" in r.direct_reply and "No" in r.direct_reply


def test_language_of_the_parser_picks_the_copy():
    r = _build([_clarify()], o=out(ask(), language="zh"))
    assert clarify_copy.text("reply_number", "zh") in r.direct_reply


def test_hand_over_is_a_request_for_help_verdict_with_the_team():
    r = _build([HandOver(ask_index=0, team_key="sales")])
    assert r.route == "hand_over" and r.direct_reply is None
    assert r.verdict["message_type"] == "request_for_help"
    assert r.verdict["routing"]["suggested_team"] == "sales"


def test_hand_over_beats_a_lookup():
    h = held("inventory", {"product": ["A"]})
    r = _build([_lookup(0, "inventory", h), HandOver(ask_index=1, team_key=None)])
    assert r.verdict["message_type"] == "request_for_help"


def test_plain_lookup_is_verdict_plus_focus():
    h = held("inventory", {"product": ["SRTWC286"]})
    r = _build([_lookup(0, "inventory", h)])
    assert r.route == "lookup" and r.direct_reply is None
    assert r.verdict["domain_hint"] == "inventory"
    assert r.verdict["entities"][0]["canonical_code"] == "SRTWC286"
    assert [x["canonical_code"] for x in r.focus.products] == ["SRTWC286"]


def test_multi_lookup_lists_the_asks_in_order():
    h = held("inventory", {"product": ["SRTWC286"]})
    r = _build([_lookup(0, "incoming", held("incoming", {"product": ["SRTWC286"]})), _lookup(1, "inventory", h)])
    assert [a["domain"] for a in r.verdict["asks"]] == ["incoming", "inventory"]
    assert [a["intent"] for a in r.verdict["asks"]] == ["check_incoming", "check_stock"]


def test_small_talk_is_a_casual_verdict():
    r = _build([SmallTalk(ask_index=0)])
    assert r.route == "small_talk" and r.direct_reply is None
    assert r.verdict["message_type"] == "casual"


class _Rep:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_report_lookup_calls_the_injected_report_answer_and_lands_pending_and_rows():
    calls = []
    spec = _spec()
    new_spec = spec.model_copy(update={"top_n": 5})
    rows = [option("1", "Ahmad Trading"), option("2", "Beta Hardware")]

    def report_answer(*args):
        calls.append(args)
        return _Rep(text="Top customers", plan=_Rep(spec=new_spec, status="ok"), rows=rows,
                    pending={"kind": "clarify_report_measure", "asked": "Which figure?",
                             "options": [option("amount", "Amount")]})

    h = Held(domain="sales", refs={"customer": []}, report=spec)
    h = held("sales", {"customer": ["C1"]}, report=spec)
    inc = ask(act="refines_held", report=spec)
    row = option("1", "Ahmad Trading")
    state = mk_state(h)
    r = answer.build_result(state, [_lookup(0, "sales", h, ask=inc, row=row)], out(inc), today=TODAY,
                            report_answer=report_answer)
    assert len(calls) == 1
    got_spec, refs, today, got_row, incoming = calls[0]
    assert got_spec == spec and refs == {"customer": ["C1"]} and today == TODAY
    assert got_row == row and incoming == spec
    assert r.route == "report" and r.direct_reply == "Top customers" and r.verdict is None
    assert r.state.pending.kind == "clarify_report_measure" and [o.key for o in r.state.pending.options] == ["amount"]
    assert [o.label for o in r.state.last_rows] == ["Ahmad Trading", "Beta Hardware"]
    assert r.state.held.report == new_spec
    assert state.pending is None and state.last_rows == []  # input state untouched


def test_report_without_pending_clears_pending():
    spec = _spec()
    h = held("sales", {"brand": ["SORENTO"]}, report=spec)
    r = answer.build_result(
        mk_state(h, pending_=pending("clarify_other", [])), [_lookup(0, "sales", h, ask=ask())], out(ask()),
        today=TODAY, report_answer=lambda *a: _Rep(text="ok", plan=None, rows=[], pending=None))
    assert r.state.pending is None


def test_idea_lookup_calls_idea_answer_and_records_the_draft():
    calls = []

    def idea_answer(lookup, pending_in, language, turn_no):
        calls.append((lookup, pending_in, language, turn_no))
        return _Rep(reply_text="Saved your idea", pending=None, idea_draft_id="draft-1", status="saved")

    h = held("ideate")
    state = mk_state(h, turn_no=7)
    lk = _lookup(0, "ideate", h, ask=ask())
    r = answer.build_result(state, [lk], out(ask(), language="ms"), today=TODAY, idea_answer=idea_answer)
    assert calls[0][0] is lk and calls[0][2] == "ms" and calls[0][3] == 7
    assert r.route == "idea" and r.direct_reply == "Saved your idea" and r.verdict is None
    assert r.state.held.idea_draft_id == "draft-1"


def test_idea_answer_returning_none_falls_through_to_a_verdict():
    h = held("ideate")
    r = answer.build_result(mk_state(h), [_lookup(0, "ideate", h, ask=ask())], out(ask()), today=TODAY,
                            idea_answer=lambda *a: None)
    assert r.verdict is not None and r.direct_reply is None


def test_ungated_domain_flag_in_trace():
    def flag(*domains):
        acts = [_lookup(i, d, held(d)) for i, d in enumerate(domains)]
        return _build(acts).trace["pv2_ungated_domain"]

    assert flag("inventory") is False
    assert flag("product_attachment") is False
    assert flag("promotion") is True
    assert flag("inventory", "promotion") is True
    assert _build([SmallTalk(ask_index=0)]).trace["pv2_ungated_domain"] is False


def test_replay_reply_is_the_question_copy_only():
    h = held("inventory", {"product": ["A"]})
    assert answer.replay_reply(mk_state(h), [_lookup(0, "inventory", h)]) == ""
    text = answer.replay_reply(mk_state(h), [_clarify()])
    assert "Which 600 mm do you mean?" in text and "dim_length" not in text
    both = answer.replay_reply(mk_state(h), [_clarify(0, asked="First?"), _clarify(1, asked="Second?")])
    assert "First?" in both and "Second?" in both


def test_two_questions_in_one_message_reply_matches_the_recorded_pending():
    """What the user is asked must be what the state expects an answer to."""
    cfg = {"entities": {
        "customer|Ahmad": {"status": "ambiguous", "options": [
            {"key": "c1", "label": "Ahmad Trading", "payload": {"kind": "customer", "key": "C1", "label": "Ahmad Trading"}},
            {"key": "c2", "label": "Ahmad Hardware", "payload": {"kind": "customer", "key": "C2", "label": "Ahmad Hardware"}}]},
        "brand|Mo": {"status": "ambiguous", "options": [
            {"key": "b1", "label": "Mocha", "payload": {"kind": "brand", "key": "MOCHA", "label": "Mocha"}},
            {"key": "b2", "label": "Moka Pot", "payload": {"kind": "brand", "key": "MOKA", "label": "Moka Pot"}}]}}}
    o = out(ask(act="new_topic", domain="sales", entities=[entity("customer", phrase="Ahmad")]),
            ask(act="new_topic", domain="sales", entities=[entity("brand", phrase="Mo")]))
    state, actions = apply_turn(mk_state(), o, resolver=FakeResolver(cfg))
    assert state.pending is not None
    r = answer.build_result(state, actions, o, today=TODAY)
    assert r.direct_reply is not None
    for opt in state.pending.options:
        assert opt.label in r.direct_reply, "the recorded question was never shown to the user"
