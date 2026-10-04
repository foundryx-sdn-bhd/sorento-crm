# PLAN - Chatbot: top X hot selling items by category / customer / sales agent / date range

Status: grilled (26 Sep 2026; owner rulings on PR #1175 folded in below, S1 in build on
`feat/chatbot-top-selling-s1`). S2 + S3 built on PR #1263 (branch
`claude/top-selling-s2-s3-j8nhan`), reviewer pass of 26 Sep 05:52Z fixed on the same PR; see
"As built (S2 + S3, PR #1263)" for where the route differs from the contract below and which
ruling decides it. S1 (PR #1258), S2 + S3 (PR #1263) and S4 are folded into ONE PR on
branch `claude/top-selling-s4-parser-wiring-69mpx3` (S4 built 26 Sep 2026, see "As built
(S4)"); the reviewer pass of 26 Sep 08:42Z (B1, B2, S1, S2, S3, N1, N2, kill R4) is fixed
on the same PR, and main (dc10a1af) is merged with the lane's migrations re-parented onto
`sales_0002_team_leader` (fix lane round 2, 26 Sep), the live-parser console run still owed
on the local stack. Fix lane round 3 (27 Sep, owner hand test, ruling assumed pending the
owner): any ACTIVE office access type makes a contact staff for top selling whatever other
types or customer links it holds (`orders._top_selling_is_staff`, shared by the lane's
`top_selling_dealer_ledgers`); no active office type keeps link-else-403. Fix lane round 4
(27 Sep, owner retest at 9ab7f89d, parser v34): F1 to F8 below ("As built (fix lane round
4)"), main (11bf373e) merged, the lane's first migration re-parented onto
`merge_27sep_three_heads`, the parser prompt republished unlabelled by
`chatbot_top_selling_vocab_r4`. Fix lane round 5 (27 Sep, owner retest of round 4 at
a7c6abc6, which ran the console on parser v34 although v38 was the newest full version):
R1 to R9 below ("As built (fix lane round 5)"), the console no longer remembers a version
pick, the parser words republished unlabelled by `chatbot_top_selling_vocab_r5`. Fix lane
round 6 (27 Sep, owner retest of round 5 at d59e1dcf, parser v39): the answer to the
ranking's own question binds in code, the "sold" words taught and republished unlabelled
by `chatbot_top_selling_vocab_r6` ("As built (fix lane round 6)"), main (52b0ac24) merged
and the lane's first migration re-parented onto `sales_s1_reports_module`. Fix lane round
7 (27 Sep, owner retest of round 5 part 2, parser v39): carried entities across report
kinds, period and basis words over a ranking, no offer inside a ranking conversation, and
sales agent "Also known as" names (`sales_agent_aliases_r7`); no parser words change ("As
built (fix lane round 7)"). Fix lane round 8 (28 Sep, owner hand test of round 7 at
b992353c): an outstanding ask after a ranking is the ordinary outstanding ask with the
ranked codes as its products, and the "Filters from the ranking" header and dropped filter
lines are gone; no parser words change ("As built (fix lane round 8)"). Fix lane
round 9 (28 Sep, owner hand test after round 8): word answers to the customer or sales
agent question ("salesman", "saleman", "client", the agent code) bind like 1 or 2; no
parser words change, no migration ("As built (fix lane round 9)"). Main merge fix round
(28 Sep, CI red after main d79b46c5): #1262's domain word rule cleared the ranking status
before `_top_selling_rules` read it, and #1262's order brand carry kept the ranking's brand
past the ranking; both fixed in `turn/apply.py` and `turn_runtime.py`, no test expectation
changed. S5 (sales
agent) is built by round 4 as a lane-side resolver; S6 (review + live console) and S7 (per-month breakdown) open. Track: full track (new route + MCP tool = a new external
ingest surface, one policy-row migration, one prompt migration, one entity-kind migration;
the diff will pass 300 lines).
Issue: #1171. UAC: `chatbot-top-x-hot-selling-24sep-acceptance-criteria.md` (AC-19xx).
Classification: CORE, `public` schema, no new table, no new column. The chatbot module is
where the wiring lands; the report itself is an order-management read.
Precedent: `PLAN-chatbot-sales-report.md` (#1034, BUILT). Every seam below copies that lane's
seam WITH the justification that earned it, or says why it does not.

## Owner rulings (26 Sep 2026)

Source: the owner's PR #1175 comment starting "@claude Default N" (verbatim, binding), plus
two follow-ups relayed by the orchestrator the same morning. Each line below is dated so a
later reader can tell a ruling from a proposal.

- Owner ruling 26 Sep: **N.** A number in the message ranks that many, 1 to 100 (the owner's
  own example is "top 100"). No number = no cut-off: every ranked row. No invented default N.
  SUPERSEDED 30 Sep 2026 (owner: "remove the cap, there is a use case of 100, 200"): N is
  1 to 1000, a technical safety ceiling, and a long ranking is split into WhatsApp-sized
  parts. See `PLAN-top-n-uncap-30sep.md`.
- Owner ruling 26 Sep 01:50Z (amends the first reading of the N ruling): **no paging.** No
  "more", no "next", no "lagi" anywhere in the reply; the owner called paging "very not
  transparent". The header states the full count.
- Owner ruling 26 Sep 01:55Z: **long lists.** When the message names no number and the ranked
  list would not fit one WhatsApp message (about 50 rows, one setting), the bot sends no
  partial list: it states how many there are and asks how many to show. A list that fits is
  sent in full. The same rule applies to PR #833's counted set answers (that lane's work, not
  this one's).
- Owner, PR #1258 26 Sep 05:32Z (amends the 01:55Z reading above): **length is not the
  bot's problem.** n8n already chunks a long WhatsApp message, so there is no "fits one
  message" threshold, no `chatbot_top_selling_one_message_rows` setting and no bot-side
  split of a long reply. With no N named, the header states the full count and the bot asks
  how many (1 to the smaller of the count and 100), whatever the count; a named N up to 100
  goes out whole and n8n chunks it. One row is sent as is (nothing to choose between).
- Owner, PR #1258 26 Sep 05:32Z: **the ranked list is a sticky pick list**, the same as the
  customer and product pickers: the printed rows arm a `top_selling_pick` roster
  (`turn/pending.py::ROSTER_KINDS`), so a later "2", a typed code or a typed category name
  resolves against the last list shown through `turn/decide.py::picked_positions`, and the
  list stays open across picks. No turn clock (the pickers have none: `pending.tick` only
  expires the escalation offers); it closes on the pickers' own two rules in
  `turn/apply.py` (every row picked, `stale_roster_closed`; a new ask about something else,
  `new_ask_closes_stale_roster`).
- Owner ruling 26 Sep: **metric is required.** `rank_by` is quantity or amount; when the
  message does not say, the bot asks "By quantity or by amount?". No default metric.
- Owner ruling 26 Sep: **basis.** Both ordered (`qty_ordered`, ordered amount) and delivered
  (transferred to DO, `LEAST(qty_delivered, qty_ordered)` and its amount) are supported.
  "Selling" normally means delivered: when the message does not say, the reply uses delivered
  and says so in the header, and "ordered" as a follow-up re-runs on the ordered basis. A
  message ambiguous between the two ("top orders") gets a clarify question instead.
- Owner ruling 26 Sep: **group.** The bot ranks items (default grain for an item / product
  ask) OR ranks categories ("which category sells most"). A category word can also be a
  filter ("top items in category A"). When the message is ambiguous between filtering by a
  category and ranking categories, the bot clarifies; it never assumes.
- Owner ruling 26 Sep: **date.** No date said = the current calendar year. Months and ranges
  are supported. "By month" is either a month filter or a per-month breakdown; when unclear,
  clarify.
- Owner ruling 26 Sep: **filters.** Customer, channel (the existing dealer / retail / project
  word), category, sales agent. Sales agent = `sales_orders.sales_agent_id` (who sold the SO).
  The S5 gate on #1168 / #1170 is lifted by this ruling; the reply carries a fill-rate note
  when the agent field is thinly filled.
- Owner ruling 26 Sep: **access.** The `sales_orders.sales_report` reveal key, no new key.
  Staff may ask across all customers. A dealer is forced to its own ledgers and can never ask
  about another dealer: a named other customer gets a polite refusal, not data.
- Owner ruling 26 Sep: **zero-value lines** (`line_total = 0`) are included.
- Owner ruling 26 Sep: **detail offer required.** After the ranking the reply offers a detail
  follow-up. Chosen smallest useful detail (captain, 26 Sep): for an item ranking, "Reply with
  a rank number to see that item's customers and months."; for a category ranking, "Reply with
  a rank number to see that category's top items." (which is the item ranking filtered to that
  category, so it needs no new answer shape).

## Journey

Actor: a management or sales contact on WhatsApp who already holds the sales report grant,
or a dealer contact holding it (forced to its own ledgers). They want to know what is
moving. The system already knows the contact, its tier, its companies, every customer ledger,
the product master with its category, the salesperson master and every SO line AutoCount has
pushed (quantity ordered, quantity transferred to DO, line total, required date, the SO's
agent code).

1. They type "top 5 selling items by quantity last month". One reply: a header naming the
   ranking, the metric, the basis (delivered), the count of items with sales and every filter
   axis, then five numbered rows, code and name, quantity and amount, then the detail offer.
2. They type "top 5 selling items last month" (no metric). The bot asks "By quantity or by
   amount?"; "amount" answers it and the ranking follows.
3. They type "top 10 for hanlim by amount this quarter". Same list, filtered to the HANLIM
   ledgers, the header says so. An ambiguous customer name goes through the existing customer
   picker; "1" or "all" continues this ask.
4. They type "top 3 kitchen sinks by quantity in 2026". Same list, filtered to the KITCHEN
   SINK category. "which category sells most by amount" ranks categories instead. "top
   selling by category" is ambiguous and gets the group clarify question.
5. They type "top 5 by quantity for sales agent SEAN I this year". Same list, filtered to the
   SOs that agent sold.
6. They type "top selling items by quantity" with no number. They get "How many items do you
   want to see? Reply with a number from 1 to 100." under a header that states the count.
   "20" or "top 20" answers it (amended by the owner, PR #1258 05:32Z).
7. They reply "2" after a ranking. That item's customers and months follow (the detail).
   The list stays open: "4" or "SRTBS1020" next answers against the same list, as a second
   pick over a customer or product picker does (owner, PR #1258 05:32Z).
8. They type "ordered" after a ranking. The same ranking re-runs on the ordered basis.
9. A dealer types "top 5 for <another dealer>". It gets "Sorry, I can only share sales
   figures for your own account." and nothing is fetched for that customer.
10. A contact without the grant gets `Sales report is not enabled for your account.` and
    nothing is fetched.

Decisions asked of the user: the customer picker (existing), the metric clarify, the group
clarify, the basis clarify (only when ambiguous), the month clarify (only when ambiguous) and
the how-many question when no N is named. Every other axis is derived.

## What exists today (measured against the code, 24 Sep 2026)

### Parser slots (`app/services/chatbot/head/parser.py::_build_json_schema`)

The strict schema has 35 top-level keys. The ones this ask needs:

| slot | exists | notes |
|---|---|---|
| `date_mode`, `date_filter_start`, `date_filter_end` | yes | carried to every tool through `DATE_PARAMS` |
| `entities[].hint = customer` | yes | resolver source `customers`, ledger-family grouping, `must_narrow_one` under `order` |
| `entities[].hint = category` | yes, but not usable under `order` | kind row exists (`policy_rows.DEFAULT_KIND_ROWS`, source `product_categories`), BUT `gate.ALLOWED["order"]` has no `category`, `gate.NO_TOOL_ID` = {brand, category} (maps to no `*_ids` param), `fetch.TYPE_TO_PARAM` has no `category`, and `entity_resolver._DOMAIN_HINT_EXPANSIONS["order"]["category"]` re-types a category token as customer / customer_order / transporter. So "top 5 kitchen sinks" under `order` today resolves KITCHEN SINK as a customer or drops it. |
| `entities[].hint = product` | yes | `list_all` under `order` |
| `demand_qty` | yes | unrelated (a quantity the customer wants to buy) |
| `top_n` | **yes** (growth r1, AC-910) | integer, current message only, "top 5" -> 5. Reaches `semantic_input` and `entity_ids_transformer`, where it aliases to `limit` for `ORDER_TOOLS` / `GROUP_BY_TOOLS` and is sent as `top_n` for `TOP_N_DIRECT_TOOLS`. Not carried into `Focus`. |
| `group_by` | yes | enum customer / transporter / date / product / warehouse / supplier. A breakdown axis, not a filter. No `category`, no `sales_agent`. |
| `order_status` | yes | the ask discriminator the two reports use: `outstanding` family and `sales_report` |
| `sales_channel` | yes | dealer / project, carried on `Focus.sales_channel` |
| ranking metric (`rank_by`) | **no** | nothing says qty vs amount anywhere in the schema, the prompt or the lane |
| sales agent slot | **no** | not in `contracts.ENTITY_HINTS`, no kind row, no resolver source in `entity_resolver.py`, not in `gate.ALLOWED`, not in `TYPE_TO_PARAM` |

Conclusion: `top_n` is already a slot and needs no new key. Two slots are new: `rank_by` and
the `sales_agent` entity kind. One existing slot needs unblocking under `order`: `category`.

### How a business_query "data page" is registered and answered

There is no registry and no page object. A report ask is one value plus five seams, all of
them already exercised by `crm_outstanding_report` and `crm_sales_report`:

1. **Parser**: an `order_status` value (`sales_report`) taught by a trailing prompt addendum
   (`chatbot_parser_prompt.SALES_REPORT_ADDENDUM`), published as a new unlabelled
   `chatbot_semantic_parser` version by one migration (`519_chatbot_sales_report_vocab`); the
   owner moves the `production` label. `turn/apply.py` projects it onto `Focus.status` so a
   refinement turn keeps the ask.
2. **Policy row**: the tool name is appended to the `order` domain's `tools` in
   `turn/policy_rows.DEFAULT_DOMAIN_ROWS` (frozen seed; a live DB is changed by a
   `chatbot_rearch_sNN` migration). That makes it a member of `fetch.CHATBOT_READ_ONLY_TOOLS`
   (the egress allow-list) and of `policy_rows.DATE_PARAM_TOOLS`. It is never `tools[0]`.
3. **Tool pick**: an override branch in `lanes/business/__init__.py::run_fetch` (line 1283 for
   the sales report): `domain == "order" and <subject rule> and order_status_raw == "<value>"`
   sets `tool_name`; the reveal-key gate runs right there, before any fetch. The engine has a
   grant-before-roster check too (`engine.py:1706`, SF-1) so an ungranted ask never renders the
   customer picker. `lanes/business/__init__.py:590` is the same check at `run_until_exit`.
4. **Arguments**: a `tool_name == "<tool>"` block in `fetch.entity_ids_transformer` builds the
   tool's own contract off `semantic_input` and the resolved entities; `fetch.DATE_PARAMS` names
   the two date params.
5. **Answer**: `sorento_crm_mcp/catalog.py` `ToolSpec` (path, params, `restricted_fields`),
   `presenters.py` renders the WhatsApp text and returns `{result_type, response, has_result}`;
   `fetch.output_structurer` dispatches on `ctx["tool"]` (line 2098) to arm an offer or take the
   miss path. A miss goes through `not_found_error_message`, so the escalate offer follows.

`documentation/plans/chatbot/PLAN-chatbot-turn-engine.md` carries no "data page" section by
that name; the policy it does carry is the one above: the domain row decides the tool, the
lane decides the arguments, the presenter decides the words, and the parser is the only
reader of the customer's text (D11).

### SQL source of truth for sales by item

`sales_orders` (header) + `sales_order_lines` (money and quantity), the source
`PLAN-chatbot-sales-report.md` measured and ruled on (S1: the DO tables `orders` /
`order_lines` are NOT usable as money; `order_analytics` and `orders_by_product` read those
and are therefore not the source here).

| need | column | table |
|---|---|---|
| item | `product_id` -> `products.product_code`, `products.product_name` | `sales_order_lines`, `products` |
| category | `products.category_id` -> `product_categories.category_code` / `category_name` | `products`, `product_categories` |
| customer | `sales_orders.customer_id` (ledger; family via the resolver) | `sales_orders` |
| sales agent | `sales_orders.sales_agent_id` -> `sales_agents.sales_agent` (code, e.g. `SEAN I`), `person_label` | `sales_orders`, `sales_agents` |
| date | `COALESCE(sales_order_lines.required_date, sales_orders.order_date)` (the sales report's bucket, S3) | both |
| channel | `sales_orders.demand_class` (`retail` = dealer, `project`) | `sales_orders` |
| quantity | `qty_ordered` (ordered) or `LEAST(qty_delivered, qty_ordered)` (confirmed, transferred to DO) | `sales_order_lines` |
| amount | `line_total` (the file's `Total (Inc)`; 22,717 lines dated 2026 carry 0, UAC sales report "Measured") | `sales_order_lines` |
| exclusions | `sales_orders.status <> 'cancelled'`, `line_status <> 'cancelled'`, bucket not null | both |

Existing ranking code: `sales_report_service._rank_breakdown` ranks a month's `by_product`
by ordered value desc, ordered qty desc, code asc, over exactly this predicate. That is a
per-month ranking with a mandatory subject; this feature is a whole-window ranking with no
mandatory subject. `_per_line_exprs`, `_common_filters` and `_bucket_expr` in that module are
the reusable parts; nothing else in the codebase ranks items on the SO source.

Not measurable from this checkout (no database here); measure before the grill and paste
into the UAC "Measured": fill rate of `sales_orders.sales_agent_id` on SOs dated 2026, count
of distinct `product_categories` reached by 2026 lines, `products.category_id` null rate on
sold products, and the row count of a whole-book `GROUP BY product_id` over 2026 (to size the
date default).

## Design: the smallest thing that works

**One new report, one new `order_status` value, three new nullable parser keys (`rank_by`,
`basis`, `rank_group`), one unblocked entity kind (`category` under `order`), one new entity
kind (`sales_agent`), one setting. No registry, no new engine, no new reveal key.** The
detail offer reuses the sales report's offer seam (S4 measures whether it carries a rank
number; if it cannot, ONE new pending kind `top_selling_detail` is the smallest shape).

Why a second route rather than a mode on `crm_sales_report`: the sales report requires a
subject (422 `subject_required`, a scan guard), prints month blocks and defaults product-only
asks to the current year (S18). A ranking has no subject and prints one list. Folding it in
means a mode switch in a shipped, 60-AC report; a sibling route that reuses the same service
helpers is fewer moving parts.

### The reply (contract for Phase 1, rewritten for the 26 Sep rulings)

Every reply is one of six shapes. The first three are rendered from the route's body by
`_top_selling_envelope`; the last three are fixed lines the lane sends before any fetch,
declared once in `presenters.py` so the goldens and the lane read the same literal.

1. **Ranking (hit).**

```
*Top 5 selling items*
Ranked by: Quantity
Basis: Delivered (transferred to DO)
Items with sales: 1,284
Customer: all
Category: all
Sales agent: all
Channel: all
Delivery date: 01/01/2026 to 31/12/2026

1. SRTWT7445 Kitchen Sink 2 Bowl: Qty 1,240, RM 86,400.00
2. SRTKT39SS Kitchen Tap: Qty 980, RM 31,200.00
3. ...

Reply with a rank number to see that item's customers and months.
```

   - Title: `*Top N selling items*` when the message named N, `*Top selling items*` when it
     did not (every row follows). Category grain: `*Top N selling categories*`, count line
     `Categories with sales: n`, offer `Reply with a rank number to see that category's top
     items.`
   - `Ranked by:` prints `Quantity` or `Amount`. `Basis:` prints `Delivered (transferred to
     DO)` or `Ordered`. The count line is the FULL count of ranked rows in the window (owner:
     the header states the full count), whatever N is.
   - Every absent filter axis prints `all`, never an omitted line. `Channel:` prints
     `Dealer`, `Project` or `all` (`_sales_channel_header`).
   - Under a sales agent filter, when the route sends `agent_fill_pct`, one extra line follows
     `Sales agent:`: `Note: only 87% of sales orders in this period carry a sales agent.`
     The route decides when to send it (S2: below 95%).
   - Rows: `n. CODE Name: Qty q, RM v` for items (code alone when the name is null);
     `n. NAME: Qty q, RM v` for categories (the category code when the name is null,
     `Unassigned` for products with no category). `n` is the body's own `rank`, rows print in
     the order given (the route sorts), at most 100 rows (the N ceiling).
   - No "more", "next" or "lagi" anywhere (owner ruling 01:50Z).
2. **How many (no N named).** The same header, a blank line, then `How many items do you want
   to see? Reply with a number from 1 to 100.` (`categories` at category grain; the upper
   bound is the smaller of the count and 100). No rows, no offer, no pick list.
   `has_result: true` (it is not a miss; nothing escalates).
3. **Miss.** The same header, a blank line, `No sales found.`, nothing after it.
   `has_result: false`, so the lane's not-found path offers the escalation.
4. **Metric clarify.** `By quantity or by amount?`
5. **Group clarify.** `Do you want the top items inside one category, or the categories
   ranked against each other?`
6. **Dealer asking about another customer.** `Sorry, I can only share sales figures for your
   own account.`

Also fixed lines, used only when the message is genuinely ambiguous: basis clarify
`Delivered (transferred to DO) or ordered?`; month clarify `One month only, or a month by
month breakdown?`. Denied (no grant): `Sales report is not enabled for your account.`, the
sales report's own line. Dates print through `_outstanding_date_range`, money through
`_rm_money`, quantities through `_outstanding_fmt_int`. No dash characters.

### Backend contract (S2)

`GET /api/v1/order-management/top-selling` on the sales report's own no-prefix router
(`orders.py::sales_report_router`, mounted beside `/sales-report`), same auth dependency
(`order_management.orders.view` with API key), same `contact_id` / `space_id` both-or-neither
rule and the same per-contact `sales_orders.sales_report` re-check (S14 of the sales report).

| param | type | rule |
|---|---|---|
| `rank_by` | `qty` / `amount` | **required** (owner: no default metric); absent or other is 422 |
| `top_n` | int | optional; absent = every row (subject to the one-message rule); 1..100, above 100 clamps to 100, below 1 is 422 |
| `basis` | `delivered` / `ordered` | default `delivered` (owner: "selling" means delivered); else 422 |
| `group` | `item` / `category` | default `item`; else 422 |
| `customer_ids` | csv uuid | the lane's resolved ledgers |
| `customer_query` | str | `ILIKE %q%`, min 3 chars (same guard as the sales report) |
| `category_ids` | csv uuid | resolved category rows |
| `sales_agent_ids` | csv uuid | resolved `sales_agents` rows |
| `channel` | `dealer` / `project` | absent = all, else 422 |
| `date_from`, `date_to` | flex date | on the bucket date, `_parse_flex_date` |
| `detail_code` | str | one product code (item grain) or category code (category grain): returns the detail body instead of the ranking |

Dealer scoping is enforced in the ROUTE, not only the lane: when the contact's tier is
dealer, the route replaces `customer_ids` with the contact's own ledgers and answers 403
`other_customer` when the request names a customer outside them (the lane checks first and
prints fixed line 6, so the 403 is a backstop, never the customer's words).

Response (`TopSellingResponse`, every field declared, asserted through the route):

```
{
  "group": "item" | "category", "rank_by": "qty" | "amount",
  "basis": "delivered" | "ordered", "top_n": int | null, "total": int,
  "customer_name": str | null, "category_name": str | null,
  "sales_agent": str | null, "agent_fill_pct": int | null,
  "channel": "dealer" | "project" | null,
  "date_from": date | null, "date_to": date | null,
  "rows": [ { "rank": 1, "code": str, "name": str | null, "qty": number, "amount": number } ]
}
```

`rows` is empty with `total > 0` exactly when `top_n` is absent and `total > 1` (owner, PR
#1258 05:32Z: no size threshold, no setting; n8n chunks long messages). The presenter renders
shape 2 off that pair. The envelope also carries `result_set`, one `{idx, label, code, name,
entity_type}` row per printed line (`idx` = rank; label = the product code at item grain, the
printed category name at category grain), empty on the how-many reply and the miss.

Detail body (`detail_code` set): `{..the same header fields.., "detail": {"code", "name",
"by_customer": [{customer_name, qty, amount}], "by_month": [{month, qty, amount}]}}`, both
lists sorted by the same metric desc; the detail presenter and its golden land with S2.

Service: `top_selling(db, filters) -> dict` in `app/services/sales_report_service.py`, ONE
grouped query over `sales_order_lines` join `sales_orders` join `products`, optional join
`product_categories`, `GROUP BY` product (or category), `ORDER BY <metric> DESC, <other
metric> DESC, code ASC`, `COUNT(*) OVER ()` for `total`, `LIMIT top_n` (or the setting + 1
when absent). Reuses `_common_filters` (extended with `category_ids` and `sales_agent_ids`),
`_bucket_expr`, `_per_line_exprs` (ordered pair = `ordered_qty` / `ordered_value`, delivered
pair = `confirmed_qty` / `confirmed_value`), `_customer_echo`, `_qty`, `_money_edge`.
Aggregated in SQL (S17 of the sales report). Decimal throughout, quantised at the edge.
Company scope through the ORM classes, never raw text SQL. Zero-value lines included (owner
ruling).

### As built (S2 + S3, PR #1263)

The route shipped under the relaunch rules (26 Sep: no paging; absent `n` = every ranked row
plus `total_count`), which supersede the contract above where the two disagree. Each line says
what shipped and what decides it.

- `n`, not `top_n`: 1 to 100, and 0, negative or above 100 is 422 `invalid_n` (no clamp).
  Absent = every ranked row. The lane maps the parser's `top_n` to `n`.
- `total_count`, not `total`; `totals {quantity, amount}` over the whole ranked set, so `n`
  never changes them. Rows carry `quantity` / `amount`, not `qty` / `amount`.
- `rank_by` values are `quantity` | `amount` (not `qty`); missing is 422 `rank_by_required`.
- Owner ruling 26 Sep ~07:40Z: "don't need to show name, just show code will do." Rows carry
  `code` only, never `name` (AC-1902, AC-1928). `detail.name` is unaffected - the detail offer
  is not a ranked row, and S4 still names the one code it was asked about.
- No one-message setting (`chatbot_top_selling_one_message_rows`, AC-1933): the relaunch rule
  supersedes it. The route always returns every row when `n` is absent; the how-many question
  (AC-1911) is S4's, decided off `total_count`.
- Date default is set in the ROUTE (current calendar year, Malaysia time, echoed as the
  resolved `date_from` / `date_to`), not the lane. Supersedes AC-1922's "no dates = every
  line" and AC-1953's "built in the lane". One side absent takes that side of the current year;
  `date_from > date_to` is 422.
- Basis: delivered = the sales report's confirmed pair; ordered = `qty_ordered` /
  `coalesce(line_total, 0)`, whatever the line's status (AC-1931 as written; review B1).
- Filters echo under `filters {customer_name, category_name, sales_agent, channel,
  dealer_scoped}`. Parameter names are the plural csv forms (`customer_ids`, `category_ids`,
  `sales_agent_ids`).
- `sales_agent_fill_rate` (0 to 1, four places) is sent whenever an agent filter is used,
  not `agent_fill_pct` only below 95% (AC-1915). S1 / S4 turn it into the note and decide the
  threshold.
- Refusal code is 403 `customer_not_permitted`, not `other_customer` (AC-1934).
- Who is staff (review S2, fail closed): a contact linked to any customer
  (`respond_contact_customers`) is a dealer, forced to those ledgers, whatever else it holds.
  An unlinked contact is staff only when every access type it holds reads as the office tier
  (the chatbot tier_gate names, restated in the route: "Sorento Office", "Mocha Office", "Cabana Office") and one of them
  is active. Every other unlinked contact (no type, end user, dealer with no link, a type
  nobody classified) is refused 403 `customer_not_permitted` with no figures.
- A dealer's `customer_query` is refused unless it matches one of its own ledgers: matching
  only other customers and matching nobody get the same 403, so the route is no name oracle
  (review S1).
- `detail_code` (AC-1935, review S4) is on the route: one product code (item grain) or
  category code (category grain), case-insensitive. `rows` / `totals` narrow to that code and
  `detail {code, name, by_customer[{customer_name, quantity, amount}], by_month[{month,
  quantity, amount}]}` follows, same filters and basis, each sorted by the metric desc (then
  the other metric desc, then name / month asc). A code with no sales returns no rows and
  `detail: null`. The detail PRESENTER and its golden are S1's (the presenter is not on main
  yet).
- The `chatbot_domains` migration that adds the tool to the `order` row
  (`chatbot_top_selling_tool`) shipped with S3; S4 adds no second one.

Lane notes for S4 (review nits):

- N1: absent `n` returns the whole book (a full year was 13,230 rows / about 1 MB on a
  750k-line seed). S4 sends `n` whenever it will print rows; only the how-many path omits it.
- N2: dealer scope applies only when `contact_id` + `space_id` are sent (the sales report's
  posture). S4 must always send both on every call.

### Parser (S4)

- `order_status` gains `top_selling`: "top 5 selling", "best selling", "hot selling", "top
  items", "most sold", "which category sells most", "paling laku", "畅销". The addendum says in
  words that "top 5" with no sales word under an order / stock question stays what it was
  (`top_n` on an order list).
- New nullable key `rank_by`: `"qty" | "amount" | null`, from the current message only. Null
  is not defaulted: the lane asks the metric clarify.
- New nullable key `basis`: `"delivered" | "ordered" | "unclear" | null`. Null = delivered;
  `unclear` (e.g. "top orders") = the lane asks the basis clarify. "ordered" on its own as a
  follow-up under a carried `top_selling` ask sets it.
- New nullable key `rank_group`: `"item" | "category" | "unclear" | null`. Null = item;
  `category` for "which category sells most"; `unclear` when a category word could be either
  a filter or the grain ("top selling by category") = the lane asks the group clarify. A
  named category ("top kitchen sinks") is a filter entity, never `rank_group`.
- `top_n`: unchanged, already taught. Absent = no cut-off (owner ruling), never defaulted.
- Month: "by month" alone is `unclear` for the month clarify through the existing
  `date_mode`; the per-month breakdown answer is S7 below.
- All three new keys are declared in the strict schema, in `required`, and in
  `TOLERATED_ABSENT` (the same reason `sales_channel` is).
- `category` under `order`: the addendum names category words on a top-selling ask as
  entities with hint `category`, `hint_confident: true`.
- `sales_agent`: a new entity hint, "by sales agent X", "agent X", "salesman X",
  "jurujual X".

No word table anywhere in Python (S12 of the sales report holds).

### Lane wiring (S4), smallest shape per seam

1. `contracts.ENTITY_HINTS`: `sales_agent` added (S5; no longer gated, owner ruling 26 Sep).
2. `turn/policy_rows.DEFAULT_DOMAIN_ROWS["order"].tools` += `crm_top_selling_report`, plus
   `DATE_PARAM_TOOLS`; a `chatbot_rearch_s13`-style migration applies the same to a seeded DB.
3. `gate.ALLOWED["order"]` += `category` and `sales_agent` (S5). `NO_TOOL_ID` stays as is
   (the category id is read off the entities inside this tool's own arg block, the way
   `crm_low_stock_report` reads its product codes, so `TYPE_TO_PARAM` and every other domain
   are untouched).
4. `entity_resolver._DOMAIN_HINT_EXPANSIONS["order"]["category"]` re-types a category token
   as a customer. The reconciliation step asks for the hinted kind first when
   `hint_confident` is true (`head/parser.py` schema comment, AC-1506); a test pins that a
   confident `category` hint on a `top_selling` ask resolves against `product_categories`. If
   it does not, the narrowest fix is an `order_status`-aware exception in that one dict, not a
   resolver change.
5. `run_fetch`: one `elif` beside the sales report override: `domain == "order" and
   order_status_raw == "top_selling"` picks `crm_top_selling_report`, gate on
   `_SALES_REPORT_GRANT` first (same constant, no new key). The two engine-level checks
   (`engine.py:1706`, `__init__.py:590`) test `order_status in {"sales_report", "top_selling"}`
   through one shared tuple `SALES_FIGURE_STATUSES` declared in `contracts.py`.
6. `fetch.DATE_PARAMS["crm_top_selling_report"] = ("date_from", "date_to")`.
7. `entity_ids_transformer`: a `tool_name == "crm_top_selling_report"` block: pop
   `product_ids` / `warehouse_ids`; `customer_ids` from the generic map (carried ids win, same
   as the sales report); `category_ids` off entities typed `category`; `sales_agent_ids` off
   entities typed `sales_agent` (S5); `rank_by`, `basis`, `rank_group` (as `group`) and
   `top_n` from `semantic_input` (absent `top_n` sends no param: no cut-off, owner ruling 26
   Sep); `channel` from `sales_channel`; the current-calendar-year date default built HERE,
   never in the route, turned off by `broaden_axis == "date"` exactly as S18. Owner ruling 26
   Sep: a dealer contact's `customer_ids` are its own ledgers, whatever it named.
8. `_fetch_semantic_input` gains `rank_by`, `basis`, `rank_group`.
   `turn/apply._IDLE_CHAT_DISQUALIFIERS` gains the same three. Owner ruling 26 Sep changes
   the carry rule: because the metric and group clarify questions and the how-many question
   are answered in a SECOND message, `top_n`, `rank_by`, `basis` and `rank_group` are carried
   on `Focus` while `focus.status == "top_selling"` (a new value in the follow-up wins; a new
   subject is a new ask and clears them). "by amount", "ordered", "20" and "amount" are all
   refinements of the carried ask, which is what the customer means.
8a. Clarify seams (owner ruling 26 Sep, no defaults): before any fetch, in this order,
   `rank_group == "unclear"` sends the group clarify, a null `rank_by` sends the metric
   clarify, `basis == "unclear"` sends the basis clarify, an unclear month sends the month
   clarify. Each is one fixed line from `presenters.py`, arms nothing new (the carried
   `focus.status` is what makes the answer land), and fetches nothing. A dealer naming a
   customer outside its own ledgers gets the refusal line, also before any fetch.
9. `output_structurer`: `crm_top_selling_report` takes the miss path on `has_result: false`
   and, on a ranking hit, arms the envelope's `result_set` as a sticky `top_selling_pick`
   roster via `turn/pending.top_selling_pick` (owner, PR #1258 05:32Z: behaves like the
   customer and product pickers; owner ruling 26 Sep: detail offer required), so a rank
   number or a typed code / category name re-calls the tool with `detail_code` = that row's
   code (category grain: re-calls the item ranking with that category as the filter), and
   the list stays open for the next pick. The how-many reply arms nothing. `_search_scope_header` skipped for it, same as the two reports.
10. `resolve_gate` picker hint off for `top_selling` (R20 rule), same population reason.
11. `mcp_tool_domains.py`: `crm_top_selling_report` under `orders`. NO in-app assistant
    bootstrap (security B1 of the sales report: the same money, same reason).


### As built (S4)

Where S4 differs from the two sections above, and which ruling decides it.

- Parser keys use the route's own vocabulary: `rank_by` quantity | amount (not `qty`),
  `basis` delivered | ordered | unclear, `rank_group` item | category | unclear, all
  nullable enums, required for strict mode and in `TOLERATED_ABSENT`. Taught by the
  trailing `TOP_SELLING_ADDENDUM`, published unlabelled by `chatbot_top_selling_vocab`
  (ONE body: the slim body was retired by the rearch S0).
- The carry is ONE slot, `Focus.top_selling` (`turn/apply._top_selling_rules`), live
  while `focus.status == "top_selling"`. A fresh ask (the parser's `domain_in_message`)
  states its own axes unless the last reply was one of the lane's questions; "by
  amount" / "ordered" under a ranking change only the axis they name; any other new ask
  leaves the ranking. A bare number the parser reads as a position, with no list open
  and a count awaited, is the count.
  Fix lane (PR #1273, reviewer B2): "the last reply was a question" is RECORDED, not
  inferred from an empty axis. The lane stamps `top_selling_asked` (group, metric, basis,
  category, how_many) on its clarify and how-many replies, `engine.py` writes it onto the
  slot after the fetch (`apply.record_top_selling_asked`) and clears it when a list, a single
  row, a detail, a miss or a refusal goes out. A fresh ask completes the last one only when it
  answers the clarify asked; otherwise it starts over and also drops the customer, channel
  and date window it did not name. The count rule needs the how-many to have been asked.
- No N named: the lane sends `count_only=true` (new route param, review N1) and the route
  returns the full count with no rows, one row kept as is; the presenter asks how many
  (owner, PR #1258 05:32Z). A named N over 100 is capped at 100 by the lane.
- No date: the lane sends none and the route's current-year default is echoed (as built
  on PR #1263). "All dates" said in words is NOT honoured yet (the route has no
  unbounded window); trigger: the owner asks for an all-dates ranking.
- Rows print the code only, at both grains (owner, 26 Sep 2026: "just show code will
  do"), and the pick label is that printed code. A category row therefore prints the
  category code, not its name.
- The ranked list arms a sticky `top_selling_pick` roster through `compose._lane_question`
  (S1's `turn/pending.top_selling_pick`). A pick is answered by
  `apply._answer_top_selling_pick`, never the generic roster path (the rows carry no uuid
  and must not land on `focus.products`): an item row sends `detail_code` (one shot),
  a category row re-runs the item ranking with that category as a filter.
- Category words are resolved by the lane (`services.resolve_category_token`), never by
  the generic resolver, which is not even asked about them (the engine strips
  category-hinted entities from its input on a top selling ask; AC-1954's hazard). A word
  matching no category is a miss, never a silent widening.
  Fix lane (PR #1273, reviewer S1): exact code or name (a plural folded) wins alone, else the
  word must be a whole-word run of the name; the reverse containment is gone. Several
  matches are asked with their codes, never all kept.
- Dealer refusal: the route is the check (403 `customer_not_permitted`, no name oracle);
  the presenter turns that body into the fixed refusal line. The customer picker still
  runs for a dealer's ambiguous customer word before the route refuses; trigger to move
  the check earlier: the first dealer contact holding the sales report key.
  Fix lane (PR #1273, reviewer S2; owner ruling 26 Sep ~09:05Z, confirmed: a dealer never
  sees another customer's name): moved earlier now. `engine._top_selling_dealer_scope` reads the
  contact's `respond_contact_customers` links (the route's own dealer test); a linked
  dealer's customer words never reach the generic resolver, words matching its own ledgers
  run on those ids (`dealer_customer_ids`), and any other word gets the refusal line with no
  lookup and no fetch. An UNLINKED contact that is not office staff still reaches the picker
  before the route refuses it; trigger: the owner confirms the ruling covers them too.
- Nits fixed in the fix lane: N1 (the catalog steers a no-number ask to `count_only`), N2
  (`DATE_PARAM_TOOLS` lists the tool) and kill R4 (`count_only` pinned to `n=1`). Not fixed:
  N3 (picking the `Unassigned` category row sends the label; the route has no "no category"
  filter, so the fix is not one line), N4 (downgrade on a fresh DB, informational), N5
  (console only).
- `top_selling` joins `resolve_gate.OUTSTANDING_ORDER_STATUS` (no DO hint on the picker,
  point 10) and `contracts.SALES_FIGURE_STATUSES` (grant before roster, point 5).
- A no-subject miss prints the ranking's own header and `No sales found.` above the
  escalate offer (`answer._outstanding_report_text` now reads the wrapped fetch body).
- Not built in S4: the month clarify and the per-month breakdown (S7), the sales agent
  entity kind and resolver (S5; the arg block already maps a resolved `sales_agent`
  entity to `sales_agent_ids`).

### As built (fix lane round 4, owner retest 27 Sep 2026)

Source: the owner's PR #1273 comment "Owner ruling from the retest of top selling (27 Sep
11:27 to 11:35 MYT ...)", verbatim from chat, and its seven transcript items. Pinned by
`tests/chatbot/test_top_selling_round4.py`, which types the owner's own messages and
replays the transcript in order.

- F1 quick replies: a ranked list sends no per-product chips (`top_selling_pick` joins
  `turn/pending.quick_replies_suppressed`); the "Reply with a rank number" line stays and a
  typed rank still opens the row. No question sends more than three chips
  (`pending.MAX_QUICK_REPLIES`, WhatsApp's own button limit); a longer list goes out as its
  numbered text alone.
- F2 sales agent: "sold by fanny", "sales agent is fanny", "by agent fanny" are an entity
  with hint `sales_agent` (prompt). `engine._top_selling_narrowing` keeps these words away
  from the generic resolver and matches them against `sales_agents` (code or person label,
  whole words, `services.resolve_sales_agent_token`); the ids ride on the slot as
  `agent_ids`. A `customer` word that names an agent and no customer is the agent; one
  that names both is asked: "Do you mean customer X or sales agent Y? Reply 1 for the
  customer, 2 for the sales agent." A word naming no agent is said once ("I don't know
  'X' as a sales agent.") above the ranking. A dealer's customer words are never searched
  against other customers (its own-ledger rule stands).
- F3 corrections: "customer is everyone", "all customers", "any customer", "everyone" are
  `broaden_axis: customer`, `broaden_to: all` (prompt); the generic `_broaden` clears the
  focus customers and `_top_selling_rules` clears the slot's own filter for that axis
  (`TOP_SELLING_BROADEN_KEYS`). No new "nothing changed" reply: the correction is applied.
- F4 metric: "1" and "2" under "By quantity or by amount?" are the parser's position, read
  in the order the question lists (`top_selling_position_is_the_metric`); "qty",
  "quantity", "amt", "amount" are taught. An answer carrying more ("amount, top 100, water
  closet") is applied whole. A lane question closes the old ranked list, so the "2" that
  answers it never picks row 2.
- F5 category: live category names are copies of their codes (SRT-WC), so a word with no
  code or name hit goes through the stock ask's class vocabulary
  (`product_class_signal.resolve_classes_for_term`: class label, synonyms, classes products
  carry, staff synonyms), then a whole-word run of one class's label or synonym ("water
  tap" holds "tap"), taken only when exactly one class answers
  (`services.resolve_category_class`). The route echoes the shared class label ("Category:
  Water Closet"). A word matching nothing is said once ("I don't know 'X' as a
  category.") and the ranking runs without it. A narrowing message ("water closet only")
  never leaves the ranking (`apply._narrows_the_ranking`), and a ranking with no sales is
  its own answer ("No sales found.") with no escalate offer and no routing picker
  (amends AC-1957).
- F6 brand: `brand_ids` on the route (`Product.brand_id`, the narrowing PR #1301 gives the
  outstanding report), echoed as `filters.brand_name` and printed on every header as
  `Brand:` (`all` when none). A `brand` word, or a `customer` word that IS a brand's name
  or code, resolves against the live `brands` table (`services.resolve_brand_token`),
  never through the customer resolver.
- F7 direction: new nullable parser key `rank_direction` (`top` | `bottom`), required for
  strict mode and in `TOLERATED_ABSENT`; the route takes `direction` (`top` default,
  `bottom` ranks both metrics and the code ascending). Titles: `*Bottom N selling items*`,
  `*Least sold items*` with no N. Items with no sale are not ranked (the plan never ranks
  them); the bottom header says so in one line: "Items with no sale in this period are
  not ranked."
- F8: every reply in the replay is checked for snake_case and dashes.

### As built (fix lane round 5, owner retest of round 4, 27 Sep 2026)

Source: the owner's PR #1273 comment "Owner ruling from the retest of top selling round 4
(27 Sep, about 11:30 to 14:10 MYT, :3083)", its five transcripts and the orchestrator's
notes. Pinned by `tests/chatbot/test_top_selling_round5.py`, which replays the five
transcripts in order and types the owner's own messages; live parser run:
`tests/chatbot/console_cases/2026-09-27-top-selling-round5.yaml`.

- Console: the retest ran every turn on parser v34 because `useChatbotConsole.ts` kept
  the version pick in localStorage and restored it on each page load ahead of the newest
  full version. The pick now lives only for the page session; the composer shows "Parser
  vNN"; the per-turn trace pill already carried the version.
- One seam reads a message inside a ranking against the question the bot asked, before
  anything routes it: `engine._top_selling_verdict`, run just before the first APPLY
  pass. It is the only place that reads the message text for top selling, and only for
  the closed questions the bot itself printed (a bare number, and the words of the
  "customer or sales agent?" options, `TOP_SELLING_WHO_WORDS`). `turn/apply` stays text
  free: the seam hands it `top_selling_who`, `top_selling_unclear` and
  `top_selling_leftover` on the verdict.
- R1/R2: "2", "1", "yeah sales agent", "sales agent", "fanny sales agent", "neither" (the
  parser's `is_affirmative: false`) answer "Do you mean customer X or sales agent Y?"
  whatever the parser made of them (v34 read "2" as a promotion lookup of "fanny water
  closet", "yeah sales agent" as low signal). The answer sets that axis, clears the other
  (the customer word the generic rules left on the focus included, which is what answered
  "Couldn't find: fanny (customer)"), keeps the category, count and metric, and runs the
  ranking (or asks the metric if the ask never named one). A customer word in a message
  that says "agent" is the agent, no question.
- R3: inside a ranking, "customer is everyone", "all customers", "neither" or a message
  naming an agent over an open customer picker closes the picker and clears the customer
  (`broaden_axis: customer`), never "all" as a pick of every row (contract 31 stays for
  every other picker).
- R4: a current entity of several words is split into word groups, longest first, matched
  strictly against sales agents, categories (code, name or class vocabulary term), brands
  and customers (`engine._split_noisy_token`). A token naming an agent or a brand beside
  another kind always splits; any other splits only when it does not resolve whole ("water
  tap" stays the Tap class, "SAMPLE - CONTACT Z" one customer). Words under three letters
  ("by") are dropped; one unknown leftover is said once ("I don't know 'marble'.") and the
  ranking runs. A split word the parser sent to a promotion or document lookup is read
  back into the ranking.
- R5: after a ranking, an order report ask ("outstanding", "can show me the DO", "show me
  the orders"; `apply._is_report_hop`) runs that report with the customer and the period
  in force (the named window, else the current year the ranking's route defaults to), and
  drops agent, category and brand, which no order report takes: one header line "Filters
  from the ranking: Customer: X / Period: dd/mm/yyyy to dd/mm/yyyy" and one line per dropped
  filter ("Category is not a filter for delivery orders, showing all categories"),
  `fetch.top_selling_hop_lines`. The ranking-only words leave the focus, so the order
  lookup never answers "Couldn't find: bathtub (category)". The slot stays marked `hop`, so
  the next report carries the same filters; a new ranking ask starts over. The order list's
  own scope header now reads the window the fetch ran over (`_spec_window`), not "all
  dates". Trigger to add agent, category or brand to the order reports: the owner asks for
  outstanding by agent.
- R6: "worst / worse 100 hot selling", "bottom 100" are taught as least sold rankings;
  inside a ranking conversation an out of scope reading gets one short question ("Sorry, I
  didn't get that. What would you like to change in the ranking?"), never the out of scope
  lane or the routing picker. A request for a person is still `request_for_help`.
- R7: over an open ranked list only a bare whole number 1 to N is a pick; "2025" or
  "2025?" is that year (the same ranking re-run over it); any other number (past the end,
  with words or punctuation) is not a pick and the ranking runs again with its filters.
  Amends S4's "a number past the end re-prints the list".
- R8: inside a ranking no picker lists more than five options (the roster cap is lowered
  for the turn), and a ranked list is never re-printed whole as a menu ("Which item do you
  mean? Reply with a rank number from 1 to N.").
- R9: every new line is plain words; the replay asserts no snake_case and no dash in any
  reply.

### As built (fix lane round 6, owner retest of round 5, 27 Sep 2026)

Source: the owner's PR #1273 comment "Owner retest of top selling round 5 (27 Sep 20:04
MYT, console :3083, parser v39)": "top 100 sold item" asked "By quantity or by amount?",
then "amount" answered the order list. Pinned by `tests/chatbot/test_top_selling_round6.py`,
which replays both messages the way the console runs them (`console_service.
run_console_turn`, dry run, the second turn sent the `session_vars` the first returned).

- Diagnosis. The console's between-turn memory did not regress: a dry run writes no
  session (`remembered: written false`), and the console carries its own, the turn's
  `session_patch` handed back as `session_vars` and sent as `previous_conversation_state`
  (`console_service._next_state`, `useChatbotConsole`), the same carry PR #1300 round 3
  found. After turn 1 it holds `focus.status: "top_selling"` and `focus.top_selling:
  {"top_n": 100, "asked": "metric"}`, so no console-only carry is added. No code asks the
  metric question without the parser's `order_status: "top_selling"` (the top selling
  override in `lanes/business` is the only asker), so v39 did read "top 100 sold item" as
  the ranking and the ask was in progress. The parser then read "amount" as a new order
  ask (`domain_in_message: true`, no `rank_by`), and `apply._top_selling_rules` left the
  ranking for it; that reading alone reproduces the owner's screen.
- R2: the answer to the ranking's own question binds before anything routes it,
  `engine._top_selling_question_answer`, called from `_top_selling_verdict` beside round
  5's who binding. It reads only the options the question printed
  (`TOP_SELLING_OPTION_ANSWERS`): the metric ("qty", "quantity", "amount", "amt", "1",
  "2"), the grain ("items", "categories"), the basis ("delivered", "ordered", "1", "2"),
  the count under the route's how-many reply ("20", "top 20") and a code under "Which
  category do you mean?". A message of more than four words is bound only when the parser
  itself read it as the ranking, so "outstanding" or a new order ask under the question is
  not taken for an answer. The question is read off `focus.top_selling.asked`, which the
  console and WhatsApp both carry, so both land the same way. The ranking asks no
  direction question (a direction is only ever stated), so there is none to bind.
- R1: the parser words teach "top 100 sold item(s)", "most sold item", "top selling",
  "top 100 hot selling item", "highest selling", "top sellers" as the ranking ask, and an
  answer to the ranking's own question as `domain_in_message false`, never an order list.
  Republished unlabelled by `chatbot_top_selling_vocab_r6` as the next version after
  whatever the database holds (v40 on a database whose newest is v39).

### As built (fix lane round 9, owner hand test after round 8, 28 Sep 2026)

After "Do you mean customer SAMPLE - WILLIAM or sales agent WT I, WT III, WT IV? Reply 1
for the customer, 2 for the sales agent." the owner typed "saleman" and "salesman" and got
the low signal greeting. `engine._top_selling_who_answer` knew only "customer", "sales"
and "agent" as whole words.

* `TOP_SELLING_WHO_WORDS` gains "client" (1) and "salesman", "salesmen", "salesperson",
  "rep" (2). "sales man", "sales person" and "sales rep" already carried "sales".
* A reply of at most four words is also read for a typo one edit away (insertion,
  deletion, substitution or a swap of two neighbours) of an option word of five letters
  or more, so "saleman", "sale man", "slaesman", "agnet", "custmer", "clinet" answer.
  "rep" is matched exactly. A longer message (a new ranking ask) is read for the exact
  words only, as before.
* A short reply that resolves to the question's own agents only ("WT", "wt i", the
  person label) is 2, to its own customers only ("SAMPLE - WILLIAM") is 1; a word naming
  both ("william") is not an answer, as before.
* No parser prompt change, no migration. Pinned by
  `tests/chatbot/test_top_selling_round9.py` (the owner transcript replay plus every word).

### As built (fix lane round 8, owner hand test of round 7, 28 Sep 2026)

Supersedes round 5 R5's and round 7's "Filters from the ranking" shape (owner: "why don't
we just show as is ... it should carry all these as an input to the message and continue
from there").

- **R1 the ordinary route.** A ranking that lists items records their codes on the slot
  (`fetch._top_selling_output` `top_selling_codes`, `turn_runtime.envelope_of`,
  `apply.record_top_selling_asked` `ranked_codes`). An outstanding ask after it
  (`apply.TOP_SELLING_OUTSTANDING_HOPS`) writes them on the hop (`apply._hop_to_report`
  `product_codes`), unless the message names a product of its own, and
  `turn_runtime.lane_parse_output` hands them to the lane as the ordinary carried
  subject (`outstanding_carried_product_code(s)`, which `run_fetch`'s `carried_subject`
  now also reads). From there it is the direct path: the outstanding override, the
  Product / Customer / Location / Order date header, the scope menu, and "1" answered off
  the question's own stored filters (`_settle_question_subject`).
- **R2 no header.** `fetch.top_selling_hop_lines`, the `output_structurer` wrapper, the
  `top_selling_hop` lane key and `TOP_SELLING_RANKING_ONLY` are deleted. The agent,
  category and brand filters are simply not carried into another report.
- **R3 cause.** With no product and no customer the ask missed the outstanding override,
  so it ran `crm_order_management_orders_list`, whose window is the actual delivery date
  (`fetch.DATE_PARAMS`); `order_service.py:880` filters `Order.actual_delivery_date >=
  from`, and an outstanding order has none yet: "No matching results found." With the
  ranked codes carried the ask never reaches the order list.
- Tests: `tests/chatbot/test_top_selling_round8.py` (the owner's seven messages, R1, R2);
  round 5 `TestR5Continuity` / `TestOwnerTranscripts` and round 7's hop pins updated.

### As built (fix lane round 7, owner retest of round 5 part 2, 27 Sep 2026)

Source: the owner's PR #1273 comment "Owner retest of top selling round 5, part 2 (27 Sep
21:05 to 21:10 MYT, console :3083, parser v39)". Pinned by
`tests/chatbot/test_top_selling_round7.py`, which replays the owner's messages in order
through `console_service.run_console_turn` with the REAL resolver on seeded rows named like
the owner's (HANLIM TRADING, SAMPLE JAYDEN, SHOPEE - 260818MRNUPWTS (MCH), NEWTON BUILDMATE
SDN BHD (PROJECT) (SRT); FANNY, JAYDEN and WT accounts).

- R1 diagnosis. The ranking turn leaves the raw agent word ("Jayden", "wt") in
  `focus.extra["sales_agent"]`, and the parser may echo it as a carried entity. On the
  report turn the ranking's own narrowing no longer ran (it ran only while
  `focus.status == "top_selling"`), so `turn_runtime.with_carried_entities` or the echo
  handed the word to the generic resolver, which has no agent probe and typed it a
  customer: SAMPLE JAYDEN by whole word, and the two "wt" customers by substring
  (`entity_resolver._probe_customer`'s name ILIKE). A reading carrying only the v3
  `status: "outstanding"` skipped the hop entirely, because `apply._focus_rules` wrote
  `focus.status` from it before `_top_selling_rules` read it.
- R1 fix. On a report a ranking handed over to (`focus.top_selling.hop`) the resolver gets
  no agent, category or brand word and no carried word without an id
  (`engine._without_carried_words`); an echoed word is dropped from the verdict too
  (`engine._top_selling_verdict`). The hop keeps carrying what the ranking resolved (the
  customer ids and labels, the period) and drops what the report cannot filter by with
  round 5's one line. `_top_selling_rules` reads whether a ranking was open before the v3
  `status` overwrote it, and `_is_report_hop` reads that key too. A fresh ranking ask
  drops a document picked for an earlier report. The generic resolver's customer name
  tier keeps whole words only (a spaceless spelling of the words still matches).
- R2. Over an open ranking, a message that is only a period ("2025?", "july", "q1 2026")
  re-runs the ranking for it, list open or not (`_period_only`). Ranking words ("top 10
  hot selling", "worst 20 selling", "best sellers", "most sold") make the message the
  ranking ask whatever the parser's status (`_ranking_words_claim`), with the count and a
  bottom direction read off the words only when the parser gave none; an unknown agent is
  said above the metric question as well as above the ranking. Inside a ranking or the
  report it handed over to, the answer and the tail drop the escalate offer and the
  routing picker (`engine._without_escalation_offer`, `ctx.top_selling_no_offer`) unless
  the message asks for a person (`request_for_help` or an escalation confirmation).
- R3. Over an open ranking, "sales order?", "based on sales order", "by SO", "SO basis",
  "ordered" set Basis: Ordered and "delivered", "by DO", "DO basis" set Delivered
  (`TOP_SELLING_BASIS_WORDS`), before any rank pick; "can show me the DO" stays round 5's
  delivery order report. Over a ranked list the parser's `open_question_answer` pick is
  held to round 5's rule too (only a bare 1 to N picks).
- R4. `sales_agents.aliases` (String 255, "Also known as", comma separated, tidied by
  `sales_agent_service.normalize_aliases`), edited on Master data > Sales agents > record >
  General. `lanes/business/services.resolve_sales_agent_token` matches the code, the person
  label, then the aliases, whole words; an alias hit widens to every account of the person
  (same code stem, or the same person label). Nothing is seeded.
- Parser words: unchanged, so no prompt migration and no new parser version this round.

## Filters combination matrix (rewritten for the 26 Sep rulings)

Every filter is optional and every combination is one query with the filters ANDed. The
header prints the axis when given and `all` when not. Three axes are NOT filters and are
settled before the matrix applies: the metric (asked when absent), the group (items unless
the message ranks categories; asked when unclear) and the basis (delivered unless the message
says ordered; asked when unclear).

| customer | category | sales agent | date | channel | what runs |
|---|---|---|---|---|---|
| no | no | no | no | no | whole book over the current calendar year; header says the window |
| yes | no | no | any | any | that ledger family only |
| no | yes | no | any | any | products in that category only (a filter, owner ruling 26 Sep) |
| no | no | yes | any | any | SOs that agent sold (`sales_orders.sales_agent_id`, owner ruling 26 Sep) |
| yes | yes | no | any | any | AND |
| yes | no | yes | any | any | AND; a customer whose SOs carry another agent code returns fewer rows, never a picker |
| no | yes | yes | any | any | AND |
| yes | yes | yes | any | any | AND |
| any | any | any | "all dates" said | any | date default off, `Delivery date: all` |
| any | any | any | a month / a range | any | that window |
| any | any | any | any | dealer / project | `demand_class` filter, header `Channel: Dealer / Project` |
| dealer contact | any | any | any | any | customer forced to the contact's own ledgers (owner ruling 26 Sep); another customer named = refusal line, no fetch |

Owner ruling 26 Sep, N across the matrix: a named N (1 to 100) cuts every row of the matrix;
no N returns the how-many question with the full count (owner, PR #1258 05:32Z: no size
threshold; n8n chunks long messages), except a single row, which is sent. Category grain (ranking categories) accepts every
filter above except a category filter, which it ignores with the header printing `all`.

An ambiguous customer name takes the existing picker on every row that names one; a category
token that resolves to several categories takes `optional_filter` (the kind row's default):
all matched categories as an `IN`, never a picker.

## Slices (rewritten for the 26 Sep rulings)

| slice | phase | what | files |
|---|---|---|---|
| S1 | 1 | Presenter over mock JSON: `_top_selling` / `_top_selling_envelope` and the fixed clarify / refusal lines. Goldens: items by quantity, items by amount, categories ranked, items within one category, customer filter, channel filter, sales agent filter (with the fill-rate note), the how-many reply for a long list with no N, the metric clarify, the group clarify, the dealer refused, a miss; the detail offer line on every ranking hit; dash guard | `sorento_crm_mcp/sorento_crm_mcp/presenters.py`, `documentation/plans/chatbot/samples/top-selling-*.txt` + `.json`, `sorento_crm_mcp/tests/fixtures/top_selling/` (the CI mirror), `sorento_crm_mcp/tests/test_presenters_top_selling.py` |
| S2 | 2 | Route + service + response model, `basis` / `group` / `detail_code`, dealer scoping, the one-message setting, the detail presenter + its golden | `app/api/v1/order_management/orders.py`, `app/services/sales_report_service.py`, `app/schemas/order_management.py`, `app/config.py`, `presenters.py`, `tests/test_top_selling_report.py` |
| S3 | 2 | MCP ToolSpec + domain map | `sorento_crm_mcp/sorento_crm_mcp/catalog.py`, `app/services/mcp_tool_domains.py`, `sorento_crm_mcp/tests/test_catalog_top_selling.py` |
| S4 | 2 | Parser addendum + prompt migration, `rank_by` / `basis` / `rank_group` keys, policy-row migration, gate row, tool override, arg block, focus carry, clarify seams, detail offer, dealer refusal | `chatbot_parser_prompt.py`, `alembic/versions/<n>_chatbot_top_selling_vocab.py`, `alembic/versions/chatbot_rearch_s13.py`, `contracts.py`, `head/parser.py`, `turn/policy_rows.py`, `turn/apply.py`, `turn/state.py`, `lanes/business/gate.py`, `lanes/business/__init__.py`, `lanes/business/fetch.py`, `lanes/business/resolve_gate.py`, `engine.py`, `tests/chatbot/test_top_selling_lane.py`, `tests/chatbot/console_cases/2026-09-24-top-selling.yaml` |
| S5 | 2 | Sales agent slot: entity hint + kind row migration + resolver source + gate row + arg param + route param + prompt words + fill-rate note. **No longer gated** (owner ruling 26 Sep: agent = `sales_orders.sales_agent_id`) | `contracts.py`, `turn/policy_rows.py` + migration, `entity_resolver.py`, `gate.py`, `fetch.py`, `orders.py`, `sales_report_service.py`, `chatbot_parser_prompt.py` (+ migration), `tests/chatbot/test_top_selling_sales_agent.py` |
| S6 | 3 | reviewer + security-reviewer (new route, reveal gating, dealer scoping, company scope) + console check, in parallel | `documentation/plans/chatbot/evidence/top-selling/` |
| S7 | 2 | Per-month breakdown ("top 5 by month" read as a breakdown): one ranking block per month in the window, the answer to the month clarify's second option. Shape to be confirmed by the owner on the S1 goldens before it is built | `presenters.py`, `sales_report_service.py`, `fetch.py`, tests |

Phase 1 for a chatbot lane is the presenter against a mock: the "UI" is the WhatsApp text
and the goldens are its screenshots; the owner reviews the goldens before S2.

## Tests per slice (tester writes red first; pytest per slice, live console at the end)

S1: AC-1901 `test_items_by_quantity_golden`; AC-1902 `test_row_line_shape`; AC-1903
`test_absent_axis_prints_all`; AC-1904 `test_ranked_by_and_basis_labels`; AC-1905
`test_never_prints_past_one_hundred`; AC-1906 `test_miss_prints_no_offer`; AC-1907
`test_presenter_keeps_input_order`; AC-1908 `test_money_qty_format_and_no_dashes`; AC-1909
`test_every_golden` (parametrised over all twelve); AC-1910 `test_no_paging_words`; AC-1911
`test_how_many_reply`; AC-1912 `test_detail_offer_on_every_hit`; AC-1913
`test_fixed_lines_match_goldens`; AC-1914 `test_category_rows`; AC-1915
`test_agent_fill_note`.

S2: AC-1920 `test_rank_by_qty_and_amount_with_tiebreak`; AC-1921 `test_top_n_optional_cap_and_422`;
AC-1922 `test_bucket_date_window_and_bad_date_422`; AC-1923 `test_customer_filter_ids_and_query`;
AC-1924 `test_category_filter`; AC-1925 `test_channel_filter`; AC-1926 `test_cancelled_excluded`;
AC-1927 `test_and_of_all_filters`; AC-1928 `test_response_model_keeps_every_field`; AC-1929
`test_auth_401_403_apikey_company_scope_and_reveal_403`; AC-1930 `test_zero_line_total_included`;
AC-1931 `test_basis_delivered_default_and_ordered`; AC-1932 `test_group_category_ranking`;
AC-1933 `test_one_message_setting_withholds_rows`; AC-1934 `test_dealer_forced_to_own_ledgers`;
AC-1935 `test_detail_code_customers_and_months`.

S3: AC-1940 `test_catalog_lists_top_selling_tool`; AC-1941 `test_read_only_union_and_domain_map`.

S4: AC-1950 `test_tool_pick_top_selling_vs_reports`; AC-1951 `test_no_grant_denies_before_fetch_and_picker`;
AC-1952 `test_rank_by_and_top_n_from_parser_only`; AC-1953 `test_param_mapping_and_date_default`;
AC-1954 `test_category_entity_resolves_under_order`; AC-1955 `test_customer_picker_continues_ask`;
AC-1956 `test_follow_up_by_amount_keeps_filters`; AC-1957 `test_miss_takes_not_found_path`;
AC-1958 `test_header_skipped`; AC-1959 `test_parser_prompt_live_sha_and_schema_key`; AC-1960
`test_plain_top_n_order_ask_unchanged`; AC-1961 `test_metric_clarify_then_answer`; AC-1962
`test_group_clarify_then_answer`; AC-1963 `test_dealer_other_customer_refused_no_fetch`;
AC-1964 `test_rank_number_opens_detail`; AC-1965 `test_how_many_then_number`.

S5: AC-1970 `test_sales_agent_entity_resolves`; AC-1971 `test_sales_agent_filter_route_and_lane`;
AC-1972 `test_agent_plus_customer_and`.

S6: AC-1980 console case file against the lane stack, then production after deploy
(`documentation/agents/chatbot-verification.md`); AC-1981 SQL cross-check of one whole-book
top 5 to the cent on the prod copy.

All backend tests on Postgres via `tests/_pg_fixture.py`, seeding company, customers,
categories, products, agents, SOs and lines (CI's database is empty).

## Grill questions for the owner (answered 26 Sep 2026)

1. **Default N** when the message names none: 5? (Hard cap 10 either way, WhatsApp.)
   Owner ruling 26 Sep: no default. No number = every row, no paging; a list longer than one
   message gets the how-many question. Cap is 100. Amended PR #1258 05:32Z: no number gets
   the how-many question whatever the count; n8n chunks long messages.
2. **Default metric**: quantity or amount when the message names neither? Owner ruling 26
   Sep: no default, clarify via chat.
3. **Quantity basis**: ordered or confirmed? Owner ruling 26 Sep: support both; normal
   meaning is delivered (transferred to DO).
4. **Date range default** when none is said: Owner ruling 26 Sep: current calendar year.
5. **Who may ask**: Owner ruling 26 Sep: the sales report key; staff see every customer; a
   dealer never sees another dealer.
6. **"by category"**: Owner ruling 26 Sep: both (filter and category ranking); clarify when
   unsure, never assume.
7. **Sales agent attribution**: Owner ruling 26 Sep: `sales_orders.sales_agent_id`. S5 is
   no longer gated.
8. **Channel**: Owner ruling 26 Sep: yes.
9. **Zero-value lines**: Owner ruling 26 Sep: include.
10. **No detail offer**: Owner ruling 26 Sep: a detail offer is required.

## Backlog triggers (not built)

- Breakdown mode per agent (top N per agent, one block each): the owner asks "per agent" on
  the live console. (Per category is now answered by the category grain, per month by S7.)
- Warehouse filter (`warehouse_codes` through the existing resolver): when the owner asks
  "top 5 at BRW".
- A `sales_agent` axis on `group_by` and on the sales report: when the sales module (#1170)
  needs agent-level figures from the chatbot.
- Product family collapsing (rank base codes, not variants): when the top 5 comes back as
  five colours of one sink.

## Risks

- Item grain groups by `products.id` (review N3). Under a multi-company scope two products
  sharing a code print as two rows with the same code; harmless under one company. Trigger to
  change it: the first multi-company contact that holds the sales report key.

- `_DOMAIN_HINT_EXPANSIONS["order"]["category"]` (resolver) is the one seam that can
  silently turn a category into a customer. Pinned by AC-1954 before S4 is called done.
- `policy_rows.py` is frozen seed data; every policy change here needs its migration and
  the two must agree (`test_rearch_invariants.py` family).
- `fetch.py` and `lanes/business/__init__.py` are the files every chatbot lane collides in;
  the changes are one `elif`, one dict entry and one arg block, kept that small on purpose.
- The parser prompt is a mechanical derivation of the live n8n body with trailing
  addenda; this adds a trailing block only, so `test_parser_prompt_is_live.py` still holds.
- A named N up to 100 can exceed one WhatsApp message (about 4,096 characters, roughly 50
  rows). Settled (owner, PR #1258 05:32Z): the bot sends it as one reply and n8n's existing
  chunking splits it; the lane adds no splitting of its own.
