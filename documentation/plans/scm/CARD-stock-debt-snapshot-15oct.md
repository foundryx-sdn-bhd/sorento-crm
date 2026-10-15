# CARD: Stock Debt snapshots, decided on paper, acted on later (STOCK-DEBT-SNAPSHOT)

Owner ask (15 Oct): print the Stock Debt view for management, management marks which products to
reserve (for example "BRW" in the Total column = reserve from BRW) and which to buy, then go to the
OI of each product to request CS to reserve. A week later the live board has moved under the new
sales orders, so the paper no longer matches the screen. Wanted: a snapshot that remembers the
sales orders behind each figure and opens their SO / OI directly.

Dev figures: `sorento_cagent_stack`, read-only, 15 Oct, through the real `StockDebtService`.
Prod: not read (owner rule).

## Today (main 01e275d4c, measured)

1. The board is `StockDebtService.list()` (`stock_debt_service.py:349-508`): one assignment walk
   (`_assignments`, `:385`), one row per product, the month cells, TBA, Total and four stock/PO
   columns. Nothing is stored; every load recomputes.
2. A cell drill is `cell()` (`stock_debt_service.py:547-866`): the SAME walk narrowed to one
   product. Its demand rows (`:653-685`) carry SO number, agent, bin, due, ordered, delivered,
   outstanding, assigned, covered by, status, short. Each row is one core
   `sales_order_lines` row (`DemandLine.key`, `:1534`).
3. SO line to OI: `order_inquiry_rows.so_line_id` points at `projects.sales_order_lines`
   (`models/project_so.py:1011-1014`), whose `core_sales_order_line_id` is the core line. The
   drill never shows an OI today (FE drill `StockDebtCellDialog.tsx:316-455` has no OI column).
4. Request CS to reserve lives on the OI page only: menu item at `OrderInquiryDetail.tsx:812-822`,
   dialog `ReserveRequestDialog.tsx:60-73` (presentational: the caller injects `onSend`), write
   `POST /order-inquiries/{id}/reserve-requests` (`order_inquiries.py:1210-1246`). A row can be
   requested only when verb is ORDER / ORDER BACK, state raised / partly linked, no open request,
   and something remains (`order_inquiry_reserve_service.py:65-66, 475-500`).
5. Print: Stock Debt has no print button or print CSS (grep). The paper today is the Export
   workbook (`StockDebtExportPopover.tsx:48-73`, rendered by `_render_workbook`,
   `stock_debt_service.py:2677`).
6. Saved views (#1683) store FILTERS only (`saved_views` table, key
   `projects.stock_debt.view::stock-debt`, `stockDebtSavedView.ts:11`). They re-run the live board;
   they are not a snapshot.

Dev, owner's view (Book Project, delivery 01/01/2023 to 31/05/2027, only in debt, stock and PO
columns on; BRW-ED and PRJ-ED do not exist in dev, so all locations): **550 products, 8 month
columns (Oct 2026 to May 2027), about 11,000 SO lines behind them, 5,054 of those lines on 322
OIs**. The live read took 5.7 s. Size measured at 409 bytes per stored SO line, so about 5 MB per
snapshot of this view (whole-set size UNVERIFIED).

## Rules

1. **Save snapshot** (button beside Export on Stock Debt): asks for a name (default
   "Stock Debt 15/10/2026"), then stores, under the board's CURRENT filters (book, location
   only / all except, supplier, delivery dates, only in debt, projected, stock and PO columns,
   search):
   - the filters (with location and supplier names as text, so a later rename does not change
     them), the month columns, every row with every figure, the totals;
   - for every row, every SO line behind every cell, exactly as the drill shows it today
     (same fields as `stock_debt_service.py:653-685`), from the SAME walk as the rows, so they
     foot;
   - for every SO line, the OI rows linked to it at that moment (OI number, row, qty, state);
   - name, who, when.
   Same 5,000-row cap as Export (`low_stock_report_service.py:69`): above it, "Narrow the
   filters first". A snapshot is never recomputed.
2. **Snapshots list**: a "Snapshots" button on Stock Debt opens `/project-sales/stock-debt/snapshots`
   (Name, Filters, Products, Decided, Created by, Created at). No new sidebar entry.
3. **Open a snapshot**: the Stock Debt grid, read-only, as it was, with "Taken 15 Oct 2026 11:53
   by <name>" and the filters it was taken under. Search by product code / name over the stored
   rows. Export gives today's workbook (same split choices) from the stored rows, plus the
   Decision and Note columns.
4. **Decision per product** (Q1): Reserve (from a pool: BRW-BB, BRW-IB, ...) / Buy / Ignore, plus a
   note. Stored on the snapshot row with who and when; the snapshot list counts decided rows.
5. **Drill on a snapshot row**: pressing a month cell shows that cell's frozen SO lines (the
   drill's own columns), pressing the product shows all of its lines. Two columns are added:
   - **OI**: the OI number(s) frozen with the line, linking to the OI page on that row
     (`/project-sales/order-inquiries/<id>?row=<row>`, the deep link the SO page already uses,
     `SalesOrderDetail.tsx:1258`);
   - **Now** (read live when the drill opens): SO line open / delivered / cancelled / closed, and
     the OI row state (Raised, Placed, Reserve requested, Reserved, Cancelled).
