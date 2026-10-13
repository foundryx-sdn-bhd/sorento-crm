"""PV2-REPORTS: period_dates, period_label, edit_report, drill_row, plan_report (pure table tests)."""
from tests.chatbot.pv2.reports_schema_shim import install

schema = install()
ReportSpec, Filter, Period = schema.ReportSpec, schema.Filter, schema.Period

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.chatbot.lanes import business as lanes_business
from app.services.chatbot.lanes.business import ranking_ask, report_ask
from app.services.chatbot.pv2 import reports as r

TODAY = date(2026, 10, 13)  # a Tuesday


def spec(**kw):
    base = dict(
        dataset=None, measures=[], group_by=[], top_n_per_group=None, filters=[], period=None,
        compare_to=None, compare_period=None, grain=None, threshold=None, sort=None, top_n=None, basis=None,
    )
    base.update(kw)
    return ReportSpec(**base)


def period(**kw):
    base = dict(preset=None, n=None, date_from=None, date_to=None)
    base.update(kw)
    return Period(**base)


def flt(dimension, keys=(), op="in", values=()):
    return Filter(dimension=dimension, op=op, keys=list(keys), values=list(values))


B_UUID, C_UUID, U_UUID, P_UUID = (str(uuid.uuid4()) for _ in range(4))
LAST_MONTH = period(preset="last_month")
SEP = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


def ranking(**kw):
    """A complete sales ranking; override any field."""
    base = dict(
        dataset="sales", measures=["amount"], basis="delivered", group_by=["customer"],
        period=LAST_MONTH, top_n=10,
    )
    base.update(kw)
    return spec(**base)


def total(**kw):
    base = dict(dataset="sales", basis="ordered", period=period(preset="this_month"))
    base.update(kw)
    return spec(**base)


# ---------------------------------------------------------------- period_dates

D = date


@pytest.mark.parametrize(
    "p,expected",
    [
        (None, None),
        (period(), None),
        (period(preset="today"), (D(2026, 10, 13), D(2026, 10, 13))),
        (period(preset="yesterday"), (D(2026, 10, 12), D(2026, 10, 12))),
        (period(preset="this_week"), (D(2026, 10, 12), D(2026, 10, 13))),
        (period(preset="last_week"), (D(2026, 10, 5), D(2026, 10, 11))),
        (period(preset="this_month"), (D(2026, 10, 1), D(2026, 10, 13))),
        (period(preset="last_month"), (D(2026, 9, 1), D(2026, 9, 30))),
        (period(preset="this_quarter"), (D(2026, 10, 1), D(2026, 10, 13))),
        (period(preset="last_quarter"), (D(2026, 7, 1), D(2026, 9, 30))),
        (period(preset="this_year"), (D(2026, 1, 1), D(2026, 10, 13))),
        (period(preset="ytd"), (D(2026, 1, 1), D(2026, 10, 13))),
        (period(preset="last_year"), (D(2025, 1, 1), D(2025, 12, 31))),
        (period(preset="last_n_days", n=7), (D(2026, 10, 7), D(2026, 10, 13))),
        (period(preset="last_n_months", n=3), (D(2026, 7, 1), D(2026, 9, 30))),
        (period(preset="last_n_days"), None),
        (period(preset="last_n_months"), None),
        # explicit dates win over a preset
        (period(preset="last_month", date_from=D(2026, 8, 1), date_to=D(2026, 8, 15)), (D(2026, 8, 1), D(2026, 8, 15))),
        (period(date_from=D(2026, 9, 1), date_to=D(2026, 9, 15)), (D(2026, 9, 1), D(2026, 9, 15))),
        # date_from only: runs to today (or to date_from itself when that is in the future)
        (period(date_from=D(2026, 9, 1)), (D(2026, 9, 1), D(2026, 10, 13))),
        (period(date_from=D(2026, 11, 1)), (D(2026, 11, 1), D(2026, 11, 1))),
        # date_to only is no period
        (period(date_to=D(2026, 9, 15)), None),
    ],
)
def test_period_dates(p, expected):
    assert r.period_dates(p, TODAY) == expected


@pytest.mark.parametrize(
    "p,label",
    [
        (period(preset="last_month"), "last month"),
        (period(preset="this_month"), "this month"),
        (period(preset="last_n_days", n=7), "last 7 days"),
        (period(date_from=D(2026, 9, 1), date_to=D(2026, 9, 15)), "1 Sep 2026 to 15 Sep 2026"),
    ],
)
def test_period_label(p, label):
    assert r.period_label(p, TODAY) == label


