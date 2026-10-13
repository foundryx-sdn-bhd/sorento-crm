# UAC: MODEL-PRICES (13 Oct 2026)

Card: `CARD-model-prices-13oct.md` (approved 13 Oct, all recommendations: Q1a Q2a Q3a Q4a Q5a).

## Model prices screen

- MP-1 Menu System Management > AI Assistant shows "Model prices" right after "Usage", path
  `/system-management/ai-assistant/model-prices`, gate `system.ai_assistant_settings.view`.
- MP-2 Priced list shows every `llm_model_prices` row: model, input, cached input ("—" when NULL),
  output (USD per 1M tokens), note, updated (Malaysia time).
- MP-3 Add price (modal): model (exact string), input, cached input (optional), output, note. Save
  creates the row; list refreshes; toast. Duplicate model -> 409 "A price for <model> already exists."
  Negative or missing input/output -> 422, modal stays open with the message.
- MP-4 Edit (modal, same fields, model read-only) updates prices + note; `updated_at` moves.
- MP-5 Delete = deferred countdown toast (pending action `llm_model_price.delete`, 10 s window,
  Cancel); no confirm dialog; server hard-deletes when the window lapses.
- MP-6 Unpriced models list: distinct non-null `ai_assistant_usage_logs.model` with no price row,
  all time, with calls and last seen (Malaysia time), most calls first. "Add price" opens the MP-3
  modal with the model filled and locked. After saving, the model leaves this list and joins MP-2.
- MP-7 Write routes (POST/PUT/DELETE, pending action) need `system.ai_assistant_settings.edit`;
  a view-only user gets 403 and sees no Add / Edit / Delete / Add price buttons.
- MP-8 Each create / edit / delete writes an `audit_logs` row (entity `llm_model_prices`).
- MP-9 A failed list load shows the error text in that card, never a spinner forever.

## Cost on the usage page

- UC-1 One shared function produces the per-call cost SQL; the `analytics_connector.llm_calls` view
  and every usage endpoint use it. Formula `(uncached x input + cached x cached_input + completion x
  output) / 1e6`; uncached = prompt - cached (OpenAI, Gemini), = prompt (Anthropic).
- UC-2 Unpriced model -> cost NULL, `priced=false`. Cached tokens > 0 with NULL cached price -> NULL,
  `cached_price_missing=true`. Never 0.
- UC-3 Recent queries has a "Cost (USD)" column: 4 decimals ("$0.0048"); NULL shows "—" with tooltip
  "No price for <model>" (or "No cached-input price for <model>").
- UC-4 Summary row gains a "Cost (USD)" card = sum of priced calls in range (4 dp), with
  "N calls not priced" under it when N > 0.
- UC-5 Top users and Top contacts gain a "Cost (USD)" column (sum of priced calls, 4 dp; "—" when
  every call is unpriced; tooltip "N calls not priced" when some are).
- UC-6 Tokens-per-day chart tooltip shows cost per day (4 dp).
- UC-7 Editing a price changes the cost shown for past calls of that model (read-time pricing).

## Malaysia time

- TZ-1 Recent queries Time renders through `formatDateTimeInMalaysia`: API `2026-10-13T14:23:33`
  shows `13/10/2026, 10:23 pm`.
- TZ-2 Tokens-per-day buckets are Malaysia days: a call at 2026-10-13 17:00 UTC counts on 2026-10-14.

## Real examples (dev `sorento_cagent_stack`)

| prompt / cached / completion | model | cost |
|---|---|---|
| 35,805 / 35,072 / 366 | gpt-5.4-mini (0.75 / 0.075 / 4.50) | $0.0048 (0.00482715) |
| 35,726 / 0 / 363 | gpt-5.4-mini | $0.0284 (0.028428) |
| 5,064 / 0 / 38 | gpt-5.4-mini | $0.0040 (0.003969) |
| 3,005 / 0 / 690 | gemini-3.5-flash (no price) | — "No price for gemini-3.5-flash" |
