# Behaviour card: New Product Application (NEW-PRODUCT-APP)

Status: card filed for owner approval (17 Oct 2026). Size L, full track (migration + new permissions).
Owner 15 Oct: "stock inquiry about a new product code -> inquiry responded -> new product application ->
purchasing opens the product code after receiving the application."
Paths: BE = `sorento_crm_backend/app/`, FE = `sorento_crm_frontend/`.

## What exists today (measured, main fde0a5597)

- **Stock inquiry** (`models/procurement.py:1003`, table `stock_inquiries`) carries ONE product as text:
  `product_code`, `item_description`, `project_customer`, `project_name`, `quantity`, `delivery_date`,
  `purchasing_response`. No product FK and no link to any other form (nothing references `stock_inquiries`).
  Statuses are hard-coded in `services/procurement_service.py` (not the status engine): `draft` (portal) ->
  `pending_project_sales` -> `pending_purchasing` -> `responded`, plus `rejected`, `voided`. "Responded" = status
  `responded`, set only by Update & Reply (`procurement_service.py:6842`).
- **AutoCount items flow INTO the CRM only.** "Sorento never calls AutoCount"
  (`documentation/plans/_archive/autocount/PLAN-autocount-integration.md:19-24`). A new code is opened in AutoCount by
  purchasing and reaches `products` through the ESB push `POST /external/ingest/products`
  (`api/v1/external/ingest.py`, payload `CanonicalProduct`, `schemas/canonical_masters.py:187-212`) or a staff pull.
  So the CRM can never "create the code"; it can only ask for it and then **link** the product once it has arrived.
- **What AutoCount sends per item** (`CanonicalProduct`) = what purchasing has to decide when opening a code:
  item code, description, further description (`remark` = `Item.Desc2`), item group (`category_code` ->
  `product_categories`), base UOM (`uom_code` -> `units_of_measure`), brand, item type (`item_type_code` ->
  `item_types`), barcode, list price, cost price. Supplier lives beside the item (`product_suppliers`,
  `models/procurement.py:96`). These are the application's line fields.
- **PR approval, as it really is:** a PR is approved through a form-SLA stage (`form_sla_service.py:216-218`, default
  approver setting at `:867-991`). On the staff side an approved PR is **not** locked (`update_request`,
  `procurement_service.py:9387`, only refuses a status change) and there is **no staff Revise button**; Revise exists
  only in the portal (`PortalRevisionService.revise`, `portal_revision_service.py:1502`: reason, `revision_no + 1`,
  snapshot row in `portal_form_revisions`, back to `submitted`, approval cleared). See Q2.
