"""PV2 -> v1 bridge (CONTRACT-PV2-13oct.md section 5, Lookup): the v1 verdict carries
RESOLVED codes exactly as if the user had typed them."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.chatbot.head.parser import DECLARED_KEYS, assert_emission
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.schema import Period
from app.services.chatbot.pv2.state import Held, Lookup, ResolvedRef
from tests.chatbot.pv2.carry_fakes import held, mk_state, option

TODAY = date(2026, 10, 13)  # a Tuesday


def P(**kw):
    base = dict(preset=None, n=None, date_from=None, date_to=None)
    base.update(kw)
    return Period(**base)


def _lookup(h: Held, domain: str | None = None, **kw) -> Lookup:
    return Lookup(ask_index=0, domain=domain if domain is not None else h.domain, held=h, **kw)


def _verdict(h: Held, **kw):
    return bridge.to_v1_verdict(mk_state(h), _lookup(h, **kw), today=TODAY)


def test_blank_verdict_declares_exactly_the_v1_keys_and_is_an_emission():
    v = bridge.blank_verdict()
    assert set(v) == set(DECLARED_KEYS)
    assert_emission(v)
    assert v["entities"] == []


def test_verdict_for_a_lookup_is_still_a_complete_emission():
    h = held("product_attachment", {"product": ["SRTWC286"]}, specs=[("dim_length", 600)],
             attachment_type="technical_specifications", period=P(preset="last_month"))
    v = _verdict(h)
    assert set(v) == set(DECLARED_KEYS)
    assert_emission(v)


def test_held_refs_become_current_message_entities_with_codes():
    h = held("inventory", {"product": ["SRTWC286"], "customer": ["CUST001"], "warehouse": ["WH1"], "brand": ["SORENTO"]})
    by_hint = {e["hint"]: e for e in _verdict(h)["entities"]}
    assert set(by_hint) >= {"product", "customer", "warehouse", "brand"}
    for hint, code in [("product", "SRTWC286"), ("customer", "CUST001"), ("warehouse", "WH1"), ("brand", "SORENTO")]:
        e = by_hint[hint]
        assert e["raw"] == code and e["canonical_code"] == code
        assert e["current_message"] is True


def test_domain_and_intent_hints():
    v = _verdict(held("product_attachment", {"product": ["A"]}))
    assert (v["domain_hint"], v["intent_hint"]) == ("product_attachment", "check_product_attachment")
    v = _verdict(held("inventory", {"product": ["A"]}))
    assert (v["domain_hint"], v["intent_hint"]) == ("inventory", "check_stock")
    v = _verdict(held("no_such_domain"))
    assert v["domain_hint"] == "no_such_domain" and v["intent_hint"] is None


def test_attachment_type_entity():
    v = _verdict(held("product_attachment", {"product": ["A"]}, attachment_type="technical_specifications"))
    att = [e for e in v["entities"] if e["hint"] == "attachment_type"]
    assert len(att) == 1 and att[0]["canonical_code"] == "technical_specifications"
    assert not [e for e in _verdict(held("product_attachment", {"product": ["A"]}))["entities"]
                if e["hint"] == "attachment_type"]


def test_spec_entity_uses_specification_hint_and_spec_fields():
    v = _verdict(held("product_attachment", {"product": ["A"]}, specs=[("dim_length", 600), ("trap_type", "p_trap")]))
    specs = [e for e in v["entities"] if e["hint"] == "specification"]
    assert [(e["spec_key"], e["spec_value"]) for e in specs] == [("dim_length", 600), ("trap_type", "p_trap")]


PRESETS = [
    ("today", None, (date(2026, 10, 13), date(2026, 10, 13))),
    ("yesterday", None, (date(2026, 10, 12), date(2026, 10, 12))),
    ("this_week", None, (date(2026, 10, 12), date(2026, 10, 13))),
    ("last_week", None, (date(2026, 10, 5), date(2026, 10, 11))),
    ("this_month", None, (date(2026, 10, 1), date(2026, 10, 13))),
    ("last_month", None, (date(2026, 9, 1), date(2026, 9, 30))),
    ("this_quarter", None, (date(2026, 10, 1), date(2026, 10, 13))),
    ("last_quarter", None, (date(2026, 7, 1), date(2026, 9, 30))),
    ("this_year", None, (date(2026, 1, 1), date(2026, 10, 13))),
    ("last_year", None, (date(2025, 1, 1), date(2025, 12, 31))),
    ("ytd", None, (date(2026, 1, 1), date(2026, 10, 13))),
    ("last_n_days", 7, (date(2026, 10, 7), date(2026, 10, 13))),
]


@pytest.mark.parametrize("preset,n,expected", PRESETS, ids=[p[0] for p in PRESETS])
def test_period_presets_become_verdict_dates(preset, n, expected):
    v = _verdict(held("sales", {"brand": ["SORENTO"]}, period=P(preset=preset, n=n)))
    assert (v["date_filter_start"], v["date_filter_end"]) == (expected[0].isoformat(), expected[1].isoformat())


def test_last_n_months_reads_as_the_report_engine_reads_it():
    # One reading for lookups and reports (`reports.period_dates`): the n full months
    # before this one.
    start, end = bridge.period_dates(P(preset="last_n_months", n=3), TODAY)
    assert (start, end) == (date(2026, 7, 1), date(2026, 9, 30))


def test_explicit_dates_beat_the_preset_and_no_period_is_null():
    p = P(preset="last_month", date_from=date(2026, 8, 1), date_to=date(2026, 8, 15))
    assert bridge.period_dates(p, TODAY) == (date(2026, 8, 1), date(2026, 8, 15))
    v = _verdict(held("sales"))
    assert v["date_filter_start"] is None and v["date_filter_end"] is None


def test_last_quarter_across_a_year_boundary():
    assert bridge.period_dates(P(preset="last_quarter"), date(2026, 2, 10)) == (date(2025, 10, 1), date(2025, 12, 31))
    assert bridge.period_dates(P(preset="last_month"), date(2026, 1, 5)) == (date(2025, 12, 1), date(2025, 12, 31))


@pytest.mark.parametrize("key,mode,picked,affirm", [
    ("2", "pick", [2], None), ("yes", "yes", [], True), ("no", "no", [], False), ("all", "all", [], None)])
def test_v1_open_question_pick_becomes_open_question_answer(key, mode, picked, affirm):
    h = held("inventory", {"product": ["A"]})
    v = _verdict(h, pending_kind="v1_open_question", picked=option(key, key.upper()))
    assert v["open_question_answer"]["mode"] == mode
    assert v["open_question_answer"]["picked"] == picked
    if affirm is not None:
        assert v["is_affirmative"] is affirm


def test_plain_lookup_has_no_open_question_answer():
    v = _verdict(held("inventory", {"product": ["A"]}))
    assert v["open_question_answer"]["mode"] is None


def test_focus_rows_are_carried_not_current():
    h = held("inventory", {"product": ["SRTWC286", "SRTWC8066"], "customer": ["CUST001"], "brand": ["SORENTO", "MOCHA"]})
    h.refs["product"][0] = ResolvedRef(kind="product", key="SRTWC286", label="SRTWC286 Family")
    f = bridge.focus_from_v2(h)
    assert [r["canonical_code"] for r in f.products] == ["SRTWC286", "SRTWC8066"]
    assert all(r["current_message"] is False for r in f.products + f.customers)
    assert f.products[0]["name"] == "SRTWC286 Family"
    assert [r["canonical_code"] for r in f.customers] == ["CUST001"]
    assert f.brands == ["SORENTO", "MOCHA"]
    assert f.domains == ["inventory"]


def test_focus_warehouse_extra_kinds_and_empty():
    f = bridge.focus_from_v2(held("incoming", {"warehouse": ["WH1"], "sales_order": ["SO1"]}))
    assert [r["canonical_code"] for r in f.warehouse] == ["WH1"]
    assert [r["canonical_code"] for r in f.extra["order"]] == ["SO1"]
    e = bridge.focus_from_v2(Held())
    assert e.products == [] and e.brands == [] and e.domains == []


def test_gate_marker():
    assert bridge.is_gated("inventory") and bridge.is_gated("product_attachment")
    assert not bridge.is_gated("promotion")
