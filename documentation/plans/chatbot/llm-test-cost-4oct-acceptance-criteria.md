# UAC: LLM-TEST-COST

1. With `LLM_CASSETTE_MODE` unset (prod), the provider calls OpenAI exactly as before; no cassette file is read or written.
2. `LLM_CASSETTE_MODE=record` calls the live model once and writes a cassette keyed by hash(model, messages, schema, tools) under `LLM_CASSETTE_DIR` (outside git by default).
3. `LLM_CASSETTE_MODE=replay` returns the recorded response with no network call; a miss raises an error naming the missing key and never calls the live model.
4. Cassette files hold no API key, base URL credentials or auth headers.
5. The turn trace records `cached_tokens` from `usage.prompt_tokens_details.cached_tokens` (0 or absent-safe).
6. `LLM_BASE_URL` / `LLM_MODEL_OVERRIDE` change the client base URL / model only when set; unset leaves behaviour unchanged.
7. Scenario runners default to replay; `--live` opts into live calls.
