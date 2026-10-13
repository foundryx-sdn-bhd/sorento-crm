"""Exact attribution of key-less AutoCount cancel lines (AC-CANCEL-CRM, owner rule 13 Oct).

Pure: `resolve_group` takes one (order, item, location) group and decides it by arithmetic only.
A group is its order lines i with transferred qty T_i (AutoCount TransferedQty less the live
line-key cancels on that line), the group's moved qty D (DO / GRN) and the key-less cancel lines
whose qty sums to C. Exact or nothing: there is no tie-break, no lowest-Seq fallback and no
guess, and a cancel that cannot be placed exactly names no line.
"""
from __future__ import annotations

import itertools
from decimal import Decimal

from app.services.autocount_cancel_matching import (
    REASON_GROUP_NONE,
    REASON_GROUP_WHOLE,
    REASON_ITEM,
    REASON_ITEM_NOT_ON_ORDER,
    REASON_UNDETERMINED,
    CancelIn,
    CandidateIn,
    resolve_group,
)

D = Decimal


def _c(cid, qty):
    return CancelIn(cid, D(qty))


def _l(lid, transferred):
    return CandidateIn(lid, D(transferred))


def _picture(out):
    return {k: (o.status, o.reason, o.target_id) for k, o in out.items()}


def test_one_line_in_the_group_takes_every_cancel():
    out = resolve_group([_c("x1", 4), _c("x2", 3)], [_l("a", 0)], D(9))
    assert _picture(out) == {"x1": ("matched", REASON_ITEM, "a"),
                             "x2": ("matched", REASON_ITEM, "a")}


def test_no_line_of_the_item_and_location_names_nothing():
    out = resolve_group([_c("x1", 4)], [], D(0))
    assert _picture(out) == {"x1": ("unmatched", REASON_ITEM_NOT_ON_ORDER, None)}
    assert out["x1"].candidates == ()


def test_nothing_moved_and_the_cancel_is_every_transfer_is_exact_per_line():
    """D = 0 and C = sum(T): each line's whole transfer is its cancel. The group is matched and
    no single line is named; each line's share is its own T."""
    out = resolve_group([_c("x1", 8), _c("x2", 6)], [_l("a", 10), _l("b", 4)], D(0))
    assert _picture(out) == {"x1": ("matched", REASON_GROUP_WHOLE, None),
                             "x2": ("matched", REASON_GROUP_WHOLE, None)}
    assert out["x1"].candidates == ("a", "b")


def test_a_zero_cancel_over_several_lines_cancels_nothing():
    out = resolve_group([_c("x1", 0)], [_l("a", 10), _l("b", 4)], D(14))
    assert _picture(out) == {"x1": ("matched", REASON_GROUP_NONE, None)}


def test_a_do_on_the_group_leaves_a_multi_line_cancel_undetermined():
    """D > 0 and C > 0 over 2+ lines: which line was cancelled cannot be known (the old
    tie-break case: a repeated item's DO line is never linked to a line)."""
    out = resolve_group([_c("x1", 12)], [_l("a", 12), _l("b", 12)], D(12))
    assert _picture(out) == {"x1": ("unmatched", REASON_UNDETERMINED, None)}
    assert out["x1"].candidates == ("a", "b")


def test_two_lines_that_could_each_hold_the_cancel_are_undetermined_never_the_first():
    """The old lowest-Seq fallback case: no line is picked."""
    out = resolve_group([_c("x1", 12)], [_l("a", 12), _l("b", 12)], D(0))
    assert _picture(out) == {"x1": ("unmatched", REASON_UNDETERMINED, None)}


def test_a_cancel_that_is_not_every_transfer_is_undetermined_even_without_a_do():
    """D = 0 but C != sum(T): T holds something else (an invoice transfer, a DO the CRM lacks)."""
    out = resolve_group([_c("x1", 8)], [_l("a", 10), _l("b", 10)], D(0))
    assert _picture(out) == {"x1": ("unmatched", REASON_UNDETERMINED, None)}


def test_the_answer_does_not_depend_on_the_order_lines_arrive_in():
    cancels = [_c("x1", 8), _c("x2", 6), _c("x3", 0)]
    lines = [_l("a", 10), _l("b", 4)]
    first = _picture(resolve_group(cancels, lines, D(0)))
    for cs in itertools.permutations(cancels):
        for ls in itertools.permutations(lines):
            assert _picture(resolve_group(list(cs), list(ls), D(0))) == first


def test_a_multi_line_group_never_names_a_line():
    for moved in (D(0), D(5)):
        for qtys in ((1,), (5, 5), (14,), (10, 4)):
            out = resolve_group([_c(f"x{i}", q) for i, q in enumerate(qtys)],
                                [_l("a", 10), _l("b", 4)], moved)
            assert all(o.target_id is None for o in out.values())
