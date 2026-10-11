# Reports step 2: one report tool, one report frame (card)

Lane AGENT-LOOP-SPIKE, step 2 of `ARCH-scalable-chat-11oct.html` (owner Q3: reports first). Size L, lead
pattern. Branch `crew/reports-step2` from origin/main 56c5d0c60. No code yet.

## What changes for a customer

Report questions (top sellers, sales totals, outstanding, sales by month/channel) go through one tool
and one held "report frame". Follow-ups like "only dealer", "by month instead", "top 5", "amount
instead" change the last report instead of starting over or being misread as a customer or product.
Every value the bot carried from earlier is shown in the reply header: "Period: Oct 2026 (from
earlier)". Grants, dealer limits, scope and the reply look stay exactly as today.

## Today (code read 11 Oct, file:line)

| Tool | Route / executor | Measures | Group by | Scope |
| --- | --- | --- | --- | --- |
| crm_report_ask (sales ranking) | `report_ask.py:178` -> `reports/ask.py:152 run_ask` | amount, qty on SO lines; basis delivered / ordered / outstanding | customer, product, brand, category, sales_agent, location, channel, month (`ask.py:27`) | grant `sales_orders.sales_report`; `enforce_customer_scope` :331; dealer only product/brand/category/month (`ask.py:46`) |
| crm_top_selling_report | `orders.py:2060` -> `sales_report_service.py:599` (own SQL) | quantity, amount on SO lines | item, category; detail by customer / month | grant; `_top_selling_dealer_scope` :2034 |
| crm_sales_report | `orders.py:1778` -> `sales_report_delivered.py:232` (`delivery_order_lines` dataset) | delivered DO qty, amount by DO date; periods | customer, product, delivery_order, sales_agent | grant (when contact given); `enforce_customer_scope` :1958 |
| crm_outstanding_report | `orders.py:1545` -> `outstanding_report_service.py:175` | SO ordered / transferred / outstanding qty; DO pending qty | by customer or product, by location, rows | `enforce_customer_scope` :1671; `sales_orders.outstanding` gated in the lane |
| crm_sales_analysis | `sales/analysis.py:436` -> `engine.run` over `sales_yearly` | sales value | month / year / channel; basis ordered / delivered / invoiced | grant; linked customers refused :253 |
| crm_low_stock_report | `scm/low_stock_report.py:577` | creates a reorder run + XLSX | (filters) | grant; not customer-scoped |

Follow-ups today: only the sales ranking holds its last call (`Focus.sales_ranking_frame`, `state.py:176`;
refine `report_ask.py:641`, drill :710). Top selling holds `Focus.top_selling`; outstanding / sales report
narrow through a detail offer (`decide.py:52`). Prompt: the report addenda are about 8.7k of the 37.9k
parser tokens (REPORT_ASK 2.0k, TOP_SELLING 2.9k, SALES_REPORT 1.8k, LOW_STOCK_FILTERS 1.0k, SALES_ANALYSIS
0.6k, plus ORDER_STATUS / GROUP_BY / TOP_N parts); 11 parser keys exist only for reports.

## Proposal

1. **One read tool `crm_report`**, built by EXTENDING the existing report_ask engine (REPORT-ENGINE plan
   section 7 already plans exactly this fold), not a new facade:
   `crm_report(measure, basis, group_by[], filters{customer, product, brand, category, sales_agent, location,
   channel}, period, top_n, sort, detail)`. Its measure / dimension registry starts from `ask.py`
   CATALOGUE and gains rows that point at today's services. Grants, dealer keys, scope, rate limits and
   presenters are the services' own, unchanged.
