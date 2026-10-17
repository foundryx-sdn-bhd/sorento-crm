# PLAN: Available stock status, embedded in every process (STOCK-STATUS)

Status: Phase 3 passed (lane STOCK-STATUS, size L, standard track; owner OK of mock round 4 at 17 Oct 07:55; reviewer CLEAN after one fix round; security review S1 fixed). In hand test on the crew copy, then the owner's merge

## Owner request (17 Oct)

Purchasing checks AutoCount's "Available Stock Status" report daily (per item, per location: On hand,
SO, PO, Forecast, Stock Status, SPO, A/O; each location expands to its SO / PO / SPO documents).
Staff copy the stock code from the CRM into AutoCount: two screens. The owner wants the same view
inside the CRM's own processes (fulfilment planning, order inquiry, loading plan, sample request),
plus one stand-alone page. Purchasing's gaps: incoming packing list (PL) qty is not on the
AutoCount report, and the replenishment page has no per-project totals. AutoCount stays the
truth; the CRM only reads.

## 1. Discovery (origin/main at c2a3b162)

Backend paths are under `sorento_crm_backend/`, frontend paths under
`sorento_crm_frontend/app/(protected)/` unless stated.

### 1.1 Where each AutoCount column lives in the CRM

| AutoCount column | CRM source | Notes |
| --- | --- | --- |
| Item code, description | `products.product_code` / `description` (`app/models/product.py:234,236`) | |
| Item group | `products.category_id` -> `product_categories.category_code` (`app/models/product.py:241`) | No `item_group` field; category is the ingest's group |
| Item type | `products.item_type_id` -> `item_types` (`app/models/product.py:245`) | |
| Lead time | `product_suppliers.standard_lead_time_days` (`app/models/procurement.py:108`) | Per supplier, not per product |
| Is active | `products.is_active` (`app/models/product.py:300`) | |
| Location | `warehouses.warehouse_code`, `is_active` (`app/models/inventory.py:34,38`) | |
| UOM | not stored on `stock` | Push ingest accepts `uom_code` and drops it (`app/services/stock_balance_ingest_service.py:124-127`); use `products.base_uom_id` (`app/models/product.py:269`) |
| On hand | `stock.quantity_on_hand` (`app/models/inventory.py:129`) | Written by the AutoCount ESB push, roughly every 5 min (`stock_balance_ingest_service.py:319-320`); last push stamped on `companies.stock_push_confirmed_at` (`app/models/company.py:33-37`) |
| SO qty (outstanding) | `sales_order_lines`, `demand_qty()` + `is_open_demand()` (`app/services/scm/demand.py:168-181`) | `greatest(coalesce(qty_required, qty_ordered) - qty_delivered, 0)`, line open, header open, **and `purchasing_status <> 'covered'`** (`demand.py:180`). AutoCount has no "covered" concept, so the CRM figure can be lower |
| PO qty | `purchase_order_lines` open: `qty_ordered - qty_received`, `OPEN_PO_SQL` (`app/services/scm/container_request_service.py:115-119`) | Per location via `pol.warehouse_id` |
| SPO qty | `spo_allocations` open, `scm.on_order_v` (`alembic/versions/420_spo_docs_in_allocations.py:761-801`) | One row per SPO line, no header table (`app/models/procurement.py:458-661`) |
| Incoming PL | `inbound_shipment_lines`, `PL_UNALLOCATED_SQL` + `PL_NOT_ARRIVED_SQL` (`container_request_service.py:133-145`) | **Per product only.** PL lines carry no warehouse; only the part not yet on an SPO counts (the rest is already in SPO) |
| Forecast | not stored | CRM's equivalent today is `available = on hand - SO + SPO`, signed (`app/services/scm/location_stock_service.py:136-138`); PO is shown, never added |
| Stock status | not found as High / Backorder / Healthy | Nearest: `scm.reorder_level` per location (`app/models/scm.py:110-146`), `scm.reorder_policy.min_override/max_override` (`app/models/scm.py:100-101`), `Stock.status` critical/low/normal/overstock (`app/models/inventory.py:144-156`) |
| A/O qty, AO qty, A/O (RM) | **not found** | Nearest concept: `so_line_allocations` (`app/models/project_so.py:1820-1875`) |
| Doc: SO | `sales_orders.so_number`, `debtor_code`, `debtor_name`, `order_date`, `requested_delivery_date`, `project_label` (`app/models/order.py:640-689`); line `required_date` (`:792`) | No loading date on SO |
| Doc: SPO | `spo_number`, `issue_date`, `expected_date`, `container_number` (= AutoCount "Loading Date" cell) (`app/models/procurement.py:488,543-570`) | Supplier via `supplier_id_for` (`app/services/scm/spo_supply.py:128-157`) |
| Doc: PO | `purchase_orders.po_number`, `issue_date`, `expected_date`, supplier (`app/models/procurement.py:880-883`) | |
| Doc: PL | `inbound_shipments.shipment_number`, `estimated_arrival_date`, `loading_date`, `delivery_warehouse` (free text) (`app/models/procurement.py:137-185`) | |

