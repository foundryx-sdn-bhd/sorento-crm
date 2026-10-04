# PLAN - PR / SF: PDF export + PIC field

**Status:** Draft - awaiting grill
**Applies to:** Purchase Request (PR) and Sponsorship Form (SF). They share one
detail component and one table (`purchase_requests`), so every change is made
once and lands on both.

---

## Journey (Phase 0)

**Who.** Two actors, same record.

1. **A contact on the portal** submitting a PR/SF (often from a WhatsApp link,
   frequently uploading a document and letting AI pre-fill).
2. **Office / CS staff** opening the submitted form in the CRM, and later
   printing it to hand to a driver or file against a delivery.

**What they do today, and why it hurts.**

- The person on site is a real human with a phone number, but the form has
  nowhere to put them. So they get typed into **Delivery Address**:
  `2, Lebuh Cecil, Ghaut, 10300 George Town, Pulau Pinang Contact: CONTACT Q
  (012-000 0502)`. The address field is now two facts wearing one coat: nobody
  can read the address programmatically, and the contact is invisible to anyone
  scanning the form.
- To print, staff hit **Export to Excel**. A long address blows one cell out to
  an enormous width, and the printed sheet is unusable. Excel is a *data* format
  being asked to do a *document* job.

**After.**

- The submitter (portal) and staff (CRM) both see a **PIC** field directly under
  Customer Name. Optional, free text, "name and contact number". The address
  goes back to being only an address.
- Anyone can hit **Download PDF** and get a tidy, fixed-layout document - the
  same mechanism complaints and stock inquiries already use - with PIC printed
  on it.
- Excel stays for whoever pipes it into another sheet, and gains a PIC column.

**Decisions taken (2026-08-06):** keep Excel *and* add PDF (nobody loses a
workflow mid-flight); PIC **is** part of AI extraction.

---

## Why PIC is one free-text field, not two

The obvious modelling instinct is `pic_name` + `pic_phone`. Rejected:

- The user asked for "free text field for user to key in name and contact
  number" - one box.
- Real data is messy (`CONTACT Q (012-000 0502)`, `CONTACT Q / CONTACT R 012-0000502`, a
  name with no number). Two required-shaped columns invite empty halves and
  validation fights over a field that is explicitly **not mandatory**.
- Nothing downstream parses it. It is printed and read by humans.

If a structured contact is ever needed, `requested_by_contact_id` already exists
as the FK-shaped path - PIC is deliberately the informal one.

---

## Acceptance criteria

### PIC field

- **AC-1** `purchase_requests` gains a nullable `pic` (Text). Applies to both
  `request_type` values; no backfill (historical rows keep PIC empty, their
  contact stays embedded in the address - we do not attempt to parse it out).
- **AC-2** CRM form: PIC renders **immediately below Customer Name**, optional,
  free text, placeholder naming both parts (e.g. `CONTACT Q (012-000 0502)`).
- **AC-3** Portal submission form: same field, same position, same optionality.
- **AC-4** Portal `/view` (read-only contact view) and the `(auth)/approval`
  page display PIC when set. Per the CRUD standard the section still renders
  when empty - an em-dash, not a hidden row.
- **AC-5** PIC round-trips: create, edit, portal submit, approval view.
- **AC-6** An empty PIC is stored as NULL, never `""` - so "not given" is one
  value, not two.

### PDF export

- **AC-7** PR/SF detail gains **Download PDF** alongside the existing Excel
  button, using the same mechanism as complaint / stock inquiry:
  `POST /{id}/export/pdf` → RQ task → `user_downloads` row → drawer.
- **AC-8** The PDF mirrors the detail page: same row order, same labels, same
  status wording. Internal-only fields (SLA tier/assignee, handling lock, audit
  trail) are excluded - the rule `complaint_pdf_service` and
  `stock_inquiry_pdf_service` already follow.
- **AC-9** **Tidy is the point.** A long address must wrap inside a
  fixed-width bordered cell and never widen the table or spill the page. This is
  the specific failure being fixed, so it gets a test with a deliberately
  pathological address.
