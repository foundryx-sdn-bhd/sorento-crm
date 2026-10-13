# PLAN: MODEL-PRICES (13 Oct 2026)

Status: building (M, LEAD pattern; one additive view-only migration for CU-6; mock skipped per owner)

UAC: `model-prices-acceptance-criteria.md`. Card: `CARD-model-prices-13oct.md`.

## Backend

1. Shared cost SQL: `app/services/analytics_connector/prices.py` gains
   `cost_sql(u: str, p: str) -> CostSql` (three SQL fragments: `cost_usd`, `priced`,
   `cached_price_missing`) given the usage-log and price-table qualifiers. `views.py` LLM_CALLS_V
   (`:206-237`) is rewritten to interpolate it (same semantics; `tests/analytics_connector/test_turn_costs.py`
   stays green). Usage endpoints use it through `sqlalchemy.literal_column` over an outer join of
   `llm_model_prices` on `model`.
2. Usage endpoints (`app/api/v1/system/ai_assistant.py:593-817`, schemas in `app/schemas/ai_assistant.py`):
   - summary: + `cost_usd` (float, sum of priced), `unpriced_calls` (int)
   - by-day: + `cost_usd`, `unpriced_calls`; bucket = Malaysia day
     (`date_trunc('day', timezone('Asia/Kuala_Lumpur', timezone('UTC', created_at)))`)
   - top-users, top-contacts: + `cost_usd`, `unpriced_calls`
   - recent-queries: + `model`, `cost_usd` (nullable), `priced`, `cached_price_missing`
3. Model prices routes (same router, `/system/ai-assistant/model-prices`): GET list, GET
   `/unpriced`, POST (201, 409 duplicate), PUT `/{id}` (model immutable), DELETE `/{id}` (204).
   View routes `system.ai_assistant_settings.view`, writes `.edit`. ORM writes so default-on audit applies.
4. Pending action `llm_model_price.delete` in `app/services/record_actions.py` (pattern
   `chatbot_status_word.delete`, `:265-283`), permission `.edit`, `WINDOW_DESTRUCTIVE`.

## Frontend

5. Page `app/(protected)/system-management/ai-assistant/model-prices/` modelled on
   `system-management/chatbot-status-words/` (list + modal + service + hook + `useDeferredAction`
   toast delete). Menu item after Usage in `config/menu.config.tsx:1064-1068`, plus
   `config/menu.config.test.ts:186`.
6. Usage page (`ai-assistant/usage/page.tsx`): Cost column, Cost summary card, cost columns in Top
   users / Top contacts, chart tooltip cost; Time via `formatDateTimeInMalaysia`
   (`lib/helpers.ts:451`). Cost formatting = one helper (4 dp, NULL -> "—").
   Types in `ai-assistant/services/aiUsageService.ts`.

## Tests (tester, red first)

BE pytest (Postgres, own seed): cost function on the card examples; view == endpoint cost; each
usage endpoint's new fields; MYT day bucketing; prices CRUD + 409 + 422 + 403 + audit rows;
unpriced list; pending action registered + deletes. FE vitest: usage page cost cells, tooltips,
summary "N calls not priced", MYT time; model prices list, unpriced Add price prefill, error state;
menu path.

## Cost truthfulness (CU rules, crew scope add 13 Oct, option (a) agreed)

7. `cost_sql` gains `cache_unknown` (price row exists AND `cached_tokens` IS NULL): cost NULL,
   priced false. `unpriced_calls` counts only no-price / cached-price-missing calls; totals add
   `unknown_cache_calls`. Recent queries add `cache_unknown` and `feature`.
8. `backfill.py:16-31` drops the `COALESCE(..., 0)`; `chatbot/usage.py:107` writes NULL when the key
   is absent; the 9 other `AIAssistantUsageLog(` writers set `cached_tokens` from the provider result.
9. New alembic migration (view-only, additive): calls `views.ensure_cost_views` and re-grants the RO
   role as `ctc_0001_turn_costs.py:64-66` does; `cache_unknown` appended LAST to `llm_calls`
   and `turn_costs` (CREATE OR REPLACE VIEW cannot reorder).

## Owner prod steps

1. After deploy, run once on prod (idempotent; crew copy at `crew/state/migrations/MODEL-PRICES.sql`):

       UPDATE ai_assistant_usage_logs SET cached_tokens = NULL
        WHERE feature = 'chatbot_parser' AND cached_tokens = 0
          AND created_at < '2026-10-04 14:12:49';

2. Add prices for the models the owner wants costed on System Management > AI Assistant > Model prices.
