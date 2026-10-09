"""CA-3 RED tests: the `crm_report_ask` presenter for `dataset == "chat"` (UAC-11, 12, 14, 19).

Plan contract, "Reply": one fact per line, no " · " anywhere. Head line names the unit, then
`Period: <from> to <to>`, ranked lines, `and N more`, `Total: <n> <unit>`, coverage last; the
coverage line is FIRST instead when the labelled share is under 80%.

Pinned where the plan is silent (flagged to the captain):
* the plan's example shows 17% with the coverage line LAST while UAC-12 says first under 80%:
  UAC-12 is the rule pinned here (the example's 17% is read as illustrative);
* dates print as `1 Sep 2026` (the example's `Period:` line);
* the chat rows are not drill rows (they carry no keys): `drill_rows` stays None;
* a failed-turns total/row unit word is not pinned beyond the number.
"""
from __future__ import annotations

import pytest

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.presenters import _report_ask, _report_ask_envelope

COVERAGE = "Topics labelled: {p}% of asks (taxonomy v1). From bot traces, 5 Sep 2026 on."
NO_ASKS = "No asks in that period. Chat data starts 5 Sep 2026."


def _body(**over):
    body = {
        "status": "ok", "message": None, "dataset": "chat", "basis": "asks", "measure": "asks",
        "unit": "asks", "group_by": "topic", "group_label": "Topic", "sort": "desc",
        "date_from": "2026-09-01", "date_to": "2026-09-30", "filters": [],
        "rows": [
            {"rank": 1, "name": "Availability", "value": 124},
            {"rank": 2, "name": "Product ETA", "value": 85},
        ],
        "more": 12, "total_count": 14, "total": {"value": 399},
        "coverage": {"percent": 85, "taxonomy_version": 1, "line": COVERAGE.format(p=85)},
    }
    body.update(over)
    return body


def _lines(body):
    return _report_ask(body).split("\n")


def test_the_ranking_reply_is_the_contract_shape_line_for_line():
    assert _lines(_body()) == [
        "Top 2 topics by asks",
        "Period: 1 Sep 2026 to 30 Sep 2026",
        "1. Availability: 124 asks",
        "2. Product ETA: 85 asks",
        "and 12 more",
        "Total: 399 asks",
        COVERAGE.format(p=85),
    ]


def test_coverage_comes_first_when_the_share_is_under_80():
    body = _body(coverage={"percent": 17, "taxonomy_version": 1, "line": COVERAGE.format(p=17)})
    lines = _lines(body)
    assert lines[0] == COVERAGE.format(p=17), lines
    assert lines[1] == "Top 2 topics by asks" and lines[-1] == "Total: 399 asks", lines


def test_coverage_at_80_stays_last():
    body = _body(coverage={"percent": 80, "taxonomy_version": 1, "line": COVERAGE.format(p=80)})
    assert _lines(body)[-1] == COVERAGE.format(p=80)


def test_no_more_line_when_nothing_is_cut():
    assert not any(ln.startswith("and ") for ln in _lines(_body(more=0, total_count=2)))


def test_one_row_reads_singular():
    lines = _lines(_body(rows=[{"rank": 1, "name": "Availability", "value": 124}], more=0, total_count=1))
    assert lines[0] == "Top 1 topic by asks", lines


def test_a_filter_is_its_own_fact_line_before_the_period():
    filters = [{"key": "customer", "label": "Customer", "values": ["ZZT Trading Sdn Bhd"]}]
    lines = _lines(_body(filters=filters))
    assert lines[1:3] == ["Customer: ZZT Trading Sdn Bhd", "Period: 1 Sep 2026 to 30 Sep 2026"], lines


def test_thousands_are_separated():
    lines = _lines(_body(rows=[{"rank": 1, "name": "Availability", "value": 1215}], more=0, total_count=1,
                          total={"value": 1215}))
    assert "1. Availability: 1,215 asks" in lines and "Total: 1,215 asks" in lines, lines


def test_contacts_name_their_unit():
    body = _body(measure="contacts", unit="contacts", rows=[{"rank": 1, "name": "Availability", "value": 7}],
                 more=0, total_count=1, total={"value": 7})
    lines = _lines(body)
    assert "1. Availability: 7 contacts" in lines and "Total: 7 contacts" in lines, lines


