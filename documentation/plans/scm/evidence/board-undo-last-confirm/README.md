# Evidence - board undo last confirm

Lane: `documentation/plans/scm/PLAN-board-undo-last-confirm.md`. This folder holds the
agent-browser screenshots for the lane's phased verification. Stack for every file below:
frontend `http://localhost:3080`, backend `http://localhost:8080`, database
`sorento_ai_automation_0915_1900`.

## AC-UC-01 to AC-UC-09 (S0, Phase 1 mock)

`AC-UC-01-02-03-gear-menu-1280.png`, `AC-UC-02-three-orders-two-undoable-1280.png`,
`AC-UC-04-no-undo-entries-1280.png`, `AC-UC-05-countdown-1280.png`, `AC-UC-06-cancel-1280.png`,
`AC-UC-07-confirm-slot-restored-1280.png`, `AC-UC-07-gear-entry-gone-1280.png`,
`AC-UC-07-real-lines-undecided-1280.png`, `AC-UC-09-countdown-375.png`,
`AC-UC-09-gear-menu-375.png` - captured against the mocked `undo` field before S1/S2 landed.
Not part of this pass; kept for the lane's own history.

## AC-UC-08, an earlier partial S4 walk

`AC-UC-08-batch-board-before-1280.png`, `AC-UC-08-batch-confirmed-rev2-1280.png`,
`AC-UC-08-batch-countdown-1280.png`, `AC-UC-08-batch-gear-entry-1280.png`,
`AC-UC-08-batch-pending-again-1280.png`, `AC-UC-40-countdown-1280.png`,
`AC-UC-40-real-gear-entry-1280.png` - an earlier pass against the real S1-S3 implementation
(batch-fork undo and a first AC-UC-40 walk). Kept alongside this pass's own, fuller AC-UC-40
walk below rather than replaced.

## AC-UC-40 - confirm, undo, verify (this pass)

Order: SO314593 (`sorento_ai_automation_0915_1900`). Line 10 (SRTWC8605-SC-RL) was the only
undecided line confirmed for this walk - the order already carried an active revision 1
covering 8 other lines from an earlier deploy/verification, left untouched throughout.

1. `AC-UC-40-1-board-before-confirm.png` - the board with line 10 selected, decision saved,
   `Confirm (1)` enabled.
2. `AC-UC-40-2-confirmed-revision-2.png` - post-confirm toast "SO314593: confirmed as
   revision 2 (1 purchase row handed over)".
3. `AC-UC-40-3-gear-menu-undo-entry.png` - Board actions menu, "Undo SO314593 confirm
   (rev 2)" enabled.
4. `AC-UC-40-4-undo-countdown.png` - the Confirm slot replaced by "Undoing in 3s" with
   Cancel, for SO314593.
5. `AC-UC-40-5-line-undecided-after-undo.png` - after the countdown lapsed and the board
   reloaded, line 10 (SRTWC8605-SC-RL) reads "Not decided" again.

**SQL diff (the nine write-set tables, keyed by id, `updated_at` and `undo_journal`
dropped):** taken before the confirm and again after the undo committed - **clean, zero
differences** (`diff before.txt after.txt` exit code 0), once one artifact of driving the
UI was removed: clicking "Save decision" for line 10 (the only way `Confirm` becomes
enabled on this board) writes a row to `projects.so_supply_decision_drafts` that did not
exist before the walk started. The confirm clears it and the undo's replay correctly
restores it (matching AC-UC-17's own contract - drafts are recovered, not lost), so its
presence right after undo is the FEATURE working, not a defect; it was removed by hand
afterwards (`DELETE FROM projects.so_supply_decision_drafts WHERE id = ...`) so the final
state matches the database exactly as it stood before this evidence run, not merely as it
stood before the confirm.

**Server-side checks:**
- `audit_logs`: one `DELETE` row, `entity_type = 'project_so_supply_decisions'`, naming the
  undone revision 2's id, timestamped at the undo.
- `automation_runs` joined to `automations`: one row, `trigger_type =
  'order_inquiry_undone'`, `name = 'Order inquiry undone'`, `status = 'success'`,
  `recipients_attempted = 7`, timestamped at the undo - confirms S3's dispatch fired for
  real against the seeded migration automation (`enabled = true`, template
  `order_inquiry_undone_default`, `recipient_config` carrying purchasing `role_ids`,
  `include_actor: true`, `one_email: true`).

## AC-UC-41 - refused undo

Same order, reconfirmed for a second time (revision 2 again, same line 10).

1. `AC-UC-41-1-reconfirmed-revision-2.png` - the reconfirm toast.
2. A manual link was inserted directly by SQL (faster than staffing a second purchasing
   session, and equivalent to what a purchasing click does):
   `INSERT INTO projects.order_inquiry_links (..., auto, linked_at) VALUES (..., false,
   now())` on one of the freshly-raised rows, `linked_at` after the decision's
   `confirmed_at`.
3. `AC-UC-41-2-disabled-gear-entry-tooltip.png` - "Undo SO314593 confirm (rev 2)" now
   disabled in the Board actions menu; its `title` attribute (read via `document
   querySelector`, since headless CDP does not render a live hover tooltip) reads exactly
   "Purchasing linked a PO line".
4. `POST /api/v1/pending-actions` for `action_key: project_sales_order.undo_confirm`,
   `entity_type: project_sales_order`, `entity_id` = the order, with a bearer token from
   the browser's own NextAuth session (`GET /api/auth/token`, the opaque FastAPI session
   token the frontend already sends as `Authorization: Bearer`) - **answered `202`, not
   `409`.** See "What looked wrong" below; the deferred commit did correctly refuse it
   (`sla_form_actions.status = 'ineligible'`, `error_text = "Purchasing has linked a PO
   line to this order since it was confirmed."`, `code: 'manual_link'` in the backend log),
   and nothing in `so_supply_decisions` changed - revision 2 stayed active throughout.
