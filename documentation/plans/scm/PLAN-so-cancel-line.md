# PLAN: Fulfilment board "Transfer to Cancel SO" on a sales order line

Status: draft, behaviour card filed 7 Oct 2026 (crew lane SO-CANCEL-LINE), awaiting owner answers Q1-Q5. Track: feature lane, size M, LEAD pattern (tester -> coder -> reviewer + security-reviewer), additive migration.
Domain: scm (fulfilment planning board, order inquiries, AutoCount ingest)
UAC: `so-cancel-line-acceptance-criteria.md`
Lane: `crew/so-cancel-line`, base `origin/main` e4158ab52.
Mockup: `documentation/mockups/so-cancel-line/index.html` (after the card is answered)
Behaviour card: crew `state/files/SO-CANCEL-LINE/behaviour-card.md` (facts and the five questions; summarised below).

## Owner's words (7 Oct 2026, verbatim)

"you see this order ah, these 3 items, it cannot be confirmed cause of this I believe, these items the quantity
delivered is all delivered already from the SO perspective but actually it is not delivered, it is transferred to
Cancel SO in autocount, so here I think if i don't put anything here, it don't let me flow through, but now we don't
have the cancel SO document from autocount so i am thinking can we have like a cancel button here that can let this
flow through and in turn it will affect the order inquiry?"

Clarification: "this one is not considered cancelled, it is considered transfer to a document called cancel SO".

## Measured (prod snapshot 6 Oct 2026, read-only; code on origin/main e4158ab52)

- SO373923 core lines 384 CB110, 400 CB110-R, 416 CB110LP: `line_status = closed`, ordered 36, delivered 36.
  No DO line names them (`public.order_lines.from_doc_no = 'SO373923'` lists 16 PS lines, none CB110*).
- The board plans `plan_qty_of(core) = coalesce(qty_required, qty_ordered)` = 36, ignoring delivered on purpose
  (`app/services/project_supply_service.py:500-515`, used at `:8647`; 14 Sep 2026 ruling in
  `PLAN-fulfilment-board-plans-delivered-lines.md`). Only `line_status = 'cancelled'` returns 0 (`:512`).
- Confirm: `_check_line` refuses `total != fact.open_qty` with "The components add up to 0 and the line is open
  for 36." (`:5917-5924`); `confirm` raises 422 "3 lines cannot be confirmed. Nothing was written." (`:5198-5206`).
- One-bad-line rule (#1362) is hold-back `_write_holding_back` (`:10688-10731`); it re-raises only when every named
  line failed (`:10717-10725`), which is this press. Rule holds; not changed here.
- OI-2609-0724 rows for the three items: verb ORDER qty 36, `raised`, `acknowledged`, zero links, in no revision.
  OI outstanding already reads `qty_ordered - qty_delivered` = 0 (`project_order_inquiry_service.py:2297/2373`).
- Reject = staged draft only (`project_line_draft_service.py`), dropped from the press body on an uncovered line
  (`fulfilmentBoard.ts:479`); writes nothing to the line or the OI row.
- AutoCount: no Cancel SO pull or push (`autocount_pull_service.py:894-897` DO/GRN only). Push line status is
  header-driven (`document_ingest_service.py:1812-1834`) and rewritten for every present line on every push
  (`:1319`, setattr loop `:1534-1535`). A line AutoCount REMOVES is auto-cancelled when referenced (`:1571-1584`);
  SO373923 already has 5 such lines.
- Existing cancel plumbing: `flag_rows_for_cancelled_lines` (`project_order_inquiry_service.py:517-565`), press skips
  a plan-qty-0 line as fulfilled (`project_supply_service.py:5169-5171`), FE retires `contribution.cancelled`
  (`fulfilmentBoard.ts:334-340`), worklist greys/tags a cancelled line and drops it from Buy (rulings C1-C4,
  `PLAN-oi-cancelled-line-used-confirm.md`).
- Same-shape lines in the snapshot: 188 across 64 SOs.

## Design (assumes the card's recommendations; revise on the owner's answers)

One action, one write path, reusing the existing cancelled state.

S1. **Backend: a registered record action, no new endpoint.** `register(FormAction(key="sales_order_line.cancel",
    entity_types=("sales_order_line",), execute=..., window=WINDOW_REVERSIBLE, permission="projects.projects.edit",
    label="Transfer to Cancel SO"))` in `app/services/record_actions.py` (precedent `order.set_status` :280-288);
    the generic pending-action routes park it, count the server window down and commit it even if the tab closes
    (`app/api/v1/system/pending_actions.py`). The execute function (new, in `project_supply_service` or a sibling)
    refuses 409 when the line is covered by an active decision ("Reject first", Q2a), 409 when already cancelled,
    422 blank reason, and writes in one transaction: core `line_status = 'cancelled'`, `cancelled_at = now()`,
    `cancelled_by_id = actor`, `cancel_reason = reason`; deletes the line's draft;
    `flag_rows_for_cancelled_lines(db, [core_id])`. Required payload: `{entity_id: core line id, reason}`.
S2. **Migration** (additive, one head): `sales_order_lines.cancelled_at timestamptz null`, `cancelled_by_id uuid null
    FK users`, `cancel_reason text null`. Model + the manual dict builders that serialise the line to the board
    (assert in a test; `response_model` drops undeclared fields).
S3. **Ingest guard**: `document_ingest_service` keeps `line_status = 'cancelled'` for a present line whose
    `cancelled_at` is set (the push may still update quantities/prices). A header-cancelled push is unchanged.
S4. **Board read** exposes `cancelled_at`, `cancelled_by` (name), `cancel_reason` on the contribution so the grey
    row can say who and why (no UUIDs in the UI).
S5. **Frontend**: "Transfer to Cancel SO" button beside Reject in `BoardLineDecisionPanel` and the verdict popover,
    reason box prefilled from the saved amend reason when present, served through `DeferredActionButton` +
    `useDeferredAction` against the pending-action service (5 s reversible window, Q5b); on commit invalidate board
    + OI queries + toast. Disabled with tooltip on a covered line.
S6. **Order inquiry worklist**: nothing new; the rows follow C1-C4 (greyed, tagged, To confirm, out of Buy).

Out of scope: partial cancel (Q3a), un-cancel after the window (Q5), pulling the Cancel SO document, changing the
14 Sep plan-qty ruling.

## Test list (red first, tester writes from the UAC)

pytest: cancel endpoint happy path on a closed 36/36 line (status, audit columns, draft deleted, OI rows flipped to
`changed`); 403 without edit; 409 covered line; 409 already cancelled; 422 blank reason; ingest re-push keeps
`cancelled` + `cancelled_at`; header-cancelled push unchanged; board contribution carries the three fields; confirm
of the order after cancel confirms the other lines and skips the cancelled one.
vitest: panel renders the button (enabled uncovered, disabled covered with tooltip); click -> countdown -> mutation
body; Cancel within window posts nothing; cancelled row renders grey with reason and no Confirm tick.
