# Browser evidence: order inquiry sheet as a migration tool

Lane `feat/oi-sheet-migration`, head c8736604a. Walked 14 Sep 2026 by the tester agent with
agent-browser 0.27.0 (headless, session `oism`) against the lane stack: frontend
http://localhost:3083 (Next dev, HMR), backend http://localhost:8083, RQ worker on the
`imports` queue, all three on the lane database `sorento_oism_ci`.

Navigation was by sidebar click from `http://localhost:3083/` every time
(Procurement > Supply Chain > Reorder Planning, and Procurement > Supply Chain > Order
Inquiries). The only deep reads were the two read-only checks at the end.

Three screenshots are kept beside this document, and the walk was wider than three: the
preview panel at 1280 (`01-preview-1280.png`), the worklist the rows land on at 1280
(`03-order-inquiries-1280.png`), and that same worklist at 375
(`06-order-inquiries-375.png`), which is the DoD's own both-widths requirement. The shots
of the job page, the two backing-document dialogs and the re-upload preview were read
during the run and are not carried: every one of them is a verdict in the table below, and
a repository does not need seven screenshots of one upload to hold the answer.

## What was seeded

One company (`Sorento`, the database's only one) and its only user as the uploader. Every
human key carries the `OISM-` prefix so nothing borrows an existing row.

| thing | key | shape |
| --- | --- | --- |
| Warehouse | `OISM-W1` | company scoped, active |
| Products | `OISM-ITEM-A`, `OISM-ITEM-B`, `OISM-ITEM-C` | own category `OISM-CAT1`, own uom |
| Sales order | `OISM-SO-0001` | `source_system=autocount`, `demand_class=project`, status open |
| L1 | `OISM-ITEM-A` | qty 50, open, W1, required 2026-10-15 |
| L2 | `OISM-ITEM-B` | qty 20, open, W1, required 2026-10-20 |
| L3 | `OISM-ITEM-C` | qty 10, CLOSED, fully delivered, W1, no required date |
| PO A | `OISM-202610-S0001` | line for item A, qty 100, open, W1 |
| PO B | `OISM-202610-S0002` | line for item B, qty 20, open, W1 |
| Claim | `scm.order_link_claim` | source `autocount`, RESOLVED, L1 to PO A's line |

Sheet `OISM-order-inquiry.xlsx`, header `SO NO | ITEM CODE | QTY | DELIVERY DATE | STOCK
LOCATION | REMARK`:

* tab `JAN26`: (SO, item A, 30, 2026-10-15, W1, no remark), (SO, item B, 20, 2026-10-20,
  W1, remark = PO B's number), (SO, item C, 10, no date, W1, no remark),
  (`OISM-NOPE-1`, item A, 5, 2026-10-15, W1, no remark).
* tab `ROLLUP`: the first row repeated exactly.

## Result per criterion

| AC | verdict | evidence |
| --- | --- | --- |
| Journey 1 to 3 | PASS | Reorder Planning reached by sidebar, Actions > "Upload order inquiry sheet", file chosen, Test, Confirm. `POST /api/v1/scm/order-inquiry/preview` 200, `POST /api/v1/scm/order-inquiry/apply` 202, the drawer showed "Finished just now, 5 rows, 1 skipped". `01-preview-1280.png` |
| Journey 4 | PASS | Order Inquiries shows the three rows acknowledged with the sheet's qty, delivery date and location, rows A and B backed by their document. `03-order-inquiries-1280.png` |
| Journey 5 | PASS | Re-upload preview: Will raise 0, Already raised 3. |
| AC-S2-1 | PASS | Seven tiles in order: Rows 5, Will raise 3, Already raised 0, No SO line 0, Orders adopted 1, Rows linked 2, Documents not found 0. No scheduled-deliveries, matched-lines, PO-links or not-ordered tile. `01-preview-1280.png` |
| AC-S2-2 | PASS | "Sales orders not in the CRM" listed `OISM-NOPE-1` with no count in the heading. "Documents we could not link" was absent, not an empty box. `01-preview-1280.png` |
| AC-S2-3 | PASS | With a real mismatch present (the first run before the seeded products were company scoped) the panel rendered "Rows with no matching line (3)" as `SO . item . qty . reason`, reason text "No sales order line for this item", the same sentence the job page prints. On the final clean run the list is correctly absent. |
| AC-S2-4 | PASS | Confirm enabled on the first preview (ok, rows_raised 3), disabled on the re-upload preview (rows_raised 0). |
| AC-S2-5 | PASS | The job's "Full result (JSON)" carries exactly the 17 keys of AC-S1-22 and none of the retired ones. |
| AC-S2-6 | PASS | The upload was driven through the dialog from the sidebar and Order Inquiries read at 1280 and at 375. `03-order-inquiries-1280.png`, `06-order-inquiries-375.png`; no page level horizontal overflow at 375 (scrollWidth 375, clientWidth 375) |
| AC-S2-7 | PASS | No new motion: the tiles and lists appear with the dialog's existing preset, nothing animates in. |
| AC-S2-8 | PASS | "Orders adopted" tile reads 1 on the first preview and 0 on the re-upload. `orders_stamped` appears on the job result only. |
| AC-S1-1 | PASS | Rows for items A, B and C all raised against their line, with the sheet's qty (30, 20, 10), delivery date and location `OISM-W1`. |
| AC-S1-6 | PASS | `OISM-NOPE-1` created nothing and is listed under "Sales orders not in the CRM"; job row outcome `skipped` / `order_not_found`. `sales_orders` still holds no row with `source_system=scm_order_inquiry`. |
| AC-S1-10, AC-S1-11 | PASS | Second preview of the same file: Will raise 0, Already raised 3, Orders adopted 0, Confirm disabled. |
| AC-S1-12 | PASS | Row B, remark citing PO B, is `placed` on `OISM-202610-S0002` for the full 20. |
| AC-S1-28 | PASS | Every raised row's note starts `Migrated from order inquiry sheet OISM-order-inquiry.xlsx`. |
| AC-S1-29 | PASS | Row C, on the closed and fully delivered line, is `state=actioned` with `actioned_by` set, its selection checkbox disabled on the worklist, and `scm.committed_v` holds no row for any OISM product. |
| AC-S1-30 | PASS | Row A, with no remark, is linked to the AutoCount claim's target PO A first, and its note ends `auto: autocount linkage`. |
| AC-S1-38 (D7) | PASS | The ROLLUP repeat of row 1 recorded outcome `unchanged` with code `restates_an_instalment` and raised nothing. |

## Read-only checks at the end

* `GET http://localhost:8083/docs` returns 200.
* `select count(*) from projects.order_inquiry_rows where item_code like 'OISM%'` returns 3.
* `select count(*) from sales_orders where source_system='scm_order_inquiry'` returns 0
  (AC-S1-19: the importer creates no sales order).
* `scm.order_link_claim` for `OISM-SO-0001`: two rows, the seeded `autocount` one and one
  `order_inquiry` claim written by the link itself for row B (AC-S1-21).
* `projects.sales_order_lines` for the adopted record: three mirror lines, items A, B and C,
  qty equal to the core ordered quantity (AC-S1-26, the matched closed line is mirrored).

## Console and network

No page errors (`errors` empty) at any step. Console carried only Next dev noise: Fast
Refresh lines, the React DevTools notice, `i18next: languageChanged en-GB` and the api
client's own debug lines. No 4xx or 5xx on any `/api/v1/` call; the feature's calls were
`POST /api/v1/scm/order-inquiry/preview` 200 and `POST /api/v1/scm/order-inquiry/apply` 202.
