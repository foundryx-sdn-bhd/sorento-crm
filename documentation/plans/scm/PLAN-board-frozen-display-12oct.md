# PLAN: fulfilment board shows the frozen decision, and Confirm never drops a named line

Status: reviewer READY, awaiting CI and owner merge (PR #1592). Track: small fix (hotfix, prod bug). Lane BOARD-FROZEN-DISPLAY.

## Journey (owner, 12 Oct 2026, SO422492 on prod)

SO422492 rev 4 is active (confirmed 06:42 UTC). C-FH14 lines are confirmed BUY in rev 3 and rev 4,
and the purchasing handover listed them as ORDER. The board showed them as Suggested Own N
(BRW-NTC) AND Decided Own N (BRW-NTC). Four saved lines (688 SRTWT2201 Buy 24, 944 and 1024
SRTFVP08, 1440 SRTUB6503-NEW) refused Confirm with "Nothing on this planning change could be
confirmed.", and the rev 4 order-inquiry email told purchasing "SRTWT2201 24 -> 0, CANCEL ALL".

## Measured cause

One cause, three symptoms: a batch stays open (`applied_at` NULL) while any row is pending
(S5b, #1245), so after a partial Confirm the board keeps opening on a batch whose other rows are
already history.

1. Display: `boardChangeAnnotations.ts` `isRetiredChangeRow` retired `superseded` rows only (the
   R3 ruling kept `applied` rows overlaying on purpose), so an applied row kept uncovering its
   line and pre-marking it with the batch's old `proposal_json` over the frozen decision.
2. Confirm: `_confirm_a_planning_change` routed every posted line that had ANY batch row through
   `set_row_decision`. On an applied row held on an older revision it raised 409 "The board
   confirmed a newer revision"; on one that was not, the decision was stored and then ignored
   (`_apply_one_order` takes pending rows only), so "Nothing on this planning change could be
   confirmed", or, when another line did confirm, the revision was written WITHOUT the line.
3. CANCEL ALL: a line left out of the payload is carried only if its snapshot still matches the
   book (`_carry_snapshot_has_drifted`); a moved line drops out of the revision, and the
   order-inquiry diff reads Buy 0 and cancels its ORDER row.

Header item (c) dropped by the owner: the later screenshot reads Revision 4 correctly.

## Fix

- (a) FE: `proposalsByLine` skips an `applied` row (`isAppliedChangeRow`), so it never uncovers
  or pre-marks its line again (reverses R3/T8); its change icon still draws, so a deep link to an
  applied batch says what it applied. A failed row still overlays (its apply rolled back).
- Confirm: `by_line` is built only from rows `row_is_live` (pending and not overtaken by a newer
  revision); every other posted line rides `extra` as an ordinary confirm.
- Never silent: `_refuse_dropped_named_lines` fails the order, naming the line, when a line the
  press named through a live row is not in the revision the apply composes. A line the book
  cancelled is exempt: it is superseded with its reason and the rest applies (OI-CLOSED-LINE AC-5).
- (b) The batch closes once only overtaken pending rows remain (they can never be applied). A
  genuinely pending row still keeps it open. No row is marked applied that was not confirmed.

## Tests

`sorento_crm_backend/tests/scm/test_board_confirm_partly_applied_batch.py` (7), and
`boardChangeAnnotations.test.ts` (T8 reversed + SO422492 shape),
`FulfilmentBoardPanel.change.test.tsx` (the R3-pinned test amended). Re-confirming a line after a
CANCEL ALL raises a fresh ORDER (`test_reconfirming_a_line_purchasing_was_told_to_cancel...`).

## Open (not in this lane)

- A multi-order batch closed while a board for ANOTHER order of it is still open refuses that
  order 409 "already applied" (main fails there too, with "newer revision"); a fresh board read
  carries no `pending_change_batch_id`. Fix when it is seen: an ordinary confirm for an order with
  no live rows.
- Undoing the confirm that overtook a row does not reopen a batch this lane closed past it.
