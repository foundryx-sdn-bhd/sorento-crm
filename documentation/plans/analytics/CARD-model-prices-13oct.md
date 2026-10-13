# CARD: MODEL-PRICES (behaviour card, 13 Oct 2026)

Lane MODEL-PRICES, size M, branch `crew/model-prices`. Owner asks (13 Oct): a screen to edit model
prices, the cost on `/system-management/ai-assistant/usage`, and Malaysia time on that page.

## Facts (verified on origin/main 1cb144cb5 and the shared dev DB `sorento_cagent_stack`)

- Prices: table `llm_model_prices` (ORM `LLMModelPrice`, `sorento_crm_backend/app/models/ai_assistant.py:185-210`).
  Dev holds one row: `gpt-5.4-mini` input 0.75, cached 0.075, output 4.50 USD per 1M.
- Cost math today exists only as SQL inside the analytics view `llm_calls`
  (`sorento_crm_backend/app/services/analytics_connector/views.py:206-237`).
- Usage page: `sorento_crm_frontend/app/(protected)/system-management/ai-assistant/usage/page.tsx`,
  API `sorento_crm_backend/app/api/v1/system/ai_assistant.py:593-817` (summary, by-day, top-users,
  top-contacts, recent-queries), all gated `system.ai_assistant_settings.view`.
- Menu: AI Assistant group in `sorento_crm_frontend/config/menu.config.tsx:1052-1079`.
- Time bug: usage page formats with `date-fns format(new Date(naiveUtc))`
  (`usage/page.tsx:66-72`), so a naive UTC string reads as browser-local time. The shared CRM
  formatter is `formatDateTimeInMalaysia` (`sorento_crm_frontend/lib/helpers.ts:451`, used in 162 files).
- Audit: every mapped table is audited by default (`sorento_crm_backend/app/services/audit_service.py:1-5,100-103`);
  `LLMModelPrice` does not opt out, so ORM create/edit/delete from a staff request writes audit rows with no new code.

## Rules

1. Model prices screen: new menu item "Model prices" under System Management > AI Assistant, next to
   Usage. View gate `system.ai_assistant_settings.view`, add/edit/delete gate
   `system.ai_assistant_settings.edit` (both exist; no new permission).
2. Priced list: model, input, cached input, output (USD per 1M), note, updated. Add / Edit in a modal
   (CRUD standard). Delete = hard delete via the standard deferred countdown, no confirm dialog.
3. Unpriced models: distinct non-null `ai_assistant_usage_logs.model` with no price row, all time,
   with call count and last seen (MYT); each row has "Add price", which opens the same modal with the
   model name filled and locked.
4. Model name must match the usage log exactly (`gemini/gemini-2.5-flash` and `gemini-2.5-flash` are
   two models). Duplicate model -> 409 with a clear message. Prices >= 0; cached input optional.
5. Cost per call = ONE shared Python function that emits the SQL expression; the `llm_calls` view and
   every usage endpoint call it. No second formula:
   `(uncached x input + cached x cached_input + completion x output) / 1,000,000`;
   OpenAI/Gemini uncached = prompt - cached; Anthropic uncached = prompt.
6. Unpriced model -> cost NULL, shown as "—" with tooltip "No price for <model>", never 0. Priced
   model with cached tokens but no cached price -> NULL, tooltip "No cached-input price for <model>".
7. Cost is computed at read time from the CURRENT price row: editing a price re-prices all history
   (same as the analytics views today).
8. Usage page: a "Cost (USD)" column in Recent queries; cost beside tokens in every place the page
   shows a token total (see Q3).
9. Every timestamp on the usage page shows Malaysia time via `formatDateTimeInMalaysia`; the
   per-day chart buckets by Malaysia day, not UTC day.
10. Never-stuck: a failed load shows the error text in that card; a failed save keeps the modal open
    with the server message in a toast.

## Real examples (shared dev DB, `ai_assistant_usage_logs`)

| Time (UTC -> MYT) | Feature | Model | prompt / cached / completion | Cost |
|---|---|---|---|---|
| 07:47:50 -> 3:47 pm, 13 Oct | chatbot_parser | gpt-5.4-mini | 35,805 / 35,072 / 366 | (733x0.75 + 35,072x0.075 + 366x4.5)/1e6 = **$0.00482715** |
| 07:47:19 -> 3:47 pm, 13 Oct | chatbot_parser | gpt-5.4-mini | 35,726 / 0 / 363 | (35,726x0.75 + 363x4.5)/1e6 = **$0.028428** (cache miss, 5.9x) |
| 07:47:23 -> 3:47 pm, 13 Oct | spec_search | gpt-5.4-mini | 5,064 / 0 / 38 | (5,064x0.75 + 38x4.5)/1e6 = **$0.003969** |
| 02:50:46 -> 10:50 am, 7 Oct | ai_extract | gemini-3.5-flash | 3,005 / 0 / 690 | unpriced -> "—", "No price for gemini-3.5-flash" |
| 03:17:15 -> 11:17 am, 17 Sep | ai_extract | gpt-4o-transcribe | 0 / 0 / 0 | unpriced -> "—" (billed per audio minute) |

Unpriced list on dev today (all time): gpt-4o-mini 190, gemini-2.5-flash 151, gemini-3.5-flash 38,
gpt-4o-transcribe 34, gemini/gemini-2.5-flash 19, gpt-4o 17, gpt-4.1-mini 8, gpt-transcribe 7,
gemini-2.5-pro 1, gpt-4o-mini-transcribe 1.

## Edge cases

- Window mixes priced and unpriced calls (dev last 7 days: 9 gpt-5.4-mini + 6 gemini-3.5-flash) -> Q1.
- Rows with NULL model (none on dev) -> cost "—", "No price for unknown model"; not in unpriced list.
- Transcribe models report 0 tokens; pricing them per token would show $0. Listed as unpriced anyway (Q5).
- Deleting a price makes that model's history unpriced again ("—"), not $0.

## Questions

Q1. Total cost when the range holds unpriced calls. (a) sum of the priced calls, with a line
"N calls not priced" under it; (b) show "—" for the whole total. **Rec (a)**: the known spend stays
visible and the gap is named, never hidden as 0.

Q2. Decimals. (a) 4 decimals everywhere ($0.0048, totals $1.2345); (b) rows 4, totals 2.
**Rec (a)**: a dev week totals about $0.10, which 2 decimals would round to $0.10 or $0.00 per user.

Q3. Where cost appears. (a) summary card "Cost (USD)", Cost column in Top users, Top contacts and
Recent queries, plus cost per day in the chart tooltip; (b) only the summary card and Recent queries.
**Rec (a)**: brief says cost wherever the page shows token totals.

Q4. Time format. The shared formatter shows "13/10/2026, 3:47 pm" (no seconds); the page shows
seconds today ("Oct 13, 14:23:33"). (a) shared format as is; (b) shared format plus seconds (a new
variant in `lib/helpers.ts`). **Rec (a)**: same format as the rest of the CRM, as the brief asks.

Q5. Unpriced list scope. (a) all time, with call count and last seen, transcribe models included;
(b) only models seen in the last 30 days. **Rec (a)**: 10 models on dev, short enough; old models
still have history that reads "—".
