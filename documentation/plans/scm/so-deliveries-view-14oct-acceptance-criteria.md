# UAC: deliveries and cancellations under each SO line (SO-DELIVERIES-VIEW)

Plan: `PLAN-so-deliveries-view-14oct.md`. Card: `CARD-so-deliveries-view-14oct.md` (owner answers
14 Oct: Q1-Q5 all (a)). Mock: `documentation/mockups/SO-DELIVERIES-VIEW/index.html`.

## Read: `GET /api/v1/scm/sales-orders/{so_id}/deliveries`

- **AC-SD1 Exact by the one line.** A live DO line naming this SO (`from_doc_no` = SO number,
  `from_doc_type` SO or empty) and a product that sits on exactly ONE line of the SO is returned
  under that line, with or without a stored `sales_order_line_id`. The DO's warehouse plays no
  part: a DO line from another warehouse is still under the one line.
- **AC-SD2 Exact by stored link.** A DO line whose `sales_order_line_id` is one of this SO's
  lines is returned under that line, even when the product sits on several lines.
- **AC-SD3 Group.** A DO line naming this SO and a product on SEVERAL lines of the SO, with no
  stored link, is returned once in that product's group (`sku`, `line_ids` = every line of the
  product on the SO). It is never placed under one of the lines, whatever its warehouse, qty or
  date.
- **AC-SD4 DO row fields.** For each row: DO id (for the link only), DO number, DO date,
  location code, the DO's status name, actual delivery date (null when none) and qty. Rows are
  ordered by DO date, then DO number, then line sequence.
- **AC-SD5 DO lines left out.**
  - The DO is voided (`is_cancelled`).
  - The DO is deleted (`deleted_at`).
  - The product is not on the SO (owner Q5 a).
  - The line belongs to another company, even with the same SO number.
  - It names another SO.
- **AC-SD6 Cancel SO exact.** A live Cancel SO line (kind `cancel_so`, document not void and not
  vanished) that is `matched` to one of this SO's lines (`line_key` / `item`) is returned under
  that line with its qty and `match_reason`.
- **AC-SD7 Cancel SO every line.** A `matched` `group_whole` Cancel SO line is returned under
  EACH of its candidate lines that belong to this SO, with qty null (only the line totals are
  known), `match_reason` `group_whole`.
- **AC-SD8 Cancel SO group.** An `unmatched` `undetermined` Cancel SO line naming this SO and an
  item that is on several lines of the SO is returned in that product's group, with qty and
  `match_reason` `undetermined`.
- **AC-SD9 Cancel rows left out.**
  - Void or vanished cancel documents.
  - Cancel PO, consignment and consignment-return kinds.
  - `waiting` lines.
  - `item_not_on_order` lines.
  - Other companies.

  Cancel rows carry: document number, document date, location code, `match_reason`, qty. They
  are ordered by date, then document number, then seq.
- **AC-SD10 Shape.**
  - Every SO line appears in `lines`, with empty lists when nothing is found.
  - A product with several lines appears in `groups` with its `line_ids` even when its lists
    are empty.
  - A product with one line never appears in `groups`.
  - The response carries no SO line, product or DO UUID beyond the ids the screen needs to key
    rows and build the DO link.
- **AC-SD11 Access.**
  - The route is gated like the SO read (`scm.dashboard.view`).
  - An unknown SO id, or an SO outside the caller's company scope, answers 404 `SO_NOT_FOUND`.
- **AC-SD12 Index.** Migration adds `ix_order_lines_company_from_doc_no` on
  `order_lines (company_id, from_doc_no)`, built CONCURRENTLY with a bounded lock wait and safe
  to re-run (precedent `alembic/versions/mig_idx_chunks_document_id.py`). One alembic head.

## Screen: SO detail, Lines tab

- **AC-SD20 Truck icon (owner, mock v1 note 14 Oct: "lightbox popup, opened by an icon").**
  - Every line row has an icon-only button with a truck icon as the first column, in view and in
    edit alike.
  - The column has an empty header and cannot be hidden.
  - The button's aria-label and title read "Deliveries for <SKU>".
  - Clicking it opens a lightbox titled "Deliveries · <SKU>"; the close X, Escape and the overlay
    close it.
  - At the top of the lightbox body, a strip of the line's own figures: Ordered, Delivered,
    Cancelled, Outstanding, Location, Delivery date. They are the same values as the grid row, so
    for a product on several lines it is clear which line was opened.
- **AC-SD21 One fetch.**
  - The deliveries read is fetched once, the first time any line's lightbox opens.
  - Opening another line's lightbox, or the same one again, does not fetch again.
- **AC-SD22 Delivered block.** Heading "Delivered" with "<n> DO line(s)", then a table:
  - columns DO no., DO date, Location, Status, Actual delivery, Qty;
  - a Total row summing Qty.
  - Status is the DO's own status pill, the same `Badge status=` as the DO list.
  - Dates read dd/mm/yyyy; an empty actual delivery reads "-".
  - With no rows the block reads "No DO in the CRM for this line." (owner Q3 a).
- **AC-SD23 Cancelled block.** Heading "Cancelled" with "<n> Cancel SO line(s)", then a table:
  - columns Cancel SO no., Date, Location, Matched by, Qty;
  - a Total row summing the known quantities.
  - Matched by reads: line key / item / every line of the item / not tied to one line.
  - A `group_whole` qty reads "-".
  - With no rows: "No Cancel SO for this line."
- **AC-SD24 Group block.** When the line's product sits on several lines of the SO:
  - below the two blocks sits a bordered block headed "<SKU> on this SO, not tied to one line"
    with "· <n> lines";
  - it holds the same two tables with the group's rows, or "No DO in the CRM for this product
    here." / "No Cancel SO for this product here.";
  - it shows in the lightbox of EVERY line of that product (owner Q2 a).
  - Products on one line show no group block.
- **AC-SD25 DO link and permission.**
  - With `order_management.orders.view` the DO number links to `/order-management/orders/{id}`.
  - Without it the number is plain text and the row still shows (owner Q4 a).
- **AC-SD26 Clear states.**
  - While loading, the lightbox shows skeleton rows.
  - On error it shows "Could not load deliveries." with a Try again button that refetches.
  - No state leaves a spinner running.
- **AC-SD27 Layout.**
  - The lightbox is at most 85% of the viewport height, and its body scrolls.
  - Tables wider than the lightbox scroll sideways inside their own frame.
  - Usable, nothing clipped, at 1280 and 375.
- **AC-SD28 No UUIDs on screen.** No SO line, product, DO or cancel id is rendered as text.
- **AC-SD29 Column preference.** A user with a saved Lines column preference (`-v3`) still gets
  the truck column as the first column.
