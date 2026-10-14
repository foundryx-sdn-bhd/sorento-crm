# CARD: deliveries and cancellations under each SO line (SO-DELIVERIES-VIEW)

Owner ask (14 Oct): on the Sales Order screen, show for each SO line the DO lines delivered
against it and the Cancel SO lines that cancelled it. Owner rules carried in: never a guessed
line (DO-HISTORY 13 + 14 Oct); Cancel SO groups on SO + item, location dropped (Q2 a, 14 Oct);
every failure ends in a clear state, no spinner left running.

Dev figures: `sorento_cagent_stack`, read-only, 14 Oct. Prod: not read (owner rule).

## Today (main, measured)

1. No screen shows DOs under an SO line. The Lines tab has per-line figures only: Qty delivered,
   Qty cancelled with a tooltip naming the XS documents, Outstanding
   (`SalesOrderDetail.tsx:830-890`). The Delivery tab is one sentence about the whole order
   (`SalesOrderDetail.tsx:2057-2087`).
2. Qty delivered on the line is AutoCount `TransferedQty` from the SO sync, less
   `qty_cancelled` (`sales_order_service.py:458-500`). It is not added up from DOs, so the line
   figure and the DOs can disagree when one sync is ahead of the other.
3. DO line to SO line: `order_lines.sales_order_line_id`. The ingest links by line key, or by
   "the one line of this product on the SO numbered `FromDocNo`"
   (`autocount_doc_ingest_service.py:889-908`). Every DO line keeps `from_doc_no` + `product_id`
   either way: the SO + product group (DO-HISTORY-L AC-DH22).
4. Dev holds **0** stored links. All 20,154 DO lines that name an SO carry `from_dtl_key = 0`,
   written before `_link_key` existed (`autocount_doc_ingest_service.py:193`). The resolver
   took the key path and found nothing. They get linked when re-ingested (#1633 + the SS
   backfill). Prod state: UNVERIFIED.
5. Cancel SO line to SO line: `autocount_cancel_lines.sales_order_line_id` with
   `match_status` / `match_reason` (`autocount_cancel.py:78-117`):
   - `line_key` / `item`: exact line;
   - `group_whole`: every line of the item, only per-line totals known
     (`autocount_cancel_matching.py:16-17`);
   - `undetermined`: no line, kept at SO + item (`:18-19`).
   `cancel_sources` already lists the matched ones per line (`autocount_cancel_ingest_service.py:1123-1153`).

Dev split of the 20,154 DO lines that name an SO:

| SO + product on the SO | DO lines | Shows |
| --- | --- | --- |
| One line of the product | 9,828 | under that line. 570 of them shipped from another warehouse |
| Several lines of the product | 4,694 | under the product: "not tied to one line" |
| Product not on the SO | 771 | nowhere (Q5). 725 are 0-qty marker lines (PP, **REPLACE) |
| SO not in the CRM | 4,861 | nowhere: there is no SO screen to show them on |

## Rules (proposed)

- **R1 Exact, under the line.** A DO line shows under SO line L when its stored link is L, or
  when it names this SO + product and L is the SO's only line of that product. This is the
  ingest's own rule (fact 3), so dev's unlinked rows (fact 4) read the same as linked ones will.
  A Cancel SO line shows under L when it is matched to L (`line_key` / `item`), or when it is
  `group_whole` with L among its lines; its qty is then "-", as in today's tooltip
  (`autocountCancel.ts:22-24`).
- **R2 Group, under the product.** When the SO has several lines of the product, a DO line
  without a stored link, and an `undetermined` Cancel SO line naming this SO + item, show in a
  block "For <SKU> on this SO, not tied to one line". No warehouse, qty or date pick.
- **R3 DO row.** DO no. (opens the DO), DO date, Location, Status pill (the DO's own status,
  as on the DO list, `OrdersList.tsx:285-291`), Actual delivery (date or "-",
  `OrdersList.tsx:225-227`), Qty. Oldest first, with a total row.
- **R4 Cancel SO row.** XS no., Date, Location, Matched by (line key / item / every line of
  the item / not tied to one line), Qty. Oldest first, with a total row.
- **R5 Left out.** Voided DOs: `is_cancelled`, AutoCount DocStatus V, 90 in dev; they delivered
  nothing. Voided or vanished Cancel SO documents: the same `_live_doc` rule as today
  (`autocount_cancel_ingest_service.py:262`). DO lines of a deleted DO.
- **R6 Scope.** DO and cancel lines are read in the SO's own company only.
- **R7 Clear states.** While loading: grey placeholder rows. Nothing: "No DO in the CRM for this
  line." / "No Cancel SO for this line." Error: "Could not load deliveries." with Try again.

## Real examples (dev)

1. **SO419866**, EUROTAN ENTERPRISE SDN BHD [A/C III]. Exact links, part delivered.
   - WESERP10B ordered 43, delivered 31, BRW, its only line.
   - 5 DOs show under the line (202609-1082 8, 202609-1101 3, 202609-1978 6, 202609-2849 11,
     202609-2865 3), total 31, which matches the line.
   - Each DO is "Picked Up / In Transit" with an actual delivery date of 7 to 18 Sep.
