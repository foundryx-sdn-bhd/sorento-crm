# PLAN: Chat analytics CA-2, topic taxonomy + topic labelling

Status: Plan, HELD for an owner ruling on HOW topics are labelled (on the fly from the turn's
own parser output vs the designed nightly LLM labeller). No labeller is designed or built until
that ruling lands. Track: full (migration + new report definitions).

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
