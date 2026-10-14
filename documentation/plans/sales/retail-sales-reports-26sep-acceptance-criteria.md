# UAC: retail sales reports, one query layer for the screens and the chatbot (#1267)

Plan: `PLAN-retail-sales-reports-26sep.md`. Track: full (S1 to S4, and S6 from round 3); S5 small fix track (round 2); S0 is a measurement.
Status: draft, round 5 (26 Sep 2026), ready to build; round 4 and round 2 status kept below. Round 4 status: draft, round 4 (26 Sep 2026). Round 2 (26 Sep 2026). The owner's Lavish review of the mockup (26 Sep 06:27Z, 10
notes) is applied: each note has an "Owner ruling 26 Sep 06:27 (Lavish) <n>" line in the section
"Round 2" below, and every criterion it changes keeps its text and gains a "Round 2:" note. No
criterion is deleted. G1 to G10 are still unanswered: their recommendations are written in as the
expected behaviour and are marked `(G<n>)`; each changes if the owner rules otherwise. The round
2 questions Q1 to Q5 (plan 9.1) are written in the same way and marked `(Q<n>)`.
Round 3 (26 Sep 2026): the owner answered G1 to G10 (PR #1269 comment 5844136277, 26 Sep 07:06Z).
Each answer has an "Owner ruling 26 Sep 07:06 G<n>" line in the section "Round 3" below, and each
criterion it changes keeps its text and gains a "Round 3:" note. No criterion is deleted. A `(G<n>)`
mark on an answered question is now a ruling, not a recommendation, except G7, which is asked again
(recommendation (a) stays written in). Round 2's Q1 to Q4 stay recommendations; Q5 is re-answered
(AC-R3-1). The round 3 questions Q6 to Q8 (plan R3.6) are written in and marked `(Q<n>)`.
Round 4 (26 Sep 2026): the owner answered round 2's Q1 to Q5 (PR #1269 comment 5844192717, 26 Sep
07:16:21Z). Each answer has an "Owner ruling 26 Sep 07:16 Q<n>" line in the section "Round 4"
below, and each criterion it changes keeps its text and gains a "Round 4:" note. No criterion is
deleted. A `(Q1)` to `(Q5)` mark is now a ruling. Q2 was ruled against the recommendation: every
chatbot answer is text plus the Excel file, with no cutoff (AC-R4-1 to AC-R4-6). Round 3's Q6 to
Q8 stay open and Q9 is new (plan R4.6); Q6's written-in behaviour changes to the new
recommendation (AC-R4-11).
Round 5 (26 Sep 2026): the owner answered round 4's Q6 to Q8 (PR #1269 comment 5844298765, 26 Sep
07:34:40Z). Each answer has an "Owner ruling 26 Sep 07:34 Q<n>" line in the section "Round 5"
below, and each criterion it changes keeps its text and gains a "Round 5:" note. No criterion is
deleted. Q6: no scheduled WhatsApp; Q7: the weekly send is an automation on the existing
Automation screen, so the S6 criteria about `sales.report_subscriptions`, the Scheduled sends
dialog, its routes, the `sales.reports.schedule` slug and the WhatsApp channel are superseded by
AC-R5-1 to AC-R5-12; Q8: (a) ruled. Q9 is still open and recommendation (a) stays written in
(AC-R4-2); Q10 is new (plan R5.6), recommendation (a) written in.
Mockups: `documentation/plans/sales/mockups/retail-sales-reports.html`.
Tags: `[BE]` backend, `[FE]` frontend, `[E2E]` browser via agent-browser, `[T]` has a named test.

## Journey

Actor: the owner or a sales manager with "sales reports: view"; on WhatsApp, a staff contact
holding the Sales report grant.

- **J1.** Opens Sales > Reports > Yearly comparison from the sidebar. The page opens on today as
  the as-at date and the default basis, with nothing to fill in.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 2: the item is Sales > Yearly comparison in the Sales group (plan 0.2); the page is the kernel's `ReportPage` (note 1).
- **J2.** Reads the DEALER block: 2024, 2025 and 2026 by month with TOTALS, the variance row, and
  the chart beneath; then the PROJECT TEAM block the same way.
- **J3.** Changes the as-at date or the basis; the tables and charts redraw.
- **J4.** Presses Export to Excel and gets a workbook laid out like the PDF.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 5: the workbook is queued and lands in My Downloads; the user downloads it from the drawer (plan 0.4).
- **J5.** Opens Sorento by account: the Monthly tab lists one table per month, agents by account,
  HANLIM apart, TOTAL SALES; Year to date and Dealer vs HANLIM tabs follow.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 4: "Sorento by account" is now **Sales report** with Company = Sorento; the sections are shared views in the Views menu, not tabs (Q4).
