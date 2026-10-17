# Behaviour card: New Product Application (NEW-PRODUCT-APP)

Status: card filed 17 Oct 2026. Crew answers 17 Oct: Q2 (a) by the #1698 precedent (below), Q3 (a), Q4 (a), Q5 (a);
Q1 (staff only now) with the owner, staff side built meanwhile. Mock v2 follows these answers.
Size L, full track (migration + new permissions).
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
  snapshot row in `portal_form_revisions`, back to `submitted`, approval cleared). Q2 answer: follow #1698 instead, rule 5.
- **Freshest sibling to copy:** Sample Request (#1718): status-engine graph (not copied, rule 5)
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
5. **Statuses** (a plain `status` column with a CHECK, moves coded in the service, like the stock inquiry and the
   PR; the status engine lives in the projects module and nothing here needs editable labels):
   Draft -> Submitted -> Approved -> Completed, plus Rejected and Cancelled. Approval, lock and Revise copy the
   owner-approved quotation approval of #1698 (`PLAN-quote-approval-ux-15oct.md` Design BE 2-4), which is the
   precedent for "approval flows mirror PR":
   - **Approvers** = active, non-integration users holding `procurement.new_product_applications.approve` AND a
     grant for the application's company, the sender excluded (the `project_notify_service.approver_user_ids:70`
     query, with this form's slug). An approve-permission step, not the PR's form-SLA stage.
   - Draft: applicant edits freely; Delete draft = countdown, no confirm dialog.
   - Submit checks the required fields; refuses with one message per missing field, naming the line.
   - **Submitted and Approved are locked**: every header and line write answers 422
     `new_product_application_locked` (one `assert_unlocked` guard, like #1698's). Approve, reject, revise, line
     linking and cancel are not edits and are not locked.
   - Submitted: an approver presses Approve or Reject (reason required).
   - Rejected: the reason shows in the red banner; the applicant edits and presses the same **Submit** again (one
     press, Rejected -> Draft -> Submitted). No "Back to draft" button.
   - **Revise** (applicant or approver) on Submitted or Approved: asks "What changed, and why?" (required), returns
     the application to Draft and writes one audit row with the reason. From Submitted it tells the approvers the
     request was withdrawn. No snapshot copy and no revision number (#1698 keeps none; the audit trail holds the old
     values). Revise is refused once any line has been opened.
   - Cancelled: applicant, from Draft / Submitted / Approved while no line is opened.
   - One primary button = the viewer's next action (Submit; Approve + Reject for an approver; Revise).
6. **Purchasing opens the code** (permission `open_code`). Per line, on an Approved application:
   - **Link product**: a product search (same as SR: ignores dashes, spaces and case; only LISTS matches; a person
     picks one; nothing preselected, no nearest-code guess). Picking stores `opened_product_id` on the line, plus who
     and when. If the code has not arrived from AutoCount yet, the search says "<typed> is not in the product list yet.
     It appears after AutoCount sends it." and the line simply stays open: never a stuck UI.
   - **Not opening** (reason required) closes the line without a product.
   - Undo either (holder of `open_code`); undoing a line of a Completed application returns it to Approved.
   When every line is linked or closed, the application moves to **Completed** by itself.
7. **Links (explicit only).** Line -> product (`opened_product_id`), application -> stock inquiry
   (`stock_inquiry_id`). The stock inquiry detail page shows a **New product applications** row listing each
   application number, status and opened codes (read by query, no new column on `stock_inquiries`).
8. **Notifications, only when someone has to act** (in-app + email, the existing notification service):
   - Submitted -> the approvers of rule 5 ("NPA-000001 waits for your approval").
   - Revised from Submitted -> the same approvers ("NPA-000001 was withdrawn"), so nobody approves a dead request.
   - Approved -> active holders of `open_code` in the company ("NPA-000001: 2 new codes to open").
   - Rejected -> applicant (to fix and resubmit). Completed -> applicant (codes ready to quote).
   Sent after the status commits, best effort, never undoing it (#1698). Nothing on draft saves, cancels, revisions
   from Approved or single line links.
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
| D | Approved application, sales notices wrong UOM | Revise, reason "UOM is SET not UNIT": back to Draft, fix, Submit, approve again. The audit trail shows the reason and the old UOM |
| D2 | Rejected "supplier price too high, find another" | Applicant changes supplier and cost, presses Submit: Submitted again, approvers notified again |
| E | Expected qty typed 40 then cleared | Saved as empty, no 0 forced |

## Edge cases

- Inquiry later voided or reopened: the link stays; the inquiry row shows its own status. No cascade.
- Same inquiry gets a second application (first was rejected or cancelled): allowed; the inquiry lists both.
- Linked product later deactivated: link kept, shown with an "Inactive" badge.
- Product picked by mistake: Undo on the line, also after Completed (it reopens the application to Approved).
  Revise stays refused once a line is opened (rule 5).
- Two purchasers link the same line at once: second write gets 409 "already linked"; screen refreshes.
- Company switch: applications and products of another company never show.

## Questions

Answered by crew 17 Oct: Q2 (a) (the #1698 approve-permission step, rule 5), Q3 (a) inquiry link optional,
Q4 (a) many lines, each linked on its own, Q5 (a) description, item group, base UOM required before submit.
Open with the owner:

- **Q1 Who fills it in.** (a) staff only in this lane (project sales staff start it from the responded inquiry;
  a portal kind for contacts follows as its own lane) / (b) portal kind for salesmen now too.
  **Rec (a)**: the fields are master-data decisions (item group, UOM, supplier) the inquiry's submitter rarely knows,
  and (b) doubles the form surface. The staff side is built meanwhile.
