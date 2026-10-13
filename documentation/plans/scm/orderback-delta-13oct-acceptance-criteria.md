# UAC: a Confirm sends only what it decided (ORDERBACK-DELTA)

Plan: `PLAN-orderback-delta-13oct.md`.

- AC-1 (owner case SO424764): rev 2 confirmed order-back CB2519SS-DIY 5. Rev 3 saves two new
  order-backs and leaves the DIY one unchanged. The email lists exactly the 2 new ones; the DIY
  row is the same row, still raised, acknowledgement kept, filed under rev 3; no cancelled copy.
- AC-2: an order-back whose qty moves 5 -> 8 on the same line and location is the same row,
  qty 8, `previous_qty` 5, CHANGED if purchasing had acknowledged it, one email line QTY 5 /
  NEW QTY 8.
- AC-3: an order-back no longer restated is cancelled and emailed CANCEL ALL; the others stay.
- AC-4: an order-back moved to another location cancels the old (CANCEL ALL) and raises the new
  (ORDER BACK).
- AC-5: an order-back purchasing refused, restated, is raised as a fresh row.
- AC-6: confirming line B of an order carries line A; line A's raised ORDER row is the same row,
  same qty and ack, filed under the new revision, nothing emailed about it.
- AC-7: a carried line's raised CANCEL BALANCE is the same row and is not emailed again.
- AC-8 (control): a carried line whose raised row no longer matches (purchasing actioned part
  of it elsewhere) is still restated and the email shows the move.
- AC-9: a Confirm email does not repeat an earlier amendment's still-raised rows.