### 1.2 Readers that already compute most of it (reuse, do not rebuild)

- `location_stock_for_product` (`app/services/scm/location_stock_service.py:35`), served at
  `GET /api/v1/scm/reorder-runs/location-stock?product_id=` (`app/api/v1/scm/reorder_runs.py:485`):
  every active location for one product with on hand, SO, SPO, PO, available. Built on the same
  readers as the board, so its numbers already agree with fulfilment planning. Permission
  `scm.reorder.run`. This is the row source for the new panel.
- `FulfilmentBoardService.stock_detail`, `GET /api/v1/project-sales/fulfilment-planning/stock-detail`
  (`app/api/v1/projects/fulfilment_planning.py:919`, schema `app/schemas/project_board.py:931`):
  one product at one location or group, with the SO and SPO documents. Its docstring calls itself
  "AutoCount's Stock Status with Detail". SO rows lack debtor code; SPO rows lack supplier code and
  loading date; no PO or PL document rows.
- `container_request_drill.drill(kind=spo|incoming_pl|po)` (`app/services/scm/container_request_drill.py`,
  route `app/api/v1/scm/container_requests.py:291`): document rows for SPO, incoming PL and PO, per
  product, but takes a `supplier_id` as context.

### 1.3 Frontend pieces to reuse

- `CellStockTable` (`project-sales/fulfilment-planning/components/CellStockTable.tsx:248`): per-location
  table with On hand / SO / SPO / Available / PO, totals row, rows that expand into
  `StockDocumentsPanel`. Deliberate non-DataGrid carve-out (`:110-117`).
- `StockDocumentsPanel` (`project-sales/fulfilment-planning/components/StockDocumentsPanel.tsx:76`):
  document rows, S/O link `/scm/sales-orders/{id}` (`:424`), SPO link via `spoDetailHref()` (`:440`).
  Row types are S/O, SPO, On hand, Hold only (`:900`): no PO or PL rows.
- `OrderInquiryStockGrid` (`project-sales/_shared/components/OrderInquiryStockGrid.tsx:31`): wraps
  `CellStockTable` for one OI line; mounted in the OI detail line expand
  (`project-sales/order-inquiries/[id]/components/orderInquiryHeaderLinesColumns.tsx:470-480`).
- `QuotationStockChip` (`project-sales/[projectId]/components/QuotationStockChip.tsx:128`): a tone
  `Badge` that opens a `Popover` holding `CellStockTable stockOnly`. The pattern for an embed on any
  item code.
- `PlanRowDialog` `OnHandTable` (`scm/components/PlanRowDialog.tsx:661`): the replenishment page's
  On hand drill-down, DataGrid with `meta.expandedContent` (`components/ui/data-grid.tsx:21`) into
  `StockDocumentsPanel`. Its `IncomingPlTable` (`:969`) and `PoTabs` (`:1175`) are the PL and PO
  document lists.
- `Badge` (`components/ui/badge.tsx`), status map `lib/status-badge.ts:96` (no backorder / healthy /
  high keys yet). SCM health tones `scm/lib/health.ts:41`.

### 1.4 Embed points

