# PLAN: a Confirm sends only what it decided (ORDERBACK-DELTA)

Status: Hand test (standard track since the 13 Oct 22:40 extension: template migrations `oihr_0007_excel_remark` + `oihr_0008_so_table_on_top`; no auth/RBAC change). PR #1631. Owner fix round 14 Oct: reviewer READY @ 8bb165772, hand test next.

UAC: `orderback-delta-13oct-acceptance-criteria.md` (alongside).

## Journey

Owner hand test of #1629 (13 Oct 2026), SO424764 rev 3: the board showed Saved (2), but the
ORDER BACK email listed 3. The third (CB2519SS-DIY 5) had been confirmed in Revision 2 and was
unchanged. Owner: "why every time a confirm is clicked, the confirmed decision will go out
[again]?" The scope was then widened: "not just order backs, any action, saved = sent in order
inquiry email".

## Rule

What a Confirm sends (OI email lines, OI rows raised to purchasing) is exactly what that
Confirm decided. Anything already confirmed and unchanged is not cancelled, not raised again
and not sent again.

## Behaviour card (crew-ask on PR #1631, answered 13 Oct)

| # | Action | Before | After |
|---|---|---|---|
| 1 | Borrow order-back (`_raise_borrow_shortfalls`) | Every open hole cancelled, re-raised, re-emailed | Matched by (line, item, donor location). Unchanged: same row, re-filed under the new revision, silent. Qty/date moved: same row, `previous_qty`/`previous_delivery_date` set, one email line QTY old / NEW QTY new (Q1 = a). Gone: cancelled + CANCEL ALL. New: raised + ORDER BACK. Location moved: old CANCEL ALL + new ORDER BACK. Refused row: raised fresh (AC-H6). |
| 2 | Carried line, raised ORDER / ORDER BACK row | Cancelled and re-raised (silent, but OI noise) | Kept, re-filed, when it already matches the outstanding Buy (`_carried_rows_stand`) |
| 3 | Carried line, raised CANCEL BALANCE | Cancelled, re-raised, RE-EMAILED | Kept, re-filed, not emailed, while the figure holds |
| 4/5 | Saved Buy line | Settle in place (silent when unchanged) | Unchanged |
| 6 | Still-raised amendment rows riding along (AC-R2-16/17) | Appended to every Confirm email with a line of its own | Dropped (Q2 = a, overturns the 18 Sep ruling) |
| 7 | STOCK SHORT | Saved lines only | Unchanged |
| 8 | Step-3 document borrows | Saved lines only, no email | Unchanged |
| 10 | Donor revision churn (borrow-from-order switch ON) | Donor re-issued per saved borrow | Follow-up, out of scope |

Q3 (a re-approved, unchanged line counts in Saved (N) but sends nothing): with the owner;
crew's proposal is to keep such lines out of Saved (N). Nothing in this lane depends on it.

## Code

- `app/services/project_order_inquiry_service.py`
  - `_raise_borrow_shortfalls`: still-raised holes collected in `open_holes`, matched per entry,
    restated by `_restate_borrow_hole`; the unmatched ones are cancelled after the raise loop.
    New rows are flushed before `_record_handover` so the queue carries their id.
  - `_carried_rows_stand`: the carried-line read-only netting check, called from
    `refresh_for_decision`'s `if carried:` block.
  - `_append_still_raised_amendment_rows` and `_amendment_row_was` removed with their call.
- Tests: `tests/test_oi_reconfirm_delta.py` (new); ride-along tests in
  `tests/test_order_inquiry_handover_automation.py` and `tests/test_oi_email_qty_moves.py`
  inverted or removed.

## Undo

Journalled undo (every board Confirm) replays the generic journal
(`project_supply_undo_service.py`), which records in-place updates, so a kept or restated row
is restored like any settle-in-place row.

