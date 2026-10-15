# UAC - Quotation approval aligned with Purchase Request / Sponsorship Form (QUOTE-APPROVAL-UX)

**Plan:** `PLAN-quote-approval-ux-15oct.md`
**Owner rulings (15 Oct 2026, via crew, from "align with PR and SF"):** Q1 (a), Q3 (a), Q4 (a),
Q5 (a); R1-R8 approved; integration act-as users (`users.is_integration`, `models/user.py:63`) are
skipped as approval recipients. Q2 CORRECTED by the owner: no silent reset; locked while Approved,
explicit Revise (section D).

## Journey

1. The salesperson prices a quotation (PRJQ-2026-0011 on PRJ-000001 Setia Alam: two lines below
   the floor). The header chip reads **Draft**; the one primary button reads **Send for approval**.
2. They press it. The chip turns **Pending approval** (amber). Every sales manager (active holder of
   `projects.quotations.approve`, integration users excluded) gets a bell + email (+ WhatsApp where
   their own preference allows). While waiting the salesperson's primary button is **Sign** (if not
   yet signed); once signed there is no primary button, only the line "With a manager for approval".
3. A manager opens the quotation from the bell. Their header shows **Approve** (primary) and
   **Reject** (outline).
4. Reject: they type a reason. The chip turns **Rejected** (red), the reason shows in the banner,
   and the salesperson gets a bell + email + web push. The salesperson re-prices and presses
   **Send for approval** again, straight from Rejected. No "Back to draft" step. Managers are told
   again.
5. Approve: chip **Approved** (blue); the salesperson is told. Primary becomes **Sign** (if unsigned),
   then **Send to Customer R1**. After sending the chip reads **Issued**.
6. While Pending or Approved the lines are locked. To change them the salesperson picks **Edit
   quotation**, which asks for a reason and offers **Revise**; that clears the approval back to
   **Draft** and opens the form. Then **Send for approval** again. Nothing changes silently.
7. On the project's Quotations tab the list is the standard list: search on the left, tickboxes and
   a bulk strip (Delete, Export), a row "..." menu with the quotation's own actions. No floor /
   non-standard chips.
8. From a quotation page, **Back to project** (top right) and the **Project** breadcrumb both land on
   the project's Quotations tab.

## Phase 2 ACs

### A. Status chip (header and list read one function)

- **A1 [FE]** Given `approval_status_key = pending_approval`, when the header and the list row
  render, then both read "Pending approval" on a `Badge` variant `warning`.
- **A2 [FE]** Given `approved`, then "Approved" on `Badge` variant `primary` (blue, as PR/SF).
- **A3 [FE]** Given `rejected`, then "Rejected" on `Badge` variant `destructive`.
- **A4 [FE]** Given no approval rung (NULL), `draft` or `issued`, then the existing reading is kept:
  Draft (`secondary`) / Issued / Accepted / Changes requested.
- **A5 [FE]** Given an issued quotation (customer_decision `changes_requested`) whose next revision
  is `pending_approval`, then the chip reads "Pending approval" (approval rung wins, Q3).

### B. Notifications (best effort, never fail the action)

- **B1 [BE]** Given a below-floor quotation, when the salesperson sends it for approval, then every
  ACTIVE, non-trashed, non-integration (`users.is_integration` false) holder of
  `projects.quotations.approve` gets one notification (in-app + email; WhatsApp-eligible) whose data
  carries project_id, document_id and document_no; users without the grant get none; an
  `is_integration` holder gets none.
- **B2 [BE]** Given a quotation a manager approves, then the user who sent it for approval (actor of
  the latest `quotation_approval_requested` activity for that document; project owner when there is
  none) gets one notification (in-app + email + web push); the approver gets none for their own act.
- **B3 [BE]** Given a rejection with a reason, then the same recipient as B2 gets one notification
  whose body carries the reason.
- **B4 [BE]** Given a resubmit from Rejected (C1), then the approvers are notified again (B1 shape),
  once per press, even though an earlier press already notified them.
- **B5 [BE]** Given the notification backend raises, then send / approve / reject still return 200
  and the status still moves.

### C. Rejected -> resubmit (no migration)

- **C1 [BE]** Given a `rejected` quotation, when the salesperson posts the move to `pending_approval`
  on the existing route, then it lands on `pending_approval` in one request (graph hops
  rejected -> draft -> pending, both existing edges), `approval_rejected_reason` is cleared, and
  exactly ONE `quotation_approval_requested` activity row is written for that press.
- **C2 [BE]** Given a `rejected` quotation, when anyone posts a move to `draft`, then 422
  `quotation_status_not_self_serve` (there is no back-to-draft step any more).
