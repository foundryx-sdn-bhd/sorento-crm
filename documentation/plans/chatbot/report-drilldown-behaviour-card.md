# REPORT-DRILLDOWN behaviour card, revision 1 (8 Oct 2026): waiting on the owner

Owner clarification folded in (8 Oct): scoped vs global is the parser's call, unsure asks
(rule 1, examples 5 and 6). No question below was answered by it.

Plan: `PLAN-report-drilldown-08oct.md`. Lane: REPORT-DRILLDOWN. PR #1531.

## In one paragraph

Today, after "top 5 sales agents for CWC7606-SH this year, delivered, by amount", a follow-up
"who are the customers of these agents" cannot work: the bot keeps only the ASK of turn 1 (its
filters, period, basis), never WHICH agents it listed, and the parser has only two readings
for a follow-up, "refine" (same axis, so it re-runs the agent ranking) or "new ask" (drops the
product, the period and the agents). This lane adds a third reading, **drill down**: the new
axis (customers), every filter of turn 1 kept, plus "sales agent = exactly the agents turn 1
listed". The numbers then add up: each customer row is that customer's share of those agents'
sales of the same product, same period, same basis.

## What happens today (code, not opinion)

- The held frame is turn 1's route args only (`fetch.py:2662-2669`, stored on
  `Focus.sales_ranking_frame`, `turn/state.py:176`): group_by, filters, basis, measure, period,
  top_n. The ranked rows are not kept anywhere; the route's rows carry a name, no id
  (`schemas/report_ask.py:17-21`, `reports/ask.py:153-156`).
- A follow-up is either a refine or a new ask (prompt `chatbot_parser_prompt.py:195-215`).
  A refine re-runs the held args with only count, period, basis, measure, channel, brand or
  category changed; it can never change the axis (`report_ask.py:571-615`).