| Process | Where | Attach |
| --- | --- | --- |
| Order inquiry detail | `project-sales/order-inquiries/[id]`, line expand `orderInquiryHeaderLinesColumns.tsx:438-480` | Swap `OrderInquiryStockGrid` for the panel |
| Order inquiry worklist | `components/orderInquiryWorklistColumns.tsx:1066` (`ItemCodeCell` `:717`) | Status chip + popover on the item code |
| Fulfilment planning | List view Product cell `FulfilmentBoardListView.tsx:620-645`; `BoardCellBreakdownDialog.tsx:1284` | Chip + popover; the breakdown dialog already holds `CellStockTable` |
| Loading plan | `scm/loading-plan/components/ContainerRequestSection.tsx:503-530` (Product cell -> `ContainerRequestRowDialog`) | Panel inside the row dialog; On hand / PL drill already there |
| Sample request | **not built** (only planned in `documentation/plans/scm/PLAN-consign-plan-15oct.md:22,30`) | Same chip when the screen lands; out of this lane |
| Stand-alone | new `/inventory-management/stock-status` under Inventory (`config/menu.config.tsx:418-454` and the compact copy `:1947-1980`) | Search an item, see the panel |

## 2. Behaviour card (posted as one crew-ask)

### Rules (round 3: owner answers + crew's masked rows of 17 Oct applied)

- R1. One panel, one product. Rows = every location of the company carrying any nonzero figure,
  **active or not in the CRM** (Q6 b: AutoCount lists and counts them). Built: no visibility policy
  narrows them; `stock_visibility.py` governs what the chatbot tells contacts, not what staff see, so
  the company scope alone applies. A location the
  CRM marks inactive (`warehouses.is_active`, `app/models/inventory.py:38`) carries a small "Inactive
  in CRM" tag and counts in every total like any other. A location with all zeros is hidden
  (AutoCount's report does the same). This departs from `location_stock_for_product`, which reads
  active warehouses only (`app/services/scm/location_stock_service.py:61-65`).
- R2. Columns, in this order: On hand, SO (shown negative, AutoCount style), PO, Incoming PL, SPO,
  then Forecast only if Q1 confirms it is AutoCount's formula. Footer = totals of every column.
  **No status / health column** (owner, 17 Oct: "dint need the health level column"). Negative
  figures print red; nothing else judges a number. Item chips at the embed points state on hand and
  SO, not a level.
- R3. Incoming PL (Q2 a): per item, over not-arrived, non-draft packing lists, each PL line's
  still-to-land qty (shipped - received) LESS the qty of that item's SPO rows on the same shipment,
  floored at 0. An SPO row is "on" a shipment when `spo_allocations.inbound_shipment_id` names it OR
  its `container_number` equals the shipment's `shipping_container_number`
  (`app/models/procurement.py:143,570`). Filled on the Total row only; location rows show a dot. Never
  placed on a location from the free-text `delivery_warehouse`. Expanding it lists the shipments.
  **Not `PL_UNALLOCATED_SQL`:** that formula subtracts the STORED `inbound_shipment_lines.
  spo_allocated_quantity`, which is 0 on PL-2609-051 and PL-2610-005 although SPO rows link them, and
  PL-2609-040 matches its SPO by container only. For SRTWB1515 it gives 686 where 227 is really on no
  SPO, so PL + SPO counts 459 twice. The loading plan's Incoming PL cell
  (`container_request_service.py:133`) carries the same double count today; reported, not fixed here.
- R3b. Forecast per location = On hand - SO + PO + SPO: **AutoCount's formula, confirmed** from the
  crew's rows (Q1). It fits every SRTWB1515 row; the 459 was SPO (BRW-BB 50, BRW-IR 385, BRW-IB 24).
  The CRM's own `available` is On hand - SO + SPO, so Forecast = available + PO. Under the footer,
  "Forecast incl. incoming PL" = total Forecast + Incoming PL.
- R4. SO outstanding counts the way AutoCount does (owner, Q3 b): `qty_ordered - qty_delivered`
  over open lines of open orders, the rule the CRM's Stock screen already uses
  (`app/services/inventory_service.py:1324-1369`), **and leaving out lines of orders AutoCount marks
  Transferable = F** (`sales_orders.is_transferable IS NOT FALSE`, `app/services/scm/demand.py:184`
  `is_transferable_order()`). Measured: SRTWB1515's CRM SO is 10,583 against AutoCount's 10,390; all
  193 sit at BRW-BB on 27 lines of 5 Transferable F orders; without them the CRM equals AutoCount to
  the unit. The Stock screen's rule and `is_open_demand()` both count F orders today (owner decision
  (a), 1 Oct, for the board), so the panel adds the F cut itself. A line CS marked "covered" COUNTS
  (tagged "covered in CRM" in the document list). **This differs from the fulfilment board and the loading
  plan**, whose rule (`app/services/scm/demand.py:168-181`) drops covered lines and reads CS's
  `qty_required` instead of `qty_ordered`, so their SO can be lower than this panel's. The panel
  follows AutoCount; the difference is stated, not hidden.
