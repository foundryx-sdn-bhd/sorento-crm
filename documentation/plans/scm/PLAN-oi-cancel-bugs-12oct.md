# PLAN: OI cancel bugs from SO422492 (12 Oct)

Status: in review (small fix track: bug fixes, red-first, no migration, no auth change).
Lane OI-CANCEL-BUGS, PR #1599.

Prod incident SO422492. Six findings from the crew investigation on a restored prod copy
(code aa1df029c; main 4ad65522 carries the same lines). Each has a red-first test in
`sorento_crm_backend/tests/test_oi_cancel_bugs_so422492.py` (B in
`tests/test_planning_change_repush.py`).

## A: wrong CANCEL ALL for a line whose AutoCount change is undecided

Cause: confirming any line of an order drops every line with an undecided planning change
from the new revision, either named in `uncover_line_ids` (`planning_change_service.py`,
`undecided_changed_line_ids`) or not carried because its snapshot drifted
(`ProjectSupplyService._carry_snapshot_has_drifted`). Absent from `buy_lines`, its raised row
was cancelled whole by `ProjectOrderInquiryService._retire_uncovered_rows` ("Superseded by
revision N", handover `cancelled` = CANCEL ALL), for qty the book still needs.

Fix (crew ruling: OI-layer guard, keep the carry-drift rule and the SO403765 uncover fix):
`_retire_uncovered_rows` skips rows on a line with a PENDING, undecided, non-`cancelled`
planning change row (`_lines_with_undecided_planning_change`). The explicit reject path
(`only_line_ids`) is unchanged. When CS decides, the confirm settles the same row by the delta.

## B: a re-changed NEW line lost its planning change row

Cause: `build_batch` retired the older undecided `added` row ("Line changed again") when a
later change failed the held-or-inquiry gate, with no replacement, so the new line's qty was
never ordered.

Fix (crew ruling: any re-change, one live row per line): `_build_row(..., replaces_added=True)`
bypasses the gate and builds an `added` replacement for the line's current product, qty and
date, which replaces the older row in the same open batch. AC-2 of
`PLAN-esb-change-row-refresh` is restated: the line still has exactly one live row.

## C: a pure split printed CANCEL + re-order

Fix: `_apply_pure_splits` / `_split_row` at the top of `refresh_for_decision`. In one confirm,
a line whose single live ORDER row gives back exactly what fresh lines (no rows yet) of the
same product and location take: the quantity, the PO links covering it and purchasing's
acknowledgement move to rows for the new lines; no cancel/order handover is queued. Email
wording for a split stays with lane EMAIL-SO-CHANGES (#1596).

## D: CANCEL BALANCE with a blank location

Cause: `_oi_demand_rows` read the location only off the held reserve; a Buy-only line holds
none. Fix: the line's own `stock_location` first (AC-H5), reserve location as fallback.

## E: a cancelled ORDER BACK never reached the email

Cause: `_raise_borrow_shortfalls` cancels raised ORDER_BACK rows on lines no longer marked
order back with no handover. Fix: each cancelled row prints CANCEL ALL unless the same pass
re-raised the same hole (line, item, location).

## F: placement on a closed, received PO

Analysis, no code defect found: a row linked to a fully received document is marked
`redirected_to_pool` (`_redirect_row_if_received`, AC-RL-10) and kept as history. The netting,
`scm.committed_v` (migration 527) and the worklist filters all exclude it, so 133 history +
133 fresh is one order of 133. It is a real double only if the received goods are still free
stock for the line and the own-arrival credit missed them; crew checks that on the restored
copy (SQL in the PR hand-test).