- **AC-10** PIC prints on the PDF, directly under Customer Name.
- **AC-11** Filename `purchase-request-<number>.pdf` /
  `sponsorship-form-<number>.pdf`, matching the `product-inquiry-<number>.pdf`
  convention.
- **AC-12** Line items render as a table; a form with zero items still produces
  a valid PDF with an explicit empty state.

### Excel

- **AC-13** The existing export gains a PIC column. Nothing else about it
  changes - no column reordering, so anyone's downstream sheet keeps working.

### AI extraction

- **AC-14** `pic` is an `ExtractFieldSpec` on both `_PORTAL_PURCHASE_REQUEST` and
  `_PORTAL_SPONSORSHIP_FORM`.
- **AC-15** Its `note` must steer the model to the **site/receiving contact** - 
  explicitly NOT the salesperson, NOT the requester, NOT the company. This is
  the same disambiguation `customer_name` already needed, and the reason that
  field's note is long.
- **AC-16** Extraction returning nothing for PIC is normal, not an error - the
  field is optional and many documents have no named contact.

---

## Implementation

### Backend

| # | Change |
|---|---|
| B1 | Migration: `ALTER TABLE purchase_requests ADD COLUMN pic TEXT` (single head - check `alembic heads` first, this repo has forked before) |
| B2 | `PurchaseRequestHeader.pic` |
| B3 | Schemas: create / update / response + portal + external. Note the `get_user`-style trap: a manual dict builder drops fields that aren't listed, so grep for every place the response is hand-assembled |
| B4 | New `app/services/purchase_request_pdf_service.py`, mirroring `stock_inquiry_pdf_service` and reusing `pdf_render` helpers |
| B5 | New `generate_purchase_request_pdf` task in `app/tasks/export_tasks.py` |
| B6 | New route `POST /purchase-requests/{id}/export/pdf` |
| B7 | `pic` spec added to both portal form schemas in `form_schema_registry.py` |

### Frontend

| # | Change |
|---|---|
| F1 | `purchaseRequest.types.ts` - `pic?: string \| null` |
| F2 | `purchase-request-schema.ts` - optional string |
| F3 | `PurchaseRequestForm.tsx` + `PurchaseRequestDocumentEditCard.tsx` - field under Customer Name |
| F4 | `PurchaseRequestDetail.tsx` - display PIC; add Download PDF button beside Excel |
| F5 | Portal `SubmissionForm.tsx` - PIC field |
| F6 | Portal `/view` + `(auth)/approval/page.tsx` - display PIC |
| F7 | `purchase-request-excel-export.ts` - PIC column |
| F8 | `AIExtractDialog.tsx` - PIC among pre-filled fields |

### Tests (land with the code, not after)

- **pytest** - PDF service renders for PR and SF; long-address wrapping
  (AC-9); PIC present in output; empty line items; route happy path + auth
  denial; `pic` round-trips through create/update.
- **vitest** - PIC renders and submits in CRM form and portal form; detail page
  shows PIC and the PDF button; Excel export includes the column.
- **playwright** - portal submit with PIC → CRM detail shows it → download PDF
  produces a file.

---

## Risks

1. **A migration on a shared dev DB across worktrees.** Several worktrees point
   at one database. Apply via `Operations.context`, never `stamp`, and confirm a
   single head before writing the revision.
2. **WeasyPrint native deps.** The worker needs `DYLD_FALLBACK_LIBRARY_PATH` +
   brew pango locally; prod needs cairo/pango in the image. The Dockerfile
   already installs them for the complaint PDF, so this rides along - but the
   local worker must be restarted after adding the task (RQ tasks do not reload).
3. **PR and SF share one component.** Every change must be checked against
   *both* `request_type` values; a field shown only for PR is a regression for
   SF and vice versa.
4. **AI extraction quality.** PIC will sometimes pull the salesperson. AC-15's
   note is the mitigation; validate against real submitted documents, not
   invented ones, per the "E2E uses real user samples" rule.

---

## Out of scope

- Parsing PIC out of historical `delivery_address` values.
- Structured `pic_name` / `pic_phone` columns.
- Removing the Excel export (explicitly kept).
- Changing the PDF layout of complaints / stock inquiries.