@pytest.mark.parametrize(
    "p",
    [period(preset=x) for x in ("today", "yesterday", "this_week", "last_week", "this_quarter", "last_quarter",
                                "this_year", "last_year", "ytd")]
    + [period(preset="last_n_months", n=3), period(date_from=D(2026, 9, 1), date_to=D(2026, 9, 15))],
)
def test_period_label_never_iso(p):
    import re

    label = r.period_label(p, TODAY)
    assert label and not re.search(r"\d{4}-\d{2}-\d{2}", label)


# ---------------------------------------------------------------- edit_report

BRAND_SORENTO = flt("brand", ["SORENTO"])
CUST_ABC = flt("customer", ["ABC"])


def dump(s):
    return s.model_dump()


def test_edit_none_cases():
    held, inc = ranking(), spec(measures=["qty"])
    assert r.edit_report(None, inc) == inc
    assert r.edit_report(held, None) == held
    assert r.edit_report(None, None) is None


def test_edit_report_cleared_drops_held():
    held, inc = ranking(), spec(dataset="chat", group_by=["topic"])
    out = r.edit_report(held, inc, cleared=("report",))
    assert dump(out) == dump(inc)


@pytest.mark.parametrize(
    "incoming,field,expected",
    [
        (spec(dataset="chat"), "dataset", "chat"),
        (spec(period=period(preset="this_week")), "period", period(preset="this_week")),
        (spec(sort="asc"), "sort", "asc"),
        (spec(top_n=5), "top_n", 5),
        (spec(basis="ordered"), "basis", "ordered"),
        (spec(compare_to="last_year"), "compare_to", "last_year"),
        (spec(grain="month"), "grain", "month"),
        (spec(threshold=1000.0), "threshold", 1000.0),
        (spec(measures=["qty"]), "measures", ["qty"]),
        (spec(group_by=["sales_agent"]), "group_by", ["sales_agent"]),
    ],
)
def test_edit_report_replaces_field(incoming, field, expected):
    held = ranking(sort="desc")
    before = dump(held)
    out = r.edit_report(held, incoming)
    assert getattr(out, field) == expected
    assert dump(held) == before  # held is never mutated
    # every other field of held carries
    for name in ("measures", "group_by", "top_n", "basis", "period", "dataset"):
        if name != field:
            assert getattr(out, name) == getattr(held, name)


def test_edit_report_empty_incoming_keeps_everything():
    held = ranking(filters=[BRAND_SORENTO])
    out = r.edit_report(held, spec())
    assert dump(out) == dump(held)


def test_edit_report_filters_merge_by_dimension():
    held = ranking(filters=[CUST_ABC])
    before = dump(held)
    out = r.edit_report(held, spec(filters=[BRAND_SORENTO]))
    assert {(f.dimension, tuple(f.keys)) for f in out.filters} == {("customer", ("ABC",)), ("brand", ("SORENTO",))}
    assert dump(held) == before


def test_edit_report_filter_same_dimension_replaces():
    held = ranking(filters=[CUST_ABC, BRAND_SORENTO])
    out = r.edit_report(held, spec(filters=[flt("brand", ["MOCHA"])]))
    by_dim = {f.dimension: f.keys for f in out.filters}
    assert by_dim == {"customer": ["ABC"], "brand": ["MOCHA"]}
    assert len(out.filters) == 2


# ---------------------------------------------------------------- drill_row


def row(dimension="sales_agent", keys=("id1",), label="ALI"):
    return r.ReportOption(
        key="ali", label=label, payload={"report_row": {"dimension": dimension, "keys": list(keys), "label": label}}
    )


def test_drill_row_total_for_that_row():
    held = ranking(group_by=["sales_agent"], filters=[flt("sales_agent", ["OLD"]), BRAND_SORENTO])
    before = dump(held)
    new, refs = r.drill_row(held, row())
    sa = [f for f in new.filters if f.dimension == "sales_agent"]
    assert len(sa) == 1 and sa[0].op == "in" and sa[0].keys == ["ALI"]
    assert [f.keys for f in new.filters if f.dimension == "brand"] == [["SORENTO"]]
    assert new.group_by == []
    assert new.period == held.period and new.basis == held.basis and new.measures == held.measures
    assert refs == {"sales_agent": [{"kind": "sales_agent", "key": "id1", "label": "ALI"}]}
    assert dump(held) == before


def test_drill_row_incoming_group_by():
    held = ranking(group_by=["sales_agent"])
    new, _ = r.drill_row(held, row(), incoming=spec(group_by=["customer"]))
    assert new.group_by == ["customer"]


def test_drill_row_incoming_without_group_by_is_total():
    held = ranking(group_by=["sales_agent"])
    new, _ = r.drill_row(held, row(), incoming=spec())
    assert new.group_by == []


