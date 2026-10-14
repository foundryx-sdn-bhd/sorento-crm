"""PV2 redesign R2, fix round 1 (security review of a28a30f2b + code review notes). Red tests, no model call.

F1  a refused turn leaves no pending / origin / held; an origin replay is re-judged against the grants.
F2  a malformed or schema-drifted origin / last_lookup_ask is never trusted (the old `deferred` fallback runs).
F3  `last_lookup_ask` does not go stale; a refine raised from a v1 picker reply does not inherit the old origin.
F4  a granted contact's report ask with an ambiguous customer runs for the picked customer (pre-R2 did).
F5  register labels are the printed identifiers.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tests.chatbot.pv2 import _register_envelopes as E  # loads the worktree MCP first

from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE
from app.services.chatbot.pv2 import register, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask, Escalation, Filter, Period, ReportSpec
from app.services.chatbot.pv2.state import (
    ClarifyAct,
    Lookup,
    Option,
    PendingQuestion,
    dump_state,
    new_state,
    roll_session,
)
from tests.chatbot.pv2.carry_fakes import (
    NOW,
    FakeResolver,
    ask,
    entity,
    held as mk_held,
    mk_state,
    out,
)

SALES_GRANT = "sales_orders.sales_report"
PO_GRANT = "purchase_orders.placed"
LAST_MONTH = Period(preset="last_month", n=None, date_from=None, date_to=None)


# ------------------------------------------------------------------ shared setups


class ReportResolver(FakeResolver):
    """What `DbResolver._report_filter` does: the customer phrase is ambiguous every time, `held` ignored."""

    def resolve_report_spec(self, spec, *, held, profile=None):
        opts = [{"key": f"cust-{i}", "label": f"TAN TRADING {i} SDN BHD",
                 "payload": {"kind": "customer", "key": f"cust-{i}", "label": f"TAN TRADING {i} SDN BHD",
                             "dimension": "customer"}} for i in (1, 2, 3)]
        return SimpleNamespace(status="ambiguous", options=opts, refs={}, phrase="tan", unresolved=[])


def _report_spec() -> ReportSpec:
    return ReportSpec(dataset=None, measures=["revenue"], group_by=[], top_n_per_group=None,
                      filters=[Filter(dimension="customer", op="in", keys=["tan"], values=[])],
                      period=LAST_MONTH, compare_to=None, compare_period=None, grain=None, threshold=None,
                      sort=None, top_n=None, basis=None)


REPORT_ASK = out(ask(act="new_topic", domain=None, report=_report_spec()))
PICK = out(ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None}))
CANDIDATES = ("TAN TRADING 1", "TAN TRADING 2", "TAN TRADING 3")


def _amb(kind, labels):
    return {"status": "ambiguous", "options": [
        {"key": str(i), "label": l, "payload": {"kind": kind, "key": l, "label": l}} for i, l in enumerate(labels, 1)]}


PO_CFG = {"entities": {"product|mou": _amb("product", ["MOU-1", "MOU-2"]),
                       "supplier|abc": _amb("supplier", ["ABC CERAMICS CO", "ABC FAUCET LTD"])}}
PO_ASK = out(ask(act="new_topic", domain="purchase_order",
                 entities=[entity("product", phrase="mou"), entity("supplier", phrase="abc")]))
PO_CANDIDATES = ("MOU-1", "MOU-2", "ABC CERAMICS", "ABC FAUCET")


def _run(parsed, raw_state, *, audience, granted=(), resolver):
    calls = []
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=raw_state, profile=MemoryProfile(audience=audience),
        parse=lambda m, u: parsed, resolver=resolver, load_exchanges=lambda s: [],
        report_answer=lambda *a, **k: calls.append(a), granted_reveals=frozenset(granted))
    result = turn._run(ctx)
    return result, calls


def _reply(result) -> str:
    from app.services.chatbot.pv2.answer import reply_for

    if result.direct_reply:
        return result.direct_reply
    return "\n".join(filter(None, (reply_for(a, "en") for a in result.actions)))


def _same_subject(a, b):
    """What a refused turn must leave untouched (turn_no and timestamps move with the turn)."""
    return (a.held, a.pending, a.topics, a.last_lookup_ask, a.register) == (b.held, b.pending, b.topics,
                                                                           b.last_lookup_ask, b.register)


# ------------------------------------------------------------------ F1 sales report grant


def test_f1a_a_refused_report_turn_leaves_the_state_as_it_was():
    before = new_state(NOW)
    result, _ = _run(REPORT_ASK, dump_state(before), audience="dealer", resolver=ReportResolver({}))
    assert result.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE
    assert result.state.pending is None, "the refused ask's question must not be kept"
    assert result.state.last_lookup_ask is None
    assert _same_subject(result.state, before), result.state


def test_f1b_the_follow_up_pick_after_a_refused_report_names_no_candidate():
    first, _ = _run(REPORT_ASK, dump_state(new_state(NOW)), audience="dealer", resolver=ReportResolver({}))
    second, calls = _run(PICK, dump_state(first.state), audience="dealer", resolver=ReportResolver({}))
    assert not any(c in _reply(second) for c in CANDIDATES), _reply(second)
    assert second.verdict is None and calls == []


def test_f1c_a_pending_saved_with_the_grant_is_refused_once_the_grant_is_gone():
    first, _ = _run(REPORT_ASK, dump_state(new_state(NOW)), audience="dealer", granted=[SALES_GRANT],
                    resolver=ReportResolver({}))
    assert first.state.pending is not None and first.state.pending.kind == "clarify_entity"
    second, calls = _run(PICK, dump_state(first.state), audience="dealer", resolver=ReportResolver({}))
    assert second.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE
    assert not any(c in _reply(second) for c in CANDIDATES)
    assert second.verdict is None and calls == []


# ------------------------------------------------------------------ F1 purchase order grant (staff)


def _po_refused(result) -> bool:
    return result.verdict is None and not any(c in _reply(result) for c in PO_CANDIDATES) and bool(result.direct_reply)


def test_f1d_a_refused_po_turn_leaves_the_state_as_it_was():
    before = new_state(NOW)
    result, _ = _run(PO_ASK, dump_state(before), audience="staff", resolver=FakeResolver(PO_CFG))
    assert result.direct_reply and "purchase order" in result.direct_reply.lower()
    assert result.state.pending is None and result.state.last_lookup_ask is None
    assert _same_subject(result.state, before), result.state


def test_f1d_the_follow_up_pick_after_a_refused_po_ask_names_no_candidate():
    first, _ = _run(PO_ASK, dump_state(new_state(NOW)), audience="staff", resolver=FakeResolver(PO_CFG))
    second, _ = _run(PICK, dump_state(first.state), audience="staff", resolver=FakeResolver(PO_CFG))
    assert second.verdict is None
    assert not any(c in _reply(second) for c in PO_CANDIDATES), _reply(second)


def test_f1d_a_po_pending_saved_with_the_grant_is_refused_once_the_grant_is_gone():
    first, _ = _run(PO_ASK, dump_state(new_state(NOW)), audience="staff", granted=[PO_GRANT],
                    resolver=FakeResolver(PO_CFG))
    assert first.state.pending is not None
    second, _ = _run(PICK, dump_state(first.state), audience="staff", resolver=FakeResolver(PO_CFG))
    assert _po_refused(second), (second.verdict, _reply(second))


def test_f1e_a_granted_staff_contact_picks_through_a_po_question_as_before():
    cfg = {"entities": {"product|mou": PO_CFG["entities"]["product|mou"]}}
    one = out(ask(act="new_topic", domain="purchase_order", entities=[entity("product", phrase="mou")]))
    first, _ = _run(one, dump_state(new_state(NOW)), audience="staff", granted=[PO_GRANT], resolver=FakeResolver(cfg))
    assert first.state.pending is not None and first.verdict is None
    second, _ = _run(PICK, dump_state(first.state), audience="staff", granted=[PO_GRANT], resolver=FakeResolver(cfg))
    assert second.verdict is not None and second.verdict.get("domain_hint") == "purchase_order"


# ------------------------------------------------------------------ F2 malformed origin


def _pending_with(origin) -> PendingQuestion:
    return PendingQuestion(
        kind="clarify_entity", asked='Which "mou" do you mean?', asked_turn=3,
        options=[Option(key="1", label="SRTWC286",
                        payload={"key": "SRTWC286", "kind": "product", "label": "SRTWC286", "op": "set"})],
        origin=origin, deferred={"domain": "incoming", "attachment_types": [], "quantity": None})


def _pick_on(origin):
    st = mk_state(mk_held("inventory", {"product": ["OTHER1"]}), pending_=_pending_with(origin))
    return apply_turn(st, PICK, resolver=FakeResolver({}), now=NOW)


def test_f2_an_origin_with_an_unknown_key_falls_back_to_deferred():
    good = ask(act="new_topic", domain="incoming").model_dump(mode="json")
    after, actions = _pick_on({**good, "field_from_a_later_schema": 1})
    assert isinstance(actions[0], Lookup)
    assert after.held.domain == "incoming" and [r.key for r in after.held.refs["product"]] == ["SRTWC286"]


def test_f2_an_origin_missing_a_required_field_falls_back_to_deferred():
    after, actions = _pick_on({"act": "new_topic"})
    assert isinstance(actions[0], Lookup)
    assert after.held.domain == "incoming" and [r.key for r in after.held.refs["product"]] == ["SRTWC286"]


def test_f2_a_malformed_last_lookup_ask_is_not_adopted_by_the_v1_picker_mirror():
    from app.services.chatbot.turn import pending as v1p

    opts = [{"code": "C1", "label": "X-V1", "position": 1, "entity_type": "product"}]
    v1 = v1p.ask("product_pick", opts, team="purchasing", expects=None, payload={}, asked_at_turn=3)
    st = mk_state(mk_held("incoming", {"product": ["X"]}))
    st.last_lookup_ask = {"act": "new_topic", "bogus": 1}
    mirrored = turn.mirror_v1_pending(st, v1, last_reply="Which product?\n1. X-V1")
    assert mirrored.pending is not None and mirrored.pending.origin is None


# ------------------------------------------------------------------ F3 stale last_lookup_ask


def _with_last_ask() -> "ConversationState":
    st = mk_state(mk_held("incoming", {"product": ["SRTWC286"]}))
    st.last_lookup_ask = ask(act="new_topic", domain="incoming").model_dump(mode="json")
    return st


def test_f3_a_session_roll_clears_last_lookup_ask():
    st = _with_last_ask()
    rolled = roll_session(st, st.last_turn_at + timedelta(hours=5))
    assert rolled.last_lookup_ask is None


def test_f3_an_escalation_turn_clears_last_lookup_ask():
    parsed = out(ask(act="small_talk")).model_copy(update={"escalation": Escalation(requested=True, team_key="sales")})
    after, actions = apply_turn(_with_last_ask(), parsed, resolver=FakeResolver({}), now=NOW)
    assert actions[0].type == "hand_over"
    assert after.last_lookup_ask is None


def test_f3_a_flagged_clarify_turn_clears_last_lookup_ask():
    parsed = out(ask(act="unclear"), clarify={"needed": True, "about": "other", "phrase": None})
    after, actions = apply_turn(_with_last_ask(), parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(actions[0], ClarifyAct)
    assert after.last_lookup_ask is None


def test_f3_a_refine_raised_from_a_v1_picker_reply_does_not_inherit_the_old_origin():
    old = ask(act="new_topic", domain="incoming").model_dump(mode="json")
    st = mk_state(mk_held("incoming", {"product": ["A1"]}), pending_=PendingQuestion(
        kind="v1_open_question", asked="Which product?", asked_turn=3, v1_kind="product_pick",
        options=[Option(key="1", label="A1-V1", payload={"kind": "choice", "key": "1"})], origin=old))
    new = ask(act="answers_bot_question", domain="order", answer={"option_key": None, "free_value": None},
              entities=[entity("product", phrase="mou")])
    after, actions = apply_turn(st, out(new), resolver=FakeResolver({"entities": {"product|mou": _amb("product", ["M1", "M2"])}}),
                                now=NOW)
    assert isinstance(actions[0], ClarifyAct)
    assert after.pending.origin != old, "the new subject's question must not carry the earlier ask"
    assert Ask.model_validate(after.pending.origin) == new


# ------------------------------------------------------------------ F4 a granted report pick runs (pre-R2 941fb867a: it did)


def test_f4_an_ambiguous_customer_report_runs_for_the_picked_customer():
    first, a1 = apply_turn(new_state(NOW), REPORT_ASK, resolver=ReportResolver({}), now=NOW)
    assert isinstance(a1[0], ClarifyAct) and first.pending.kind == "clarify_entity"
    second, a2 = apply_turn(first, PICK, resolver=ReportResolver({}), now=NOW)
    assert isinstance(a2[0], Lookup), "the pick must run the report, not ask the same question again"
    report = a2[0].held.report
    keys = [k for f in report.filters if f.dimension == "customer" for k in f.keys]
    refs = [r.key for r in a2[0].held.refs.get("customer", [])]
    assert "cust-1" in keys or "cust-1" in refs
    assert "tan" not in keys, "the ambiguous phrase is replaced by the picked customer"


# ------------------------------------------------------------------ F5 printed labels


def test_f5_a_stock_ask_entry_is_labelled_with_the_code_the_reply_printed():
    entry = {"product_id": "33333333-3333-4333-8333-333333333333", "product_code": "SRTWC286",
             "product_name": "Wall Mixer Chrome 150", "needs_quantity": False, "requested_qty": 5,
             "branch": "in_stock", "cap_unset": None, "category_name": "Category", "eta": None,
             "packing_list": None, "is_discontinued": False}
    raw = {"data": [], "pagination": {"total": 0, "page": 1, "limit": 50}, "empty": True,
           "stock_visibility": {"mode": "availability", "warehouse_codes": ["BRW"], "source": "access_type"},
           "stock_availability": [entry], "last_updated_at": "2026-08-24T18:00:00"}
    printed = [i["title"] for i in json.loads(E.present_response(E.STOCK_TOOL, json.dumps(raw)))["items"]]
    assert printed and "SRTWC286 x 5" in printed[0], printed
    env = E.envelope(E.STOCK_TOOL, raw, domain="inventory")
    entries = register.entries_from_envelopes([env], 3)
    assert entries, "the stock-ask line was printed"
    for e in entries:
        assert e.label in printed[0], (e.label, printed[0])
    assert "Wall Mixer" not in json.dumps([e.model_dump() for e in entries])


from tests.chatbot.pv2.test_core_engine_hook import FakeResult, fake_turn  # noqa: E402,F401 - fixture
from tests.chatbot.test_engine import CONTACT_ID, _parser_output, seeded  # noqa: E402,F401
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact  # noqa: E402


def test_f5_an_entities_only_entry_is_labelled_with_the_token_the_reply_printed(session_factory, monkeypatch, fake_turn):
    typed = "srtwt7445"
    db = session_factory()
    _seed_contact(session_factory, variables={})
    db.execute(text("update respond_contacts set chatbot_parser_mode = 'v2' where respond_io_id = :c"),
               {"c": str(CONTACT_ID)})
    db.commit()
    entities = [{"raw": typed, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}]
    qf = _parser_output(asks=None, topic_reset=None, domain_hint=None, intent_hint=None, entities=entities)
    fake_turn(FakeResult(verdict=qf, state=mk_state()))
    matches = {typed: {"uuid": "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa", "entity_type": "product",
                       "canonical_code": "SRTWT7445"}}
    result, _ = _run_turn(session_factory, monkeypatch, qf=qf, text_body=typed,
                          msg_id=f"ZZT-r2f1-{uuid.uuid4().hex[:8]}", matches=matches)
    reply = (result.reply or {}).get("text") or ""
    row = session_factory().execute(
        text("select session_vars from respond_contacts where respond_io_id = :c"), {"c": str(CONTACT_ID)}).first()
    raw = row.session_vars
    saved = (json.loads(raw) if isinstance(raw, str) else raw)["pv2"]["register"]
    assert saved and reply
    for e in saved:
        assert e["label"] in reply, (e["label"], reply)
