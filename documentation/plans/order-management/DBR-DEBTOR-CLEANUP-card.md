# DBR-DEBTOR-CLEANUP owner card (7 Oct 2026)

Source: prod snapshot `sorento_ai_automation_20261006` (read-only) + origin/main 65b235435.

## Rule after this lane

A DO/order whose debtor code is blank keeps its debtor NAME as text and has no customer
(customer_id NULL). No customer row is invented. Today `_upsert_customer_from_debtor`
(`sorento_crm_backend/app/services/order_service.py:2284-2287`) mints `DBR-<md5(name)>`.
`orders.customer_id` is already nullable (`app/models/order.py:413`; snapshot `is_nullable = YES`): no migration.

## Numbers (snapshot)

- 186 DBR customers (168 Sorento, 18 Mocha). 0 AutoCount links.
- 229 DOs point at them; all `source_book` NULL, all blank debtor code, and every one's
  `debtor_name` already equals the DBR customer name (229/229), so no text is lost.
- 150 chat links (`respond_contact_customers`; 81 bulk_setup, 69 manual) over 77 contacts.
  76 of the 77 contacts ALSO hold a link to a real AutoCount customer. 1 contact holds only a
  DBR link: DELUXE HOME CENTRE (KTN) SDN BHD (no customer group).
- 11 stock asks, all open, all 6 Oct, all from that one DELUXE (KTN) contact.
- Other 9 FK tables: 0 rows.
- DB FK `orders.customer_id` is NO ACTION (model says SET NULL; drift). Script NULLs the DOs first.

## Real examples

| DO | Debtor name | Type | Today | After |
|---|---|---|---|---|
| XFER-2026/09-756 | STOCK TRANSFER - BRW TO JB SHOWROOM | ST | DBR-302fd29fc4 | customer none, name kept |
| HQ/IV26/09/001 | BUIMACO SDN BHD (PROJECT) | SORENTO | DBR-b35bf4c812 | customer none (NOT linked to 300-B117 even though same name) |
| MKT-0912 | SAMPLE | SAMPLE | DBR-5e8ff9bf55 | customer none |
| MTB2026-0045 | TEMPERORY BILL | MOCHA | DBR-9003be804e | customer none |
| 26106 | MARKETING D/O | MOCHA | DBR-620627645d | customer none |

MKTPT-1001 is not in the 6 Oct snapshot (made after it; UNVERIFIED on prod). Placeholder
debtors by DO count: SAMPLE 44, DO DEALER 15, MOCHA SDN BHD (DOCUMENT MOCHA) 14, DO MARKETING 11,
TEMPERORY BILL 7, DO SORENTO 7, MARKETING D/O 6, DEALER D/O 6, STOCK TRANSFER - ... (18 ST DOs).

## What the owner sees

DO list and detail never read the customer: columns are debtor name/code
(`sorento_crm_frontend/app/(protected)/order-management/orders/components/OrdersList.tsx:187-189,232-234`),
detail shows `debtor_name || debtor_code` (`OrderDetail.tsx:80,142-146`). Group-by-customer
groups on debtor name (`api/v1/order_management/orders.py:633-642`). No screen change.

Changes are in customer FILTERS and two reports:
- Customer filter / dealer scope (`order_service.py:928-949`, same at 1559, 1699, 1869) has a
  legacy NAME fallback for NULL-customer rows. After cleanup that fallback would name-match:
  AutoCount customer 300-S011 is literally named "SAMPLE", so filtering by it would pull in all
  44 SAMPLE DOs; BUIMACO (PROJECT) 300-B117 would pull HQ/IV26/09/001. 56 DOs in all.
- Outstanding report DO side (`services/outstanding_report_service.py:503-509,538-541`) and the
  delivery-order-lines report Customer column (`services/reports/datasets/delivery_order_lines.py:80,172`)
  read the customer name only: a NULL row shows blank / lands in an unnamed bucket.
- Export quick search (`services/list_query_export_service.py:167-182`) searches customer name only.

## Chat links: what a contact loses

The link gives a WhatsApp contact (a) dealer scope: which DOs/sales the chatbot shows
(`services/contact_customer_scope.py:85-118`), (b) price tier `account_level` (`:107-113`),
(c) default customer (`contact_customer_service.py:49-61`). After delete: 76 contacts keep their
real AutoCount links, so price tier and scope over real DOs are unchanged; they lose sight of the
31 DOs on 25 DBR debtors (those DOs become customer-less, so no dealer sees them in chat).
The DELUXE (KTN) contact loses its only link: becomes unlinked (no scope, no tier).

Debtors with chat links (contacts): DELUXE HOME CENTRE (KTN) 7, DELUXE HOME CENTRE (TRIANG) 6,
FULLSHUN SANITARYWARE (SAMPLE) 6, DELUXE HOME CENTRE 6, DELUXE (KEPONG) 6, FULLSHUN 6,
DELUXE (STP) 6, FULLSHUN [A/C III] 6, SIN HON ENTERPRISE (Mocha) 5, DIGNIFIED SQUARE 5,
DIGNIFIED SQUARE [A/C IV] 5, GLORY MOTION 4, YOO LIVING HOUSE [A/C III] - PRICETAG 4,
TEE CERAMICS (SAMPLE BOARD) 4, TEE CERAMICS (SAMPLE) 4, GLORY MOTION (SORENTO PRICETAG) 4,
CHIN CHUN HOMEMART 3, DE BATHLABZ 3, YI HONG TILING 3, SK ONE STOP 3, DE BATHLABZ [A/C I] 3,
BUILDERS HARDWARE 3, ONG BROTHERS (SAMPLE) 3, and 30 more with 1-2 each (BATHPRO, CASA,
DILOOMA, J&Y, SANIMART, KBO, ONE SIEW, YIKSON, MASTILE KLANG, SCR, DE HARMONI, PERNIAGAAN W JAYA,
Super Ceramic Tiles, BATH IDEA, SIN HON ENTERPRICE).

Option (b) "re-point to AutoCount customer in same group" does not resolve to one row: each
group holds 3-7 AutoCount accounts ([A/C I]..[A/C IV], PROJECT, SHOWCASE), and the contacts are
already linked to them. 8 debtors (all DELUXE, YOO LIVING, CHIN CHUN, Super Ceramic) have no group.
