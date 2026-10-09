# CARD - per-ask planning, exact codes, compact replies (MULTI-ASK-PLAN)

Status: card SETTLED by the owner (9 Oct 2026, two rounds). Built: S1 (d9f8321b), red-first
tests (f6fc7adf), S3 (b482cfdb), S2 (3f9d878c), S4 (b6daddb0); in review. Track: L
(chatbot core: parser prompt migration, per-ask runner in the engine, one new read endpoint +
MCP tool). Lane PR #1542 carries `crew-lane: MULTI-ASK-PLAN`. Measured on origin/main
bf07eff8b.

Live parser checks and dev-data values cannot run in the cloud sandbox (no OpenAI, no dev
DB): both are listed at the bottom for crew to run locally.

## Owner answers (9 Oct 2026, two rounds, relayed by crew)

| Q | Ruling | What it means in the build |
| --- | --- | --- |
| Q1 | No fixed template, NO size ceiling for now | ONE line per sub-ask the user ACTUALLY asked, in their order: "price and last receipt date" is two lines. Nothing unasked is printed (no stock summary under a cost ask, no header block). No list cap and no DETAIL offer: a "per salesman" list prints every salesman, one per line. |
| Q2 | Clarify, parser-driven | The parser says per product code whether the asker meant the exact code, the family, or did not say (`family: true / false / null`). `null` and the code has variants -> ONE short question naming the variant count, and nothing else runs that turn: `SRTWT7445 有 8 个款式。只要 SRTWT7445，还是全部 8 款？` / `SRTWT7445 has 8 variants. Just SRTWT7445, or all 8?`. The reply to it is parsed as `family`, and the original sub-asks run. No code rule picks exact or family. |
| Q3 | (b) current behaviour, labelled | Supplier = the supplier of the LAST PO line for the product (newest PO date across every location), the same row `crm_procurement_po_last_cost_list` answers today; the line names that PO: `供应商: ABC SDN BHD (PO-2026-0412, 03/06/2026)`. No date window. |
| Q4 | Clarify | `sold_*` with no basis the parser can read -> ONE short question `卖出按已出货还是按下单算？` / `Sold as delivered or as ordered?`. Only the sold lines wait on it: every other asked line answers in the same reply and the question is the last line. |
| Q5 | (b), text, no Excel | The reply goes as TEXT packed into the fewest WhatsApp messages: lines fill each message up to the per-message limit (`WHATSAPP_MESSAGE_MAX_CHARS` 3,900), never a short part when the next line would have fitted, never one message per section. |

Question order when both are needed: the variant question (Q2) goes ALONE, since every line
depends on which products are asked; the basis question (Q4) rides at the end of the
answered lines, after Q2 is settled.

## The journey (owner, 9 Oct 2026, PROD turn 8 Oct 19:50)

> SRTWT7445,2026 年 入库了多少数量 , 供应商是谁 ， 采购价是多少钱。 最后一次进库是什么时候 ，2026 总共卖了多少个，2026年每位业务员共卖了多少个

Six asks about ONE code: (1) received qty in 2026, (2) supplier, (3) purchase price,
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

## Rules (settled)

R1. **The parser decides, code never guesses.** The parser emits one typed sub-ask per thing
asked, in the order asked, plus `family` per product code. No regex, keyword or fuzzy match in
code picks a measure, a family or a basis.

R2. **One query per sub-ask.** Several sub-asks on one domain are several queries. Each
returns an aggregate (a number, a date, a name, or a ranked list), never a row dump.

R3. **Exact or family is the asker's** (Q2). `family: false` is the exact code alone (the
resolver's prefix siblings dropped, through the existing `engine._exact_codes_only`);
`family: true` is the family, ONE summed line per sub-ask marked with the variant count;
`family: null` with variants present asks back once. A code with no exact hit keeps today's
which-one picker.

