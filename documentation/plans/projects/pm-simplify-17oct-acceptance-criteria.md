# UAC: simplify the project PO and delivery schedule parts (PM-SIMPLIFY)

Status: DRAFT, written against the recommended answers to Q1-Q3 (Q4 and Q5 answered (a) by crew,
17 Oct) in
`PLAN-pm-simplify-17oct.md`. Rewritten once the owner OKs mock v1
(`mockups/po-schedule-simplify-v1.html`) and answers the questions. Nothing here is built.

Tags: `[BE]` pytest, `[FE]` vitest, `[E2E]` recorded agent-browser run (no new Playwright spec),
`[T]` copy check. Words on screen follow the 24 Sep ruling: "Area" (never "Phase"), "AutoCount
differences" (never "divergence").

## Journey

Actor: a project salesperson at Sorento with a customer PO, a delivery schedule, or a phone call
moving a delivery.

- **J1. Find the order.** From `/`: Project Sales > Projects > the project > **Orders** tab. One
  row per PO; expanding it shows its schedule and its sales orders.
- **J2. Add a PO.** Upload PO (header button on the project, or Start on the listing), drop the
  file. Lands on the PO page; the Lines tab is the review until **Confirm PO**.
- **J3. Add or revise a schedule.** On the PO page, Deliveries tab, **Upload revised schedule**:
  the file is the only field. Until confirmed, the Deliveries tab shows the uploaded version's
  By product matrix with its flags; **Confirm schedule** is the header button.
- **J4. Move a delivery.** Deliveries tab, click the area's date, pick a new date. Draft sales
  order lines for that area take the new date; a published sales order gets an amendment started.
  A message says what moved, with Undo.
- **J5. See what is left.** PO page, Lines tab: Ordered, Scheduled, Delivered, Outstanding per
  line; expanding a line lists its deliveries.
- **J6. Build and publish.** The header button is always the next step: Confirm PO, Confirm
  schedule, Build sales orders (one dialog, split by), Publish N sales orders.

## Acceptance criteria

### Orders tab (J1)
- AC-O1 `[FE]` The project page shows one Orders tab where POs, Delivery schedules and Sales
  orders were; old `?tab=pos|schedules|sales-orders` links open Orders.
- AC-O2 `[FE]` A PO row expands to its latest schedule version (label, dated, confirmed or not)
  and its sales orders (count by status). The grid has the Columns toggle and a right-click menu
  (Open, Upload delivery schedule, Upload a new PO version, Copy PO number).
- AC-O3 `[BE]` The Orders list returns, per PO, delivered and ordered unit totals and the next
  delivery date.

### PO page (J2, J6)
- AC-P1 `[FE]` `pos/[poId]` shows the header facts and four line tabs: Lines, Deliveries, Sales
  orders, Documents. `purchase-orders/[versionId]` and `delivery-schedules/[versionId]` redirect to
  it with the right tab open.
- AC-P2 `[FE]` Exactly one primary header button, chosen by state: Confirm PO (unconfirmed
  version), Confirm schedule (unconfirmed schedule version), Build sales orders (no drafts), Publish
  N sales orders (unpublished drafts), none when all are published.
- AC-P3 `[BE]` (Q1a) Confirm is the only PO sign-off; Approve and Countersign are gone from the
  API surface the page uses. Existing stamps stay readable in the audit log.
- AC-P4 `[BE]` (D1) A scanned PO's lines are edited in one place; an edit reaches the next build.
- AC-P5 `[BE]` (D3) Build refuses (409, plain message) when the PO's latest schedule version is
  not confirmed; the build uses the latest confirmed PO version only.

### Lines tab (J5)
- AC-L1 `[BE]` Per PO line: ordered = the line's qty; scheduled = the confirmed schedule's units
  for that product; delivered = sum of `delivered_qty_of` over the core lines linked through
  `projects.sales_order_lines.source_po_line_id`; outstanding = ordered minus delivered, never below
  zero. Fixture: 12 lines, one 10 short on the schedule, three part delivered.
- AC-L2 `[FE]` Expanding a line lists its deliveries (date, area, units, delivered, sales order and
  its status). A line scheduled short is amber with "N not scheduled"; the filter chip counts them.
- AC-L3 `[FE]` Rows drag to sort; Columns toggle; usable at 375 as cards (ordered, delivered,
  outstanding).

### Deliveries tab (J3, J4)
- AC-D1 `[FE]` One row per area of the confirmed schedule: area, levels, date, units, delivered,
  sales order. By product shows today's matrix.
- AC-D2 `[BE]` (Q2a) Changing an area's date writes that area's date and the `delivery_date` of
  every draft sales order line on it, in one transaction; published sales orders are untouched and
  an amendment is opened for each. Audit row per change.
- AC-D3 `[FE]` After a date change a message names the area, the new date and what followed, with
  Undo; Undo restores both the area and the draft lines.
- AC-D4 `[FE]` Upload revised schedule from the PO page asks only for the file; PO, schedule and
  issuer come from the page (issuer defaults to the PO's issuer).

### Build (J6)
- AC-B1 `[FE]` (Q5a) Build sales orders opens one dialog with one field (split by); no second
  confirm dialog; PO and schedule are not asked.

### Words
- AC-T1 `[T]` No screen in this lane says "Phase", "divergence", "Approve" or "Countersign".

### Browser
- AC-E1 `[E2E]` J1-J6 walked on the sandbox project at 1280 and 375, screenshots per step, counts
  matching the plan's before/after table.
