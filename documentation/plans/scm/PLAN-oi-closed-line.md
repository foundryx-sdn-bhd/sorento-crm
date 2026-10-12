# PLAN: OI-CLOSED-LINE - CS decisions apply on closed SO lines

Status: building, hand-test fix round 12 Oct (full track now: Confirm contract change + UI; no migration)
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

## Hand-test round, owner rules 12 Oct 2026

Owner hand test on SO416732 (CB2807-DIY delivered 2/2): the Decide dialog skipped "only 0 free at BRW".
Owner rules: (R1) "the user will come in to plan AFTER they issue the DO": every planning stock limit applies
ONLY to the undelivered part; the delivered part is never capped. (R2) An undelivered shortfall is a warning,
never a block; "one bad line never refuses the whole order".

6. FE `_shared/lib/boardAmend.ts` `decideComposition`: draws free stock only for `qty - qty_delivered`; a short
   Reserve (own / BRW) composes the whole line on the first location and returns `warning`, never `skip`.
   `BoardDecideControl.tsx`: "N line(s) short: only X free at BRW", toast "n short: ...".
7. FE `BoardLineDecisionPanel.tsx`: "Only N available here" compares only the undelivered part (credited
   front-first like `delivered_credits`), amber, never a Save blocker; Add location offers 0-free bins on a line
   with deliveries; Order back shows whenever Buy > 0 (SO424764 L144: Reserve 98 + order back 2).
8. BE Confirm (`project_supply_service.py`): Reserve capacity, the R14 on-hand guard and Borrow free / held
   record a shortfall (`_record_short`) instead of refusing; holds are net of it; the snapshot carries
   `stock_short`; the result returns `stock_short_notices`; `ProjectOrderInquiryService.record_stock_short`
   queues a STOCK SHORT line in the same handover email (no purchasing row raised).
9. BE `api/v1/projects/fulfilment_planning.py` `confirm_supply`: runs through `_write_holding_back` (#1362), so
   a line that still cannot be written is held back and named (`lines_held_back`); refused only when nothing is
   left. Not for a planning-change press.
10. Old tests pinning AC-C02 / AC-C03 / R14 refusals are restated to R2, keeping their invariants (holds never
    exceed stock; two lines never share units).

## Tests (red first)

New `tests/scm/test_oi_closed_line.py` (AC-1..AC-10) and `tests/test_oi_closed_line_cleanup.py` (AC-11..14).

## Owner step after deploy

Dry run on prod, review output, then `--apply --cancel-balance CKSW015` for OI-2609-0724 (see PR body).
