"""Phase 2 RED tests - chat fix PR-B S6 (AC-B8): the `crm_report_ask` ranking reply display.

The renderer is `sorento_crm_mcp.presenters._report_ask(body)` (the old backend anchor is
gone). Behaviour is asserted, not wording, except the "of N" shape of the header.
"""
from __future__ import annotations

import re

from sorento_crm_mcp.presenters import _report_ask

ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _body(**over):
    body = {
        "status": "ok", "message": None,
        "basis": "delivered", "basis_label": "delivered sales", "measure": "amount",
        "group_by": "customer", "group_label": "Customer",
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "filters": [],
        "rows": [
            {"rank": i, "name": f"CUST {i:02d}", "qty": 100 - i, "amount": 5000.0 - i, "keys": [f"k{i}"]}
            for i in range(1, 11)
        ],
        "more": 27, "total_count": 37,
        "total": {"qty": 9000, "amount": 90000.0},
    }
    body.update(over)
    return body


def test_b8_the_header_says_how_many_rows_exist() -> None:
    head = _report_ask(_body()).splitlines()[0]
    assert re.search(r"Top 10 of 37\b", head), head


def test_b8_near_miss_when_every_row_is_shown_there_is_no_of_n() -> None:
    rows = [{"rank": i, "name": f"C{i}", "qty": 5, "amount": 50.0, "keys": []} for i in range(1, 4)]
    head = _report_ask(_body(rows=rows, more=0, total_count=3)).splitlines()[0]
    assert " of " not in head, head


def test_b8_the_sort_measure_is_named_first_by_amount() -> None:
    line = _report_ask(_body(measure="amount")).splitlines()[3]
    assert line.index("RM") < line.index("pcs"), line


def test_b8_the_sort_measure_is_named_first_by_quantity() -> None:
    text = _report_ask(_body(measure="qty"))
    row = next(l for l in text.splitlines() if l.startswith("1. "))
    assert row.index("pcs") < row.index("RM"), row


def test_b8_each_name_is_shown_once_when_two_rows_share_it() -> None:
    rows = [
        {"rank": 1, "name": "SORENTO", "qty": 10, "amount": 100.0, "keys": ["a"]},
        {"rank": 2, "name": "SORENTO", "qty": 5, "amount": 50.0, "keys": ["b"]},
        {"rank": 3, "name": "MOCHA", "qty": 4, "amount": 40.0, "keys": ["c"]},
    ]
    text = _report_ask(_body(group_by="brand", group_label="Brand", rows=rows, more=0, total_count=3))
    assert text.count("SORENTO") == 1, text
    assert "MOCHA" in text


def test_b8_near_miss_two_different_names_are_both_shown() -> None:
    rows = [
        {"rank": 1, "name": "SORENTO", "qty": 10, "amount": 100.0, "keys": ["a"]},
        {"rank": 2, "name": "MOCHA", "qty": 5, "amount": 50.0, "keys": ["b"]},
    ]
    text = _report_ask(_body(group_by="brand", group_label="Brand", rows=rows, more=0, total_count=2))
    assert "SORENTO" in text and "MOCHA" in text


def test_b8_dates_are_readable_not_iso() -> None:
    text = _report_ask(_body())
    assert not ISO.search(text), text
    assert "2026" in text, text


def test_b8_a_total_shows_readable_dates_too() -> None:
    text = _report_ask(_body(group_by=None, rows=[], more=0, total_count=0))
    assert not ISO.search(text), text


def test_b8_month_rows_print_in_date_order_and_readable() -> None:
    rows = [
        {"rank": 1, "name": "2026-09", "qty": 90, "amount": 900.0, "keys": []},
        {"rank": 2, "name": "2026-03", "qty": 50, "amount": 500.0, "keys": []},
        {"rank": 3, "name": "2026-06", "qty": 10, "amount": 100.0, "keys": []},
    ]
    text = _report_ask(_body(group_by="month", group_label="Month", rows=rows, more=0, total_count=3))
    assert not ISO.search(text), text
    lines = [l for l in text.splitlines() if re.match(r"^\s*(\d+\.\s*)?(Mar|Jun|Sep)", l)]
    months = [re.search(r"(Mar|Jun|Sep)", l).group(1) for l in lines]
    assert months == ["Mar", "Jun", "Sep"], text