def test_a_failure_rate_row_prints_percent_and_the_turns_behind_it():
    body = _body(
        basis="turns", measure="failure_rate", unit="%", group_by="week", group_label="Week",
        rows=[{"rank": 1, "name": "2026-09-14", "value": 1.2, "numerator": 14, "denominator": 1215}],
        more=0, total_count=1, total={"value": 1.5, "numerator": 50, "denominator": 3300},
    )
    lines = _lines(body)
    assert "failure rate" in lines[0].lower(), lines
    assert "1. 2026-09-14: 1.2% (14 of 1,215 turns)" in lines, lines
    total = next(ln for ln in lines if ln.startswith("Total: "))
    assert total.startswith("Total: 1.5%"), total


def test_failed_turns_print_their_counts():
    body = _body(basis="turns", measure="failed", unit="turns", rows=[{"rank": 1, "name": "Availability", "value": 9}],
                 more=0, total_count=1, total={"value": 9})
    lines = _lines(body)
    assert "1. Availability: 9" in " ".join(lines) and any(ln.startswith("Total: 9") for ln in lines), lines


def test_a_total_with_no_ranking_is_head_period_total():
    body = _body(group_by=None, group_label=None, rows=[], more=0, total_count=0)
    lines = _lines(body)
    assert lines[1] == "Period: 1 Sep 2026 to 30 Sep 2026" and "Total: 399 asks" in lines, lines
    assert "asks" in lines[0].lower(), lines


@pytest.mark.parametrize("status", ["ok", "no_data"])
def test_no_asks_says_so_in_one_line(status):
    body = _body(status=status, rows=[], more=0, total_count=0, total={"value": 0}, message=NO_ASKS,
                 coverage={"percent": 0, "taxonomy_version": 1, "line": COVERAGE.format(p=0)})
    assert NO_ASKS in _lines(body), _lines(body)


def test_a_busy_reply_is_the_message_only():
    msg = "Too many reports in the last 10 minutes."
    assert _report_ask(_body(status="busy", message=msg, rows=[], coverage=None)) == msg


def test_no_reply_has_a_middot_or_a_dash():
    shapes = [
        _body(),
        _body(group_by=None, rows=[]),
        _body(basis="turns", measure="failure_rate", unit="%", rows=[
            {"rank": 1, "name": "2026-09-14", "value": 1.2, "numerator": 14, "denominator": 1215}],
            total={"value": 1.5, "numerator": 50, "denominator": 3300}),
        _body(rows=[], message=NO_ASKS),
    ]
    for body in shapes:
        text = _report_ask(body)
        assert " · " not in text and chr(0x2014) not in text and chr(0x2013) not in text, text


def test_chat_rows_are_not_drill_rows():
    envelope = _report_ask_envelope(_body())
    assert envelope["drill_rows"] is None, envelope
    assert envelope["has_result"] is True and envelope["response"] == _report_ask(_body()), envelope


def test_the_catalog_declares_the_dataset_and_chat_params():
    spec = next(s for s in CATALOG if s.name == "crm_report_ask")
    params = set(spec.query_params)
    assert {"dataset", "subjects", "product_ids", "customer_ids", "measure", "basis", "group_by", "top_n"} <= params, \
        sorted(params)
    assert "chat" in spec.description, "the tool description names the chat dataset"


def test_review_sort_asc_heads_bottom():
    lines = _lines(_body(sort="asc"))
    assert lines[0] == "Bottom 2 topics by asks", lines


@pytest.mark.parametrize(
    ("unit", "plural", "singular"),
    [("asks", "asks", "ask"), ("turns", "turns", "turn"), ("contacts", "contacts", "contact")],
)
def test_a_figure_of_exactly_one_uses_the_singular_unit(unit, plural, singular):
    one = _report_ask(_body(unit=unit, rows=[{"rank": 1, "name": "Availability", "value": 1}], more=0,
                            total_count=1, total={"value": 1}))
    assert f"Availability: 1 {singular}" in one and f"1 {plural}" not in one, one
    two = _report_ask(_body(unit=unit, rows=[{"rank": 1, "name": "Availability", "value": 2}], more=0,
                            total_count=1, total={"value": 2}))
    assert f"Availability: 2 {plural}" in two, two


def test_a_failure_rate_over_one_turn_reads_singular():
    body = _body(unit="%", measure="failure_rate",
                 rows=[{"rank": 1, "name": "Availability", "value": 100.0, "numerator": 1, "denominator": 1}],
                 more=0, total_count=1, total={"value": 100.0, "numerator": 1, "denominator": 1})
    assert "(1 of 1 turn)" in _report_ask(body) and "1 turns" not in _report_ask(body)
