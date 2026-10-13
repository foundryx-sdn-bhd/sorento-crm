# PLAN: order-back delta on reconfirm (ORDERBACK-DELTA)

Status: Plan (small fix track: backend only, no migration, no auth/RBAC change)

## Journey

Owner hand test of #1629 (13 Oct 2026), SO424764 rev 3: the board showed Saved (2) but the
ORDER BACK email listed 3. The third (CB2519SS-DIY 5) was confirmed in Revision 2 and unchanged.

## Cause

On every reconfirm `_raise_borrow_shortfalls` (project_order_inquiry_service.py) cancels every
still-raised borrow hole (cause `borrow_retired`) and raises it again as a new row, queueing the
new row for the email as `kind="raised"`. So the email lists every order-back of the revision,
not what this confirm changed.

## Goal

A Confirm raises / cancels / emails ONLY the order-backs it creates or changes (qty, location,
line). Unchanged order-backs keep the same row: not cancelled, not re-raised, not re-emailed.

(Detail filled in below as the lane progresses.)
