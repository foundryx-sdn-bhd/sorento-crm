# UAC: REPORTS-STEP2

Plan: `PLAN-reports-step2-11oct.md`. Card: `CARD-reports-step2-11oct.md`. Tests under
`sorento_crm_backend/tests/reports_step2/` (backend) and `sorento_crm_mcp/tests/` (MCP).

## S1: measure registry + crm_report over the ask engine (sales rows only)

Contract (pinned for tester and coder):

- Module `app/services/reports/measures.py`:
  `@dataclass(frozen=True) Measure(key, label, synonyms: tuple[str, ...], basis, figure, dimensions: tuple[str, ...],
  grant: str, dealer_dimensions: tuple[str, ...], run: Callable)`; `MEASURES: dict[str, Measure]`;
  `register(measure)` (duplicate key raises ValueError); `get(key)` (unknown raises KeyError);
  `keys() -> tuple[str, ...]` in registration order.
- S1 rows (all over `reports/ask.py` `run_ask`, basis x figure): `ordered_amount`, `ordered_qty`,
  `delivered_amount`, `delivered_qty`, `outstanding_amount`, `outstanding_qty`. dimensions = `ask.DIMENSIONS`,
  dealer_dimensions = `ask.DEALER_KEYS`, grant `sales_orders.sales_report`.
- Route `GET /api/v1/order-management/report` in `app/api/v1/order_management/report_ask.py`, same guards as
  `/report-ask`: params `measure` (required, a registry key), `group_by`, `top_n`, `sort`, `date_from`,
  `date_to`, the same filter ids (`product_ids`, `brand_ids`, `category_ids`, `sales_agent_ids`,
  `customer_ids`, `customer_agent_ids`, `warehouse_codes`, `channel`, `product_code`), `contact_id`, `space_id`.
- MCP: ToolSpec `crm_report` in `sorento_crm_mcp/catalog.py`, read_only, restricted_fields like
  `crm_report_ask`, `measure` described with the registry keys; presenter = the report_ask presenter. Chatbot
  allow-list gains `crm_report`; `crm_report_ask` and the other report tools stay.

Acceptance:

- AC-1 For each of the 6 S1 measures, `/report` returns the same rows, totals and keys as `/report-ask` with
  the matching basis and measure for the same inputs (equivalence on seeded data).
- AC-2 Unknown `measure` -> 422 whose detail lists the valid keys; missing `measure` -> 422.
- AC-3 Period required, group_by needs top_n, empty filter -> the same 422 codes as `/report-ask`.
- AC-4 Grant missing (`sales_orders.sales_report`) -> the same refusal as `/report-ask`.
- AC-5 Customer-scoped (dealer) contact: rows limited to its own customers; a group_by outside the measure's
  dealer_dimensions (e.g. sales_agent, customer, location, channel) -> 403 `report_dimension_not_allowed`.
- AC-6 A group_by outside the measure's dimensions -> 422.
- AC-7 Adding a measure is one `register(Measure(...))` plus its function: a test registers a fake measure and
  `/report` serves it with no other code change; `keys()` and the MCP description include it.
- AC-8 Duplicate key -> ValueError; `get` of an unknown key -> KeyError.
- AC-9 The MCP `crm_report` ToolSpec is read_only, carries contact_id/space_id, and its `measure` description
  names every registry key; the chatbot read-only allow-list includes `crm_report` and still includes the old
  six tools.
- AC-7 note: the MCP description lists the keys as a literal (no import of the backend registry from the MCP
  package); the sync is checked by a backend test in S4.
- AC-10 Rate limit and brand scope behave as `/report-ask` (same settings, same stamping).

## S2: the chatbot asks crm_report; one report entry in Focus; refine / switch; provenance

Scope: sales rankings only (the six S1 measures). Top selling, sales report, outstanding, sales analysis and the
low stock read move in S4. No parser prompt change (S3); the lane maps today's parser keys (order_status
sales_ranking, basis, measure/rank_by, group_by, top_n, filters, ranking_refine, ranking_drill) as today.