- "who are the customers of these agents" names its own axis (customer), so by the prompt it
  is a NEW ask (`chatbot_parser_prompt.py:210-212`): period never carried, product only if the
  message names it (`report_ask.py:441-453`), no agent filter at all ("the ranked noun is never
  an entity", `chatbot_parser_prompt.py:186-189`). Result: the bot asks "Which period?", then
  the basis, then lists the top customers of the WHOLE company for every product. A confident
  wrong answer to the question that was asked.
- The data already supports the answer: the ask dataset filters by `sales_agent`
  (`sales_order_lines_ask.py:240-241`) and groups by `customer` (`:270`), and the route takes
  `sales_agent_ids` with `group_by=customer` (`order_management/report_ask.py:56-64`).

## Rules (proposed)

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
2. **Everything of turn 1 carries.** Product, brand, category, location, channel, period,
   basis and measure all carry unchanged, unless the drill-down message itself names a new one
   (then that one replaces it, as a refine does today). Nothing is dropped silently: the header
   lists every filter the answer used.
3. **"These agents" = exactly the agents turn 1 printed**, in its top N, not every agent that
   sold. Their names are listed in the header.
4. **Which customer belongs to which agent:** see Q1. Recommendation: the agent on the sales
   order (the same lines that made turn 1's figures).
5. **How many customers:** see Q2. Recommendation: the count from the message; none named, ask
   "How many?" as every ranking does today.
6. **Reply shape, no dot separators, one fact per line:**

   ```
   Top 10 customers by delivered sales
   Sales agents: A01, A07, B12
   Product: CWC7606-SH
   Period: 1 Jan 2026 to 8 Oct 2026
   1. <customer> RM 12,400.00, 31 pcs
   ...
   Total RM 58,200.00, 140 pcs
   ```

   The total equals the sum of the three agents' rows in turn 1 when every customer is shown.

## Examples

Real agent codes, customers and products to come from the shared dev data (crew-data-request
posted on PR #1531; nothing real is committed, the repo is public). Shapes:

1. T1 "top 3 salesman for CWC7606-SH this year, delivered, by amount" -> Top 3 sales agents.
   T2 "who are the customers for these sales agents" -> asks "How many?" (Q2), "10" -> top 10
   customers, filters: Sales agents = the three, Product CWC7606-SH, this year, delivered,
   amount.
2. T2 "top 10 customers for these agents" -> runs at once, same filters.
3. T2 "top 5 customers for these agents by quantity, ordered" -> agents, product and period
   carry; measure and basis replaced by the message's.
4. T1 "top 5 sales agents for Sorento brand last month", T2 "这些业务员的客户" -> top customers of
   those 5 agents, brand Sorento, last month.
5. **Global** after the agent ranking: T2 "top 10 customers overall this year" or "top 10
   customers for Cabana" -> the ordinary new ask; no agent filter, turn 1's product not
   carried; the header shows exactly what ran.
6. **Unsure** after the agent ranking: T2 "top 10 customers" -> "Top 10 customers of the 3
   sales agents above, or across all customers?"; "the agents above" / "those" -> example 2;
   "all" / "overall" -> example 5.
7. T2 "who are the customers" with NO ranking before it (fresh conversation, or the last
   answer was a stock check) -> not a drill down; the ordinary ranking ask (period and basis
   are asked), header shows no agent filter.

## Edge cases

- **"These" with no ranking held** (the frame is cleared by any non-ranking ask,
  `engine.py:6453-6457`): asks "Which sales agents? For example top 5 sales agents for
  CWC7606-SH this year." Never guesses.
- **Turn 1 listed "(no agent)"** (lines whose SO has no agent, `delivery_order_lines.py:46`):
  that row is not an agent. It stays in as "sales orders with no agent" so the totals still
  add up, and the header says so (Q4).
- **An agent with no customers in the result** (only under Q1 (b)): one line "A07: no
  customers bought CWC7606-SH in this period".
- **Customer reassigned between agents** (`Customer.sales_agent_id` moved, audited at
  `models/order.py:119-121`): under Q1 (a) the customer appears under whoever SOLD each order
  in the period, possibly under two agents; under (b) only under today's agent.
- **Ties at turn 1's cut** (two agents equal at rank N): the drill uses the rows turn 1
  PRINTED, which is already a fixed order (amount, then qty, then name: `reports/ask.py:148-150`).
  The header lists them, so the user sees which.
- **A filter that does not apply to customers:** none today. Every catalogue filter
  (product, brand, category, sales agent, location, channel) is a property of the sales order
  line, so it narrows the customer ranking too.
- **Dealer contact:** cannot rank agents (`reports/ask.py:46`), so never reaches a drill down.
- **A drill down on a ranking that was not by agent** ("top 5 customers" then "their sales
  agents"): Q3.

## Questions (each with a recommendation)

- **Q1. Which link decides "customers of these agents"?**
  (a) the agent on each sales order (`SalesOrder.sales_agent_id`, `models/order.py:629`): same
  lines as turn 1, totals reconcile, a reassigned customer shows under whoever sold it;
  (b) the agent assigned on the customer card (`Customer.sales_agent_id`, `models/order.py:174`):
  today's portfolio, but the figures no longer add up to turn 1's agent totals.
  **Recommendation: (a)**, because turn 1's agent totals are built on the SO agent, so (b) can
  show a customer that never bought from these agents in the period.
- **Q2. No count named ("who are the customers for these agents")?**
  (a) ask "How many?" as every ranking does (your ruling 2 Oct, Q5);
  (b) list them all up to the ceiling (100).
  **Recommendation: (a)**, one rule for every ranking.
- **Q3. Drill in every direction, or only agent -> customer?**
  (a) any axis to any axis (agents -> customers, customers -> products, brands -> agents) using
  the same mechanism, ids kept for sales agent and customer rows;
  (b) agent -> customer only now, the rest when asked.
  **Recommendation: (b)**, the simplest thing that answers your example; the parser reading and
  the frame are built axis-neutral, so (a) later is a catalogue change.
- **Q4. Turn 1 listed "(no agent)" among the top N?**
  (a) keep it as "sales orders with no agent" in the drill, totals reconcile;
  (b) leave it out and say so in one line.
  **Recommendation: (a)**, never a silent drop and the sum still matches turn 1.
- **Q5. Parser prompt.** The prompt change ships as a NEW dev version (data-only migration, the
  `production` label unmoved, as `report_engine_0001_prompt` did). Your hand-edited prod prompt
  is not touched: you hand-merge the new drill-down block into prod after your hand test, as
  for #1447 (`PROMPT-MERGE-1447` pattern). OK?
  **Recommendation: yes.**
