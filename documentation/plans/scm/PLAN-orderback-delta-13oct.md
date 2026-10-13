# PLAN: a Confirm sends only what it decided (ORDERBACK-DELTA)

Status: Build (small fix track: backend only, no migration, no auth/RBAC change). PR #1631.

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

## Not changed

Undo replays the generic journal (`project_supply_undo_service.py`), which records in-place
updates, so a kept or restated row is restored like any settle-in-place row.