2. **Low stock stays its own tool** (it creates a reorder run and a file: an action, "reads generic,
   actions specific"). The sales-analysis Excel path stays; only its read figures fold in.
3. **Old tools stay on the allow-list** until crm_report passes the gate for that measure.
4. **One report frame** held in focus, owned by the report intent (INTENT-FRAMES rule: a new intent starts a
   clean frame; one live frame). It replaces `sales_ranking_frame` + `top_selling` + the outstanding
   detail-offer carry. Refine = patch of the held frame; a different report kind or subject = new frame.
5. **Provenance in the reply header**: every carried or defaulted value is labelled ("Period: Oct 2026
   (from earlier)", "Basis: delivered (default)"). Defaults only where CLARIFY-POLICY allows; rankings stay
   must-ask for period / metric / top N (owner rule).
6. **Static prompt** (DP1 lesson): one report section replaces the report addenda, same text every turn,
   cache-friendly. No per-message prompt parts.

## Starting registry (code rows; add a measure = one row + one function)

| measure key | label | dataset / function (today) | dimensions | grant | dealer |
| --- | --- | --- | --- | --- | --- |
| sales_amount, sales_qty (basis ordered / delivered) | sales | `sales_order_lines_ask.py:196` DEFINITIONS | 8 ask dimensions | sales_orders.sales_report | product, brand, category, month |
| outstanding_qty, outstanding_amount (SO) | outstanding orders | same, basis outstanding | 8 ask dimensions | sales_orders.outstanding | same |
| do_pending_qty | DO not yet delivered | `outstanding_report_service.py:175` DO block | customer, product, location | sales_orders.outstanding | own customers |
| delivered_do_amount, delivered_do_qty (by DO date) | delivered sales report | `delivery_order_lines.py:271` via `sales_report_delivered.py:232` | customer, product, delivery_order, sales_agent, period grain | sales_orders.sales_report | own customers, no agent |
| sales_value (basis ordered / delivered / invoiced) | sales analysis | `sales_yearly` via `analysis.py:296` | month, year, channel | sales_orders.sales_report | refused (as today) |

Row shape: `{key, label, synonyms, dataset fn, allowed dimensions, grant, dealer allowed}`. Code, reviewed;
the model-facing enum is generated from the rows, so a new measure needs no prompt or schema edit. DP2 asks
for one new measure added in a day or less (candidate: `so_count`, already computed by the outstanding service).

## Contract choice, decided by eval: F vs T

Both prototypes share the same static report section and differ only in what the parser emits:

- **F (semantic frame)**: `{act: new|refine|switch, measure, dimensions[], filters[{axis, raw}], period,
  top, sort}`; code maps the frame to the crm_report call via the registry and resolves raw mentions.
- **T (tool call)**: the crm_report params directly (raw mentions in the id slots, the resolver still fills
  ids; the model never invents ids).

Measured on the report eval slice: pass / strict per report topic, prompt tokens and cache share, p95.
Tie goes to F: it survives tool changes and keeps the tool contract out of the prompt.

## DP2 (go-on bar, owner rulings)

Report-topic pass +10 pt vs today on the report slice; p95 at or under 8 s; no other topic worse (30-case
smoke); one new measure added in a day or less. **Measurability:** DEV holds only about 6 report-shaped final
turns (0309, 0312, p9ob, 0221, 0230, 0318), where one case is about 17 pt. A report slice must be labelled
first (Q3).

## Real examples (masked)

1. Refine (snapshot, staff): "Srtkt71-ss outstanding" -> "Dealer outstanding only" -> "Delivery order list".
   Under the frame: channel=dealer patches the held outstanding frame; the third message switches the view to
   rows of the same frame.
2. Regroup / measure (snapshot, staff): "top 200 project hot selling item" -> "quantity" -> "amount". Frame:
   top=200, channel=project, then measure qty, then amount; the header shows the carried period.
3. Filter (snapshot, staff): after a ranking, "Sold by [agent]" -> filter sales_agent=[agent] on the held frame.
4. Misread narrowing (snapshot, staff): "[Customer C] outstanding" -> "By products o/s brand Sorento" -> the
   bot offered "Sorento (customer)" and the user had to restate everything. Frame: group_by product + filter
   brand=Sorento patch; "Sorento" is read in the brand slot, not as a customer.
5. Stale subject (DEV p9ob, staff): "CBDR4003 stock" -> "Top sales Sorento and Mocha water closet this
   month" -> "Sorento and mocha" answered "No sales for BK007A". Frame: a new report frame has no product
   slot from the stock ask, so nothing leaks in.
6. Grant must survive (DEV 0309, dealer): "Top 20 [Customer S] order products" -> "Sales report is not
   enabled for your account." Unchanged.
7. Scenario (snapshot, staff): "generate low stock report until end of the year" stays on the low stock tool.

## Questions for the owner (with recommendations)

- Q1 Low stock stays its own tool (it is an action: reorder run + file); sales-analysis Excel stays.
  Recommend yes.
- Q2 Build crm_report by extending the report_ask engine (REPORT-ENGINE fold path) rather than a new facade
  over five services. Recommend extend (one engine, one registry, less code).
- Q3 Report eval slice: crew labels about 40 report cases from the snapshot's report chats (194 report turns,
  15 contacts; refine, regroup, switch, scenario), split dev / hidden 1:2; DP2 is measured on it plus the
  30-case smoke. Recommend yes; without it DP2 is not measurable.
- Q4 F vs T decided by that eval, tie -> F. Recommend yes.
- Q5 Provenance: carried values always shown in one header line; new defaults only where CLARIFY-POLICY
  allows; rankings stay must-ask. Recommend yes.

## Not in this step

No change to grants, scope or presenters; no new UI; no DB migration for the registry (code rows); prompt
diet stays off; low stock and the sales-analysis Excel unchanged.
