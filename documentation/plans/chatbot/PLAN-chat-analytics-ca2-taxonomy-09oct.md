# PLAN: Chat analytics CA-2, topic taxonomy + topic labelling

Status: Plan, behaviour card posted as a crew-ask on PR #1551 (labelling mode ruled HYBRID by
the owner, 9 Oct 2026). Track: full (migration + new queue + new report definitions).

Design (binding): `documentation/plans/chatbot/DESIGN-chat-analytics-09oct.md` (PR #1545, branch
`claude/chat-analytics-design-cvrxu8`, approved 9 Oct 2026), section 8 lane CA-2. Builds on CA-1
(#1546, `PLAN-chat-analytics-ca1-facts-09oct.md`).

## What already exists (read, cited)

- Fact table with empty label columns: `alembic/versions/chat_facts_0001_tables.py:76-82`
  (`topic_code`, `taxonomy_version`, `label_model`, `label_prompt_version`, `labelled_at`,
  `label_confidence`). No `language` column (Q8 ruling wants one).
- Fact writer: `app/services/chatbot/analytics/facts.py` `record_turn` (hooked at turn close in
  `engine._close_turn`) and `sweep`; pure trace reader `analytics/outcome.py` (`flatten`).
- Report: `app/services/reports/definitions/chat_overview.py` over datasets
  `datasets/chat_asks.py` and `datasets/chat_ask_items.py`; note text says "Topics arrive with
  CA-2" (`chat_asks.py` `COVERAGE_NOTE`).
- Parser output per turn, strict JSON (`app/services/chatbot/head/parser.py:84`
  `_build_json_schema`): `message_type` (5 values, `contracts.py:160`), `intent_hint` /
  `domain_hint` (closed lists from `turn/policy_rows.py`, 14 domains, 15 intents),
  `entities[].hint` (entity kind), `requested_attributes`, `order_status`, `group_by`,
  `date_mode`, `top_n`, `user_goal` (free text). CA-1 already copies domain + intent into
  facts.
- Language: chosen per turn, deterministically, no model (`app/services/chatbot/language.py`
  `detect`, `engine.py:5540` / `:5901` `item["reply_language"]`, carried by
  `turn/tail.py:87`). Zero tokens.

## The open ruling (owner, pending): how a topic is assigned

| | (a) On the fly from parser fields | (b) Parser emits `topic` | (c) Nightly LLM labeller (design 3.4) |
|---|---|---|---|
| When | at `record_turn`, same INSERT | at parse, every turn | 02:15 MYT batch |
| Extra tokens | none | ~400 cached prompt + ~5 out per turn | ~120 in / 40 out per ask |
| Parser change | no | yes (schema key + prompt version) | no |
| Leaf granularity | what the parser already distinguishes: domain x intent x message type x entity kind x order status / attribute | any leaf in the list | any leaf in the list |
| Multi-ask split | no (one ask per turn) | possible with an array | yes |
| Taxonomy bump | a migration of mapping rows | a prompt version | a version number |

Pre-5-Sep history (CA-4 backfill) has no traces under any option; it still needs an LLM pass.

## Taxonomy discovery (planning only, nothing run)

1. Seed subjects from the 14 parser domains plus `general` (casual / unknown / escalation).
2. Measure the real distribution, counts only, of the parser fields over live turns since
   5 Sep: `(domain, intent, message_type, entity kinds, order_status, requested_attributes
   present, group_by)` per turn, plus `branch_kind`. Requested from crew as a
   `crew-data-request:` (no message text needed for counts).
3. Under (a): each leaf is a combination that holds >= 0.5% of asks; rarer ones fold into
   `<subject>.other`. Under (b)/(c): the one-off LLM discovery over ~2,000 masked asks the
   brief describes.
4. `taxonomy_v1.md` (tree, counts per leaf, 2 masked examples per leaf) goes to the owner as a
   crew-ask; nothing is labelled before approval.

## Q5 (staff replies in chat_histories), as measured by CA-1

Measured on #1546, not settled: no local copy can tell a staff reply from a bot reply (the 6 Oct
prod copy predates the `chatbot_turn_id` / `sender_source` columns; `is_human_intervened` true
on 0 of 8,990 turns). Upper bound: 921 of 4,984 outgoing rows (about 18%, 5 to 18 Sep) carry no
turn id. The prod-latest data request raised there has no answer on the PR yet.