def test_drill_row_location_is_warehouse_kind():
    held = ranking(group_by=["location"])
    new, refs = r.drill_row(held, row("location", ("BRW",), "Bukit Raja"))
    assert refs == {"location": [{"kind": "warehouse", "key": "BRW", "label": "Bukit Raja"}]}
    assert [f.keys for f in new.filters if f.dimension == "location"] == [["Bukit Raja"]]


def test_drill_row_multi_keys_become_refs():
    held = ranking()
    _, refs = r.drill_row(held, row("customer", ("c2", "c3"), "XYZ"))
    assert [x["key"] for x in refs["customer"]] == ["c2", "c3"]
    assert {x["label"] for x in refs["customer"]} == {"XYZ"}


@pytest.mark.parametrize("payload", [{}, {"other": 1}])
def test_drill_row_without_report_row_raises(payload):
    with pytest.raises(ValueError):
        r.drill_row(ranking(), SimpleNamespace(payload=payload))


# ---------------------------------------------------------------- plan_report: runs

FULL_ROUTE = {
    "basis": "delivered", "measure": "amount", "group_by": "customer", "sort": "desc", "top_n": 10, **SEP,
}


def test_plan_full_sales_ranking():
    plan = r.plan_report(ranking(), refs={}, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args == FULL_ROUTE
    assert plan.header == "*Read as:* sales amount, delivered sales, by customer, last month, top 10"
    assert plan.notes == []


def test_plan_dataset_none_is_sales():
    plan = r.plan_report(ranking(dataset=None), refs={}, today=TODAY)
    assert plan.status == "run"
    assert plan.spec.dataset == "sales"
    assert "dataset" not in plan.route_args


def test_plan_sort_asc_says_bottom():
    plan = r.plan_report(ranking(sort="asc"), refs={}, today=TODAY)
    assert plan.route_args["sort"] == "asc"
    assert "bottom 10" in plan.header and "top 10" not in plan.header


# ---------------------------------------------------------------- plan_report: asks

SIX = [(m, b) for m in ("amount", "qty") for b in ("delivered", "ordered", "outstanding")]


def _assert_six_options(plan):
    assert [(o.payload["report"]["measures"][0], o.payload["report"]["basis"]) for o in plan.options] == SIX
    assert [o.payload for o in plan.options] == [{"report": {"measures": [m], "basis": b}} for m, b in SIX]
    assert plan.options[0].label == "Amount, delivered sales"
    keys = [o.key for o in plan.options]
    assert len(set(keys)) == 6 and all(" " not in k for k in keys)


def test_plan_missing_measure_and_basis_asks_once():
    plan = r.plan_report(ranking(measures=[], basis=None), refs={}, today=TODAY)
    assert plan.status == "ask"
    assert plan.missing == ["measure", "basis"]
    assert plan.about == "report_measure"
    assert plan.reply == "By quantity or by amount?\nDelivered, ordered or outstanding?"
    assert len(plan.options) == 6
    _assert_six_options(plan)
    assert plan.route_args == {}


def test_plan_missing_period_and_top_n():
    plan = r.plan_report(ranking(period=None, top_n=None), refs={}, today=TODAY)
    assert plan.status == "ask"
    assert plan.missing == ["period", "top_n"]
    assert plan.about == "report_period"
    assert plan.reply == report_ask.PERIOD_QUESTION + "\n" + report_ask.TOP_N_QUESTION
    assert plan.options == []


def test_plan_all_four_missing_one_question():
    plan = r.plan_report(spec(dataset="sales", group_by=["customer"]), refs={}, today=TODAY)
    assert plan.status == "ask"
    assert plan.missing == ["period", "top_n", "measure", "basis"]
    assert plan.about == "report_measure"
    assert plan.reply == "\n".join(
        [report_ask.PERIOD_QUESTION, report_ask.TOP_N_QUESTION, ranking_ask.METRIC_LINE, ranking_ask.RANKING_BASIS_LINE]
    )
    _assert_six_options(plan)


@pytest.mark.parametrize("field,missing", [("period", ["period"]), ("top_n", ["top_n"])])
def test_plan_single_missing_field(field, missing):
    plan = r.plan_report(ranking(**{field: None}), refs={}, today=TODAY)
    assert plan.status == "ask" and plan.missing == missing and plan.about == "report_period"


def test_plan_missing_basis_only():
    plan = r.plan_report(ranking(basis=None), refs={}, today=TODAY)
    assert plan.status == "ask" and plan.missing == ["basis"] and plan.about == "report_measure"
    assert plan.reply == ranking_ask.RANKING_BASIS_LINE


def test_plan_chat_ranking_without_top_n_asks_it():
    s = spec(dataset="chat", measures=["failed"], group_by=["topic"], period=period(preset="this_week"))
    plan = r.plan_report(s, refs={}, today=TODAY)
    assert plan.status == "ask" and plan.missing == ["top_n"]


# ---------------------------------------------------------------- plan_report: totals and trends


def test_plan_total_needs_no_measure_or_top_n():
    plan = r.plan_report(total(filters=[flt("brand", ["SORENTO"])]), refs={"brand": [{"key": B_UUID, "label": "SORENTO"}]}, today=TODAY)
    assert plan.status == "run"
    ra = plan.route_args
    assert "group_by" not in ra and "top_n" not in ra
    assert ra["measure"] == "amount" and ra["basis"] == "ordered"
    assert ra["date_from"] == "2026-10-01" and ra["date_to"] == "2026-10-13"
    assert plan.header == "*Read as:* ordered sales, brand SORENTO, this month"


def test_plan_sales_total_without_axis_or_filter_says_catalogue():
    plan = r.plan_report(total(), refs={}, today=TODAY)
    assert plan.status == "say" and plan.reply == report_ask.CATALOGUE_LINE


def test_plan_month_trend_needs_no_top_n_or_measure():
    plan = r.plan_report(ranking(group_by=["month"], top_n=None, measures=[]), refs={}, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["group_by"] == "month"
    assert plan.route_args["top_n"] == report_ask.MONTH_TOP_N == 100
    assert "by month" in plan.header and "top" not in plan.header


def test_plan_grain_month_is_group_by_month():
    plan = r.plan_report(total(grain="month"), refs={}, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["group_by"] == "month"
    assert plan.notes == []


def test_plan_grain_quarter_noted():
    plan = r.plan_report(
        total(grain="quarter", filters=[BRAND_SORENTO]), refs={"brand": [{"key": B_UUID, "label": "SORENTO"}]}, today=TODAY
    )
    assert plan.status == "run"
    assert "group_by" not in plan.route_args
    assert plan.notes == ["Breakdown by quarter is not available yet."]


# ---------------------------------------------------------------- plan_report: not available yet notes


@pytest.mark.parametrize(
    "compare,note",
    [
        ("last_year", "Comparison with last year is not available yet."),
        ("prev_period", "Comparison with the previous period is not available yet."),
    ],
)
def test_plan_compare_noted(compare, note):
    plan = r.plan_report(ranking(compare_to=compare), refs={}, today=TODAY)
    assert plan.status == "run" and plan.notes == [note]
    assert plan.route_args == FULL_ROUTE


def test_plan_two_measures_sales_no_note():
    plan = r.plan_report(ranking(measures=["amount", "qty"]), refs={}, today=TODAY)
    assert plan.status == "run" and plan.route_args["measure"] == "amount" and plan.notes == []


def test_plan_ratio_only_measure_is_missing():
    plan = r.plan_report(ranking(measures=["amount/qty"]), refs={}, today=TODAY)
    assert plan.status == "ask" and plan.missing == ["measure"]
    assert "Ratios are not available yet." in plan.notes


def test_plan_ratio_dropped_remaining_measure_decides():
    plan = r.plan_report(ranking(measures=["amount/qty", "qty"]), refs={}, today=TODAY)
    assert plan.status == "run" and plan.route_args["measure"] == "qty"
    assert plan.notes == ["Ratios are not available yet."]


def test_plan_second_group_by_noted():
    plan = r.plan_report(ranking(group_by=["brand", "sales_agent"]), refs={}, today=TODAY)
    assert plan.status == "run" and plan.route_args["group_by"] == "brand"
    assert plan.notes == ["A second breakdown (by sales agent) is not available yet."]


def test_plan_top_n_per_group_alone_noted():
    plan = r.plan_report(ranking(top_n_per_group=3), refs={}, today=TODAY)
    assert plan.status == "run" and plan.notes == ["Top per group is not available yet."]


def test_plan_threshold_noted():
    plan = r.plan_report(ranking(threshold=1000.0), refs={}, today=TODAY)
    assert plan.status == "run" and plan.notes == ["Thresholds are not available yet."]


def test_plan_top_n_over_ceiling_asks_again_with_note():
    plan = r.plan_report(ranking(top_n=5000), refs={}, today=TODAY)
    assert plan.status == "ask" and "top_n" in plan.missing
    assert plan.route_args == {}
    assert plan.notes == [lanes_business.top_selling_ceiling_note({"top_n": 5000})]


# ---------------------------------------------------------------- plan_report: say


def test_plan_region_group_by_says():
    plan = r.plan_report(ranking(group_by=["region"]), refs={}, today=TODAY)
    assert plan.status == "say"
    assert plan.reply == "Breakdown by region is not available yet.\n" + report_ask.CATALOGUE_LINE
    assert plan.route_args == {}


def test_plan_state_filter_says():
    plan = r.plan_report(ranking(filters=[flt("state", ["Johor"])]), refs={}, today=TODAY)
    assert plan.status == "say"
    assert plan.reply.startswith("Filtering by state is not available yet.")


@pytest.mark.parametrize(
    "f,line",
    [
        (flt("brand", ["SORENTO"], op="not_in"), "Excluding brands is not available yet."),
        (flt("category", ["Basin"], op="not_in"), "Excluding categories is not available yet."),
        (flt("brand", op="gt", values=[5]), "Filtering brand by a number is not available yet."),
        (flt("brand", op="lt", values=[5]), "Filtering brand by a number is not available yet."),
        (flt("brand", op="between", values=[1, 5]), "Filtering brand by a number is not available yet."),
    ],
)
def test_plan_unsupported_filter_ops_say(f, line):
    plan = r.plan_report(ranking(filters=[f]), refs={"brand": [], "category": []}, today=TODAY)
    assert plan.status == "say" and plan.reply == line


def test_plan_unknown_dataset_says():
    plan = r.plan_report(ranking(dataset="inventory"), refs={}, today=TODAY)
    assert plan.status == "say" and "not available yet" in plan.reply


# ---------------------------------------------------------------- plan_report: filters and refs


def test_plan_brand_filter_uses_ref_key_and_label():
    refs = {"brand": [{"key": B_UUID, "label": "SORENTO"}]}
    plan = r.plan_report(ranking(filters=[BRAND_SORENTO]), refs=refs, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["brand_ids"] == [B_UUID]
    assert "brand SORENTO" in plan.header and B_UUID not in plan.header


def test_plan_refs_may_be_objects():
    refs = {"brand": [SimpleNamespace(key=B_UUID, label="SORENTO")]}
    plan = r.plan_report(ranking(filters=[BRAND_SORENTO]), refs=refs, today=TODAY)
    assert plan.route_args["brand_ids"] == [B_UUID]


def test_plan_filter_without_refs_is_unresolved():
    plan = r.plan_report(ranking(filters=[CUST_ABC]), refs={}, today=TODAY)
    assert plan.status == "unresolved" and plan.unresolved == ["customer"]
    assert plan.route_args == {}


def test_plan_channel_needs_no_refs():
    plan = r.plan_report(ranking(filters=[flt("channel", ["dealer"])]), refs={}, today=TODAY)
    assert plan.status == "run" and plan.route_args["channel"] == "dealer"
    assert "channel Dealer" in plan.header


def test_plan_location_filter_warehouse_codes():
    refs = {"location": [{"key": "BRW", "label": "Bukit Raja"}]}
    plan = r.plan_report(ranking(filters=[flt("location", ["Bukit Raja"])]), refs=refs, today=TODAY)
    assert plan.status == "run" and plan.route_args["warehouse_codes"] == ["BRW"]
    assert "Bukit Raja" in plan.header


# ---------------------------------------------------------------- plan_report: dealer


def test_plan_dealer_refused_breakdown():
    plan = r.plan_report(ranking(group_by=["sales_agent"]), refs={}, today=TODAY, dealer=True)
    assert plan.status == "say" and plan.reply == report_ask.DEALER_REFUSAL


def test_plan_dealer_refused_filter():
    refs = {"sales_agent": [{"key": "u1", "label": "ALI"}]}
    s = ranking(filters=[flt("sales_agent", ["ALI"])])
    plan = r.plan_report(s, refs=refs, today=TODAY, dealer=True)
    assert plan.status == "say" and plan.reply == report_ask.DEALER_REFUSAL


def test_plan_dealer_product_group_runs():
    plan = r.plan_report(ranking(group_by=["product"]), refs={}, today=TODAY, dealer=True)
    assert plan.status == "run" and plan.route_args["group_by"] == "product"


# ---------------------------------------------------------------- plan_report: chat

CHAT_WEEK = period(preset="this_week")
CHAT_DATES = {"date_from": "2026-10-12", "date_to": "2026-10-13"}


def test_plan_chat_failed_by_topic():
    s = spec(dataset="chat", measures=["failed"], group_by=["topic"], period=CHAT_WEEK, top_n=5)
    plan = r.plan_report(s, refs={}, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args == {
        "dataset": "chat", "basis": "turns", "measure": "failed", "group_by": "topic", "sort": "desc",
        "top_n": 5, **CHAT_DATES,
    }


def test_plan_chat_default_measure_never_asked():
    s = spec(dataset="chat", group_by=["topic"], period=CHAT_WEEK, top_n=5)
    plan = r.plan_report(s, refs={}, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["measure"] == "asks" and plan.route_args["basis"] == "asks"


def test_plan_chat_week_trend_needs_no_top_n():
    s = spec(dataset="chat", group_by=["week"], period=period(preset="last_n_months", n=2))
    plan = r.plan_report(s, refs={}, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["group_by"] == "week" and "top_n" not in plan.route_args


def test_plan_chat_subject_filter():
    refs = {"subject": [{"key": "order", "label": "Orders"}]}
    s = spec(
        dataset="chat", measures=["asks"], group_by=["topic"], period=CHAT_WEEK, top_n=5,
        filters=[flt("subject", ["order"])],
    )
    plan = r.plan_report(s, refs=refs, today=TODAY)
    assert plan.status == "run" and plan.route_args["subjects"] == ["order"]


# ---------------------------------------------------------------- no leaks

UUID_KEYS = [B_UUID, C_UUID, U_UUID, P_UUID]
BRAND_REFS = {"brand": [{"key": B_UUID, "label": "SORENTO"}]}
CUST_REFS = {"customer": [{"key": C_UUID, "label": "ABC SDN BHD"}]}
AGENT_REFS = {"sales_agent": [{"key": U_UUID, "label": "ALI"}]}

LEAK_CASES = [
    ("full", ranking(), {}, False),
    ("ask_both", ranking(measures=[], basis=None), {}, False),
    ("ask_period", ranking(period=None, top_n=None), {}, False),
    ("total", total(), {}, False),
    ("brand", ranking(filters=[BRAND_SORENTO]), BRAND_REFS, False),
    ("customer", ranking(filters=[CUST_ABC]), CUST_REFS, False),
    ("agent_filter", ranking(filters=[flt("sales_agent", ["ALI"])]), AGENT_REFS, False),
    ("unresolved", ranking(filters=[CUST_ABC]), {}, False),
    ("compare", ranking(compare_to="last_year"), {}, False),
    ("ceiling", ranking(top_n=5000), {}, False),
    ("region", ranking(group_by=["region"]), {}, False),
    ("not_in", ranking(filters=[flt("brand", ["SORENTO"], op="not_in")]), BRAND_REFS, False),
    ("dealer", ranking(group_by=["sales_agent"]), {}, True),
    ("second", ranking(group_by=["brand", "sales_agent"], threshold=5.0), {}, False),
]


@pytest.mark.parametrize("name,s,refs,dealer", LEAK_CASES, ids=[c[0] for c in LEAK_CASES])
def test_plan_text_never_leaks_keys_or_dots(name, s, refs, dealer):
    plan = r.plan_report(s, refs=refs, today=TODAY, dealer=dealer)
    texts = [plan.header, plan.reply or "", *plan.notes, *[o.label for o in plan.options]]
    for text in texts:
        assert " · " not in text
        for key in UUID_KEYS:
            assert key not in text


# ---------------------------------------------------------------- review round: refs, held spec, dealer, drill

from app.services.chatbot.lanes.business import chat_report
from app.services.reports.ask import NO_AGENT_KEY


@pytest.mark.parametrize(
    "dim,key",
    [("customer", "C001"), ("brand", "SORENTO"), ("category", "basin"), ("sales_agent", "ali")],
)
def test_plan_non_uuid_ref_key_is_unresolved(dim, key):
    plan = r.plan_report(ranking(filters=[flt(dim, ["x"])]), refs={dim: [{"key": key, "label": "X"}]}, today=TODAY)
    assert plan.status == "unresolved" and plan.unresolved == [dim]
    assert plan.route_args == {}


def test_plan_customer_agent_non_uuid_unresolved():
    refs = {"customer_agent": [{"key": "bob", "label": "Bob"}]}
    plan = r.plan_report(ranking(filters=[flt("customer_agent", ["Bob"])]), refs=refs, today=TODAY)
    assert plan.status == "unresolved" and plan.unresolved == ["customer_agent"]


def test_plan_uuid_param_filters_carry_ids():
    cases = {"customer": "customer_ids", "category": "category_ids", "sales_agent": "sales_agent_ids"}
    for dim, param in cases.items():
        k = str(uuid.uuid4())
        plan = r.plan_report(ranking(filters=[flt(dim, ["X"])]), refs={dim: [{"key": k, "label": "X"}]}, today=TODAY)
        assert plan.status == "run" and plan.route_args[param] == [k]


def test_plan_sales_product_single_code_rides_as_product_code():
    refs = {"product": [{"key": "SRTWC286", "label": "SRTWC286"}]}
    plan = r.plan_report(ranking(filters=[flt("product", ["SRTWC286"])]), refs=refs, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["product_code"] == "SRTWC286" and "product_ids" not in plan.route_args


def test_plan_sales_product_uuid_rides_as_product_ids():
    refs = {"product": [{"key": P_UUID, "label": "Basin A"}]}
    plan = r.plan_report(ranking(filters=[flt("product", ["Basin A"])]), refs=refs, today=TODAY)
    assert plan.route_args["product_ids"] == [P_UUID] and "product_code" not in plan.route_args


def test_plan_sales_product_two_codes_says():
    refs = {"product": [{"key": "SRTWC286", "label": "A"}, {"key": "SRTWC287", "label": "B"}]}
    plan = r.plan_report(ranking(filters=[flt("product", ["A", "B"])]), refs=refs, today=TODAY)
    assert plan.status == "say"
    assert plan.reply == "Filtering by several product codes at once is not available yet."
    assert plan.spec.filters == []


def test_plan_sales_agent_no_agent_key_allowed():
    refs = {"sales_agent": [{"key": NO_AGENT_KEY, "label": "No agent"}]}
    plan = r.plan_report(ranking(filters=[flt("sales_agent", ["No agent"])]), refs=refs, today=TODAY)
    assert plan.status == "run" and plan.route_args["sales_agent_ids"] == [NO_AGENT_KEY]


# held spec: declined parts are stripped so the line is said once


def test_plan_spec_strips_compare():
    plan = r.plan_report(ranking(compare_to="last_year", compare_period=LAST_MONTH), refs={}, today=TODAY)
    assert plan.notes and plan.spec.compare_to is None and plan.spec.compare_period is None


def test_plan_spec_strips_threshold_group_and_grain():
    plan = r.plan_report(ranking(threshold=5.0, top_n_per_group=2, grain="quarter"), refs={}, today=TODAY)
    assert plan.status == "run"
    assert plan.spec.threshold is None and plan.spec.top_n_per_group is None and plan.spec.grain is None
    assert len(plan.notes) == 3


def test_plan_spec_strips_second_group_by():
    plan = r.plan_report(ranking(group_by=["brand", "sales_agent"]), refs={}, today=TODAY)
    assert plan.spec.group_by == ["brand"]


def test_plan_spec_strips_unsupported_filters():
    plan = r.plan_report(ranking(filters=[flt("state", ["Johor"]), BRAND_SORENTO]), refs=BRAND_REFS, today=TODAY)
    assert plan.status == "say"
    assert [f.dimension for f in plan.spec.filters] == ["brand"]
    plan = r.plan_report(ranking(filters=[flt("brand", ["SORENTO"], op="not_in")]), refs=BRAND_REFS, today=TODAY)
    assert plan.status == "say" and plan.spec.filters == []


def test_plan_spec_strips_unsupported_group_by():
    plan = r.plan_report(ranking(group_by=["region"]), refs={}, today=TODAY)
    assert plan.status == "say" and plan.spec.group_by == []


def test_plan_spec_strips_ratio_measures():
    plan = r.plan_report(ranking(measures=["amount/qty", "qty"]), refs={}, today=TODAY)
    assert plan.spec.measures == ["qty"]
    plan = r.plan_report(ranking(measures=["amount/qty"]), refs={}, today=TODAY)
    assert plan.spec.measures == []


def test_chain_compare_said_once():
    plan = r.plan_report(ranking(compare_to="last_year"), refs={}, today=TODAY)
    assert plan.notes
    held = r.edit_report(plan.spec, spec(group_by=["brand"]))
    again = r.plan_report(held, refs={}, today=TODAY)
    assert again.status == "run" and again.notes == [] and again.route_args["group_by"] == "brand"


def test_chain_state_filter_said_once():
    plan = r.plan_report(ranking(filters=[flt("state", ["Johor"])]), refs={}, today=TODAY)
    assert plan.status == "say"
    held = r.edit_report(plan.spec, spec(group_by=["brand"]))
    again = r.plan_report(held, refs={}, today=TODAY)
    assert again.status == "run" and again.route_args["group_by"] == "brand"


def test_plan_grain_month_with_group_by_is_second_breakdown():
    plan = r.plan_report(ranking(grain="month"), refs={}, today=TODAY)
    assert plan.status == "run" and plan.route_args["group_by"] == "customer"
    assert plan.notes == ["A second breakdown (by month) is not available yet."]


def test_plan_ratio_only_ranking_asks_with_note():
    plan = r.plan_report(ranking(measures=["amount/qty"]), refs={}, today=TODAY)
    assert plan.status == "ask" and plan.notes == ["Ratios are not available yet."]


# dataset inference from keys


def test_plan_dataset_none_chat_keys_infers_chat():
    s = spec(measures=["asks"], group_by=["topic"], period=CHAT_WEEK, top_n=5)
    plan = r.plan_report(s, refs={}, today=TODAY)
    assert plan.status == "run" and plan.route_args["dataset"] == "chat"
    assert plan.spec.dataset == "chat"


def test_plan_dataset_none_sales_keys_infers_sales():
    plan = r.plan_report(ranking(dataset=None, group_by=["brand"]), refs={}, today=TODAY)
    assert plan.status == "run" and "dataset" not in plan.route_args and plan.spec.dataset == "sales"


def test_plan_chat_period_question_is_chat_wording():
    s = spec(dataset="chat", group_by=["topic"], top_n=5)
    plan = r.plan_report(s, refs={}, today=TODAY)
    assert plan.status == "ask" and plan.missing == ["period"]
    assert plan.reply == chat_report.PERIOD_QUESTION


# dealer


@pytest.mark.parametrize("dim", ["location", "channel", "customer_agent", "sales_agent"])
def test_dealer_refusal_sales_filters(dim):
    base = ranking(group_by=["product"])
    assert r.dealer_refusal(base) is None
    assert r.dealer_refusal(ranking(group_by=["product"], filters=[flt(dim, ["x"])])) == report_ask.DEALER_REFUSAL


def test_dealer_refusal_sales_group_by():
    assert r.dealer_refusal(ranking(group_by=["sales_agent"])) == report_ask.DEALER_REFUSAL
    assert r.dealer_refusal(ranking(group_by=["customer"])) == report_ask.DEALER_REFUSAL
    assert r.dealer_refusal(ranking(group_by=["brand"])) is None


def test_dealer_refusal_chat_group_by():
    chat = dict(dataset="chat", period=CHAT_WEEK, top_n=5)
    assert r.dealer_refusal(spec(group_by=["salesperson"], **chat)) == report_ask.DEALER_REFUSAL
    assert r.dealer_refusal(spec(group_by=["product"], **chat)) is None


def test_dealer_say_plan_drops_offending_parts():
    plan = r.plan_report(ranking(group_by=["product"], filters=[flt("location", ["x"]), BRAND_SORENTO]),
                         refs=BRAND_REFS, today=TODAY, dealer=True)
    assert plan.status == "say" and [f.dimension for f in plan.spec.filters] == ["brand"]
    plan = r.plan_report(ranking(group_by=["sales_agent"]), refs={}, today=TODAY, dealer=True)
    assert plan.status == "say" and plan.spec.group_by == []


# drill refusals


@pytest.mark.parametrize("dim,label", [("channel", "Dealer"), ("month", "Sep 2026")])
def test_drill_not_a_source_refused(dim, label):
    with pytest.raises(r.DrillRefused) as e:
        r.drill_row(ranking(), row(dim, ("k",), label))
    assert e.value.line == report_ask.NOT_A_SOURCE_LINES[dim]
    assert isinstance(e.value, ValueError)


def test_drill_no_keys_refused():
    with pytest.raises(r.DrillRefused) as e:
        r.drill_row(ranking(), row("customer", (), "ABC"))
    plural = report_ask.AXIS_NOUNS["customer"][1]
    assert e.value.line == report_ask.NO_KEYS_LINE.format(name="ABC", plural=plural)


def test_drill_too_many_keys_refused():
    with pytest.raises(r.DrillRefused) as e:
        r.drill_row(ranking(), row("customer", tuple(f"k{i}" for i in range(51)), "ABC"))
    assert e.value.line == report_ask.TOO_MANY_LINE.format(max=report_ask.DRILL_MAX_KEYS, plural="customers")


def test_drill_exactly_max_keys_ok():
    new, refs = r.drill_row(ranking(), row("customer", tuple(f"k{i}" for i in range(50)), "ABC"))
    assert len(refs["customer"]) == 50


def test_edit_report_cleared_period():
    out = r.edit_report(ranking(), spec(), cleared=["period"])
    assert out.period is None and out.top_n == 10
    assert r.edit_report(ranking(), None, cleared=["period"]).period is None


# ReportResolution


def test_report_resolution_validates():
    res = r.ReportResolution.model_validate(
        {"status": "exact", "refs": {"brand": [{"kind": "brand", "key": B_UUID, "label": "SORENTO"}]}}
    )
    assert res.status == "exact" and res.refs["brand"][0]["label"] == "SORENTO"


def test_report_resolution_rejects_extra_key():
    with pytest.raises(Exception):
        r.ReportResolution.model_validate({"status": "exact", "refs": {}, "surprise": 1})
