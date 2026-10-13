"""PV2-FIX r3 batch 2 (review): quoted media text whole, report filter only in report domains,
live team and refer wording, family through v1's gate, decimal picks. Red tests."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, dump_state

from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask as _base_ask, held as mk_held, mk_state, out
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e
from tests.chatbot.pv2.test_core_engine_hook import fake_turn  # noqa: F401 - fixture
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401 - fixtures


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


# ------------------------------------------------------------------ 1 quoted / media text


def _env(inner_text=None, reply_to=None):
    from tests.chatbot.test_engine import _envelope

    env = _envelope()
    inner = env.message["message"]["message"]
    if inner_text is None:
        inner.pop("text", None)
    else:
        inner["text"] = inner_text
    env.message["message"].pop("replyTo", None)
    if reply_to:
        env.message["message"]["replyTo"] = {"message": {"text": reply_to}}
    return env


def test_a_media_turn_with_a_quoted_reply_keeps_every_code_line():
    from app.services.chatbot import engine as engine_mod

    body = "stock?\nSRTWC286\nSRTWC6003"
    env = _env(reply_to="Which product?")
    assert engine_mod._pv2_text(env, body) == body


def test_only_the_builders_reply_to_line_is_removed():
    from app.services.chatbot import engine as engine_mod

    env = _env(reply_to="Which product?")
    assert engine_mod._pv2_text(env, "stock?\nSRTWC286\nreply to: Which product?\n") == "stock?\nSRTWC286"


def test_a_quoted_body_with_newlines_is_removed_whole():
    from app.services.chatbot import engine as engine_mod

    env = _env(reply_to="Line A\nLine B")
    assert engine_mod._pv2_text(env, "stock?\nreply to: Line A\nLine B\n") == "stock?"


# ------------------------------------------------------------------ 2 brand filter only in report domains


def test_a_brand_in_a_stock_question_is_not_a_report_filter():
    h = mk_held("sales")
    h.report = ReportSpec.model_validate(dict(
        dataset="sales", measures=["amount"], group_by=["customer"], top_n_per_group=None, filters=[],
        period=None, compare_to=None, compare_period=None, grain=None, threshold=None, sort=None,
        top_n=None, basis=None))
    ent = {"kind": "brand", "key": "MOCHA", "phrase": "mocha", "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": None}
    state, actions = apply_turn(
        mk_state(h), out(_ask(act="refines_held", domain="inventory", entities=[ent])),
        resolver=_Fake({"brand|MOCHA": ("exact", [("brand", "MOCHA")], False)}))
    assert isinstance(actions[0], Lookup) and actions[0].domain == "inventory"
    assert [r.key for r in state.held.refs["brand"]] == ["MOCHA"]
    assert not state.held.report.filters, state.held.report.filters


# ------------------------------------------------------------------ 3 live team and refer wording


def _doc_ctx(**extra):
    ent = {"kind": "order", "key": None, "phrase": None, "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": "202610-0151"}
    parsed = out(_ask(domain="order", entities=[ent]))
    return turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [], **extra,
    )


NOT_FOUND = 'Couldn\'t find: "202610-0151" (order).'


def test_the_offer_names_the_team_from_the_live_policy_rows():
    r = turn._run(_doc_ctx(domain_teams={"order": "sales_admin"}, offer_mode="offer"))
    assert f"{NOT_FOUND} Would you like me to escalate to sales admin team?" in r.direct_reply, r.direct_reply


def test_a_barred_contact_gets_v1s_refer_sentence_after_the_not_found_line():
    from app.services.chatbot.turn.refer import REFER_TO_SALESMAN

    r = turn._run(_doc_ctx(offer_mode="refer"))
    assert f"{NOT_FOUND} {REFER_TO_SALESMAN}" in r.direct_reply, r.direct_reply
    assert "escalate" not in r.direct_reply.lower()


def test_staff_get_the_not_found_line_alone():
    r = turn._run(_doc_ctx(offer_mode="none"))
    assert r.direct_reply.strip() == NOT_FOUND, r.direct_reply


# ------------------------------------------------------------------ 5 decimal picks


def test_a_superscript_digit_reply_is_a_miss_not_a_crash():
    table = {"product|WF12C": ("ambiguous", [("product", f"SRTWF12C-V{n}") for n in range(1, 4)], False)}
    state, _ = apply_turn(mk_state(), out(_ask(domain="incoming", entities=[_e(phrase="WF12C")])),
                          resolver=_Fake(table))
    state, actions = apply_turn(
        state, out(_ask(act="answers_bot_question", answer={"option_key": None, "free_value": "²"})),
        resolver=_Fake({}))
    assert isinstance(actions[0], ClarifyAct) and actions[0].reask is True


# ------------------------------------------------------------------ 4 family through v1's gate (engine level)


def test_a_customer_family_of_three_reaches_v1_without_a_customer_question(
    session_factory, seeded, stub_access, stub_parser, fake_turn, monkeypatch
):
    """A PV2 Lookup holding a three-account family (refs keyed by uuid) must pass v1's
    customer gate without a question and fetch exactly those three customer ids."""
    import uuid as _uuid

    from app.models.order import Customer
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests.chatbot.pv2.test_core_engine_hook import FakeResult, _set_contact_mode
    from tests.chatbot.pv2.test_fix_r3 import _patch_contact  # noqa: F401
    from tests.chatbot.test_engine import _envelope, _parser_output
    from tests.chatbot.test_outstanding_lane import _capturing_mcp, _enable_business_lane
    from tests.chatbot.test_rearch_r5_production_decides import _real_resolve_with_safe_probe
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business.services import AnswerServices, FetchServices
    from app.services.chatbot.pv2 import bridge
    from app.services.chatbot.pv2.state import Lookup, ResolvedRef

    names = ["ACFIRST CERAMICS SDN BHD - [A/C I]", "ACFIRST CERAMICS SDN BHD - [CERAMIC]",
             "ACFIRST CERAMICS SDN BHD - [IBORN]"]
    db = seeded
    ids = []
    for i, name in enumerate(names):
        row = Customer(id=str(_uuid.uuid4()), customer_code=f"ACF{i}", customer_name=name, is_active=True,
                       company_id=DEFAULT_COMPANY_ID)
        db.add(row)
        ids.append(row.id)
    db.commit()
    from app.services.chatbot.pv2.schema import Period

    h = mk_held("order")
    h.period = Period(preset="last_month", n=None, date_from=None, date_to=None)
    h.refs["customer"] = [ResolvedRef(kind="customer", key=i, label=n) for i, n in zip(ids, names)]
    verdict = bridge.to_v1_verdict(mk_state(h), Lookup(ask_index=0, domain="order", held=h), today=date(2026, 10, 15))
    verdict["asks"] = None
    verdict["topic_reset"] = None
    fake_turn(FakeResult(verdict=verdict))
    stub_parser(error=AssertionError("v1 parser must not run"))
    stub_access()
    _enable_business_lane(session_factory)
    _real_resolve_with_safe_probe(monkeypatch)
    mcp_call, captured = _capturing_mcp({"data": []})
    monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda d: FetchServices(mcp_call=mcp_call))
    monkeypatch.setattr(engine_mod.business_services, "answer_services_for",
                        lambda sf: AnswerServices(mcp_probe=lambda n, a: {"data": []}, family_fetch=lambda q: {"data": []}))
    monkeypatch.setattr(engine_mod.business_services, "production_answer_services",
                        lambda d: AnswerServices(mcp_probe=lambda n, a: {"data": []}, family_fetch=lambda q: {"data": []}))
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    text = ((result.reply or {}).get("text") or "")
    assert "which customer" not in text.lower() and "which one" not in text.lower(), text
    seen = {str(i) for call in captured for i in (call[1].get("customer_ids") or [])} if captured else set()
    assert seen == set(ids), (captured, text)
