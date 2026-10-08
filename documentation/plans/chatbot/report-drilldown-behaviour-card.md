# REPORT-DRILLDOWN behaviour card, revision 3 (8 Oct 2026): APPROVED

Plan: `PLAN-report-drilldown-08oct.md`. Lane: REPORT-DRILLDOWN. PR #1531.

## Owner answers (8 Oct 2026), binding; they win over everything below

- **Q1: the customer card link.** Verbatim: "the customer of these agents should be the
  customer with sales agent id = these answered agent, and go find the sales order". So
  "customers of these agents" = customers whose `customers.sales_agent_id` is one of the agents
  turn 1 listed; then THEIR sales order lines, filtered by every carried turn-1 filter (product,
  period, basis, measure). The customer totals need NOT add up to turn 1's agent totals (prod:
  346 active customers have 2026 orders sold by another agent); no reconcile claim anywhere,
  and the header names the link used ("Customers assigned to: ...").
- **Q2 (directions): (a), generic.** Any axis to any axis, built on a DECLARED link per axis
  pair (table below).
- Crew-decided: ask "How many?" when no count is named; keep "(no agent)"; new dev prompt
  version, prod label unmoved, owner hand-merges.

## Declared links (Q2 (a))

| From (turn 1 rows) | To | Link |
| --- | --- | --- |
| sales agent | customer | the customer card: `customers.sales_agent_id` in the listed agents (owner Q1) |
| sales agent | any other axis | the agent on the sales order (turn 1's own lines) |
| customer, product, brand, category, location | any axis | that row's own filter on the sales order line |
| channel, month | - | not a drill source: a channel or a month is named in words ("dealer only", "in September"), which a refine already does |

"(no agent)" in turn 1 drills as "no agent": for agent to customer, customers with no agent on
the card; for any other axis, orders with no agent.

Revision 2 text below is kept for the record; where it differs (Q1 recommendation (a), the
"totals reconcile" lines in the examples), the answers above win.

## In one paragraph

Today, after "top 3 salesman for SRTWC8518-SH this year, delivered, by amount", a follow-up
"who are the customers for these sales agents" cannot work: the bot keeps only the ASK of turn
1 (its filters, period, basis), never WHICH agents it listed, and the parser has only two
readings for a follow-up, "refine" (same axis, so it re-runs the agent ranking) or "new ask"
(drops the product, the period and the agents). This lane adds a third reading, **drill down**:
the new axis (customers), every filter of turn 1 kept, plus "sales agent = exactly the agents
turn 1 listed". When the parser cannot tell whether the follow-up means those agents or
everyone, the bot asks.

## What happens today (reproduced 8 Oct 2026)

Run in a seeded sandbox through the real console turn, engine, lane and MCP presenter
(`tests/chatbot/test_report_ask_lane.py`'s harness); the parser is a double returning what the
current main prompt prescribes for each message (no LLM in the sandbox), the route body a
double. What matters is the route call each turn makes.

Turn 1 "top 3 salesman for SRTWT7408 this year, delivered, by amount" runs
`group_by=sales_agent, product_code=SRTWT7408, basis=delivered, measure=amount, top_n=3`.

**Reading A, new ask** (what the prompt says for a message naming its own axis,
`chatbot_parser_prompt.py:210-212`; "the ranked noun is never an entity", `:186-189`):
"who are the customers for these sales agents" ->

```
Which period? For example this month, September, 2026, or 1 to 15 Sep.
(this year) How many? For example top 5.
(10) By quantity or by amount?
(by amount) Delivered, ordered or outstanding?
(delivered) Top N customer by delivered sales, 2026-01-01 to 2026-10-08 ...
```

Final call: `group_by=customer, top_n=10, measure=amount, basis=delivered`, period this year,
**no product, no sales agents**: the top customers of the whole company for every product,
after four questions whose answers turn 1 already had. A confident wrong answer.

**Reading B, refine** (if the parser reads "top 10" as a count change, `:200-202`):
"top 10 customer for these sales agent" re-runs turn 1 with `top_n=10`: the agent list again,
not customers. A refine can never change the axis (`lanes/business/report_ask.py:571-615`).

Why, in code:
- The held frame is turn 1's route args only (`lanes/business/fetch.py:2662-2669`, stored on
  `Focus.sales_ranking_frame`, `turn/state.py:176`). The ranked rows are kept nowhere; the
  route's rows carry a name, no id (`schemas/report_ask.py:17-21`, `reports/ask.py:153-156`).
- The frame reaches the lane only on a refine verdict (`turn_runtime.py:1684-1691`).
- The data already supports the answer: the ask dataset filters by sales agent
  (`reports/datasets/sales_order_lines_ask.py:81-82`, param `:153`) and groups by customer
  (`:111`); the route takes `sales_agent_ids` with `group_by=customer`
  (`order_management/report_ask.py:56-64`).

## Rules

1. **The parser decides whether a follow-up is about turn 1's agents; when unsure, the bot
   asks** (owner, 8 Oct 2026: "top 10 customers can be not from these agents also, the parser
   need to determine, if unsure then we can clarify"). After a ranking, a ranking on a new axis
   gets one of three parser readings, never a keyword rule in code:
   - **scoped**: it refers to the rows the last ranking listed ("these agents", "their
     customers", "for them", "这些业务员的客户") -> drill down (rules 2 and 3);
   - **global**: it is its own ask ("top 10 customers overall", "top 10 customers for Cabana
     this year", a new subject) -> today's new ask, nothing of turn 1 carried;
   - **unsure**: it could be either (a bare "top 10 customers" right after the agent list) ->
     one short question, "Top 10 customers of the 3 sales agents above, or across all
     customers?". The reply's reading (parser again) then runs scoped or global.
2. **Everything of turn 1 carries on a scoped drill.** Product, brand, category, location,
   channel, period, basis and measure all carry unchanged, unless the drill-down message itself
   names a new one (then that one replaces it, as a refine does today). Nothing is dropped
   silently: the header lists every filter the answer used.
3. **"These agents" = exactly the agents turn 1 printed**, in its top N, not every agent that
   sold. Their names are listed in the header. Ties at the cut: the rows turn 1 printed are
   already a fixed order (amount, then qty, then name, `reports/ask.py:148-150`).
4. **No count named: ask "How many?"** (decided by crew from your 2 Oct rule Q5 for every
   ranking).
5. **Turn 1 listed "(no agent)"** (lines whose SO has no agent): it stays in the drill as
   "sales orders with no agent", so totals still add up, and the header says so (decided by
   crew: never a silent drop).
6. **Prompt:** a NEW dev prompt version (data-only migration, the `production` label unmoved,
   as `report_engine_0001_prompt` did). Your hand-edited prod prompt is not touched; you
   hand-merge the drill-down block into prod after your hand test (PROMPT-MERGE-1447 pattern).
   Decided by crew.
7. **Reply shape, no dot separators, one fact per line:**

   ```
   Top 5 customers by delivered sales
   Sales agents: Agent 01, Agent 02, Agent 03
   Product: SRTWC8518-SH
   Period: 1 Jan 2026 to 8 Oct 2026
   1. Customer A RM 206k, 572 pcs
   ...
   Total RM 683k, 1,974 pcs
   ```

## Examples (dev data, 1 Jan to 8 Oct 2026, delivered, masked)

Product SRTWC8518-SH: RM 1.45M delivered, 25 agents, 138 customers. Turn 1 "top 3 salesman for
SRTWC8518-SH this year, delivered, by amount":

```
Agent 01 RM 242k, 734 pcs (8 customers)
Agent 02 RM 235k, 665 pcs (24 customers)
Agent 03 RM 207k, 575 pcs (2 customers)
```

1. **Scoped, count named.** T2 "top 5 customers for these agents" -> runs at once, header
   Sales agents: Agent 01, Agent 02, Agent 03 / Product: SRTWC8518-SH / this year / delivered:
   Customer A (project, Agent 03) RM 206k; Customer B (Agent 01) RM 171k; Customer C
   (Agent 02) RM 35k; Customer D (Agent 02) RM 23k; Customer E (Agent 02) RM 22k.
   "and N more" (up to 34 customers), Total = the three agents' RM 683k.
2. **Scoped, no count.** T2 "who are the customers for these sales agents" -> "How many? For
   example top 5." -> "10" -> as example 1 with 10 rows.
3. **Scoped, one agent has few customers.** Agent 03 sold this product to 2 customers only;
   they both appear, nothing is padded.
4. **Scoped, a filter replaced.** T2 "top 5 customers for these agents, ordered, by quantity"
   -> agents, product and period carry; basis and measure are the message's.
5. **Global.** T2 "top 10 customers overall this year" or "top 10 customers for Cabana" -> the
   ordinary new ask; no agent filter, turn 1's product not carried; the header shows exactly
   what ran.
6. **Unsure.** T2 "top 10 customers" -> "Top 10 customers of the 3 sales agents above, or
   across all customers?"; "the agents above" -> example 1 with 10 rows; "all" -> example 5.
7. **No ranking held.** T2 "who are the customers for these agents" in a fresh conversation,
   or after a stock check (any non-ranking ask clears the frame, `engine.py:6453-6457`) ->
   "Which sales agents? For example top 5 sales agents for SRTWC8518-SH this year." Never a
   guess.

## Edge cases

- **"(no agent)" in turn 1:** none in this product's slice; over every product in the period,
  28 SO lines (17 products, RM 1.7k delivered) have no agent, so it can happen (rule 5).
- **An agent with no customers in the result:** cannot happen under Q1 (a) (an agent ranked in
  turn 1 has sales, so customers); under Q1 (b) one line "Agent 03: no customers on the
  customer card bought SRTWC8518-SH in this period".
- **Customer reassigned between agents:** see Q1; under (a) a customer shows under whoever sold
  each order in the period (possibly under two agents), under (b) only under the card's agent.
- **A filter that does not apply to customers:** none today; every catalogue filter (product,
  brand, category, sales agent, location, channel) is a property of the sales order line.
- **Dealer contact:** cannot rank agents (`reports/ask.py:46`), so never reaches a drill down.

## Questions for the owner

- **Q1. Which link decides "customers of these agents"?**
  (a) the agent on each sales order (`SalesOrder.sales_agent_id`, `models/order.py:629`): the
  same lines that made turn 1's figures, totals reconcile, a customer shows under whoever sold
  it in the period;
  (b) the agent on the customer card (`Customer.sales_agent_id`, `models/order.py:174`): the
  portfolio, but the figures no longer add up to turn 1's agent totals.
  Measured: in PROD (6 Oct snapshot, counts only) 6,702 of 7,005 customers have a card agent;
  all 2,368 customers with a 2026 sales order have one, and **346 of them (about 15%) have at
  least one 2026 order sold by a different agent**, so (a) and (b) give different answers for
  real. In DEV the card agent is set on only 28 of 5,332 customers (none of the 12 in the
  examples), so dev cannot show (b). No other customer to agent link exists (only free-text
  agent on old orders).
  **Recommendation: (a)**, because turn 1's agent totals are built on the SO agent; (b) can list
  a customer who never bought from these agents in the period, and miss one who did.
- **Q2. Which directions?**
  (a) any axis to any axis (agents -> customers, customers -> agents, brands -> agents) with the
  same mechanism;
  (b) agent -> customer only now, the rest when asked.
  **Recommendation: (b)**, the simplest thing that answers your example; the parser reading and
  the held frame are built axis-neutral, so (a) later is a small change.
