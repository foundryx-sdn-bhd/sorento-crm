# UAC: no live LLM / messaging calls from tests

- AC-1: an unstubbed chatbot parser call inside a test fails with `LiveExternalCallBlocked`
  naming the host, and makes no network connection.
- AC-2: an httpx or httpx2 (sync and async), urllib, or direct-DNS request to api.openai.com, api.anthropic.com,
  generativelanguage.googleapis.com, any `*.respond.io` host or graph.facebook.com is blocked in
  every backend test, with no per-module opt-in.
- AC-3: a blocked attempt that app code catches still fails the test at teardown.
- AC-4: during tests `settings.openai_api_key`, `anthropic_api_key`, `gemini_api_key`,
  `respond_api_key` (and their env vars) are empty, whatever the developer's `.env` holds.
- AC-5: `@pytest.mark.live_external` tests are skipped unless `CHATBOT_LIVE_LLM=1`; with it set
  the guard is lifted for that test only.
- AC-6: requests to other hosts and mock transports are unaffected.
