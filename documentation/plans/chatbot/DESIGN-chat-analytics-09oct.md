# DESIGN: Chat analytics, methodology and engineering (9 Oct 2026)

Status: APPROVED by the owner, 9 Oct 2026 ("okay can"). All 8 open questions ruled as recommended
(section 8, "Owner rulings"). Design only; no code, no migrations in this lane (CHAT-ANALYTICS-DESIGN).
Build starts with CA-1 as its own lane. Track once built: full pipeline (migrations + new grant).

Twin files: `DESIGN-chat-analytics-09oct.md` and `DESIGN-chat-analytics-09oct.html` carry the same content; the HTML draws the diagrams.

Sources: code at `origin/main` 4ee6f643d (cited `file:line`); numbers are AGGREGATES from the prod
snapshot `sorento_ai_automation_20261006` taken by the crew scout. This repo is public: there are no
real names, phone numbers or message texts in this document. Every example message is invented
and masked (`<product>`, `<dealer>`).

---

## 0. The short version (read this if nothing else)

1. **What we are building.** A way to ask questions of the whole chat history ("which products
   did dealers ask about most in September?", "what did the bot fail on last week?") and get an
   answer by texting the bot OR on a CRM Reports screen. Same engine, same numbers, both places.
2. **How.** Every customer question becomes one **row of facts** (when, who, what topic, which
   products, what the bot did, whether it worked). The rows come from three places: the bot's own
   trace for recent turns (free, already structured), a one-time AI read-through of older messages
   (one-off, roughly USD 5 to 15), and a small nightly AI pass that names each question's topic.
3. **Topics are decided by AI against a fixed list we agree once** (the "taxonomy": about 40
   question types under 10 subjects). No keyword or pattern code anywhere. The list changes only
   by an explicit new version, so a trend line never shifts because the AI changed its mind.
4. **Questions stay flexible** in three steps: (1) the existing report catalogue, extended with a
   "chat" dataset, answers most questions with exact numbers; (2) combinations of those (two
   groupings, a comparison of two periods); (3) only if (1) and (2) cannot, a guarded read-only
   query over curated views, with a time limit, a row cap and no access to raw message text.
5. **Privacy.** Analytics answer with counts and labels, never with what a customer typed, never
   with episode summaries. Contact-level breakdowns need a new grant. Dealers see only their own.
6. **Order of work.** Six build lanes, each one showing the owner something usable. First lane:
   the fact table from traces plus a "Chat overview" screen (about five weeks of traced history,
   5 Sep on, is already there). Last lane: the AI assistant folds into the chatbot console.
7. **Honest limits.** Before 5 Sep we only have message text, and 19 Aug to 7 Sep is a gap in
   saved parser output. Every answer prints a coverage line so nobody reads a gap as a trend.

---

## 1. Problem statement

### 1.1 What the owner asked for

"Analyse chat history at scale: most asked products in a period, most common questions, what the
bot failed on, trends. The questions must be flexible, like ChatGPT over any data." The WhatsApp
chatbot is the engine (one engine, `chatbot.turns`); answers come back by texting the bot AND on a
CRM dashboard. Bot replies count, not only customer messages.

### 1.2 Why it does not exist yet

- There is no analytics feature over chat. `conversation_analysis` appears only as an
  `access_agents` label (6 contacts + 1 team) and is read by no code.
- The AI assistant (`ai_assistant_service.py`, ReAct loop `_run_agent_loop` :2300, gpt-4o-mini,
  6 iterations / 3 tool calls) has no chat tools and no SQL. It is not the engine we extend.
- The chatbot already answers report questions through the **report engine**
  (`lanes/business/report_ask.py` -> MCP `crm_report_ask` -> `GET /report-ask` ->
  `services/reports/ask.py run_ask` :152 -> `engine.run_summary` :930). But its catalogue is sales
  only: `CATALOGUE` (`services/reports/ask.py:31`) has three bases, all over sales order lines.

### 1.3 Example questions, grouped by type

Owner's own: most asked products in a period; most common questions; what the bot failed on;
trends. Realistic ones from managers and salesmen:

| Type | Example question (masked) | Who asks |
|---|---|---|
| **Ranking: products** | "Top 10 products asked about this month" | Owner, product manager |
| | "Which `<brand>` items did dealers ask stock for most in September?" | Brand manager |
| | "Products asked about but we had no stock" | Purchasing |
| **Ranking: questions** | "What are the most common questions this week?" | Owner |
| | "What do dealers ask most about promotions?" | Marketing |
| **Failure** | "What did the bot fail on yesterday?" | Owner, bot operator |
| | "Which questions does the bot say it cannot do, most often?" | Owner |
| | "How many questions ended with a hand-over to staff?" | CS lead |
| | "Show the failure rate by week since September" | Owner |
| **Trend** | "Is stock enquiry going up or down month on month?" | Owner |
| | "Compare promotions questions this month vs last month" | Marketing |
| | "Busiest hours for chat" | CS lead |
| **Customer / salesman** | "Which of my dealers chatted most this month?" | Salesman (own customers) |
| | "Dealers who asked about `<product>` but did not order within 14 days" | Salesman, sales manager |
| | "Per salesman: how many of their customers used the bot this month?" | Sales manager |
| **Demand signal** | "Products asked for more than 5 times with zero sales" | Purchasing |
| | "Which categories get price questions most?" | Sales manager |
| **Bot quality** | "Questions the bot answered with an empty result" | Bot operator |
| | "How often did customers ask again right after a bot reply?" | Bot operator |
| **Dealer self-service** (texted by a dealer) | "What did I ask about most this month?" | Dealer, own data only |

The last two families (demand signal, customer x sales) need a JOIN between chat facts and sales
facts. That is why the design generalises the report engine instead of building a separate chat
report: one engine can line the two up.

---

## 2. Measurement model (plain language first)

### 2.1 The five units, and which question uses which

```
contact  (a WhatsApp number linked to a customer)
  `-- conversation-day  (all of one contact's messages on one Malaysia calendar day)
        `-- episode  (one topic, from first question to topic switch / idle; conversation_frames)
              `-- turn  (one inbound message the engine processed; chatbot.turns row)
                    `-- ask  (one question inside that message; usually 1, sometimes 2-3)
```

| Unit | Definition | Use it for |
|---|---|---|
| **Ask** (the atomic row) | One distinct question in one inbound message. A message "stock `<A>` and price `<B>`?" is 2 asks. | "Most asked products", "most common questions" |
| **Turn** | One `chatbot.turns` row (live, not test, latest attempt). | Bot behaviour: answered / failed / clarified, latency |
| **Episode** | One `conversation_frames` row: one topic span. | "Resolved?", "asked again?", how long a topic took |
| **Conversation-day** | One contact x one Malaysia day. | Activity counts ("dealers who chatted") |
| **Contact** | One `respond_contacts` row. | Reach ("how many dealers used the bot") |

Rule: **every metric names its unit.** "Most asked product" counts asks; "failure rate" counts
turns; "resolved rate" counts episodes. Printing the unit in the reply ("Asks", "Turns") stops a
manager comparing a turn count with an ask count.

### 2.2 Metric definitions

**Asked a product.** An ask "asked about product P" when P was:

1. *named and resolved*: the engine (or the backfill) resolved the customer's words to P's id
   (trace `understood.entities` resolved, or the tool call's product args); or
2. *shown*: P appeared in the bot's reply rows (`response.reply.result_set`) for a question that
   named a brand, category or partial description; counted separately as "shown", never merged.

Unresolved wording ("that blue one") counts at the level it did resolve to (brand / category) or
as "unresolved". It is never matched to a product by string similarity (owner rule: semantics only
via the parser or the LLM). Today only 592 of ~9,000 live turns resolved a product code, so section
3.4 adds a resolution pass that reuses the bot's own resolver.

**Common question.** An ask's **topic** is one leaf of the taxonomy (section 2.3), assigned by the
LLM labeller. "Most common question" = ask count per topic leaf, ranked. A leaf is a question
TYPE ("stock availability of a product"), not a sentence; the product is a separate dimension, so
"most common question about `<brand>`" is topic x brand.

**Bot outcome per turn** (from structured trace fields only; no reading of reply text):

| Outcome | Rule (structured fields) | Counts as failed? |
|---|---|---|
| `answered` | lane ran a tool, tool ok, `replied.rows_offered > 0` or a number reply | no |
| `answered_empty` | tool ok, zero rows ("none found") | no, reported separately (often a demand signal) |
| `clarified` | `branch_kind = clarify_menu`, or `replied.asking`, or a `required_ask` event | no (but see "asked again") |
| `refused_policy` | `access_denied`, `ai_not_allowed`, domain grant refusal (`domain_grant` event) | no: working as designed; reported as "refused" |
| `cannot_do` | `not_supported`, `out_of_scope` | **yes** (capability gap) |
| `missed` | `looked_up.missed` set, or a `crossdomain` block that ended the turn | **yes** |
| `phantom` | a `phantom_answer` event (bot claimed a fact without a tool) | **yes** (quality) |
| `technical` | `status = failed` (184 turns), any failure stage | **yes** |
| `handover` | escalation to staff completed | no; its own metric |
| `noise` | `low_signal` (greetings, "ok", stickers) | excluded from ask counts |

**Failed (turn)** = `cannot_do` + `missed` + `phantom` + `technical`. **Failure rate** = failed
turns / (all turns minus `noise`). The four reasons stay separate in every breakdown, because "the
bot cannot do X" (a product decision) and "the bot broke" (a defect) need different owners.

**Asked again** (episode-level quality signal): within one episode, the next ask from the contact
within 10 minutes has the SAME topic leaf and the same product/brand after a bot reply. Determined
from the labels (semantic) and the episode (structural); no text comparison.

**Resolved (episode).** An episode is *resolved* when its last bot turn was `answered` /
`answered_empty` / `refused_policy`, there was no `handover`, and the contact did not raise the
same topic leaf again within 24 hours. This is a **proxy**, said as such on the dashboard. A true
"was the customer satisfied" needs a signal we do not have (no rating, no sentiment stored).

### 2.3 Topics: how the taxonomy is built and kept stable

```
seed (code) --> discovery (LLM, one-off) --> owner review --> v1 frozen --> label everything
                                                                   ^              |
                                                                   |              v
                                    monthly: promote proposals --- "other" bucket + proposals
```

1. **Seed** from what the engine already knows: the parser's domains (`chatbot_domains`,
   `models/chatbot_policy.py:31`) and intents. That gives the top level: stock, price, order
   status, delivery / ETA, promotion, product info, account / statement, sales report, complaint,
   general.
2. **Discovery** (one-off): an LLM reads a stratified sample of ~2,000 masked asks (all eras, all
   domains) and proposes leaf question-types under each seed subject, with a one-line definition
   and two INVENTED examples each. Target 30 to 50 leaves; anything rarer than 0.5% of asks folds
   into `<subject>.other`.
3. **Owner review**: the owner accepts / merges / renames leaves in a short list (a
   `taxonomy_v1.md` in this folder plus the seed rows). That review is the only place the list
   changes.
4. **Frozen version.** Every label row records `taxonomy_version`, `model`, `prompt_version`. A
   trend chart only compares labels of ONE version; re-labelling history to a new version is a
   batch job (cheap, section 3.6) run once per version bump, so the whole history moves together.
5. **"Other" with a proposal.** When no leaf fits, the labeller returns `other` plus a short
   proposed leaf name. A monthly report lists the top proposals; promoting one is a v2 bump.

Why not let the LLM invent topics freely each time: counts would not be comparable week to week,
and "the most common question" would change because wording changed. A fixed list is what makes a
trend a trend.

### 2.4 How bot replies are classified

- **Live era (5 Sep on):** from structure only (the outcome table above), plus what was SHOWN:
  `response.reply.result_set` items (products offered), `quick_replies` (menu offered),
  `attachments_src` (catalogue / flyer sent). No LLM needed.
- **Pre-turn era:** only reply text exists (`chat_histories` outgoing, avg 540 chars). The
  backfill LLM classifies each bot reply into the same outcome set from the reply AND the ask it
  answered. Where `chat_histories.result` holds product items (7,217 items, 14 Jun to 22 Sep),
  "shown" products come from that structure, not from the text.
- **Staff replies** (a human in the chat) are a separate reply class `staff`, never counted as bot
  answers. Identification: outgoing rows with no `chatbot_turn_id`
  (`models/chat_history.py`, the column added by QUOTED-REPLY R1) during a span when the contact
  was human-intervened (`memory.py contact_is_human_intervened`). Coverage of staff replies in
  `chat_histories` is unknown (open question Q5).

### 2.5 Edge rules

| Case | Rule |
|---|---|
| Test turns | Excluded everywhere: `is_test = true` (2,171 turns) and `ingress = console`. A test turn never becomes a fact row. |
| Retries | One fact row per respond `message_id`: the latest `attempt` (unique key `models/chatbot_turn.py:63`). |
| Duplicate history rows | From 14 Aug, `chat_histories` is unique per message id (`CHAT_HISTORY_DEDUPE_CUTOVER`). Before that, exact duplicates (same contact, direction, `message_id` or same text + `sent_at` to the second) collapse to one. Exact equality only. |
| Multi-ask turns | The labeller splits a message into asks (`ask_seq` 1..n). Turn measures count the turn once; ask measures count each ask. |
| Era overlap | 5 Sep to 22 Sep both `chat_histories` structure and traces exist: traces win, history rows for those turns are skipped (joined by `chat_histories.chatbot_turn_id` or the respond `message_id`). |
| Media messages | An image / voice note counts as an ask when media extraction produced text (`media_intake` stage); otherwise outcome `noise`. |
| Internal staff contacts | Contacts linked to a user (`users.respond_contact_id`, `models/user.py:72`) are tagged `audience = staff` and filterable out of "customer" metrics. |

### 2.6 Coverage: what we know per period, and how answers say it

| Era | Dates (2026) | What exists | How facts are made | Expected quality |
|---|---|---|---|---|
| A | 17 Apr to 21 Jul | message text only; product items in `result` from 14 Jun | backfill LLM | topic + product good; outcome from reply text (medium) |
| B | 22 Jul to 18 Aug | text + parser domain/intent (`state_trace.parser_raw`) | backfill LLM, seeded with the saved domain/intent | good |
| C | 19 Aug to 4 Sep | text only (parser output not saved: 9,991 incoming null) | backfill LLM | as A |
| D | 5 Sep on | full traces in `chatbot.turns` | flattener (no LLM) + nightly topic labeller | best |

Every answer (WhatsApp and screen) carries ONE coverage line:
`Coverage: 96% of asks labelled; 5 Sep to 8 Oct from bot traces`. When a period crosses eras the
line says so: `Before 5 Sep from message text (estimated)`. When coverage for the period is under
80%, the reply leads with that line.

---

## 3. Engineering: data pipeline

### 3.1 The whole flow

```
 respond.io --> chatbot.turns (envelope, trace, response) ----+
                         |                                    |  (A) flattener, at turn close
 n8n --> chat_histories  |                                    v   + nightly sweep
   (older text, result,  |                         chatbot.chat_ask_facts  <---+
    state_trace)         |                         chatbot.chat_ask_items      |
         |               v                                    ^                | (C) labeller,
         |   conversation_frames (episodes, newest 20 kept)   |                |  nightly
         |               | (B) snapshot at close              |                |  (topic, asks,
         |               v                                    |                |   product resolve)
         |      chatbot.chat_episode_facts -------------------+                |
         |                                                                     |
         +----------(D) one-time backfill extractor (LLM) ---------------------+
                                                   |
                                                   v
                    report registry: dataset "chat", bases asks / turns / episodes
                                  |                                   |
                      /report-ask (chatbot, MCP)          /reports/{key}/run (CRM screen)
```

Four producers, one fact store. Nothing new sits in the turn's request path except (A), which is a
single INSERT at turn close of values the turn already holds.

### 3.2 Storage schema (new tables in schema `chatbot`)

Same module schema as `chatbot.turns` so uninstalling the chatbot module drops them too.

**`chatbot.chat_ask_facts`** : one row per ask (the fact table).

| Column | Type | Note |
|---|---|---|
| id | uuid pk | |
| source | text | `trace` / `history_backfill` |
| turn_id | uuid null | `chatbot.turns.id` (trace source) |
| history_message_id | bigint null | `chat_histories.id` (backfill source) |
| ask_seq | smallint | 1..n within the message |
| contact_id | text | `respond_contacts.id` |
| customer_id | uuid null | the contact's customer at the time (snapshot) |
| audience | text | `dealer` / `staff` / `unknown` |
| asked_at | timestamptz | message send time (Malaysia day derived in the dataset) |
| channel | text | whatsapp, ... |
| domain | text | parser domain (trace) or labeller domain |
| intent | text | parser intent |
| topic_code | text null | taxonomy leaf, null until labelled |
| taxonomy_version | smallint null | |
| outcome | text | section 2.2 outcome |
| failure_reason | text null | `cannot_do` / `missed` / `phantom` / `technical` |
| branch_kind | text null | copied from the turn |
| tools | text[] | tool names called |
| rows_offered | int null | |
| reply_ms | int null | finished_at minus started_at |
| tokens | int null | parser tokens |
| episode_id | uuid null | the frame this turn ended up in |
| label_model / label_prompt_version / labelled_at | text / text / timestamptz | audit of the labeller |
| label_confidence | real null | labeller's own score, used to pick audit samples |
| is_media | bool | |

Unique: (`turn_id`, `ask_seq`) and (`history_message_id`, `ask_seq`). Idempotent upsert.
**No free-text column.** The customer's words never land here; the table is safe to aggregate and
safe to expose to the guarded SQL step.

**`chatbot.chat_ask_items`** : products / brands / categories an ask touched.

| Column | Note |
|---|---|
| ask_id | fk |
| kind | `product` / `brand` / `category` / `warehouse` |
| ref_id | the CRM id (product id, brand id, ...) |
| role | `named` (customer asked) / `shown` (bot offered) |
| resolved_by | `parser` / `resolver_pass` / `history_result` |

**`chatbot.chat_episode_facts`** : a snapshot of each episode's analytic fields, written in the
same transaction as the frame close, BEFORE `_trim_to_newest` (`turn/memory.py:117`, called at :269; KEEP_EPISODES
20 at :69) deletes the 21st. Columns: frame id, contact, opened/closed at, domain, intent, topic of
first ask, turn count, last outcome, handover, close_reason, `resolved`, `asked_again` (computed by
the nightly job once labels exist). **Not** the `summary` text.

**`chatbot.chat_topics`** : the taxonomy (code, parent_code, label, definition, version, status
`active`/`retired`). Seeded by migration; edited only by a version bump.

Volume: ~25,000 ask rows for the whole history today, ~300 to 400 per day. Years of headroom on
plain indexes (`asked_at`, `topic_code`, `contact_id`, items by `ref_id`).

### 3.3 (A) Trace flattener

- Where: the engine's tail, after the turn closes, as one more step that never fails the turn
  (wrapped; a failure is logged and the nightly sweep refills).
- What: reads only the trace stage records already in memory: `understood` (intent, domain,
  entities, message_type, tokens), `looked_up` (domains, missed, gate_reason, exit, tool
  {name, args, domain, ms}), `replied` (rows_offered, asking, cross_domain_block), the kind events
  (`tool`, `crossdomain`, `domain_grant`, `phantom_answer`, `required_ask`), and
  `response.reply.result_set` for shown items.
- Writes ask_seq 1 with `topic_code = null`; the labeller fills topic and splits further asks.
- Skips `is_test` and `ingress = console` turns entirely.
- Nightly sweep: `INSERT ... ON CONFLICT DO NOTHING` for any closed live turn of the last 3 days
  with no fact row (covers crashes and the first deploy).
- One-time: run the sweep over every turn since 5 Sep (11,161 rows, seconds).

### 3.4 (C) Nightly labeller (topic + ask split + product resolution)

- RQ job on the existing worker (new queue `analytics`, added to `WORKER_QUEUES`), scheduled
  nightly at 02:15 Malaysia by the existing scheduler; also runnable on demand.
- Input per ask: the customer's message text (read from `chatbot.turns.envelope`, never stored in
  facts), the parser's domain/intent/entities, the bot's outcome and the first 300 characters of
  the bot reply, plus the taxonomy leaves (prompt-cached).
- Output (strict JSON schema): list of asks `{ask_seq, topic_code | "other", proposed_leaf?,
  product_words[], brand_words[], confidence}`.
- Product resolution: `product_words` go through the SAME resolver the bot uses for entity
  words (`report_ask._resolve_word` :350 path / the head resolver), not a new matcher. A unique hit
  writes a `chat_ask_items` row `resolved_by = resolver_pass`.
- Model: through the one provider seam (`services/llm_provider.py`), configured in System Settings
  like the parser (today gpt-5.4-mini). Batches of 40 asks per call, grouped by contact-day so the
  model sees the neighbouring asks (that is how it splits "and that one also?").
- Idempotency: a row is labelled when `topic_code IS NULL` or its `taxonomy_version` is older than
  current; the job picks rows, labels, writes in one transaction per batch.

### 3.5 (D) One-time backfill for old messages

- Scope: `chat_histories` rows before 5 Sep, plus 5 to 22 Sep rows that did NOT come from a turn.
  Incoming rows become asks; outgoing rows supply the reply each ask got (the next outgoing bot row
  of the same contact) and staff replies (section 2.4).
- Same prompt as the labeller, plus "classify the bot reply outcome" (only this era needs it).
  Era B rows pass the saved `parser_raw` domain/intent as a hint. Era rows with `result` items pass
  those as "shown" items directly (no LLM).
- Batching: per contact, per day, 40 asks per call; a resumable cursor table
  (`chatbot.chat_backfill_runs`: run id, last processed id, counts, cost) so a stop resumes.
- Idempotency: unique (`history_message_id`, `ask_seq`) upsert; re-running is a no-op.

**Cost estimate, from real volumes.** Customer text is tiny (22,845 incoming messages, ~481 KB,
avg 22 chars). The bulk is bot replies (31,050 outgoing, ~16 MB, avg 540 chars), and we send only
the first 300 chars of each.

| Item | Count | Tokens per unit | Tokens |
|---|---|---|---|
| Pre-turn incoming asks (22,845 total minus ~9,000 live-era) | ~13,800 | ~10 text + ~25 parser hint/meta | ~0.5 M in |
| The bot reply each ask got (300 chars) | ~13,800 | ~80 | ~1.1 M in |
| Neighbour context (previous ask of the same day) | ~13,800 | ~15 | ~0.2 M in |
| Taxonomy + instructions per call (~350 calls) | 350 | ~3,000 (cache-eligible) | ~1.05 M in |
| Output JSON | ~13,800 | ~45 | ~0.6 M out |
| **Total backfill** | | | **~2.9 M in, ~0.6 M out** |
| Live era topic labelling (one-time, ~9,000 turns) | 9,000 | ~120 in / ~40 out | ~1.1 M in, ~0.4 M out |
| Audit re-label of a 10% sample on a larger model | ~2,300 | as above | ~0.4 M in, 0.1 M out |
| Nightly steady state (~350 asks/day) | 350 | ~120 in / ~40 out | ~45 K in, ~15 K out per day |

At small-model list prices (order of USD 0.4 to 1 per million input tokens, 1.6 to 5 per million
output), the whole one-time work is about **USD 5 to 15**, the nightly job a few US cents per day.
The provider's current price page is the authority; the run table records actual spend per run.
Runtime: ~350 calls, at 4 concurrent calls about 15 to 30 minutes, well inside the 200K tokens/min
limit seen in prod (`llm_call.py:6`) if paced at one call per second.

### 3.6 Re-labelling

A taxonomy bump sets `current_version`; the labeller treats older rows as stale and relabels
newest first. Full history relabel ~4 M input tokens, the same order of cost as the backfill. Old
labels are overwritten (no history of labels kept: one version is live; the audit sample keeps
its own record).

---

## 4. Engineering: report engine generalisation

### 4.1 What exists

- **Governed layer**: `services/reports/registry.py`: `Dataset` (:127) with `Column` (:80) tagged
  dimension / measure / date / text, `DateBasis`, params `PeriodParam` / `SelectParam`, `scope`
  company/none. It powers the CRM Reports screen (`api/v1/reports/reports.py`: catalogue :161,
  run :224, export :262, saved views :321) and the xlsx renderer. Datasets today:
  `delivery_order_lines`, `sales_order_lines` (+ `_ask`), `billing_documents`,
  `sponsorship_forms`. Pivot cap `PIVOT_CELL_CAP = 5000` (`engine.py:53`).
- **Chatbot ask catalogue**: `services/reports/ask.py` `CATALOGUE` (:31), 8 dimensions, 2
  measures, 3 bases, all sales. The parser fills slots; nothing writes SQL. Dealers restricted to
  `DEALER_KEYS` (:46).

### 4.2 The change: a dataset axis

`CATALOGUE[basis]` becomes `CATALOGUE[dataset][basis]`. Sales keeps its three bases unchanged
under `dataset = "sales"`. New:

| dataset | basis | Row unit | Date |
|---|---|---|---|
| `chat` | `asks` | ask | asked_at |
| `chat` | `turns` | turn (ask_seq = 1) | asked_at |
| `chat` | `episodes` | episode | closed_at |

**Chat dimensions:** topic, subject (topic parent), product, brand, category, outcome,
failure_reason, contact (staff only), customer, sales_agent (customer's agent), channel, month,
week, day, weekday, hour, audience, source era.
**Chat measures:** asks, turns, episodes, contacts (distinct), failed, failure_rate, answered_empty,
handovers, asked_again, resolved_rate, median_reply_seconds.

Two engine additions, each earned by a real question in section 1.3 (and nothing else):

1. **Count-distinct measures.** `engine.py` only sums (:614, :654). "How many dealers used the
   bot" cannot be summed across rows. `Column` gets `agg: "sum" | "count_distinct"` (default sum).
2. **Ratio measures.** "Failure rate by week" = failed / non-noise turns per cell, computed after
   the grouped query from two summed measures. A `ratio_of = (num_key, den_key)` field on `Column`.

The route (`api/v1/order_management/report_ask.py:178`, permission
`order_management.orders.view` :209, 422 on unknown params) gains `dataset` and the chat filter
params; chat datasets require the new grant (section 6). MCP `crm_report_ask` (`catalog.py:810`,
presenter `presenters.py:1880`, reply builder :3238) gains `dataset` and a chat presenter.

### 4.3 How a free question becomes dataset + dims + measures + filters

The parser already does this for sales (`report_ask.settle` :564, `route_args` :837). It gets a
prompt addendum (a prompt migration like `alembic/versions/report_engine_0001_prompt.py`; the
owner moves the production label) teaching:

| Customer words (masked) | dataset / basis | group_by | measure | filters | period |
|---|---|---|---|---|---|
| "top 10 products asked this month" | chat / asks | product | asks | role=named | this month |
| "what did the bot fail on yesterday" | chat / turns | failure_reason, then topic | failed | outcome in failed | yesterday |
| "most common questions about `<brand>`" | chat / asks | topic | asks | brand=`<brand>` | default: last 30 days |
| "failure rate by week since Sept" | chat / turns | week | failure_rate | | 1 Sep to today |
| "products asked >5 times with no sales" | chat / asks x sales / delivered | product | asks, amount | asks > 5, amount = 0 | given |

The model picks words from a closed list; the route validates each against the catalogue and
answers 422 on anything else, exactly as today. The model never writes SQL at this step.

### 4.4 The flexibility ladder

```
 Step 1  Catalogue ask        one dataset, 1 group, filters           exact, cheap, cached
   |  cannot express it?
 Step 2  Composed pivots      2 groups (pivot), period vs period,     still catalogue SQL
   |                          chat x sales join on product/customer
   |  still cannot?
 Step 3  Guarded SQL          LLM writes ONE SELECT over curated      read-only role,
                              views; validated; 5 s; 500 rows         staff only
```

| | Step 1 | Step 2 | Step 3 |
|---|---|---|---|
| Who can use | everyone with the grant (dealer: own data, DEALER keys) | same | staff with `chatbot.analytics.sql` only |
| Accuracy risk | none beyond labels | none beyond labels | the model's SQL may be wrong; reply says "(computed)" and shows the plan in words |
| Cost | ms | ms | one LLM call + query |
| Build size | M | M | L |

**When to climb**: the dashboard logs every question that fell through to step 3 or was refused.
Step 3 ships only after step 1+2 have run for a month AND the fall-through log shows repeated real
questions they cannot answer. That keeps the "simplest thing" rule: the evidence earns the layer.

**Step 3 guardrails** (none of these exist today: `database.py:11-20` has no statement timeout and
no read-only engine):

- A second SQLAlchemy engine bound to role `ai_ro_nocost` (exists on the cluster, unused: read
  only, 5 s `statement_timeout`, `search_path ai_nocost`). A migration creates schema `ai_nocost`
  holding only VIEWS: `chat_asks`, `chat_turns`, `chat_episodes`, `chat_items`, and the existing
  dataset bases as views (no cost columns; `ai_ro_cost` / `ai_cost` holds the cost variant, used
  only for users with `purchase_orders.cost`). GRANT SELECT on those views only.
- No view exposes message text, summaries, phone numbers or names of contacts (contact appears as
  customer name only, and only on the staff variant).
- The generated statement is parsed (`sqlglot`), must be a single SELECT, may reference only the
  view names, gets `LIMIT 500` appended, and runs in a READ ONLY transaction.
- Company scoping: views filter on `current_setting('app.company_ids')`, set per transaction from
  the caller's grants (fail closed: unset = no rows).
- Every step-3 run is written to the audit log with the SQL and the asker.

### 4.5 Dashboard (CRM Reports screen)

Three `ReportDefinition`s on the existing Reports screen (`registry.register`, like
`definitions/sales_yearly.py`), so filters, saved views, publish and xlsx export come free:

1. **Chat overview**: tiles (asks, contacts, failure rate, handovers, coverage) + weekly trend of
   asks and failure rate.
2. **Top asks**: pivot topic x month, and product x month (role = named), with a "shown" toggle.
3. **Bot failures**: failure_reason x topic, plus the turn list (turn id link to the existing
   trace screen; no message text in the grid, the trace screen keeps its own permission).

### 4.6 WhatsApp answer format

One fact per line; a head line naming the unit; filters as `Label: value` lines; the period; the
coverage line last. Same shape as `_report_ask` (`presenters.py:3238`).

```
Top 5 products by asks
Period: 2026-09-01 to 2026-09-30
1. <product A> 182 asks
2. <product B> 140 asks
3. <product C> 97 asks
4. <product D> 88 asks
5. <product E> 61 asks
and 412 more
Total 3,904 asks
Coverage: 97% labelled, from bot traces
```

```
Bot failures by reason
Period: 2026-10-07
Cannot do: 6 turns
Missed lookup: 3 turns
Technical: 1 turn
Failure rate: 4.2%
Top failed topic: order status, invoice copy
```

No customer wording, no names of who asked (unless staff asked "by customer", then customer
names, which the sales report already prints for the same grant).

---

## 5. Access control and PII

| Rule | How |
|---|---|
| Analytics needs a grant | New reveal grant key `chatbot.analytics` (granted per contact via `contact_field_reveal_service`, like `sales_orders.sales_report`): added to `DOMAIN_GRANT_REQUIRED` (`lanes/business/answer.py:1174`), enforced at the lane (`lanes/business/__init__.py:1635-1655`) AND re-checked at the route (as `api/v1/order_management/report_ask.py:291-298` does for the sales reveal grant). |
| Per-contact breakdowns | `contact` dimension only with `chatbot.analytics.contacts`; otherwise rejected at the catalogue like a dealer-outside key. |
| Dealers | Audience `DEALER` (`ask.py:46` pattern): chat dataset filtered to the asker's own contact/customer; dims limited to topic, product, brand, category, month. "What did I ask most" works; nobody else's data. |
| Salesmen | Filtered to customers they are agent of (same `customer_agent` filter the sales ask uses). |
| Never exposed | message text, episode `summary` / `last_user_message`, phone, contact names, the labeller's proposal text. Not in fact tables, not in views, not in replies. |
| Company scope | Fact rows carry the customer's company via the customer join; the dataset declares `scope = company` like the sales datasets. |
| Public repo | Fixtures and golden sets use synthetic data; the golden "expected answers" file holds counts only. |
| LLM data flow | The labeller sends customer text to the configured provider, the same provider the parser already sends every message to. No new data leaves the system. |

---

## 6. Later phase: AI assistant becomes a chatbot console

Today the console (`POST /api/v1/system/chatbot/console/turn`, `console_service.run_console_turn`
:444) is a dry run that impersonates a contact. The unification: a staff user types into the CRM
chat panel, the turn goes in as `ingress = crm` with the STAFF identity (`users.respond_contact_id`
links a user to a contact, `models/user.py:72`), runs the same engine and the same grants, and is
recorded (not a dry run, but excluded from customer analytics by `audience = staff`). The
`ai_assistant_service.py` loop is retired once its tools are covered. Separate lane, after
analytics is proven, because it touches auth and the engine's ingress contract.

---

## 7. Evaluation

1. **Golden question set** (~40 questions): each with the expected structured route (dataset,
   group, measure, filters, period) and the expected answer computed by hand-written SQL over a
   SYNTHETIC fixture (seeded turns, frames, histories). Pytest asserts parser-route and numbers.
   Run in CI on every change to the catalogue or prompt; prompt changes also run it against the
   provider in a nightly job (not in CI).
2. **Label accuracy audit**: per taxonomy version, a stratified sample of 300 asks (by subject and
   by low confidence) relabelled by a larger model, disagreements reviewed by the owner or a
   delegate in a small review screen. Target: topic agreement >= 90% at subject level, >= 80% at
   leaf; product resolution precision >= 95% (a wrong product is worse than "unresolved").
3. **Outcome rule tests**: one fixture turn per outcome row in section 2.2 (trace JSON in, outcome
   out). These are pure functions: fast unit tests.
4. **Coverage tests**: the coverage line computed on a fixture spanning eras.
5. **Regression**: existing `report_ask` sales tests must stay green (the dataset axis defaults to
   `sales`); the reviewer's kill test covers the grant checks.
6. **Reconciliation**: nightly check that live-era fact rows = closed non-test turns (by day);
   mismatch alerts the operator.

---

## 8. Phased plan

| Lane | Size | Depends on | Delivers | What the owner sees |
|---|---|---|---|---|
| **CA-1 Facts from traces** | M | none | `chat_ask_facts`, `chat_ask_items`, `chat_episode_facts`, flattener, sweep, one-time load since 5 Sep, outcome rules | Reports screen "Chat overview" with counts, failure reasons, products named/shown (no topics yet) for 5 Sep on |
| **CA-2 Taxonomy + labeller** | M | CA-1 | discovery run, owner review of v1, `chat_topics`, nightly labeller, resolver pass, audit sample screen | "Top asks" by topic; "most common questions"; better product coverage |
| **CA-3 Chat dataset in the bot** | M | CA-2 | dataset axis, count-distinct + ratio measures, route + MCP + presenter, prompt addendum, grants, golden set | Owner texts the bot "top questions this week" and gets the answer |
| **CA-4 Backfill** | M | CA-2 | backfill extractor, run table, era coverage line | History from 17 Apr in every chart, with coverage lines |
| **CA-5 Composed + guarded SQL** | L | CA-3, a month of fall-through log | pivots, chat x sales join, `ai_nocost` views + role engine, sqlglot validation, audit | Free questions beyond the catalogue (staff only) |
| **CA-6 Assistant into console** | L | CA-3 | `ingress = crm` staff turns, retire the assistant loop | One chat for staff and customers |

CA-4 can run in parallel with CA-3. Each lane: one branch, one PR.

### Risks

| Risk | Mitigation |
|---|---|
| Labels drift or are wrong | frozen taxonomy versions, audit sample, confidence-based review |
| Product under-resolution (592 of ~9,000 today) | resolver pass in CA-2; "unresolved" reported, never guessed |
| Readers misread the era gap as a trend | coverage line in every answer; charts shade era C |
| PII leak via free text | no text column anywhere in facts or views; review checklist item |
| Step-3 SQL wrong or slow | gated by evidence, read-only role, 5 s, row cap, "(computed)" marker |
| Episode trim loses history | snapshot in the same transaction as the close (CA-1) |

### Owner rulings (9 Oct 2026)

The owner approved the design and ruled every open question as recommended. They are recorded
here as decisions; a change needs a new ruling.

| # | Question | Ruling |
|---|---|---|
| 1 | Who approves the taxonomy (v1 and later bumps)? | The owner approves v1; after that a named delegate reviews proposals monthly and approves version bumps. |
| 2 | Is the "resolved" proxy acceptable? | Yes, shown labelled "(estimate)". An optional thumbs-up quick reply is added later only if the proxy proves misleading. |
| 3 | Who gets `chatbot.analytics`? | Managers and admins in full; salesmen scoped to their own customers; dealers get the self-view only. |
| 4 | Which model runs the backfill? | The parser's model, plus the 10% audit on a larger model; switch only if the audit misses its targets (section 7). |
| 5 | Are staff replies reliably in `chat_histories`? | Measure it in CA-1 (outgoing rows without `chatbot_turn_id` during human-intervened spans) before any staff metric is promised. |
| 6 | Stop trimming episodes instead of snapshotting? | Keep the 20-episode trim; snapshot analytic fields only (no summary text) in the same transaction as the close. |
| 7 | Build step 3 (guarded SQL) at all? | Decide after CA-3 has run for a month, from the fall-through log. |
| 8 | Language / sentiment in the labeller? | Language yes (same call); sentiment no until a question needs it. |

Also settled with the approval: CA-1 ships first and alone (facts from traces + "Chat overview",
no topics), as a separate lane; topics follow in CA-2 once taxonomy v1 is approved.
