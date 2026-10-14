# UAC: AutoCount Cancel SO effects stop at the SO (CANCEL-STOP-AT-SO)

Plan: `PLAN-autocount-cancel-crm-13oct.md` (R5 as revised 14 Oct). [BE] backend, [FE] frontend,
[T] automated test, [OWNER] owner-run data step.

Owner ruling 14 Oct ~19:55: "for the cancel order we shouldn't auto retire the OI rows; our effect
should stop at the SO, that's all; the future flow is CS handles it in Fulfilment Planning."

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
  (state not `cancelled`) is on the board, read-only at zero, pill **Cancelled**, opening to
  "Transferred to Cancel SO by AutoCount, <when the CRM received the latest XS> · <XS nos> ·
  <n> open order inquiry row(s)". `cancel_open_oi_rows` = n. Confirm never acts on it.
- **UAC-08 [BE][T]** Such a line with no live order inquiry row is not on the board (nothing for CS).
- **UAC-09 [BE][T]** A line CS transferred on the board reads exactly as before (its own who / when /
  why, pill Confirmed, `cancel_open_oi_rows` 0).

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