def test_b8_near_miss_a_non_month_ranking_keeps_its_rank_order() -> None:
    rows = [
        {"rank": 1, "name": "ZED", "qty": 9, "amount": 90.0, "keys": []},
        {"rank": 2, "name": "ALPHA", "qty": 5, "amount": 50.0, "keys": []},
    ]
    text = _report_ask(_body(rows=rows, more=0, total_count=2))
    assert text.index("ZED") < text.index("ALPHA")


# Review round: rows merged by name must be re-sorted, renumbered and recounted.


def _twin_rows():
    return [
        {"rank": 1, "name": "A", "qty": 10, "amount": 100.0, "keys": ["a"]},
        {"rank": 2, "name": "B", "qty": 9, "amount": 90.0, "keys": ["b1"]},
        {"rank": 3, "name": "C", "qty": 8, "amount": 80.0, "keys": ["c"]},
        {"rank": 4, "name": "B", "qty": 5, "amount": 50.0, "keys": ["b2"]},
    ]


def _lines(text):
    return [l for l in text.splitlines() if re.match(r"^\d+\. ", l)]


def test_b8_merged_twin_rows_are_resorted_by_the_sort_measure() -> None:
    text = _report_ask(_body(group_by="brand", group_label="Brand", rows=_twin_rows(), more=6, total_count=10))
    names = [re.match(r"^\d+\. (\S+)", l).group(1) for l in _lines(text)]
    assert names == ["B", "A", "C"], text  # B is 140 after the merge


def test_b8_merged_rows_are_renumbered_one_to_n() -> None:
    text = _report_ask(_body(group_by="brand", group_label="Brand", rows=_twin_rows(), more=6, total_count=10))
    assert [int(re.match(r"^(\d+)\.", l).group(1)) for l in _lines(text)] == [1, 2, 3], text


def test_b8_the_header_counts_the_merged_distinct_rows() -> None:
    head = _report_ask(_body(group_by="brand", group_label="Brand", rows=_twin_rows(), more=6, total_count=10)).splitlines()[0]
    assert head.startswith("Top 3"), head


def test_b8_merged_rows_resort_by_quantity_when_the_measure_is_qty() -> None:
    rows = [
        {"rank": 1, "name": "A", "qty": 10, "amount": 1.0, "keys": []},
        {"rank": 2, "name": "B", "qty": 9, "amount": 1.0, "keys": []},
        {"rank": 3, "name": "B", "qty": 5, "amount": 1.0, "keys": []},
    ]
    text = _report_ask(_body(measure="qty", group_by="brand", group_label="Brand", rows=rows, more=0, total_count=2))
    assert [re.match(r"^\d+\. (\S+)", l).group(1) for l in _lines(text)] == ["B", "A"], text


def test_b8_near_miss_no_twins_keeps_the_route_order() -> None:
    rows = [{"rank": i, "name": n, "qty": 1, "amount": a, "keys": []} for i, (n, a) in enumerate([("X", 5.0), ("Y", 9.0)], 1)]
    text = _report_ask(_body(group_by="brand", group_label="Brand", rows=rows, more=0, total_count=2))
    assert [re.match(r"^\d+\. (\S+)", l).group(1) for l in _lines(text)] == ["X", "Y"], text


def test_b8_the_of_count_is_the_merged_distinct_total_not_the_raw_row_count() -> None:
    """Route rows A, B, B(twin), C with 1 more raw row beyond them (total_count 5 counts the twin):
    3 distinct names are printed, 4 exist in all -> "Top 3 of 4", not "of 5"."""
    rows = [
        {"rank": 1, "name": "A", "qty": 1, "amount": 100.0, "keys": []},
        {"rank": 2, "name": "B", "qty": 1, "amount": 90.0, "keys": []},
        {"rank": 3, "name": "B", "qty": 1, "amount": 50.0, "keys": []},
        {"rank": 4, "name": "C", "qty": 1, "amount": 80.0, "keys": []},
    ]
    head = _report_ask(_body(group_by="brand", group_label="Brand", rows=rows, more=1, total_count=5)).splitlines()[0]
    assert re.search(r"Top 3 of 4\b", head), head
