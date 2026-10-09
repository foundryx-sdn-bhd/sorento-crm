# PLAN: Chat analytics CA-2, topic taxonomy + topic labelling

Status: Build. Labelling ruled HYBRID; taxonomy v1 APPROVED by the owner (9 Oct 2026) and
seeded as version 1; monthly bumps approved by the owner until he names a delegate. Labeller,
resolver pass and migration built and green; next: Top asks + audit review mocks, then build.
Labelling runs on dev only (nightly tick off unless CHAT_TOPIC_LABELLER_ENABLED). Track: full (migration + new queue + new report definitions).

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
  `domain_hint` (closed lists from `turn/policy_rows.py`, 15 domains, 16 intents),
  `entities[].hint` (entity kind), `requested_attributes`, `order_status`, `group_by`,
  `date_mode`, `top_n`, `user_goal` (free text). CA-1 already copies domain + intent into
  facts.
- The bot also picks a reply language per turn (`language.py`, `engine.py:5540`). Not used
  here: crew ruling Q4, the labeller decides language for every row in its own call.

## Taxonomy discovery

1. Subjects are exactly the 15 parser domains (`turn/policy_rows.py`) plus `general` (no
   domain). Ruled, so the migration seeds them now.
2. Leaves nest under a subject. Crew sends, from the local 6 Oct prod snapshot (no prod DB
   access, ever): counts per (domain, intent, message type, outcome) since 5 Sep, and about 60
   masked asks with their parser fields.
3. Leaves are drafted from those asks. A leaf holding under 0.5% of asks folds into
   `<subject>.other`.
4. `taxonomy_v1.md` (tree, counts per leaf, 2 real masked examples per leaf) went to the owner
   as a crew-ask and was approved as drawn; the same migration seeds it.

## Schema (migration `chat_topics_0001_taxonomy`, additive)

- `chatbot.chat_topics`: `code` text pk (`inventory`, `inventory.availability`),
  `parent_code` text null (null on a subject), `label`, `definition` text, `version` smallint
  (the version that introduced the row), `status` text `active` / `retired`, `sort_order`
  int. The current taxonomy version is the highest `version` of an active row.
- `chatbot.chat_ask_facts.language` text null (`en` / `ms` / `zh` / `mixed` / `other`).
- `chatbot.chat_ask_items.confidence` real null (the labeller's own score for a
  `resolver_pass` item).
- Seed: the 16 subjects (15 parser domains + `general`) and the 31 approved leaves, all at
  version 1 (`taxonomy_v1.md`).

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

- R1 Subject. `subject = fact.domain` (one of the 15 parser domains). Null domain: `casual`
  message type -> `general.noise`; anything else -> `general` (the labeller picks its leaf).
  The labeller never changes the subject of ask 1.
- R2 Leaf. The labeller picks one leaf code from the active leaves UNDER that subject (the
  prompt lists only that subject's leaves per ask). Nothing fits: `<subject>.other`.
- R3 Split. The labeller may return asks 2..n for one message ("stock A and price B"). Ask 1
  keeps the turn row; each extra ask is a new fact row (`ask_seq` 2..n) copying the turn's
  contact, time, outcome and branch, with the labeller's subject (must be a parser domain).
  Turn measures count `ask_seq = 1` only.
- R4 Language. `en` / `ms` / `zh` / `mixed` / `other` per ask, new column `language`, decided
  by the labeller in the same call for every row (card answer 4).
- R5 Products. The labeller returns product words with a confidence; each word goes through
  the bot's own entity resolver (`lanes/business/services.py:534` `production_services` ->
  `resolve_entity`), keeping only `exact`-tier matches (see "Q5 follow-up"). A unique hit writes `chat_ask_items` (`role = named`,
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

### Taxonomy v1

Drafted from crew's data in `taxonomy_v1.md` (30 leaves under 16 subjects, real masked
examples). Awaiting owner approval.

## Card answers (crew, 9 Oct 2026)

1. Monthly taxonomy bumps: the owner approves until a delegate is named.
2. A split ask's subject: the labeller picks it, from the parser domains only.
3. A pick or follow-up with no domain inherits the domain of the previous ask ONLY through an
   explicit episode / turn link, never by text or name matching.
4. Language: the labeller decides it for every row, in the same call; `mixed` allowed; no
   `language.detect` heuristic.
5. Product items: confidence >= 0.8 AND a unique EXACT resolver hit.

### Q5 follow-up: the resolver DOES fuzzy-match, so the pass keeps exact hits only

`resolve_entity` (`lanes/business/services.py:140`) calls the route
`resolve_reference_post` (`api/v1/system/references.py:3151`). Its product matches carry a
`match_tier`, and several tiers are not exact: `prefix`, `substring`, `head_code`,
`embedding` (`services/entity_resolver.py:511`) and `trgm` (`references.py:1700`
`_NEIGHBOUR_TIERS`, "nearest-neighbour guesses", :1610). Spec search runs only when the caller
opts in (`spec_fallback`, default false, `references.py:1378`).

Rule for the resolver pass: call it without `spec_fallback`, keep only product matches whose
`match_tier == "exact"`, and write an item only when exactly one product remains (and the
labeller's confidence is >= 0.8). Every other tier counts as unresolved.

## Built (code map)

- Migration `alembic/versions/chat_topics_0001_taxonomy.py`; model `ChatTopic` + the two new
  columns in `app/models/chat_facts.py`.
- `app/services/chatbot/analytics/labeller.py`: `subject_for`, `parse_labels`, `build_messages`,
  `json_schema`, `run`, `production_chat`, `production_resolve`. `outcome.message_type` added.
- `app/tasks/chat_turns.py`: `ANALYTICS_QUEUE`, `label_chat_topics_job`,
  `enqueue_chat_topic_labeller`; `worker.py` QUEUES gains `analytics` (batch);
  `app/scheduler/task_scheduler.py` tick `chat_topic_labeller` at 02:15 Malaysia, gated by
  `settings.chat_topic_labeller_enabled` (default off).
- `scripts/chat_topic_label.py [--limit N]`: run now, in process.
- Tests: `tests/chatbot/test_chat_topic_labeller.py`, `tests/chatbot/test_migration_chat_topics_0001.py`,
  `tests/test_chat_topic_label_script.py`.
