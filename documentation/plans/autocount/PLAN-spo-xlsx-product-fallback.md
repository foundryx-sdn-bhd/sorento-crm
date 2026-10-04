# PLAN: SPO-XLSX-SUPERSEDE (round 2) - Excel rows with no destination superseded by AutoCount split lines

Status: REVIEWED 2026-10-01 (reviewer + security-reviewer findings fixed, awaiting CI + owner), standard track (expected diff over 300 lines incl. tests; no migration; touches
receipts, so reviewer + security-reviewer). UAC: `spo-xlsx-product-fallback-acceptance-criteria.md` (same folder).
Parent: `PLAN-spo-xlsx-supersede.md` (D25..D30), this adds D31..D36.

## 0. Why (owner case, prod, 1 Oct 2026)

PL GCXU6137164, SRTWCX8605-S-RL-PJ: shipped 99, "SPO allocated" 194, received 95; SPO-2026/08-0074 all Fully
Received; chatbot "Incoming Quantity 4, BRW-IB (22), BRW-NTC (77)". Same for SRTWC8605-SC-RL (96 vs 191).

Verified on `757b41da2`:

- The Excel upload wrote one aggregate row per `(product, upper(location))` (`services/scm/outstanding_import_service.py`
  `_spo_line_plans`), here location `HQ` and no `warehouse_id`: rows of 95 (on the PL) and 4 (no PL).
- The first AutoCount push supersedes only within `supersede_group_key` (`services/rules/shipping_order_rules.py:355`,
  product + warehouse, else product + location). `(p, loc:HQ)` never meets `(p, wh:BRW-IB)` / `(p, wh:BRW-NTC)`, so
  `plan_xlsx_supersede` (`:477`) files the Excel group under `KEPT_NO_COUNTERPART` (`:540`) and the push closes it
  (`shipping_order_ingest_service.py:557`) without retiring it; R2 (`services/scm/spo_supply.py:74`) keeps a row with a
  receipt visible.
- GR-2026/09-0049 picks 22 (BRW-IB) + 73 (BRW-NTC) against the Excel 95 row and 4 against the Excel 4 row.
  `build_allocation_pool` (`services/grn_spo_matching.py:96`) does not skip closed or retired rows and is FIFO by
  `created_at`, so the older Excel rows are drawn first whenever they still show capacity.
- PL "SPO allocated" sums every visible allocation on the shipment (`api/v1/procurement/packing_lists.py:273`):
  99 AutoCount + 95 Excel = 194. PL "Received" is approved picking lines linked to allocations on the shipment
  (`procurement_service.py:1040`): 95, because the 4 hangs off the PL-less Excel row.
- Chatbot: `remaining = shipped - stored received` (`incoming_stock_service.py:67`), stored by
  `refresh_shipment_line_statuses` (`procurement_service.py:1135`) = 99 - 95 = 4; warehouse allocations
  (`_warehouse_allocations_for`, `:233`) sum `allocated_quantity` and ignore receipts, so BRW-IB 22 + BRW-NTC 77.

## 1. Decisions

