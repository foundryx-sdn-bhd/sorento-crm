# UAC - Chatbot sales report (confirmed vs outstanding sales, by month and product)

Plan: `PLAN-chatbot-sales-report.md`. Numbering: AC-16xx. Each criterion names its evidence
(pytest / golden fixture / console check). "Contact" = a Respond.io contact through
`/api/v1/external/chat/turn`. Built on origin/main's chatbot (the `crm_outstanding_report`
mechanism, #862). The turn re-architecture (#952, #863) is ON HOLD by owner ruling 19 Sep 2026
and this lane does not depend on it.

## Journey

Actor: a management or sales contact on WhatsApp who holds the sales report grant. They arrive
from the Respond.io channel with a dealer name (or a product code) in mind and want the sales
figures for it, month by month, then the orders behind them. The system already knows the
contact and its grants, its companies, every customer ledger, the product master, every
warehouse code and every sales order line AutoCount has pushed (quantity ordered, quantity
transferred to DO, unit price, discount, line total, required date).

1. They type "give me the dealer sales report for hanlim". The parser reads a sales report ask,
   the customer "hanlim" and the channel "dealer". If "hanlim" matches more than one company
   family the existing customer picker asks which one; "1" or "all" continues this same ask.
   No date in the message means all dates. Nothing else is asked.
2. One reply. Header: Customer / Product / Channel / Location / Required date (or order date), `all` on any
   axis nobody named. Then one block per month, latest month first. Each block: Sales orders,
   Ordered, Confirmed (DO), Outstanding, each money line as `RM value (Qty: n)`, then a
   By product list ranked by ordered value. Then one closing line: `Reply 1 for the sales
   order list.`
3. They type "1". Every matching sales order, one item per SO, latest first: SO number,
   customer, location, order date, ordered, confirmed, outstanding (value and quantity each).
   Every row is sent; n8n chunks long messages as it does today.
4. They type "only SRTWT7445" or "june 2026 only". The same report re-runs narrowed, the header
   shows the new filter, and the offer is re-armed over the narrower set.
5. They type "sales report for SRTWT7445" (product only). Same shape; the list under each
   month is By customer instead of By product. With no date named, this prints the current
   year, not all dates; "all dates" said in words gives everything (S18).
6. They type "project sales report for hanlim". Same report, `Channel: Project`, counting only
   sales orders classed project.
7. A contact without the grant asking any of the above gets one line, `Sales report is not
   enabled for your account.`, and nothing is fetched.

Other stakeholders: nobody is notified; this is a read.

## Measured (prod copy `sorento_ai_automation_0918_1900`, 19 Sep 2026)

- `sales_order_lines.line_total = qty_ordered * unit_price - discount` on 142,401 of 144,318
  lines dated 2026. This is the money source.
- `order_lines.total` (the DO table) is not usable as money: August 2026 sums to 136.3M
  against 23.2M for quantity times unit price, and single lines read 5 x 37.00 = 99.65.
  DO rows also only exist from April 2026. There is no FK from an SO line to a DO line.
  "Converted to DO" is `sales_order_lines.qty_delivered`, pushed by AutoCount.
- 2026 lines, cancelled excluded: no closed line has `qty_delivered < qty_ordered`, 5 lines
  have `qty_delivered > qty_ordered`, 5,428 lines have `qty_ordered = 0`. So
  Ordered = Confirmed + Outstanding holds once confirmed is capped at ordered.
- `sales_orders.requested_delivery_date` is filled on 2,459 of 149,383 SOs and on none since
  2025. `sales_order_lines.required_date` is filled on 143,118 of 144,318 lines dated 2026:
  114,751 equal the order date, 23,343 later, 5,024 earlier, 10,542 in the future (max
  10/02/2032). 972 SOs carry lines in more than one month.
- `sales_orders.demand_class`, 2026, cancelled excluded: retail 30,571, project 5,210, null
  170. `order_type` is null on all but 198. 11 customers hold both classes.
- Sales documents carry no currency column; base currency is MYR (`scm/money.py:28`).
- HANLIM: 6 ledgers, 1,230 SOs over 37 months, 1,021 products, 8 to 129 products a month
  (average 80), 5 warehouses, all retail.
- 2,254 SOs dated 2026 span two or more warehouses.

## Rulings (owner, grill 19 Sep 2026)