Contract (pinned for tester and coder):
- `Focus.report` replaces `Focus.sales_ranking_frame`: `{"measure": <registry key>, "args": {<crm_report params
  without contact/space>}, "shown": <drill rows or absent>}`. Reading a stored session that still has
  `sales_ranking_frame` maps it to `report` (measure = f"{basis}_{measure}") so a refine after deploy works.
- The lane calls `crm_report` with `measure=<registry key>` wherever it called `crm_report_ask`; the reply text is
  the presenter's, as today.
- `/report` echoes `measure_key` (the registry key) next to the existing `measure` figure; the presenter is unchanged
  except for provenance below.
- Provenance: the call carries `carried` (list of param names whose value came from the Focus entry, not from this
  message); `/report` accepts and echoes it; the presenter appends " (from earlier)" to each fact line whose param is
  in `carried` (Period, and each filter label). Not sent on a fresh ask.

Acceptance:
- AC-11 A fresh sales ranking ask calls `crm_report` (not `crm_report_ask`) with the registry key; for the same
  inputs the reply text equals what the crm_report_ask path printed (no "(from earlier)").
- AC-12 After a ranking, `Focus.report` holds the measure key, the args and `shown`; `Focus.sales_ranking_frame` is
  no longer written.
- AC-13 A stored session with only `sales_ranking_frame` (old shape) still refines: "top 5" re-runs the held args
  with top_n 5 through `crm_report`.
- AC-14 Refine patches only what the message changes: "top 5", "amount instead", "ordered instead" (basis),
  "only dealer" (channel), "last month" (period), a brand / category word; every other held arg is unchanged.
- AC-15 Provenance: on a refine, each fact line whose value was carried ends with " (from earlier)"; a value said in
  this message has no mark; a fresh ask has no marks. One fact per line, "Label: value", no " · " joins.
- AC-16 Switch: a report ask that names a different group_by without a refine or drill reading starts a fresh
  entry (no held filters leak); drill behaviour (`ranking_drill` scoped / unsure / global) is unchanged.
- AC-17 Dealer: a held entry is re-checked on refine exactly as today (report_ask.py:647-657); a dealer refine that
  asks for a staff-only dimension is refused, never served.
- AC-18 (revised 13 Oct, owner rule: topic switch and back) The held report survives a turn in another domain: after a
  stock question, a later "back to the ranking, top 5" refines the held ranking. A topic reset or a new report clears
  or replaces it, and a fresh ask with its own axis or period is a new ask, never a stale refine.

## S4: the remaining measures (backend + MCP only; no lane change)

The chatbot keeps calling the old tools for these kinds until S5 switches the lane per kind after the gate.

Contract:
- `Measure` gains `kind` (default "ranking" for the six S1 rows). `/report` returns the measure's native body plus
  `report_kind`; the MCP presenter renders `crm_report` with the presenter that kind uses today (ranking ->
  `_report_ask`, outstanding -> `_outstanding_report`, sales_report -> `_sales_report`, sales_analysis ->
  `_sales_analysis_*`, low_stock -> the low stock envelope), so replies are byte-for-byte today's.
- New rows (each run adapts the S1 signature to its service; params a kind does not support -> 422):
  - `order_outstanding` (kind outstanding) -> `outstanding_report_service.outstanding_report` (:175); `scope` so | do
    | both, default both (CLARIFY-POLICY row 14); needs a subject (customer or product) as the route does today;
    grant `sales_orders.outstanding`; dealer = own customers (enforce_customer_scope as orders.py:1671).
  - `delivered_sales` (kind sales_report) -> `sales_report_delivered.delivered_sales_report` (:232); group_by customer |
    product | delivery_order | sales_agent; grant `sales_orders.sales_report`; dealer: own customers, no sales_agent.
  - `sales_value` (kind sales_analysis) -> the `sales_yearly` engine run as `api/v1/sales/analysis.py` does (:198-331);
    axes month | year | channel; basis ordered | delivered | invoiced; grant `sales_orders.sales_report`; a contact
    linked to a customer is refused (as :253-258). The Excel path stays on its own tool.
  - `low_stock` (kind low_stock, owner Q1 split) -> `low_stock_report_service.build_low_stock_view` (:271) over the run
    from `reorder_run_service.today_or_latest_run` (:466); filters categories / suppliers / warehouses; grant
    `scm.low_stock_report`, supplier column under `purchase_orders.supplier`; company from the contact; NEVER creates a
    run, a download row or a job; the reply says which run date it reads.
