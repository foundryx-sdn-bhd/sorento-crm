# PLAN: Two-way comment thread on form submissions (FORM-COMMENTS)

Status: Card posted, waiting on owner answers (Q1-Q5); building the non-blocked parts meanwhile.
Track: full lane (one additive migration, notifications on two channels).

Owner request (9 Oct 2026): every form submission page gets a comment thread reusing the
Ideation Comments component, so a submitter can answer a purchasing clarification (today: one
purchasing reply -> WhatsApp -> the process stops).

## What exists today (measured)

- "Product Inquiry Form SI26-0178" is a `stock_inquiries` row (`app/models/procurement.py:995`);
  the heading is only print layout (`ProductInquiryFormLayout.tsx:21`). The four portal form kinds
  are hardcoded: complaint, stock_inquiry, purchase_request, sponsorship_form
  (`lib/portal-form-kinds.ts:15-28`, `app/models/respond_template.py:37`). Purchase request and
  sponsorship share `purchase_requests`.
- Every kind carries the same three fields this lane needs: `contact_id` (the submitter, a respond
  contact, not a CRM user), `respond_inbox_url` (the WhatsApp identifier source) and
  `last_responded_by` (`complaints.py:46-51`, `procurement.py:1034-1039`, `:1139-1141`).
- The submitter sees the record on the PORTAL (`(auth)/portal/components/SubmissionForm.tsx`, green
  box at `:1634-1659`); staff see it on the CRM detail pages (`StockInquiryDetail.tsx`,
  `ComplaintDetail.tsx`, `PurchaseRequestDetail.tsx`, each ending in Attachments then AuditTrail).
- Ideation comments: `components/ideas/IdeaComments.tsx` is idea-specific (`ideaId` prop, idea
  hooks); the backend only relays to the external Ideas service, there is NO local comment table to
  reuse (`app/api/v1/ideation/ideas.py:102-159`).
- A generic thread table already exists and is unused by any code path:
  `entity_conversation_messages` (`app/models/entity_conversation.py`: entity_type, entity_id,
  scope, is_system, body_html, author_user_id, created_at; migration 150). It has no way to name a
  portal contact as author.
- Purchasing reply WhatsApp: `update_inquiry_and_reply` (`procurement_service.py:6550`) ->
  `_enqueue_stock_inquiry_respond_message` (`:6232`, queue `respond_io`) -> worker
  `_send_and_log(use_case="stock_inquiry")` (`app/tasks/respond_io_tasks.py:20`): text inside the
  24h window, otherwise the template the admin mapped to the use case. Complaint has the same path
  (`send_complaint_respond_message`, `:292`).
- Team email: `_notify_team_stock_inquiry` (`procurement_service.py:5193`): Notification with
  `single_email_to_all`, delivered on the `notifications` queue, template via
  `EmailTemplateService.render_code`. No email exists on the respond path today.

## Proposed rules (the card)

1. Storage: reuse `entity_conversation_messages`, `scope='form_comment'`,
   `entity_type` = form kind, `entity_id` = row id. Additive migration: one nullable column
   `author_contact_id` (FK `respond_contacts`, SET NULL) so a portal comment names its author.
   No new table.
2. UI: extract the presentational part of `IdeaComments` into a shared `CommentThread` (header
   "Comments <count>", avatar + "Write a comment" + Comment button, "No comments yet");
   `IdeaComments` becomes a thin wrapper, so ideation looks and behaves the same. Form threads are
   add-only in v1 (no reply/edit/delete: the WhatsApp/email has already gone out).
   Placement: CRM detail pages, after Attachments and before Audit trail; portal page, after the
   Attachments card and before the button row. Same "thread at the bottom" placement as ideation,
   so no mock.
3. Who: the submitter (portal session that owns the submission, the same check as
   `PortalService.get_submission`) and any staff user who can open the CRM record.
4. Staff comment -> WhatsApp to the submitter through the existing window-aware path
   (`_send_and_log` with the kind's own use case), never inline in the request.
5. Submitter comment -> one email to the form's staff participants (see Q2), new system email
   template `form_comment_from_submitter` editable in Email Templates.
6. Never-stuck: the comment row commits first; the send is enqueued after. A failed send is an
   `integration_logs` failed row (as today) and does not roll back or hide the comment.
7. Drafts have no thread (not submitted). Voided: thread visible, composer hidden.

## Notification wording (drafts; owner decides)

WhatsApp (staff -> submitter, in-window text):

    There is a new comment on your stock inquiry SI26-0178:
    Which brand do you need?

    https://<portal link to the submission>

Outside the 24h window the mapped template for the kind's use case goes out with
`update` = the comment text and `portal_url` as today.

Email (submitter -> staff):

    Subject: New comment on Stock Inquiry SI26-0178
    Body:    Ali (Dealer ABC) commented on Stock Inquiry SI26-0178:
             "Hafele, model HT-200"
             Open: https://<crm>/procurement-management/stock-inquiries/<id>

## Examples

This sandbox has no dev data (crew rule: no shared DB), so the three are shapes, anchored on the
owner's own case:
1. SI26-0178, Responded, purchasing_response "brand?". Submitter comments "Hafele" -> email to
   the purchasing user who replied. Purchasing comments "ok, 2 weeks lead time" -> WhatsApp.
2. A complaint in Responded where CS asked for a photo: the customer comments; CS gets the email.
3. A voided purchase request: thread readable on both sides, no composer.

## Questions (Q1-Q5, posted as crew-ask)

- Q1 existing "Comment / reply by purchasing" field: (a) keep it as is, thread below it;
  (b) show it as the thread's first entry. Rec (a).
- Q2 email recipients on a submitter comment: (a) participants = last_responded_by + SLA handler
  + staff who already commented, falling back to the kind's team; (b) always the whole team. Rec (a).
- Q3 status after a submitter comment: (a) unchanged; (b) back to pending_purchasing (reopens the
  SLA clock). Rec (a).
- Q4 kinds: (a) all four (complaint, stock inquiry, purchase request, sponsorship); (b) stock
  inquiry only first. Rec (a).
- Q5 attachments in comments: (a) none in v1, the Attachments section stays the place; (b) allow.
  Rec (a).