2. **SO418699**, TOKOMA BATH SDN BHD [A/C III]. Exact link at another warehouse.
   - CWC7601-S-ECO: one line at BRW, ordered 150, delivered 100.
   - DO 202609-0031 shipped 100 from **MWH**. It still shows under the line (one line of the
     product).
   - DO 202610-0114 (50, BRW, New Order, no actual date) shows too. The DOs total 150 against
     the line's 100: SO sync lag in dev, both figures shown as they are.
3. **SO373923**, HAANAFLO SDN BHD (PROJECT). Group DOs, exact and group Cancel SO. C-FH14 has
   3 lines (36 open, 12 closed, 84 cancelled):
   - group block: DO PS202609-0054 12 and XS-016501 84 (undetermined);
   - under no single line.
   - CB110 (one line, 36): XS-016501 36 shows under it (`item`).
   - TPE-9201-300 (2 lines, BRW-BB): DO PS202609-0054 4 from BRW shows in the group block.
4. **SO414796**, LEN MIN STEEL SDN BHD (PROJECT). Group DOs at another warehouse, an undelivered DO.
   - SRTFP4002 has 4 lines at BRW-BB. DO PS202609-0353 lines 4 + 2 from BRW go in the group
     block, total 6.
   - SRT384-6 (2 lines): DO PS202610-0029 28, New Order, not delivered yet, in the group block.
   - XS-016506 SRTSP224 56 (undetermined, 2 lines) is in the group block too.
5. **SO396482**, YI HONG TILING SDN BHD [A/C III], dated 27 Mar (pre-July). Delivered with no DO,
   and a group cancel.
   - All 9 lines show delivered but no DO in the CRM: "No DO in the CRM for this line."
   - WESERP10B has 2 lines (17/17 closed, 17/15 open). XS-016507 2 (undetermined) goes in the
     group block, never onto the open line.

## Edge cases

- **Pre-July lines.** 447,541 of 448,829 pre-July SO + product groups with a delivered qty have
  no DO in dev, because dev's AutoCount DOs start 1 Sep 2026.
  - The 78,374 Excel-era DO lines carry no SO number at all, so they can never show under an SO.
  - After the SS backfill from 2023-09-01, assumed-delivered DOs (#1633 C2) read status
    Delivered, Actual delivery "-".
- **Undetermined cancel groups:** rule R2, never split.
- **Other warehouse:** a DO's location is shown, never used to pick a line (AC-DH23 / AC-DH26).
- **DO total vs line figure.** Both are shown as they are; nothing is computed to make them meet
  (fact 2). In dev, 871 post-July groups have more on DOs than the SO sync says was delivered
  (sync lag).

## Questions

- **Q1 Where.**
  - (a) Lines tab: a chevron on each line opens its history in place. This is the existing
    expand pattern (`orderInquiryHeaderLinesColumns.tsx:415-462`, DataGrid
    `meta.expandedContent` `data-grid.tsx:21`).
  - (b) Delivery tab: one list for the whole order, grouped by product.
  - **Recommend (a).** The ask is "under each SO line", and the line's own figures sit right
    above the history. The Delivery tab keeps its summary.
- **Q2 Group block repeat.** The product has several lines.
  - (a) Show the group block under each of them, labelled with the line count.
  - (b) Show it once, under the product's first line.
  - **Recommend (a).** Whichever line is opened, its product's untied DOs are visible, and the
    label stops anyone reading them as that line's.
- **Q3 Delivered with no DO.**
  - (a) Empty text "No DO in the CRM for this line"; the line's Qty delivered stays as it is.
  - (b) Add a computed row "N delivered with no DO in the CRM".
  - **Recommend (a).** The two syncs disagree both ways (edge case 4), so a computed gap would
    often be wrong; for a group it would also need cross-line arithmetic.
- **Q4 Who sees DO rows.**
  - (a) Anyone who can open the SO (`scm.dashboard.view`, `scm/sales_orders.py:177`). The DO
    no. opens the DO only with the DO view permission; otherwise it is plain text.
  - (b) The whole history only with the DO view permission.
  - **Recommend (a).** The line already shows the delivered qty to the same people.
- **Q5 DO lines whose product is not on the SO.** 771 in dev, 46 with qty, for example CSK14B 51
  on SO417636.
  - (a) Leave them out.
  - (b) Show an order-level block "On this order's DOs, product not on the order".
  - **Recommend (a)** for now. Trigger for (b): the owner meets a substitution he expected to
    see.

## Build notes (no owner decision)

- **Base: main.** Every column read here is already on main. Nothing depends on #1633 or
  DO-HISTORY-L's `claude/do-history-group-links`; both keep the same columns.
- **Data.** One lazy read, `GET /api/v1/scm/sales-orders/{id}/deliveries`, fetched once when the
  first line is expanded. Lines are keyed by SO line, groups by product.
- **One additive index:** `order_lines (company_id, from_doc_no)`. There is none today, so a
  lookup scans the table: 6.2 s on dev at load 24, UNVERIFIED on an idle machine. Prod row count
  after the backfill: UNVERIFIED.
  - Migration plus idempotent `CREATE INDEX IF NOT EXISTS` SQL for crew.
  - With a migration this runs on the L-track steps: dev-copy check, security-reviewer for the
    permission rule.