- `KNOWN_GRANTS` gains `sales_orders.outstanding` and `scm.low_stock_report` (reviewed here).
- DP2 timing: one new measure `so_count` (count of distinct SOs, from the outstanding service's `so_count`) added as
  a separate commit; its commit message states the time it took.

Acceptance:
- AC-19 For each new measure, `/report?measure=<key>` with the same inputs returns the same body as today's route for
  that kind (outstanding-report, sales-report, sales-analysis read, low-stock read view), and the MCP reply text is
  identical to today's tool's.
- AC-20 Every guard of today's route for that kind holds on `/report` (grant, dealer scope, linked-customer refusal for
  sales_value, subject required for outstanding, rate limits where the old route has one).
- AC-21 `low_stock` writes nothing: the reorder_runs, downloads and job queue counts are unchanged after a call; with
  no completed run the reply says there is no low stock snapshot yet (never stuck).
- AC-22 A param the measure's kind does not support -> 422 naming the param.
- AC-23 `so_count` registered with one row + one function; `/report?measure=so_count` serves it; the MCP measure
  description lists every key (and a backend test compares the MCP literal with `measures.keys()`).

S4 as built (11 Oct, lead notes): low_stock filters are categories and suppliers only (`build_low_stock_view` has no
warehouse parameter); a contact without the supplier grant gets the view with `split="none"`; the run is picked by
`today_or_latest_run`, an in-progress first run answers status `preparing` ("still being prepared"); the MCP presenter
renders `no_snapshot` / `preparing` only, the rendering of a full low stock view in chat is S5. `register()`'s
dealer-dimension subset rule applies to ranking measures; non-ranking kinds keep their own route's dealer rules.
`so_count` took about 2 minutes (one row, one function, one catalogue line): DP2 "new measure <= 1 day" met.

## S5a: the lane calls crm_report for outstanding and the sales report; low stock read view renders

