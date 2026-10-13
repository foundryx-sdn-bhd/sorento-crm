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
