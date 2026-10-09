# PLAN: CA-3 chat dataset in the bot (10 Oct 2026)

Status: BUILDING (Phase 2 red tests). Behaviour card ruled 10 Oct (Q1 b, Q2 b, Q3 a, Q4 a, Q5 a). Track: full pipeline,
size M, LEAD pattern (tester -> coder -> reviewer + security-reviewer: the lane adds a grant).
Base: CA-2 #1551 (open) on main 096e4665d; rebase onto main once #1551 merges.

UAC: `ca3-chat-dataset-acceptance-criteria.md`. Design: `DESIGN-chat-analytics-09oct.md`
(PR #1545) sections 4.2 to 4.6, 5, phase row CA-3.

## Scope

In: asks + turns bases over `chatbot.chat_ask_facts`; count-distinct and ratio measures; route,
MCP tool, presenter; parser addendum; `chatbot.analytics` reveal grant; golden set.
Out: episodes basis (`chat_episode_facts` has 0 rows on the shared dev DB), chat x sales join,
two-group pivots (CA-5), backfill (CA-4), any new screen.

## Slices (one branch, commits folded)

1. **Engine measures** (`services/reports/registry.py` Column, `engine.py` :627-634 and
   :664-700). `Column.agg: "sum" | "count_distinct"` (default sum) and
   `Column.ratio_of: (num_key, den_key)`. Count-distinct cells come from `count(distinct expr)`;
   their row/col/grand totals are a separate grouped query, never a sum of cells. Ratio is
   computed after aggregation from the two summed measures, in every cell and total. Existing
   sum-only datasets unchanged (regression tests stay green).
2. **Chat catalogue** (`services/reports/ask.py`). `CATALOGUE[dataset][basis]`; sales keeps its
   three bases under `sales`. `chat` bases `asks` (every ask) and `turns` (ask_seq 1, via
   `chat_asks.turn_only`). Dimensions: topic, subject, product (via `chat_ask_items` role
   named), customer, outcome, failure_reason, week, month, day, language. Measures asks,
   failed, failure_rate (ratio failed / turns), contacts (count-distinct
   `contact_respond_id`). Defaults outcomes minus noise, contact types minus office (reuse
   `chat_asks.DEFAULT_OUTCOMES` / `DEFAULT_CONTACT_TYPES`). Dealer keys topic, subject,
   product, month. Coverage line from `chat_asks.topics_note`.
3. **Route** (`api/v1/order_management/report_ask.py`). `dataset` param (default `sales`);
   chat params `topic_codes`, `outcomes`, `product_ids`, `customer_ids`. Chat requires reveal
   `chatbot.analytics` (403 `chat_analytics_not_enabled`), dealer forced to own customers,
   salesman (contact on `sales_agents.contact_id`) forced to customers whose card agent is
   theirs. Same rate limit, same 422 on unknown params.
4. **Grant** `chatbot.analytics` in `FIELD_REVEAL_KEYS` (`contact_field_reveal_service.py:43`)
   and in the MCP catalog `restricted_fields` (pinned by
   `tests/chatbot/test_field_reveal_keys_pinned_to_catalog.py`). New domain `chat_analytics`
   (`chatbot_domains` row, `reveal_key = chatbot.analytics`, tool `crm_report_ask`) and
   `DOMAIN_GRANT_REQUIRED["chat_analytics"]` (`lanes/business/answer.py:1199`) so the lane gate
   at `lanes/business/__init__.py:1685` refuses before any call.
5. **Lane** `lanes/business/chat_report.py` beside `report_ask.py`: settle (no period -> ask "Which
   period?", ranking with no count -> ask "How many?", both missing -> both in ONE reply,
   through `required_fields` like `report_ask.py`; words -> ids through the existing resolvers, never string
   matching), route args, error lines. Dispatch in `lanes/business/__init__.py` next to the
   sales_ranking branch (:2098).
6. **MCP** (`sorento_crm_mcp/catalog.py:814`, `presenters.py:1896`, :3270): `dataset` param;
   chat presenter printing UAC-11/12 shape.
7. **Parser addendum** (`app/services/chatbot_parser_prompt.py`): domain_hint
   `chat_analytics`, intent `chat_report`, keys `chat_group_by`, `chat_measure`, filters; zh /
   en / ms / mixed examples, synthetic. Published by a prompt migration (pattern
   `report_drill_0001_prompt.py`) and by the crew migration file
   `crew/state/migrations/CA-3-CHAT-DATASET.sql` (insert-if-absent, check rows > 0).
8. **Golden set** `tests/chatbot/golden/chat_report_golden.json` (>= 10 synthetic messages,
   expected route) + pytest asserting parser route offline (recorded) and numbers over a
   synthetic fixture; live run 3x each via the parity script before hand test.

## Tests (tester writes red first, from the UAC)

- engine: count-distinct cells/totals, ratio cells/totals, sum datasets unchanged.
- ask/route: chat ranking by topic/product/week; contacts total distinct; failure rate;
  office/noise excluded; dealer/salesman scoping; grant 403; unknown dim 422; no-data line;
  sales call without `dataset` unchanged.
- lane: grant refusal before route; defaults Q1/Q2; catalogue line; error lines.
- presenter: line shape, no " · ", coverage first under 80%.
- golden: parser route per message.

## Rulings (10 Oct 2026, crew a66005801)

Q1 (b) ask the period; Q2 (b) ask how many; both in one reply when both missing. Q3 (a) office
excluded. Q4 (a) nobody granted by default. Q5 (a) unlinked staff with grant sees all.