- **J6.** Opens Mocha by account (once Mocha's feed is live): by account per month, by debtor type,
  by month, project debtors, by quarter with the "without Dilooma and Pintar" row.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 4: the same Sales report with Company = Mocha; before S4 it shows the empty state, not a hidden item.
- **J7.** On WhatsApp asks "compare dealer sales 2025 vs 2026 by month" and gets the months with
  both years and the difference, the basis and the count in the header.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 6: the answer is a short text header plus the Excel file, like the low stock report (plan 0.6).
  - Round 4: Owner ruling 26 Sep 07:16 Q2: the text carries the months, both years and the difference (the whole answer), and the Excel file follows.
- **J8.** Asks "Sean's sales this month by brand" and gets Sean's accounts, all of Sean's codes
  counted, and the total.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 7 and 9: text; all of Sean's codes are counted with no "which code" question.
  - Round 4: Owner ruling 26 Sep 07:16 Q2: text plus the Excel file.
- **J9.** Asks "Mocha Q2 by agent without Dilooma" and gets every agent, the excluded customer
  named in the header, and the total.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 8: text.
  - Round 4: Owner ruling 26 Sep 07:16 Q2: text plus the Excel file.
- **J10.** (Round 3, Owner ruling 26 Sep 07:06 G9) Every Monday at 09:00 a named sales manager
  gets the Sales report's "By account" view as an Excel file, year to date as at that day, by
  email or on WhatsApp; the same file is in their My Downloads. The owner set it up once from the
  report's Views menu.
  - Round 4: on WhatsApp outside the 24 hour window the person gets the template text and, on replying, the file (Q6, recommended).
  - Round 5: Owner ruling 26 Sep 07:34 Q6 and Q7: by **email only**, never WhatsApp. The owner set it up once as an automation on System Management > Automation (trigger "Sales report view", the view, Weekly, Monday, 09:00, the people). The file is attached to the email; one copy sits in the automation owner's My Downloads (AC-R5-1 to AC-R5-12).

Decisions asked of the user: the as-at date and basis (both pre-filled), the company on report A,
and on WhatsApp only what the message leaves ambiguous (company, period per G6, basis, axis, which
code).
Round 3: for a scheduled send, the owner decides the view, the people, the channel, the weekday
and the time; nothing else (the period is year to date as at the send date, Q8).
Round 5: no channel choice (email only, Q6); the owner decides the view, the people, the weekday
and the time on the automation (Q7); the period is ruled (Q8).

## Measured (S0, prod copy, to be pasted before the grill closes)

- Sales orders and `SUM(line_total)` per month per company, Jan 2023 to Sep 2026: _pending_.
- Three owner-chosen months: dealer and project totals, both bases, both date rules, against the
  PDF totals, gap in RM and percent: _pending_.
- Raw `Debtor.DebtorType` values on the customers masters push, with counts and the number
  dropped as `segment_unknown`: _pending_.
- `sales_agents` per company, `person_label` fill (expected 0 of 80): _pending_.
- HANLIM ledger ids (6 expected); Mocha SO count (expected 0) and `so_feed_live`: _pending_.
- `EXPLAIN ANALYZE` of a three-year year by month grid: _pending_.

## S1. Report A and the query layer

### The grid

- **AC-S1-1 [BE][T]** Given SO lines in one company and window, when `sales_grid` runs with
  `rows=year, cols=month, basis=ordered`, then every cell is the SUM of the per-line `ordered_value`
  of `_per_line_exprs` and the grand total equals `sales_report`'s ordered value for the same
  lines and window to the cent. Same for `basis=delivered` against the confirmed value.
  - Round 3: Owner ruling 26 Sep 07:06 G1: both bases are offered on screen (Basis filter), Delivered is the default.
- **AC-S1-2 [BE][T]** Given a cancelled SO and a cancelled line, when the grid runs, then neither
  is in any cell or total.
- **AC-S1-3 [BE][T]** Given a line with `required_date` in March on an SO dated February, when the
  grid runs, then it is counted in February (`sales_orders.order_date`, G1).
- **AC-S1-4 [BE][T]** Given an SO with `demand_class` null, when the grid runs with
  `rows=channel`, then it appears in a `(blank)` row sorted last and in the grand total; it is never
  dropped.
- **AC-S1-5 [BE][T]** Given two companies each with sales, when a user granted only Sorento runs the
  grid with Sorento, then no Mocha line is in it; with `company_id` = Mocha the route is 403.
- **AC-S1-6 [BE][T]** Given `rows == cols`, an unknown axis, `date_from > date_to`, no basis, or a
  list over 50 ids, when `GET /sales-analysis` is called, then it is 422 with a named error.
  - Round 2: the route is `GET /api/v1/sales/analysis` (plan 5.5); the 422 table is unchanged.
- **AC-S1-7 [BE][T]** Given `n=3` on a grid of 14 agent rows, when the route runs, then 3 rows come
  back ranked by total desc then label asc, and `total_count` is 14 and the totals are the whole
  set's. No `page`, `page_size` or `has_more` key exists in the response.
- **AC-S1-8 [BE][T]** Access: no token 401; a user without `order_management.sales_reports.view`
  403; the API key acting for a contact without the `sales_orders.sales_report` reveal key 403.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 2: the slug is `sales.reports.view` under the `sales` module (plan 0.2, Q5).

### The report

- **AC-S1-9 [BE][T]** Given `as_at` = 26/09/2026, when `yearly-comparison` runs, then each block
  has rows 2024, 2025, 2026 and columns JAN to DEC plus TOTALS; 2026 cells for October to December
  are blank (not 0).
- **AC-S1-10 [BE][T]** The variance row is 2026 minus 2025 per month, and its total is 2026 year to
  date minus 2025 over the same months (G5); a negative prints in brackets.
  - Round 3: Owner ruling 26 Sep 07:06 G5: agreed; this is the ruling.
- **AC-S1-11 [BE][T]** The two blocks are titled `SORENTO SDN BHD - DEALER` and `SORENTO SDN BHD -
  PROJECT TEAM` from `companies.name` and the channel (G4), and each carries one chart series per
  year row.
  - Round 3: Owner ruling 26 Sep 07:06 G4: DEALER and PROJECT TEAM are the sales order's `demand_class` (retail, project).
  - Round 2: the two blocks are the Channel filter on one kernel pivot; both blocks in one Excel file is Q1 (recommended: one block per ticked channel, one under the other).
  - Round 4: Owner ruling 26 Sep 07:16 Q1: "okay", one block per ticked channel, one under the other (AC-R4-7).
- **AC-S1-12 [FE][E2E]** From `/`, Sales > Reports > Yearly comparison opens the page (sidebar
  click, never a deep URL). A user without the slug does not see the menu item.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 2: the path is Sales > Yearly comparison (SALES heading, Sales group).
- **AC-S1-13 [FE][T]** The page shows the PageHeader with the title, the as-at date and basis in
  the filter row, the basis line "Basis: Delivered (transferred to DO), sales orders, not
  invoices" (G1), both blocks with their tables and a line chart under each (months on x, one line
  per year, legend by year).
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 1: the page is `ReportPage` with key `sales_yearly`; the table is the Summary tab's `ReportPivotTable`, the chart is `ReportPivotChart`; the filter row is `ReportFilterBar` (Company, Channel, Basis, Period).
- **AC-S1-14 [FE][T]** Changing the as-at date or basis refetches once and redraws both blocks.
- **AC-S1-15 [FE][T]** A block with no sales renders its table with blanks and "No sales in this
  period", never a missing section.
- **AC-S1-16 [FE][E2E]** Screens print whole ringgit (the export keeps cents). At 375, and at 1280
  whenever twelve months do not fit, each table scrolls sideways inside its card with the YEAR
  column sticky and no cell cut; the chart fits the width; the page itself never scrolls sideways.
- **AC-S1-17 [BE][T]** Export: the `.xlsx` has one sheet with the title block (company, report
  title, "As at 26/09/2026", basis), the two blocks in the PDF's order, the variance row, a native
  line chart under each block, number format `#,##0.00;(#,##0.00)`, and totals as values, never
  formulas. A text cell starting with `=`, `+`, `-` or `@` is escaped.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 5: the workbook is written by the kernel's `generate_report_xlsx` worker job, with `month_sheets` off for this report.
- **AC-S1-18 [FE][E2E]** Export to Excel is the page's one primary action and downloads the file
  named `Yearly sales comparison as at 2026-09-26.xlsx`.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 5: superseded by AC-R2-8. The click queues the file and toasts "... is being prepared in My Downloads"; the file name is the kernel's `<title>-<period>.xlsx`.

