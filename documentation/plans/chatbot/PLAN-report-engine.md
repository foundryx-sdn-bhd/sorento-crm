# PLAN: Chatbot report engine (one catalogue, one spec, one executor)

Status: slice 1 rebuilt on SALES ORDER lines (owner direction 4 Oct 2026, section 00, which wins
over every section after it); clarify round (metric then basis asked, no silent defaults) in
progress. Earlier: owner chose option (a), 4 Oct 2026 (full asks reliable, short follow-ups
degrade safely, measured on live use). The prompt md5 is recorded in the PR with each prompt
change. Waiting on CI and the owner's hand test (`laneboard/scripts/1447.md`, on shared dev,
prompt picked in the console, the shared `production` label unmoved).
FULL track (new API-key route, per-audience access rule, data-only migration
`report_engine_0001_prompt`: owner moves the parser `production` label after deploy). Card answered 2 Oct 2026 (`report-engine-behaviour-card.md`
revision 2); section 0 below records how the answers change this plan, and wins over the
sections after it where they differ.
Lane: REPORT-ENGINE. Evidence: `report-engine-inventory.md` (deliverable 1, file:line for every
claim below that is not cited inline). UAC: `report-engine-acceptance-criteria.md`.

## 00. Owner direction 4 Oct 2026: rank from sales orders, clarify instead of default

Supersedes every DO-based statement below (sections 0 to 12 describe the first build and are
kept as history).

- **Source.** DO data is thin (the AutoCount push went live 3 Oct; prod has DOs for the second
  half of 2026 only) and no DO carries `sales_order_id` (only the AutoCount push sets it, from
  `ref_doc_no`: `autocount_doc_ingest_service.py:904`, `:1065`). So every `crm_report_ask` basis
  ranks SALES ORDER lines with the SO's own agent (`SalesOrder.sales_agent_id`).
- **One definition with top selling.** `datasets/sales_order_lines_ask.py` builds one definition
  per basis from `sales_report_service`, imported, never copied: `delivered` and `ordered` are
  `_basis_figures(basis)`, `outstanding` (new) is `_per_line_exprs()` outstanding (ordered minus
  delivered while the SO and the line are open). Every line is dated by `_bucket_expr()` (the
  line's required date, else the SO date), cancelled SOs and lines are out, and a group zero on
  the basis is not ranked: top selling's own rules. Parity tests pin the ranking by product equal
  to `top_selling(...)`. `delivery_order_lines` stays for the chatbot delivered sales report only.
- **Category.** A category word resolves through top selling's `_top_selling_category_ids`
  (`lanes/business/__init__.py:522`): "water closet" is every category whose `class_label` is
  `Water Closet`. An ambiguous word lists codes, as top selling's picker does.
- **Clarify, never default (Q1 ruling).** A ranking that names no metric asks `By quantity or by
  amount?`, then a missing basis asks `Delivered, ordered or outstanding?`; plain top selling
  asks its own basis question the same way. One shared function picks the next question for both
  lanes; the answer is bound from the parser's keys (semantic only), never the message text.
- **Rejected:** resolving the agent from the DO's own `orders.agent` (built, then reverted on this
  ruling: rankings no longer read DOs).

- **Access (Q1, Q2).** The gate is the reveal grant `sales_orders.sales_report`
  (`contact_field_reveal_service.py:51`), checked in the route off `contact_id`, the shape the
  sales report route already uses (`orders.py:1918-1936`). No office-staff rule. Two audiences:
  `DEALER` = the contact is customer-scoped (`enforce_customer_scope` returns its links,
  `_contact_scope.py:32-78`; `ContactCustomerScope.enforced`, `contact_customer_scope.py:52-54`),
  `FULL` = every other grant holder. A DEALER may use dimensions and filters product, brand,
  category, month only (plus its forced own-customer filter); `FULL` may use the whole
  catalogue. The "STAFF" ladder of 3.1 / 3.4 is replaced by this.
- **Period (Q3).** Required. No catalogue default; the route answers 422 `period_required`.
- **Basis (Q4).** `delivered` (DO lines, DO date, `delivery_order_lines`) and `ordered` (SO
  lines, SO date). The spec carries `basis`, default `delivered`, and the reply header always
  names it ("by delivered sales"). The ordered basis gets an ask definition over SO lines that
  reuses `sales_order_lines`' base and per-line expressions (`datasets/sales_order_lines.py:93-115`,
  `sales_report_service._per_line_exprs`), registered nowhere, so the Reports screen's Yearly
  comparison catalogue does not change. Its amount is the Yearly comparison's ordered value
  (`exprs["ordered_value"]`), its qty `exprs["ordered_qty"]`.
