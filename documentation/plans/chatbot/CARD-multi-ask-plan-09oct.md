# CARD - per-ask planning, exact codes, compact replies (MULTI-ASK-PLAN)

Status: behaviour card, waiting on owner answers Q1-Q5 (Plan stage). Track: L (chatbot core
planner + resolver + reply budget, a parser prompt migration, one new read endpoint + MCP
tool). Lane PR #1542 carries `crew-lane: MULTI-ASK-PLAN`. Measured on origin/main bf07eff8b.

Live parser checks and dev-data values cannot run in the cloud sandbox (no OpenAI, no dev
DB): both are listed at the bottom for crew to run locally.

## The journey (owner, 9 Oct 2026, PROD turn 8 Oct 19:50)

> SRTWT7445,2026 年 入库了多少数量 , 供应商是谁 ， 采购价是多少钱。 最后一次进库是什么时候 ，2026 总共卖了多少个，2026年每位业务员共卖了多少个

Six asks about ONE exact code: (1) received qty in 2026, (2) supplier, (3) purchase price,
(4) last receipt date, (5) total sold in 2026, (6) sold in 2026 per salesman.

What it got (18 s, dozens of WhatsApp parts, "very wasteful of WABA balance"): "No incoming
stock", a stock summary, 32 PO cost rows for 8 `SRTWT7445*` variants back to 2021, ~120 SO/DO
customer blocks back to 2020, and `客户: 33` (a count printed as if it were a name). Asks 1, 4,
5 and 6 were never answered.

## Why (verified against the code)

| Claim | Where | Verified |
| --- | --- | --- |
| The parser's `asks[]` is `{domain, intent}` only: no measure, no group_by, no window per ask | `head/parser.py:361-386` | yes |
| The planner makes ONE `FetchSpec` per domain, all sharing the turn's entities and window | `turn/apply.py:2282-2458` (loop `for name in domains`, `fetch.append` at :2450) | yes |
| A domain runs exactly one tool: `tools[0]`, or one override picked from ONE `order_status` per turn | `lanes/business/fetch.py:206-209`; overrides `lanes/business/__init__.py:1873-1879` (outstanding), `:1924` (sales_analysis), `:1955` (sales_report), `:2058-2064` (`crm_report_ask`) | yes |
| So "per salesman" (`crm_report_ask group_by=sales_agent`) could not run beside the order ask | `sorento_crm_mcp/catalog.py:810-840` | yes |
| `goods_receive` has no tool and `supported=False`, so the received-qty ask is dropped silently | `turn/policy_rows.py:259-268`; `turn/apply.py:2302-2305` (`continue`, nothing said) | yes |
| Last receipt exists (`crm_procurement_spo_allocations_last_receipt_list`, `sort=gr_date`) but nothing asks it from a multi-ask | `policy_rows.py:271-280`; `catalog.py:1376-1418` | yes |
| No tool answers "received quantity in a window" | MCP catalog has none; the SQL precedent is `procurement_service.py:1068-1080` (sum `picking_lines.quantity_picked`, `picking_type='goods_received'`, `picking_status='approved'`) | yes |
| Resolver adds every code STARTING with the typed code even after an exact hit | `entity_resolver.py:5163-5188` -> `_prefix_probe_product` `:1942-1966` (prefix, then substring) | yes |
| An exact-codes-only filter ALREADY exists, but only for dealer (availability) turns | `engine.py:519` `_exact_codes_only`, called at `engine.py:5245-5252` only when `profile.stock_availability_only` | yes (new find) |
| The gate trims a family to a pick only for `incoming` / `product_attachment` | `lanes/business/gate.py:165` (`REQUIRE_SPECIFIC_DOMAINS`) | yes |
| `purchase_cost` takes no date window (latest cost by design); last cost is per product PER LOCATION | `policy_rows.py:309-330`; `catalog.py:1461-1490` | yes |
| SO half of the order summary has no date filter | `order_service.py:382-405` (`so_outstanding_summary`: customer + product only) | yes |
| Header date line is built from the lane input the fetch went out with, not from whether the tool honoured it | `turn_runtime.py:4945`, `:4997-5007` | yes |
| `Customers` -> `客户`, so the count `customers: 33` reads `客户: 33` | `label_catalog.py:163`; `presenters.py:676-678` | yes |
| No reply size budget: compose joins every section; `whatsapp_parts` splits at 3,900 chars with no cap on parts | `turn/compose.py:512+`; `presenters.py:2863`, `:2986-3010` | yes |
| The parser prompt is DB-published (prompt registry); new prompt text ships as a migration publishing a new version | `chatbot_parser_prompt.py:1-40`; e.g. `alembic/versions/report_engine_0001_prompt.py` | yes |

