"""CA-3 owner hand test round, RED: the `crm_report_ask` chat presenter prints the card agent on a
customer ranking and the `Unclassified askers: N` line (UAC-28, UAC-31).

Pinned where the plan is silent (flagged to the captain):
* a customer row carries `agent` (name or null); the line is `1. <customer> (agent <name>): N asks`
  and `1. <customer> (no agent): N asks` for null. Only rows of a `customer` ranking print it;
* the body carries `unclassified_askers` (int). Present (even 0) -> exactly one line
  `Unclassified askers: N` (thousands separated like other figures); null or absent -> no such line;
* the line's position is not pinned, only that it is its own line, never merged into another;
* the head, row and total wording for the new group_by words (`asker`, `salesperson`, `category`,
  `brand`) is free: only that each row prints as `<rank>. <name>: <n> asks` is pinned.
"""
from __future__ import annotations

import pytest

from sorento_crm_mcp.presenters import _report_ask

COVERAGE = "Topics labelled: 85% of asks (taxonomy v1). From bot traces, 5 Sep 2026 on."


def _body(**over):
    body = {
        "status": "ok", "message": None, "dataset": "chat", "basis": "asks", "measure": "asks",
        "unit": "asks", "group_by": "customer", "group_label": "Customer", "sort": "desc",
        "date_from": "2026-09-01", "date_to": "2026-09-30", "filters": [],
        "rows": [
            {"rank": 1, "name": "ZZT Trading Sdn Bhd", "value": 12, "agent": "ZZT Agent Two"},
            {"rank": 2, "name": "ZZT Hardware", "value": 3, "agent": None},
        ],
        "more": 0, "total_count": 2, "total": {"value": 15},
        "coverage": {"percent": 85, "taxonomy_version": 1, "line": COVERAGE},
    }
    body.update(over)
    return body


def _lines(body):
    return _report_ask(body).split("\n")


def test_a_customer_row_prints_its_card_agent():
    lines = _lines(_body())
    assert "1. ZZT Trading Sdn Bhd (agent ZZT Agent Two): 12 asks" in lines, lines


def test_a_customer_row_with_no_agent_says_so():
    lines = _lines(_body())
    assert "2. ZZT Hardware (no agent): 3 asks" in lines, lines


def test_a_customer_row_with_no_agent_key_prints_plain():
    rows = [{"rank": 1, "name": "ZZT Hardware", "value": 3}]
    lines = _lines(_body(rows=rows, total_count=1, total={"value": 3}))
    assert "1. ZZT Hardware: 3 asks" in lines, lines


@pytest.mark.parametrize("group_by", ["topic", "product", "asker", "salesperson", "category", "brand"])
def test_other_rankings_never_print_an_agent(group_by):
    rows = [{"rank": 1, "name": "Alpha", "value": 4}]
    lines = _lines(_body(group_by=group_by, group_label=group_by.title(), rows=rows, total_count=1,
                         total={"value": 4}))
    assert "1. Alpha: 4 asks" in lines, lines
    assert not any("agent" in ln.lower() and ln.startswith("1.") for ln in lines), lines


def test_an_unclassified_askers_line_is_printed_with_the_count():
    lines = _lines(_body(group_by="asker", group_label="Asker", unclassified_askers=3,
                         rows=[{"rank": 1, "name": "Dealer", "value": 4}], total_count=1, total={"value": 4}))
    assert lines.count("Unclassified askers: 3") == 1, lines


def test_the_line_is_printed_even_when_the_count_is_zero():
    lines = _lines(_body(unclassified_askers=0))
    assert "Unclassified askers: 0" in lines, lines


def test_the_count_is_thousands_separated():
    assert "Unclassified askers: 1,204" in _lines(_body(unclassified_askers=1204))


@pytest.mark.parametrize("over", [{}, {"unclassified_askers": None}])
def test_no_line_when_the_reply_does_not_split_or_filter_by_asker(over):
    text = _report_ask(_body(**over))
    assert "Unclassified askers" not in text, text


def test_the_line_survives_a_low_coverage_reorder():
    body = _body(unclassified_askers=2, coverage={"percent": 17, "taxonomy_version": 1, "line": COVERAGE})
    lines = _lines(body)
    assert "Unclassified askers: 2" in lines and lines[0] == COVERAGE, lines


def test_no_dash_or_middot_in_the_new_lines():
    text = _report_ask(_body(unclassified_askers=2))
    assert " · " not in text and chr(0x2014) not in text and chr(0x2013) not in text, text


def test_the_asker_ranking_head_reads_askers_never_asked_bys():
    rows = [{"rank": 1, "name": "Dealer", "value": 4}, {"rank": 2, "name": "Salesperson", "value": 3}]
    head = _lines(_body(group_by="asker", group_label="Asked by", rows=rows, total_count=2,
                        total={"value": 7}, unclassified_askers=1))[0]
    assert "asked bys" not in head.lower() and "askers" in head.lower(), head


def test_a_customer_row_for_asks_with_no_customer_prints_its_label():
    rows = [{"rank": 1, "name": "ZZT Trading Sdn Bhd", "value": 12, "agent": "ZZT Agent Two"},
            {"rank": 2, "name": "(no customer)", "value": 3, "agent": None}]
    lines = _lines(_body(rows=rows))
    assert "2. (no customer) (no agent): 3 asks" in lines or "2. (no customer): 3 asks" in lines, lines


# owner 10 Oct: unclassified out by default -> the left-out line


def test_the_left_out_line_prints_when_some_were_left_out():
    lines = _lines(_body(unclassified_left_out=33))
    assert lines.count("Unclassified askers left out: 33") == 1, lines


@pytest.mark.parametrize("value", [0, None])
def test_no_left_out_line_for_zero_or_null(value):
    assert "left out" not in _report_ask(_body(unclassified_left_out=value))
    assert "left out" not in _report_ask(_body())


def test_the_existing_split_line_is_unchanged_beside_the_left_out_line():
    lines = _lines(_body(unclassified_askers=2, unclassified_left_out=5))
    assert "Unclassified askers: 2" in lines and "Unclassified askers left out: 5" in lines, lines
