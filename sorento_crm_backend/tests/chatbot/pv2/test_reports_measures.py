"""PV2-REPORTS: the dimension + measure table and the report policy table (pure, no DB)."""
from tests.chatbot.pv2.reports_schema_shim import install

schema = install()
ReportSpec, Filter, Period = schema.ReportSpec, schema.Filter, schema.Period

import re

import pytest

from app.services.chatbot.pv2 import report_measures as rm
from app.services.chatbot.pv2 import report_policy as rp
from app.services.reports import ask


def test_datasets_are_sales_and_chat():
    assert set(rm.DATASETS) == {"sales", "chat"}


def test_index_lines_stable():
    assert rm.index_lines() == rm.index_lines()
    assert all(isinstance(line, str) for line in rm.index_lines())


def test_index_lines_have_no_dates_or_counts():
    text = "\n".join(rm.index_lines())
    assert not re.search(r"\d{4}-\d{2}", text)
    assert not re.search(r"(?<![\w/])\d+(?![\w/])", text)


def test_index_lines_length_cap():
    assert len("\n".join(rm.index_lines())) <= 1500


@pytest.mark.parametrize("key", list(ask.DIMENSIONS) + list(ask.CHAT_MEASURES) + ["region"])
def test_index_lists_key(key):
    assert key in "\n".join(rm.index_lines())


@pytest.mark.parametrize(
    "dataset,dim,group_by,filter_param,trend",
    [
        ("sales", "location", "location", "warehouse_codes", False),
        ("sales", "customer_agent", None, "customer_agent_ids", False),
        ("sales", "month", "month", None, True),
        ("chat", "week", "week", None, True),
    ],
)
def test_dimension_rows(dataset, dim, group_by, filter_param, trend):
    d = rm.DATASETS[dataset].dimensions[dim]
    assert d.group_by == group_by
    if filter_param is not None:
        assert d.filter_param == filter_param
    assert d.trend is trend


@pytest.mark.parametrize("measure,basis", [("failed", "turns"), ("asks", "asks")])
def test_chat_measure_basis(measure, basis):
    assert rm.DATASETS["chat"].measures[measure].basis == basis


@pytest.mark.parametrize(
    "name,mode,proposed",
    [
        ("dataset", "default", None),
        ("measure", "ask", None),
        ("basis", "ask", None),
        ("period", "ask", "this_month"),
        ("top_n", "ask", 10),
    ],
)
def test_policy_modes(name, mode, proposed):
    rule = rp.POLICY[name]
    assert rule.mode == mode
    assert rule.proposed_default == proposed


def test_policy_dataset_default_sales_and_proposals_not_applied():
    assert rp.POLICY["dataset"].default == "sales"
    assert rp.default_for("dataset") == "sales"
    for name in ("measure", "basis", "period", "top_n"):
        assert rp.default_for(name) is None