### The chatbot

- **AC-S1-19 [BE][T]** `crm_sales_analysis` is in the MCP catalogue, in `PRESENTER_TOOLS`, in
  `CHATBOT_READ_ONLY_TOOLS`, mapped to the order domain, and in the order domain seed as an
  allow-list member; one migration updates the live `chatbot_domains` row; single alembic head.
- **AC-S1-20 [BE][T]** "compare dealer sales 2025 vs 2026 by month" fetches `rows=month,
  cols=year, channel=dealer, compare_years=[2025, 2026]` and prints the golden
  `sales-analysis-compare.txt`: header (company, channel, basis, period, "Months: 12"), one line
  per month with both years and the difference, a total line. No "more", "next" or "lagi".
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 6: the golden is now the text header plus an attachment (AC-R2-13, AC-R2-14); the header and total lines stay; the month lines move into the file.
  - Round 4: Owner ruling 26 Sep 07:16 Q2: the month lines come back into the text (the text is the whole answer) and the attachment stays (AC-R4-1).
- **AC-S1-21 [BE][T]** A contact granted both companies who names none gets `Sorento or Mocha?`
  and nothing is fetched. The clarify body also carries `companies`, the option names in the same order, so the chatbot can store the question as an open question.
  - Round 4: a clarify question carries no file (AC-R4-3).
- **AC-S1-22 [BE][T]** No period in the message = the current calendar year, printed in the
  header (G6). An ambiguous period ("last quarter" in January) gets `Which period: last quarter of
  2025, or Q1 2026?`.
  - Round 3: Owner ruling 26 Sep 07:06 G6: "date i think assume is ok"; this is the ruling.
- **AC-S1-23 [BE][T]** A contact linked to a customer gets `Sorry, I can only share sales figures
  for your own account.` and nothing is fetched.

## S2. Sales by agent as a person

- **AC-S2-1 [BE][T]** Given codes `SEAN I` and `SEAN III` both labelled SEAN, when the grid runs
  with `rows=agent`, then one SEAN row holds both codes' sales.
- **AC-S2-2 [BE][T]** Given a code with no `person_label`, then its row is labelled with the code;
  an SO with no agent is in `(blank)` and in the total.
- **AC-S2-3 [BE][T]** Given `set_apart_customer_ids` = the HANLIM ledgers, then HANLIM's lines are
  in no agent row, appear once in a row labelled `HANLIM`, below the agent rows, and the grand total
  is unchanged.
- **AC-S2-4 [BE][T]** `sorento-by-account` returns one section per month from January to the as-at
  month (agent rows, HANLIM row, TOTAL SALES), a "Sales for year" section, and a DEALER - SALESMAN
  vs HANLIM vs TOTAL section for JAN to DEC. The monthly sections sum to the year section to the
  cent.
  - Round 3: Owner ruling 26 Sep 07:06 G4: the per-agent sections count dealer and project orders together (AC-R3-4).
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 4: there is no `sorento-by-account` key. The sections are shared views of the `sales` definition (AC-R2-10); the monthly tables are the workbook's month sheets.
- **AC-S2-5 [FE][T]** Report B page: line tabs Monthly, Year to date, Dealer vs HANLIM; the HANLIM
  set is chosen with a `SearchableMultiSelect` of customers and saved as the report's shared view;
  agents are listed in the owner's order (G7), a gap row before HANLIM, TOTAL SALES bold.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 1 and 4: no line tabs; the sections are shared views in `ReportViewsMenu`. Agents are listed alphabetically by the kernel (the owner's own order is a named trigger, plan section 7), set-apart rows last.
  - Round 3: G7 was not ruled (the owner asked what it is for); the HANLIM set stays a customer list saved in the shared view, recommendation (a), asked again (plan R3.5).
- **AC-S2-6 [FE][E2E]** At 375 the tab strip scrolls and never wraps; every table scrolls sideways
  with the agent column sticky.
  - Round 2: no tab strip; the tables still scroll sideways inside their card with the first column pinned.
- **AC-S2-7 [BE][T]** Export: one sheet, sections in the PDF's order, titled "Weekly Sales by
  Debtor Type by Sales Agent", period "01/01/2026 to 26/09/2026".
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 5: through My Downloads; the title block keeps "Weekly Sales by Debtor Type by Sales Agent".
- **AC-S2-8 [BE][T]** Chatbot "Sean's sales this month" resolves SEAN to every code with that label
  and prints the golden `sales-analysis-agent.txt` (the codes named in the header).
  - Round 4: Owner ruling 26 Sep 07:16 Q2: plus the Excel file (AC-R4-1).
- **AC-S2-9 [BE][T]** "Sean" with no labels and two codes starting SEAN gets `SEAN I or SEAN III?`
  and nothing is fetched.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 9: superseded by AC-R2-11 and AC-R2-12. With a label, or with codes that share a name (SEAN I, SEAN III), the bot sums them and never asks.
- **AC-S2-10 [BE][T]** "sales by agent this year" with more rows than one message holds and no N
  gets the header with the full count and `That list is too long for one message. How many agents
  do you want to see?`; "top 5 agents this year" gets 5 rows under a header stating the full count.
  - Round 2: superseded in part by the owner's PR #1258 ruling (26 Sep 05:32Z, "length is not the bot's problem"): a breakdown sends every row and n8n chunks (AC-R2-16). "top 5 agents this year" is unchanged.
- **AC-S2-11 [T]** DoD: every active Sorento code has a `person_label`; the count is pasted in the
  PR.
  - Round 2: the labels are pre-filled by the name split and reviewed by the owner (Q3, AC-R2-18).
  - Round 4: Owner ruling 26 Sep 07:16 Q3: "okay"; the pre-fill is the ruling.

## S3. Debtor type and the account columns

- **AC-S3-1 [BE][T]** Given a customers masters push carrying `Debtor.DebtorType` = `BRAVAT`, when
  it is ingested, then `customers.debtor_type` = `BRAVAT` on insert and on update, and
  `market_segment_code` behaves exactly as before (folded or dropped with `segment_unknown`).
- **AC-S3-2 [BE][T]** `debtor_type` is in the customer response schemas and in both manual customer
  dict builders; a test asserts the field in the API response.
- **AC-S3-3 [BE][T]** The grid with `cols=debtor_type` puts a customer with no debtor type in
  `(blank)`, last, and in the total.