6. **Request CS to reserve from the snapshot** (Q2): lines are grouped by OI; an OI with
   requestable rows (rule 4 of Today) gets "Request CS to reserve", which opens the OI page's own
   dialog with that OI's rows for this product, the location defaulting to the row's decision. It
   posts the existing reserve request; nothing new is written. Same permission as on the OI page.

## Examples (dev, the view above)

| # | Product | Snapshot shows | Decision a reader would mark | What the drill offers |
| - | --- | --- | --- | --- |
| 1 | SRTWT904-NL | Total -223 (Jan -30, Feb -27, Mar -56, Apr -56, May -54); BRW on hand 972 | Reserve from BRW-BB | 6 lines, all SO383872 LEENA BRW-BB, all on OI-2609-0417 (raised, acknowledged, no reserve). One "Request CS to reserve": 6 rows, remaining 30 / 27 / 28 / 28 / 28 / 54 |
| 2 | SRTWT5875 | Total -405 (Oct -30, Jan -375); BRW on hand 24 | Buy | SO391092 SHAHRUL BRW-NTC: Oct 30 on OI-2609-0271 row Placed (not requestable); Jan 375 on the same OI, row Raised, remaining 405 (ordered 405, delivered 30) |
| 3 | SRT23-CR | Oct -633; BRW on hand 0 | (already handled) | SO422078 TERA BRW-BB, OI-2609-0733: Now = Reserved. No request button |
| 4 | SRTWT8203 | Oct -2; BRW on hand 41 | Reserve from BRW-IB | SO387599 JUSTIN BRW-IB short 2 has NO OI: "No OI", SO link only. Its other 4 lines are covered / pinned |
| 5 | C-FH14 | Total -23,638 over 8 months | Buy | Many SOs on more than 10 OIs, some lines with no OI: the product search case |

## Edge cases

1. **SO cancelled or closed after the snapshot**: the line stays as frozen; Now reads "SO
   cancelled" / "SO closed"; no request button (the service refuses anyway, rule 4 of Today).
2. **OI already reserved** (example 3): Now shows "Reserved"; no button.
3. **Line had no OI when frozen** (example 4): OI column "No OI". If an OI has been raised for that
   line since, Now names it, "raised after the snapshot", with its link.
4. **OI row qty is not the SO line's** (SRTWT904-NL SO383872 01/03/2027: SO line 56 in the snapshot,
   OI row 28; example 2: short 375, OI remaining 405): the dialog's ceiling is the OI row's own
   remaining, never the snapshot figure. Both are shown.
5. **One SO line, several OI rows** (dev SO373653: rows 112 + 110 on OI-2609-0509): all listed.
6. **Product merged**: the app has no product merge (no merge path in the product services or
   models). A renamed or deactivated product keeps its frozen code and name in the snapshot; the
   links still work by id.
7. **Projected balance on when saved**: the month cells are the projected ones; the SO lines are
   the same (projection moves figures between months, not lines). The drill's projection
   breakdown is not frozen (Q3).
8. **More than 5,000 rows**: refused, "Narrow the filters first".

## Questions

1. **Decisions in v1?** (a) Yes: per product Reserve from <pool> / Buy / Ignore + note, on the
   snapshot, printed in the export. (b) No: v1 freezes and links; the paper stays the record.
   **Recommend (a)**: the paper marks are the decision the whole flow serves; it is a few columns
   on the snapshot row, no extra table.
2. **Request reserve from the snapshot?** (a) "Request CS to reserve" per OI inside the snapshot
   drill, reusing the OI page's dialog (`ReserveRequestDialog.tsx:60`) and its write
   (`order_inquiries.py:1210`), plus the Open OI link. (b) Open OI link only; request on the OI
   page. **Recommend (a)**: a product spans many OIs (C-FH14: more than 10), and one tab per OI is
   the trip the owner wants gone; the dialog takes its write from the caller, so this is reuse,
   not a new flow.
3. **What is frozen behind a cell?** (a) The SO lines and their OIs only; the snapshot drill has
   no Supply tab. (b) SO lines and supply (stock, SPO, PO). **Recommend (a)**: the ask is SO / OI;
   supply needs a per-product event read (`keep_events`, `stock_debt_service.py:594`) for every
   row, about double the size and time; the reserve acts on today's stock, which Now and the
   dialog's live pool stock already show.
4. **Who sees, saves, deletes?** (a) Everyone with Stock Debt access sees every snapshot of the
   company, can save one and mark decisions; only its creator deletes it (the usual 10 s
   countdown, no confirm). (b) Snapshots are private to their creator. **Recommend (a)**:
   purchasing and management share one record; no new permission.
5. **Keep for how long?** (a) Until deleted. (b) Auto-delete after N days. **Recommend (a)**:
   simplest; about 5 MB each for the widest dev view; revisit if the count grows.
