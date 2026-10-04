# CUSTOMER-BULK-OPS behaviour card (3 Oct 2026)

Facts from code (file:line) and the 25 Sep prod copy `sorento_ai_automation_0925` (read-only queries).

## 1. Pager vanishes after Edit > Save (bug)
- Cause: `app/(protected)/order-management/customers/[id]/edit/page.tsx:51` `router.push(/order-management/customers/${id})`
  drops the list query string. The pager rebuilds its list from URL params (`hooks/useListPager.ts:157-161`) and hides
  when the record is not on that page (`components/common/ListPager.tsx`, `visible: isLoading || onPage`).
  Same drop on the "Back to customer" link, `edit/page.tsx:29`.
- Fix: keep `?page&sort&query&status&customer_group_id...` on both, as products already do
  (`master-data-management/products/[id]/edit/page.tsx:20-22,63`).
- Rule: after Save the pager shows the same "N / M" as before Edit; next/prev step through the same filtered list.
- Same bug exists on suppliers edit (`procurement-management/suppliers/[id]/edit/page.tsx:39`).

## 2. Customers list: bulk "Set sales agent"
- Reuses the bulk strip in `components/ui/data-grid-list-toolbar.tsx:37-38,203` (`bulkActions`), as
  `user-management/contacts/components/ContactsList.tsx:445` does. Checkbox column already on
  `CustomersList.tsx:22`.
- Select rows > "Set sales agent (n)" > SearchableSelect of active agents (`hooks/useCustomerSalesAgentOptions.ts`,
  label `CODE - person`) > Apply. Calls the existing `POST /sales-agents/{id}/customers`
  (`api/v1/master_data/sales_agents.py:181`, all-or-nothing, perm `master_data.sales_agents.edit`,
  per-customer company check in `order_service.py:3765`). No new endpoint.
- Toast "Sales agent set on n customers"; selection clears; list refreshes.

## 3. Contact detail > Customers card: bulk Unlink
- Card: `user-management/contacts/[id]/components/ContactCustomersSection.tsx`; row Unlink is a 5 s deferred
  action `contact_customer_link.unlink` (`:34-42`, `services/record_actions.py:1291-1300`).
- Add a checkbox per row + "Unlink (n)" in the card header. One countdown "Unlinking n customers in 5s" with one
  Cancel, one pending action per link, via `hooks/useDeferredBulkAction.tsx` (already used by
  `orders/components/OrdersList.tsx`). No new route, no confirm dialog (D7).

## 4. The owner's CSV `dealer_contacts_approved.csv`
- 143 rows, a Respond.io contact export. Columns: First Name, Last Name, Phone Number, Email, Tags, Lifecycle,
  Assignee, enquiry_type, user_type, complaint_id, is_allowed_stock, is_human_intervened, backend_id,
  is_allowed_ai, is_allowed_voice. Only First Name, Phone Number, Tags are filled (all 143); every other column empty.
- Tags = comma list of: dealer NAME, company (`Sorento Sdn Bhd` 121, `Mocha Sdn Bhd` 56), sales agent FIRST NAME
  (CONTACT Z 26, William 18, Long 17, Jayden 14, Kent 13, CONTACT AG 12, Chong 10, Samantha 9, ...), role
  (Business Owner 92, Employee 51). ~75 distinct dealer names; some rows carry 2 agents.
- Samples (phones masked):
  - `Wong | +6016*****982 | YOO LIVING HOUSE, Sorento Sdn Bhd, Chong, Business Owner, Mocha Sdn Bhd, Samantha`
  - `ELEEN TAY | +6016*****885 | SKY PURE Water Solutions, Sorento Sdn Bhd, Chong, Business Owner`
  - `Lokyi | +6016*****980 | EUROTAN ENTERPRISE SDN BHD, Sorento Sdn Bhd, CONTACT Z, Business Owner`
- NO customer code and NO agent code anywhere. And names are not unique:
  - `EUROTAN ENTERPRISE SDN BHD` = 300-E002, E029, E031, E043, E046 (Sorento) + E025, E026, E027 (Mocha, A/C I / CERAMIC / IBORN).
  - Tag `Chong` = agents `CHONG - I`, `CHONG - II`, `CHONG - III`, `CHONGTH I/III/IV`; `Kent` = `KENT - I..III`, `CONTACT AB I/III/IV`.
  - Spelling variants: `MAHLIM CERAMICS SDN. BHD.` vs `MAHLIM CERAMICS SDN BHD`; `DELUXE HOME CENTRE` vs `... SDN BHD`.
- Contacts: 0 of the 143 phones are in the prod copy (100 contacts there). UNVERIFIED for live prod.
  CRM can create a contact (`POST /contacts/`, `api/v1/user_management/contacts.py:214`).
- Prod copy: 7170 customers, 0 with a sales agent, 0 contact-customer links.

### Proposed import (exact codes only, nothing guessed)
- Contacts list > Import > upload CSV with columns `phone, name, customer_codes, sales_agent_code`
  (`customer_codes` = `;` list, each `COMPANY:CODE`, e.g. `Sorento:300-E002;Mocha:300-E025`).
- Step 1 Preview (dry run, nothing written): per row OK / ERROR; counts "n contacts to create, n links, n agent sets".
- Step 2 Apply: creates missing contacts by phone (existing phone = reuse), links each customer, sets
  `customer.sales_agent_id` to the agent code. Only rows that are OK are applied.
- Error report (downloadable CSV, same rows + `error` column): `unknown customer code Sorento:300-X999`,
  `customer code matches 2 customers in Sorento` (code is unique only with name, `models/order.py:250`),
  `unknown sales agent code CHONG`, `inactive sales agent`, `bad phone`, `duplicate phone in file`,
  `customer already has agent KENT - I` (see Q4).
- Needs a preview screen = new screen, so a Lavish mock comes first.

## Questions (max 5, each with recommendation)
- Q1 CSV has dealer names + agent first names, no codes; names map to many codes (EUROTAN 8 codes, Chong 6 agents).
  (a) I generate a mapping sheet: one row per distinct dealer tag (~75) and agent tag (~14); owner types the
  exact codes once; the import joins CSV + sheet by the exact tag text (a lookup the owner filled, not a guess).
  (b) owner adds `customer_codes` + `sales_agent_code` to all 143 rows. (c) no import; use items 2 + 3 + manual link.
  Rec (a): 89 cells to fill instead of 143 rows, and no name matching by the system.
- Q2 Sales agent lives on the CUSTOMER (`models/order.py:174`), not the contact. A row's agent code sets the agent
  on every customer it links. Two rows linking the same customer with different agents = error for both. OK? Rec yes.
- Q3 Missing contact (phone not in CRM): (a) create it (phone + name) (b) error row. Rec (a): 0/143 exist in the prod copy.
- Q4 Customer already has a different agent: (a) preview shows "KENT - I -> CHONG - I" and Apply overwrites
  (b) error, untouched. Rec (a): preview is the safety; bulk Set sales agent (item 2) overwrites too.
- Q5 Fix the same pager drop on suppliers edit (`suppliers/[id]/edit/page.tsx:39`) in this lane? Rec yes, 2 lines.
