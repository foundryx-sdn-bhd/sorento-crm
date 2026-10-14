# PLAN: consignment goods are delivered at issue (CONSIGN-DELIVERED-FIX)

Status: in progress (small fix track, URGENT prod data fix)

## Problem

ss#125 projected about 786 AutoCount CGs as consignment sales_orders with
qty_delivered = ConsignmentDTL.TransferedQty. In AutoCount, TransferedQty on a CG line is the
qty already RETURNED (Consignment Return). The goods left the warehouse when the CG was issued
(lines sit at Location CON). So 696 CGs read as open demand (board, Stock Debt, outstanding
report, reorder run).

## Fix

1. A consignment line is delivered in full at issue: qty_delivered = Qty. TransferedQty is the
   returned qty (qty_returned stays the return-doc matcher's column).
2. A consignment sales_order is closed for demand in every open-demand reader.
3. Consignment page: Qty ordered / delivered / returned, plus "At customer" = qty - returned;
   Status reads "At customer" / "Fully returned".
4. Re-project the stored CGs from autocount_cancel_docs (kind consignment) idempotently.
5. Nothing else reads TransferedQty-as-delivered.
