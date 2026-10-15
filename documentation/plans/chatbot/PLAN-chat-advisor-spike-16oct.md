# PLAN: chat advisor spike ("Claude Code for Sorento"), lane CHAT-ADVISOR-SPIKE

Status: Plan. Track: spike (time-box 3 days, done by ~19 Oct 2026). NOT a product PR: deliverables
are a design page and a measured prototype on a draft PR. Owner approved 15 Oct ~22:15. LLM budget
about $30 total, spend reported.

## Owner's goal (15 Oct)

"I want to build a Claude Code for Sorento. It can counter propose, suggest, think - e.g. think about
alternative products, offer the promotion, engage with the user. Can we even do that with the current
architecture?" Reference: 7-layer agent architecture (1 channels, 2 perception, 3 orchestration and
reasoning, 4 memory, 5 knowledge/RAG, 6 tools/MCP, 7 guardrails + observability + response).

Owner question 15 Oct ~22:20: "as Claude Code, do you extract entities first before the LLM decides?"
Answer: no. Claude Code feeds the raw conversation plus tool descriptions to one model that
understands, decides and calls tools in a loop, looking facts up through tools rather than carrying
knowledge in the prompt. Hence variant C2 below.

## Variants compared

| Id | Shape |
| -- | ----- |
| A  | Today v1: LLM parser (layer 2) + code routing + templated replies |
| B  | PV2 as is |
| C  | HYBRID: thin parser (understanding only) -> deterministic fast path for simple asks (stock, price, ETA, order status); open-ended / selling turns -> layer-3 ADVISOR (reasoning LLM, bounded loop, 3-6 tools chosen from the parser's intent) + one structured conversation state (layer 4) + fact guard (layer 7) |
| C2 | Raw-conversation advisor (Claude Code style): no parser pre-extraction; the advisor reads the raw transcript, same tool subset, same fact guard |
| D  | Full agent: all tools, model chooses, no parser |

Design page covers A-D + C2. The spike measures A (v1) vs C vs C2 on two models, plus a C2-all-tools
run (D-shaped) if budget allows, since the owner's n8n lesson is "more tools = worse tool choice".

## Deliverable 1: design page (by 17 Oct evening)

`documentation/plans/chatbot/ARCH-advisor-16oct.html`, self-contained, phone-friendly, Lavish.
- Map v1 and PV2 onto the 7 layers with file:line.
- Per option: owner's examples, n8n failure modes (wrong tool, worse with more tools, slow), cost/turn,
  p50/p95, risk, migration path from today.
- 4-6 mock WhatsApp conversations built on REAL dev cases (masked names): alternative when out of
  stock, promotion offer, compatible accessory (seat cover / mirror / shelf), counter-proposal on qty or
  ETA, project from a chat or photo.
- Filed as `crew report --ask` with the repo path.

## Deliverable 2: spike (by ~19 Oct)

1. Scenarios: 25-30 "salesman moments" mined from REAL dev conversations (sorento_cagent_stack,
   read-only) and crew eval sets, plus 10 plain fast-path asks as control. Messages kept real, person
   names masked in anything committed. Expected behaviour per scenario cites owner-ruling memory files.
2. Prototype (dev only, behind a flag, never prod): advisor loop over the EXISTING MCP tools (no new
   data access paths), tool subset from the parser intent (C) or from the same subset table (C2),
   structured state in, fact guard out. Two models: a Claude model via the dev Anthropic key and the
   current OpenAI mini.
3. Measure per scenario: right tools chosen, made-up facts (target 0; fact guard + judge), owner-rule
   compliance, helpfulness (judge rubric), p50/p95 latency, tokens + cost/turn. Same scenarios on v1.
4. Report `crew/state/reports/CHAT-ADVISOR-SPIKE.md`: table per model + v1, failure examples,
   recommendation, production build size / migration / gates.

Tests: red-first does not apply to the throwaway prototype; the fact guard and the tool-subset
selection get unit tests.

## Steps

- [ ] S1 facts: map v1/PV2 code, MCP tool catalogue, crew eval assets (subagents, file:line).
- [ ] S2 design page draft -> `crew report --ask`.
- [ ] S3 scenario set (masked) committed under the spike dir.
- [ ] S4 prototype: fact guard + subset selection (unit tests), advisor loop C and C2, runner.
- [ ] S5 runs: v1, C x 2 models, C2 x 2 models; judge; numbers.
- [ ] S6 report + design page updated with measured numbers; draft PR.

## Facts the design rests on (verified 15 Oct, file:line)

- v1 turn: `POST /api/v1/external/chat/turn` (`app/api/v1/external/chat.py:171`) -> `engine.run_turn`
  (`app/services/chatbot/engine.py:3037`). Parser key `chatbot_semantic_parser` (`head/parser.py:33`), called at
  `engine.py:4549`, strict json_schema, temperature 0 (`parser.py:1016-1046`), gpt-5.4-mini. Routing is code
  (`turn/apply.py:3288`, `turn/route.py:65`); replies are templates (`chatbot_reply_copy.py:439`,
  `turn/compose.py:506`); the only LLM-written reply is the low-signal clarifier (`lanes/casual.py:45`).
- Data: MCP over Streamable HTTP (`lanes/business/fetch.py:1423` -> `services.py:226`); the tool is the parsed
  domain row's `tools[0]` (`fetch.py:156`); scope args `contact_id` + `space_id` are stamped by code
  (`fetch.py:1295`); Stock Visibility is enforced in the backend (`stock_visibility.py:229`, applied in
  `inventory_service.py:704-717`, fails closed). Read-only allow-list `CHATBOT_READ_ONLY_TOOLS` = every
  `chatbot_domains` row's tools + `contracts.UNDOMAINED_CHATBOT_TOOLS` (`fetch.py:1404`, `contracts.py:65`).
- `chatbot_domains` (dev DB, 15 Oct) already maps parser domain -> tools: inventory {stock_balance, warehouses,
  low_stock_report}, master_products {products, brands, categories, uom}, promotion {promotions,
  promotion_attachments, promotion_products}, incoming {incoming_list, incoming_by_product, shipments}, order {8
  order/report tools}, sales, purchase_order, ... This is the intent -> tool table the advisor subset builds on.
- Alternatives are stored (`product_alternatives`, `app/models/product.py:421`) and printed inside the stock
  answer (`sorento_crm_mcp/presenters.py:1556`); there is no standalone alternatives tool. Seat-cover fits are the
  `fits` filter of `crm_find` (`app/services/product_find.py:188`); mirror/shelf fits are PR #1710 (not merged).
- MCP catalogue: 52 `crm_*` tools + `user_guides_read` (`sorento_crm_mcp/catalog.py:56`), including writes
  (`crm_order_cancel` :1364 etc), which the advisor never sees.
- Parser cost/latency (parser-gold runs, 15 Oct): v1 ~39k prompt tokens (~38k cached), ~441 out, $0.0054-0.0062
  per call, p50 3.3 s / p95 4.0 s. CRM side p50 0.14 s / p95 0.5 s (crew report CHAT-SPEED).

## Prototype contract (spike code, dev only)

Code lives in `sorento_crm_backend/scripts/advisor_spike/` (throwaway, never imported by `app/`). The "flag" is
that it is a script: no live turn path changes, and the runner refuses any DATABASE_URL whose host is not
localhost/127.0.0.1 or whose database is not `sorento_cagent_stack`. Run outputs and the unmasked scenario file live
in `crew/state/eval/advisor-spike/` (never committed); the committed scenario file is masked.

### `fact_guard.py` (unit tested, `tests/advisor_spike/test_fact_guard.py`)

`check_reply(reply: str, evidence: list[str], user_texts: list[str]) -> GuardResult`;
`GuardResult(ok: bool, unsupported: list[str], leaks: list[str])`, `ok` is True only when both lists are empty.

- Checked tokens in the reply:
  1. Codes: a token with at least one letter AND one digit, length >= 3, may contain `-`, `/`, `.`
     (e.g. `WC-2219`, `SB8812A`, `2219-W`). Compared case-insensitively against codes in the evidence.
  2. Numbers: integers and decimals, thousands separators and a currency prefix allowed (`RM 1,250.00`,
     `1250`, `12.5`). Compared by numeric value (`1,250` == `1250` == `1250.00`).
  3. Dates: `12 Nov`, `12 Nov 2026`, `2026-11-12`, `12/11/2026` normalise to month+day (+year when given) and
     match an ISO date in the evidence with the same month+day (and year when the reply gives one).
- A token is supported when it appears in any evidence string (tool results, serialised) or any user text.
- Exempt: list enumerators at line start (`1.`, `2)`), numbers inside a code token (the code is checked as a
  whole, not its digits).
- Leaks: any UUID (8-4-4-4-12 hex) in the reply is a leak even when it appears in the evidence (owner ruling
  never-leak-ids).
- Unsupported tokens are reported in reply order, de-duplicated.

### `subset.py` (unit tested, `tests/advisor_spike/test_tool_subset.py`)

`select_tools(domains: list[str], *, domain_tools: dict[str, list[str]], read_only: frozenset[str],
max_tools: int = 6) -> list[str]`

- `domain_tools` is the `chatbot_domains` name -> tools map (passed in, so the function is pure).
- Order: the primary tools of each domain in the given domain order (row order inside a domain), then each
  domain's `ADVISOR_COMPANIONS` entries, de-duplicated, keeping first occurrence.
- `ADVISOR_COMPANIONS` (module constant): inventory -> [crm_incoming_stock_by_product,
  crm_marketing_promotion_products_list, crm_find]; master_products -> [crm_inventory_stock_balance_list,
  crm_find, crm_marketing_promotion_products_list]; promotion -> [crm_inventory_stock_balance_list];
  incoming -> [crm_inventory_stock_balance_list]; order -> [crm_inventory_stock_balance_list].
- Drops any tool not in `read_only` (so a write tool listed on a row never reaches the model).
- Caps at `max_tools`.
- Unknown domain, empty domain list, or a domain whose tools are all dropped -> `[]` (caller falls back to v1).
- `ADVISOR_READ_ONLY` (module constant) = `CHATBOT_READ_ONLY_TOOLS | {"crm_find"}` is what the runner passes.

### Variants run by the spike

- **C**: advisor sees the parser verdict (v1 parser output of that turn) + carried state (focus, open question)
  + the last 3 exchanges; tools = `select_tools(parsed domains)`.
- **C2**: advisor sees the raw transcript (every prior user message and bot reply, then the new message), no
  parser output; tools = the same subset as C (isolates the context variable).
- **C2-all**: C2 with every tool in `ADVISOR_READ_ONLY` (the Claude Code shape; tests the n8n "more tools =
  worse" failure head on). Run if the budget allows.
- Loop: at most 4 model calls per turn; scope args (`contact_id`, `space_id`) are removed from the tool schemas
  and stamped by code; fact guard on the final reply; one retry with the unsupported tokens named; a second
  failure falls back to the v1 reply (counted as a guard fallback).
- Models: claude-sonnet-5-5 (effort low) and gpt-5.4-mini. Judge: claude-opus-5-5.

## Open questions

- C2 tool menu: same subset as C (isolates the context variable) vs all tools (true Claude Code).
  Plan: run both if budget allows; same-subset is the primary C vs C2 comparison.
