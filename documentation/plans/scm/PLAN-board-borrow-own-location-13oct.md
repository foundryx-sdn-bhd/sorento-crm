# PLAN: Fulfilment Planning board, borrow from the line's own location

Status: Plan (behaviour card pending owner answer). Track: standard (Size M, worker leads).
Lane: BORROW-OWN-LOC, PR (draft, link in PR).

## Owner ask (13 Oct 2026, verbatim)

"for the order back and borrow, can borrow own location also, cause this situation is I take
my own location 98 own, and borrow 2 from my own location, cause maybe that's from another order,
so I need the BB to appear in the dropdown and I can borrow from there and confirm".

Case: SO424764 line 144 CGB9032B, own location BRW-BB (77 available shown under Reserve).

## What already exists (measured from code on main 2dfb83d4f)

Confirm ALREADY accepts a borrow of another order's committed quantity at the line's own
location: `_check_borrow` refuses only a plain free-stock borrow there
(`project_supply_service.py:6553-6563`); a borrow that names a donor line (`donor_core_line_id`)
goes to `_check_group_borrow` (`:6860`), which has no own-location exclusion. So the gap is the
OFFER list, not Confirm.

The Borrow list for a board line (`_borrow_candidates`, `:10330`) offers the own location only
through three holder shapes, and each one filters:

1. Step 2, later orders holding on hand (`order_borrow_candidates_for`, `:3898`): only donors
   whose required date is at or after `as_of + lead + 14` (`_eligible_donor`, `:3860`). A
   near-dated holder is never offered.
2. Manual group borrow (`_group_borrow_offer_rows`, `:10665`, over `_group_borrow_donors`,
   `:4409`): only a donor ranked BELOW this line, or one with the SAME sales agent
   (`:10686`). Never a line covered by an active decision (`:4446`), never the same SO, never a
   line in the current selection. With "Borrow from another order" OFF (the default, no settings
   row = off, `scm/borrow_mode.py`), the same-agent ranked-ahead rows are dropped too (`:10463`).
   Also nothing at all when this line is not itself open demand in the group pile (`:4436`).
3. Another project's hold (`:10398`): only a hold with a PROJECT. An adopted AutoCount order's
   confirmed hold has no project (`_hold_rows`, `:9439`), so it is never offered.

The frontend passes the server list through unchanged (`boardAmend.ts:323`,
`BoardLineDecisionPanel.tsx:255`, `:948`). So for SO424764 line 144 the BRW-BB stock beyond the
77 free must be held by orders that are (a) ranked ahead of line 144 under another agent, (b)
already covered by a confirmed decision, or (c) dated inside the reserve window. Which one is a
data question: `sorento_crm_backend/scripts/diagnose_own_location_borrow.py SO424764 144`
(read-only, rolls back) prints every BRW-BB holder and the exact reason it was or was not
offered. The cloud sandbox has no dev DB, so crew runs it.

## Proposed rule (behaviour card, pending owner answer)

At the line's OWN location, every other open order line holding stock there is offered under
Borrow, whatever its rank or agent, named the way the switch already decides (ON: SO + line;
OFF: the location only, D2). Free units stay under Reserve only. Same-SO lines, lines in the
current selection, and decided lines (switch OFF) stay out. Confirm is unchanged.
