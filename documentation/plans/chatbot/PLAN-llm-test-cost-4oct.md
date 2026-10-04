# PLAN: LLM-TEST-COST (cut test LLM spend: prompt size, cache, record/replay)

Status: planning. Track: small fix track (backend/test infra only, no migration, no auth/RBAC change, no owner-visible behaviour).

Lane: crew-lane LLM-TEST-COST, 4 Oct 2026.

## Goal

Cut OpenAI spend from testing without changing prod accuracy. No local model. Owner hand tests stay on real gpt-5.4-mini.

## Deliverables (in order)

1. Prompt size report (why the parser prompt is ~33k tokens/parse), prompt-cache friendliness check, `cached_tokens` logged into the turn trace. Prompt content is NOT changed in this lane; trims are a separate ask.
2. Record/replay cassette layer at the `llm_provider` seam, `LLM_CASSETTE_MODE=record|replay|off` (default off).
3. Scripted scenario runners use replay by default with a `--live` flag.
4. Optional `LLM_BASE_URL` / `LLM_MODEL_OVERRIDE` read at the seam, only when set.

Details filled in below as the lane progresses.