| # | Ruling |
|---|---|
| S1 | Money and quantity come from sales order lines only. The DO tables are not read. |
| S2 | Confirmed = the part of a line transferred to DO: `confirmed_qty = LEAST(qty_delivered, qty_ordered)`, `confirmed_value = line_total * confirmed_qty / qty_ordered` (0 when `qty_ordered` is 0). Outstanding = the rest of the line, counted only while `sales_orders.status = 'open'` AND `line_status = 'open'` (the outstanding report's own predicate). Ordered = Confirmed + Outstanding. Cancelled lines and cancelled SOs are never counted. |
| S3 | A line belongs to the month of its own `required_date`; a line with none falls back to `sales_orders.order_date`. Header line is `Required date (or order date):`. An SO with lines in two months counts once in each month's `Sales orders`. Future months print like any other. |
| S4 | No date in the message = all dates, printed `Required date (or order date): all`. |
| S5 | ONE shape: a block per month, latest first, each with its own breakdown list, always. Every row is sent, no "+N more". |
| S6 | Breakdown under a month: customer subject = By product; product subject = By customer; both named = no breakdown, UNLESS the product filter covers 2+ codes (S20 extends this - see S20). Ranked by ordered value descending, ties by ordered quantity descending, then name ascending. Sorted in the route; the presenter never re-sorts. |
| S7 | Subject rule: at least one of customer or product (422 `subject_required` otherwise). |
| S8 | Channel is a filter the PARSER reads: "dealer" = `demand_class = 'retail'`, "project" = `demand_class = 'project'`, neither word = all, including the null-class SOs, which appear under `all` only. Header line `Channel: Dealer / Project / all`. The outstanding report is unchanged: "dealer" binds nothing there. |
| S9 | Location filters like the outstanding report (same warehouse resolver, exact code or `-suffix`), header line `Location:` printed the way the outstanding header prints it: `IB (BRW-IB, MWH-IB)`. The route echoes `location_token` for that and never filters on it. |
| S10 | Access: one new per-contact field-reveal key `sales_orders.sales_report`, default deny, ticked on Contacts > Access. Without it: `Sales report is not enabled for your account.` and no fetch. Company scope per contact applies as on every route. |
| S11 | The detail offer reuses the sticky offer mechanism under its own kind `sales_report_detail`. No new arm in the head: the existing detail-offer arms accept the second kind. |
| S12 | The parser decides the ask (`order_status: "sales_report"`) and the channel. No word table in deterministic code. |
| S13 | Amounts print `RM 1,234.50` (two decimals, thousands separators). Quantities print as the outstanding report prints them. |
| S14 | The route re-checks the per-contact `sales_orders.sales_report` reveal key when `contact_id` is present (not just the lane's own gate, which a direct MCP/n8n caller bypasses): no grant is 403 `sales_report_not_enabled`. `contact_id` and `space_id` are both-or-neither on this route - one without the other is 422. |
| S15 | The pro-rated confirmed value rounds to the cent PER LINE, not once at the end of an aggregate; every total (month, breakdown, `so_rows`) is a SUM of those already-rounded cents. |
| S16 | A line with neither `required_date` nor its SO's `order_date` is excluded from EVERYTHING (no further fallback to `created_at`) - not just absent from `months[]` but from `so_rows` too, under every date window including none. |
| S17 | `customer_query` needs at least 3 characters (after strip) - shorter is 422. The report is aggregated in SQL, not rolled up from raw rows in Python. |
| S18 | A PRODUCT-ONLY ask (a resolved product, no customer) with no date window defaults to the CURRENT CALENDAR YEAR (Malaysia time); a customer ask, or a customer+product ask, with no date stays all dates. The default is built in the lane, never the route - `date_from`/`date_to` absent still means all dates when the route is called directly. "All dates" said in words turns the default off. |
| S19 | On the SALES REPORT, a product ask covers the typed code AND every product whose code STARTS WITH it (case-insensitive). The OUTSTANDING report keeps its exact-code rule (AC-1119), do not touch it. (Owner ruling from live testing, 19 Sep 2026: "Srt5674 August total sale quantity" answered "No sales found." because every August sale sat on the sibling SRT5674-N.) The `Product:` header line and the response's own `product_codes` echo BOTH follow this same "covers" rule, not "has a row in the report": `product_codes` is every code the prefix matches (a covered code with zero sales in the window still appears), sorted ascending, printed as a comma-separated list (AC-1633 - second fix round, owner ruling, 19 Sep 2026: the earlier bracket form `SRT5674 (SRT5674, SRT5674-N)` read "weird"). |
| S20 | (Owner ruling from live testing, 19 Sep 2026: with S19, the header read `Product: SRT5674, SRT5674-BL, SRT5674-N, SRT5674-NL` but each month only printed `*_By customer_*` - "how I know the report is for which product".) The breakdown a month carries is decided by the SUBJECT AND by how many product codes the product filter COVERS (`product_codes`, the S19 family): customer only -> `by_product` (unchanged); product only, ONE covered code -> `by_customer` (unchanged); product only, 2+ covered codes -> BOTH: `by_product` first, then `by_customer`; customer + product, ONE covered code -> none (unchanged); customer + product, 2+ covered codes -> `by_product` only. `by_product` rows list only codes that HAVE sales in that month (a covered code with no sales in the month is absent, never a zero row), same six figures, ranked per S6 (ordered value desc, ordered qty desc, code asc). Both lists sum to the month totals to the cent (per-line cent rounding, S15). Still aggregated in SQL: when both lists are needed the route runs the grouped query once per breakdown key (two grouped queries), never materialising lines. The presenter prints `*_By product_*` then `*_By customer_*` when both keys are present, in that order; each still prints rows in the order given, never sorts. Absent key = heading absent (unchanged). |

## Phase 1 - the reply (presenter over mock JSON)

- **AC-1601 [FE][T]** Given a report body with a customer subject and two months, when
  presented, then the text equals golden `sales-report-customer.txt` byte for byte: five
  header lines in the order Customer / Product / Channel / Location / Required date (or order date), a blank
  line, then month blocks in the order given. Evidence: golden fixture.
- **AC-1602 [FE][T]** Each month block prints, in order: `*_Sep 2026_*`, `Sales orders: n`,
  `Ordered: RM v (Qty: n)`, `Confirmed (DO): RM v (Qty: n)`, `Outstanding: RM v (Qty: n)`,
  the breakdown sub-heading, then breakdown lines `name: RM v (Qty: n) (Confirmed: RM v,
  Qty: n)`. Evidence: golden fixture.
- **AC-1603 [FE][T]** Any header axis absent from the body prints `all`, never an omitted
  line. `Channel` prints `Dealer`, `Project` or `all`. `Location` prints `IB (BRW-IB, MWH-IB)` when the body carries a `location_token`, the bare code when the token is itself the one code, `all` with no token. Evidence: pytest, presenter.
- **AC-1604 [FE][T]** Product subject prints `*_By customer_*`; customer subject prints
  `*_By product_*`; both named prints no breakdown heading at all - UNLESS the product
  covers 2+ codes (S20): product-only over a family prints BOTH headings, `*_By product_*`
  first; customer + a family prints `*_By product_*` only. Evidence: goldens, presenter
  tests.
- **AC-1605 [FE][T]** A hit ends with `Reply 1 for the sales order list.` and nothing after
  it. Evidence: golden fixture.
- **AC-1606 [FE][T]** With `detail=so` the presenter prints one numbered item per row:
  `*SO Number:*`, `*Customer:*`, `*Location:*`, `*Order Date:*`, `*Ordered:* RM v (Qty: n)`,
  `*Confirmed (DO):* RM v (Qty: n)`, `*Outstanding:* RM v (Qty: n)`, in the order given.
  Evidence: golden `sales-report-detail.txt`.
- **AC-1607 [FE][T]** A body with no months prints the header then `No sales found.` and no
  offer. Evidence: golden fixture.
- **AC-1608 [FE][T]** The presenter prints months and breakdown rows in the order given: an
  unsorted body comes out in input order. Evidence: pytest.
- **AC-1609 [FE][T]** Money prints `RM 1,234.50`; zero prints `RM 0.00`; no dash characters
  anywhere in the output. Evidence: pytest plus the dash guard.
- **AC-1633 [FE][T]** (S19, revised by owner ruling 19 Sep 2026 live testing: "why it
  says SRT5674 (SRT5674-N) so weird, it should just be comma separated") The `Product:`
  header line is the COMMA-SEPARATED list of every product code the typed stem covers -
  every code that starts with it, sorted ascending, e.g. `SRT5674, SRT5674-N,
  SRT5674-NL`. One covered code prints bare (there is nothing to list); more than 10
  covered codes collapses to `SRT56 (37 products)`; `all` with no product filter. A
  `detail=so` row prints a `*Product:*` line (after `*Customer:*`) with the SO's own
  matched codes, comma joined, when the body carries them; an OLD body with no
  `product_codes` key at all still renders, with no `*Product:*` line. Evidence: pytest,
  golden fixtures.

## Phase 2 - route and service

- **AC-1620 [BE][T]** Given four seeded lines (one fully transferred, one half transferred
  and open, one open untouched, one cancelled), then `ordered = confirmed + outstanding` for
  value and for quantity, the cancelled line contributes to nothing, and the half line's
  confirmed value is half its `line_total`. Evidence: pytest, Postgres.
- **AC-1621 [BE][T]** A line with `qty_delivered > qty_ordered` contributes `qty_ordered`
  to confirmed, never more; a line with `qty_ordered = 0` contributes 0 everywhere and does
  not raise. Evidence: pytest.
- **AC-1622 [BE][T]** A line is bucketed by `required_date`; a line with none by its SO's
  `order_date`. An SO with lines in June and July counts 1 in June's `so_count` and 1 in
  July's. Evidence: pytest.
- **AC-1623 [BE][T]** `date_from` / `date_to` filter on the SAME bucket date (required date,
  else order date), accept the formats `_parse_flex_date` accepts, and return 422 on
  anything else. No dates = every month. Evidence: pytest.
- **AC-1624 [BE][T]** `months[]` arrives latest first. Each month's breakdown arrives ranked
  per S6. Breakdown rows sum to their month's totals, value and quantity. Evidence: pytest.
- **AC-1625 [BE][T]** `channel=dealer` counts only `demand_class = 'retail'` SOs,
  `channel=project` only `'project'`; no channel counts both AND null-class SOs; a
  null-class SO appears under neither word. Any other value is 422. Evidence: pytest.
- **AC-1626 [BE][T]** Neither `product_code` nor `customer_ids` nor `customer_query` = 422
  `subject_required`. Product only, customer only and both each return 200. Evidence: pytest.
- **AC-1627 [BE][T]** `product_code` is a PREFIX, case-insensitive (S19): it matches
  the typed code AND every product whose code STARTS WITH it (`abc1` matches `ABC1`,
  `ABC10` and `ABC1-N`, never `XABC1`). LIKE metacharacters in the typed code are
  literal, never wildcards. A stripped `product_code` under 3 characters is 422
  `product_code_too_short`. No code starting with it is 404. The response echoes
  `product_codes`: every DISTINCT code the prefix matches (not only codes with a row
  in the filtered report - S19 second fix round, 19 Sep 2026), sorted ascending, `[]`
  with no product filter. `warehouse_codes` filters lines to
  those codes (unchanged by S19). Evidence: pytest.
- **AC-1628 [BE][T]** Breakdown key follows the subject: customer subject carries
  `by_product[]`, product subject `by_customer[]`, both named carries neither key -
  UNLESS the product filter covers 2+ codes (S20): product-only over a family carries
  BOTH keys; customer + a family carries `by_product[]` only. Evidence: pytest, the
  five-case table (customer only / product-only-one-code / product-only-family /
  both-one-code / both-family).
- **AC-1634 [BE][T]** (S20) When a month carries both `by_product[]` and `by_customer[]`
  (a product filter covering 2+ codes), BOTH lists sum to the month's own six figures
  to the cent - the per-line cent rounding (S15) tallies identically whichever
  breakdown key rolled them up, never a one-cent drift between the two grouped
  queries. `by_product[]` lists only codes with sales inside the window; a covered
  code with none in that month is absent, never a zero row. Evidence: pytest, the
  10/3-repeating rounding seed shape.
- **AC-1629 [BE][T]** `detail=so` returns `so_rows[]`, one row per SO with lines rolled up
  over the WHOLE filtered window: `so_number`, `customer_name`, `location` (distinct codes,
  comma joined), `order_date`, ordered / confirmed / outstanding value and quantity; sorted
  `order_date` descending, ties by `so_number` descending, undated last. Row sums equal the
  sum of the months. Evidence: pytest.
- **AC-1630 [BE][T]** The header echo `customer_name` is built by the outstanding report's
  own `_customer_echo` (distinct ledger names, first seen order). Evidence: pytest.
- **AC-1631 [BE][T]** Every field of the response is declared on the response model and
  asserted present through the HTTP route (`response_model` drops undeclared fields).
  Evidence: pytest.
- **AC-1632 [BE][T]** Route auth: no credential 401; a user without the order view
  permission 403; API key with act-as user 200; a contact-scoped API key sees only its
  companies' SOs (seed two companies, assert the other company's SO is absent).
  Evidence: pytest.

## Phase 2 - MCP tool and access

- **AC-1640 [BE][T]** The catalog lists `crm_sales_report` with path
  `/api/v1/order-management/sales-report`, domain `orders`, and
  `restricted_fields = (("sales_orders.sales_report", "Sales report"),)`. Evidence: pytest, MCP.
- **AC-1641 [BE][T]** `sales_orders.sales_report` is in `FIELD_REVEAL_KEYS`, the pinning
  tests pass, and the key is listed on Contacts > Access. Evidence: pytest plus AC-1670.
- **AC-1642 [BE][T]** STRUCK 19 Sep, security B1: no bootstrap - the in-app AI assistant must
  never carry `crm_sales_report`, or a staff member 403 on the route (no
  `order_management.orders.view`) could read the money through the assistant instead, across
  every company (the same reason `crm_low_stock_report` is kept off it, N4).

## Phase 2 - the lane

- **AC-1650 [BE][T]** Domain `order` AND a resolved customer or product AND
  `order_status = "sales_report"` picks tool `crm_sales_report`; every outstanding status
  still picks `crm_outstanding_report`. Evidence: pytest, lane.
- **AC-1651 [BE][T]** A contact without `sales_orders.sales_report` gets
  `Sales report is not enabled for your account.`, the fetch function is never called, and no
  offer is armed. Evidence: pytest with a fetch spy.
- **AC-1652 [BE][T]** The parser's channel value reaches the tool as `channel`; absent means
  no param. The lane never reads the message text for it. Evidence: pytest.
- **AC-1653 [BE][T]** Parsed dates map to `date_from` / `date_to`; a resolved customer to
  `customer_ids`; a location token to `warehouse_codes` through the existing warehouse
  resolver; the product through `outstanding_product_code`'s rule (typed code wins).
  Evidence: pytest.
- **AC-1654 [BE][T]** A hit arms `selection_context = "sales_report_detail"`, a one-row
  `last_result_set` (`Sales order list`, value `so`) and stores the filter set including the
  tool name; no escalate offer on a hit. Evidence: pytest.
- **AC-1655 [BE][T]** Under that offer, "1" re-runs `crm_sales_report` with the stored
  filters plus `detail=so`. The offer is sticky across the pick and across a casual turn,
  drops on a new ask, re-prints at most once on an unreadable reply, and closes on a decline
  with the `offer_declined` copy. Evidence: pytest, parametrized over BOTH detail kinds so the
  outstanding offer's behaviour is pinned unchanged.
- **AC-1656 [BE][T]** A refinement turn under the offer (dates only, a location word, a
  product on a customer report) re-runs the report with the overlaid filters and re-arms the
  offer; a turn naming a new subject is a new ask. Evidence: pytest, same parametrization.
- **AC-1657 [BE][T]** The ambiguous-customer picker on a sales report ask prints no
  `has DO` / `no DO` hint, and a pick ("1" or "all") continues the sales report with the
  original dates and channel. Evidence: pytest.
- **AC-1658 [BE][T]** A miss (no months) returns the presenter's miss text through the
  existing not-found path, so the escalate offer follows unchanged. Evidence: pytest.
- **AC-1659 [BE][T]** The generic search-scope header is skipped for `crm_sales_report`.
  Evidence: pytest.
- **AC-1660 [BE][T]** The parser prompt fallback and its published migration teach
  `order_status: "sales_report"` and the channel field; the prompt-is-live sha test passes.
  Evidence: pytest.

## Phase 3 - live

- **AC-1670 [E2E]** Browser, sidebar clicks from `/`: Contacts > a contact > Access lists
  `Sales report`, ticking it persists after reload, at 375px and 1280px. Evidence:
  agent-browser run.
- **AC-1671 [E2E]** Console check `tests/chatbot/console_cases/2026-09-19-sales-report.yaml`
  against the lane stack, graded line by line: the seven journey messages, plus one fuzzy
  phrasing ("how much did hanlim buy this year") and one trap ("dealer outstanding quantity
  for hanlim" must still reach the outstanding report). Evidence: console log under
  `evidence/sales-report/`.
- **AC-1672 [E2E]** On the prod copy, the report for HANLIM, all dates: month totals summed
  equal an independent SQL over the same predicate, to the cent. Evidence: SQL plus console
  log.

## [UX]

WhatsApp text only. Nothing animates (no-motion list: everything). The one screen touched,
Contacts > Access, gains a row through an existing list and gets no new motion.

## Backlog (triggers, not built)

- Bucket confirmed sales by the actual DO date: when the AutoCount DO feed lands with an SO
  line link.
- A `Reply 2` product-totals list over the whole window: when the owner finds the all-dates
  reply too long to read.
- Repair `order_lines.total` on the DO import: when anything needs DO money.