R4. **One line per asked sub-ask, no template** (Q1). `Label: value`, ordered as asked, in
the asker's language (`label_catalog`), one fact per line, never ` · `. A ranked list is one
line per row under its label, every row (no cap). Nothing unasked is printed.

R5. **Window shown only where it applied.** A sub-ask that ran with the window carries it in
its own label (`2026 入库数量`); last cost, supplier and last receipt never show it. No
turn-level date line on a compact reply. (The turn-level date line of an ordinary reply is
now also printed only under a tool that takes a window: S1, d9f8321b.)

R6. **Never stuck.** A sub-ask with no query says `<label>: not available yet` on its own
line; one the contact may not see says `<label>: not shared with you`; a zero is `0`, never
"No incoming stock". The other lines still answer.

R7. **Fewest messages** (Q5). The whole reply is one text, split only by
`presenters.whatsapp_parts`, which packs lines greedily up to 3,900 characters a message.

R8. **Counts are labelled as counts**: `Customer count` / `客户数` / `Bil. pelanggan` (S1,
d9f8321b; a new catalog key, so the stored `translation_memory` row for the old `Customers`
-> `客户` cannot win).

## Scope

- The per-ask path runs when the parser emits TWO OR MORE sub-asks and at least one is a
  measure only it answers (received, last receipt, supplier, last cost, sold, other). A
  message of stock / incoming asks only ("Incoming and stock for 7445") keeps today's
  multi-domain answer, which already handles it. A one-ask message keeps
  today's reply byte for byte (crew reading of Q1: "just stock -> 1 line" describes a stock
  ask inside a multi-ask; say so if every single ask should go compact too).
- Staff contacts. A dealer (availability-only) contact keeps today's path: its replies have
  their own owner-approved shapes (AVAIL-MODE-REPLIES) and it may not see most measures.

## Sub-ask vocabulary (closed enum the parser emits)

| `measure` | Asked as | Query (one call) | Window | Grant (line says "not shared" without it) | Line (en / zh) |
| --- | --- | --- | --- | --- | --- |
| `stock` | 库存 / stock | `crm_inventory_stock_balance_list` | no | `inventory.sellable` domain reveal, as today | `Stock: 120` / `库存: 120` |
| `incoming` | 到货 / incoming | `crm_incoming_stock_by_product` | yes | none | `Incoming: 300, ETA 15/10/2026` / `到货: 300，ETA 15/10/2026` |
| `received_qty` | 入库了多少 / received | NEW `GET /api/v1/procurement/grn/summary` via NEW `crm_procurement_grn_summary`: sum `picking_lines.quantity_picked`, `picking_type='goods_received'`, `picking_status='approved'`, `picking_date` in window (precedent `procurement_service.py:1068-1080`) | yes | `purchase_orders.placed` | `Received 2026: 1,200` / `2026 入库数量: 1,200` |
| `last_receipt` | 最后一次进库 / last in | the same new endpoint's `last_receipt` {date, qty}: the newest approved GRN for the product, all time | no | `purchase_orders.placed` | `Last received: 18/09/2026 (300)` / `最后一次进库: 18/09/2026（300）` |
| `supplier` | 供应商 / supplier | `crm_procurement_po_last_cost_list top_n=1`, the newest row across locations (Q3) | no | `purchase_orders.supplier` | `Supplier: ABC SDN BHD (PO-2026-0412, 03/06/2026)` |
| `last_cost` | 采购价 / purchase price | the same row as `supplier` (one call serves both) | no | `purchase_orders.cost` | `Last cost: RM 45.00 / unit (PO-2026-0412, 03/06/2026)` / `采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）` |
| `sold_total` | 总共卖了多少 / total sold | `crm_report_ask` no `group_by`, `measure=qty`, `basis`, `product_ids` (never `product_code`, a PREFIX filter) | yes, required | `sales_orders.sales_report` | `Sold 2026 (delivered): 860` / `2026 总共卖出（已出货）: 860` |
| `sold_by` | 每位业务员 / per salesman / per customer | `crm_report_ask group_by=<axis> measure=qty top_n=1000` | yes, required | `sales_orders.sales_report` | header line, then `1. Ali: 300` per row |
| `other` | anything else | none | - | - | `<asker's words>: not available yet` |

