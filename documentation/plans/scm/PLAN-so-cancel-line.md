# PLAN: Fulfilment board "Transfer to Cancel SO" on a sales order line

Status: draft, behaviour card filed 7 Oct 2026 (crew lane SO-CANCEL-LINE), awaiting owner answers Q1-Q5. Track: feature lane, size M, LEAD pattern (tester -> coder -> reviewer + security-reviewer), additive migration.
Domain: scm (fulfilment planning board, order inquiries, AutoCount ingest)
UAC: `so-cancel-line-acceptance-criteria.md`
Lane: `crew/so-cancel-line`, base `origin/main` e4158ab52.
Mockup: `documentation/mockups/so-cancel-line/index.html` (v3, 7 Oct 2026)
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

## Design (owner direction 7 Oct: "cancel is also a decision", no new button; assumes the card's recommendations Q1a Q2a Q3a Q4a Q5a; revise on the owner's answers)

One Decide item, one new confirm channel, the existing cancelled state.

S1. **Decide item.** `DecideWay` gains `'cancel_so'` (`_shared/lib/boardAmend.ts:724-730`), `MENU_ORDER` lists it after
    `buy` with label "Transfer to Cancel SO" (`BoardDecideControl.tsx:60-74`, special-cased in `labelOf` like "As
    suggested"). `chooseWay` opens the existing dialog (no picker, Reason required `:429-433`); `runSave` writes a draft
    `{verdict: 'cancelled', reason, reserve: [], borrow: [], timely_spo_qty: '0', buy_qty: '0'}` per ticked row through
    the unchanged `decideBatch` path. Skips: already cancelled ("already cancelled"), covered by an active decision
    ("confirmed, reject it first", Q2a). `BoardVerdict` gains `'cancelled'` (`fulfilmentPlanning.types.ts:1987`);
    the Decided column / summary word is "Cancel SO"; the row keeps the Saved pill and its Undo. Owner (mock v2 note, 7 Oct): NO new verdict label; after Confirm the row wears the existing Confirmed pill (a CRM-cancelled line with `cancelled_at` set bypasses the `cancelled` pill precedence at `BoardDecisionPill.tsx:145-148`), the cancel reads in Decided, the greyed row with Planned qty 0, and the expanded sentence.
S2. **Press routing.** `pressRoleFor`/`confirmLinesFor` (`fulfilmentBoard.ts:327-390`) send a `cancelled`-verdict draft
    to a new `cancelled_lines: [{project_line_id, reason}]` channel on the confirm body, never into `lines`, so
    `_check_line` never sees it. Preview groups them under "Cancel SO".
S3. **Backend.** `ConfirmSupplyBody` (`schemas/project_supply.py:417-450`) gains `cancelled_lines`, refused beside a
    `batch_id` like `rejected_line_ids`. `confirm` handles them before the line recheck: per line, refuse 409 if the
    line is covered by an active decision or already cancelled (the FE skips these, the server still guards), then in
    the same transaction write core `line_status = 'cancelled'`, `cancelled_at = now()`, `cancelled_by_id = actor`,
    `cancel_reason`, delete the draft, `flag_rows_for_cancelled_lines(db, [core_id])`. Echo them in the result as
    `lines_cancelled: [{line_no, item_code}]`. A press with only cancelled lines and nothing else is valid.
S4. **Migration** (additive, one head): `sales_order_lines.cancelled_at timestamptz null`, `cancelled_by_id uuid null`
    FK users, `cancel_reason text null`. Model + every manual dict builder that serialises the line to the board
    (assert in a test; `response_model` drops undeclared fields).
S5. **Ingest guard**: `document_ingest_service` keeps `line_status = 'cancelled'` for a present line whose
    `cancelled_at` is set (the push may still update quantities/prices). A header-cancelled push is unchanged.
S6. **Board read** exposes `cancelled_at`, `cancelled_by` (name), `cancel_reason` on the contribution; the list view's
    cancelled-row sentence (`FulfilmentBoardListView.tsx:545-549`) reads "Transferred to Cancel SO by <name>, <time> ·
    <reason>" when they are set, the existing sentence otherwise. Results block lists "Transferred to Cancel SO: ..."
    from the echo.
S7. **Order inquiry worklist**: nothing new; the rows follow C1-C4 (greyed, tagged, To confirm, out of Buy).

Out of scope: partial cancel (Q3a), un-cancel after Confirm (Q5a), pulling the Cancel SO document, changing the 14 Sep
plan-qty ruling, the Use BRW stale-panel defect (`BoardLineDecisionPanel.tsx:145-147`, separate S lane).

## Test list (red first, tester writes from the UAC)

pytest: confirm body with `cancelled_lines` on a closed 36/36 line writes status + audit columns, deletes the draft,
flips the OI rows to `changed`, echoes `lines_cancelled`; the other decided line in the same press confirms; 403
without edit; 409 covered line; 409 already cancelled; 422 blank reason; 422 beside `batch_id`; re-push keeps
`cancelled` + `cancelled_at`; header-cancelled push unchanged; board contribution carries the three fields; the
cancelled line is skipped by the next press.
vitest: Decide menu lists the item last; choosing it opens the dialog with Reason only; Save disabled on blank reason;
`runSave` posts verdict `cancelled` with the reason per ticked row; covered/cancelled rows skipped with the toast
wording; `pressRoleFor` routes a cancelled draft to `cancelled_lines`, not `lines`; `confirmLinesFor` body shape;
Decided column reads "Cancel SO"; list-view sentence shows who/when/reason; results block lists cancelled lines.