5. The manual link was removed (`DELETE FROM projects.order_inquiry_links WHERE document =
   'MANUAL-TEST-LINK'`) and the order undone for real through the UI (gear menu, same
   countdown, left to lapse).
6. `AC-UC-41-3-final-clean-state.png` - the board back to `0 to confirm`, revision 1 active,
   line 10 undecided.

**Final SQL diff:** clean (same nine tables, same before/after comparison as AC-UC-40,
after removing the one leftover draft row the second "Save decision" click created).

## What looked wrong on screen

`POST /api/v1/pending-actions` parked the refused undo with `202` instead of answering
`409` synchronously, contrary to AC-UC-41's literal wording. The refusal predicate itself
is correct - the board reads it right (disabled entry, right tooltip) and the DEFERRED
commit refuses it correctly (`ineligible`, right reason, nothing changed) - but the CREATE
route does not pre-check `board_undo_map`'s refusal before parking the countdown, so a
caller that bypasses the disabled UI (a raw API call, exactly as this check did) gets a
counting-down action that fails a few seconds later rather than an immediate 409. Worth a
follow-up ticket if a synchronous 409 at creation time is wanted; not something this
verification pass changes, since browser verification does not edit implementation.

## Re-verification after the review fix round (`bca512634`) - AC-UC-03 and AC-UC-41 only

The disabled gear entry's reason moved from a `title` attribute (never rendered on a
`data-disabled` item, see the tooltip screenshot above) to plain visible text inside the item,
and the refusal code changed from `manual_link` to `linked`. Re-ran only the two ACs this
touched, same order (SO314593), fresh chain: saved and confirmed the one remaining undecided
line (Line 10, SRTWC8605-SC-RL) to get a new active revision (2), inserted a manual
`order_inquiry_links` row by hand on the OI row that revision raised, then reversed both.

1. `AC-UC-03-refused-reason-visible-1280.png`, `AC-UC-03-refused-reason-visible-375.png` - Board
   actions menu, "Undo SO314593 confirm (rev 2)" disabled, with "Purchasing linked a PO line"
   rendered as its own line of muted text under the entry label (confirmed via the
   accessibility snapshot: the string is inside the menuitem's accessible name, not a hover-only
   `title`). Legible and non-clipped at both widths.
2. `AC-UC-41-refused-409.txt` - the same `POST /api/v1/pending-actions` re-check: still answers
   `202` (parks), not `409`, for the same by-design reason as the original AC-UC-41 pass above
   (the "linked" refusal is a commit-time check, not a create-time one). The pending action
   resolved 5s later as `status = ineligible`, `error_text = "Purchasing has linked a PO line to
   this order since it was confirmed."`, and revision 2 was never touched by the refused
   attempt - matching the original pass's finding exactly, now with the reason string updated to
   match the new visible-text copy. Also notes a `last_outcome` ordering quirk found while
   re-checking (`committed_at desc nullslast` ranks an older `committed` row ahead of a newer
   `ineligible` one, since `ineligible` never sets `committed_at`) - not exercised by the gear's
   own disabled state (that reads fresh from the board endpoint each load), logged as a possible
   follow-up.
3. `AC-UC-41-undo-after-unlink-1280.png` - link deleted, gear entry re-enabled, undo run for
   real through the UI (countdown let lapse, per standing rule), board back to revision 1
   active, line 10 reading "Not decided" again (after also removing the leftover
   `so_supply_decision_drafts` row this walk's own "Save decision" click created - same
   AC-UC-17 recovery-not-loss behaviour the original AC-UC-40 pass documented above, cleaned up
   by hand so the final state matches what the run started from).

Cleanup: `order_inquiry_links` row and the `so_supply_decision_drafts` row this walk created
were both deleted by hand; no pending countdown was left running; 2 `email_outbox` rows this
walk generated (`OI: BRW-IR @ SO314593`, `OI undone: SO314593 rev 2`) were cancelled
(`status='cancelled', cancel_reason='lane evidence run'`). Final DB check: `so_supply_decisions`
for SO314593 shows only revision 1, active - matches the state before this walk started.

One pre-existing, unrelated console warning throughout ("Each child in a list should have a
unique key prop... Demo1Layout") - the Metronic shell, not this feature; not a regression.