| id | decision |
| --- | --- |
| D31 | **Product-level fallback pairing.** In `plan_xlsx_supersede`, after the keyed pass, the Excel rows still `no_counterpart` that carry NO `warehouse_id` are pooled per product, together with that product's incoming lines no keyed group claimed. The pool is superseded as one group when `sum(rows.allocated_quantity) == sum(lines.allocated_quantity)` (exact: there is no destination evidence, so the quantities are the only proof the two sides describe the same goods). Otherwise the rows stay `no_counterpart` exactly as before. A row that names a warehouse is never fallback-paired. |
| D32 | **Receipt links split by capacity on a fallback group.** The keyed path moves every picking line to the group's first line (all its lines share one destination and D28a redistributes within it). A fallback group's lines sit in DIFFERENT `(product, location)` groups, so its picking lines are moved with the GRN import's own rule (`grn_spo_matching.draw_fifo`): same warehouse first (`source_warehouse_id`, else `destination_warehouse_id`), then any, each line up to its allocated quantity less what already picks against it, an overflow onto the last line; a picking line spanning two targets is split into two rows (expected follows the split, the shortfall stays on the last chunk). Claims and order-inquiry links still move to the first line. The receipt carry (`distribute_received`) is unchanged. |
| D33 | **A superseded row that is not deleted is retired** (no `.delete` grant, D30). Closed as today, plus `retired_at` stamped and `quantity_received` zeroed (its receipt now lives on the replacement lines; the trail log and the `superseded by <DocKey> (received N carried)` note keep the old figure). Without this the row stayed visible (R2) and kept inflating PL "SPO allocated". |
| D34 | **Future GRs never land on a retired allocation.** `build_allocation_pool` skips `retired_at IS NOT NULL`. With D31..D33 a superseded Excel row is gone or retired, so the pool offers only the AutoCount lines. |
| D35 | **No false incoming on a received PL line.** `get_received_quantities_by_product` = per product `max(approved-pick total, sum(stated_received) of visible allocations on the shipment)`, so AutoCount's own statement that the lines arrived is never undercut by a pick hanging off another row. The chatbot's per-warehouse list shows only what is still to come per allocation (`allocated - max(received, stated)`, closed / fully received lines contribute 0) and drops warehouses at 0; the unallocated-gap arithmetic keeps the full allocated total. |
| D33a | **Conservation guard** (crew ruling 1 Oct; security review S2). Before any superseded row is deleted or zeroed, `assert_supersede_conserved` proves the planned carry equals what the rows held, the replacement lines state at least that, and no GRN pick of ANY company still points at them; otherwise the record fails (ingest) or the document is rolled back, named and counted as aborted (repair script, which also runs the pure half in dry-run). The zeroed figure is frozen into `stated_received` on the retired row (N1). |
| D35a | **Stated floor is capped and trusts the ingest principal** (security review S1). Each allocation contributes `least(stated_received, allocated_quantity)`. Consequence for the owner to note: the ESB's TransferedQty statement now decides PL receipt state alongside approved picks, so an inflated push can mark a container received (it could already mark the SPO line received under D26). |
| D31a | **Fallback on a product's first push only** (review B1). A no-warehouse Excel group never joins the ESB keys, so on a re-push it would be offered every NEW line and pair on quantity alone, marking an unreceived line fully received. The ingest blocks the fallback for any product that already has a ref row on the `spo_number`; the repair script, which plans against the whole live line-set, is unaffected. A locked (D26a) group's lines are claimed before the fallback runs. |
| D32a | **AutoCount GRN picks move whole** (review). A pick with a `dtl_key` is never split (its `(header, dtl_key)` identity is unique and a re-push would fold the chunk back); it goes to its first draw's line. Split chunks spread `quantity_expected` and `qty_accepted` like a receipt (never negative, never above the chunk). Capacity ignores picks on rejected GRNs. |
| D34a | **Retired rows that still carry picks stay in the pool** (review B2): a GRN re-import must find the allocation its own picks already sit on, or it inserts a second copy of the receipt on a live sibling. Only a retired row nothing picks against is skipped. |
| D36 | **Repair = the dedupe script, extended.** `scripts/dedupe_spo_xlsx_superseded.py` gets D31/D32 through the shared planner and the shared split repoint, stays DRY-RUN by default, and its summary adds the scope count (`shipments (PLs) touched`). Production apply is owner-gated. |

## 2. Not touched

The keyed supersede (D25..D30), the D26a guard, adoption, the Excel importer, PL page UI, the
`refresh_shipment_line_statuses` allocated total (AC-H17).

## 3. Slices

One slice, one PR. Red tests first on a fixture of exactly the owner case, kill-tested; then the code; reviewer +
security-reviewer (receipts are rewritten by the repair).

## 4. Follow-up: follow AutoCount across warehouses (owner ruling, 1 Oct 2026, after #1411)

| id | decision |
| --- | --- |
| D37 | **The repair follows AutoCount across warehouses.** In `scripts/dedupe_spo_xlsx_superseded.py` and `scripts/oneoff/dedupe_spo_standalone.py` (`plan_xlsx_supersede(..., follow_autocount=True)` and its standalone twin), an Excel-era row with no keyed counterpart whose product AutoCount lists on the same SPO - at another warehouse (SPO-2026/08-0074 L1-6: Excel at BRW, AutoCount L34-39 at BRW-NTC) or with no warehouse - is pooled per product with that product's unclaimed AutoCount lines and superseded (receipt carried, picks split by capacity, claims and links to the first line, same conservation guard) whenever those lines can hold the receipt the rows carry (the D26a rule); otherwise kept as `received locked`. AutoCount's quantities win, so the D31 equal-quantities requirement no longer applies in the repair. The orphan rule is unchanged. The ingest push keeps D31 (`follow_autocount` off). |
| D37a | **Whole-product pool** (review of #1424). A product the keyed pass settles completely keeps that result; a product with ANY Excel row the keyed pass cannot settle (no line at its warehouse, no warehouse, a same-warehouse line too small for its receipt such as NTC 77 + IB 22 against 99 received at NTC, or a sibling group that took every line) is planned as ONE pool: all its Excel rows against all its AutoCount lines, picks split by capacity, superseded when the lines can hold the receipt, else `received locked`. |
