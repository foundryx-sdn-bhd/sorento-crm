# UAC: AutoCount Cancel SO effects stop at the SO (CANCEL-STOP-AT-SO)

Plan: `PLAN-autocount-cancel-crm-13oct.md` (R5 as revised 14 Oct). [BE] backend, [FE] frontend,
[T] automated test, [OWNER] owner-run data step.

Owner ruling 14 Oct ~19:55: "for the cancel order we shouldn't auto retire the OI rows; our effect
should stop at the SO, that's all; the future flow is CS handles it in Fulfilment Planning."
Owner rulings 14 Oct 23:16 / 23:20: "why the CS cannot cancel it in fulfilment planning and flow that
to order inquiry to cancel the OI line just like how we did for Transfer to Cancel SO action in
fulfilment planning?" and "it is essentially when SO is cancelled, we trigger the transfer to cancel
SO action in fulfilment planning and let the CS confirm themselves".

## The cancel stops at the SO

- **UAC-01 [BE][T]** A Cancel SO that fully cancels an SO line still writes `qty_cancelled`, the line's
  `cancelled` status and `cancelled_by_cancel_doc` exactly as before. Partial cancels are unchanged
  (planned qty lowers, the `qty_down` planning change is raised for CS).
- **UAC-02 [BE][T]** The line's order inquiry rows are untouched: no row is cancelled, no note is stamped,
  no link is removed, no `OI_ROW_CANCELLED` audit row is written, and an acknowledged row (actioned
  included) is not sent back to To confirm (`ack_state` stays).
- **UAC-03 [BE][T]** No handover line is queued, no purchasing automation is dispatched and no
  `email_outbox` row is written for it.
- **UAC-04 [BE][T]** The same holds when the cancel lands through an SO / PO / SPO push's relink (a
  waiting cancel line links, or ordered drops onto the cancel).
- **UAC-05 [BE]** CS's own Transfer to Cancel SO on the board is unchanged (it still retires the rows and
  hands over to purchasing: SO-CANCEL-HANDOVER).
- **UAC-06 [BE]** Cancel PO: unchanged. It writes `qty_cancelled` / line status on PO lines and
  `qty_cancelled` on SPO allocations only; it never touched OI rows, links, decisions or emails.

## Fulfilment Planning shows it for CS

- **UAC-07 [BE][FE][T]** A line an AutoCount Cancel SO cancelled that still has a live order inquiry row
  (state not `cancelled`) is on the board at zero, opening to "Cancelled in AutoCount, <when the CRM
  received the latest XS> · <XS nos> · <n> open order inquiry row(s)". `cancel_open_oi_rows` = n.
  With its pending Transfer to Cancel SO (UAC-12) the pill reads **Saved** and Decided reads
  "Cancel SO"; dismissed (UAC-17) the pill reads **Cancelled**. Confirm acts on it only through the
  pending transfer (UAC-16).
- **UAC-08 [BE][T]** Such a line with no live order inquiry row is not on the board (nothing for CS).
- **UAC-09 [BE][T]** A line CS transferred on the board reads exactly as before (its own who / when /
  why, pill Confirmed, `cancel_open_oi_rows` 0).

## The pending Transfer to Cancel SO (rulings 23:16 / 23:20)

- **UAC-12 [BE][T]** When a Cancel SO fully cancels a line that has a live order inquiry row, the sync
  saves the board's own Transfer to Cancel SO as that line's pending decision (a board draft: verdict
  `cancelled`, reason "AutoCount Cancel SO <XS no>", marked `source: autocount_cancel`, saved by the
  integration). Nothing else moves: OI rows, acknowledgements, emails as in UAC-02/03.
- **UAC-13 [BE][T]** No live order inquiry row: no pending decision.
- **UAC-14 [BE][T]** A cancel that lowers or is voided, so the line reopens (R6), withdraws the pending
  decision the sync saved (a decision CS saved stays).
- **UAC-15 [BE][T]** The board line carries the pending decision (`draft.decision`), like any saved line.
- **UAC-16 [BE][FE][T]** Confirm posts it in `cancelled_lines` and runs the existing Transfer to Cancel
  SO (`ProjectSupplyService.cancel_lines`, which takes this one cancelled line, active decision or not):
  the OI rows are cancelled in Order Inquiry with the same handover to purchasing CS's own transfer
  sends, the line records who / when / why, the draft is gone and the board reads it as UAC-09.
  The Confirm count and dialog list it ("Cancel SO").
- **UAC-17 [BE][FE][T]** CS can dismiss it with the decision's Undo: the draft is deleted, nothing else
  moves, the line stays on the board (UAC-07, pill Cancelled).
- **UAC-18 [BE][FE][T]** On such a line the Decide strip and the line's draft route accept Transfer to
  Cancel SO (CS can re-save it, with their own reason) and refuse any supply decision (409
  `board_line_cancelled_in_autocount`; the strip skips it).

## Owner data repair (rows retired on 14 Oct before the ruling)

- **UAC-10 [OWNER]** `SQL-cancel-so-retired-oi-rows-14oct.sql` (read only) lists every row retired with
  cause `cancel_so` since 14 Oct 00:00 MYT, says which writer did it (AutoCount vs CS board) and what the
  restore does with each; it also lists acknowledged rows the hook sent back to To confirm, and the
  CANCEL ALL emails written since then.
- **UAC-11 [OWNER]** `SQL-cancel-so-restore-oi-rows-14oct.sql`, after the fix is deployed, puts every
  AutoCount-retired row (stamp names a held Cancel SO of that company, line never transferred by CS; the
  line may have reopened since) whose retirement removed no link back to `raised` with its note as it
  was, sets its inquiry header back to raised, writes one `OI_ROW_RESTORED` audit row each, verifies,
  and waits for the owner's COMMIT. Rows whose retirement
  removed a link (or that carry a PO tag) are listed for manual re-link and left alone. Re-running it
  restores nothing more.
