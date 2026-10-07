# REPORT-ENGINE scout: today's chatbot report tools and audience gates (2 Oct 2026)

Deliverable 1 of lane REPORT-ENGINE. Plan: `PLAN-report-engine.md`. Paths are relative to
`sorento_crm_backend/` unless prefixed `sorento_crm_mcp/` or `documentation/`. Read on main at
`6864cd0b`. Nothing here was measured against data: this sandbox has no dev DB copy.

## 0. The headline: the semantic layer already exists, half-wired

- `app/services/reports/registry.py` already declares a report as a `Dataset` of `Column`s
  tagged `dimension` / `measure` (`registry.py:79-124`), `SelectParam` filters where "the param
  IS the predicate" (`registry.py:194-209`), a `PeriodParam`, and a pivot view (rows x cols x
  measures). Company scope is declared per dataset and refused at import when missing
  (`registry.py:147-156`).
- `app/services/reports/engine.py` runs any such view as ONE grouped, parameterised SQLAlchemy
  statement (`_pivot`, `engine.py:633-733`), company arm fail-closed (`_predicates`,
  `engine.py:365-397`), cell cap 5,000 (`engine.py:53`), and `run_summary` exists precisely for
  "a caller that ranks the whole grouping itself (the chatbot's sales report)"
  (`engine.py:926-940`).
