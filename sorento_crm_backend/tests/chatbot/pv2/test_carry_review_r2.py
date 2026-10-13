"""Reviewer findings on the delta 17711b20c..06fb1dede (PV2-CARRY follow-ups, 14 Oct).

M1  a drilled report row is never re-resolved by its display label (two customers who share a
    name made "the 2nd one" ask `Which "Tan Motor" do you mean?` with two identical options);
S1  the plan that runs after a drill is guarded (a drilled spec missing a policy field asks);
S2  `_run_from_hook` hands `run_idea_turn` the contact's first name as `submitter_name`;
N1  the history cap says when it cut inside a line, and a blank-only reply is empty.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.chatbot.pv2 import answer, reports
from app.services.chatbot.pv2.apply import apply_turn, report_refs
from app.services.chatbot.pv2.history import REPLY_CHAR_CAP, compact_reply
from app.services.chatbot.pv2.schema import ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Held, Lookup, Option
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, mk_state, out
from tests.chatbot.pv2.test_carry_review_r1 import _hook, _report

TODAY = NOW.date()
ID_1 = "0a8e5c3d-1111-4a2b-9c3d-4e5f6a7b8c9d"
ID_2 = "0a8e5c3d-2222-4a2b-9c3d-4e5f6a7b8c9d"
TAN = Option(
    key="Tan Motor",
    label="Tan Motor",
    payload={reports.ROW_PAYLOAD: {"dimension": "customer", "keys": [ID_1, ID_2], "label": "Tan Motor"}},
)


class NameResolver(FakeResolver):
    """Answers report filters the way `DbResolver._report_filter` does for a customer name
    two rows share: ambiguous, two same-named options. Records every spec it is asked."""

    def __init__(self):
        super().__init__({})
        self.report_specs: list[ReportSpec] = []

    def resolve_report_spec(self, spec, *, held, profile=None):
        self.report_specs.append(spec)
        if any(f.dimension == "customer" for f in spec.filters):
            opts = [{"key": k, "label": "Tan Motor", "payload": {"kind": "customer", "key": k}} for k in (ID_1, ID_2)]
            return reports.ReportResolution(status="ambiguous", options=opts, phrase="Tan Motor")
        return reports.ReportResolution(status="exact")


def _drill_turn(resolver, held_report, *, incoming=None):
    state = mk_state(Held(domain="sales", report=held_report), last_rows=[TAN])
    o = out(ask(act="refines_held", domain="sales", row_pick=1, report=incoming))
    return apply_turn(state, o, resolver=resolver, now=NOW)


def test_m1_a_drilled_row_is_not_re_resolved_by_its_label():
    resolver = NameResolver()
    state, actions = _drill_turn(resolver, _report(group_by=["customer"]))
    assert isinstance(actions[0], Lookup), actions[0]
    assert [r.key for r in state.held.refs["customer"]] == [ID_1, ID_2]
    assert all(f.dimension != "customer" for spec in resolver.report_specs for f in spec.filters)
    plan = reports.plan_report(state.held.report, refs=report_refs(state.held), today=TODAY)
    assert plan.status == "run" and plan.header.startswith("*Read as:*")


def test_m1_holds_when_the_drill_turn_also_names_a_new_breakdown():
    resolver = NameResolver()
    incoming = _report(dataset=None, measures=[], group_by=["brand"], period=None, top_n=None, basis=None)
    state, actions = _drill_turn(resolver, _report(group_by=["customer"]), incoming=incoming)
    assert isinstance(actions[0], Lookup), actions[0]
    assert state.held.report.group_by == ["brand"]
    assert [r.key for r in state.held.refs["customer"]] == [ID_1, ID_2]
    assert all(f.dimension != "customer" for spec in resolver.report_specs for f in spec.filters)


def test_s1_a_drilled_spec_missing_a_policy_field_asks_after_the_drill():
    # No measure held (the ranking ruling asks it, never defaults it): the post-drill plan
    # asks the one combined question rather than answering.
    state, actions = _drill_turn(NameResolver(), _report(group_by=["customer"], measures=[], basis=None))
    assert isinstance(actions[0], ClarifyAct) and actions[0].about == "report_measure"
    assert [r.key for r in state.held.refs["customer"]] == [ID_1, ID_2]


def test_s2_run_from_hook_sends_the_contact_first_name_as_submitter(monkeypatch):
    seen = {}

    def fake_default_idea_answer(db, **kw):
        seen.update(kw)
        return lambda *a, **k: None

    monkeypatch.setattr(answer, "default_idea_answer", fake_default_idea_answer)
    _hook(monkeypatch, SimpleNamespace(ingress="console", is_test=True, contact={"id": 7, "firstName": "Ahmad"}))
    assert seen["submitter_name"] == "Ahmad" and seen["respond_io_id"] == "r-1"


def test_n1_a_cut_inside_one_line_says_so_and_blank_only_is_empty():
    short = compact_reply("x" * 5_000)
    assert short.startswith("x" * REPLY_CHAR_CAP)
    assert short.endswith("[... rest of this line and 0 more lines not shown]")
    assert compact_reply("\n" * 600) == ""
    assert compact_reply("  \n \n" * 200) == ""
