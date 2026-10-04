# UAC - Chatbot: a linked contact's sales asks run the customer sales report

Plan: `PLAN-chatbot-selfref-scope-30sep.md`. Crew lane CHATBOT-SELFREF-SCOPE, PR #1401
(the engine/resolver half is parked on PR #1403). Parent UAC:
`chatbot-customer-scope-29sep-acceptance-criteria.md` (AC-CS-10, 11, 22 to 26 stay green).

## Journey

A dealer's purchaser, linked to six customer accounts, asks "what is my sales this month".
The bot answers with the customer sales report over all six accounts, as text on WhatsApp:
the period, the ordered and confirmed figures, the outstanding, by month and by product.
Never "Could not run the sales report right now." When a tool call fails behind the bot,
the trace says which route, which status and why, and every customer-scope decision is
readable in Chat history.

- **AC-SR-10** `[BE]` Migration `selfref_0001_n8n_sales_view` grants `sales.reports.view` to
  role `integration_n8n`, creates the permission row when absent, is idempotent, grants
  nothing to other roles, and its downgrade removes exactly that grant.
- **AC-SR-11** `[BE]` A scoped contact's `order_status: sales_analysis` ask is answered by
  `crm_sales_report` over the linked ids with the verdict's date window, as text with no
  attachment; `crm_sales_analysis` is not called; the trace carries
  `decision: sales_analysis_answered_as_sales_report`. An unlinked or staff contact keeps
  `crm_sales_analysis`.
- **AC-SR-12** `[MCP]` A non-2xx answer from the CRM keeps the route's own keys and gains
  `status_code`, `error` (one line: method, path, status, the route's code or message) and
  `http_error`; `present_response` renders it for every presenter tool as `has_result: false`,
  `error`, `status_code`, `detail`, empty `response` and `answers`.
- **AC-SR-13** `[BE]` `run_fetch` given that envelope returns a `kind: error` fragment whose
  error names the status and code, never a `has_result: true` line, and the `tool` trace
  event carries the envelope with its `error`, `status_code` and `detail`.
- **AC-SR-14** `[BE]` Every scope decision writes a `customer_scope` trace event: the gate's
  refusal (`typed_customer_word_outside_links`), the screen's refusal
  (`resolver_matched_only_other_customers`, with the dropped ids), the scoped-to-links pass
  (with `ids` and `self_reference`), the fetch refusal and the tier-probe refusal (with the
  dropped ids). `compose_trace_detail` projects them as `customer_scope` and the Chat history
  turn drawer shows a "Customer scope" panel, empty when the turn made none.

## Sales report v4: delivered basis and drill-downs (owner approval of mock v4, 2 Oct 2026)

Mock: `documentation/mockups/sales-report/index.html` (made-up figures). Design note: plan
section "Design note: one dimension/measure model". Q1 to Q5 answered (a).

- **AC-SR-20** `[BE]` Dataset `delivery_order_lines` (`app/services/reports/datasets/`): one
  row per DO line of a DO that is not cancelled, not deleted and whose number does not start
  with `REP`; date basis `orders.order_date`; company scope fail-closed like every dataset.
- **AC-SR-21** `[BE]` Measure `amount`: a legacy DO (`source_book` NULL) counts its DOC total
  once (the value its lines repeat); an AutoCount DO sums its line totals. Each line's share
  of the DO amount is weighted by qty x unit price x (1 - discount) (equal shares when every
  weight is 0), so grouping by product, customer or period sums to the same Total to the sen.
  Measure `qty` sums `order_lines.quantity`.
- **AC-SR-22** `[BE]` Dimensions `customer`, `product`, `sales_agent`, `location`, `channel`,
  `day`, `week` (Monday to Sunday), `month`, `delivery_order`, `all`. `channel` reads the
  account's `market_segment_code` (project or contract = Project, else Retail); an account
  with no segment is in every channel (a channel filter keeps it).
- **AC-SR-23** `[BE]` `GET /order-management/sales-report` answers the delivered report:
  `total {qty, amount}`, `periods[]` (grain day when the window is at most 7 days, week when
  at most 31, else month; latest first; only periods with a delivery), and with
  `group_by=customer|product|delivery_order|sales_agent` a ranked `rows[]` (by amount desc;
  `delivery_order` latest first) capped at 10 with `more` = the rest, plus `options[]`, the
  drill-downs the reply offers. Any other `group_by` is 422.
- **AC-SR-24** `[BE]` `options[]` offers By customer only when the scope holds more than one
  account, By product only when more than one product delivered, and Delivery orders; never
  the view just shown. Numbering continues after the list rows (rows 1 to N, options N+1...).
- **AC-SR-25** `[BE]` With a contact: the contact's stock visibility policy caps locations. A
  named location outside it answers `status: refused` with "Sorry, <location> isn't one of the
  locations you can check."; an include list of `[]` answers the header with no rows (the
  engine is never called with `[]` as a filter).
- **AC-SR-26** `[BE]` Channel line: printed only when the scoped accounts span more than one
  segment; a channel filter the accounts do not span is ignored.
- **AC-SR-27** `[MCP]` Reply: bold `*Customer:*` (every account's full name), `*Product:*`,
  `*Channel:*`/`*Location:*` when they apply, `*Delivery date:*` (one date for a one-day
  window), blank line, `*Total:* Qty n, RM v`, one line per period (`dd/mm/yyyy`, `dd/mm to
  dd/mm`, `Mon yyyy`), blank line, `*Drill down:*` and the numbered options. A drill prints
  `*By product*, delivery date <window>`, the rows `n. NAME: Qty q, RM v`, `and N more`,
  `*Total:*`, then the remaining options. No months: header plus "No sales found." and no
  options.
- **AC-SR-28** `[BE]` Lane: the options are the `sales_report_detail` open question; a number
  or a label ("by product", "DO", "delivery orders", "by customer") re-runs the report with
  the same window, links, location and channel and the matching `group_by`; an out-of-range
  number re-asks the same options; a new ask drops the offer (existing rules unchanged).
- **AC-SR-29** `[BE]` Own-account miss stays a final answer (no escalation), as shipped.