`supplier` and `last_cost` in one message make ONE call. `received_qty` and `last_receipt` in
one message make ONE call.

Known data gap (crew to measure): a receipt booked through the ESB-stated path has a
`gr_quantity` and no GRN (`catalog.py:1386-1395`), so it is not counted by `received_qty` or
`last_receipt`. Measure on dev how much of SRTWT7445's 2026 receipts that is.

## Wire shape (parser output)

```json
{
  "entities": [{"raw": "SRTWT7445", "hint": "product", "canonical_code": "SRTWT7445"}],
  "family": null,
  "sub_asks": [
    {"measure": "received_qty", "group_by": null, "date_from": "2026-01-01", "date_to": "2026-12-31", "basis": null, "label": "入库了多少数量"},
    {"measure": "supplier", "group_by": null, "date_from": null, "date_to": null, "basis": null, "label": "供应商是谁"},
    {"measure": "last_cost", "...": "..."},
    {"measure": "last_receipt", "...": "..."},
    {"measure": "sold_total", "date_from": "2026-01-01", "date_to": "2026-12-31", "basis": null, "label": "总共卖了多少个"},
    {"measure": "sold_by", "group_by": "sales_agent", "date_from": "2026-01-01", "date_to": "2026-12-31", "basis": null, "label": "每位业务员共卖了多少个"}
  ]
}
```

`sub_asks` and `family` are NEW top-level keys, null for an ordinary message; today's `asks[]`
(second domain onward) is untouched, so every existing reader keeps working. The reply to the
variant or basis question is NOT a new key: both questions are numbered options, so the
parser's existing `open_question_answer` pick reads the reply (1 = just the code / delivered,
2 = all variants / ordered). Both are exempt from the required-key check, so a captured emission that predates
them still post-processes (`output_exchange`, the `group_by` precedent at `head/parser.py:212`).

## The SRTWT7445 turn, expected replies

Staff contact holding every grant, Chinese message. `<...>` values come from dev data (SQL at
the bottom).

