# PLAN: OI supplier always from the PO, not the SPO (OI-SUPPLIER-FROM-PO)

Status: in progress (small fix track, cloud lane)

## Owner rule (9 Oct 2026)

"wherever in the system, as far as order inquiry is concerned, be it system view, printouts, the supplier
always take from PO, not the SPO ... always take from the PO supplier"

## Scope

Every place an Order Inquiry shows or prints a supplier. Inventory of sites (file:line, source today) and the
fix follow below as the lane progresses.

## Root cause

The "via SPO" PO cell prints `SPOAllocation.from_po_number` (AutoCount text). Every OI supplier reader
resolved the PO only through `po_line_id` (never set on a book-fed SPO line) and otherwise fell back to
`SPOAllocation.supplier_id`, so a via-SPO row showed the SPO's supplier (the CHAOZHOU case).

## Fix

One SQL expression, `app/services/scm/spo_supply.py::supplier_id_for`: the supplier of the PO the SPO line
draws down (`po_line_id`), else of the PO its `from_po_number` names in the same company, else the SPO's own
`supplier_id` (only when the CRM holds no such PO; crew-ask on the PR, recommendation (a) keep the fallback).

## Inventory (BE paths under `sorento_crm_backend/app/services/`)

| # | Surface | Site | Source before | After |
|---|---------|------|---------------|-------|
| 1-8 | Worklist API `supplier`/`supplier_id`, filter, sort, facet, worklist + OI Lines tab + matrix drilldown (`SupplierCell`), Excel export (both export jobs) | `order_inquiry_worklist_service.py` `_base` Supplier join, `_SPO_LINKED_SUPPLIER_ID` | PO via link / SPO `po_line_id` / `spo_ref`, else SPO supplier | same PO legs, then `supplier_id_for` (adds `from_po_number`) |
| 9 | PO popover | `get_po_detail` | the PO's own supplier | unchanged (already the PO) |
| 10 | SPO popover header | `get_spo_detail` | SPO supplier | `supplier_id_for` |
| 11 | OI Related documents tab, SPO group | `order_inquiry_header_service.related_documents` | SPO supplier | `supplier_id_for` |
| 12 | Link dialog candidates + "Linked to X (supplier)" row note | `project_order_inquiry_service.py` SPO candidate queries | SPO supplier | `supplier_id_for` |
| 13 | Companion-rule host supplier (never displayed) | `_host_row_supplier_id` | PO via link, else product default | unchanged (not a display site) |

No chatbot, MCP, n8n, OI email or PDF surface shows an OI supplier. FE needs no change (every cell renders
the API value).
