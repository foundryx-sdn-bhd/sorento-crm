# PLAN: per-turn token + cost dataset in the Claude analytics connector (CHAT-TURN-COST)

Status: Review. Track: standard (additive migration: two columns, one index, one price table, two views).

Owner request 13 Oct: make per-chatbot-turn token consumption and cost queryable through the
analytics connector (`chat_list_datasets` / `chat_describe_dataset` / `chat_query`), so the owner can
analyse cost per turn / conversation / contact / category and latency in claude.ai.

## What exists today (measured, cited)

| Fact | Where |
| --- | --- |
| The "AI usage" screen (Feature, Principal, Query, ms, Tokens) reads `ai_assistant_usage_logs`: one row per LLM call, `feature`, `model`, `provider`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `response_time_ms`, `contact_id` (internal `respond_contacts.id`), `chatbot_turn_id`. | `app/models/ai_assistant.py:122-167`, `app/api/v1/system/ai_assistant.py` |
| The chatbot writes ONE row per live turn there, `feature = "chatbot_parser"`, carrying `chatbot_turn_id`. No `cached_tokens` column exists, so the prompt-cache hit is lost on that table. | `app/services/chatbot/usage.py:40-87`, `engine.py:1575-1600` |
| The parser call's split (`prompt_tokens`, `cached_tokens`, `completion_tokens`), `prompt_version`, `domain` (= parser `domain_hint`), `intent` (= `intent_hint`) and `message_type` are on the turn's trace, stage `understood`, under `facts`. | `engine.py:4544-4570`, `trace.py:155-180` |
| The casual lane's clarifier makes a second LLM call on casual turns; its tokens are recorded NOWHERE (the call returns content only). | `lanes/casual.py:300-328` |
| No embedding call happens inside a chatbot turn (the business lane avoids `sub-get-rag` on purpose). | `lanes/business/services.py:15-20`, `fetch.py:185` |
| No model price table exists anywhere. | grep `per_1m|cost_usd|token_price` over `app/` |
| The connector's datasets are a whitelist catalog over read-only views in schema `analytics_connector`, recreated by `ensure_views` from a migration and from `bootstrap_env`. | `analytics_connector/datasets.py:62-170`, `views.py:166-180` |
| `ai_assistant_usage_logs.chatbot_turn_id` has no index. | `\d ai_assistant_usage_logs` |

## Decisions (crew relay, 13 Oct)

- Q1 phone: pending the owner. Card Q3 (9 Oct) excludes phone numbers from every dataset and a test
  enforces it; this lane keeps that. Only an explicit owner ruling reverses Q3.
- Q2 (a): the casual lane's clarifier call is logged as `feature = chatbot_clarifier` with the turn id.
- Q3 (a): `llm_model_prices` table, seeded ONLY with the three prices the repo already cites with a
  source and date (`scripts/chatbot_eval/budget.py`): gpt-5.4-mini, gpt-4.1-mini, claude-sonnet-5-5.
  Every other model is unpriced (`cost_usd` NULL, `priced` false) until the owner adds its row.
- Q4 (a): no embedding column; `llm_calls` (count) instead.

## Build (as shipped)

1. Migration `ctc_0001_turn_costs` (additive): `ai_assistant_usage_logs.cached_tokens`,
   `chatbot.chat_ask_facts.prompt_version`, both backfilled once from the turn's `understood` trace
   facts; table `llm_model_prices` (uuid id, unique `model`) + seeds; the two views; index on
   `ai_assistant_usage_logs.chatbot_turn_id` built CONCURRENTLY.
2. Writers: `chatbot/usage.record_turn_llm_usage` (parser + clarifier rows, with `cached_tokens`);
   `llm_call.capture_usage()` collects the usage of a call whose caller only returns content (the
   clarifier); the CA-1 fact writer stores `prompt_version`.
3. Views: `analytics_connector.llm_calls` (one row per CHATBOT usage-log row, priced; staff-side AI
   spend stays out because the CRM gates it behind `system.ai_assistant_settings.view`, security review) and
   `analytics_connector.turn_costs` (one row per live turn). Category, outcome, topic, episode and
   prompt version come from the CA-1 fact row (`chatbot.chat_ask_facts`), never from the trace.
4. `cost_usd = (uncached * input + cached * cached_input + completion * output) / 1e6`, where
   uncached is `prompt - cached` for OpenAI/Gemini and `prompt` for Anthropic (Anthropic reports
   cache reads outside `prompt_tokens`, `llm_provider.py`). Missing cached price = input price.
5. Catalog entries document every column (`chat_describe_dataset`); new column type `number`.

### `turn_costs` columns

`id`, `created_at` (MYT), `contact_id`, `contact_respond_id`, `contact_name`, `customer_name`,
`episode_id`, `inbound_text` and `reply_text` (OTP-masked), `status`, `stage`, `branch_kind` (lane),
`domain`, `intent`, `topic_code`, `topic_label`, `outcome`, `prompt_version`, `model`, `llm_calls`,
`prompt_tokens`, `cached_tokens`, `completion_tokens`, `total_tokens`, `llm_ms`, `latency_ms`,
`cost_usd`, `priced`.

### `llm_calls` columns

`id`, `created_at` (MYT), `feature`, `chatbot_turn_id`, `contact_id`, `contact_name`, `provider`,
`model`, `prompt_tokens`, `cached_tokens`, `completion_tokens`, `total_tokens`, `response_time_ms`,
`was_answered`, `cost_usd`, `priced`.

## Performance (sandbox Postgres 16, 100k synthetic live turns, 500 contacts)

| Query | Time |
| --- | --- |
| `count(*)` over `turn_costs` | 0.1 s |
| group by domain, intent, prompt_version with sum(cost), sum(tokens) | 1.2 s |
| newest-first page of 201 rows | 0.07 s |
| one contact's newest 201 | 0.12 s |
| `llm_calls` group by feature, model | 0.06 s |
| backfill `cached_tokens` / `prompt_version` (migration, once) | 2.7 s / 3.0 s |

A first version read the parser facts from `chatbot.turns.trace` per row: 3.4 s on 50k turns with
small traces, so it was replaced by the fact-row join. The connector's statement timeout is 10 s.

## Example rows (synthetic: this lane runs in a cloud sandbox with no access to dev data)

| id | contact_name | domain / intent | prompt_version | model | prompt / cached / completion | latency_ms | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3f1c...a2 | Ah Seng (Bengkel Maju) | stock / availability | 52 | gpt-4.1-mini | 38 214 / 36 864 / 312 | 4 120 | 0.00473 |
| 8b07...19 | Ah Seng (Bengkel Maju) | order / status | 52 | gpt-4.1-mini | 38 390 / 0 / 288 | 5 870 | 0.01582 |
| c55e...0d | Mei Ling | casual / greeting | 52 | gpt-4.1-mini | 37 902 / 36 864 / 95 | 2 310 | 0.00425 |

The crew hand-test runs two real analyses against the test copy.

## Trigger for later machinery (not built)

Price history (`effective_from`) is built when a configured model's price actually changes; today one
row per model is the whole need.
