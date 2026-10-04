# Behaviour card: Contact detail page ignores the company switcher (CONTACT-COMPANYLESS)

Status: card filed for owner approval (3 Oct 2026). Size M.

## Rule

On `/user-management/contacts/<id>`, every picker, filter and linked list shows the options of
**every company the signed-in user is granted** (superadmin/admin = all companies), whatever the
header switcher says. Company-less never widens past the user's grants: the widened scope is
`resolve_user_grant_ids(user)` (`sorento_crm_backend/app/services/company_scope_resolver.py:114`),
the same set the switcher itself offers. Saving stays company-agnostic (see "Save" column).

How scoping works today: the switcher sets the request's ORM scope to `{active company}`
(`company_scope_resolver.py:88-128`); every `CompanyScopedMixin` model is auto-filtered
(`app/services/company_scope.py:263`). Contacts themselves are not scoped
(`RespondContact`, `app/models/access.py:230`).

## Inventory (only rows the switcher affects today; FE paths under `contacts/[id]/`)

| Element | FE file:line | Scoped today by | Proposed |
|---|---|---|---|
| Locations picker (Include/Exclude) | `components/stock-visibility/StockVisibilitySection.tsx:362,394` | options = GET `/inventory/warehouses` (`api/v1/inventory/warehouses.py:28`), `Warehouse` scoped (`models/inventory.py:26`). Chips + save already unscoped (`services/stock_visibility.py:82-95,328`) | options widened to grants; option + chip label carries company |
| Dealer pool button | `StockVisibilitySection.tsx:404` | same endpoint `?segment=dealer`, active company only | dealer warehouses of all granted companies |
| All locations button | `StockVisibilitySection.tsx:413` | saves `warehouse_ids = null`; runtime = contact's own companies (`stock_visibility.py:364-416`) | unchanged |
| Add customers picker | `components/ContactCustomersSection.tsx:106` | GET `/order-management/customers/select` (`customers_select.py:19`), `Customer` scoped (`models/order.py:76`); POST re-reads customer under scope, 404 if hidden (`contact_customer_service.py:210`) | options + POST widened to grants |
| Linked customers list + Unlink | `ContactCustomersSection.tsx:144-190` | GET `/contacts/{id}/customers` (`contact_customer_service.py:227,325`), link table scoped (`models/access.py:11`); unlink runs under requester's stored scope (`form_action_service.py:73-107`) | list + unlink widened to grants |
| Chatbot facts: usual products / brands / sites | `components/ContactChatbotSection.tsx:292,303` | `/master-data/products/select`, `/master-data/brands/`, `/inventory/warehouses/` all scoped; save validates value against scoped Brand/Warehouse names (`services/profile_facts.py:423-428`) | options + validation widened to grants |
| Contact facts derived from primary customer | `profile_facts.py:247` | joins scoped Customer | widened to grants |

Already company-less, no change (verified): spec keys (`ProductSpecRegistry` plain,
`models/product_spec.py:32`), access types, market segments, attachment types, portal forms,
media access, field reveals, access agents + field access, Respond workspace, user account,
chatbot memory/tier, companies badge/picker (superadmin only). CS routing candidates ignore the
switcher too but are hard-pinned to the Sorento CS team (`services/cs_routing_service.py:70-88`), see Q4.

Save: stock visibility policy has no company column and stores a cross-company id list
(`stock_visibility_policies.warehouse_ids uuid[]`); a customer link is stamped from the
**customer's own** company, not the switcher (`contact_customer_service.py:86-107`). Both stay as is.

## Real examples (shared dev DB `sorento_cagent_stack`, companies SRT Sorento, MCH Mocha)

1. Contact A (member SRT+MCH) include-list = `MCH:MOCHA-WH, SRT:BRW, SRT:MWH`. Switcher on Sorento: chips show all 3 but the picker offers only the 56 active SRT locations; MOCHA-WH cannot be re-added if removed. After: picker offers 72 (56 SRT + 16 MCH active).
2. Contact B (member SRT only) include-list holds `MCH:MOCHA-WH` (chips from another company). After: still shown, labelled `Mocha · MOCHA-WH`.
3. Contact C exclude-list = `MCH:RESERVED, SRT:DC1, SRT:RESERVE`; with Mocha active, Dealer pool today adds only Mocha dealer locations. After: both companies'.
4. Customer `CAPRIFORM BUILDERS SDN BHD` exists as `SRT:301-C069` and `MCH:301-C002`; today only one of them is findable in Add customers, depending on the switcher. After: both, distinguished by company + code.
5. Contact D has 44 SRT customers linked; with Mocha active, the linked list shows 0 today. After: 44.

## Edge cases

- **Same location code in two companies**: `MWH-RSV`, `REPAIR`, `REWORK` exist in both SRT and MCH (`uq_warehouses_company_warehouse_code`). Label must carry the company or the two are indistinguishable. Ids are distinct, so saving is unambiguous.
- **Same customer name in two companies**: e.g. `DELUXE HOME CENTRE SDN BHD (KEPONG)` = SRT `300-S060`, `300-D093`, MCH `300-D021`, `300-D058`; also codes repeat across companies (`300-A001` x3). Label = company + code + name.
- **RBAC**: 57 of the users on dev hold exactly one company grant. Widened scope = their grants only, so a Sorento-only user still sees only Sorento locations/customers and Sorento links in the list; a Mocha link on the same contact stays hidden and cannot be unlinked by them. Superadmin/admin = all. No new permission; module/permission guards unchanged.
- **Primary customer** is unique per (contact, company) (`models/access.py:55-62`): unchanged; a contact can still have one primary per company.
- **Single-grant user**: no company label needed (one company), see Q1.

## Questions (each with recommendation)

1. **Company tag on options and chips** (`Mocha · MOCHA-WH`, `Sorento · 301-C069 CAPRIFORM ...`): (a) always on these pickers, (b) only when the user is granted more than one company. **Recommend (b)**: mirrors the existing rule "label per company only when the lookup spans more than one" (`company_scope.py:343-357`); single-company users see no change.
2. **Saving stays company-agnostic**: (a) yes, keep as today (policy is per contact, link takes the customer's company), (b) stamp the switcher's company. **Recommend (a)**: switcher must not change what is saved on a company-less record.
3. **Dealer pool**: (a) all granted companies' dealer locations, (b) active company only. **Recommend (a)**: same rule as the rest of the page.
4. **CS routing candidates** (always the Sorento CS team today, `cs_routing_service.py:73-88`): (a) leave as is, (b) add the Mocha CS team. **Recommend (a)**: it already ignores the switcher; adding Mocha's team is a separate routing call, not a filter fix.
5. **Chatbot usual products / brands / sites pickers**: (a) widen too, (b) leave scoped. **Recommend (a)**: same page, same rule; values are stored on the company-less contact.
