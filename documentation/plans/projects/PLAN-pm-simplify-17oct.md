# PLAN: simplify the project PO and delivery schedule parts (PM-SIMPLIFY)

Status: discovery (17 Oct 2026). No code until the owner OKs the mock. Track: not chosen yet
(decided by the diff once the mock is approved).

Owner 15 Oct: "the PO part and the delivery schedule part are badly designed, too complicated ->
simplify." This is a new round on top of the shipped 24 Sep redesign
(`PLAN-po-schedule-redesign-24sep.md`, slices S1-S7, #1219 #1237 #1264 #1265, later #1618 #1681).

## Steps

1. Discovery: walk create PO from a project, plan and change delivery dates, partial deliveries,
   see what is outstanding. Count screens, clicks and fields per job, screenshot every step.
2. Proposal: fewer steps and screens, existing components only. Mock v1 on the real case with an
   element -> component map (repo paths), at most 5 questions, one report to the owner.
3. After the owner OKs the mock: behaviour card if rules change, red-first tests, reviewer,
   security review, browser pass, hand-test script.

## Discovery findings (code walk, 17 Oct; browser walk with step counts follows)

FE root `sorento_crm_frontend/app/(protected)/project-sales/[projectId]` (P below), BE root
`sorento_crm_backend/app`.

### Where the work lives today

- Project detail has 12 tabs (`P/components/ProjectDetailClient.tsx:77-90`); PO, schedule and SO
  work sits on three of them (POs, Delivery schedules, Sales orders).
- One PO end to end visits those 3 tabs plus 4 pages: PO record `P/pos/[poId]`, PO review
  `P/purchase-orders/[versionId]`, schedule review `P/delivery-schedules/[versionId]`, sales
  order `P/sales-orders/[psoId]` (and its /worksheet, /revisions, /divergence sub-pages).

### Jobs, as the code runs them

1. **Create a PO (scan).** POs tab -> Upload a PO document (2 fields) -> PO review page, wait
   for extraction -> fix lines (7 editable columns) -> act on each handwritten note -> Confirm ->
   Approve -> Countersign (a second user). Three sign-offs on one screen
   (`P/components/POIntakeConfirmClient.tsx:215-233`, `BE/api/v1/projects/po_intake.py:329,366,387`).
   By hand: Record a PO (7 fields, `PurchaseOrderDialog.tsx:131-221`) then Edit the PO lines
   (8-column editor) on the PO record page.
2. **Plan delivery dates.** Delivery schedules tab -> Upload a schedule (4-6 fields,
   `DeliveryScheduleUploadDialog.tsx:160-262`) -> review matrix -> fix cells / resolve or dismiss
   columns -> Confirm (dialog, "Confirm anyway" + reason when anything is unreconciled).
3. **Change delivery dates.** Dates are read-only on the matrix
   (`DeliveryScheduleMatrix.tsx:233-239`). Three different routes, three different stores:
   upload a whole new version (`delivery_phases.delivery_date`), accept a re-date proposal in the
   History sheet (`delivery_schedule_cells.delivery_date_override`,
   `BE/services/project_schedule_service.py:2330`), or edit `delivery_date` on SO lines
   (`SalesOrderLinesEditor.tsx:273`), which never flows back. Published SOs then go through
   Revisions -> Compare -> Create the amendment (dialog) -> decide rows -> Publish the amendment.
4. **Partial deliveries.** No delivery is recorded on the project at all. Splitting happens
   when sales orders are built (Build drafts dialog + second confirm,
   `SalesOrderBuildDialog.tsx:92-202`): each PO line is spread over schedule phases into SO lines.
   Delivered quantities exist only on core `sales_order_lines.qty_delivered` (AutoCount sync,
   `BE/models/order.py:759-764`).
5. **See what is outstanding.** No project screen shows ordered vs scheduled vs delivered per
   item. Only Stock debt and Fulfilment planning read delivered quantities, keyed by product/SO,
   outside the project.

### Defects found on the way (verified against the code)

- D1. Two editing surfaces for a scanned PO's lines; only one reaches the sales orders. The
  build reads `po_lines` whenever a PO version exists
  (`BE/services/project_so_draft_service.py:577-602`); hand edits to `purchase_order_lines` are
  used only without a scan (`:604-627`) and confirming a new version deletes and rewrites them
  (`BE/services/project_po_confirm.py:246-247`).
- D2. Approve and Countersign gate nothing: no reader of the approval stamps in the build or
  publish path (grep of `approved_at` in `project_so_draft_service.py` and
  `api/v1/projects/sales_orders.py` is empty), though `po_intake.py:372` says CS cannot open the
  draft before approval.
- D3. A sales order can be built from an unconfirmed schedule version: `build` never checks
  `confirmed_at`; the build dialog lists "Not confirmed yet" versions. The PO version picked
  also falls back to an unconfirmed one (`project_so_draft_service.py:571-575`).
- D4. The next-step card links `?tab=pos&po=`, `?tab=schedules&po=`, `?tab=sales-orders&build=`
  (`POToSalesOrderStep.tsx:76-81`) but only `tab` is read (`ProjectDetailClient.tsx:98`), so the
  user re-picks the PO in the next dialog.
- D5. The PO is re-picked in the schedule upload, the build dialog and the amendment picker.

### Data model, for reference

Four line sets describe one order: `po_lines` (scan, per version), `purchase_order_lines`
(agreed/keyed), `delivery_schedule_cells` (product x phase), project `sales_order_lines`
(product x phase x date). Delivery dates live in three places: `delivery_phases.delivery_date`,
`delivery_schedule_cells.delivery_date_override`, `sales_order_lines.delivery_date`.