## Rules (proposed)

R1. **The parser decides, code never guesses.** The parser emits one typed sub-ask per thing
asked, in the order asked. No regex, keyword or fuzzy match in code picks a measure.

R2. **One query per sub-ask.** Several sub-asks on one domain are several queries. Each returns
an AGGREGATE (a number, a date, a name, or a short ranked list), never the raw row dump.

R3. **Exact code only.** A typed code that exists exactly is that one product; siblings
(`-NEW`, `-GM`, ...) are added only when the parser marks the ask as a family ask (Q2). A code
with no exact hit keeps today's which-one picker. (Reuses `engine._exact_codes_only`, extended
from dealer turns to every turn that has a multi-ask, see Slice 1.)

R4. **Compact reply.** One `Label: value` line per sub-ask, ordered as asked, in the asker's
language (`label_catalog`), one fact per line, never ` · `. A ranked list is one line per
row under its label.

R5. **Window shown only where it applied.** A sub-ask that ran with the window says it in its
own label (`2026 入库数量`); one that does not take a window (last cost, last receipt) never
shows it. No turn-level date line on a compact reply.

R6. **Never stuck.** A sub-ask with no tool says `<label>: not available yet` on its own line;
one the contact may not see says `<label>: not shared with you`; a zero is `0`, not "No
incoming stock". The other lines still answer.

R7. **Budget.** A compact reply fits ONE WhatsApp message (Q1). A list over the cap prints the
top rows, then one offer line (`另有 N 位。回复 DETAIL 查看完整名单。`) (Q5). Never hundreds of rows.

R8. **Counts are labelled as counts**: `客户数` / `Bil. pelanggan` / `Customers (count)`.

## Sub-ask vocabulary (closed enum the parser emits)

| `measure` | Asked as | Query | Takes window | Grant |
| --- | --- | --- | --- | --- |
| `received_qty` | 入库了多少 / how many received | NEW `GET /procurement/goods-received/summary` (sum `quantity_picked`, approved `goods_received`, by `picking_date`) + MCP `crm_procurement_grn_summary` | yes | `purchase_orders.placed` (proposal) |
| `supplier` | 供应商是谁 / who is the supplier | Q3 | Q3 | `purchase_orders.supplier` |
| `last_cost` | 采购价 / purchase price / last cost | `crm_procurement_po_last_cost_list top_n=1`, newest line across locations | no | `purchase_orders.cost` |
| `last_receipt` | 最后一次进库 / last in | `crm_procurement_spo_allocations_last_receipt_list top_n=1 sort=gr_date` | no | none today |
| `sold_total` | 总共卖了多少 / total sold | `crm_report_ask` no `group_by`, `measure=qty`, `product_ids` (never `product_code`, which is a PREFIX filter) | yes | `sales_orders.sales_report` |
| `sold_by` + `group_by` | 每位业务员 / per salesman / per customer / per month | `crm_report_ask group_by=<axis> measure=qty top_n=<cap>` | yes | `sales_orders.sales_report` |
| existing domains | stock / incoming / PO / outstanding | today's tool for that domain, unchanged | as today | as today |

Anything the parser cannot place in this table is emitted as `measure: "other"` with the
asker's own words as `label`, and answers `<label>: not available yet`.