- **AC-S3-4 [BE][T]** Report B's columns are SORENTO, BRAVAT, CERAMIC, CABANA, PROJECT, SAMPLE, then
  any other value present, then `(blank)`, then TOTAL (G3); a column with no sales in a month still
  prints, blank.
  - Round 3: Owner ruling 26 Sep 07:06 G3: agreed, (a) the raw debtor type on the customer; the product brand is a separate axis (AC-R3-5).
- **AC-S3-5 [FE][T]** The customer detail header shows the debtor type read-only.
- **AC-S3-6 [BE][T]** Chatbot "Sean's sales this month by brand" prints the golden
  `sales-analysis-agent-by-account.txt` (one line per account with sales, "Accounts with sales: 3",
  a total).
  - Round 3: Owner ruling 26 Sep 07:06 G3: with the product brand now its own axis, "by brand" alone is ambiguous, so the bot first asks `Debtor type or product brand?` (AC-R3-6, the owner's "clarify, never assume" ruling from #1175); this golden is the reply after "debtor type", and "Sean's sales this month by account" prints it with no question.
  - Round 4: Owner ruling 26 Sep 07:16 Q2: the golden reply also carries the Excel file (AC-R4-1).
- **AC-S3-7 [T]** DoD: after the full masters re-push, `debtor_type` fill per company is pasted in
  the PR; the owner reads report B beside the PDF.

## S4. Mocha sales order feed

- **AC-S4-1 [BE][T]** A sales order pushed with Mocha's company code lands under Mocha and is
  invisible to a Sorento-only user.
- **AC-S4-2 [T]** DoD: Mocha's SO count and first month pasted; `so_feed_live` is true for Mocha;
  the Mocha by account menu item appears.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 4: there is no Mocha menu item to appear; Mocha's figures appear in the Sales report and the Yearly comparison through the Company filter.
  - Round 3: Owner ruling 26 Sep 07:06 G2: Mocha is the company (AutoCount db2); S4 stays the prerequisite of S5.

## S5. Report C

- **AC-S5-1 [BE][T]** `rows=agent, cols=quarter` files Jan to Mar in Q1 through Oct to Dec in Q4.
- **AC-S5-2 [BE][T]** `exclude_customer_ids` = DILOOMA and PINTAR removes their lines from every
  cell and total; the response names the excluded customers.
- **AC-S5-3 [BE][T]** `mocha-by-account` returns, in order: agent by debtor type per month (with a
  Grand Total row); agent by month per debtor type; agent by month; debtor type by month; the named
  project debtors by month; agent by quarter with a closing row "Total sales without Dilooma and
  Pintar (project)". Every section's grand total for the same window agrees.
  - Round 3: G7 asked again; the DILOOMA and PINTAR exclusion stays saved in Mocha's "By quarter" view (recommendation (a)).
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 4: there is no `mocha-by-account` key; these sections are the Sales report's shared views for Mocha (AC-R2-10).
- **AC-S5-4 [FE][T]** Report C page with line tabs By account, By debtor type, By month, Project
  debtors, By quarter; the named debtors set in the report's shared view.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 1 and 4: shared views in the Views menu, not line tabs.
- **AC-S5-5 [BE][T]** Chatbot "Mocha Q2 by agent without Dilooma" prints the golden
  `sales-analysis-exclude.txt`: header with `Company: Mocha`, `Excluded: DILOOMA ...`, `Agents with
  sales: 8`, one line per agent, the total.
  - Round 2: Owner ruling 26 Sep 06:27 (Lavish) 8: stays text.
  - Round 4: Owner ruling 26 Sep 07:16 Q2: text plus the Excel file (AC-R4-1).
- **AC-S5-6 [FE][E2E]** At 375 and 1280, report C is usable and not clipped; export matches the
  PDF's section order.

## Round 2: the owner's Lavish review (26 Sep 06:27Z)

- Owner ruling 26 Sep 06:27 (Lavish) 1: reuse the reporting module's components; few new components.
- Owner ruling 26 Sep 06:27 (Lavish) 2: sales module or schema; reuse the sponsorship form's reporting function.
- Owner ruling 26 Sep 06:27 (Lavish) 3: the same as 1 and 2, on the Sorento by account screen.
- Owner ruling 26 Sep 06:27 (Lavish) 4: one "Sales report" for both companies, not "Sorento by account".
- Owner ruling 26 Sep 06:27 (Lavish) 5: downloads go to My Downloads.
- Owner ruling 26 Sep 06:27 (Lavish) 6: the dealer year comparison is sent as a file, like the low stock report.
- Owner ruling 26 Sep 06:27 (Lavish) 7: "Sales by account" for Sean can be text.
- Owner ruling 26 Sep 06:27 (Lavish) 8: "Mocha sales by agent" can be text.
- Owner ruling 26 Sep 06:27 (Lavish) 9: SEAN I and SEAN III are the same person; count both.
- Owner ruling 26 Sep 06:27 (Lavish) 10: decide file, text, or text + file.

### Reuse and module (notes 1, 2, 3)

- **AC-R2-1 [FE][T]** (S1, S2) Both screens render `ReportPage` (`components/reports/ReportPage.tsx:270`)
  with keys `sales_yearly` and `sales`; the only new frontend files are the two route wrappers and
  `components/reports/ReportPivotChart.tsx`. An inventory test lists them.
  - S1 build: plus `components/reports/reportFormat.ts`, the whole-ringgit formatter the table and
    the chart share (a 'use client' component file must export components only, LESSONS-LEARNT
    106). `config/menu.sales.test.ts` is the inventory test.
- **AC-R2-2 [BE][T]** (S1) The `sales_order_lines` dataset and the two definitions are registered;
  `GET /reports` lists them to a holder of `sales.reports.view` and not to anyone else; the
  sponsorship report's meta, run and workbook are byte-for-byte unchanged.
- **AC-R2-3 [BE][T]** (S1) The reports router checks each definition's `module_key`: with `sales`
  disabled, both sales reports are 403 and the sponsorship report still works; a definition with no
  `module_key` is 403 (fail closed).
- **AC-R2-4 [BE][T]** (S1) The kernel's company arm is fail-closed: no resolved scope = no rows; a
  Company filter value outside the caller's grant is 403; the export job runs under the
  enqueuer's company.
- **AC-R2-5 [FE][E2E]** (S1, S2) From `/`, SALES > Sales > **Sales report** and **Yearly comparison**
  open by sidebar clicks; both items carry `moduleKey: 'sales'` and `sales.reports.view`; a user
  without the slug sees neither (Q5).
- **AC-R2-6 [T]** (S1 to S5) No new table and no `sales` Postgres schema; the only schema change of
  the whole plan is `customers.debtor_type` (S3) (Q5).
  - Round 3: superseded by AC-R3-1 (Q5 re-answered to align with #1260 round 5): the plan's one new table, `sales.report_subscriptions` (S6), is in the `sales` schema. It still holds for S1 to S5.
  - Round 4: Owner ruling 26 Sep 07:16 Q5: "we need a sales schema and a sales module"; superseded by AC-R4-8 to AC-R4-10. S1 to S5 still add no table, but the `sales` schema is created by the lane that creates the module.
  - Round 5: Owner ruling 26 Sep 07:34 Q7: no slice adds a table now, S6 included; the schema changes are `customers.debtor_type` (S3) and `automations.run_weekday` (S6), and the `sales` schema is still created (AC-R5-11).

### One Sales report (note 4)

- **AC-R2-7 [FE][T]** (S2) The title is "Sales report" on the menu, the page header, the crumbs,
  the workbook and the chatbot header; the string "Sorento by account" appears nowhere. The
  Company filter lists the caller's granted companies, defaults to the current one, and is
  required.
- **AC-R2-8 [FE][E2E]** (S1, S2) Export to Excel queues the file: the toast reads "`<file>` is being
  prepared in My Downloads", the My Downloads badge moves, and the file downloads from the drawer
  row. No new file imports `saveBlobAs` (note 5).
- **AC-R2-9 [FE][E2E]** (S1, S2) With Company = Mocha and no Mocha sales orders, both reports show
  the kernel's empty state; no menu item is hidden or added.
- **AC-R2-10 [BE][T]** (S2, S5) Six shared views per company exist and are published: By account,
  By month, Debtor type by month, Dealer vs set apart, By quarter, Set apart customers by month
  (plan 0.3). Sorento's carry Set apart = the HANLIM ledgers; Mocha's "By quarter" carries
  Exclude = DILOOMA, PINTAR (Q4).
  - Round 4: Owner ruling 26 Sep 07:16 Q4: "okay"; shared views are the ruling.
- **AC-R2-17 [FE][T]** (S1) The yearly comparison's Summary tab shows the VARIANCE row under the
  year rows (muted row, brackets for negatives, blank after the as-at month) and the line chart
  under the table, one line per year, the current year in the primary colour.

### The agent is a person (note 9)

- **AC-R2-11 [BE][T]** (S2) Given SEAN I and SEAN III labelled SEAN, "Sean's sales this month" sums
  both, the header reads "Sales agent: SEAN (SEAN I, SEAN III)", and no question is asked.
- **AC-R2-12 [BE][T]** (S2) Given no labels, SEAN I and SEAN III are grouped by the name split and
  summed, named in the header; a typed name that matches two different names (TAN KH, TAN WL)
  gets `Which Tan: TAN KH or TAN WL?` and nothing is fetched.
- **AC-R2-18 [BE][T]** (S2) The pre-fill migration sets `person_label` from the name split on every
  code with a roman suffix and a null label, never overwrites a typed label, and is idempotent
  (Q3).
  - Round 4: Owner ruling 26 Sep 07:16 Q3: "okay".

### Chatbot file or text (notes 6, 7, 8, 10)

- **AC-R2-13 [BE][T]** (S1, S2) The presenter picks the format by shape (Q2): one value column =
  text (3 rows and 300 rows alike); two or more value columns and at most 12 value cells = text
  (`label: a | b | c`); more than 12 value cells = text header plus the Excel file. Goldens:
  "compare dealer sales 2025 vs 2026 by month" (36 cells, file), "Sean's sales this month by
  brand" (3 x 1, text), "Mocha Q2 by agent without Dilooma" (8 x 1, text), "HANLIM this year vs
  last year" (1 x 3, text).
  - Round 4: Owner ruling 26 Sep 07:16 Q2: "always text + file, no cutoff"; superseded by AC-R4-1 and AC-R4-2. The four goldens stay, each now text plus the file.
- **AC-R2-14 [BE][T]** (S1) A file answer creates a `report_xlsx` My Downloads row owned by the CRM
  user linked to the contact, queues `generate_report_xlsx`, and when ready within the sync window
  returns `attachments` so the engine adds `send_attachments` after `send_message`; otherwise the
  reply is `Preparing the Excel, it will be sent here when ready.` and the worker pushes the file
  once (one-shot claim, as the low stock push).
  - Round 4: Owner ruling 26 Sep 07:16 Q2: this path now serves every answer; the pending line reads `The Excel follows here.` (AC-R4-4).
- **AC-R2-15 [BE][T]** (S1) "in Excel", "as a file" or "send the report" forces the file for any
  answer; "as text" forces text for any answer.
  - Round 4: Owner ruling 26 Sep 07:16 Q2: superseded by AC-R4-5. No override words: the file always comes, and "as text" is dropped.
- **AC-R2-16 [BE][T]** (S2) No one-message setting exists; "sales by agent this year" with 61 agents
  sends every row (n8n chunks); "best agents this year" with no N states the count and asks how
  many (the top X rule).

## Round 3: the owner's grill answers (26 Sep 07:06Z)

- Owner ruling 26 Sep 07:06 G1: (c), either basis chosen on the screen, Delivered by default, the basis printed on every header.
- Owner ruling 26 Sep 07:06 G2: Mocha is the company (AutoCount db2); connecting its SO feed (S4) stays a prerequisite.
- Owner ruling 26 Sep 07:06 G3: agree, (a) the debtor type raw on the customer, plus (d) the product brand as a separate axis.
- Owner ruling 26 Sep 07:06 G4: (a) the sales order's dealer or project class; report B's per-agent table includes project orders.
- Owner ruling 26 Sep 07:06 G5: agree, (a).
- Owner ruling 26 Sep 07:06 G6: (b), the current calendar year, stated in the header.
- Owner ruling 26 Sep 07:06 G7: not ruled ("what's this quesiton for"); recommendation (a) stays and is asked again.
- Owner ruling 26 Sep 07:06 G8: (c) for now; footnotes are typed into the Excel after export.
- Owner ruling 26 Sep 07:06 G9: build both: on-demand export (S1, S2) and a weekly scheduled Excel to named people by email or WhatsApp (S6).
- Owner ruling 26 Sep 07:06 G10: (a) is fine.

### Module and schema (Q5 re-answered, aligned with #1260 round 5)

- **AC-R3-1 [BE][T]** (S6) The plan's one new table is `sales.report_subscriptions` in the `sales`
  Postgres schema (`__table_args__` schema `sales`); the migration runs `CREATE SCHEMA IF NOT
  EXISTS sales` and is safe whether or not #1260's migration ran first; `purge_tables.json` lists
  it; a purge invariants test shows uninstall deletes its rows and never drops the schema. Saved
  views stay `report_views` rows; `sales_orders`, `sales_order_lines`, `sales_agents` and
  `customers` stay in `public`. Single alembic head.
  - Round 4: Owner ruling 26 Sep 07:16 Q5: ruled. The purge file is `sorento_crm_frontend/modules/sales/purge_tables.json` (AC-R4-9).
  - Round 5: Owner ruling 26 Sep 07:34 Q7: superseded; `sales.report_subscriptions` is not built, the weekly send is an automation, and the plan creates no table (AC-R5-11). The `sales` schema is still created by the module-creating lane (AC-R4-8).
- **AC-R3-2 [BE][T]** (S1 or #1260 S6, whichever lands first) One `sales` module: one
  `bootstrap.py`, one `MODULE_MANIFEST` entry, one `"sales": "sales"` permission map entry; the
  second lane adds none of them again.

### Rulings applied (G1, G3, G4)

- **AC-R3-3 [FE][T]** (S1, S2) Both screens carry a required Basis filter with Ordered and
  Delivered, defaulting to Delivered; changing it redraws the tables, the chart, the basis line
  and the export's title block (G1).
- **AC-R3-4 [BE][T]** (S2) The shared views By account, By month and By quarter carry no Channel
  filter: an agent's project order is in that agent's row and in the total; the "Dealer vs set
  apart" view carries Channel = Dealer (G4).
- **AC-R3-5 [BE][FE][T]** (S3) The dataset has a `brand` dimension (the product category prefix:
  SRT Sorento, BRT Bravat, CB Cabana, M Mocha, IB Iborn, IDC) and a Product brand filter; a shared
  view "By product brand" (agent x product brand) is published; a line whose product has no
  category is in `(blank)`, last, and in the total (G3 (d)).
- **AC-R3-6 [BE][T]** (S3) "Sean's sales this month by brand" asks `Debtor type or product
  brand?` and fetches nothing; "by debtor type" and "by product brand" each fetch their own axis
  with no question.

### The weekly scheduled Excel (G9 (b), S6)

- **AC-R3-7 [FE][E2E]** (S6) From `/`, by sidebar clicks, Sales > Sales report, pick the shared
  view "By account", Views menu > **Scheduled sends** opens "Scheduled sends: By account". The
  item shows only for a shared view of a sales report and only to holders of
  `sales.reports.schedule` (Q7).
  - Round 5: Owner ruling 26 Sep 07:34 Q7: superseded by AC-R5-1; there is no Views menu > Scheduled sends item and no `sales.reports.schedule` slug.
- **AC-R3-8 [FE][T]** (S6) The dialog lists one row per subscription (Person, Channel, When "Mon
  09:00", Enabled, Next send, Last sent) and **Add** with Person, Channel, Day
  (`SearchableSelect` each) and Time. Person lists only users holding `sales.reports.view` in the
  view's company. A new row is saved disabled. No UUID shows.
  - Round 5: Owner ruling 26 Sep 07:34 Q6 and Q7: superseded by AC-R5-1 and AC-R5-2; the automation form holds the view, the recipients, Weekly, Day and Time, with no Channel (email only). Enabled, Run now and the runs table are the Automation screen's own.
- **AC-R3-9 [FE][E2E]** (S6) Delete on a row is the deferred hard delete: the button counts down
  with Cancel and the row is removed when the window lapses; no confirm dialog.
  - Round 5: Owner ruling 26 Sep 07:34 Q7: the dialog is not built; deleting the weekly send is the Automation screen's existing delete, unchanged by this plan.
- **AC-R3-10 [BE][T]** (S6) The runner sends each enabled row whose `next_run_at` is due once, and
  advances `next_run_at` to the next weekday and time in the row's timezone (golden table across a
  week boundary); a second tick in the same window sends nothing (idempotency).
  - Round 5: Owner ruling 26 Sep 07:34 Q7: superseded by AC-R5-3 and AC-R5-9; the existing `automation_runner` runs it, `next_run_at` is the automation's own.
- **AC-R3-11 [BE][T]** (S6) The file is the view's saved filters and pivot with the period
  replaced by 1 January of the send date's year to the send date (Q8); its title block reads "As
  at <send date>" and the basis; a `report_xlsx` row owned by the recipient lands in their My
  Downloads.
  - Round 5: Owner ruling 26 Sep 07:34 Q8: (a) ruled, the period stands (AC-R5-4). Q7: the `report_xlsx` row is owned by the automation's owner, not by each recipient (AC-R5-4).
- **AC-R3-12 [BE][T]** (S6) Access at every send: a recipient who lost `sales.reports.view`, lost
  the company grant or is inactive gets nothing (no file built, no message), an `integration_log`
  failure `no_access` is written, and `next_run_at` still advances (G10).
  - Round 5: Owner ruling 26 Sep 07:34 Q7: the check moves to `recipient_check` and the run summary instead of `integration_log` (AC-R5-6).
- **AC-R3-13 [BE][T]** (S6) Email: one `email_outbox` row with event `sales_report_scheduled`, to
  the user's email, subject "<view name>, as at dd/mm/yyyy", the text header as the body and the
  download's file as the attachment. A user with no email is skipped and logged.
  - Round 5: Owner ruling 26 Sep 07:34 Q7: superseded by AC-R5-5; the email goes through the automation's template and Notification path with the attachment, and no `sales_report_scheduled` email event is added.
- **AC-R3-14 [BE][T]** (S6) WhatsApp, window open: the text header, then the file through
  `send_chat_attachment_for`. Window closed: the `sales_report_scheduled` template text with the
  totals line and "The Excel is in your My Downloads." (Q6).
  - Round 4: under Owner ruling 26 Sep 07:16 Q2 the window-closed branch is re-recommended; AC-R4-11 carries the new expected behaviour (Q6 still open). The window-open branch is unchanged. No linked contact, or
  `outbound_enabled` false: skipped and logged. Each send writes an `integration_log` row with
  `business_table` `sales.report_subscriptions`.
  - Round 5: Owner ruling 26 Sep 07:34 Q6, "we don't scheduled send whatsapp": superseded whole, window open and window closed; a scheduled send never goes by WhatsApp (AC-R5-8).
- **AC-R3-15 [BE][T]** (S6) **Send now** sends one message for that row in a worker job whatever
  its Enabled state and does not change `next_run_at`.
  - Round 5: Owner ruling 26 Sep 07:34 Q7: Send now is the automation's existing Run now (AC-R5-9).
- **AC-R3-16 [BE][T]** (S6) Routes `GET|POST /api/v1/sales/report-subscriptions`, `PATCH|DELETE
  .../{id}`, `POST .../{id}/send-now`: no token 401; without `sales.reports.schedule` 403; `sales`
  module disabled 403; a view that is not shared or not `sales` / `sales_yearly` is 422; another
  company's view is 403; a duplicate (view, person, channel) is 409.
  - Round 5: Owner ruling 26 Sep 07:34 Q7: superseded; the routes are not built. The existing `/api/v1/automation/automations` routes and `automation.automations.*` slugs apply, plus the trigger's view check (AC-R5-7).
- **AC-R3-17 [FE][E2E]** (S6) The Scheduled sends dialog is usable and not clipped at 375 and
  1280 (rows stack at 375; no sideways page scroll).
  - Round 5: Owner ruling 26 Sep 07:34 Q7: superseded by AC-R5-10 (the Automation form at 375 and 1280).
- **AC-R3-18 [T]** (S6) DoD: the Meta-approved `sales_report_scheduled` template is mapped on prod
  before any WhatsApp row is enabled; the owner receives one Send now by email and one by WhatsApp
  and enables their own row.
  - Round 5: Owner ruling 26 Sep 07:34 Q6: the Meta template gate and the WhatsApp Send now are withdrawn; DoD is AC-R5-12 (email only).

## Round 4: the owner's answers to round 2's Q1 to Q5 (26 Sep 07:16Z)

- Owner ruling 26 Sep 07:16 Q1: "okay", (b): both channel blocks in one Excel, one under the other.
- Owner ruling 26 Sep 07:16 Q2: "always text + file, no cutoff": every chatbot answer sends the text and the Excel file; no 12 figure rule, no override words.
- Owner ruling 26 Sep 07:16 Q3: "okay": person labels pre-filled from the code's name part; the owner corrects exceptions.
- Owner ruling 26 Sep 07:16 Q4: "okay": report sections are shared saved views.
- Owner ruling 26 Sep 07:16 Q5: "we need a sales schema and a sales module so we are more modular": module `sales` and Postgres schema `sales`; every new table of this plan in `sales`.

### Text + file on every answer (Q2)

- **AC-R4-1 [BE][T]** (S1, S2, S3, S5) Every answer of `crm_sales_analysis` sends the text and the
  Excel file of the same query: 1 cell ("total project sales this year"), 3 x 1 ("Sean's sales
  this month by brand"), 1 x 3 ("HANLIM this year vs last year"), 12 x 3 ("compare dealer sales
  2025 vs 2026 by month") and 61 x 1 ("sales by agent this year") each assert a text golden and
  one `report_xlsx` attachment. No cell count decides the format.
- **AC-R4-2 [BE][T]** (S1) The text is the whole answer: the header, one line per row (`label: RM
  a`, or `label: a | b | c` under a column name line), the totals line; never "Full table in the
  attached Excel" in place of rows. Every row is sent whatever the count (n8n chunks). The
  width of a wide table's lines follows Q9 (recommended: every column).
- **AC-R4-3 [BE][T]** (S1, S2, S3) A clarify question (`Sorento or Mocha?`, `Which Tan: TAN KH or
  TAN WL?`, `Debtor type or product brand?`, a ranked ask with no N), the dealer contact refusal
  and an error line carry no attachment and create no My Downloads row.
- **AC-R4-4 [BE][T]** (S1) The file is a `report_xlsx` My Downloads row owned by the contact's
  linked CRM user (else the act-as user, the low stock rule); ready within the sync window, it is
  returned as `attachments`; otherwise the text ends `The Excel follows here.` and the worker
  pushes the file exactly once (`deliver_to_contact_id` claim; a retried job sends nothing). A
  failed build sends the failure notice as text. The text is sent without waiting for the file.
- **AC-R4-5 [BE][T]** (S1) The parser has no `reply_format` key and the route no `deliver`
  switch; "as text", "in Excel", "as a file" and "send the report" change nothing (text + file
  either way).
- **AC-R4-6 [T]** (S1) The file name is the kernel's `<title>-<period>.xlsx`, and the file's
  figures equal the text's to the sen (one query, two formats).

### The yearly comparison's two blocks (Q1)

- **AC-R4-7 [BE][T]** (S1) With Channel = Dealer and Project, the export writes the DEALER block,
  then the PROJECT TEAM block, one under the other on one sheet, each with its variance row and
  chart (`WorkbookSpec.sheet_per`); with one channel, one block. The sponsorship workbook is
  unchanged.

### The sales module and the sales schema (Q5)

- **AC-R4-8 [BE][T]** (S1 or #1260 S6, whichever creates the module) The migration that creates
  the `sales` module runs `CREATE SCHEMA IF NOT EXISTS sales`; after `alembic upgrade head` the
  schema exists; running it after #1260's migration is a no-op; `alembic/env.py` is unchanged
  and autogenerate proposes no DROP of `sales`. Single alembic head.
- **AC-R4-9 [BE][T]** (S6) `ReportSubscription` is in `app/models/sales.py` with
  `__table_args__` `{"schema": "sales"}`; its FKs into core (`report_views`, `users`,
  `companies`) are unqualified; `sorento_crm_frontend/modules/sales/purge_tables.json` lists
  `sales.report_subscriptions`; a purge invariants test (as
  `tests/test_projects_module_purge_invariants.py`) shows uninstall deletes its rows and never
  issues `DROP SCHEMA`.
  - Round 5: Owner ruling 26 Sep 07:34 Q7: superseded; `ReportSubscription` is not built and `purge_tables.json` gains no entry from this plan (AC-R5-11).
- **AC-R4-10 [T]** (every slice) No new table of this plan is outside `sales`: a test lists the
  tables the plan's migrations create and asserts each has schema `sales`. Saved views stay
  `report_views` rows, person labels stay `sales_agents.person_label`, the debtor type is a
  column on `customers`.
  - Round 5: still holds, now with an empty list: the plan's migrations create no table (S6 adds the column `automations.run_weekday`), and the test stays as the guard for any later table (AC-R5-11).

### The scheduled WhatsApp send outside the window (Q6, still open)

- **AC-R4-11 [BE][T]** (S6) (Q6, recommended (b)) Window closed: the `sales_report_scheduled`
  template text with the totals line ending `Reply to this message and the Excel is sent here.`;
  the `report_xlsx` row is written with `deliver_to_contact_id` set. On that contact's next chat
  turn within 7 days the file is pushed once; a second turn sends nothing; after 7 days nothing is
  pushed and the file stays in My Downloads. The reply itself is answered as a normal message.
  Email is unchanged and always carries the file.
  - Round 5: Owner ruling 26 Sep 07:34 Q6, "we don't scheduled send whatsapp": superseded whole; no template text, no reply-to-fetch, no `deliver_to_contact_id` row from a scheduled send (AC-R5-8).

## Round 5: the owner's answers to round 4's Q6 to Q8 (26 Sep 07:34Z)

- Owner ruling 26 Sep 07:34 Q6: "we don't scheduled send whatsapp": a scheduled send never goes by WhatsApp; WhatsApp only answers when asked (the chatbot's text + file, AC-R4-1, unchanged).
- Owner ruling 26 Sep 07:34 Q7: "we can use our scheudled task and automation": the weekly send is an automation on System Management > Automation, run by the existing `automation_runner` scheduled task; no new table, dialog, route or slug (plan R5.3).
- Owner ruling 26 Sep 07:34 Q8: "okay": (a), year to date as at the send date.
- Q9: not answered; (a) stays written in (AC-R4-2).
- Q10 (new, plan R5.6): "the daily low stock email" named in the brief does not exist; the automation machinery is recommended (a) and written in below.

### The weekly Sales report email as an automation (S6)

- **AC-R5-1 [FE][E2E]** (S6) From `/`, by sidebar clicks, System Management > Automation > Add:
  the Trigger list offers "Sales report view"; picking it shows **Report view** (a
  `SearchableSelect` listing only shared views of the Sales report and Yearly comparison, by
  report and view name, no UUID) in place of Days before. Schedule offers Manual, Daily and
  **Weekly**; Weekly shows **Day** (`SearchableSelect`, Monday to Sunday) and Time. Recipients are
  the existing picker (users, roles). No Channel field anywhere.
- **AC-R5-2 [BE][T]** (S6) Saving stores `trigger_type` `sales_report_view`,
  `trigger_config.report_view_id`, `schedule_type` `weekly`, `run_weekday` 0 to 6 and `run_time`;
  `weekly` without `run_weekday` or `run_time` is 400; a `report_view_id` that is not a shared view
  of `sales` / `sales_yearly` is 400. Creating needs `automation.automations.add` (existing); no
  `sales.reports.schedule` slug exists.
- **AC-R5-3 [BE][T]** (S6) `next_run_at` for Weekly is the next `run_weekday` at `run_time` in the
  automation's timezone (golden table: Sunday 23:59, Monday 08:59, Monday 09:00, Monday 09:01,
  across a month end, in `Asia/Kuala_Lumpur`); after a run it moves 7 days on. `daily` and `manual`
  automations compute exactly as before.
- **AC-R5-4 [BE][T]** (S6) Each run builds one workbook with the view's saved filters and pivot
  under the view's company, the period replaced by 1 January of the send date's year to the send
  date (Q8); the title block reads "As at <send date>" and the basis. One `report_xlsx` My
  Downloads row is owned by the automation's owner.
- **AC-R5-5 [BE][T]** (S6) Each email carries that workbook as its attachment
  (`email_outbox.attachment_filename`, `attachment_storage_provider`, `attachment_storage_key`
  set) and the seeded "Sales report (weekly)" template's subject "<view name>, as at dd/mm/yyyy" and
  text header (report and view name, company, basis, the period, the totals line; no UUID). An
  existing automation (for example `days_before_promotion_end`) produces the same `email_outbox`
  row as before, with no attachment.
- **AC-R5-6 [BE][T]** (S6) Access at every run: a recipient who is inactive, lacks
  `sales.reports.view`, or lacks the view's company, and any Extra email address, gets no email and
  is listed in the run summary under `no_access`; the other recipients get theirs. With every
  recipient dropped, no email is sent and the run says so.
- **AC-R5-7 [BE][T]** (S6) A view deleted, unshared or moved to another report after saving, or
  the `sales` module disabled, gives a run that sends nothing and records the reason
  (`view_missing`, `view_not_shared`, `not_a_sales_report`, `module_disabled`); `next_run_at` still
  moves on.
- **AC-R5-8 [BE][T]** (S6) No scheduled send reaches WhatsApp: a run makes no Respond.io call and
  writes no `deliver_to_contact_id` row, whatever the recipient's linked contact (Q6).
- **AC-R5-9 [BE][T]** (S6) Run now sends once whatever the weekday and does not move
  `next_run_at`; a second `evaluate_due` in the same minute sends nothing (the runner's existing
  due check).
- **AC-R5-10 [FE][E2E]** (S6) The Automation form with this trigger is usable and not clipped at
  375 and 1280 (no sideways page scroll).
- **AC-R5-11 [T]** (S6) No new table: S6's migration adds only `automations.run_weekday` and the
  template row; the AC-R4-10 test's list of the plan's new tables is empty;
  `sorento_crm_frontend/modules/sales/purge_tables.json` gains nothing from this plan.
- **AC-R5-12 [T]** (S6) DoD: the owner creates the automation for one shared view, presses Run
  now, reads the email and its Excel beside the PDF, and enables it. `security-reviewer` has run.

## Every slice

- **AC-X-1 [FE]** No UUID in any screen, export or reply; agents, customers and companies are
  names or codes.
- **AC-X-2 [FE]** Every dropdown is `SearchableSelect` / `SearchableMultiSelect`, optional ones
  clearable.
- **AC-X-3 [T]** Tests run on Postgres (`tests/_pg_fixture.py`), seeding their own SO chain; CI
  holds no data.
- **AC-X-4 [T]** No em-dash or en-dash in code, goldens, docs or commits.

## Out of scope (from the plan)

Invoice feed, quantity measure, scheduled weekly send (G9), footnote storage (G8), dated person
labels, targets and commissions (#1260), text + file on every chatbot answer (Q2).

Round 4: text + file on every chatbot answer is now in scope (Owner ruling 26 Sep 07:16 Q2,
AC-R4-1 to AC-R4-6). Out: a text-only reply ("as text").

Round 3: the scheduled weekly send is now in scope (Owner ruling 26 Sep 07:06 G9, S6, AC-R3-7 to
AC-R3-18). Still out: invoice feed, quantity measure, footnote storage (G8 (c)), dated person
labels, agents seeing only their own rows (G10 (b)), daily or monthly sends, targets and
commissions (#1260), text + file on every chatbot answer (Q2).

Round 5: out: a scheduled send by WhatsApp (Owner ruling 26 Sep 07:34 Q6), a per-person scheduled
send table and the Scheduled sends dialog (Q7), a copy of the weekly file in each recipient's My
Downloads. In: the weekly email as an automation (AC-R5-1 to AC-R5-12).
