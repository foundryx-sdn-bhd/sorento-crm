# PLAN: Sponsorship forms keep a sales type, like purchase requests (SF-SALES-TYPE)

Status: in progress (Build). Track: small fix (S): no migration, no auth/RBAC change, no new ingest surface.
UAC: `sf-sales-type-acceptance-criteria.md` (alongside).

## Journey

Owner, 16 Oct 2026: a Sponsorship Form (SF) should store a Sales type auto-filled from the picked
customer's debtor type, exactly like a Purchase Request (PR). Owner: "yeah i want it".

## Measured facts (origin/main 05f030e15)

- PR and SF share `purchase_requests`; `sales_type` column exists (`app/models/procurement.py:1127`,
  comment "SF leaves NULL"). SF links a customer through `customer_id` (`:1117`).
- The mapping lives in ONE place: `SALES_TYPE_FOR_DEBTOR_TYPE` (`app/services/debtor_type.py:15`),
  served on every customer-picker row as `sales_type` (`lookup_customers`, `:52`).
  Project -> project; Project cash -> cash_sales; Cash sales -> cash_sales.
- The backend already saves `sales_type` for any request type: staff create dumps the schema
  (`procurement_service.py:9325`), staff update setattrs the payload (`:9426`), the portal write
  allowlist is shared by PR and SF (`portal_service.py:1677`). Only the FRONTEND drops it for SF:
  - staff pick fill gated `!isSponsorship` (`RequestCustomerField.tsx:38`),
  - staff create/edit payload clears it for SF (`PurchaseRequestForm.tsx:485`),
  - field hidden for SF (`PurchaseRequestForm.tsx:729`, `PurchaseRequestDocumentEditCard.tsx:478`),
  - portal pick fill PR only (`SubmissionForm.tsx:1452`), no SF field (`SubmissionForm.tsx:318`).
- Display PR only: list column (`PurchaseRequestsList.tsx:361`), detail (`PurchaseRequestDetail.tsx:1271`),
  Excel (`purchase-request-excel-export.ts:250-262`), PDF (`purchase_request_pdf_service.py:376-396`),
  SF revision snapshot fields (`portal_revision_service.py:290-305`).
- Required: PR only (`procurement_service.py:8201`, FE `purchase-request-schema.ts:94`). SF stays optional.
- CS routing for SF reads the customer's CURRENT debtor type (`form_sla_service.py:1125-1146`): unchanged.
  Routing rows also see the stored `sales_type` column (`row_to_json`, `form_sla_service.py:1117`), and the
  SF predicate builder offers `sales_type` (lookup binding on `purchase_requests`, `cs_routing_service.py:187`).
  Dev DB (`sorento_cagent_stack`, read-only): 15 SF routing rules, 0 with a `sales_type` predicate, so no SF
  routing outcome changes there. Prod check is an owner-run read-only query (below).
- Dev data: 86 SFs, 2 customer-linked (PSSF26-0393/0394, customer 300-1002, debtor type project), 0 with a
  sales type.

## Build (reuse the PR path; no new components)

1. Staff form + edit card: show the same Sales Type field for SF (not required, no asterisk), placed after
   Sponsor Subject (the SF's "purpose" slot, same as the list column's position); picking a customer fills
   it on SF too; payload sends it for SF.
2. Portal: SF gets the same `sales_type` lookup-select (not required) after Sponsor Subject; pick fills it.
3. Display for SF: list column (drop the PR-only gate), detail (after Sponsor Subject), Excel
   (`Sales Type:` row after `Sponsor Subject:`), PDF (same row, same position), SF revision snapshot
   form fields + lookup label.
4. Backend: no write-path change needed (verified above); adapter + PDF only. Comments that say "SF leaves
   NULL" updated.
5. `scripts/backfill_sf_sales_type.py`: dry-run by default, `--apply` writes. Fills SFs with a `customer_id`
   and NULL `sales_type` from the customer's CURRENT debtor type via `SALES_TYPE_FOR_DEBTOR_TYPE`; reports
   counts (candidates, fill per value, skipped because the customer has no debtor type). Unlinked SFs and
   PRs untouched. NOT run by the PR; owner runs it on prod.
6. Owner-run read-only prod check: SF routing rules with a `sales_type` predicate (would start matching once
   SFs carry a value):
   `SELECT count(*) FROM respond_contact_cs_routing WHERE use_case='sponsorship_form' AND match_conditions::text LIKE '%sales_type%';`

## Tests (red first)

- BE `tests/test_sf_sales_type.py`: SF revision adapter fields + lookup; PDF SF block prints Sales Type
  after Sponsor Subject; backfill dry-run writes nothing + reports counts, apply fills only linked NULL SFs.
  Pins (expected green before the fix): staff create + portal draft save keep an SF's sales_type.
- FE vitest: flip `SubmissionForm.customerLink.test.tsx` (SF pick fills), `PurchaseRequestDocumentEditCard.salesType.test.tsx`
  (SF edit shows field, pick fills, saves), staff create form SF sends sales_type, Excel SF row.