- **Freshest sibling to copy:** Sample Request (#1718): status-engine graph
  (`modules/projects/status_entities.py`), PR-style document card, product search that ignores dashes and case and
  only lists (`services/sample_request_service.py:538`), countdown delete of drafts, `request_no` numbering.

## What ships

One record, **New Product Application** `NPA-000001`, under Procurement Management > New Product Applications.

## Rules

1. **Start.** The main entry is a **Create new product application** button on a stock inquiry whose status is
   `responded` (gear menu of `StockInquiryDetail.tsx`). It opens the New form pre-filled from that inquiry: inquiry
   link, project name, customer TEXT, needed-by date, and line 1 with the inquiry's product code as the proposed code,
   its description and quantity. Pre-fill copies text only; the customer text is never matched to a debtor (owner
   rule, no auto name-matching): the applicant picks the debtor in the searchable box or leaves the typed name.
   Staff can also press **New** on the list and pick the inquiry themselves (Q3).
2. **Header** (PR / SF / Stock Inquiry document card): Applicant (signed-in user, read-only), Date (today), Stock
   inquiry (searchable picker of inquiries, shows `SI26-0123 - <product code>`, clearable), Customer (the PR
   searchable customer box; free text allowed, then "No debtor code"), Project name, Needed by (date), Reason / remark.
3. **Line items** (one row per new code, Q4). Columns:
   - Proposed code (text, optional: purchasing decides the final code) and Description (required);
   - Further description (optional, AutoCount Desc2);
   - Item group (`SearchableSelect` over `product_categories`, required on submit);
   - Base UOM (`SearchableSelect` over `units_of_measure`, required on submit);
   - Brand (`SearchableSelect`, clearable); Item type (`SearchableSelect` over `item_types`, clearable);
   - Supplier (`SearchableSelect` over `suppliers`, clearable) and Supplier item / model no (text);
   - Cost price and Selling price (number, **clearable**, MYR), Expected qty (number, clearable);
   - Remark.
   Every optional select is clearable; every number field can be emptied (owner rule).
   The table sits inside its own horizontal scroll box at 375 px, exactly like the PR line table.
4. **No attachments in this lane.** Each form's attachment section is wired to its own endpoints
   (`PurchaseRequestAttachmentsSection.tsx:10-12`), so this would add a new upload surface. Follow-up if purchasing
   asks for the supplier quote on the application itself.
5. **Statuses** (status-engine graph `new_product_application`, labels editable in Status graphs, like SR):
   Draft -> Submitted -> Approved -> Completed, plus Rejected and Cancelled.
   - Draft: applicant edits freely; Delete draft = countdown, no confirm dialog.
   - Submit checks the required fields; refuses with one message per missing field.
   - Submitted: **locked** for the applicant. An approver Approves or Rejects (reason required).
   - Rejected: shows the reason banner; applicant presses **Revise** to edit and resubmit.
   - Approved: **locked** (nobody edits the lines). Purchasing works the lines (rule 6). **Revise** (applicant or
     approver, reason required) returns it to Draft with `revision_no + 1`, needs approval again, and keeps the
     approved version in the revision history. Revise is refused once any line has been opened.
   - Cancelled: applicant, from Draft / Submitted / Approved while no line is opened.
6. **Purchasing opens the code** (permission `open_code`). Per line, on an Approved application:
   - **Link product**: a product search (same as SR: ignores dashes, spaces and case; only LISTS matches; a person
     picks one; nothing preselected, no nearest-code guess). Picking stores `opened_product_id` on the line, plus who
     and when. If the code has not arrived from AutoCount yet, the search says "<typed> is not in the product list yet.
     It appears after AutoCount sends it." and the line simply stays open: never a stuck UI.
   - **Not opening** (reason required) closes the line without a product.
   - Undo either while the application is not Completed.
   When every line is linked or closed, the application moves to **Completed** by itself.
7. **Links (explicit only).** Line -> product (`opened_product_id`), application -> stock inquiry
   (`stock_inquiry_id`). The stock inquiry detail page shows a **New product applications** row listing each
   application number, status and opened codes (read by query, no new column on `stock_inquiries`).
8. **Notifications, only when someone has to act** (in-app + email, the existing notification service):
   - Submitted -> holders of `approve` ("NPA-000001 waits for your approval").
   - Approved -> holders of `open_code` ("NPA-000001: 2 new codes to open").
   - Rejected -> applicant (to revise). Completed -> applicant (codes ready to quote).
   Nothing on draft saves, cancels, revisions or single line links.
9. **Permissions** `procurement.new_product_applications.view | create | approve | open_code`, all four to the admin
   roles; `create` also to holders of `procurement.stock_inquiries.edit`. Approver sees all; applicant sees own.
10. **Company scoped** (`CompanyScopedMixin`, fail closed). The linked product must be in the same company.
11. **Print**: none in this lane (no paper form exists for this). Download PDF can follow if asked.

## Examples (shape; real rows come from the pending `crew-data-request` and stay out of the repo)

| # | Input | Result |
|---|---|---|
| A | Inquiry SI26-xxxx, `responded`, product code `XY-1234-MB` (not in products), "matt black basin mixer", qty 40, project text "Residensi Example, Tower B". Project sales presses Create from the inquiry | New form: inquiry link set, project text copied, customer typed text shown as "No debtor code" until a debtor is picked, line 1 = proposed `XY-1234-MB`, description, expected qty 40. Item group / UOM empty |
| A2 | Submit A without Item group | Refused: "Line 1: choose an item group". Draft kept |
| A3 | Item group Basin Mixer, UOM UNIT, supplier picked, cost 185.00, submit; manager approves | Approved, locked. Purchasing gets "NPA-000001: 1 new code to open" |
| A4 | Purchasing opens `XY1234MB` in AutoCount; ESB sends it; purchasing types "xy1234" in Link product | List shows `XY1234MB`; purchasing picks it. Application Completed; applicant notified; inquiry page shows NPA-000001 -> `XY1234MB` |
| B | Same as A but searched before the ESB push | "xy1234 is not in the product list yet ..."; line stays open; nothing breaks |
| C | Application with 3 colour lines (CP / MB / BG); supplier discontinues BG | Purchasing links CP and MB, marks BG Not opening "supplier dropped BG". Completed |
| D | Approved application, sales notices wrong UOM | Revise "UOM is SET not UNIT": back to Draft R1, fix, submit, approve again. History keeps R0 |
| E | Expected qty typed 40 then cleared | Saved as empty, no 0 forced |

## Edge cases

- Inquiry later voided or reopened: the link stays; the inquiry row shows its own status. No cascade.
- Same inquiry gets a second application (first was rejected or cancelled): allowed; the inquiry lists both.
- Linked product later deactivated: link kept, shown with an "Inactive" badge.
- Product picked by mistake: Undo on the line (before Completed). After Completed: Revise is refused (rule 5); an
  admin with `open_code` can still unlink a line, which reopens the application to Approved.
- Two purchasers link the same line at once: second write gets 409 "already linked"; screen refreshes.
- Company switch: applications and products of another company never show.

## Questions (each with a recommendation)

- **Q1 Who fills it in.** (a) staff only in this lane (project sales staff start it from the responded inquiry;
  a portal kind for contacts follows as its own lane) / (b) portal kind for salesmen now too.
  **Rec (a)**: the fields are master-data decisions (item group, UOM, supplier) the inquiry's submitter rarely knows,
  and (b) doubles the form surface.
- **Q2 Approval and Revise.** The PR has no staff lock and no staff Revise today; its approver comes from the
  form-SLA stage. (a) one approval step by holders of `approve`, lock on Submitted/Approved, Revise as rule 5 reusing
  the PR revision mechanics (`portal_form_revisions` snapshot + `revision_no`) / (b) route approval through the PR's
  form-SLA stage and default-approver setting.
  **Rec (a)**: one permission is the simplest working route; SLA stages, team tiers and trackers exist for forms
  with a clock, which this form does not have yet.
- **Q3 Inquiry link required?** (a) optional (the Create button on a responded inquiry is the main entry; New on the
  list still works) / (b) required, only from a responded inquiry.
  **Rec (a)**: a new code can also come from a supplier offer with no inquiry, and (b) leaves those stuck.
- **Q4 Lines.** (a) many lines per application, each opened and linked on its own / (b) one product per
  application like the stock inquiry. **Rec (a)**: a new series usually comes in finishes (CP / MB / BG), and the
  owner asked for the product line table.
- **Q5 Who must fill what before submit.** (a) Description, Item group and Base UOM required, the rest optional /
  (b) also Supplier and Cost price required. **Rec (a)**: the salesman often has no supplier quote yet; purchasing
  supplies those in AutoCount, and the linked product then carries the real values.
