# PLAN: per-turn token + cost dataset in the Claude analytics connector (CHAT-TURN-COST)

Status: Build. Track: standard (additive migration: one column, one index, one price table, two views).

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

## Build

1. Migration (additive): `ai_assistant_usage_logs.cached_tokens INT NULL`, index on
   `chatbot_turn_id` (CONCURRENTLY), table `llm_model_prices (model PK, input_usd_per_1m,
   cached_input_usd_per_1m, output_usd_per_1m, note, updated_at)` seeded with the models the
   parser can be configured to; one-time backfill of `cached_tokens` for existing
   `chatbot_parser` rows from their turn's `understood` facts; `ensure_views` re-run.
2. `record_parser_usage` writes `cached_tokens`.
3. Two views + catalog entries:
   - `turn_costs`: one row per live (non-test) turn.
   - `llm_calls`: one row per LLM call (every feature in the usage log), with cost.
4. `cost_usd = ((prompt - cached) * input + cached * cached_input + completion * output) / 1e6`,
   prices joined from `llm_model_prices` by exact model name. A model with no price row gives
   `cost_usd = NULL` and `priced = false`, never a silent 0.
5. `chat_describe_dataset` documents every column (the catalog `Col.description`).

### `turn_costs` columns

`id` (turn), `created_at` (MYT), `contact_id`, `contact_respond_id`, `contact_name`, `customer_name`,
`episode_id` (the conversation frame holding the turn), `inbound_text` (OTP-masked), `reply_text`
(OTP-masked), `status`, `stage`, `branch_kind` (lane), `message_type`, `domain_hint`, `intent_hint`,
`prompt_version`, `model`, `llm_calls`, `prompt_tokens`, `cached_tokens`, `completion_tokens`,
`total_tokens`, `llm_ms`, `latency_ms`, `cost_usd`, `priced`.

### `llm_calls` columns

`id`, `created_at` (MYT), `feature`, `chatbot_turn_id`, `contact_id`, `contact_name`, `provider`,
`model`, `prompt_tokens`, `cached_tokens`, `completion_tokens`, `total_tokens`, `response_time_ms`,
`was_answered`, `cost_usd`, `priced`.

## Example rows (synthetic: this lane runs in a cloud sandbox with no access to dev data)

| id | contact_name | domain_hint / intent_hint | prompt_version | model | prompt / cached / completion | latency_ms | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3f1c...a2 | Ah Seng (Bengkel Maju) | stock / availability | 52 | gpt-4.1-mini | 38 214 / 36 864 / 312 | 4 120 | 0.00473 |
| 8b07...19 | Ah Seng (Bengkel Maju) | order / status | 52 | gpt-4.1-mini | 38 390 / 0 / 288 | 5 870 | 0.01582 |
| c55e...0d | Mei Ling | casual / greeting | 52 | gpt-4.1-mini | 37 902 / 36 864 / 95 | 2 310 | 0.00425 |

The crew hand-test runs two real analyses against the test copy.

## Questions (crew-ask on the PR)

Q1 phone, Q2 clarifier tokens, Q3 price source + seed list, Q4 embedding column. Answers recorded
here when they arrive.

## Trigger for later machinery (not built)

Price history (`effective_from`) is built when a configured model's price actually changes; today one
row per model is the whole need.