- The chatbot sales report already runs on it (`sales_report_delivered.py:7-10, 104-116`) over
  `datasets/delivery_order_lines.py`, whose catalogue ALREADY has a `sales_agent` dimension
  (`delivery_order_lines.py:210-217`). The owner's 2 Oct design note says exactly this
  (`documentation/plans/chatbot/PLAN-chatbot-selfref-scope-30sep.md:103-150`: "Top sales agent
  ... No new code: the dimension is in the dataset now; what is missing is the parser mapping
  ... and the decision who may ask it").

So the gap is not an engine. It is (1) a catalogue the parser can speak to, (2) one spec route,
(3) server-side audience rules for that route, and (4) folding the bespoke tools that predate it.

## 1. Report tools today

All business tools go through one path: `turn_runtime.make_tool_runner` (`turn_runtime.py:2661`)
-> `business.run_fetch` (`lanes/business/__init__.py:1272`) -> `fetch.entity_ids_transformer`
builds args (`fetch.py:643-1221`) -> MCP tool -> route; the MCP presenter renders
(`sorento_crm_mcp/sorento_crm_mcp/presenters.py`) and the lane passes the text through
(`fetch.py:2223, 2335, 2525`). `app/services/chatbot/dispatch.py` is the turn queue, not a tool
dispatcher (`dispatch.py:1-7`).

### 1.1 Aggregating (report-shaped) tools

| Tool | Parser slot | Route / service | Filters (param -> column) | Group by | Measures, "what counts" | Period | Sort / cap |
|---|---|---|---|---|---|---|---|
| **Sales report** `crm_sales_report` | `order_status=sales_report` (`chatbot_parser_prompt.py:131`); lane `__init__.py:1724-1728` | `GET /order-management/sales-report` (`orders.py:1778`) -> `delivered_sales_report` (`sales_report_delivered.py:198`) -> `engine.run_summary` over `delivery_order_lines` | `product_code` prefix >= 3 chars (`orders.py:1898-1905`, `_resolve_products` `sales_report_service.py:127`) -> `OrderLine.product_id` (`delivery_order_lines.py:188-189`); `customer_ids` / `customer_query` -> `Order.customer_id` (`:184-185`); `channel` -> segment CASE (`:55-59, 196-199`); `warehouse_codes` -> `OrderLine.warehouse_id` (`:192-193`); dates -> `Order.order_date` (`:74-77`). **No brand, category or agent filter** (`:262-265`) | `customer / product / delivery_order / sales_agent` (`sales_report_delivered.py:42`); default = period lines day/week/month by window (`:66-76`). Chatbot sets `group_by` ONLY from a picked drill option (`fetch.py:831-833`); options never offer `sales_agent` (`sales_report_delivered.py:48-52`) | `qty` = `OrderLine.quantity`, `amount` = per-line DO amount subquery (`delivery_order_lines.py:100-162, 235-236`). DO lines not cancelled, not deleted, dated, not `REP%` (`:62-69`) | Lane defaults product-only asks to current Malaysia year (`fetch.py:843-861`); open ends 1900/2199 (`sales_report_delivered.py:58-59`) | amount desc, qty desc, name (`:175-182`); 10 rows + "and N more" (`:45`); `top_n` never passed |
| **Top selling** `crm_top_selling_report` | `order_status=top_selling` (`chatbot_parser_prompt.py:537`); keys `rank_by`, `basis`, `rank_group`, `rank_direction`, `top_n` (`head/parser.py:214-287`) | `GET /order-management/top-selling` (`orders.py:2060`) -> `top_selling` (`sales_report_service.py:599`), its OWN SQL over SO lines | customer ids / query, channel on `SalesOrder.demand_class` (`sales_report_service.py:256-273`); `category_ids` -> `Product.category_id` (`:646-647`); `brand_ids` -> `Product.brand_id` (`:648-649`); `sales_agent_ids` -> `SalesOrder.sales_agent_id` (`:650`); **no product filter** (lane strips it, `fetch.py:868`) | `item` or `category` only (`:652-655`) | qty / amount on basis: `delivered` = `LEAST(qty_delivered, qty_ordered)` and that share of `line_total`; `ordered` = `qty_ordered` / `line_total` (`:540-549, 200-223`) | `COALESCE(required_date, order_date)` (`:193-197`); route default current MY year (`orders.py:2262-2266`) | metric then other metric then code, `bottom` ascending (`:660-662`); n 1..1000 (`orders.py:2183-2186`); no N -> asks how many (`fetch.py:922-926`) |
| **Sales analysis** `crm_sales_analysis` | `order_status=sales_analysis` (`chatbot_parser_prompt.py:389-437`); `group_by` month/year (`head/parser.py:233-235`) | `GET /api/v1/sales/analysis` (`api/v1/sales/analysis.py`) -> `engine.run` over `sales_yearly` (`sales_order_lines` / `billing_documents` by basis) | company, channel, basis, dates, `n` (`analysis.py:85-99`) | axes `month / year / channel` only (`analysis.py:75`) | ordered / delivered (SO lines, `datasets/sales_order_lines.py:203-207`) or invoiced (billing doc headers, `datasets/billing_documents.py:133`; **no product axis**, one row per document) | 1 Jan to today (`fetch.py:990-995`) | row total desc (`analysis.py:171-173`); n <= 100 (`:76`) |
| **Outstanding** `crm_outstanding_report` | `order_status in {outstanding, so_outstanding, do_outstanding, outstanding_both}` (`chatbot_parser_prompt.py:98`); lane `__init__.py:1645-1652` | `GET /order-management/outstanding-report` (`orders.py:1545`) -> `outstanding_report` (`outstanding_report_service.py:180`), own SQL | `product_code(s)` exact (`:108-111`); customer ids / query (`:306-313, 507-514`); `brand_ids` (`:304-305, 501-502`); `warehouse_codes` (`:314-315`); order date window (`:316-319`); `scope` so/do/both | fixed by subject: product -> location + customer; customer -> location + product; brand -> location (`:236-240`) | SO: open header and open line with `qty_ordered - qty_delivered > 0` (`:292-296`); DO: not yet delivered (`:478-506`) | none (all dates) | outstanding qty desc, total, name (`:70-87`); no N |
| **Low stock / reorder** `crm_low_stock_report` | `intent=low_stock_report` (`chatbot_parser_prompt.py:118`) | `GET /scm/low-stock-report` (`api/v1/scm/low_stock_report.py:536`); creates a reorder run (`sorento_crm_mcp/catalog.py:550-558`) | warehouse / product codes, dates (`__init__.py:1533-1585`) | none (workbook) | Excel: on hand vs reorder level, suggested qty, o/s, PO, incoming | - | workbook |

### 1.2 Lookup / list tools (not aggregations)

| Tool | Route / service | Filters | Group / sort / cap |
|---|---|---|---|
| Stock balance `crm_inventory_stock_balance_list` | `/inventory/stock/balance` (`stock.py:203`) -> `StockService.list_stock` (`inventory_service.py:633`) | product, warehouse, visibility policy (`inventory_service.py:810-818, 906`) | none; modes detailed / compact / availability (`stock_visibility.py:39`) |
| Incoming `crm_incoming_stock_list` | `/incoming-stock/list` (`incoming_stock.py:345`) | product, shipment, ETA window (`incoming_stock_service.py:734-744`) | ETA asc; limit 10, max 50 |
| SPO last receipt | `/procurement/spo-allocations/last-receipt` (`spo_allocations.py:34`) | product, warehouse | per-product window, `top_n` 1..50 (`spo_last_receipt_service.py:183-206`) |
| PO placed | `/procurement/purchase-orders/placed` (`purchase_orders.py:86`) | open lines, product, warehouse, expected date (`purchase_order_service.py:117-126`) | group product / supplier / date (`:45`); limit 50..500 |
| Last purchase cost | `/procurement/purchase-orders/last-cost` (`purchase_orders.py:34`) | product required | per (product, warehouse) latest, `top_n` 1..50 |
| SO / DO list | `/order-management/orders` (`orders.py:304`) | order, customer, product, transporter, brand, warehouse, delivery date | group customer / transporter / date / product (`order_service.py:286`); external cap 20 |

## 2. What "sales" means today: four definitions

| Path | Rows | Date | "Delivered" means |
|---|---|---|---|
| Sales report | DO lines (`delivery_order_lines`) | DO date | the DO line's amount (legacy DOC total split) |
| Top selling | SO lines (own SQL) | `required_date` else SO date | SO line's transferred share, `LEAST(qty_delivered, qty_ordered)` |
| Sales analysis | SO lines (`sales_order_lines`) / billing docs | SO date / doc date | as top selling, but filed by SO date |
| Old `sales_report()` | SO lines | `required_date` else SO date | dead code, no caller (`sales_report_service.py:277`) |

The MCP catalogue still tells n8n top selling uses "the same sales_order_lines source as
crm_sales_report" (`sorento_crm_mcp/catalog.py:782-784`), which stopped being true with PR #1401.
"Top 10 products this year" and "sales of X this year" can disagree today.

## 3. Can it answer the owner's asks today?

- **Top sales agent for product X:** backend yes (`GET /sales-report?product_code=X&group_by=sales_agent`,
  `orders.py:1832-1838`), chatbot no: the parser `group_by` enum has no `sales_agent`
  (`head/parser.py:225-239`), the drill never offers it, and fetch only forwards a picked option.
- **Top sales agent for brand Y:** no. The delivered dataset has no brand filter.
- **Top customer for brand Y:** no. Top selling has the brand filter but ranks items only;
  outstanding with a brand gives by-location only.

## 4. Audience gates any generic engine must respect

There is no audience enum; a turn's audience is assembled from per-contact facts keyed on the
Respond.io contact id:

| Fact | Source | Cite |
|---|---|---|
| Office staff (data scoping) | active "<brand> Office" access type | `contact_customer_scope.py:21-41` (`is_office_staff`) |
| Office staff (escalation) | `chatbot_profile.tier == "office"` | `turn/state.py:211-218` |
| Customer-scoped contact | rows in `respond_contact_customers` and not office | `contact_customer_scope.py:52-54, 86-115` |
| Dealer (stock) | stock visibility mode `availability` | `stock_visibility.py:39-45`; `turn_runtime.py:676-692` |
| Reveal grants | `contact_field_reveals`, default hidden | `contact_field_reveal_service.py:41-61`; `head/access.py:42-63` |
| Company | contact's companies | `engine.py:487-500` |

Grants that gate reporting: `sales_orders.sales_report` (sales report, top selling, sales
analysis; one tuple `contracts.py:393`, checked in engine `engine.py:4042-4063`, lane
`__init__.py:1597-1607, 1755-1760`, route `orders.py:1915-1936`); `sales_orders.outstanding`
(lane only, `__init__.py:1667-1670`; the route only echoes `so_refused`, `orders.py:1625-1632`);
`scm.low_stock_report` (`__init__.py:1516-1522`); `purchase_orders.cost` (lane only,
`__init__.py:1459-1470`); field reveals `inventory.sellable`, `purchase_orders.placed`,
`purchase_orders.supplier` stripped AFTER the tool returns (`fetch.py:2644-2715`).

Row scoping: customer-scoped contacts are confined to their linked customers before and after
the resolver (`engine.py:1380-1465, 1500-1520`), and at the route by `enforce_customer_scope`
(`api/v1/order_management/_contact_scope.py:32-78`, used by the sales report at `orders.py:1958`)
**only when `contact_id` is sent**. The sales report already refuses `group_by=sales_agent` for
a customer-scoped contact (`orders.py:1965-1974`). Sales analysis refuses any linked contact
(`api/v1/sales/analysis.py:248-259`).

Known holes a generic engine must not inherit:

1. **G1:** a contact with no links and no office type is not scoped at all
   (`contact_customer_scope.py:52-54`). Today such a contact holding `sales_orders.sales_report`
   would pass the sales-report agent check above.
2. **Lane-only gates:** outstanding SO grant, purchase cost, restricted fields are enforced in the
   chatbot lane, not the route. A new route must enforce its own rules from `contact_id`.
3. Two "staff" tests coexist (access type vs profile tier).
4. Route RBAC (`order_management.orders.view`, `orders.py:1852`) is on the act-as user, never
   per contact.

**Parser per audience (#1429, unmerged, branch `claude/parser-per-audience-xfg4cw`):** strips
prompt blocks by reveal grant (`sales_orders.sales_report` removes SALES REPORT / SALES ANALYSIS
/ TOP SELLING). Its plan is explicit that hiding a block "is a size and focus change, never the
gate". `tier_gate` (`lanes/business/tier_gate.py`) only scopes promotions and attachments by
dealer / office / end_user tier and does not gate any data domain.

**Sales agent columns:** `sales_orders.sales_agent_id` (`models/order.py:527`),
`billing_documents.sales_agent_id` (`models/finance.py:62`), `customers.sales_agent_id` = the
customer's assigned salesperson (`models/order.py:146-152`), `orders.salesman` free text, no FK
(`models/order.py:333`). `sales_agents.contact_id` links an agent to a Respond.io contact
(`models/sales_agent.py:93-100`). The delivered dataset attributes a DO to its SO's agent, and a
DO without SO or SO without agent groups as `(no agent)` (`delivery_order_lines.py:46, 226-233`).