- R5. Horizon: an "SO due by" selector (All / end of this month / +1 / +2 / +3 months). It cuts SO
  only, by line `required_date`, else the header's `requested_delivery_date`. A line with no date,
  OR with the placeholder date 1 Jan 2030 (AutoCount's "3001"), is "no date yet" and always counts:
  28 lines carry it (SRTWB1515 4,411: BRW-BB 3,476, BRW-IB 920, BRW-IR 15; CWCX605-RL 7,332), so
  treating it as a real date would quietly drop 4,411 of SRTWB1515's 10,390 from every horizon. The
  document list prints it as "No date". Supply is never cut ("SO until Nov, less on hand, SPO and incoming PL").
- R6. (Dropped, owner Q4: no status / health level anywhere.)
- R7. Expanding a location lists its documents: SO, PO, SPO, sorted by delivery / expected date,
  with Doc no, Type, Account (debtor or creditor code), Name, Doc date, Delivery / ETA, Loading date
  (SPO: container), Qty. **Every document number is a link to that document's CRM page** (owner,
  17 Oct; AutoCount's double-click), the Incoming PL shipments included: SO `/scm/sales-orders/{id}`
  (`StockDocumentsPanel.tsx:424` already does it), SPO `spoDetailHref()` to
  `/procurement-management/spo-allocations/{spoNumber}` (`:440`), PO `/scm/purchase-orders/{id}` (as
  `scm/sales-orders/[id]/components/SoLineLinksBody.tsx:60`), PL
  `/procurement-management/packing-lists/{id}`. A viewer without the right to a document sees its
  number as plain text.
- R7b. **Outstanding, not cancelled, approved only** (owner, 17 Oct). Document rows and every SO / PO /
  SPO total count only the outstanding qty of open lines on open documents: SO `qty_ordered -
  qty_delivered` (AutoCount's delivered already includes cancelled qty, `app/models/order.py:760-764`),
  PO `qty_ordered - qty_received` (`OPEN_PO_SQL`), SPO allocated less received (`scm.on_order_v`). A
  cancelled line or document counts nothing and is not listed. The approval AutoCount holds and the
  CRM stores is the SO header's **Transferable** flag: `CanonicalSalesOrder.transferable`
  (`app/schemas/canonical_documents.py:260-265`) stored as `sales_orders.is_transferable`
  (`app/models/order.py:700`). F = not approved for the queue: left out, exactly as AutoCount's report
  leaves it out (R4). Searched: the CRM stores no other approval field from AutoCount on SO, PO or
  SPO (PO and SPO status is derived from their lines, `canonical_documents.py` S2 notes); if the owner
  means a different AutoCount column, it is not ingested today (Q7).
- R8. **SO totals with a Group by switch** (owner, 17 Oct: "need to be dynamic, like customer angle
  also"). One totals view replaces the project-only tab: rows = the values of the chosen attribute,
  columns = the locations carrying SO, a total per row and per column, each row expanding to its SOs.
  Group by: **Project** (`sales_orders.project_label`, else the customer; default), **Customer**
  (`debtor_code` / `debtor_name`), **Salesperson** (`sales_orders.sales_agent_id`,
  `app/models/order.py:658`; stock-detail's SO row already carries `agent_code`,
  `app/schemas/project_board.py:857`), **Delivery month** (line `required_date`, `order.py:792`; "No
  date" for none or 1 Jan 2030). Pattern reused: the chat history page's Group by, a
  `SearchableSelect` over fixed options (`sorento_crm_frontend/app/(protected)/system-management/
  chat-history/page.tsx:51` and `:353`). Not reused: the reports pivot
  (`components/reports/ReportPivotTable.tsx`, a server-side report with datasets and saved views; one
  item's SO lines are already loaded, so grouping in the browser is simpler); the two `GroupBySelect`
  components (`components/common/GroupBySelect.tsx`, `components/ui/group-by-select.tsx`) are imported
  nowhere. The app has no drag-a-column-header-to-group grid.
- R11. **Search the locations** (owner, 17 Oct: "got many locations"; SRTWB1515 has 14). One search
  box above both tabs, the shared `ListSearchInput` (`sorento_crm_frontend/components/common/
  ListSearchInput.tsx`, used by 159 screens). It filters as you type, client side, matching the
  location code or name anywhere, the match highlighted. By location: filters the rows. SO totals:
  narrows the location columns, and rows with nothing at the shown locations drop out. A "Shown, n of
  N" subtotal sums the visible locations; the All locations total, Incoming PL and Forecast incl. PL
  stay on screen, so a filter is never mistaken for the item's total. Enter (`onSubmit`) opens the
  documents of the first visible location; the x clears. No match: "No location matches <text>" with
  the box kept.
- R12. **No separators** (owner rule): one fact per element, never "a · b". Labelled fields for the item
  and record metadata; two chips (On hand, SO) rather than one joined chip; a missing per-location
  Incoming PL prints "-".
- R9. Read-only. No writes to AutoCount, no new stored figures; every number is a live read.
  "On hand as of <last push time>" from `companies.stock_push_confirmed_at`.
- R10. A/O, AO and A/O (RM) columns are dropped: the CRM holds no such figure.

### Edge cases

- Product with no stock row anywhere: panel shows the empty state "No stock, orders or incoming
  for this item", not a blank table.
- SO line with no warehouse (`sales_order_lines.warehouse_id` nullable, `order.py:758`): built as
  LEFT OUT (deviation from the sketch's "No location" row). AutoCount's report is per location, so a
  line with none is not on it either; the panel stays equal to AutoCount, which is the check crew
  runs. Trigger to revisit: a real SO line reaching the CRM with no location.
- SPO past its ETA: still counted (`spo_supply.py:9-22`), the doc row shows days late.
- Location inactive in the CRM but holding stock: listed, tagged "Inactive in CRM", counted (R1,
  Q6 b). SRTWB1515 per AutoCount's screenshot: REJ100% 1, JB SHOWR 1, PJ-SR 1, CON 1 (all four inactive
  in the CRM), so AutoCount's 499 = the CRM's active 495 + 4, exactly. The replenishment page and
  the board still leave these out; the panel does not.
- **Data timing, not a rule:** CON is 32 in the CRM's 15 Oct copy but 1 in AutoCount's screenshot
  (date unknown, likely 16 Oct). The panel prints whatever the last stock push holds, stamped "On
  hand as of <push time>" (R9); a difference against an AutoCount screenshot taken at another moment
  is not a defect.
- Warehouses are not company-scoped (`warehouses` has no company cut in `location_stock_for_product`),
  so another company's pools (HOLD, HQ, 2nd REWORK) appear as zero rows; the panel scopes them out
  and hides all-zero rows (R1).
- An item code with nothing anywhere (SRTWCX8605-S-PJ: 0 at every active location, no open SO / PO /
  SPO / PL) shows the empty state; its live sibling SRTWCX8605-S-RL-PJ is a different item.
- Viewer without the permission for a linked document: the doc number shows, not as a link.
- 375px: one card per location (On hand, SO, PO, SPO, Forecast), then a Total card carrying
  Incoming PL.
- An SO line with `qty_delivered > qty_ordered` (over-delivery): contributes 0, never negative.
- Group by a value that is empty (no project label, no salesperson, no date): one row "No project" /
  "No salesperson" / "No date", so the column totals still add up.

### Examples (crew's masked rows, dev DB = 15 Oct prod copy)

- SRTWB1515, panel rules applied: On hand at active locations 495; AutoCount's 499 adds REJ100% 1,
  JB SHOWR 1, PJ-SR 1, CON 1 (inactive in the CRM, counted per Q6 b), so the panel's on hand matches
  AutoCount when both read the same moment (the 15 Oct copy holds CON 32, so the panel on that copy
  reads 530: data timing). SO 10,390 (= AutoCount, after dropping 193 Transferable F at BRW-BB), PO
  2,438, SPO 459, Forecast 499 - 10,390 + 2,438 + 459 = -6,994 = AutoCount. BRW-BB: 0 - 7,312 + 1,734 + 50 = -5,528, equal to
  AutoCount. Incoming PL 227 (PL-2610-001 118, PL-2610-010 109); Forecast incl. PL -6,771.
- CWCX605-RL: On hand 1,030, SO 12,081, PO 1,820, SPO 0, Forecast -9,231; Incoming PL 641
  (PL-2610-014, on no SPO); Forecast incl. PL -8,590. (Transferable F not measured for this item.)
- SRTWCX8605-S-PJ: nothing at any active location and nothing open, but 7 at CON (inactive in the
  CRM), so under R1 it lists one row, CON 7, rather than the empty state.

### Questions (max 5)

Answers: Q2 (a) and Q5 (a) (crew, 16 Oct). Q3 (b) and Q4 "no status column" (owner, 17 Oct ~02:10,
verbatim: "B, autocount is source of truth, dint need the health level column. Need on hand, so,
po, incoming pl, spo most important"). Q1: reconcile against AutoCount's -6,994 from the crew's
masked rows before build, and name what the 459 is.

The 459 is SPO (crew's rows, 17 Oct): AutoCount's Forecast = On hand - SO + PO + SPO, Q1 (a).

- Q1. Forecast: (a) On hand - SO + PO + SPO, (b) AutoCount's exact formula once the masked rows
  show what the 459 is. **Answered: (a) is AutoCount's formula**, confirmed row by row.
- Q2. Incoming PL: (a) one product-level line, not split by location, (b) split by the PL's
  free-text `delivery_warehouse`. Recommendation: (a), because (b) is free text and would put
  numbers on locations that may be wrong.
- Q3. A line CS marked "covered" (no purchase needed): (a) drop it from SO like the board does,
  (b) count it like AutoCount does. Recommendation was (a). **Answered (b)**: AutoCount is the source of truth.
- Q4. Status thresholds: (a) Backorder < 0, High above the CRM's per-location max level, Healthy
  otherwise, (b) AutoCount's own levels (needs a new ingest field). **Answered: no status column at all.**
- Q5. Per-project totals: (a) group by the SO's project label, falling back to customer,
  (b) customer only. Recommendation: (a). **Answered (a).**
- Q6 (new, from the crew's rows). Inactive CRM locations that hold stock (SRTWB1515: CON 32,
  JB SHOWR 1, PJ-SR 1, REJ100% 1): (a) leave them out, the CRM's existing cut; (b) list them, counted,
  because AutoCount's report lists those locations. Recommendation was (a). **Answered (b)** (crew,
  from the owner's AutoCount screenshot: REJ100% 1, BRW-BB 0, DC1-BB 191, JB SHOWR 1, PJ-SR 1,
  MWH-IB 140, RESERVE 0, BRW 0, BRW-IR 11, CON 1, MWH-SMC 24, MWH-BB 108, BRW-IB 6, BRW-SMC 15 = 499).
  Every location AutoCount shows is listed, inactive ones tagged. The mock's footnote ("35 not
  counted") is superseded by R1; per crew, no new mock round for this.

## 3. Mock

`documentation/plans/inventory/mockups/stock-status.html`, round 4: SRTWB1515 drawn from the crew's masked rows, 14 locations (the four inactive in the CRM at AutoCount's values), Group by, location search, no separators, every document a link, a 'Where it appears' strip and a Sample request frame (round 1 had a Status column and the board's SO rule; round 2 used made-up figures; round 3 had a project-only tab). Six screens at 1280 and 375:
stand-alone page (By location with BRW-BB expanded to its documents), By project tab, Order inquiry
line expand, Fulfilment planning item chip + popover (same chip on the OI worklist), Loading plan row
dialog tab, empty state. Each screen ends with a "Built from" table mapping every element to its
existing component. Quantities, dates, document numbers, account codes and short company names are
the crew's masked rows (person names masked by crew); project labels are AutoCount's, cut at 12
characters as crew sent them.

### Where it appears (owner asked, 17 Oct)

| Place | How | Frame in the mock |
| --- | --- | --- |
| Inventory, Stock status page | stand-alone, any item | 1, 1b, 2, 2b |
| Order inquiry detail | each line's expand | 3 |
| Order inquiry worklist | chip + popover on the item code | 4 |
| Fulfilment planning | chip + popover on the product | 4 |
| Loading plan | Stock status tab in the product row dialog | 5 |
| Sample request | each line's expand, **follow-up once PR #1718 (SR-CORE) merges** | 5b |

Sample request hook (planned, not built: #1718 merged to main as b303169f during this lane).
`OrderInquiryStockGrid` was NOT turned into the panel: `RequestReserveDialog` still uses it for the
reserve flow, so the OI line expand swapped to `StockStatusPanel` at its own call site instead. The
follow-up is therefore a one-line swap at
`project-sales/sample-requests/components/SampleRequestDetail.tsx:290`
(`<OrderInquiryStockGrid productId={line.product_id} location="BRW" />` becomes
`<StockStatusPanel productId={line.product_id} showOpenFullPage />`), plus its test, plus the item chip
on the form's Item Code column only if the owner asks. Its own small-fix lane.

### Questions

- Q7. Approval: the only AutoCount approval-like field the CRM stores is SO Transferable (R7b), left out
  when F. (a) that is the "approve column" the owner means; (b) another AutoCount column, not
  ingested today, which a separate ingest lane would add first. Recommendation: (a), because
  Transferable is AutoCount's own gate for "confirmed for the queue" and leaving F out already makes
  the SO match AutoCount's report to the unit (10,390).

## 4. Build sketch (after the mock is OK'd)

- BE: one read endpoint `GET /api/v1/inventory/stock-status?product_id=&so_due_by=` composing
  `location_stock_for_product`'s on hand / PO / SPO readers, widened to inactive warehouses (R1)
  (rows; its SO reader uses the board's
  `demand.py` rule, so the panel's SO comes from the AutoCount-shaped
  `open_so_qty_by_product_warehouse` instead, R4), the stock-detail SO / SPO readers plus a PO reader
  (documents), the R3 Incoming PL reader (NOT `PL_UNALLOCATED_SQL`) and a project grouping; SO adds
  the Transferable F cut (R4) and treats 2030-01-01 as no date (R5). Gate (built, after security
  review S1): any of `projects.projects.view`, `scm.reorder.run`, `scm.dashboard.view`, the screens
  that embed it. `inventory.stock.view` alone is refused: the panel names customers, suppliers and
  their documents, which stock viewers do not otherwise see. The menu entry uses the same three.
- FE: `StockStatusPanel` (CellStockTable-based) + `StockStatusChip` (QuotationStockChip pattern),
  mounted at the four embed points and the stand-alone page.

## 5. As built (17 Oct)

- BE `app/services/stock_status_service.py`, `app/api/v1/inventory/stock_status.py`
  (`GET /api/v1/inventory/stock-status`, `GET .../summary` for list chips, 200 ids max). Incoming PL is
  netted against the SPO column's own rows (open, allocated less received), by link, else by container
  for an unlinked row only (reviewer B1).
- FE `components/stock-status/` (panel, chip, batched summary loader), stand-alone page
  `inventory-management/stock-status`, embeds at the four points of the table above.
- R7: a document number is a link only for a viewer holding that page's menu permission (SO / PO
  `scm.dashboard.view`, SPO `procurement.spo_allocations.view`, PL `procurement.packing_lists.view`),
  else plain text.
- Below 992 wide, By location reads as cards (mock frame 1 at 375).
- Crew audit fix round (17 Oct): which chip is open lives outside the chip, because a list
  that rebuilds its columns remounts every cell (TanStack renders a cell function as a
  component) and the board's first click after load lost its popover that way. It is keyed by
  the caller's row (board contribution key, worklist row id), so two rows of one product never
  share it. Below 448px of room under the chip the popover asks for the side with more room,
  and its height is capped at the room on that side, so it neither shrinks to a strip low on the
  screen nor runs off a small one.
- Open, owner ruling: (1) container text such as "SEKU1741573 (KUCHING)" on a packing list does
  not equal the SPO's "SEKU1741573", so the exact match leaves 50 units of PL-2609-040 in both
  SPO and Incoming PL (1 of 359 open shipments); matching unchanged until the ruling.
  (2) Security S-C: the effective ETA folds in `eta_delay_date`, which the registry lists as a
  `procurement.packing_lists.view_clearance` field; the board does the same today.
- Follow-ups: Sample Request swap (above); the fulfilment board and loading plan count SO the
  `demand.py` way, which differs from this panel by design (R4).
