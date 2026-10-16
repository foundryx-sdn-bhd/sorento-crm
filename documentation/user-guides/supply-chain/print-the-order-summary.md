# Supply Chain - Print the order summary

The order summary is the same paper sheet a buyer used to fill in by hand, now built from the
plan's own figures. Use it to see what to order, print it, or hand it to a supplier.

## Where

From a reorder plan (see [Run a reorder plan](run-a-reorder-plan.md)), open the **Actions** menu
on the Lines tab toolbar and pick **Order sheet PDF** or **Order sheet Excel**. The same menu
also carries **Low stock report Excel** (see [Low stock report](low-stock-report.md)) and
**OI worksheet Excel** (see "OI worksheet Excel" below) - all four items grey out together
while any one of them is preparing.

## What the sheet shows

One row per product, with the plan's columns in the sheet's own order: **Item code**, **BRW on
hand**, **Reorder level**, **Project qty**, **Dealer o/s**, **Suggested qty**, **Suggestion**,
**Order qty**, **Delivery**, **Project / customer**, **Supplier**, **Last cost**, **BRW PO
qty**, **BRW incoming qty**, **Last in qty**, **Last in date**, **Remarks**.

* **BRW on hand / BRW PO qty / BRW incoming qty** count the site pool only (active,
  non-project warehouses) - stock or supply sitting in a project bin is not on the sheet.
  **BRW PO qty** and **BRW incoming qty** are the same numbers the grid's PO and SPO columns
  showed for that product on this run. **BRW incoming qty** prints one "container - quantity"
  line per open shipment, or the bare quantity when a shipment names no container - the SPO
  number itself is not shown. **BRW PO qty** still names each purchase order number.
* **Suggestion** prints one part per line - **Stock:** what sits at BRW, **PO:** what is on
  open purchase orders, **Buy:** the suggested quantity beside it in **Suggested qty**. It is
  a reading of those figures, not a sum of them. A row with nothing to order reads "Nothing".
* **Delivery** and **Project / customer** count exactly the order inquiry rows the plan is
  buying for: raised or partly linked, acknowledged, not redirected to the shared pool, still
  owed, on the sales orders picked in Start Plan, and due inside the plan's delivery window. A
  product with no inquiry row in that scope shows blank Delivery and Project / customer cells
  even when it has Project demand elsewhere on the grid. On a Project plan this is the same
  row set the plan buys for, so **Project qty** reads the same figure as **Suggested qty**.
* **Project / customer** reads **CUSTOMER / PROJECT**, the same way the Order Inquiries
  worklist's Project column does: the project is the registered project's own title, or, for a
  sales order adopted from AutoCount, that order's own project label.
* **Supplier** is the chosen supplier, or the suggested one if none has been chosen yet - the
  newest non-cancelled purchase order line for the product.
* **Last cost**, right of Supplier, is the unit cost and currency on the newest purchase order
  line that carries a price, for example "12.50 CNY" - blank when no priced line exists. A
  costed line is not always the same purchase **Supplier** names: Last cost looks past a
  costless newer line to find the newest one that carries a price.
* **Last in qty** reads the newest shipping order line for the product, received or not - the
  same line the chatbot's "last in" answer names. It prints one "container - quantity" line,
  or the bare quantity when that line names no container - the SPO number itself is not
  shown. It is blank when the product has no shipping order line at all.
* **Last in date** is that same line's expected delivery date, or its issue date when it has
  none.
* **Remarks** shows the MOQ (for example "MOQ 1000") only when there is one.

Every figure on the sheet is the plan's own - nothing is typed twice, with one exception:
**Last cost** is read from current purchase-order data at export time, the same way
Description and Category are on the low stock report, so it can move between two exports of
the same run. The sheet prints every product the plan planned, the same rows the list shows
(see [Run a reorder plan](run-a-reorder-plan.md#filters)), so the Excel row count equals the
plan's **Decisions** tile total.

## Getting the file

Picking **Order sheet PDF** or **Order sheet Excel** does not download straight away - a toast
reads "Preparing the order sheet - it will appear in My Downloads." while the sheet is built in
the background:

* The header's download icon (**My downloads**) picks up a badge counting sheets still
  preparing.
* Open the drawer to watch the row move from **Queued** to **Preparing** to **Ready**, then
  click it to download.
* Only one order sheet per plan can be preparing at a time. Picking either item again before
  the first finishes answers "An order sheet for this plan is already being prepared - check
  My Downloads." instead of starting a second one.
* If a sheet fails, the drawer shows the reason next to that row.

**PDF** is a landscape sheet with one row per product, the same 17 columns; **Excel** is a
workbook of the same rows.

Only products with something to order are included. If more than 2,000 such products would be
on the sheet, the export is refused up front ("Narrow the plan first") before anything starts
preparing - narrow the plan's scope (fewer warehouses or products) and try again.

## OI worksheet Excel

Pick **OI worksheet Excel** from the same **Actions** menu to get the order inquiry rows
behind this plan before any decision is made on it. This is meant to be downloaded first, so
purchasing can tick every line against a purchase order in AutoCount before working the plan's
own suggestions.

The workbook lists the order inquiry rows inside the plan's own Start Plan scope - the orders
picked, the products picked, and the delivery window - not the engine's decision. A product the
plan chose not to put on the order sheet still shows here if its order inquiry row is in scope.

* A plan scoped to no products produces an empty worksheet.
* A plan with no picked orders lists every order in the delivery window.

It prints in the same ORDER INQUIRY layout the Order Inquiries worklist export already uses
(see [Upload the data a reorder plan is built
from](upload-plan-data.md#the-order-inquiries-page)): one **ORDER INQUIRY** sheet holding every
delivery month (rows with no date included), rows ordered by **SUPPLIER**, **ITEM CODE**, then
delivery date, and
**TOTAL QTY** printed once, on the last row of each supplier + item code run, totalling every
date. Columns, in order: **SO DATE**, **S/O NO**,
**ITEM CODE**, **QTY**, **TOTAL QTY**, **DELIVERY DATE**, **PROJECT/CUSTOMER**, **SUPPLIER**,
**PO NO**, **LOCATION**. **QTY** is the quantity still owed - a linked quantity is already
netted out.

Getting the file follows the same **My Downloads** flow described above; picking it again
before the first finishes answers "An OI worksheet for this plan is already being prepared -
check My Downloads." instead of starting a second one.

Needs both the **Order Inquiries** view permission and the reorder plan's own view permission -
ask an admin if the item does not appear in the menu.

## See also

* [Run a reorder plan](run-a-reorder-plan.md)
* [Upload the data a reorder plan is built from](upload-plan-data.md)
* [Low stock report](low-stock-report.md)
* [Order inquiries: the Documents view and the OI detail page](order-inquiry-documents.md)
