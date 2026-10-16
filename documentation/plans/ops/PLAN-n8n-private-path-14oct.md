# PLAN: n8n reaches the CRM over the tailnet, not Cloudflare (HA-N8N-PRIVATE-PATH)

Status: in review (small fix track: docs + runbook only, no code, no migration, no auth change).

## Why

On 26 Aug the Cloudflare to origin leg stalled. Bot reply p95 went from 10 s to 327 s while the
origin itself answered in 32 ms. n8n reaches the CRM API through Cloudflare (about 151 workflow
files), MCP on the raw origin over plain http :8765, and the CRM DB on pgbouncer :6432
(`sorento-crm-db` credential, BL-049).

## Decision

- Tailscale between the CRM box and the n8n box (WireGuard documented as the alternative).
- n8n containers get `extra_hosts`: `<CRM_DOMAIN>` and `crm-private.internal`, both to
  `${CRM_PRIVATE_IP}` from the n8n `.env`. The API keeps its hostname, TLS and certificate, so no
  workflow file changes; MCP nodes and the DB credential move from the raw origin to
  `crm-private.internal`.
- Failover (Tier 1): the one `CRM_PRIVATE_IP` line.
- After 48 h on the tailnet, the public 6432 / 8765 Hostinger rules are deleted.

## Repo side

Checked, no change needed: `ai_assistant_mcp_url` (`sorento_crm_backend/app/config.py:333`) is an
env-only setting defaulting to `localhost`, already hostname-configurable; the release's MCP check
(`.github/workflows/deploy.yml:2116`) curls loopback on the box. So no code and no tests.

## Deliverable

`documentation/runbooks/n8n-private-path.md` (owner-run), plus two cross-references in
`documentation/runbooks/prod-edge-lockdown.md` (the Origin CA option in step 5.2 would break this
path; the clear-text MCP follow-up now points at the new runbook).

## Acceptance

Full list: `n8n-private-path-acceptance-criteria.md` alongside this plan.

- From inside an n8n container, a request to `https://<CRM_DOMAIN>/api/v1/...` shows the peer as
  `<CRM_TS_IP>` with no `cf-ray` header (runbook step 5.1).
- One chatbot turn on the test number answers, with the CRM nginx log showing `<N8N_TS_IP>`
  (step 5.2).
- Every prod step has a check and a rollback line; no real address, hostname of an origin or
  secret is in the repo.
