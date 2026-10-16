# PLAN - Quotation approval and list aligned with Purchase Request / Sponsorship Form (QUOTE-APPROVAL-UX)

**Status:** build, track M (FE + BE on existing screens, no migration). Behaviour card answered
15 Oct 2026: Q1 (a), Q3 (a), Q4 (a), Q5 (a), R1-R8 approved; Q2 corrected by the owner to PR's
creator-revision rule and ruled (a) by crew: whole quotation locked while Pending / Approved,
explicit Revise with a reason. Item G added 15 Oct (Back to project). Crew audit 15 Oct (bc59cfcc0):
chip = PR/SF status pill, own WhatsApp use case `quotation_approval`, notify after the route commits,
reject keeps its reason, Recheck hidden while locked, toolbar search slot; scope won/lost outcome NOT
locked (it records the customer's answer on a revision they already hold and changes no price).
Reviewer READY + security clean on e4817037a; browser pass 1280/375 PASS (evidence/quote-approval-ux).
Deferred: stale-approve token (pre-existing window; approve/reject carry no request id).
**UAC:** `quote-approval-ux-15oct-acceptance-criteria.md`
**Mock:** skipped by owner exception (existing components only).

## Facts (origin/main 6c4ddcb71)

PR / SF side (SF is the same `PurchaseRequestHeader`, `models/procurement.py:1092-1099`):
- Chip words `PurchaseRequestDetail.tsx:1216-1227`; colours `lib/status-pill.ts:9,13,22,23`.
- Approver told via SLA stage assignment, in-app + email + WhatsApp by preference
  (`form_sla_service.py:871-1082, 1469-1512`); requester told on decision in-app + email + web push
  (`procurement_service.py:8894-8976`).
- Rejected: creator edits and presses the same Submit (`portal_service.py:1113-1132, 1427-1439`);
  no back-to-draft (`:1031-1038`).
- Creator revision: allowed at submitted + approved (`portal_rev_0001...py:47`), reason required
  (`portal_revision_service.py:1534-1542`), approval fields cleared (`:206-212, 1598-1606`), back to
  the start (`:1626-1629`), open-stage handlers told (`:1808`). The engine is token-bound
  (`revise()` takes a `PortalToken`, `fetch_owned` `:1333`), needs `revision_no` and a
  `portal_revision_configs` row, and voids SLA trackers: its code is NOT called; its rules are.
- All CTAs in the header primary slot (`PurchaseRequestDetail.tsx:830-935`), banners info only.

Quotation side:
- Approval service `project_quotation_approval_service.py`: `move` `:285-335` (self-serve keys
  `:66`), `approve` `:350-374`, `reject` `:377-418`, `_set_status` `:225-231`, `_record` `:247-282`,
  `mark_issued` `:208-219`, `serialize_approval` `:424-440`.
- Routes `api/v1/projects/quotation_documents.py`: PATCH document `:182-242`, DELETE `:245-268`,
  add scope `:271`, patch scope `:304`, issue `:355-382`, move `:418-452`, approve `:455-488`,
  reject `:491-528`, sign `:531-565`; `_editable_project` `:84-91`.
- Line / scope routes `api/v1/projects/quotations.py`: PUT scope `:179`, DELETE scope `:229`,
  revise scope `:429`, lines `:471,502,548,582`, recompute `:603`; `_notify_breaches` `:79-115`.
- Version guard `assert_editable` `project_quotation_service.py:205-226`, called by `upsert_line`
  `:958`, `replace_lines` `:1001`, `delete_line` `:1052`, `recompute_version` `:1093`.
  Scope paths: `update_quotation` `:326`, `revise` `:348`, `delete_quotation` `:426`;
  `project_quotation_document_service.py` `add_scope` `:255`, `apply_form_scopes` `:416`,
  `remove_form_scopes` `:387`, `update_document` `:813`, `delete_document` `:597-614`.
- Graph edges `project_seed_service.py:208-213` (no rejected -> pending edge).
- Recipient helpers `project_notify_service.py`: `management_user_ids` `:34-66` (query shape to
  copy for the approve slug), `_send` `:69-131` (best effort per recipient).
- `users.is_integration` `models/user.py:63`; dev: 1 of 3 active approve-slug holders is flagged.
- Record actions `services/record_actions.py` (`register(FormAction(...))`, e.g. `:2272-2285`);
  park-time hooks `api/v1/system/pending_actions.py:163-230`.
- FE: `QuotationDocumentClient.tsx` (header `:305-512`, gear `:369-443`, primary `:445-498`,
  `startEditing` `:232-252`, ConfirmDeleteDialog `:598-608`); `QuotationApprovalPanel.tsx`
  (`approvalMoves` `:57-68`, buttons `:170-191`); `quotationDecision.ts:19-29`;
  `QuotationsPanel.tsx` (chips `:193-204`); `QuotationDocumentPageHeader.tsx`; `crumbs.ts:18-27`;
  `BackToList.tsx`; list pattern `StockTransfersPanel.tsx:185,308,444-457,550-637` and action-set
  file `inventory-management/stock-transfers/actions.tsx`; deferred hooks
  `hooks/useDeferredRowAction.tsx`, `hooks/useDeferredBulkAction.tsx`,
  `components/common/DeferredActionButton.tsx`.

## Design (simplest thing)

### BE
1. **Resubmit (C1).** In `move`, a target of `pending_approval` from `rejected` checks both existing
   edges (rejected -> draft, draft -> pending) with `assert_transition_allowed`, then sets pending
   once and records ONE `quotation_approval_requested` row. `draft` leaves `SELF_SERVE_KEYS`
   (C2).
2. **Notifications (B).** `project_notify_service`: `approver_user_ids(db)` (approve slug, ACTIVE,
   not trashed, not `is_integration`) and three senders reusing `_send`:
   requested (to approvers, in-app + email, `send_whatsapp=True`), decided (approved / rejected to
   the requester, in-app + email + web push), withdrawn (revise from pending, to approvers).
   Requester = actor of the newest `quotation_approval_requested` activity whose payload names the
   document; fallback project owner. Dedup key per press (activity id) so a resubmit re-notifies.
   Called after the status write from `move` / `approve` / `reject` / `revise_approval`, best
   effort.
3. **Lock (D1, D2).** `approvals.assert_unlocked(db, document)` raises 422
   `quotation_locked_for_approval` when the rung is `pending_approval` or `approved`.
   `assert_editable(version)` resolves the version's document and calls it, which covers every
   line path; the scope paths (`add_scope`, `update_scope` (PATCH `.../scopes/{id}`,
   `quotation_documents.py:304`), `apply_form_scopes`, `remove_form_scopes`, `update_quotation`,
   `revise`, `delete_quotation`) and `update_document` call it directly.
   Sign, issue, approve, reject, move and delete are not edits and are not locked.
4. **Revise (D3-D5).** `approvals.revise_approval(db, document, actor_user_id, reason)`: refuses
   empty reason (422 `quotation_revise_reason_required`) and rungs other than pending / approved
   (422 `quotation_not_revisable`); `_set_status(draft)` (system move, like `mark_issued`); one
   `quotation_approval_revised` activity row with the reason; from pending, notify approvers.
   Route `POST .../quotation-documents/{id}/approval-revise` gated like move (EDIT +
   `_editable_project`).
5. **Deferred delete (F6).** `register(FormAction(key="quotation_document.delete",
   entity_types=("quotation_document",), window=WINDOW_DESTRUCTIVE,
   permission="projects.projects.delete"))`; execute loads the document, re-checks
   `assert_can_edit_project` for `requested_by_id`, calls `delete_document`. Park-time branch in
   `_assert_undo_not_refused` (or a sibling) runs the same edit-rights and issued checks.

### FE
1. **Chip (A).** `quotationStanding` gains the approval rungs first (A1-A5); variant union adds
   `primary` and `destructive`. Header and list already both call it.
2. **Header CTA (E).** One function `nextAction(record, { canEdit, canApprove, isSigned })` returns
   the primary (send_for_approval | approve | sign | send | none) plus a hint; the header renders
   it in `DetailActions` `primary`, Reject as outline beside Approve. The banner keeps only its
   sentences (E8). Send for approval label from the graph's draft -> pending edge (fallback "Send
   for approval").
3. **Revise (D6, D7).** `startEditing`: locked (pending / approved) -> the existing
   `ReviseToEditDialog` pattern with a required reason field ("What changed, and why?", PR
   `SubmissionForm.tsx:2031`) and confirm "Revise"; confirm calls
   `reviseQuotationApproval(projectId, documentId, reason)` then pushes the edit path. The Lines
   tab's per-scope "Revise to vN" hides while locked.
4. **Action set (F4, E7).** `quotation-documents/actions.tsx` builds the gear items once
   (`RecordAction[]`); the record gear renders it via `DetailActionsMenu actions`, the list row via
   `RowActionsMenu`. Delete uses the deferred row action (`quotation_document.delete`); the record
   page drops `ConfirmDeleteDialog`.
5. **List (F).** `QuotationsPanel` keeps `PanelDataGrid` and passes `toolbar={({ table }) =>
   <DataGridListToolbar table searchSlot=... primaryAction=Add bulkActionsSlot=Delete ... />}`
   (search moves into the toolbar's search slot, left), `buildSelectColumn` first, `actions` column
   last; the floor chips go.
6. **Back (G).** `projectCrumbs(projectId, { tab: 'quotations' }, ...below)` option, used by
   `QuotationDocumentPageHeader` and `QuotationFormClient`; PageHeader `actions={<BackToList
   listPath=".../?tab=quotations" label="Back to project" appendListState={false} />}`.

### No motion
No new animation; the countdown is the existing deferred-action UI.

## Contract (for tester and coder)

BE (prefix `/api/v1/project-sales`):
- `POST /projects/{p}/quotation-documents/{d}/approval-status` `{to_status_id}`: rejected ->
  pending allowed (walk), `draft` target 422 `quotation_status_not_self_serve`.
- `POST /projects/{p}/quotation-documents/{d}/approval-revise` `{reason}` -> 200 document payload.
- 422 `quotation_locked_for_approval` on every write listed in Design BE 3 while pending/approved.
- `POST /api/v1/pending-actions` `{action_key:"quotation_document.delete", entity_type:
  "quotation_document", entity_id:<d>, payload:{}}` -> 202; 403 without `projects.projects.delete`
  or edit rights; 422 `quotation_document_issued` when issued.
- Activity templates: `quotation_approval_requested` (existing), `quotation_approval_revised` (new,
  payload `reason`).
- Notification `type` values: `project_quotation_approval_requested`,
  `project_quotation_approval_approved`, `project_quotation_approval_rejected`,
  `project_quotation_approval_withdrawn`; `data` carries `project_id`, `project_code`,
  `document_id`, `document_no`.

FE:
- Service: `reviseQuotationApproval(projectId, documentId, reason)` in
  `_shared/services/quotationDocumentService.ts`; hook `mutations.reviseApproval`.
- Labels: "Send for approval", "Approve", "Reject", "Sign", `Send to Customer R{n}`, hint "With a
  manager for approval", gear "Edit quotation", dialog field "What changed, and why?", confirm
  "Revise", "Back to project", crumb "Project".
- Pending action: `createPendingAction` from `@/services/pendingActionService` with
  `action_key: 'quotation_document.delete'`, `entity_type: 'quotation_document'`.

## Slices (one lane, one PR)
S1 BE (C, B, D, F6) - S2 FE (A, E, D6-D7, F, G). Tester writes both red sets first.

## Hand test
PRJQ-2026-0011 on PRJ-000001 Setia Alam (2 below-floor lines): send -> reject -> amend -> resubmit
-> approve -> revise -> resend -> approve -> sign -> send. Never send to a real customer.
