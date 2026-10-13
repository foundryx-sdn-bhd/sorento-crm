# PLAN: Fulfilment Planning board, borrow from the line's own location

Status: Built, in review; awaiting owner hand test. Track: small fix (no migration, no auth/RBAC
change, backend diff under 300 lines; Size M by the board's sizing).
Lane: BORROW-OWN-LOC, PR #1629.
UAC: `board-borrow-own-location-13oct-acceptance-criteria.md`.

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
data question, answered with a throwaway read-only diagnostic (`scripts/diagnose_own_location_borrow.py`,
commit 2ae4ec6e, removed once the owner ruled), which crew ran because the cloud sandbox has no
dev DB.

## Why BRW-BB never showed (crew ran the diagnostic on the 12 Oct prod clone)

SO424764 is not in the dev DB, so crew ran the script on `sorento_prod_20261012`. Switch OFF;
26 other open BRW-BB lines, no confirmed holds there.

1. On the board, `demand_facts` (`:4517`) never sets the fact's `core` line, so
   `_group_borrow_donors` finds no entry for the asker and returns no donors at all (`:4436`).
   The manual group-borrow list is therefore empty for every board line. Reproduced locally.
   Latent defect, out of scope here (the owner's rule makes it moot for this ask); follow-up.
2. Step 2 offers only lines the assignment gave BRW-BB on-hand to AND dated after
   `as_of + lead + 14`; the on-hand is assigned to the earliest-dated lines, inside the window.
3. Plain free stock at the own location was barred by design.

## Rule (owner, 13 Oct 2026, card answers Q1 (a), Q2 "still offer", Q3 "holder does not matter")

The line's own location ALWAYS appears in the Borrow dropdown: any holders, any rank or agent,
decided or not, switch on or off, even with nothing there. The typed quantity is not capped:
Confirm passes. The reason box stays mandatory. No holder naming.

No double counting: the own row offers `free_qty` 0 (its free units are Reserve's), so
`qty_borrow_available` and the bulk borrow fill never count them again. At Confirm a Reserve at
the own location draws the borrow ledger first, so an own-location Borrow is held only from what
is free after this line's own Reserve; the rest is confirmed as decided and recorded short with
the existing OI-CLOSED-LINE sentence. It reads as Borrow, never Own, on the board.

## Build

- `_borrow_candidates`: append the plain own-location row, last, never `recommended`.
- `_check_borrow`: drop the own-location refusal; own location skips the Reserve-pool refusal
  and goes through the free check + `record_short`.
- `_check_line` reserve loop: a Reserve at `fact.own_code` draws `borrow_left`.
- No FE code change: an unrunged borrow already reads `borrow_other` (`supplyVocabulary.ts:321`).

## Acceptance (tests)

`tests/scm/test_borrow_own_location.py`: own row offered on the board in both switch states,
last, not recommended, 0 free, `qty_borrow_available` unchanged; offered with no stock anywhere;
Reserve 8 + Borrow 2 on 8 free confirms, holds 8, notice "Borrow 2 at X confirmed as decided;
only 0 free at X"; Borrow 5 of 10 free holds 5; Borrow 10 with no stock passes short; reason
still required; a plain borrow at the site pool still refused. Vitest pin in
`boardAmend.test.ts` ("Own 98 BRW-BB · Borrow 2 BRW-BB").
