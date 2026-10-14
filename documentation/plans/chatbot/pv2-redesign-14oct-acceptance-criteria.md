# UAC: PV2 redesign (14 Oct 2026)

Plan: `PLAN-pv2-redesign-14oct.md`. Each round adds its section before its red tests.

## R1 Answer register writer
R1-1 After a PV2 turn whose v1 lane printed a delivery-order list, the saved PV2 state's register holds one entry per
     printed order (kind delivery_order, label = the printed order number) in printed order, ids R1..Rn.
R1-2 Each order entry has child entries for its product lines (kind product, label = printed product code,
     parent = the order's R id), from the MCP item's unprinted `lines` key, never by parsing the reply text.
R1-3 A PO placed list writes purchase_order entries with their product as a child entry.
R1-4 Stock, incoming, matching-product, attachment, set-page and top-selling lists write product (or their
     entity_type) entries, one per printed row, in printed order.
R1-5 A sales-order card or SO list writes sales_order entries (label = SO number) from so_status's structured rows;
     PO/PR ref cards likewise.
R1-6 Report rows (report_ask drill rows; delivered by-customer/by-product breakdown) write entries of the group_by kind.
R1-7 A field removed by the reveal filter, and an item's unprinted title, are never recorded; keys (codes, uuids,
     numbers) are stored but no entry label shows a uuid.
R1-7b Only rows the reply printed are recorded: rows kept in `answers` but hidden from the text (summary mode,
     needs-quantity stock mode, duplicates skipped on a status-only ask) are not recorded.
R1-8 The register keeps at most 20 entries and only the last 2 replies; a PV2 direct reply (no lanes) adds nothing and
     keeps the previous reply's entries; a new session clears it.
R1-9 Stored state from before R1 (no register field) loads unchanged.
R1-10 MEMORY is byte-identical to today for the same state (the register is not rendered in R1).
R1-11 A non-staff contact asking for a product's PO list is not refused for lacking purchase_orders.placed (owner
      ruling 14 Oct); the answer matches v1's for the same contact. Staff behaviour is unchanged.
R1-12 v1 turns (parser mode v1) are unaffected: no register write, same replies.
