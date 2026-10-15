# UAC: consignment pages get the SO planning buttons (CONSIGN-PLAN)

Plan: `PLAN-consign-plan-15oct.md`. Card: `CARD-consign-plan-15oct.md`.
Owner answers 16 Oct 00:10: Q1 the board's existing Borrow picker, CON is one of the donor
choices, no Buy, nothing to purchasing, no stock move; Q2 (a) CON plannable and a donor for
consignment lines only; Q3 (a) plan = ordered less returned. Crew 15 Oct: Q4 (a), Q5 (a).
Owner note: the coming Sample Request flow borrows through this same path, so the rule is keyed
on a doc-type set, not on the code `CON` and not on `consignment` spelled inline.

## Board (backend)

- **UAC-01 [BE][T]** A closed, fully delivered, project-class consignment typed into
  `?orders=` is admitted, one row per undecided line.
- **UAC-02 [BE][T]** A consignment line whose own bin is flagged out of fulfilment planning
  (CON) does NOT carry "Outside fulfilment planning"; it is plannable and confirmable.
- **UAC-03 [BE][T]** That line's Borrow candidates include its own bin (CON) as a plain
  location row (BORROW-OWN-LOC shape).
- **UAC-04 [BE][T]** A sales order line never sees CON stock: not in its Borrow candidates, not
  drawn by its proposal, even when CON holds on-hand stock of the product.
- **UAC-05 [BE][T]** A sales order line whose own bin is flagged off still reads "Outside
  fulfilment planning" (the lift is doc-type scoped).
- **UAC-06 [BE][T]** Confirming Borrow from CON on a consignment line succeeds, writes no Buy and
  no purchasing (Order Inquiry) row, and the line then counts as planned
  (`planned_lines == plannable_lines` on the order read).
- **UAC-07 [BE][T]** Plan qty = ordered (or required) less cancelled less returned, floored at 0,
  in both the SQL `plan_qty()` and the Python `plan_qty_of`. A fully returned line is not admitted
  and is not counted in `plannable_lines`. Sales order lines (returned 0) are unchanged.
- **UAC-08 [BE][T]** A retail-class consignment is not admitted (existing rule, regression).
- **UAC-09 [BE][T]** Reset planning on a consignment removes its decision and puts it back to
  never planned.

## Consignment list (frontend)

- **UAC-10 [FE][T]** Row ticks; **Plan selected (N)** is the toolbar's main button (same
  `buildPlanActions`, max 50, permission `projects.projects.view`, absent without it); disabled
  with the reason at 0 ticked; with CG-A and CG-B ticked it opens
  `/project-sales/fulfilment-planning?orders=CG-A,CG-B`.
- **UAC-11 [FE][T]** Actions menu: **Reset planning (N)** (with `scm.reorder.run`) and
  **Refresh**. No Upload, no Add.
- **UAC-12 [FE]** DataGrid rules hold (fixed layout, resizable, explicit sizes); usable at 375px
  and 1280px.

## Consignment detail (frontend)

- **UAC-13 [FE][T]** A project-class consignment shows the **Plan** primary button
  (`?orders=<so_number>`) and the planned chip, exactly as a sales order does; a retail-class
  consignment shows neither the Plan button (same rule as a retail sales order).

## Browser

- **UAC-14 [FE]** Sandbox pass: list tick, Plan selected, board shows the consignment line at CON,
  Decide, Borrow, CON chosen, Confirm, back on the detail the chip reads planned.
