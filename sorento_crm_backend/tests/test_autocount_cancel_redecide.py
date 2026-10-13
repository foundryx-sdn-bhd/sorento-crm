"""Re-decide every stored AutoCount cancel line under the exact rule (AC-CANCEL-CRM, crew audit).

A copy that ran the old matcher still holds its guesses (`tie_break` / `fallback` rows and the
`qty_cancelled` they put on a line), and a re-push with the same LastModified is `unchanged`, so
nothing re-decides them. `scripts/autocount_cancel_redecide.py` re-decides every stored line of
one company from what is stored: dry run by default (counts only), `--apply` writes, and a second
run changes nothing. Reuses the ingest suite's `env`; every record here is fake.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.autocount_cancel import AutocountCancelLine

from tests.test_ingest_autocount_cancel_docs import (  # noqa: F401 - env is a fixture
    _cancel_lines,
    _detail,
    _push_xs,
    _so,
    _so_line,
    _xs,
    env,
)


def _old_tie_break(env):
    """The old matcher's answer on a two-line group: the whole 12 on line 5002, which then
    read fully cancelled. The exact rule calls this group undetermined."""
    lines = _so(env, lines=((5001, "p1", 12, 12, "wh1"), (5002, "p1", 12, 12, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=12, from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    stored.match_status, stored.match_reason = "matched", "tie_break"
    stored.sales_order_line_id = lines[5002]
    stored.candidate_line_ids = sorted(lines.values())
    env.db.commit()  # before `_so_line`, whose expire_all would drop the edits above
    row = _so_line(env, lines[5002])
    row.qty_cancelled, row.line_status, row.cancelled_by_cancel_doc = 12, "cancelled", True
    env.db.commit()
    return lines


def _run(env, **kw):
    from scripts.autocount_cancel_redecide import run

    return run(env.db, env.company, **kw)


def test_the_dry_run_counts_the_stale_guess_and_writes_nothing(env):
    lines = _old_tie_break(env)
    summary = _run(env)
    assert summary["lines"] == 1
    assert summary["changed"] == 1
    assert summary["moves"] == {"matched:tie_break -> unmatched:undetermined": 1}
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_reason, stored.sales_order_line_id) == ("tie_break", lines[5002])
    assert _so_line(env, lines[5002]).qty_cancelled == Decimal("12")


def test_apply_replaces_the_guess_with_the_exact_answer_and_reopens_the_line(env):
    lines = _old_tie_break(env)
    summary = _run(env, dry_run=False)
    assert summary["changed"] == 1
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_status, stored.match_reason) == ("unmatched", "undetermined")
    assert stored.sales_order_line_id is None
    line = _so_line(env, lines[5002])
    assert (line.qty_cancelled, line.line_status) == (Decimal("0"), "closed")
    assert line.cancelled_by_cancel_doc is False


def test_a_second_apply_changes_nothing(env):
    _old_tie_break(env)
    _run(env, dry_run=False)
    again = _run(env, dry_run=False)
    assert (again["lines"], again["changed"], again["moves"]) == (1, 0, {})


def test_an_exact_answer_the_old_matcher_also_gave_is_left_as_it_is(env):
    """A single-line `item` match is exact under both rules: nothing moves."""
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"),))
    _push_xs(env, [_xs(details=[_detail(951001, qty=4, from_dtl=0)])])
    summary = _run(env, dry_run=False)
    assert (summary["lines"], summary["changed"]) == (1, 0)
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("4")


def test_only_the_company_asked_for_is_touched(env):
    lines = _old_tie_break(env)
    from scripts.autocount_cancel_redecide import run

    summary = run(env.db, env.company_b, dry_run=False)
    assert summary["lines"] == 0
    assert env.db.get(AutocountCancelLine,
                      _cancel_lines(env, 950001)[951001].id).match_reason == "tie_break"
    assert _so_line(env, lines[5002]).qty_cancelled == Decimal("12")


def _stale_no_fit(env, ordered, transferred):
    """The old matcher left a single-line cancel unmatched (`no_fit`); the exact rule puts it
    on the line."""
    lines = _so(env, lines=((5001, "p1", ordered, transferred, "wh1"),))
    _push_xs(env, [_xs(details=[_detail(951001, qty=transferred, from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    stored.match_status, stored.match_reason, stored.sales_order_line_id = (
        "unmatched", "no_fit", None)
    env.db.commit()
    row = _so_line(env, lines[5001])
    row.qty_cancelled, row.cancelled_by_cancel_doc = 0, False
    row.line_status = "closed" if transferred >= ordered else "open"
    env.db.commit()
    return lines


def _record_hooks(monkeypatch):
    import scripts.autocount_cancel_redecide as script

    calls = []
    monkeypatch.setattr(script, "run_cancel_hooks",
                        lambda db, service, actor: calls.append(service.derived))
    return calls


def test_apply_hands_a_newly_cancelled_line_to_the_oi_retire_hook_once(env, monkeypatch):
    """--apply runs the ingest's own post-commit hooks: a line the exact answer fully cancels
    goes to the #1530 OI retire with its cancel doc number. The dry run never calls them."""
    calls = _record_hooks(monkeypatch)
    lines = _stale_no_fit(env, 12, 12)
    preview = _run(env)
    assert (preview["newly_cancelled"], calls) == (1, [])
    summary = _run(env, dry_run=False)
    assert summary["newly_cancelled"] == 1
    assert [d.newly_cancelled for d in calls] == [[(lines[5001], "ZZXS-0001")]]
    assert _so_line(env, lines[5001]).line_status == "cancelled"
    calls.clear()
    assert _run(env, dry_run=False)["newly_cancelled"] == 0
    assert all(not d.newly_cancelled for d in calls)


