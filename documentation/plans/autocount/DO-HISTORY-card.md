# DO-HISTORY owner card (13 Oct 2026, v2 after your direction)

Plan: `PLAN-do-history-backfill-13oct.md`. Counts: prod clone `sorento_prod_20261012` (crew, read-only).

## Your direction, applied

1. **The backfill runs from the shared service's existing DocDate DO job.** The CRM script is dropped.
2. **The start is 2023-09-01**, set in the shared service. MCH's first SO in the CRM is also
   2023-09-01, so both books start inside the CRM's SO history.
3. **Only provable SO-line links.** The key link wins when present. Otherwise a link is made when
   the SO has exactly one line of that product, narrowed by location if needed. This rule made all
   29,040 of today's links, and they stay. When several lines could match (about 13,259 today), the
   DO line stays unlinked, with no guessing. AutoCount's API sends no line key (0 of 45,157 lines).

## Who does what

| Where | What |
| --- | --- |
| Crew (pending) | Find a line-key source for the ambiguous lines (DODTL via FXView, or an API wrapper change). |
| Shared service | Start floor 2023-01-01 -> 2023-09-01 (`doc_feed/constants.py:48`). DO feed rows for Sorento and Mocha. Run the existing DocDate backfill 2023-09-01 -> today, dry run on the prod clone first. |
| CRM (this PR) | Link by key, else unique product, else unique product at the same location. Otherwise unlinked. Mark pre-cutover DOs delivered as they land. Keep a backfill run out of the audit log and the embedding queue. |

## What you will see

| Before | After |
| --- | --- |
| AutoCount DOs only from Aug 2026 | AutoCount DOs from 1 Sep 2023 |
| A backfilled DO would read "New Order" (outstanding) | DOs before the cutover with no tracking read Delivered, dated on the DO date, marked "assumed" (one SQL statement undoes it) |
| DO lines linked by key or a unique product match | Same, plus a unique match at the same location; several candidates stay unlinked |

Not changed: SO demand, OI, the planning board, and emails (the DO import writes none of them).

## One question

A cutover of 1 Sep 2023 (the backfill start) marks **no** DO delivered. About 270,000 backfilled
DOs (Sep 2023 to Jul 2026) would then read **outstanding**, because they have no Order Tracking.

- **(a) Recommended.** Backfill from 1 Sep 2023, and treat DOs before **1 Jul 2026** that have no
  tracking as delivered (your "fully delivered").
- (b) Accept them as outstanding, or upload tracking for them.
