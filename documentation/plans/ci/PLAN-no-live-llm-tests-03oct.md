# PLAN: no live LLM / messaging calls from the backend test suite

Status: in progress (small fix track: tests-only diff, no migration, no auth/RBAC change)

## Problem

`pytest` loads the developer's real backend `.env` (`app/config.py` `_resolve_settings_env_file`,
`model_config` `env_file`), which carries OPENAI_API_KEY and RESPOND_API_KEY. Nothing blocks
network calls: the chatbot parser has no global stub, and `llm_provider.resolve_api_key` falls
back to `settings.openai_api_key`. The opt-in `no_live_llm` fixture is unused under
`tests/chatbot`. A test that forgets its stub can spend the real key.

## Fix

1. `tests/_live_call_guard.py`, installed from `pytest_configure` (so every xdist worker gets
   it): wraps `HTTPTransport.handle_request` / `AsyncHTTPTransport.handle_async_request` of
   both `httpx` and `httpx2` (the fork openai 3.x and anthropic 1.x send through; a guard on
   `httpx` alone misses every SDK call), `urllib.request.OpenerDirector.open`, and
   `socket.getaddrinfo` as a backstop for any other client connecting directly. A request to
   any host under openai.com, anthropic.com, generativelanguage.googleapis.com, respond.io or
   graph.facebook.com raises `LiveExternalCallBlocked`. Every attempt is recorded and
   `fail_if_blocked()` fails the test at teardown, so app code that swallows the exception
   cannot pass silently. Mock transports (`httpx.MockTransport`, Starlette TestClient) are
   not `HTTPTransport`, so they are untouched.
2. `tests/conftest.py`: autouse `_no_live_external_calls`. Opt-out marker
   `@pytest.mark.live_external` lifts the guard for that test; such a test is skipped unless
   `CHATBOT_LIVE_LLM=1`.
3. Keys: the same fixture blanks OPENAI/ANTHROPIC/GEMINI/RESPOND keys on `settings` and in
   `os.environ` per test (unless opted in). Per test, not at import, because
   `app.main._load_env_file` reloads the dotenv with `override=True` when `app.main` is
   imported. Keys held in the `ai_assistant_config` DB row (prod-copy DB) are not blanked;
   the transport guard is what stops those.
4. Red-first test: `tests/chatbot/test_no_live_llm_guard.py` (DNS tripwire so the red run
   cannot reach a provider).

The older opt-in `no_live_llm` fixture (seam guard on `get_provider()`) stays; its
`allow_live_llm` marker now lifts only that seam guard.

Triggers for more machinery (not built now):
- `requests` or `aiohttp` imported by `app/`: wrap its send path (today the getaddrinfo
  backstop covers a direct connection, not one through a proxy).
- n8n webhooks (`N8N_WEBHOOK_URL`, `chatbot_retry_ingress_url`) are not blocked; n8n then
  messages via respond.io. Out of this lane's host list; blank or block them when a test is
  found reaching one.
