# CARD - Packing list region (West / East Malaysia)

Lane REGION-PACKING-LIST, size L. Status: ANSWERED 2 Oct 2026. Q1 = (b) contact region grant lives in the ACCESS-MODEL access model (contact
side waits for that lane); Q2 (a); Q3 (a); Q4 (a) workbook fine as is; Q5 (a). Mock v2 approved, default West
Malaysia only. Rule 4 below (contact column + Chatbot settings card) is superseded by Q1 (b): see the PLAN.
Re-upload ruling (2 Oct): a second packing list uploaded onto an existing container takes the regions the user
picked in the upload dialog.

## Rules

1. Every packing list (one `inbound_shipments` row, `models/procurement.py:128`) carries one or more
   regions: West Malaysia, East Malaysia, or both. New rows default to West only; at least one stays set.
2. Upload: the Packing Lists page Upload button opens the shared `AttachmentUploadDialog` locked to the
   Packing List type (`PackingListsList.tsx:502-511`). It gains a Regions multi-select, West preselected.
   n8n then creates the shipment through `POST /external/packing-lists/` with the `attachment_id`
   (`api/v1/external/packing_lists.py:112`); that route copies the regions off the attachment, so n8n
   needs no change.
3. The regions are also set and editable on Create Packing List and on the packing list detail page
   (`PackingListForm.tsx`, `PackingListDetailsTab.tsx`). A draft shipment converted from a proforma
   (`proforma_invoice_service.py:2167`) starts West and is edited on its detail page.
4. Every chatbot contact (`respond_contacts`, `models/access.py:231`) holds one or more regions, default
   West. Set on the contact's Chatbot settings card (`ContactChatbotSection.tsx`) beside "Packing list
   allowed".
5. Visibility: a packing list is visible to a contact when ANY of its regions is one the contact sees.
   A contact that holds East sees East and West (so every packing list). A West-only contact sees a
   packing list tagged West or West + East, and never one tagged East only: not its lines, ETA, file,
   or "has incoming" stamp.
6. One filter, applied in SQL next to the existing draft filter `_not_draft_shipment_filter()`
   (`services/incoming_stock_service.py:85`, used at `:204,448,570,625,790,931,1044`). That covers every
   chatbot path:
   - MCP `crm_incoming_stock_list` / `_by_product` / `_shipments` -> `/incoming-stock/*`
     (`api/v1/incoming_stock.py`, contact resolved once in `_Contact` `:45`);
   - picker / roster "has incoming" stamps, did-you-mean and cross-domain probes, which all call
     `crm_incoming_stock_list` with `contact_id` (`resolve_gate.py:1489`, `miss_suggest.py:1393`,
     `answer.py:759`);
   - stock-ask ETA + packing list file (`inventory_service.py:1629` `earliest_packing_list_shipment`).
   Filtering in SQL keeps counts and paging right (a post-filter would break `total`).
7. The contact's regions are read in the same place as its ETA and packing-list switches
   (`eta_policy.rules_for_contact`, `eta_policy.py:63`).
8. Staff screens and the bare API key (no contact in play) see every packing list. The staff list gains a
   Regions column + filter (matches when any region matches).

## Real examples (dev DB)

| Packing list | Container | Consignee | ETA | Lines | Regions after backfill |
|---|---|---|---|---|---|
| PL-2609-033 | DFSU7408507 | Sorento (Mocha) | 27 Sep 2026 | 50 | West |
| PL-2609-038 | FSCU8706420 | Sorento | 30 Sep 2026 | 4 | West |
| PL-2609-036 | TRHU6072964 | Sorento | 23 Sep 2026 | 9 | West |

- If PL-2609-038 were uploaded as East only: a West-only contact asking "incoming FSCU8706420" or the product
  on it gets "no incoming" for that container; a contact holding East (for example one linked to customer
  SK HARDWARE (KUCHING) SDN BHD or LONGHOUSE DEVELOPMENT (MIRI) SDN BHD, both in dev `customers`) sees it
  and every West container.
- If PL-2609-039 (OOLU9610547, 30 Sep 2026) were uploaded as West + East: both contacts above see it.
- Contact Jayson (linked to HANLIM TRADING, 1 LIVING DEPOT, SCR MARKETING) backfilled West: sees West only.

Data facts: 275 packing lists (120 in transit, 155 fully received); `loc` is MWH 66 / BRW 49 / DC1 41 /
PSM 13 / blank 106, all West Malaysia warehouses (`warehouses.location`: Meru, Bukit Raja, Port Klang,
WH3). No packing list carries any East signal. 100 contacts; no contact is linked to an East customer.

## Edge cases

- Contact the chatbot cannot resolve: West only (fail closed, same as `UNRESOLVED` `eta_policy.py:60`).
- Contact with East only ticked: sees both (rule 5); the UI keeps at least one region ticked.
- Packing list with both regions unticked: refused (422); the UI keeps at least one ticked.
- A product with one West and one East container in transit: West-only contact's ETA is the West
  container's; "has incoming" only if the West one exists.
- Changing a packing list's regions applies on the next ask (no cache).
- `/incoming-stock/grn` (GRN history, no contact param) is not filtered: received stock, not incoming.

## Questions (each with recommendation)

**Q1. Where the contact's region lives (ACCESS-MODEL alignment).**
(a) Column `regions` on `respond_contacts` now, beside `packing_list_allowed`, read by `rules_for_contact`;
ACCESS-MODEL's `effective_access()` returns it as-is when it lands (a contact fact, like the customer link).
(b) A field key in the ACCESS-MODEL tree (`incoming_stock.region_east`), ticked on roles or per-contact
overrides; this lane waits for ACCESS-MODEL (its plan: "build after #1405 and #1429 merge").
Recommend (a): region is where the contact is, not what their role may see, so a role tick would need a
role per region pair; and (a) ships without waiting. ACCESS-MODEL's tree is unchanged.

**Q2. Backfill.** (a) All 275 existing packing lists = West only, all 100 contacts = West only.
(b) Leave existing NULL = visible to everyone. Recommend (a): every `loc` today is a West warehouse.
Staff then tick East on the East contacts by hand.

**Q3. Contacts who should hold East.** (a) Staff set them by hand after merge. (b) I derive them from
linked customers whose name or region says Sabah / Sarawak / Labuan towns. Recommend (a): `Customer.region`
is blank on all 6358 dev customers, and name matching is a guess.

**Q4. Container Status workbook file.** The bot can send the whole Container Status workbook as an
attachment (`tasks/import_tasks.py:3303`, `crm_resource_attachments_list`); it lists every container,
East included. (a) Out of scope; attachment-type grants keep governing who gets it. (b) Hide that file
from West-only contacts. Recommend (a) and name it as follow-up if East containers start appearing there.

**Q5. Where regions are picked.** (a) Upload dialog (West preselected) + Create form + editable on the
detail page. (b) Upload dialog only. Recommend (a): a wrong pick must be fixable without re-uploading.