Turn 1 (the owner's message; SRTWT7445 has variants, the message did not say which):

```
SRTWT7445 有 8 个款式。只要 SRTWT7445，还是全部 8 款？
```

Turn 2 (`只要这个` -> `family: false`), the six asks run; basis not said:

```
SRTWT7445
2026 入库数量: <received_2026>
供应商: <supplier> (<po_number>, <po_date>)
采购价: RM <cost_after_discount> / 件（<po_number>，<po_date>）
最后一次进库: <gr_date>（<gr_qty>）
卖出按已出货还是按下单算？
```

Turn 3 (`已出货` -> `basis: delivered`):

```
SRTWT7445
2026 总共卖出（已出货）: <sold_2026>
2026 每位业务员卖出（已出货）:
1. <agent_1>: <qty_1>
2. <agent_2>: <qty_2>
...every salesman
```

A message that says `只要 SRTWT7445` and `已出货` up front gets everything in turn 1, one
message, one line per ask.

## More real multi-ask examples (from the committed test corpus, `tests/chatbot/*.json`)

1. `Last purchase cost and stock for srtwc286` (no exact hit) -> today's which-one picker
   first, then `SRTWC286-SH` / `Last cost: RM ... / unit (PO..., dd/mm/yyyy)` / `Stock: <qty>`.
2. `Srt38ss last in date and quantity` -> ONE sub-ask (`last_receipt`), so today's path,
   unchanged.
3. `Incoming and stock for 7445` -> the bare number ladder is unchanged
   (`turn.task.code_or_number`); then `Incoming: <qty>, ETA dd/mm/yyyy` / `Stock: <qty>`.
4. `Incoming and PO srtkt1871ss,srtkt1866ss,srtkt1865ss` -> `po_placed` is not in the
   vocabulary yet: per product `Incoming: ...` / `PO: not available yet`. (Adding `po_placed`
   is one row and one formatter; left out until asked.)
5. `SRTWT7445 2026 sold per customer and last cost` (shape from the owner's turn) ->
   `sold_by group_by=customer` (every customer, one line each) + `Last cost: ...`.

## Edge cases

| Case | Behaviour |
| --- | --- |
| One sub-ask only ("stock 7445") | Today's path, byte-identical. |
| Window named once, used by several sub-asks ("2026 ... 2026 ...") | The parser puts it on each sub-ask that takes one. |
| `sold_*` with no window anywhere | That line asks `<label>: which period?`; the others answer. |
| Contact lacks a grant (cost, supplier, sales) | That line `<label>: not shared with you` (localised); others answer. |
| Zero received / never sold in window | `2026 入库数量: 0`, `2026 总共卖出（已出货）: 0`. |
| No GRN ever | `最后一次进库: 没有记录` / `Last received: none on record`. |
| No PO line ever | `供应商: 没有记录`, `采购价: 没有记录`. |
| Two products, six asks | One block per product in typed order, the same line order in each. |
| `family: true` | The code line reads `SRTWT7445（全部 8 款）`; one summed line per sub-ask; supplier / last cost / last receipt answer the newest row across the family. |
| Two typed codes, both with variants, `family` null | ONE variant question, about the first; the answer applies to every typed code. |
| A typed code with no exact hit | Today's path (which-one picker); the runner steps aside. |
| A reply to the variant / basis question that is not a pick | Today's path reads it as a new message. |
| `measure: other` ("margin?") | `<asker's words>: not available yet`. |
| `sold_by` axis the report does not have (`transporter`) | `<label>: not available yet`. |
| A query fails (HTTP error) | That line `<label>: couldn't check right now`; the others answer. |
| Language | The language `chat-language` already picks for the turn (`PLAN-chat-language-2oct`). |

## Build (slices, each red-first)

- **S1 (done, d9f8321b)**: R8 count label; R5 for the ordinary reply's date line.
- **S2 parser**: `sub_asks` + `entities[].family` in the response schema
  (`head/parser.py`), the prompt addendum (a migration publishing a new UNLABELLED version
  under `chatbot_semantic_parser`, the `report_engine_0001_prompt` precedent), carried
  through the verdict.
- **S3 data**: `GET /api/v1/procurement/grn/summary` (product_ids, date_from, date_to ->
  per product `{received_qty, grn_count, last_receipt_date, last_receipt_qty}`) + MCP
  `crm_procurement_grn_summary` + the chatbot read-only tool allow-list.
- **S4 runner + reply**: `app/services/chatbot/turn/sub_asks.py`: engine hook after the
  resolver (product candidates known) and before `turn_apply`'s fetch, when the verdict
  carries 2+ sub-asks and the contact is staff. Q2 ask-back and Q4 basis question as open
  questions (`turn/pending.py`), the answering turn re-runs the stored sub-asks. Lines built
  from the raw tool JSON (no `view=render`), grants applied from `access.attributes`, the
  same keys the renderer's `restricted_fields` uses.

## Acceptance (red-first test list)

1. The SRTWT7445 verdict (six sub-asks, `family: false`, basis delivered) -> one message,
   seven fact lines + the code line, in asked order; exactly the tool calls of the vocabulary
   table (`crm_procurement_grn_summary` once, `crm_procurement_po_last_cost_list` once,
   `crm_report_ask` twice); every call's `product_ids` is SRTWT7445 alone.
2. Same verdict, `family: null`, SRTWT7445 has variants -> the variant question only, no
   tool call; the next turn with `family: false` answers the six asks.
3. Same verdict, basis null -> the four non-sold lines + the basis question as the last line,
   no `crm_report_ask` call; the next turn with `basis: delivered` answers the two sold lines.
4. The date window shows on received / sold lines only.
5. A contact without `purchase_orders.cost` -> `采购价: not shared with you`, the supplier line
   still answers with `purchase_orders.supplier`.
6. `measure: other` -> `<label>: not available yet`, other lines answer.
7. A one-sub-ask verdict -> today's reply (no new tool, no compact line).
8. `sold_by` with 40 salesmen -> 40 rows, packed into the fewest WhatsApp parts (each part but
   the last cannot take the next line).
9. The GRN summary route: approved `goods_received` only, window on `picking_date`, a draft or
   a non-GRN picking excluded, last receipt all-time.

## Live checks for crew (local only, after S2's prompt version is published unlabelled)

1. The SRTWT7445 message above -> six sub-asks in that order: `received_qty`, `supplier`,
   `last_cost`, `last_receipt`, `sold_total`, `sold_by(sales_agent)`; window
   2026-01-01..2026-12-31 on 1, 5, 6 only; `family: null`; `basis: null`.
2. `只要这个` / `just this one`, replying to the variant question -> `family: false`;
   `全部` / `all of them` -> `family: true`.
3. `已出货` / `delivered`, replying to the basis question -> `basis: delivered`; `下单` /
   `ordered` -> `basis: ordered`.
4. `Srt38ss last in date and quantity` -> ONE sub-ask (`last_receipt`) or `sub_asks: null`.
5. `Incoming and stock for 7445` -> two sub-asks, `incoming` and `stock`.
6. `stock 7445` -> `sub_asks: null` (single-ask path untouched).
7. `SRTWT7445 系列 2026 卖了多少，按已出货` -> `sold_total`, `family: true`, `basis: delivered`.
8. `SRTWT7445 margin and last cost` -> `other` ("margin") + `last_cost`.

## Dev-data values for the expected reply (shared dev DB, read-only)

```sql
-- received 2026
SELECT sum(pl.quantity_picked) AS received_2026, count(DISTINCT ph.id) AS grns
FROM picking_lines pl JOIN picking_headers ph ON ph.id = pl.picking_header_id
JOIN products p ON p.id = pl.product_id
WHERE p.product_code = 'SRTWT7445' AND ph.picking_type = 'goods_received'
  AND ph.picking_status = 'approved' AND ph.picking_date BETWEEN '2026-01-01' AND '2026-12-31';

-- last receipt
SELECT ph.picking_date, sum(pl.quantity_picked) FROM picking_lines pl
JOIN picking_headers ph ON ph.id = pl.picking_header_id JOIN products p ON p.id = pl.product_id
WHERE p.product_code = 'SRTWT7445' AND ph.picking_type = 'goods_received' AND ph.picking_status = 'approved'
GROUP BY ph.picking_date ORDER BY ph.picking_date DESC LIMIT 1;

-- sold 2026 (delivered basis, required date else SO date), total and per salesman
SELECT CASE WHEN grouping(sa.sales_agent) = 1 THEN 'TOTAL' ELSE coalesce(sa.sales_agent, '(none)') END AS agent,
       sum(least(l.qty_delivered, l.qty_ordered)) AS sold
FROM sales_order_lines l JOIN sales_orders so ON so.id = l.sales_order_id
JOIN products p ON p.id = l.product_id LEFT JOIN sales_agents sa ON sa.id = so.sales_agent_id
WHERE p.product_code = 'SRTWT7445'
  AND coalesce(so.requested_delivery_date, so.order_date) BETWEEN '2026-01-01' AND '2026-12-31'
GROUP BY ROLLUP (sa.sales_agent) ORDER BY sold DESC;
```

Supplier and last cost: `crm_procurement_po_last_cost_list product_ids=<SRTWT7445 id> top_n=1`,
the row with the newest `po_date`. The sold query mirrors `crm_report_ask`'s delivered basis
as its catalog entry describes it; the tool's own answer is the one to trust if they differ.
