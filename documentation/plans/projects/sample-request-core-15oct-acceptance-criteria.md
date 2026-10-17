# UAC: SR-CORE - Sample Request (consignment requisition) in project management

Status: updated 16 Oct for the form rebuild (owner OK on mock v3: no quotation, no alternative product, no immediate tick). Originally drafted from the settled card (`CARD-sample-request-core-15oct.md`), mock awaiting owner OK.
Each criterion names the test that proves it (BE = pytest, FE = vitest, BR = browser pass).

## Create and submit

- AC-SR-01 A salesperson creates a request from the project's Sample requests tab; project and its customer are
  prefilled; saving as draft gives a number `SR-000001` (next number per company). BE, BR
- AC-SR-02 Submit refuses without: customer (picked debtor or typed name), project (picked or typed title), delivery
  mode, ship-to name, at least one line or the board switch on. Each refusal names the missing field. BE, FE
- AC-SR-03 Submit refuses without a Request Delivery Date (a plain date; no "Immediate arrangement"). BE
- AC-SR-04 Loan period = whole number >= 1 + unit days|months; default 2 months. Return due is stored on Delivered
  as delivered date + period. BE
- AC-SR-05 Goods from defaults to Either; purpose optional. BE
- AC-SR-06 Board switch ON requires a label; the board never becomes a line. BE

## Lines (owner Q1: the product must be in the product list)

- AC-SR-10 Every line carries an existing active product picked by a person; the API returns 422 for a missing,
  unknown or inactive product_id, (a line is one product; there is no alternative product). BE
- AC-SR-11 Product search ignores dashes, spaces and case: "tpe9101" lists TPE-9101. The search only lists; no
  product is preselected or added without a click. BE (search), FE
- AC-SR-12 A search with no hits shows "<text> is not in the product list. Pick the product you mean, or ask
  purchasing to add it as a new product before you request it." and adds nothing; submit stays available. FE
- AC-SR-13 Qty is a whole number >= 1, default 1. BE

## Soft transition switches (rulings 3, 5)

- AC-SR-20 System Settings has "Sample requests need a registered project", OFF on deploy; it is returned by GET settings and saved by PUT. BE
- AC-SR-21 Project switch OFF: a typed title alone submits. ON: submit refuses without a registered project; a request
  saved before the switch still opens and prints unchanged. BE
- AC-SR-22 (removed 16 Oct: no quotation on a sample request.)

## Statuses and roles

- AC-SR-30 Flow: Draft -> Submitted -> Processing -> Customer confirmed -> Delivered; Cancelled from Draft, Submitted,
  Processing. Any other move is refused. BE
- AC-SR-31 The owner (salesperson) may submit and cancel own Draft/Submitted. Only `projects.sample_requests.process`
  may move to Processing, Customer confirmed (with date), Delivered (with date), cancel a Processing request (reason
  required), and edit Consignment No / PO No. A salesperson with `projects.projects.edit` but not process gets 403. BE
- AC-SR-32 After Submitted the owner cannot edit header or lines. BE
- AC-SR-33 (removed 16 Oct: no quotation, so Delivered creates no Samples row.)
- AC-SR-34 Each status move writes a project activity event when the request has a project. BE

## Portal (crew Q3: opt-in per contact)

- AC-SR-40 `sample_request` is grantable, not default: a contact without the grant gets 403 on list, new and submit;
  a granted contact sees the form on the portal landing. BE, FE
- AC-SR-41 A portal contact lists and opens only own requests (by contact); another contact's id gives 404 (no existence leak). BE
- AC-SR-42 A project picked in the portal must be one the contact's project lookup returns; anything else is 422. BE
- AC-SR-43 The portal shows the request read-only after submit with its status, and offers Cancel while Submitted. FE, BR

## Staff views

- AC-SR-50 Project detail has a "Sample requests" tab before "Samples", listing that project's requests. FE, BR
- AC-SR-51 Sidebar Project Sales > Sample Requests (needs `projects.sample_requests.view`) opens a cross-project queue,
  default filter Submitted + Processing + Customer confirmed, needed date ascending with Immediate first. BE, BR
- AC-SR-52 Request page shows every section with empty states; prev/next walks the queue; view and edit share the
  layout. BR
- AC-SR-53 Each line expands to the existing OrderInquiryStockGrid for that product (location BRW). FE, BR
- AC-SR-54 Site contact phone and ship-to address never appear in list responses. BE
- AC-SR-55 Usable at 375 and 1280. BR

## Print

- AC-SR-60 Print returns an A4 PDF: header fields, internal box, board as row 1 when on, lines with brand, model
  (with "OR alt"), qty, footer ticks for delivery mode, loan period, goods from, purpose. BE
- AC-SR-61 Print needs view on the request (owner, or `projects.sample_requests.view`). BE

## Migration

- AC-SR-70 One additive revision creates `projects.sample_requests` and `projects.sample_request_lines`, the two
  settings columns, and grants the new permissions; downgrade drops only those. Single alembic head. BE

## Form layout (owner OK on mock v3, built as mock v4, 16 Oct)

- AC-SR-80 Staff New / Edit and the portal New / Edit use the Purchase Request / Sponsorship Form document card: header
  fields in the paper order, the line table (one product search box per row, Qty, Remark, delete; row 1 = board), the
  paper footer fields, footer Cancel / Save as draft / Submit. No Quotation, no alternative column, no Immediate tick. FE, BR
- AC-SR-81 Customer Name is a searchable box listing debtors with their code; free text is accepted for a customer with
  no debtor code (staff: PR customer lookup; portal: the contact's own debtors). FE, BR
- AC-SR-82 Under staff impersonation the portal stays on `/portal`; New and an existing card open the sample request
  form (no 404). FE, BR
- AC-SR-83 The portal "Created" date reads dd/mm/yyyy in Malaysia time. FE