- **Top N (Q5).** A ranking (one `group_by`) requires `top_n`; missing -> 422 `top_n_required`.
- **Asking back.** The lane collects period and top N through the shared required-field helper
  of LOWSTOCK-FILTER-ASK (#1445, `required_fields.collect` with an `AskType` per ask). Its API is
  not settled yet, so slice 1 is built in two parts: **1a** catalogue, datasets, spec, route,
  MCP tool + presenter, parser vocabulary (no dependency); **1b** the lane wiring
  (`spec_from_parse` + the `AskType("sales_ranking", period, top_n)`) once crew relays the
  helper's API.

## 1. Problem

Owner (2 Oct 2026): "How can our chatbot reporting be flexible - e.g. top sales agent for a
certain product or brand - plus common filters? ... so we don't keep developing a new reporting
tool for every report users want."

Today every report angle is its own tool with its own filter list, its own SQL and its own
definition of "sales" (inventory section 1-2):

- 4 aggregating tools (sales report, top selling, sales analysis, outstanding), each accepting a
  different subset of the same 8 filters, each grouping by a fixed handful of dimensions.
- 3 definitions of "delivered sales" in live code (DO lines by DO date; SO lines by required
  date; SO lines by SO date). Two of the tools can give different totals for the same words.
- "Top sales agent for product X" needs one word added to a parser enum; "for brand Y" needs a
  filter that exists on top selling and outstanding but not on the sales report. Each such gap is
  a lane today.

The thing to stop is the per-angle tool, not the per-domain dataset: rows that MEAN something
different (DO lines vs open SO lines vs stock) stay separate datasets, and every angle over one
dataset is data, not code.

## 2. What already exists (and is reused, not rebuilt)

`app/services/reports/` is already the semantic layer this lane was asked to design
(`PRINCIPLES.md` "check whether it already exists"):

| Needed | Already there |
|---|---|
| Governed catalogue of measures and dimensions | `registry.Column(tag="dimension"/"measure")` per `Dataset` (`registry.py:79-124`) |
| Joins owned by the catalogue, not the caller | `Dataset.base(ctx)` (`registry.py:127-136`), e.g. `delivery_order_lines._base` (`:169-182`) |
| Common filters as predicates | `SelectParam.condition` "the param IS the predicate" (`registry.py:194-209`) |
| Period with Malaysia time | `PeriodParam` + `resolve_period`, `to_malaysia` (`registry.py:43-66`, `engine.py:161`) |
| Company scope, fail-closed | `Dataset.scope`, `engine._predicates` (`engine.py:365-397`) |
| One executor, parameterised SQL | `engine.run_summary` -> `_pivot` (`engine.py:633-733, 926-940`) |
| Output caps | `PIVOT_CELL_CAP = 5000` (`engine.py:53`) |
| The DO-basis sales measure | `delivery_order_lines` amount subquery (`delivery_order_lines.py:100-162`) |

What is missing is thin: a chatbot-facing catalogue entry (synonyms, resolver, audience), a
query spec the parser fills, one route that validates the spec and applies audience rules
server-side, and renderers by result shape.

## 3. Design

```
WhatsApp text
  -> parser (LLM)            emits entities + report slots (no SQL, no column names)
  -> lane: spec_from_parse   builds a ReportSpec from parser keys + resolved entity ids
  -> MCP crm_report_ask      GET /api/v1/reports/ask?spec=...&contact_id=...   (API key only)
  -> route:  audience  = resolve from contact_id (server-side, never from the caller)
             validate  spec against CATALOGUE[dataset] for that audience   (422 / 403 before SQL)
             bind      filters -> SelectParam values, period -> PeriodParam, company grant
             run       engine.run_summary(definition, params, view)        (one grouped statement)
             shape     ranking | table | number, rank + top_n in Python over the whole grouping
  -> MCP presenter _report_ask   one renderer per shape, header echoes the interpreted spec
```

### 3.1 The catalogue (`app/services/reports/ask_catalogue.py`, new, data only)

One entry per chatbot-askable dataset. It REFERENCES the dataset's own column keys and param
keys, so the SQL stays in the dataset and cannot drift:

```python
AskDataset(
    key="sales",                         # what the spec names
    definition=delivery_order_lines.DEFINITION,
    grant="sales_orders.sales_report",   # reveal key the contact must hold (existing tuple)
    measures={"amount": "RM", "qty": "Qty"},
    dimensions={                         # spec word -> (column key, min audience)
        "customer": ("customer", CUSTOMER), "product": ("product", CUSTOMER),
        "brand": ("brand", CUSTOMER),       "category": ("category", CUSTOMER),
        "sales_agent": ("sales_agent", STAFF), "location": ("location", STAFF),
        "channel": ("channel", STAFF),      "month": ("month", CUSTOMER),
        "week": ("week", CUSTOMER),         "day": ("day", CUSTOMER),
    },
    filters={                            # spec word -> (param key, min audience)
        "customer": ("customer", CUSTOMER), "product": ("product", CUSTOMER),
        "brand": ("brand", CUSTOMER),       "category": ("category", CUSTOMER),
        "sales_agent": ("sales_agent", STAFF), "location": ("location", CUSTOMER),
        "channel": ("channel", CUSTOMER),
    },
    default_period="this_year",
)
```

Audience is a two-value ladder, `CUSTOMER < STAFF`, computed server-side (3.4). The audience
values per row are the recommendation in card Q1 / Q2, not decided.

Dataset additions for slice 1 (in `delivery_order_lines.py`, the one place the SQL lives):
`brand` and `category` dimensions (outer join `brands`, `product_categories` on the product,
`models/product.py:214-215`) and `brand`, `category`, `sales_agent` `SelectParam`s. No new
dataset, no new measure.

### 3.2 The query spec (`app/schemas/report_ask.py`, new)

```python
class ReportSpec(BaseModel):          # extra="forbid"
    dataset: Literal["sales"]         # grows by catalogue entry, never by free text
    measure: Literal["amount", "qty"] = "amount"
    group_by: list[str] = []          # 0 = number, 1 = ranking, 2 = table (rows x cols)
    filters: dict[str, list[str]] = {}  # spec word -> RESOLVED ids (never names)
    period: PeriodSpec | None = None  # {from, to} dates; None = catalogue default
    sort: Literal["desc", "asc"] = "desc"
    top_n: int | None = None          # 1..TOP_SELLING_N_CEILING (1000, #1407); required with group_by (card Q5)
```

Validation (all before SQL; each failure a 422 with a code the presenter words):
unknown dataset / measure / dimension / filter word; `group_by` longer than 2 or repeated; a
dimension or filter above the contact's audience (403 `report_dimension_not_allowed`); an
empty filter list (the engine reads `[]` as "no filter", `engine.py:375`, so an empty
resolution must be refused here, the lesson `sales_report_delivered.py:12-15` already records).

### 3.3 The parser: fills slots, never writes queries

Reuse the keys the strict parser schema already has (`head/parser.py:214-287`); add values, not
a new grammar:

- `order_status`: `sales_report` (existing). An ask with a parser-emitted `group_by` or `top_n`
  goes to `crm_report_ask`; the drill-down path stays on `crm_sales_report` until slice 3.
- `group_by`: enum gains `sales_agent`, `brand`, `category` (has `customer`, `product`,
  `warehouse`, `month`, `date` already, `head/parser.py:225-239`; `warehouse` maps to
  `location`, `date` to `day`).
- `top_n`, `rank_direction` (top / bottom), `rank_by` (quantity / amount): existing.
- Filters come from ENTITIES the parser already extracts (product, brand, customer, sales agent,
  warehouse, category) and the existing resolvers turn into ids (product prefix
  `sales_report_service.py:127`; brand and agent words `engine.py:1633-1700`, `services.py:683`;
  category `__init__.py:516`; warehouse `resolve_warehouse_token`; customer resolver + scope).
- Period: existing `date_filter_start` / `date_filter_end` (Malaysia today, `engine.py:7362-7370`).

`spec_from_parse` in `lanes/business/fetch.py` is the ONE function that maps parser output to a
`ReportSpec`. A word with no catalogue entry is never guessed: the route answers 422 and the
presenter lists what can be sliced ("I can rank sales by customer, product, brand, category,
sales agent, location or month").

### 3.4 Audience and permissions: enforced in the route, from `contact_id`

The route takes `contact_id` + `space_id` (required) and API key only, the shape
`api/v1/sales/analysis.py:27-43` already justifies. In order, all server-side:

1. RBAC on the act-as principal: `sales.reports.view` (existing permission).
2. Grant: the contact holds the catalogue entry's `grant` (`sales_orders.sales_report`), else 403
   (the same key the three sales asks share, `contracts.py:393`).
3. Audience: `STAFF` iff `contact_customer_scope.is_office_staff(db, contact)`
   (`contact_customer_scope.py:21-41`), else `CUSTOMER`. **Fail closed:** an unknown contact, or
   the G1 contact with no links and no office type, is `CUSTOMER`, so it never sees a
   `STAFF` dimension (closes G1 for this route; inventory section 4).
4. Row scope: a `CUSTOMER` audience is forced to its linked customers via the existing
   `enforce_customer_scope` (`_contact_scope.py:32-78`); a contact with no links gets no rows.
   A named customer outside the links is refused with the existing wording.
5. Company: the contact's companies as the engine's `company_grants` (fail-closed in
   `engine._predicates`).
6. Location policy: the contact's stock visibility policy caps `location`, as the sales report
   does today (`sales_report_delivered.py:150-172`).

The prompt trim of #1429 stays a size change; nothing here depends on it.

### 3.5 Renderers by shape (`sorento_crm_mcp/.../presenters.py::_report_ask`)

| Shape | When | Reply |
|---|---|---|
| number | `group_by = []` | header + one line: `RM 1,234,567.89 (4,321 pcs)` |
| ranking | one `group_by` | header + numbered lines `1. ALI (SA01) RM 120,000.00, 340 pcs`, "and N more", the whole set's total |
| table | two `group_by` | header + one line per row value with its column cells; over 10 x 6 cells -> Excel via the existing `report_xlsx` My Downloads path (`analysis.py:1-15`) |

Every header echoes the interpreted spec: measure, basis ("Delivered, by DO date"), period,
filters as names. A misread ask is then visible in the reply, which is the first defence
against a wrong number.

## 4. "Top sales agent for product X / brand Y in period Z", end to end

Ask (office staff, holds `sales_orders.sales_report`): **"top 3 salesman for Sorento brand last month"**

1. Parser: `order_status=sales_report`, `group_by=sales_agent`, `top_n=3`,
   `rank_direction=top`, entities `[{type: brand, raw: "Sorento"}]`, dates 2026-09-01..2026-09-30.
2. Lane: brand resolver -> brand id; `spec_from_parse` ->
   `{dataset: sales, measure: amount, group_by: [sales_agent], filters: {brand: [<id>]}, period: {from: 2026-09-01, to: 2026-09-30}, top_n: 3}`.
3. Route: grant ok; audience STAFF (office access type); `sales_agent` dimension needs STAFF, ok;
   company grant = contact's companies.
4. Engine: `run_summary(delivery_order_lines, {period, brand}, view rows=sales_agent cols=all
   measures=[amount, qty])` = one statement: DO lines in Sept, not cancelled / REP, product's
   brand = Sorento, company in grant, `GROUP BY coalesce(agent, '(no agent)')`.
5. Shape: ranking, sorted amount desc, qty desc, name; top 3 + "and N more"; total of all agents.
6. Reply: "Top 3 sales agents, Sorento brand, 1 to 30 Sep 2026 (delivered, by DO date): 1. ...".

Product X instead of brand Y: `filters: {product: [ids of the prefix]}`; nothing else changes.
A dealer asking the same: step 3 refuses (`sales_agent` is STAFF), reply "That breakdown is not
available for your account." (the wording `orders.py:1970-1973` already uses).

## 5. Three (four) future asks, zero new tools

| Ask | Spec | New code |
|---|---|---|
| "Top 5 customers for Mocha brand last quarter" | `group_by [customer], filters {brand}, top_n 5` | none |
| "Which location sold most SR-1234 in September" | `group_by [location], filters {product}, top_n 1` | none |
| "Sales by month for agent JOHN this year" | `group_by [month], filters {sales_agent}` | none |
| "Bottom 10 products by qty for my account this year" (dealer) | `group_by [product], measure qty, sort asc, top_n 10`; customer filter forced to the links | none |
| "Agent by month for brand Y" | `group_by [sales_agent, month], filters {brand}` (table) | none |

"How many customers bought X" is the first ask that is NOT free: the engine only sums measures
(`engine.py:650`), a distinct count needs an aggregate kind on `Column`. Named trigger: that ask.

## 6. Alternatives, honestly

| | Flexibility | Correctness | Permissions | Cost | Verdict |
|---|---|---|---|---|---|
| **A. Catalogue + spec + existing engine (this plan)** | Any measure x up to 2 dims x any catalogued filter; new angle = one catalogue line | Measure SQL written once per dataset; golden tests | Validated per audience BEFORE SQL; company arm fail-closed | Small: engine exists | **Recommended** |
| B. LLM text-to-SQL | Highest: any question, any table | Poor here: the DO amount alone is 70 lines of split / rounding rules (`delivery_order_lines.py:100-162`), cancelled / REP / required-date rules differ per table; an LLM re-derives them per ask, differently each time; not testable as code | Raw SQL bypasses the ORM company-scope listener (`sales_report_service.py` docstring: "never raw text SQL, which that listener cannot see"); any table readable unless a separate read-only role + RLS is built; prompt injection from a WhatsApp user reaches SQL | Low to start, high to make safe | Rejected |
| C. More bespoke tools (today) | One angle per lane | Each tool its own SQL; three "delivered" definitions already | Each tool re-implements gates; two are lane-only today | One lane per ask | Rejected as the default; kept for non-aggregations (section 7) |
| D. External semantic layer (Cube, dbt MetricFlow) | Like A | Like A | Contact-level audience would need re-plumbing into a second service | New service, second catalogue | Rejected now. Trigger: a BI tool or a second consumer outside the CRM needs the same metrics |

A is B's flexibility for the asks users actually make (rank / total / trend over a governed set
of words) without handing the LLM the database.

## 7. Migration path

| Slice | What | Retires |
|---|---|---|
| **1 (first build)** | Catalogue `sales` entry; `brand`, `category` dims + `brand`, `category`, `sales_agent` params on `delivery_order_lines`; `ReportSpec`; `GET /api/v1/reports/ask` with 3.4 gates; MCP `crm_report_ask` + `_report_ask` (ranking + number shapes); parser `group_by` enum + prompt lines; `spec_from_parse`; routing of grouped / top-n `sales_report` asks | nothing yet |
| 2 | Fold top selling: `rank_group=item/category` -> `group_by [product/category]` | `crm_top_selling_report`, `top_selling()` SQL. **Numbers move** from SO-line basis to DO lines (card Q4) |
| 3 | Fold sales report drill + period lines (table shape day/week/month x all) | `delivered_sales_report` shaping, drill offer moves onto spec |
| 4 | Catalogue entries `sales_ordered` / `sales_invoiced` over `sales_order_lines` / `billing_documents` (invoiced: no product, brand, category axis; the catalogue simply omits them) | `crm_sales_analysis` routing (the Excel stays) |
| 5 | Outstanding dataset (open SO lines, open DOs) + count-distinct aggregate | `outstanding_report` own SQL |

Not folded, each with its trigger: stock balance / availability (dealer availability semantics,
not a sum; trigger: a stock-by-X aggregate ask), low stock workbook (a reorder run, not a read),
incoming, SPO last receipt, last cost, PO placed (row lookups; trigger: an aggregate ask such as
"PO value by supplier this year", which becomes a catalogue entry over PO lines).

## 8. Risks and how answers stay correct

**Wrong numbers.**
- One SQL definition per measure, owned by the dataset. Folding slices 2-5 retires the second
  copies; until then the inventory's section 2 table is the known-divergence list.
- The reply header echoes measure, basis, period and filters (3.5), so a misread is visible.
- `(no agent)` is a real row (DO without SO or SO without agent, `delivery_order_lines.py:46`):
  printed, never hidden, so agent totals reconcile to the grand total.
- Golden-query tests, two layers:
  1. **pytest, seeded chain** (CI's DB has no data, `LESSONS-LEARNT`): 2 companies, 3 agents, 2
     brands, a legacy DO (repeated DOC total), an AutoCount DO, a cancelled DO, a `REP` DO, a DO
     with no SO. Hand-computed expected rankings per ask. Plus parity: `report_ask` vs
     `GET /sales-report?group_by=customer|product|sales_agent` on the same seed, equal to the sen.
  2. **Dev-data golden set** `report-engine-golden-asks.md`: ~15 asks with expected figures the
     owner signs off from the Reports screen / AutoCount, run read-only on the dev copy by a
     script before each fold slice merges. Not run from this sandbox (no dev data here).
  3. **Parser fixtures**: utterance -> expected `ReportSpec` (no DB), including asks that must
     be refused or clarified.

**Permissions leak.**
- Every gate in 3.4 is in the route, from `contact_id`; nothing depends on the lane or the prompt
  (the lane-only gates in inventory section 4 are not copied).
- Audience fails closed (G1). Tests per audience: office staff, linked dealer, unlinked plain
  contact, unknown contact, contact without the grant, contact in two companies.
- security-reviewer runs on slice 1 (new external route, per-contact RBAC).

**Performance.**
- One grouped statement per reply; the DO amount subquery is pushed down by period, customer
  and company (`delivery_order_lines.py:72-88`). Default period (card Q3) bounds the scan.
- Slice 1 DoD: `EXPLAIN ANALYZE` of the worst ask (all products, this year, by agent) on the dev
  copy, and a `statement_timeout` on the route's session. Rate limit as the analysis route
  (10 per contact per 10 min, `analysis.py:77-78`).

## 9. Not built (triggers)

- Free 3+ dimension pivots, nested drills: trigger = an owner ask for one.
- Ratios / growth % / margin measures: no margin data reaches the chatbot today; trigger = an ask.
- A "salesman sees own ranking" audience (agent <-> contact via `sales_agents.contact_id`):
  trigger = card Q1 answer (b).
- Reports screen exposure of the delivered dataset: trigger = owner wants it there.

## 10. Slice 1a contract (binding for tester and coder)

**Route** `GET /api/v1/order-management/report-ask`, file
`app/api/v1/order_management/report_ask.py`, router mounted with no prefix in
`app/api/v1/order_management/__init__.py` (the sales report's mounting, same module guard).
RBAC on the act-as principal: `require_permission_with_api_key("order_management.orders.view")`
(the delivered definition's own permission, `delivery_order_lines.py` `DEFINITION`). A request
without `X-API-Key` -> 403 `api_key_required` (`api/v1/sales/analysis.py:450-453` shape).

Query params (all optional unless marked):

| Param | Values | Notes |
|---|---|---|
| `contact_id`, `space_id` | **required** | 422 `contact_identity_required` when either is missing |
| `date_from`, `date_to` | **required**, ISO dates | either missing -> 422 `period_required`; from > to -> 422 `date_range_inverted` |
| `basis` | `delivered` (default) / `ordered` | other -> 422 `unknown_basis` |
| `measure` | `amount` (default) / `qty` | other -> 422 `unknown_measure` |
| `group_by` | one of `customer, product, brand, category, sales_agent, location, channel, month` | absent = number shape; other -> 422 `unknown_group_by` |
| `top_n` | 1..`TOP_SELLING_N_CEILING` (1000, owner 3 Oct: one ceiling, #1407) | required with `group_by` -> 422 `top_n_required`; out of range -> 422 `top_n_out_of_range` |
| `sort` | `desc` (default) / `asc` | other -> 422 `unknown_sort` |
| `product_code` | prefix, >= 3 chars | resolved like the sales report (`_resolve_products`); no match -> 404 |
| `brand_ids`, `category_ids`, `sales_agent_ids`, `customer_ids` | uuid lists (repeated param) | parsed by `parse_uuid_list` |
| `warehouse_codes` | codes (repeated) | none of them a warehouse -> zero rows |
| `channel` | `dealer` / `project` | other -> 422 `invalid_channel` |

Order of checks: identity pair, API key, period, enums / ranges, then the grant
(`sales_orders.sales_report` via `granted_keys` on the resolved contact; missing or unknown
contact -> 403 `sales_report_not_enabled`), then audience: `enforce_customer_scope` returns a list
-> DEALER. A DEALER whose `group_by` or any filter is outside {product, brand, category, month}
(customer filter included; its own customers are forced) -> 403 `report_dimension_not_allowed`,
message "That breakdown is not available for your account.". Location policy: the contact's
stock visibility policy caps locations exactly as `delivered_sales_report` does (named location
outside it -> body `status: "refused"`, `message` = `refusal_message(code)`).

**Response 200** (`ReportAskResponse`, `app/schemas/report_ask.py`):

```json
{
  "status": "ok",                      // "ok" | "refused"
  "message": null,
  "basis": "delivered",                // or "ordered"
  "basis_label": "delivered sales",    // or "ordered sales"
  "measure": "amount",
  "group_by": "sales_agent",           // null for the number shape
  "group_label": "Sales agent",
  "date_from": "2026-09-01", "date_to": "2026-09-30",
  "filters": [{"key": "brand", "label": "Brand", "values": ["SORENTO"]}],
  "rows": [{"rank": 1, "name": "SA01", "qty": 30, "amount": 1200.0}],
  "more": 2,                           // ranked rows not printed
  "total_count": 3,                    // every ranked row
  "total": {"qty": 55, "amount": 2100.0}
}
```

Rows rank by the measure then the other measure then name (`asc` flips both measures; name
stays ascending). A group whose qty and amount are both 0 is not ranked. `total` is the whole
set's. Money as numbers with 2 dp (`_money_edge`), qty as integers (`_qty`).

**Catalogue / datasets.**
- `app/services/reports/ask.py`: `CATALOGUE = {"delivered": ..., "ordered": ...}` mapping spec
  words to each definition's column and param keys, `DEALER_KEYS = {product, brand, category,
  month}`, `run_ask(db, spec, ...) -> dict` using `engine.run_summary` (rows = group column or
  `all`, cols = `all`, measures = [amount, qty]).
- `delivery_order_lines.py`: new dimensions `brand` (`Brand.brand_name`), `category`
  (`ProductCategory.category_name`); new params `brand` (`Product.brand_id`), `category`
  (`Product.category_id`), `sales_agent` (`SalesOrder.sales_agent_id`); outer joins in `_base`.
- `app/services/reports/datasets/sales_order_lines_ask.py` (ordered basis, registered nowhere):
  base = `sales_order_lines._base` + outer joins product brand / category / warehouse; columns
  customer, product, brand, category, sales_agent (`coalesce(code, '(no agent)')`), location
  (`Warehouse.warehouse_code` via `SalesOrderLine.warehouse_id`), channel
  (`channel_label(SalesOrder.demand_class)`), month (`to_char(order_date,'YYYY-MM')`), all;
  measures amount = `_per_line_exprs()["ordered_value"]`, qty = `["ordered_qty"]`; date basis
  `SalesOrder.order_date`; the same seven params.

**MCP.** Tool `crm_report_ask` in `sorento_crm_mcp/sorento_crm_mcp/catalog.py` wrapping the route
(query-param tool, `view=render`), presenter `_report_ask` in `presenters.py`:
header `Top {n} {group label plural} by {basis_label}, {filters}, {from} to {to}` (sort asc:
`Bottom {n}`), numbered rows `1. NAME RM 1,200.00, 30 pcs`, `and N more`, `Total RM ..., N pcs`;
number shape: `Sales by {basis_label}, {filters}, {from} to {to}: RM ..., N pcs`; `refused`
prints `message`.

Captain rulings on the tester's open points (2 Oct 2026):

- A dealer naming another customer in `customer_ids` gets `enforce_customer_scope`'s own 403
  `customer_not_permitted` (that check runs before the dimension check); naming its own
  customers is allowed and changes nothing. The `report_dimension_not_allowed` filter list
  for a dealer is therefore sales agent, location (`warehouse_codes`) and channel.
- Channel rows read "Dealer" / "Project team" on both bases (the delivered dataset's raw
  `retail` / `project` are mapped in `ask.py`; the ordered dataset already prints them).
- Month rows rank by the measure like every other dimension (not chronological).
- The header's N is the number of rows printed.

### Fix round 1 rulings (reviewer + security-reviewer, 2 Oct 2026)

- **Pool pins (B1).** `crm_report_ask` joins `fetch.CUSTOMER_SCOPED_TOOLS`; it is an
  `UNCALLABLE_READS` row in `tests/chatbot/test_tool_pool_is_read_only.py` ("needs period and
  top_n from the lane; wired in 1b"). MCP `TOOL_REQUIRED_QUERY_HINTS["crm_report_ask"] =
  ("contact_id", "space_id", "date_from", "date_to")`.
- **A named filter never widens (B2).** A filter param PRESENT in the query but resolving to
  nothing (blank, whitespace, `customer_ids=` etc., `product_code=" "`, blank
  `warehouse_codes`) -> 422 `empty_filter`, `detail` = the param name. An id that names no row
  (brand / category / sales agent / customer, inside the contact's companies) -> 404 `NOT_FOUND`
  (`handle_not_found("<Thing>", ...)`), like `product_code`.
- **Unknown query keys** -> 422 `unknown_param`, `detail` = the key. Every enum 422
  (`unknown_basis`, `unknown_measure`, `unknown_sort`, `unknown_group_by`, `invalid_channel`)
  carries `detail = "allowed: a, b, ..."`.
- **List caps.** More than 50 values in any list param -> 422 `too_many_values`, detail = the
  param (the sales report's cap).
- **Dates.** A year outside 1900..2200 -> 422 `date_out_of_range`.
- **Company grant from the contact.** The run's company grant is the resolved contact's own
  `respond_contact_companies` rows (the `analysis.py:260-268` read), passed to
  `engine.run_summary(company_grants=...)`, never the session scope. No company -> zero rows.
  `contact_id` / `space_id` are stripped before the pair check.
- **Busy.** Rate limit answers through `ReportAskResponse` with `status: "busy"`; tested.
- **One location helper.** The location-policy block of `delivered_sales_report` and `ask.py`
  becomes one shared function in `sales_report_delivered.py`, called by both.
- **Echo.** A dealer naming its own customers sees them echoed. Filters in the header are
  joined by "; ", values inside one filter by ", " (the presenter test is the pin).
- **One word set.** `ask.py` keeps one dimension list and one filter list, shared by both
  bases, until a word differs from its key.
- **Known semantics, documented not changed.** Channel: delivered reads the account's segment
  (no segment = in every channel); ordered reads the SO's `demand_class` (NULL = in none).
  Ordered basis under a location policy: an SO line with no warehouse is outside every capped
  location (fail closed); the share is unmeasured (no dev data in the sandbox), golden-asks
  item.

## 11. Slice 1b contract: parser vocabulary + lane wiring (built on #1445 `required_fields`)

Base: #1445 head `57af6b3e` merged into this branch (`required_fields.py` API as posted on #1445;
crew relays when it lands on main, then main is merged here). FULL track: adds a data-only
migration (a parser prompt version, label unmoved).

**Parser** (`app/services/chatbot/head/parser.py` strict schema + `chatbot_parser_prompt.py`
addendum `REPORT_ASK_ADDENDUM`, inserted before `MEMORY_ADDENDUM`, which existing tests pin as the tail):
- `order_status` gains `"sales_ranking"`: sales ranked or totalled BY one dimension, for a
  sales agent / salesman, customer, brand, category, location, channel or month: "top 3
  salesman for Sorento brand last month", "which location sold most SR1234 in September",
  "top 5 customers for Mocha this year", "sales by month for agent Agent A 2026", "bottom 5
  sales agents", "how much did we sell of brand X in August" (no `group_by`: a total).
  Ranking PRODUCTS or CATEGORIES ("top 10 products", "hot selling") stays `top_selling`
  (slice 2 folds it). `domain_hint` = `order`.
- `group_by` enum gains `sales_agent`, `brand`, `category`, `channel`.
- Reused keys: `top_n`; `rank_direction` (top / bottom -> `sort` desc / asc); `rank_by`
  (quantity -> `measure=qty`, amount or null -> `amount`); `basis` (delivered / ordered;
  null or unclear -> delivered, owner Q4); `date_filter_start` / `date_filter_end`;
  `sales_channel`. Entities: `brand`, `sales_agent`, `category`, `product`, `customer`,
  `warehouse` hints as today.
- Migration `report_engine_0001_prompt` publishes the full `SEMANTIC_PARSER_PROMPT` as a new
  `chatbot_semantic_parser` version, label unmoved (`acct_ledger_0002_vocab` pattern). The owner
  moves `production` onto it after deploy. `test_parser_prompt_budget.py` ceiling raised by the
  addendum's size.

**Contracts.** `contracts.SALES_FIGURE_STATUSES` gains `sales_ranking` (the grant check before
anything is fetched covers it with no new key).

**Engine seam** (`engine.py`, beside `low_stock_ask.take_words`): `report_ask.take_words(verdict,
text)` on a FRESH `sales_ranking` verdict moves this message's `brand`, `sales_agent` and
`category` entities off the entity list onto `report_ask_words` (`{"brand": [...],
"sales_agent": [...], "category": [...]}`), so the generic resolver never reads them as
customers (the top selling lesson, `engine._top_selling_narrowing`). `report_ask_words` joins
`required_fields.ENGINE_KEYS` (the parser can never forge it).

**Lane** (new `app/services/chatbot/lanes/business/report_ask.py`, branch in `run_fetch` for
`domain == "order" and order_status == "sales_ranking"`):
1. Grant: no `sales_orders.sales_report` -> `_sales_report_not_enabled()` (existing line).
2. Words -> ids: brand `business_services.resolve_brand_token`, sales agent
   `resolve_sales_agent_token`, category `resolve_category_token`. A word naming nothing ->
   one line `I don't know '<word>' as a <brand|sales agent|category>.` and no run.
   Resolved `product` entities -> `product_ids`; `customer` -> `customer_ids`; `warehouse` ->
   `warehouse_codes` (their codes).
3. `group_by` mapped (`warehouse` -> `location`; `date` and anything outside the catalogue ->
   the line `I can rank sales by customer, product, brand, category, sales agent, location,
   channel or month.` and no run).
4. Required fields through `required_fields` (`AskType("sales_ranking")`, registered once):
   - `period` (required, `allow_all=False`), question `Which period? For example this month,
     September, 2026, or 1 to 15 Sep.`. A fresh ask gives it from the parser's
     `date_filter_start` / `date_filter_end` (both, or one widened to that day) as a settled
     `Resolved("ok", {"from", "to"}, "<d Mon yyyy> to <d Mon yyyy>")`. An ANSWERING turn takes
     it the same way from the reply's own parsed dates; a reply with no date is a miss
     (the helper re-asks, gives up after two).
   - `top_n` (required only with a `group_by`; without one it is given as settled `None`),
     question `How many? For example top 5.`; a reply word with an integer 1..100 settles it,
     anything else is a miss.
   - `reroute = {"message_type": "business_query", "domain_hint": "order", "intent_hint":
     "check_order", "order_status": "sales_ranking"}`, `cancelled = "Sales ranking
     cancelled."`, `give_up = "I still can't read '{word}'. Ask again with the period and how
     many, e.g. top 5 sales agents for Sorento this month."`.
   - Extras carry every other settled arg (ids, group_by, sort, measure, basis, channel) so the
     answering turn runs the first message's ask.
   - Not done -> `_fixed_reply(outcome.reply, required_ask=outcome.slot)`.
5. Done -> `semantic_input["report_ask_args"]` = the route params; tool `crm_report_ask`
   (`fetch.entity_ids_transformer` sends them plus `contact_id` / `space_id`). The reply is
   the presenter's text. Route refusals are said as their `message` (403
   `report_dimension_not_allowed`, `customer_not_permitted`, `sales_report_not_enabled`,
   `busy`, `refused`); a 404 says `I couldn't find that <thing>.`.
6. Tool pool: `crm_report_ask` leaves `UNCALLABLE_READS`, joins `CHATBOT_READ_ONLY_TOOLS` and
   the order domain's tools wherever the pin tests require.

**Route addition:** `product_ids` (uuid list, same rules as the other id lists: `empty_filter`,
404, 50 cap), ANDed with `product_code` when both are given.

**UAC for 1b** (AC-RE-18, AC-RE-19 refined): AC-RE-18a ranking ask with no period -> the
period question; the reply "last month" runs the ask with the first message's filters.
AC-RE-18b ranking ask with no number -> "How many? For example top 5."; reply "5" runs it.
AC-RE-18c both missing -> period asked first, then how many. AC-RE-18d "cancel" -> "Sales
ranking cancelled."; two misses -> the give-up line. AC-RE-19a parser maps "salesman / sales
agent / SA", "brand", "category", "by location", "by month" onto `group_by` with
`order_status=sales_ranking`; AC-RE-19b "by colour" -> the catalogue line; AC-RE-19c a brand
word naming no brand -> "I don't know 'X' as a brand."; AC-RE-19d the header names the basis.

Captain rulings on the 1b tester's points: `order_status` stays a free string in the strict
schema (no enum added); the helper's miss line uses the field nouns `period` and `number`
(`FieldSpec.noun`); `report_ask_words` holds the raw words per hint (low stock's shape); a
forged `report_ask_words` from the parser is stripped and the ask runs without that filter.

### 1b fix round rulings (security review, 2 Oct 2026)

- **F1.** A dealer (the turn's contact is customer-scoped) whose ask names a sales agent or a
  location word, or groups by a staff dimension, gets "That breakdown is not available for your
  account." straight away: the lane never resolves agent or location words for a dealer (so the
  unknown-word line cannot be used to probe agent names). Brand and category words resolve as
  before.
- **F2.** Tests pin: a dealer naming another customer is refused; an answering turn runs only the
  carried args (a customer or sales agent entity on the reply turn never reaches the request);
  `contact_id` / `space_id` always come from the turn even if the carried args hold one; a route
  `product_ids` of another company's real product is 404.

### 1b fix round rulings (code review, 2 Oct 2026)

- **S1 parser overlap.** The addendum gains: a company's own totals by month, year or channel
  with no brand, sales agent, category, location or product named and no ranking word stay
  `sales_analysis`; "sales report of X" stays `sales_report`; ranking products / categories stays
  `top_selling`. The examples use a brand that is not a company name. Console cases (live model)
  go in `tests/chatbot/console_cases/2026-10-02-report-ask.yaml`.
- **S2 month breakdown.** `group_by=month` never asks "How many?": `top_n` is settled as 100 (every
  month of the period). Owner Q5's "how many?" is about rankings; a month breakdown is a trend.
- **S3 several matches.** A brand or category word matching more than one row runs nothing and
  says `'<word>' matches several <brands|categories>: A, B. Ask again naming one.` (no silent
  widening, the PR #1273 rule). An exact name / code match wins alone. Several sales agent rows
  for one word (AGENT A I, AGENT A II) stay a union: one person.
- **S4 products.** A named product goes to the route as `product_code` = its code (the sales
  report's PREFIX rule, S19: "SRT5674" covers "SRT5674-N"), not `product_ids`.
- **S5.** Test: a customer or product carried from an earlier message never becomes a filter.
- **N2 open-ended period.** A start date alone ("since September") is start to today (Malaysia);
  an end date alone is a miss (asked).
- **N3 dealer links.** For `crm_report_ask` the customer-scope tail does not add the dealer's
  linked customers (the route forces them itself, `enforce_customer_scope`); the out-of-scope
  check stays. So a dealer with more than 50 links still gets an answer, and the header does not
  list every account.

### Owner hand-test FAIL round (3 Oct 2026)

Owner on :3104: "who's the top 3 salesman for sorento water closet this year" answered as top
selling items with agents WT I / WT III / WT IV as a filter. Dev ran the production-labelled
older parser prompt (this lane's prompt is published unlabelled by design), so the ask reached
top selling. Rulings, each with red tests first and a kill test:

- **Reroute.** A fresh top selling reading whose ranked noun is a PERSON ("top 3 salesman",
  "top 5 customers", "top 3 SA", "top 1,000 customers") becomes `sales_ranking` with that
  `group_by`, keeping brand / category / product, never the axis as a filter. Its own noun
  set: a bare "sales" is never a person ("top 10 sales items" stays top selling), and the noun
  ends at a word boundary ("top SA01 items" keeps SA01 as the agent filter).
- **Word groups.** Category, then exact brand, then sales agent, then customer: a word that is
  a category or brand never resolves to an agent alias (the WT shape).
- **Ceiling.** `top_n` runs to `TOP_SELLING_N_CEILING` (1000, #1407) on the route, the lane and
  the MCP text; past it the lane says "I can list at most the top 1,000 in one reply." and asks
  again. A month breakdown keeps its own 100 rows.
- **Cell cap.** Cannot truncate a ranking: `run_summary` runs `_pivot(cap=False)`, the grouped
  SQL has no LIMIT and the rank is cut after sorting (guard test over 5002 products).
- **Live parser findings (gpt-5.4-mini).** The model emits `rank_by` quantity for "top 3
  salesman" with no quantity word: the lane takes quantity only when the message names it
  (quantity, qty, units, pcs, pieces), else amount (AC-RE-6). It sometimes emits the ranked noun
  ("salesman") as an entity: such a noun is the axis, never a filter word.
- **Prompt.** The owner message is an addendum example; the budget ceiling rose by 2 to keep
  "SA" and "(dealer / project)". Dev's copy needs the crew SQL above, then the label move, and
  the parser on gpt-5.4-mini (gpt-4o-mini misses the addendum).
- **Open for the owner.** With `measure=qty` the header still reads "by delivered sales"; an
  agent coded literally "SA" or "REP" cannot be named as a filter on a ranking.

### Semantic-only round (owner rule, 4 Oct 2026)

Owner: the PARSER decides meaning; code never reads the message text with a regex or a word list
("hard-coded rules are a blocker"). Crew: no regex even for value extraction. Rulings:

- **Removed text rules.** The salesman reroute (`_ranked_who`, `_RANKED_NOUN_RE`,
  `_sales_ranking_verdict`), the quantity-word measure guard, the person-noun entity drop and the
  digit read of the reply to "How many?" are gone; kill tests pin their absence. The published
  prompt parses "who's the top 3 salesman ..." as `sales_ranking` itself (live 46/48 overall).
- **Parser keys.** `measure` ("qty" / "amount" / null, null = amount) and `ranking_refine`. The
  ranked noun is never an entity; a count, period, basis or measure word after a sales ranking is
  a refine; a message naming its own axis or subject is a new ask whose count and period come
  only from that message.
- **Held frame.** `Focus.sales_ranking_frame` holds the route args of the last ranking that ran.
  It is merged only on a `sales_ranking` verdict with `ranking_refine` true, is an engine key (a
  parser-emitted frame is stripped), is dealer-checked on refine, and is cleared by any other ask.
  Compatible with STUCK-QTY-LOOP's central held-state rule: a held frame applies only when the
  turn refines it; a new intent always wins.
- **"How many?"** is answered from the parser's `top_n` for that turn; one re-ask, then the
  default top 10 with "I couldn't read how many, so here is the top 10."
- **Bare number after a ranking** = top N of that ranking (crew-ask, recommendation a; the owner
  confirms in the morning).
- **Follow-up for #1470 (LOWSTOCK-SEMANTIC).** The shared `required_fields` helper still reads
  reply text itself (`collect`'s cancel words, `reply_verdict`'s "more than three words" / "?"
  checks); #1470 replaces that capture with `reply_verdict`.
- **Rate limit.** The route keeps 10 rankings per contact per 10 minutes.

### Owner decision on short follow-ups (4 Oct 2026)

Live gpt-5.4-mini reads full asks reliably (the owner's message 100% across every run) but short
follow-ups ("5", "3", "last month", a singular "top salesman", a ranking typed over an open top
selling question) vary run to run (the 10-step script as one conversation: 24/33). They degrade
safely: no widening, no wrong filter, no company total. Owner: option (a), ship and measure on
live use; a stronger parser model (b) or dropping bare refines (c) stay open if the live numbers
call for it. The hand test picks the published prompt in the console's prompt-version picker; the
shared dev `production` label is not moved.
