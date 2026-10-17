# Behaviour card: link every form to a project (FORMS-PROJECT)

**Answered 17 Oct 2026 (crew):** Q2 (a) one Forms tab, Q3 (a) keep free text, suggestion only,
Q4 (a) optional everywhere, Q5 (a) portal picker too, checked server-side. Q1 (scope) with the owner;
the (a) scope is built meanwhile (workflow + form-builder submissions out).

Base: origin/main fde0a5597. Dev examples: pending the crew data request on PR #1768 (masked rows,
not copied into the repo). Fixes #1689.

## Today (code)

| Form | Link column | Staff form | Portal form |
| --- | --- | --- | --- |
| Quotation | `quotations.project_id` NOT NULL (`models/projects.py:1365`) | made inside a project | none |
| Sample request | `sample_requests.project_id` (`models/sample_request.py:70`) | picker, checked (`sample_request_service.py:207`) | picker, contact's companies only (`:232`) |
| Purchase request / Sponsorship form | `purchase_requests.project_id` (`models/procurement.py:1112`) | picker shown (`PurchaseRequestForm.tsx:700`) but **dropped** by `toRequestBody` (`purchaseRequestService.ts:134-165`, #1689); backend writes it unchecked (`procurement_service.py:9323`, `:9417`) | SF: picker for flagged contacts, checked at submit (`portal_service.py:1096`); PR: no picker, and an API-sent `project_id` is **not** checked |
| Complaint | `complaints.project_id` (`models/complaints.py:43`) | picker (`ComplaintForm.tsx:891`), backend unchecked (`complaints_service.py:1707`) | free-text `project_title` only (`portal_service.py:1620`) |
| Stock inquiry | none; free text `project_name` + `project_customer` (`models/procurement.py:1036-1037`) | text inputs (`StockInquiryForm.tsx:308-335`) | text inputs (`SubmissionForm.tsx:200`) |
| Price tag request | none (`models/price_tag.py:82`) | none | none |
| Sales opportunity | none (`models/sales.py:200`) | none | none |
| Workflow submission / form-builder submission | none; JSON `header_data` / `submission_data` | n/a | n/a |

Project page (`ProjectDetailClient.tsx:77-90`) lists Quotations, Sample requests and Sponsorships
(SF only, `sponsorship_link_service.py:144`). Linked complaints and PRs appear nowhere.

Staff picker: `SearchableSelect` + `searchProjectsForLink` (`complaintService.ts:467`) over
`GET /project-sales/projects/` (company-scoped, `projects.projects.view`). Portal picker:
`AsyncCombobox` + `lookupProjects` over `GET /public/portal/lookups/projects` (contact's companies).

## Rules (proposed)

1. **One picker, everywhere.** Every form in scope gets the same "Project (optional)" picker the
   PR/SF form uses (staff `components/common/SearchableSelect.tsx` + `searchProjectsForLink`;
   portal `app/(auth)/portal/components/AsyncCombobox.tsx` + `lookupProjects`), clearable, placed
   where the form's project fields already sit (PR/SF / Stock Inquiry layout). No new component, so
   no mock.
2. **New link columns** (nullable FK to `projects.projects.id`, ON DELETE SET NULL, indexed):
   `stock_inquiries.project_id`, `price_tag_requests.project_id`, `sales.opportunities.project_id`.
3. **Gaps closed on forms that already have the column:** staff PR/SF sends `project_id` (#1689);
   portal complaint gets the picker; portal PR gets the picker and the check.
4. **Every write is checked**, staff and portal, create, edit and update-and-reply:
   - staff: the project must be visible to the editor (same company scope as the picker list);
     otherwise 422 "That project was not found." on the project field;
   - portal: the project must belong to one of the contact's companies (existing
     `assert_portal_project_allowed` rule); otherwise 422 "Pick one of your own projects.";
   - clearing the picker saves no link (explicit null clears it on edit).
5. **Project page: one new "Forms" tab** with a section per form type (Complaints, Purchase
   requests, Sponsorship forms, Stock inquiries, Price tag requests, Sales opportunities). Each
   section is the form's own list filtered by `project_id`, so the form's own row rules apply
   (e.g. own-rows-only users still see only their own). A section shows only if the user holds
   that form's view permission (`complaint_management.complaints.view`,
   `procurement.purchase_requests.view`, `procurement.sponsorship_forms.view`,
   `procurement.stock_inquiries.view`, `dealer_kit.price_tag_requests.view`,
   `sales.opportunities.view`). Rows open the form's detail page. Empty section = heading + hint.
   Quotations, Sample requests and Sponsorships tabs stay as they are.
6. **Form detail pages** show the linked project as `CODE - Title`, a link to `/project-sales/{id}`;
   "No project" when empty.
7. **Free-text names stay.** Stock inquiry `project_name` / `project_customer`, and `project_title`
   on PR/SF/complaint, are kept, shown and editable. No backfill, no name matching. When a form has
   a project name but no link, the picker's search box opens pre-filled with that name
   (`SearchableSelect initialQuery`), so likely matches list first; nothing is linked until a person
   picks one.
8. **Required:** optional everywhere. Existing switches keep working unchanged (SF
   `requires_registered_project` contact flag, sample request "require project" setting).

## Edge cases

- Project deleted: the FK sets the form's link to null; the form shows "No project".
- Project in another company (staff on company A editing a form, project in B): 422, picker never
  offers it.
- Portal contact with no linked company: picker shows "No projects"; a hand-crafted `project_id`
  is refused (422).
- Edit that does not send `project_id` leaves the link as is; sending null clears it.
- External n8n API (`schemas/external/*`) is unchanged: it never sets a link today and stays so.
- Every save failure ends with the error under the picker and the form still editable (no stuck
  spinner).

## Questions (answers pending)

See the `crew-ask` on PR #1768.
