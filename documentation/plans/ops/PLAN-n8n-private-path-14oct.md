# PLAN: n8n reaches the CRM over the tailnet, not Cloudflare (HA-N8N-PRIVATE-PATH)

Status: in progress (small fix track: docs + runbook, no migration, no auth change).

Why: on 26 Aug the Cloudflare to origin leg stalled. Bot reply p95 went from 10 s to 327 s while
the origin itself answered in 32 ms. n8n reaches the CRM API through Cloudflare, MCP on the raw
origin over plain http :8765, and the CRM DB on pgbouncer :6432.

Deliverable: `documentation/runbooks/n8n-private-path.md` (owner-run).
