# PLAN: prod edge lockdown (HA-SEC-EDGE)

Status: in progress (small fix track: compose/deploy config + runbook, no migration, no RBAC change; reviewer + security-reviewer)

## Problem (public checks, 14 Oct)

- Prod pgbouncer port 6432 accepts TCP connects from the internet; only the DB password protects it.
- The CRM origin answers direct internet traffic, so the Cloudflare proxy can be bypassed.
- MCP :8765 is plain http on the raw origin address, called by n8n.
- No WAF rules in any repo; `TRUSTED_PROXIES` does not list Cloudflare.

## Scope

1. Consumer inventory of 6432 / 5432 / 6379 / 8765 from outside the CRM box (PR body).
2. Repo-side config so the next deploy publishes nothing on a public interface.
3. Owner runbook `documentation/runbooks/prod-edge-lockdown.md`.

No prod access from this lane. No addresses or hostnames in the repo: placeholders only.