- **C3 [FE]** Given a `rejected` quotation, then the reason shows in the banner and the header's
  primary button reads "Send for approval" (the label of the graph's draft -> pending_approval edge);
  no "Back to draft" control renders anywhere.
- **C4 [BE]** Given a `rejected` quotation, when its lines are edited, then it stays `rejected` with
  its reason until Send for approval.

### D. Locked while Pending or Approved; explicit Revise (owner correction 15 Oct, PR creator rule)

PR/SF rules reused (not their code; the portal engine is token-bound, see plan): locked at
submitted + approved (`portal_rev_0001_portal_submission_revisions.py:47`), Revise needs a reason
(`portal_revision_service.py:1534-1542`), Revise clears the approval fields (`:206-212`) and goes
back to the start, nothing is sent until the creator sends again, open-stage handlers are told
(`:1808`). Crew ruled (a) 15 Oct: whole quotation locked while Pending / Approved.

- **D1 [BE]** Given a `pending_approval` or `approved` quotation, when a line is added, edited or
  deleted (per-line routes, bulk replace, the form Save with `scopes`), a scope is added or removed,
  a scope is revised to a new version, or the version is recomputed, then 422
  `quotation_locked_for_approval` and nothing changes.
- **D2 [BE]** Given the same, when only header / cover letter / terms fields are patched (PATCH
  document with no `scopes`), then also 422 `quotation_locked_for_approval` (whole quotation locked,
  crew ruling 15 Oct). Signing is NOT an edit and stays allowed while Pending / Approved.
- **D3 [BE]** Given a `pending_approval` or `approved` quotation, when the salesperson (edit rights)
  posts Revise with a reason, then the rung becomes `draft`, `approval_rejected_reason` is NULL, one
  activity row `quotation_approval_revised` carries the reason, and lines become editable again.
- **D4 [BE]** Revise with an empty / whitespace reason -> 422 `quotation_revise_reason_required`;
  Revise on a NULL / `draft` / `rejected` / `issued` rung -> 422 `quotation_not_revisable`; without
  edit rights -> 403.
- **D5 [BE]** Revise from `pending_approval` notifies the approvers (B1 recipient set) that the
  request was withdrawn to be revised; Revise from `approved` notifies nobody.
- **D6 [FE]** On a Pending / Approved quotation the gear's "Edit quotation" opens the Revise prompt
  (reason field, required) instead of the form; confirming calls Revise, then opens the edit form.
  The Lines tab's per-scope "Revise to vN" does not render while locked.
- **D7 [FE]** After Revise, with a below-floor line still there, the chip reads Draft and the primary
  button reads "Send for approval".
- **D8 [BE]** A `rejected` quotation is NOT locked: lines edit directly (PR: rejected is editable).

### E. One primary CTA = the next action

- **E1 [FE]** Below floor, rung NULL/draft/issued (not approved), can edit -> primary "Send for
  approval"; no "Send to Customer" button renders.
- **E2 [FE]** `pending_approval`, viewer holds the approve grant -> primary "Approve" plus outline
  "Reject" in the header; Approve/Reject do not also render in the banner.
- **E3 [FE]** `pending_approval`, viewer without the grant, unsigned -> primary "Sign"; signed -> no
  primary button and the line "With a manager for approval".
- **E4 [FE]** Not blocked (nothing below floor, or approved), unsigned -> primary "Sign"; no "Send to
  Customer" button.
- **E5 [FE]** Not blocked and signed -> primary "Send to Customer R{n}" (enabled).
- **E6 [FE]** At most ONE primary-variant button in the header at any time (Reject is outline).
- **E7 [FE]** The gear keeps Sign quotation / Sign again, Edit quotation, Download PDF, Download Excel,
  Copy counter-sign link, Delete quotation.
- **E8 [FE]** The approval banner is information only: it renders the reason / line-count sentence
  and no buttons.
- **E9 [FE]** A reader who cannot edit (and lacks the grant) sees no primary button.

### F. Project > Quotations list

- **F1 [FE]** No "below the price floor" / "non-standard" chips render in the tab.
- **F2 [FE]** The toolbar is the app's `DataGridListToolbar`: search box first (left), Columns, then
  "Add a quotation" on the right (edit rights only).
- **F3 [FE]** Each row has a tickbox (`buildSelectColumn`); ticking shows the bulk strip "n selected"
  with Delete and Export.
- **F4 [FE]** Each row has a "..." menu (`RowActionsMenu`) with the SAME action set the record gear
  renders (one definition): Edit quotation, Sign quotation / Sign again, Download PDF, Download Excel,
  Copy counter-sign link, Delete quotation; items the record disables are disabled here too.
- **F5 [FE]** Clicking the row body still opens the quotation; clicking the tickbox or the menu does
  not navigate.
- **F6 [BE]** A record action `quotation_document.delete` exists on `/api/v1/pending-actions`: parked
  for the destructive window, then runs the existing `delete_document`; requires
  `projects.projects.delete` AND edit rights on the project at the click (same checks as the DELETE
  route); refused for an issued quotation with the existing 422 `quotation_document_issued`.
- **F7 [FE]** Delete (row menu, bulk strip, record gear) is the standard countdown with Cancel; no
  confirmation dialog; `ConfirmDeleteDialog` is no longer imported by the quotation document page or
  the panel.
- **F8 [FE]** Bulk Delete excludes issued quotations (disabled with a reason when every ticked row is
  issued; otherwise acts on the deletable ones and says how many were skipped).
- **F9 [FE]** DataGrid rules: `tableLayout` fixed + resizable, explicit `size`, `truncate` + `title`.

### G. Back to project

- **G1 [FE]** The quotation page header (view tabs) shows "Back to project" top right, the same
  `BackToList` as the project page, linking to `/project-sales/<projectId>?tab=quotations`.
- **G2 [FE]** On the quotation view, edit and new pages, the "Project" breadcrumb links to
  `/project-sales/<projectId>?tab=quotations`; other project sub-pages keep `/project-sales/<projectId>`.

### Layout

- **L1 [UX]** Header and list usable, non-clipped at 375px and 1280px. No new motion.