Crew go 11 Oct. Behaviour-preserving: the customer sees byte-identical replies. Old tools stay on the allow-list.
Scope decision (lead): only kinds whose chat reply today comes from a presenter alone switch now: outstanding
(`crm_outstanding_report`) and the delivered sales report (`crm_sales_report`). Sales analysis (the chat path also
starts the Excel file) and low stock (the chat ask always generates a reorder run) need a parser signal for "show me"
vs "make the file"; they switch in S3 together with the parser change. Top selling stays on its own tool until S3
(its presenter differs from the ranking's).

Contract:
- The lane builds `crm_report` with `measure=order_outstanding` (scope, subject, filters as today's args) wherever it
  called `crm_outstanding_report`, and `measure=delivered_sales` wherever it called `crm_sales_report`. The SO-grant
  redirect (scope=do + so_refused) stays where it is today in the lane, and /report applies its own as well.
- The chatbot render functions for those kinds (`fetch.py` `_outstanding_report_output`, `_sales_report_output`)
  receive the same envelope as before (the MCP presenter output for `report_kind`).
- MCP presenter: a full `low_stock` read body (no `status`) renders a proper chat reply: one fact per line ("Label:
  value"), the run date ("As of: <date>"), the count of low items and the top items by shortage (name and code,
  supplier only when the contact holds the supplier grant), never the "could not run" line. `no_snapshot` /
  `preparing` unchanged.

Acceptance:
- AC-24 For each switched kind, a fresh ask records an MCP call to `crm_report` with the measure key and the reply text
  is byte-identical to the old tool's reply for the same parser emission and data.
- AC-25 Follow-ups that work today for those kinds (outstanding detail offer / narrowing reply, sales report detail
  pick) still produce identical replies through `crm_report`.
- AC-26 A contact without `sales_orders.outstanding` asking outstanding still gets the DO-only reply it gets today.
- AC-27 MCP: `present_response("crm_report", <full low_stock body>)` gives a reply with "As of:", the low item count and
  item lines; no supplier names when the body has no supplier column; never the could-not-run text.

## S3a: the six report failure shapes from the DEV slice (crew go 11 Oct; reply/tool layer first)

Measured on the 13 DEV report cases on the branch (parser v50 pinned). Six fail by their expectation notes. Each AC
below is one shape; the case id is the DEV evidence. No keyword or regex rules on the user's text: the parser's
reading drives every decision; code changes are in resolution, tool args, state and reply copy.

- AC-28 (rpt-025) A product code shared by two companies the contact belongs to (e.g. Sorento and Mocha twins):
  outstanding and the delivered sales report count the lines of EVERY same-code product in the contact's companies,
  not one arbitrary row (`outstanding_report_service._resolve_products` :97-120 keeps one row per code today; check
  `sales_report_service._resolve_products` :127 and `sales_report_delivered` for the same pattern).
- AC-29 (rpt-012) An outstanding ask naming two locations ("for location BRW-IB & BRW-AM") answers both: the call
  carries both warehouses and the reply shows each location's figure and the total (the report's by-location
  breakdown), not only the first location.
- AC-30 (rpt-018) After an outstanding summary that offered the delivery order list, a message that asks for that
  list in other words ("Delivery order list") returns the DO rows of the SAME held report (detail do), not a fresh
  summary.
- AC-31 (rpt-032, rpt-035) An ask the bot cannot answer from its data, read by the parser as such: overdue /
  receivable / payment balance / account statement -> wording line 1; dealer locator ("nearby dealer") -> line 2
  (draft wording filed for the owner; zh/ms via the label catalog). No figures shown, never stuck. Needs the parser
  to classify these asks (prompt rule in a new parser version for the eval; the switch to production is the owner's).
- AC-32 (rpt-015) A compound message "<code> got stock? got outstanding for <customer>": both halves are answered;
  the code resolves to the one product the customer typed (CB88SS/diy -> CB88SS-DIY), not a family of 20; the
  outstanding half is filtered by the named customer.
- AC-33 The 7 DEV report cases that pass today still pass (no regression), measured live on the slice.

## S6: the owner hand test and the golden replay set (13 Oct)

Parser fields and held state only; no keyword rules on the user's text. Each line is covered by a case in
`tests/chatbot/replay_reports/` or by `tests/chatbot/test_reports_step2_handtest1.py`.

- AC-34 A follow-up that only changes filters the held ranking carries (a channel, a brand, a basis that differs from the
  held one) refines the held ranking: same axis, count, period, measure, brand. It does so whether the parser reads it as a
  company analysis or a ranking, and whatever it says in `ranking_refine` and `domain_in_message`. A new axis, a new
  period, a named company, a non-brand subject, or a basis the ranking cannot do ("invoiced") is its own ask.
- AC-35 The analysis company question ("A or B?") is stored as an open question with its options. The reply is read by the
  parser (a declared pick, or its own `sales_company`): it re-runs the analysis for that company with the filters the first
  message carried. An unknown company is asked again once, then given up and cleared; a cancel ends it; "all" says one
  company is needed and asks again.
- AC-36 The held report survives an answer in another domain: after a stock ask, "back to the ranking, dealers only" finds the
  held ranking (or the top selling slot). A new report replaces it, and a topic reset drops it. A fresh ask on another axis
  or with its own period stays a new ask.
- AC-37 The reply "all" to "Top N customers of the K sales agents above, or across all customers?" runs the customers
  ranking across all customers and keeps the held brand, period, measure and basis; only the agent filter goes.
- AC-38 The company a contact picked for the sales analysis is held across a channel, period or basis narrowing, which
  keeps it; a narrowing never asks the company question again. The exact product code a report was narrowed to is held
  through its drill picks (number or typed label): the drill is that code's, not the family's.
- AC-39 A chat analytics follow-up that names no axis, count or period (an asker class, a measure) narrows the chat report
  that just ran: its axis, period, count and filters stay, and no period is asked again.
