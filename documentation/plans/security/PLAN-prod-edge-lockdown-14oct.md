# PLAN: prod edge lockdown (HA-SEC-EDGE)

Status: in review, PR #1688 (small fix track: compose/deploy config + runbook, no migration, no RBAC change; reviewer + security-reviewer)

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

## Decisions

- SSH 22 stays open to everyone, key-only, root password off, fail2ban with a short ban (owner
  ruling 14 Oct): the release job SSHes from GitHub-hosted runners. Tailscale for the release job
  is a follow-up (runbook, last section).
- `TRUSTED_PROXIES` stays the private ranges. Cloudflare's ranges are never trusted: every
  Cloudflare customer sends from them, so a Worker could plant a typed address (security review).
  The host nginx takes the client from `CF-Connecting-IP` instead (runbook step 5.3).
- MCP 8765 has no inbound credential and registers write tools
  (`sorento_crm_mcp/sorento_crm_mcp/record_actions.py:115-177`), so it closes tonight with 6432.
  Inbound auth on MCP is a follow-up, blocker-class for prod.
- pgbouncer 6432 keeps its current binding until BL-049 moves n8n's raw SQL into the CRM; the
  provider firewall limits it to the n8n host meanwhile.

## Repo changes

- Every compose file publishes on `127.0.0.1` only (except the all-in-one stack's frontend edge),
  pinned by `sorento_crm_backend/tests/test_prod_edge_bindings.py`.
- No public address in deploy files; `start.sh` fails fast without a DB host instead of falling
  back to a hard-coded one.
- The origin address is replaced by `<CRM_ORIGIN>` in docs and tests that carried it.
