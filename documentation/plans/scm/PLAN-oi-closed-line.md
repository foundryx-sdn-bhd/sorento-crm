# PLAN: OI-CLOSED-LINE - CS decisions apply on closed SO lines

Status: building (M, LEAD pattern; small diff, no migration, no UI change)
UAC: `oi-closed-line-acceptance-criteria.md`. Card: `oi-closed-line-behaviour-card.md` (owner approved v2, 11 Oct).

## Problem

SO373923 / OI-2609-0724: AutoCount closed lines as delivered at 02:58 on 6 Oct; CS applied a planning batch
right after with Use BRW / OB BRW-BB decisions; apply dropped them "the sales-order line is closed"
(`sorento_crm_backend/app/services/planning_change_service.py:4170-4192`), so the OI rows stayed raised and the
purchasing email left them out.

## Changes

1. `planning_change_service.py:4164-4192`: supersede only rows whose core line is `cancelled` (reason "the
   sales-order line is cancelled"); a `closed` line flows to confirm like an open one. This reverses #1092's R3
   minimum for closed lines (owner ruling 11 Oct: line status must not decide whether a decision applies).
2. `project_supply_service.py` `_check_line` capacity recheck (~5640-5800): the line's delivered qty
   (`min(qty_delivered, plan qty)`) is credited before Reserve / Borrow components are drawn against live
   capacity; only the remainder is checked and charged to `capacity_left`. Hold cap unchanged (`_open_of`).
3. Nothing in ingest, refresh_for_decision or the email builder changes: Confirm already cancels raised Buy rows
   at buy 0 and queues them (`project_order_inquiry_service.py:1349-1368`).
4. `sorento_crm_backend/scripts/oneoff/oi_closed_line_cleanup.py`: dry-run default, `--apply`,
   `--cancel-balance ITEM`; uses the service's `_record_handover` so purchasing gets one email after commit.
5. Existing test `tests/scm/test_board_received_stock_s1_apply.py::test_ac_s1_3_...` flips: the closed row now
   applies (updated by the tester, owner ruling cited).

## Tests (red first)

New `tests/scm/test_oi_closed_line.py` (AC-1..AC-10) and `tests/test_oi_closed_line_cleanup.py` (AC-11..14).

## Owner step after deploy

Dry run on prod, review output, then `--apply --cancel-balance CKSW015` for OI-2609-0724 (see PR body).
