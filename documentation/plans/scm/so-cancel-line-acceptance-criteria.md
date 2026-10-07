# UAC: Fulfilment board "Transfer to Cancel SO" on a sales order line

Plan: `PLAN-so-cancel-line.md`. Status: accepted 7 Oct 2026 (owner go on mock v3, card v2 recommendations).
Fixture shape: a project SO with one core line ordered 36 / delivered 36 / `closed`, a raised ORDER inquiry row of 36
on it with no links, and at least one other open line with a saved decision. Fake ids and names only.

## Journey

CS opens the fulfilment board for the order, ticks the three lines, picks Decide > Transfer to Cancel SO, types the
reason, saves; the rows read Saved / Cancel SO with Undo; Preview lists them; Confirm writes the cancels and confirms
the other decided lines in one press; the rows turn grey Cancelled with who/when/reason; purchasing finds the inquiry
rows in To confirm, greyed and tagged.

## Backend

- AC-C1 `POST .../confirm-all` with `cancelled_lines: [{project_line_id, reason}]` for a closed 36/36 line, by a user
  holding `projects.projects.edit`, sets the core line `line_status = cancelled`, `cancelled_at` (now),
  `cancelled_by_id` (actor), `cancel_reason` (the text), and echoes `lines_cancelled: [{line_no, item_code}]`.
- AC-C2 Without `projects.projects.edit` the call is 403 and nothing changes.
- AC-C3 A blank or whitespace reason is 422 and nothing changes.
- AC-C4 A line covered by an active supply decision is 409 with a message that says to reject it first; nothing changes.
- AC-C5 A line already `cancelled` is 409; `cancelled_at` and `cancel_reason` are not rewritten.
- AC-C6 After AC-C1 the line's saved draft is gone.
- AC-C7 After AC-C1 every live inquiry row on the line that was `acknowledged` is `changed` with `changed_at` set;
  rows already `awaiting`/`changed`/`cancelled` are untouched; rows of other lines are untouched.
- AC-C8 Board read after AC-C1: the contribution is `cancelled: true` and carries `cancelled_at`, `cancelled_by`
  (display name, no UUID) and `cancel_reason`.
- AC-C9 A press carrying `cancelled_lines` for line A and a `lines` entry for line B writes the revision for B and
  cancels A in the same call; a later press skips A and lists no failing line.
- AC-C10 An AutoCount re-push of the same document with the line present (ordered 36, delivered 36, header open)
  leaves `line_status = cancelled`, `cancelled_at` and `cancel_reason` as they were, while quantities/prices follow
  the push.
- AC-C11 A push whose header status is cancelled still cancels every line (existing behaviour unchanged).
- AC-C12 `cancelled_lines` beside a `batch_id` is 422 (same rule as `rejected_line_ids`).
- AC-C13 A migration adds the three nullable columns; `alembic heads` shows one head.

## Frontend

- AC-F1 The Decide dropdown lists "Transfer to Cancel SO" as its last item; the other six are unchanged.
- AC-F2 Choosing it opens the Decide dialog titled "Decide N lines: Transfer to Cancel SO" with a required Reason and
  no picker; Save is disabled while the reason is blank.
- AC-F3 Save posts, per ticked uncovered row, a draft with `verdict: 'cancelled'` and the reason; covered rows and
  already-cancelled rows are skipped and named in the toast ("N saved as Transfer to Cancel SO · M skipped: ...").
- AC-F4 A saved cancel row shows the Saved pill, "Cancel SO" in the Decided column, and the row Undo removes it.
- AC-F5 Confirm sends those rows in `cancelled_lines`, not `lines`; Preview groups them under "Cancel SO".
- AC-F6 After Confirm the row's Verdict pill reads Confirmed (no new verdict label), the row is greyed with Planned qty 0
  and a disabled tick, the Decided column reads "Cancel SO", and its expanded sentence reads "Transferred to Cancel SO by <name>, <time> · <reason>"; the results block lists the
  cancelled lines beside the confirmed ones.
- AC-F7 Errors surface through `extractApiError` in a non-sticky toast.
- AC-F8 Usable and non-clipped at 1280 and 375 px (browser pass table).
- AC-F9 Undo on a saved cancel decision removes it; the row reads Suggested again.
- AC-F10 A line covered by an ACTIVE decision with a cancel draft yields no `cancelled_lines` entry and no `lines` entry.
- AC-F11 On a batch-blocked order a saved cancel is not counted, not posted, and is named under held back by batch.
- AC-F12 The expanded panel of a saved cancel reads "Transfer to Cancel SO" with the reason, no short hint.
- AC-F13 to F15 Held-back-by-batch note per line; the order sentence names Cancel SO; an unplannable line is not listed.
- AC-F16 A committed Cancel SO line (cancelled_at set) is skipped by the press and not counted in Confirm (N).
- AC-F17 Cancel SO skips only an already cancelled line and a line covered by an ACTIVE decision (`decision` non-null,
  "confirmed, reject it first"); a line that is `covered` only because a live inquiry row names it (`inquiry_decided`,
  `decision` null) is cancellable, and its inquiry rows then follow AC-O1 (owner's SO373923 case, 7 Oct).
- AC-F18 The Decide dialog states skipped rows before Save: "N lines will be skipped: <reason>" (every Decide way); the
  button reads "Save M lines" for the rows that will save, disabled at 0; no sentence when nothing is skipped.
- AC-F19 An inquiry-decided line carrying a saved cancel draft is posted in `cancelled_lines`, never in `lines`.

## Order inquiry worklist (existing C1-C4, verified not regressed)

- AC-O1 The three rows appear under To confirm, greyed, tagged as a cancelled line, qty kept, out of Buy, links kept.
