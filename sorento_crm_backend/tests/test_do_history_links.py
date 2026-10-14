"""DO-HISTORY C1: a DO line links only to a provable SO line (owner rules 13 and 14 Oct).

Plan: documentation/plans/autocount/PLAN-do-history-backfill-13oct.md sections 0b and 3.
UAC:  documentation/plans/autocount/do-history-backfill-13oct-acceptance-criteria.md (AC-DH20..26).

Order of the resolver: the `FromDocDtlKey` wins when sent; else the one line of the product in SO
`FromDocNo`. When the SO holds several lines of the product, the DO line links to the SO +
product group (its own `from_doc_no` + `product_id`) and to no single line: no warehouse, Seq,
position or quantity pick (owner, 14 Oct: "never a guessed line").
The key / unique / two-candidate cases are pinned in tests/test_ingest_autocount_do_grn.py
(AC-AG031, AC-DSL001, AC-DSL004); this file repeats them in one place next to the group ones.
"""
from __future__ import annotations

import copy

from app.models.inventory import Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from tests.test_ingest_autocount_do_grn import (  # noqa: F401 - env is a fixture
    _another,
    _records,
    _with_from,
    do_records,
    env,
)

SO_NO = "ZZSO-DH01"


def _wh(env, code: str) -> str:
    row = Warehouse(warehouse_code=code, warehouse_name=code, company_id=env.company)
    env.db.add(row)
    env.db.flush()
    return str(row.id)


def _so(env, lines: list[tuple[str | None, str]]) -> list[str]:
    """An SO numbered SO_NO with one line of product P1 per (warehouse_id, dtl ref)."""
    so = SalesOrder(so_number=SO_NO, company_id=env.company, source_system="autocount")
    env.db.add(so)
    env.db.flush()
    ids = []
    for warehouse_id, ref in lines:
        line = SalesOrderLine(sales_order_id=so.id, product_id=env.p1, warehouse_id=warehouse_id,
                              qty_ordered=5, source_ref=ref, company_id=env.company)
        env.db.add(line)
        env.db.flush()
        ids.append(str(line.id))
    env.db.commit()
    return ids


def _push_first_line(env, dtl_key: int = 0) -> dict:
    """DO 900001's first line (product P1 at ZZAC-WH1) names SO_NO."""
    rec = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", SO_NO, dtl_key)
    return _records(env.push_do([rec]))["db1:DO:900001"]


def _first_line(env):
    return {l.dtl_key: l for l in env.order_lines(env.order(900001).id)}[910001]


def test_key_link_wins_over_a_unique_product_match(env):
    """AC-DH20: the key names the second line; the product match would name the first."""
    other_wh = _wh(env, "ZZAC-WH2")
    first, second = _so(env, [(env.wh1, "SRT_DB:4101:5101"), (other_wh, "SRT_DB:4101:5102")])
    _push_first_line(env, dtl_key=5102)
    assert _first_line(env).sales_order_line_id == second


def test_unique_product_match_links(env):
    """AC-DH21: no key, one line of the product in the SO."""
    (only,) = _so(env, [(None, "SRT_DB:4101:5101")])
    r = _push_first_line(env)
    assert "so_line_unresolved" not in r.get("warnings", [])
    assert _first_line(env).sales_order_line_id == only


def test_two_lines_of_the_product_without_locations_stay_unlinked(env):
    """AC-DH22: no guess."""
    _so(env, [(None, "SRT_DB:4101:5101"), (None, "SRT_DB:4101:5102")])
    r = _push_first_line(env)
    assert "so_line_unresolved" in r["warnings"]
    assert _first_line(env).sales_order_line_id is None


def _assert_group_linked(env, r: dict) -> None:
    """No single line, and the line still names its SO + product group."""
    line = _first_line(env)
    assert "so_line_unresolved" in r["warnings"]
    assert line.sales_order_line_id is None
    assert (line.from_doc_no, str(line.product_id)) == (SO_NO, str(env.p1))


def test_unique_product_match_links_at_another_location(env):
    """AC-DH26: the one line of the product links even when it sits at another warehouse."""
    other_wh = _wh(env, "ZZAC-WH2")
    (only,) = _so(env, [(other_wh, "SRT_DB:4101:5101")])
    r = _push_first_line(env)
    assert "so_line_unresolved" not in r.get("warnings", [])
    assert _first_line(env).sales_order_line_id == only


def test_two_lines_at_different_locations_link_the_group_not_a_line(env):
    """AC-DH23 (owner 14 Oct): the DO line sits at one candidate's warehouse; still no line."""
    other_wh = _wh(env, "ZZAC-WH2")
    _so(env, [(env.wh1, "SRT_DB:4101:5101"), (other_wh, "SRT_DB:4101:5102")])
    r = _push_first_line(env)
    _assert_group_linked(env, r)


def test_two_lines_at_the_same_location_stay_unlinked(env):
    """AC-DH24: narrowing leaves two."""
    other_wh = _wh(env, "ZZAC-WH2")
    _so(env, [(env.wh1, "SRT_DB:4101:5101"), (env.wh1, "SRT_DB:4101:5102"),
              (other_wh, "SRT_DB:4101:5103")])
    r = _push_first_line(env)
    assert "so_line_unresolved" in r["warnings"]
    assert _first_line(env).sales_order_line_id is None


def test_a_candidate_without_location_blocks_narrowing(env):
    """AC-DH24: the line with no warehouse could be the real one."""
    _so(env, [(env.wh1, "SRT_DB:4101:5101"), (None, "SRT_DB:4101:5102")])
    r = _push_first_line(env)
    assert "so_line_unresolved" in r["warnings"]
    assert _first_line(env).sales_order_line_id is None


def test_waiting_fill_never_narrows_by_location(env):
    """AC-DH25: the DO landed before its SO; a later batch leaves it group-linked."""
    _push_first_line(env)
    assert _first_line(env).sales_order_line_id is None
    other_wh = _wh(env, "ZZAC-WH2")
    _so(env, [(env.wh1, "SRT_DB:4101:5101"), (other_wh, "SRT_DB:4101:5102")])
    env.push_do([_another(do_records()[1], 900007, "ZZDO-DH07", 910070)])
    env.db.expire_all()
    line = _first_line(env)
    assert line.sales_order_line_id is None
    assert (line.from_doc_no, str(line.product_id)) == (SO_NO, str(env.p1))
