# Supply Chain - Order inquiry handover email to purchasing

During the parallel run, every time customer service raises, changes or cancels an order
inquiry row in one write, purchasing gets an email shaped like the manual mail CS used to type
by hand. This explains what's in it, what sends it, and how to manage it.

## What purchasing receives

One email per customer-service write - one email per raise, per settle, or per cancel, not one
per line inside it.

**Subject:** `OI: <stock location> @ <S/O list>` when every line shares one stock location, for
example `OI: BRW-BB @ SO397450 , SO397460`. Across several locations the subject drops the
location: `OI: SO397450 , SO397460`.

**Body**, top to bottom:

1. A red headline naming the verb(s) in the mail: **ORDER**, **RESERVE & ORDER**, **ORDER
   BACK**, **PRE-ORDERED**, **ALREADY INBOUND**, **ADVANCE**, **DELAY**, **CHANGE SO NO**,
   **CANCEL BALANCE**.
2. A sales-order table: **S/O NO**, **SO DATE**, **CUSTOMER**, **PROJECT**. This is the only
   place **CUSTOMER** and **PROJECT** appear.
3. A line table: **LOCATION**, **ITEM CODE**, **NEW ITEM CODE**, **QTY**, **NEW QTY**,
   **DELIVERY**, **NEW DATE**. When the mail covers more than one sales order, a grey
   **S/O NO** row opens each order's lines.
4. When any line carries files, one **Attachments:** line under the table names them by item
   code.
5. "Raised by \<name\> (\<email\>) on \<date\>."
6. A link, **Open in Order Inquiries**, that opens the order inquiry's own detail page - see
   [Order inquiries: the Documents view and the OI detail
   page](order-inquiry-documents.md). When the mail covers more than one sales order, the link
   opens the first order inquiry named in it.

There is no REMARK column. The numbers say what moved, and the red headline lists the
actions in the mail.

**How a change reads.** **ITEM CODE**, **QTY** and **DELIVERY** always print what the line
was. When one of them moved, the new value prints in the **NEW** column next to it. A
**NEW** column only appears when some line in that mail moved that value, and its cell is
filled only on the lines that moved. Every date in the mail reads `dd/mm/yyyy`.

* A quantity that moved 182 to 214 reads **QTY 182**, **NEW QTY 214**.
* A date that moved reads **DELIVERY 01/09/2026**, **NEW DATE 01/04/2027**. A later new date
  is a delay, an earlier one an advance.
* A cancelled line reads its old quantity in **QTY** and **0** in **NEW QTY**.
* A product change reads the old code in **ITEM CODE** and the new one in **NEW ITEM CODE**.
* A plain new line prints **QTY** and **DELIVERY** only, with every **NEW** cell blank.

The before values are the same ones the Order Inquiries Was/Now dialog shows for the row. A
line whose received supply the change sends back to the pool (its fresh row carries
"Replaces N used" in Order Inquiries) prints its own move the same way, for example
**DELIVERY 01/10/2026**, **NEW DATE 02/11/2026**.

**One line per sales order line, in line totals.** When a planning change moves a line whose
existing order inquiry row purchasing already placed (or a line carrying two open rows), the row
keeps its own quantity in Order Inquiries and the extra quantity is raised as its own ORDER
row there. The mail still prints ONE line for it: **QTY** is what purchasing already held for
the line (its open rows added up) and **NEW QTY** is the line's new Buy, for example **QTY
172**, **NEW QTY 436** for a line that moved 172 to 436. No separate line is printed for
that same line.

**Width.** The handover mail uses the print card (672px), sized to one A4 portrait page.
Codes, quantities and dates never wrap. The width is a setting on the template in **System Management → Email
Templates** (**Width**); every other mail stays on the standard 600px card.

**How an item swap reads.** Swapping the product on a line that has no row to restate prints
as two lines: the old item with **NEW QTY 0**, and the new item as a plain new line.

A line whose item code, quantity and delivery date are unchanged from the last mail never
appears - only what actually moved or was cancelled is printed. Confirming a second time with
nothing left to change sends no mail at all, not an empty one.

**Still-open amendment instructions.** If the order also carries amendment rows raised by an SO
change (DELAY, ADVANCE, CANCEL BALANCE, CHANGE SO) that purchasing has not yet actioned, they're
listed in the same line table, after this Confirm's own lines, so purchasing reads one page per
Confirm instead of having to remember an earlier mail. An order with nothing still open there
adds nothing.

## What sends it, and what doesn't

Sends one mail:

* Confirming lines on the fulfilment board.
* Applying a planning change, including **Confirm** (confirm-all).
* An SO amendment (editing a sales order's lines and clicking **Save sales order**).
* Re-uploading the sales order book (**Upload sales orders** on Reorder Planning).

Never sends anything:

* Purchasing's own actions on **Order Inquiries** - **Acknowledge**, **Reject** / **Reject
  selected**, **Link now** / **Auto-link**, **Unlink** / **Unlink selected**.
* **Upload order inquiry sheet** on Reorder Planning (the migration importer).

## How to manage it

Open **[System Management → Automation](/system-management/automation)** and edit the row
named **Order inquiry to purchasing**.

* **Recipients** - pick **Specific users**, **By role**, or type an address under **External
  emails** and click **Add** (for example `purchasing@` and the CS manager's own address).
* **Cc the person who raised it** - keeps the CS admin who made the change on the thread.
* **One email, everyone on the thread** - sends a single mail with the first purchasing address
  in **To** and everyone else, including the person who raised it, in **Cc**, so a reply-all
  reaches the whole thread instead of landing back with only one recipient.
* **Enabled** - untick this to stop the mail once the manual email is retired. Nothing else
  needs to change.

## Parallel run

While the parallel run is on, CS keeps sending the manual mail as well. Compare the two on the
same thread and report any mismatch between them.

## Not yet built

Two cases still print as separate cancel-and-order lines rather than one combined line:

* Moving lines to a different sales order number (**CHANGE SO NO** across two sales orders).
* Replacing one item for another on a single line.

Both arrive as a cancelled line (**NEW QTY 0**) and a plain new line, the same as any other
item swap, until a dedicated combined line is built.

## See also

* [Order inquiries: the Documents view and the OI detail page](order-inquiry-documents.md) - what
  the mail's link opens.
* [Reserve request and reserved-stock emails](order-inquiry-reserve-email.md) - the separate
  emails a "Request CS to reserve" exchange sends.
* [Sales order changes after planning](sales-order-changes.md) - the in-app notification
  purchasing gets once a change is confirmed or amended, separate from this email.
* [Buy and borrow decisions on Fulfilment Planning](local-buy-and-borrow-source.md#undo-last-confirm)
  - undoing a Confirm, which sends purchasing its own "Order inquiry undone" email instead of
  this one.
* [Run a reorder plan](run-a-reorder-plan.md)
* [Upload the data a reorder plan is built from](upload-plan-data.md)