Known limit (review S1, follow-up, not fixed here): reconstructed undo
(`project_supply_undo_reconstruct_service.py` step b, run for a journal-less revision such as a
planning-change apply or `uncover_lines`) "restores" any row filed under the undone revision
that carries `previous_qty` / `previous_delivery_date`. A row this lane only re-files keeps the
`previous_*` of an OLDER settle, so reconstruct would roll it back to that older figure. The
same gap already existed for a silent settle-in-place and for the carried placed-row restamp
(B1, `refresh_for_decision`). The fix needs a marker telling "re-filed by D" from "settled by
D" (or step b reading `changed_at` only); trigger: the first reconstruct undo of a planning
change on an order with a previously settled row.

## Review round 1 (reviewer, Opus)

- B1 fixed: `tests/test_oi_reconfirm_delta.py` `_raise` mints a real revision per pass, so the
  re-filing is guarded (kill test K4 now red).
- S2 fixed: holes read oldest first, and an entry takes the hole already holding its quantity
  before the oldest one (`test_two_holes_on_one_key_each_keep_their_own_row`, kill-tested).
- Nits: unchanged hole's note follows the entry silently; date-only restate test added; stale
  docstrings and test prose updated.

## Owner hand test SO323450 (13 Oct): carried linked hole

A borrow hole the raise-time cascade had partly linked to an SPO was cut and its remainder
re-raised and emailed on the next Confirm that saved only another line. A partly linked or
placed borrow hole restated exactly (line, item, location, qty, date) is now only re-filed
(`_raise_borrow_shortfalls` pre-pass). Owner direction: keep it simple, no SPO matching.

## Extension (owner, 13 Oct 22:40): one truthful line per SAVED line

The purchasing email of a Confirm has exactly one line per saved line whose decision is new or
changed (`_saved_decision_key`), in the owner's Excel ORDER INQUIRY columns (SO DATE | S/O NO |
ITEM CODE | QTY | DELIVERY DATE | PROJECT/CUSTOMER | STOCK LOCATION | REMARK), template
`oihr_0007_excel_remark`. STOCK LOCATION is always the SO line's own location. REMARK, in the
board's own vocabulary (`supplyVocabulary.ts`), owner answers 13 Oct:

