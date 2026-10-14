# UAC: AutoCount Consignment + Consignment Return on the CRM, Cancel PO screens (AC-CONSIGN-CRM)

Plan: `PLAN-autocount-consign-crm-14oct.md`. [BE] backend, [FE] frontend, [T] automated test.

## Contract

- **UAC-01 [BE][T]** `GET /api/v1/external/contract` reads version `2.9`. `consignments` lists the 2.9 fields
  (DebtorCode, DebtorName, SalesAgent; Details.UOM, Details.UnitPrice, Details.SubTotal, Details.TransferedQty).
  The consignment note says a CG becomes a sales order marked Consignment once its lines state TransferedQty;
  the return note says it records the returned qty only.

## Consignment as demand (ruling 1, Q1-Q3)

- **UAC-02 [BE][T]** A CG whose every item line states TransferedQty lands as one sales order, doc_type
  `consignment`, number = CG number, one line per item line; ordered = Qty, delivered = TransferedQty.
- **UAC-03 [BE][T]** A CG without TransferedQty on every item line is stored only; the next push that carries it
  makes the order.
- **UAC-04 [BE][T]** Voided or swept CG = cancelled order, never deleted. Every line transferred = closed.
- **UAC-05 [BE][T]** A CG and an SO with the same number never take over each other's row.
- **UAC-06 [BE][FE][T]** The order reads "Consignment" on: SO list, SO detail, Order Inquiry (header list,
  detail, lines view), Fulfilment Planning (list, board). A sales order shows no such label.
- **UAC-07 [BE][T]** Demand, planning and outstanding readers include consignment lines, including the
  report-ask "Outstanding orders" basis.

## Not a sale (Q5 a)

- **UAC-08 [BE][T]** A consignment line is left out of every SO-based sales figure: sales report, top selling,
  achievement / targets / unassigned, Reports `sales_order_lines` dataset, report-ask ordered and delivered bases.

## Consignment Return (ruling 2-3, Q4)

- **UAC-09 [BE][T]** A keyed return line (FromDocDtlKey) records its qty in `qty_returned` on that line.
- **UAC-10 [BE][T]** A key-less return takes the (CG, item, location) group's one line; when the group has
  several lines it applies only when it returns every unit still out (`group_whole`), else undetermined and
  applied to no line.
- **UAC-11 [BE][T]** `qty_returned` = matched live returns, capped at ordered, derived every sync; voided or
  swept returns drop out. Planned, delivered, cancelled and status never move because of a return.
- **UAC-12 [BE][FE][T]** A consignment line exposes `qty_returned` on the SO detail API and screen.

## Cancel PO (part C)

- **UAC-13 [BE][T]** A cancelled SPO allocation qty is never GRN capacity (`grn_spo_matching._measure`).
- **UAC-14 [FE][T]** PO detail edit mode outstanding = ordered - received - cancelled.
- **UAC-15 [BE][T]** Incoming stock (shipment line, chatbot ETA, MCP) never shows a cancelled qty as incoming
  when GRN picks sit on a sibling allocation of the same product and its stated receipt lags.
- **UAC-16 [BE][T]** Spreading a group's GRN picks never lands them on the cancelled part of a member.
- **UAC-17** The PR lists every PO / SPO / incoming screen checked, with file:line and verdict.