Wire shape (extends today's `asks[]` items; old `{domain, intent}` emissions still parse):

```json
{"domain": "goods_receive", "intent": "check_goods_receive",
 "measure": "received_qty", "group_by": null, "window": {"from": "2026-01-01", "to": "2026-12-31"},
 "basis": null, "family": false, "label": "入库数量"}
```

## The SRTWT7445 turn, expected reply

Shape below; the `<...>` values are dev-data numbers crew fills by running the SQL at the
bottom (the sandbox has no dev DB). Staff contact holding every grant, Chinese message, so a
Chinese reply.

```
SRTWT7445
2026 入库数量: <received_2026>
供应商: <supplier>
采购价: RM <cost_after_discount> / 件 (<po_number>, <po_date>)
最后一次进库: <gr_date> (<gr_qty>)
2026 总共卖出: <sold_2026>
2026 每位业务员卖出:
1. <agent_1>: <qty_1>
2. <agent_2>: <qty_2>
...
(up to 10 rows; over 10: 另有 <N-10> 位。回复 DETAIL 查看完整名单。)
```

Seven to seventeen lines, one WhatsApp message, no variant, no 2021 cost row, no customer block.

## More real multi-ask examples (from the committed test corpus, `tests/chatbot/*.json`)

1. `Last purchase cost and stock for srtwc286` (code with no exact hit: family) -> today's
   which-one picker first (R3), then
   `SRTWC286-SH` / `Last cost: RM ... / unit (PO..., dd/mm/yyyy)` / `Stock: <qty> (<n> locations)`.
2. `Srt38ss last in date and quantity` -> ONE sub-ask `last_receipt` with two facts:
   `SRT38SS` / `Last in: dd/mm/yyyy` / `Qty: <gr_qty>`.
3. `Incoming and stock for 7445` -> two existing domains; bare number ladder unchanged
   (`turn.task.code_or_number`), then one line per ask:
   `Stock: ...` / `Incoming: <qty>, ETA dd/mm/yyyy`.
4. `Incoming and PO srtkt1871ss,srtkt1866ss,srtkt1865ss` -> three products x two asks: a block
   per product in typed order, two lines each (6 fact lines + 3 headers).
5. `SRTWT7445 sold this year by customer, and last cost` (constructed from the owner's shape,
   not in corpus) -> `sold_by group_by=customer` list capped at 10 + `Last cost: ...`.

## Edge cases

| Case | Behaviour |
| --- | --- |
| One sub-ask only ("stock 7445") | Today's path, byte-identical. The compact path runs only for 2+ sub-asks. |
| Window named once, used by several sub-asks ("2026 ... 2026 ...") | Parser puts the window on each sub-ask that takes one; a sub-ask with no window of its own inherits the message's. |
| `sold_*` with no window anywhere in the message | One line `2026 总共卖出: which period?` style ask for that sub-ask only; the other lines still answer (R6). |
| `sold_*` with no basis | Q4 default; the label names it (`卖出 (已出货)`). |
| Contact lacks a grant (dealer asks cost / supplier) | That line: `采购价: not shared with you` (localised); others answer. |
| Zero received / never sold in window | `2026 入库数量: 0`, `2026 总共卖出: 0`. |
| No GRN ever (last receipt) | `最后一次进库: none on record`. |
| Two products, six asks | One block per product in typed order, same line order in each. Budget still applies to the whole reply. |
| `measure: other` ("margin?") | `<asker's words>: not available yet`. |
| Unsupported group_by (`sold_by group_by=transporter`) | `<label>: can't break sales down by transporter yet` (today's `_sales_analysis_axis_unsupported` wording). |
| Over budget after capping lists (many products) | Products past the budget get one line `... and N more products, ask one at a time`. |
| Language | zh -> zh labels, ms -> ms, en -> en, mixed -> the language `chat-language` already picks (`PLAN-chat-language-2oct`). |

## Slices

- **S1 (small, no parser change):** `客户数` label (R8); date line only for a tool that takes a
  window (R5, `turn_runtime._date_line` reads the domain's `takes_date_filter`); exact codes
  for staff on a multi-domain turn (R3). Ships alone, independently useful.
- **S2 (parser):** `asks[]` gains `measure / group_by / window / basis / family / label`;
  prompt addendum migration publishing a new version (unlabelled until crew's live checks pass).
- **S3 (planner + data):** one FetchSpec per sub-ask (`turn/apply._narrow_and_plan` iterates
  sub-asks, not domains); `measure -> (tool, args)` table; the GRN summary endpoint + MCP tool;
  `received_qty` / `last_receipt` / `sold_*` wired.
- **S4 (reply):** compact composer (one line per sub-ask, ordered as asked) + budget + DETAIL
  offer.

## Questions (recommendation first)

**Q1. Budget size.** (a) one WhatsApp message, at most 1,500 characters, any list capped at 10
rows; (b) one full message, 3,900 characters (`WHATSAPP_MESSAGE_MAX_CHARS`), lists capped at
25. Rec (a): six asks fit in under 600 characters, and 10 rows covers the salesman list.

**Q2. Variants.** (a) exact code only; the family only when the asker says so ("all
variants", "系列", "SRTWT7445 全部"), and then ONE summed line per sub-ask with `(8 款)`;
(b) family asks list one line per variant. Rec (a): one fact per line, and the 32-row cost
dump was the variant list.

**Q3. "Supplier" with a window.** (a) the supplier(s) on the approved GRNs in the window (who
actually delivered in 2026), falling back to the last PO's supplier when nothing came in; (b)
always the supplier of the last PO line. Rec (a): asked beside "received in 2026", it means
who delivered it.

**Q4. "Sold" basis when not said.** (a) delivered (quantity transferred to a DO), named in the
label; (b) ordered (SO lines); (c) ask every time. Rec (a): "卖了多少个" means what went out,
and asking every time costs a round trip per turn.

**Q5. DETAIL.** (a) the offer line, and a reply of DETAIL (any wording the parser reads as
"the full list") sends the full list as ONE Excel file, the way the sales analysis already
does; (b) DETAIL prints the full list as text, split across messages. Rec (a): one file is one
WABA message whatever the row count.

## Live checks for crew (local only)

Parser (live OpenAI, after S2's prompt version is published unlabelled):
1. The SRTWT7445 message above -> exactly six sub-asks in that order: `received_qty`,
   `supplier`, `last_cost`, `last_receipt`, `sold_total`, `sold_by(sales_agent)`; window
   2026-01-01..2026-12-31 on 1, 5, 6 (and 2 if Q3 a); `family: false`.
2. `Srt38ss last in date and quantity` -> ONE sub-ask `last_receipt`.
3. `Incoming and stock for 7445` -> two sub-asks on existing domains, no `measure`.
4. `stock 7445` -> `asks` null or one item (single-ask path untouched).
5. `SRTWT7445 系列 2026 卖了多少` -> `sold_total`, `family: true`.
6. `SRTWT7445 margin and last cost` -> `other` ("margin") + `last_cost`.

Dev-data values for the expected reply (shared dev DB, read-only):

```sql
-- received 2026 + supplier(s) on those GRNs
SELECT sum(pl.quantity_picked) AS received_2026, string_agg(DISTINCT ph.creditor_name, ', ') AS suppliers
FROM picking_lines pl JOIN picking_headers ph ON ph.id = pl.picking_header_id
JOIN products p ON p.id = pl.product_id
WHERE p.product_code = 'SRTWT7445' AND ph.picking_type = 'goods_received'
  AND ph.picking_status = 'approved' AND ph.picking_date BETWEEN '2026-01-01' AND '2026-12-31';

-- last receipt
SELECT ph.picking_date, sum(pl.quantity_picked) FROM picking_lines pl
JOIN picking_headers ph ON ph.id = pl.picking_header_id JOIN products p ON p.id = pl.product_id
WHERE p.product_code = 'SRTWT7445' AND ph.picking_type = 'goods_received' AND ph.picking_status = 'approved'
GROUP BY ph.picking_date ORDER BY ph.picking_date DESC LIMIT 1;

-- sold 2026 (delivered basis, SO date), total and per salesman
SELECT CASE WHEN grouping(sa.sales_agent) = 1 THEN 'TOTAL' ELSE coalesce(sa.sales_agent, '(none)') END AS agent, sum(least(l.qty_delivered, l.qty_ordered)) AS sold
FROM sales_order_lines l JOIN sales_orders so ON so.id = l.sales_order_id
JOIN products p ON p.id = l.product_id LEFT JOIN sales_agents sa ON sa.id = so.sales_agent_id
WHERE p.product_code = 'SRTWT7445'
  AND coalesce(so.requested_delivery_date, so.order_date) BETWEEN '2026-01-01' AND '2026-12-31'
GROUP BY ROLLUP (sa.sales_agent) ORDER BY sold DESC;
```

Last cost: run `crm_procurement_po_last_cost_list product_ids=<SRTWT7445 id> top_n=1` and take
the newest line. The sold query mirrors `crm_report_ask`'s delivered basis as its catalog entry
describes it; the tool's own answer is the one to trust if they differ.