| Board word | REMARK |
|---|---|
| Buy | ORDER (local origin: LOCAL BUY; Order back Buy: ORDER BACK [cited doc]) |
| Use own location | USE <own location code> |
| Use BRW | USE BRW (the pool's own code) |
| Borrow (any) | ORDER BACK |
| Borrow incoming / Use incoming | the document / SPO number(s) |
| Several parts | each with its qty, joined by " & " |
| Line qty cut | CANCEL n (QTY / QTY CHANGE TO columns) |
| Cancel SO | CANCEL |
| Short of free stock | ", n SHORT" on the same line (no separate STOCK SHORT line) |

Code: `saved_line_remark` / `_saved_line_parts` / `_saved_decision_key` and
`record_saved_lines` / `record_cancelled_lines` in `project_order_inquiry_service.py`;
`ProjectSupplyService._tell_saved_lines` (called at the end of `confirm`) and `cancel_lines`.
A saved line's row lines (ORDER, ORDER BACK, settled, cancelled, STOCK SHORT) queued in the
same commit are replaced by its one line. Donor holes on other lines, planning-change
reactions on unsaved lines and amendment emails keep their own row lines.

## Review of the extension (reviewer, Opus)

- B1 fixed: a planning-change apply's own DELAY / ADVANCE / CANCEL BALANCE notice rows no
  longer add email lines for a line its confirm already told
  (`forget_rows_told_for_saved_lines`, called after `derive_for_book_change`).
- S1 fixed: each row line carries `adds_order` (what it asks purchasing to buy) as data;
  the fold no longer parses REMARK text.
- S2/S4 fixed: a single row line is kept only for a Buy-only decision.
- S3 fixed: a stock shortfall is part of the "unchanged" key.
- S5/S6 fixed: the separate STOCK SHORT writer is removed; the header lookup excludes
  amendment headers.

## Final review and browser pass (14 Oct)

- Reviewer READY @ a5cc8725 (S1 fixed: the told set is cleared when the root write ends);
  security-reviewer SECURE @ 78a41d34.
- Cloud browser pass on a sandbox mirror of SO304546 + one SO323450 line, evidence in
  `evidence/orderback-delta/script-pass/` (`r2-*`): every Confirm's email matched the owner's
  mapping; the hand-test script is `laneboard/scripts/1631.md`.
- ~~Open for the owner: a Reject of a Borrow line tells purchasing CANCEL ALL at the donor row's
  location.~~ Ruled 14 Oct (crew, owner's 13 Oct rule): fixed in the round below, item 6.

## Owner hand test fix round (14 Oct), red-first

1. Bulk "Decide N lines: Borrow" listed no location. The line's own location is now always in
   its Location list (`isOwnLocationBorrow`, `boardAmend.ts`) and never capped: what is not free
   there is saved short, as the per-line "Add a borrow" saves it. Other locations keep their
   skip rules.
2. Email "just like before" (`oihr_0008_so_table_on_top`): the S/O facts once in a top table,
   item rows below (LOCATION | ITEM CODE | QTY | [NEW QTY] | DELIVERY | [NEW DATE] | REMARK), one
   plain heading ORDER INQUIRY. The details live only in REMARK.
3. No shortfall wording: ", n SHORT" and the STOCK SHORT headline are gone, and a shortfall is no
   longer a change to tell.
4. Simulation (owner case SO424764 line 416, simulated on main): a row the press only re-filed
   (every fact unchanged) is no longer listed under "Would write" (`_preview_row_facts`). The
   email side was already right on this branch: the carried row is kept, not cancelled and
   re-raised, and not sent.

Continued locally by lane ORDERBACK-DELTA-L (the cloud session stopped after bfa4ef76d):

5. Review S1 (reviewer, 14 Oct): Simulate held stock TRANSFERS to the same rule as rows, so a
   transfer the press only re-filed is no longer listed (`_preview_move_facts`). The row facts
   also read the note. Dead STOCK_SHORT headline vocabulary removed.
6. Every Borrow line reads the SO line's own location (owner 13 Oct rule, re-confirmed by crew
   14 Oct): a CHANGED Borrow is one ORDER BACK line at the own location, and a REJECTED Borrow's
   CANCEL ALL line and the subject use the own location too. The ORDER BACK rows keep their donor
   location on the worklist. A move plus a cut or a new Buy split reads exactly as the same change
   without the move (review B1), and a Borrow reshuffled across donors, with holes resized in
   place, nets to one hole settled (`_hole_move`): never a false CANCEL ORDER.
7. Hand-test script re-pointed at SO422076, because the morning test used SO419944. Browser pass
   mirroring the script on SO380096: `evidence/orderback-delta/script-pass-r3/` (at d3ae516d5) and
   `script-pass-r4/` (changed and rejected Borrow, at f5100e707) and `script-pass-r5/` (a Borrow
   split across two donors, at 8bb165772: one row, ORDER BACK 3 & ORDER BACK 2, no CANCEL).

Follow-up (not this PR): an own-location ORDER BACK row that the raise-time cascade auto-placed
onto an already-received SPO within seconds (SO380096 CWCY605 at BRW-BB, SPO-2026/05-0058,
`order_inquiry_links.auto = t`) stays live after the line's Borrow moves to another location,
because placed rows are netted and kept on a re-confirm (`project_order_inquiry_service.py:1729`;
the cascade-only test is `_cascade_only`, :8248).

Follow-up (review nit, not this PR): when the NUMBER of Borrow parts changes together with the
line quantity (X 10 to X 5 + Y 3 with a cut to 8), the email line loses its QTY / NEW QTY "was"
columns, because `_decision_words` reads one ORDER BACK part and two as different decisions, so
`held_before` is false in `_queue_saved_line`. The numbers are right; the single-donor twin
shows "was 10".