def test_apply_hands_a_partial_cancel_to_the_planning_change_hook(env, monkeypatch):
    """A partial cancel the exact answer adds moves the open line's planned qty (Q4 a)."""
    calls = _record_hooks(monkeypatch)
    lines = _stale_no_fit(env, 10, 4)
    summary = _run(env, dry_run=False)
    assert summary["plan_moves"] == 1
    assert [d.plan_moves for d in calls] == [[(lines[5001], Decimal("10"), Decimal("6"))]]


def test_a_run_for_one_company_leaves_the_other_companys_guess_alone(env):
    """Security review nit: both companies hold a stale guess; applying for A moves A only."""
    from datetime import date

    from app.models.autocount_cancel import AutocountCancelDoc
    from app.models.inventory import Warehouse
    from app.models.order import SalesOrder, SalesOrderLine

    a_lines = _old_tie_break(env)
    b = env.company_b
    product = env.product("ZZAC-PB", company_id=b)
    wh = Warehouse(warehouse_code="ZZAC-WHB", warehouse_name="whb", company_id=b)
    so = SalesOrder(so_number="ZZSO-C001", company_id=b, source_system="autocount")
    env.db.add_all([wh, so])
    env.db.flush()
    b_lines = []
    for dtl in (6001, 6002):
        line = SalesOrderLine(sales_order_id=so.id, product_id=product, warehouse_id=wh.id,
                              qty_ordered=12, qty_delivered=12, line_status="closed",
                              source_ref=f"SRT_DB:4002:{dtl}", company_id=b, line_no=dtl % 1000)
        env.db.add(line)
        env.db.flush()
        b_lines.append(str(line.id))
    doc = AutocountCancelDoc(company_id=b, kind="cancel_so", source_book="db1", doc_key=950901,
                             doc_no="ZZXS-B001", doc_date=date(2026, 10, 1))
    env.db.add(doc)
    env.db.flush()
    stale = AutocountCancelLine(company_id=b, cancel_doc_id=doc.id, dtl_key=951901, qty=12,
                                item_code="ZZAC-PB", location_code="ZZAC-WHB",
                                from_doc_type="SO", from_doc_no="ZZSO-C001",
                                match_status="matched", match_reason="tie_break",
                                sales_order_line_id=b_lines[1], candidate_line_ids=b_lines)
    env.db.add(stale)
    row = env.db.get(SalesOrderLine, b_lines[1])
    row.qty_cancelled, row.line_status, row.cancelled_by_cancel_doc = 12, "cancelled", True
    env.db.commit()

    summary = _run(env, dry_run=False)
    assert summary["moves"] == {"matched:tie_break -> unmatched:undetermined": 1}
    assert _so_line(env, a_lines[5002]).qty_cancelled == Decimal("0")
    env.db.expire_all()
    kept = env.db.get(AutocountCancelLine, stale.id)
    assert (kept.match_reason, kept.sales_order_line_id) == ("tie_break", b_lines[1])
    line = env.db.get(SalesOrderLine, b_lines[1])
    assert (line.qty_cancelled, line.line_status) == (Decimal("12"), "cancelled")
