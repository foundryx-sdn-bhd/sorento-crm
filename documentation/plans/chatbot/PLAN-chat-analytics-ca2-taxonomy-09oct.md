# PLAN: Chat analytics CA-2, topic taxonomy + topic labelling

Status: Hand test (10 Oct 2026). Built red-first, reviewer READY + security-reviewer clean,
browser pass on head chat_topics_0002_audit; labelling ran on dev only (399 asks). Track: full
(migrations + new queue + new report definitions + new audit routes).

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

Drafted from crew's data in `taxonomy_v1.md` (31 leaves under 16 subjects, real masked
examples). Approved by the owner as the 31-leaf table, 9 Oct 2026.

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

## Mock v1 APPROVED (owner, Lavish, 9 Oct 2026): build contract for Top asks + Topic audit

Mock: `documentation/mockups/CA-2-TAXONOMY/index.html` (652404da). Q1-Q5 answered as
recommended: a person judges all 300; audit gated by `system.chat_history.view` AND
`chatbot.analytics.view`, no new grant; stratified draw; Bot failures report IN this lane;
Top asks opens 5 Sep 2026 to today, topic by month. UAC: `ca2-taxonomy-acceptance-criteria.md`.

### Top asks (report, no route of its own)

- `app/services/reports/definitions/chat_top_asks.py`, `KEY = "chat_top_asks"`, permission
  `chatbot.analytics.view`, module `chatbot`, modelled on `definitions/chat_overview.py`.
- `datasets/chat_asks.py` gains dimensions `subject` (subject words: the `chat_topics` label of
  the subject code, `general` for a null domain), `topic` (the `chat_topics` label of
  `topic_code`, `(not labelled yet)` when null) and `language`. Joined as Core tables (no
  mapped-class scope splice). A `month` dimension exists already or is added (Malaysia month).
- Params: Period (default 5 Sep 2026 to today), Contact type, Outcome (defaults as Chat
  overview), Subject (multi, clearable, options = subject rows of `chat_topics`), Count by
  (`topics` default / `products`), Products (`asked` default / `shown`; `asked` = role in
  (`named`, `asked`), `shown` = role `shown`; filters only the products grain).
- Default view: pivot rows `topic`, cols `month`, measure `asks`, bar chart; detail columns
  asked_at, subject, topic, language, outcome, customer. Note: `Topics labelled: <n>% of asks
  (taxonomy v<k>).` plus the coverage line.
- Product grain counts one per (ask, product) for the chosen role (the unique constraint
  `uq_chat_ask_items_ask_kind_ref_role` already guarantees it).
- `definitions/chat_bot_failures.py`, `KEY = "chat_bot_failures"`: same datasets, outcome
  filter default = failed outcomes (technical, cannot_do, missed), pivot rows `failure_reason`
  cols `topic`, measure `asks`.

### Topic audit (new table, routes, page)

- Migration `chat_topics_0002_audit` (down_revision `chat_topics_0001_taxonomy`): table
  `chatbot.chat_label_audits`: `id uuid pk`, `ask_id uuid FK chat_ask_facts.id ON DELETE
  CASCADE`, `taxonomy_version smallint not null`, `drawn_at timestamptz not null default now()`,
  `verdict text null` (`right` / `wrong_topic` / `wrong_subject`), `corrected_topic text null FK
  chat_topics.code`, `products_verdict text null` (`right` / `wrong`), `reviewed_by text null`
  (user id), `reviewed_at timestamptz null`; unique (`ask_id`, `taxonomy_version`). No message
  text column.
- Routes in `app/api/v1/system/chatbot.py` (both slugs required, 403 otherwise):
  - `GET /system/chatbot/topic-audit?version=&show=to_review|reviewed|all&subject=` ->
    `{version, versions[], sample_size, reviewed, rates: {subject, topic, product} (null when
    nothing judged), targets: {subject: 0.9, topic: 0.8, product: 0.95}, topics: [{code, label, parent_code}] (active taxonomy rows, for the corrected-topic pickers; added at build), rows: [{id, asked_at,
    message, subject, subject_label, topic, topic_label, confidence, products: [code],
    verdict, corrected_topic, corrected_topic_label, products_verdict, reviewed_by_name}]}`.
    `version` defaults to the current taxonomy version. `message` is read from
    `chatbot.turns.envelope` at request time (labeller `_message_text`), never stored.
  - `POST /system/chatbot/topic-audit/sample {version}` -> 201 `{drawn}`; 409 when a sample
    for that version exists; draws from facts with `topic_code` set and `taxonomy_version =
    version`: at least 10 per subject that has asks (all, when fewer), the rest of the 300 in
    proportion to subject volume, half of that rest from the lowest `label_confidence`.
    Fewer than 300 labelled asks: draws all of them.
  - `PATCH /system/chatbot/topic-audit/{id} {verdict, corrected_topic?, products_verdict?}`
    -> the row. `wrong_topic` / `wrong_subject` require `corrected_topic` (a leaf; for
    `wrong_topic` it must be under the ask's subject, else 422).
  - Rates: subject = (right + wrong_topic) / judged; topic = right / judged; product = right /
    rows with a products_verdict.
- FE: `app/(protected)/system-management/top-asks/page.tsx` and `bot-failures/page.tsx`
  (`ReportPage`, like `chat-overview/page.tsx`); `topic-audit/page.tsx` + 
  `services/topicAuditService.ts` + `hooks/useTopicAuditQuery.ts` /
  `useTopicAuditMutations.ts`; menu entries after Chat Overview in BOTH `MENU_SIDEBAR` and
  `MENU_SIDEBAR_COMPACT` (`config/menu.config.tsx:945`, `:2250`): Top Asks and Bot Failures
  gated `chatbot.analytics.view`, Topic Audit gated `system.chat_history.view` (menu items take
  one slug, `config/types.ts:18`; the server enforces both).

### Review round 1 (reviewer + security-reviewer, 10 Oct 2026): rulings

Fixed in this lane (red-first): split asks count once on turn measures (Chat overview and Bot
failures read `ask_seq = 1`; Top asks counts every ask); `wrong_subject` needs a leaf under a
DIFFERENT subject (422); a concurrent second draw is 409 and a version with no topics 404; a
malformed audit id is 404; the labeller caps extra asks per message, ignores keys not in the
batch and keeps the first label per key (one customer's text cannot relabel another's ask);
Chat overview's coverage note no longer says topics are coming.

Accepted, recorded here:
- Topic Audit menu is gated on `system.chat_history.view` only (menu items take one slug,
  `config/types.ts:18`). CA-1 granted `chatbot.analytics.view` to every role holding that slug
  (`chat_facts_0001_tables.py:40,172`), so the 403 case needs a role edited by hand since; the
  server enforces both.
- `chat_label_audits.ask_id` cascades: a CA-1 `sweep(rebuild=True)` or a relabel that replaces
  split rows drops those verdicts. A rebuild is a rare admin step, and a relabel happens only on
  a taxonomy bump, which gets its own sample. Trigger to revisit: a rebuild after an audit has
  started.
- A fact domain outside the 16 subjects would label as the bare domain code with no row. All 15
  parser domains are subjects today; a new parser domain needs a taxonomy bump anyway.
- Nightly flag gates only the scheduler tick; the script and a hand-queued job are deliberate
  dev actions.
