# DO-HISTORY owner card (13 Oct 2026, v2 after your direction)

Plan: `PLAN-do-history-backfill-13oct.md`. Counts: prod clone `sorento_prod_20261012` (crew, read-only).

## Your direction, applied

1. **The backfill runs from the shared service's existing DocDate DO job.** The CRM script is dropped.
2. **The start is 2023-09-01**, set in the shared service. MCH's first SO in the CRM is also
   2023-09-01, so both books start inside the CRM's SO history.
3. **Exact SO-line links only.** The current "same product on that SO" fallback is removed. Today all
   29,040 linked DO lines are fallback links: 0 of 45,157 lines carry AutoCount's SO line key.

## Who does what

| Where | What |
| --- | --- |
| Crew (now) | Find out whether the AutoCount API sends the SO line key on DO lines. **Waiting on this.** |
| Shared service | Start floor 2023-01-01 -> 2023-09-01 (`doc_feed/constants.py:48`). DO feed rows for Sorento and Mocha. Run the existing DocDate backfill 2023-09-01 -> today, dry run on the prod clone first. |
| CRM (this PR) | Link DO lines by key only. Mark pre-cutover DOs delivered as they land. Keep a backfill run out of the audit log and the embedding queue. |

## If the key does NOT exist

No exact line link is possible from the API. DOs can land linked to their SO header only, with no
line links and no guessing. Getting the key from somewhere else is a separate decision.

## What you will see

| Before | After |
| --- | --- |
| AutoCount DOs only from Aug 2026 | AutoCount DOs from 1 Sep 2023 |
| A backfilled DO would read "New Order" (outstanding) | DOs before the cutover with no tracking read Delivered, dated on the DO date, marked "assumed" (one SQL statement undoes it) |
| DO lines linked by "same product on that SO" | Linked only by AutoCount's key; otherwise unlinked |

Not changed: SO demand, OI, the planning board, and emails (the DO import writes none of them).

## One question

Cutover for "assume delivered":
- **(a) before 1 Jul 2026** (recommended: your "prior to July")
- (b) before 1 Aug 2026 (where the AutoCount feed starts)