## Owner ruling, 9 Oct 2026: HYBRID labelling

1. **Subject (top level) = the parser's own domain**, already on the fact row (CA-1
   `outcome._domain`: `plan.domains[0]`, else `verdict.domain_hint`; `outcome.py:183`). Free,
   written at turn close. A `casual` message type is the noise bucket.
2. **A small nightly labeller fills only what the parser does not**: the leaf topic, the ask
   split, language, product words + confidence, history backfill (CA-4), taxonomy version
   bumps, and messages with no parser turn. Batches of 40, the cheap model, only rows whose
   topic is empty or whose taxonomy version is stale.
3. **No parser prompt change; the live chatbot is untouched.** Leaves nest under the parser
   domains.

## Behaviour card (posted on PR #1551 as crew-ask)

### Rules

- R1 Subject. `subject = fact.domain` (one of the 14 parser domains). Null domain: `casual`
  message type -> `general.noise`; anything else -> `general` (the labeller picks its leaf).
  The labeller never changes the subject of ask 1.
- R2 Leaf. The labeller picks one leaf code from the active leaves UNDER that subject (the
  prompt lists only that subject's leaves per ask). Nothing fits: `<subject>.other`.
- R3 Split. The labeller may return asks 2..n for one message ("stock A and price B"). Ask 1
  keeps the turn row; each extra ask is a new fact row (`ask_seq` 2..n) copying the turn's
  contact, time, outcome and branch, with the labeller's subject (must be a parser domain).
  Turn measures count `ask_seq = 1` only.
- R4 Language. `en` / `ms` / `zh` / `mixed` / `other` per ask, new column `language`.
- R5 Products. The labeller returns product words with a confidence; each word goes through
  the bot's own entity resolver (`lanes/business/services.py:534` `production_services` ->
  `resolve_entity`). A unique hit writes `chat_ask_items` (`role = named`,
  `resolved_by = resolver_pass`). No hit or several hits: nothing (never guessed). Note:
  `report_ask._resolve_word` (`report_ask.py:350`) resolves brand / category / sales agent
  only, not products, so brand words use it and product words use `resolve_entity`.
- R6 What gets labelled. Rows with `topic_code IS NULL` or `taxonomy_version <` current,
  newest first, capped per night. A row is written once per batch in one transaction.
- R7 Privacy. The customer's words are read from `chatbot.turns.envelope` into memory for the
  call and never stored. Facts get codes, a version, model, prompt version, confidence.
- R8 Schedule. RQ job on a new `analytics` queue (`worker.py` `QUEUES`, role `batch`), queued
  by the scheduler at 02:15 Malaysia; also runnable by script.

### Token estimate (per day)

Measured: ~315 asks/day, ~62K input + ~13K output tokens/day, 8 calls of 40 asks. At small
model list prices (USD 0.4 to 1 per M in, 1.6 to 5 per M out) that is about USD 0.05 to 0.13 a
day, USD 1.5 to 4 a month. One-time label of the live era (5 Sep on, ~11K asks): ~2.2M in /
0.45M out, USD 1 to 4.

### Draft taxonomy v1 (subjects = parser domains; leaves pending counts)

| Subject (parser domain) | Draft leaves |
|---|---|
| inventory | availability, quantity_for_need, by_warehouse, low_stock_list, other |
| incoming | product_eta, container_contents, other |
| order | status, delivery_eta, outstanding_list, document_copy, other |
| master_products | price, specification, alternative, discontinued, find_by_description, other |
| product_attachment | photo, catalogue, other |
| promotion | current_promotions, tier_terms, other |
| sales | ranking_report, own_figures, other |
| purchase_order | outstanding, other |
| purchase_cost | last_cost |
| spo_allocation | last_in |
| forms / portal_link / resource_attachment / ideate / goods_receive | one leaf each + other |
| general (no domain) | noise, ask_for_person, account_statement, complaint, other |

About 45 leaves. Final leaves keep only those holding >= 0.5% of asks; the counts come from the
data request.
