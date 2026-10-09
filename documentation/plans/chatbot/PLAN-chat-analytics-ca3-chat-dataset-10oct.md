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

## Contract (binding for tester and coder)

**Parser output.** A chat analytics question is `message_type "business_query"`, `domain_hint
"chat_analytics"`, `intent_hint "chat_report"`, `domain_in_message true`, plus ONE new object key
(the `low_stock` object pattern, `head/parser.py` ~:465, so no shared enum changes):

```
"chat_report": {
  "group_by": "topic|subject|product|customer|outcome|failure_reason|week|month|day|language"|null,
  "measure":  "asks|failed|failure_rate|contacts"|null,
  "subject":  <a chatbot domain name, e.g. "inventory", "promotion">|null
} | null
```

Null on every other message. Period = the existing `date_filter_start` / `date_filter_end`;
count = `top_n`; direction = `rank_direction`; product and customer filters = `entities`
(hint `product` / `customer`), resolved by the existing resolvers. `measure` null reads as
`asks` (the head line names the unit); "fail" words set `failed`, "fail rate" `failure_rate`,
"how many dealers / contacts / people" `contacts`.

**Lane** (`lanes/business/chat_report.py`, ask name `chat_report`, tool `crm_report_ask`).
Gate: `DOMAIN_GRANT_REQUIRED["chat_analytics"] = "chatbot.analytics"`. Required: period always;
`top_n` only when `group_by` is set and not week / month / day (a trend lists every bucket).
Both missing -> ONE reply with both questions, one per line:
`Which period? For example this week, September, or 1 to 15 Sep.` then
`How many? For example top 5.` Basis: `turns` for failed / failure_rate, else `asks`.

**Route** `GET /api/v1/order-management/report-ask` gains `dataset` (`sales` default | `chat`).
Chat params: `basis` asks|turns, `measure` asks|failed|failure_rate|contacts, `group_by` (the
ten words), `top_n`, `sort`, `date_from`, `date_to`, `subjects` (domain codes), `product_ids`,
`customer_ids`, `contact_id`, `space_id`. Sales-only params with `dataset=chat` -> 422
`unknown_param`. Chat needs reveal `chatbot.analytics` (403 `chat_analytics_not_enabled`); the
sales reveal is not checked on chat and not enough for it. Response (chat):

```
{status, message, dataset: "chat", basis, measure, unit ("asks"|"turns"|"contacts"|"%"),
 group_by, group_label, date_from, date_to (clipped to >= 2026-09-05),
 rows: [{rank, name, value, numerator?, denominator?}], more, total_count,
 total: {value, numerator?, denominator?}, filters: [{key,label,values}],
 coverage: {percent, taxonomy_version, line}}
```

`numerator`/`denominator` only for failure_rate (failed turns / non-noise turns). Contacts
total = distinct over the whole set.

**Reply** (MCP presenter for `dataset == "chat"`), one fact per line, no " · ":

```
Topics labelled: 17% of asks (taxonomy v1). From bot traces, 5 Sep 2026 on.
Top 5 topics by asks
Period: 1 Sep 2026 to 30 Sep 2026
1. Availability: 124 asks
2. Product ETA: 85 asks
and 12 more
Total: 399 asks
```

Coverage line LAST when percent >= 80, FIRST when < 80 (the example is 17%, so first). Failure rate rows: `1. 2026-09-14: 1.2% (14 of 1,215 turns)`.
No asks: `No asks in that period. Chat data starts 5 Sep 2026.`

## Revision 10 Oct 2026: focused extractor (pending crew ruling, recommended)

Measured live (gpt-5.4-mini, dev DB production prompt + domain row, read-only): the parser
addendum alone reads golden 16/20 stable over 3 runs and a held-out set of 8 new messages 4/8,
after five prompt rounds. A focused second call with a 2.1k-char system prompt and a strict
schema read golden 19/20 and held-out 8/8 in a scratch run. So:

- **Parser** only ROUTES: `domain_hint "chat_analytics"`, `intent_hint "chat_report"`, plus
  the `chat_report` object kept as a light "this message is a new chat report" signal for the
  open-question seam (`names_its_own_ask`). Its addendum shrinks to routing (target <= 250 est.
  tokens). Period / count on an ANSWERING turn still come from the parser's own date and top_n
  keys, as today.
- **Extractor** `app/services/chatbot/analytics/chat_report_extractor.py`
  `extract_chat_report(db, message_text, *, today) -> dict | None` returning
  `{group_by, measure, subject, top_n, date_from, date_to}` (strict JSON schema, enums as the
  contract, subject = a chatbot domain name or null). Prompt key `chat_report_extractor` in
  `ai_prompt_registry.PROMPT_KEYS` with a code fallback (pattern: `ideation_extractor.py`,
  `ideate_extractor` key), provider through `llm_provider.get_provider` + `resolve_api_key`
  and the per-key model override (`agent_model`). Called by the lane on a FRESH chat report
  only (never on the answer to "Which period?" / "How many?"); its values replace the parser's
  chat_report / top_n / dates for that ask. Provider error, timeout or invalid output -> one
  clear line ("I couldn't read that report request. Try: top 5 questions this week."), turn
  closes, nothing runs.
- Golden live check runs the whole read: parser route + extractor fields, 3 runs each, plus
  the held-out set.
