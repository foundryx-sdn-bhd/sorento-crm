# PLAN: HA-MONITOR (outside uptime checks, heartbeats, Cloudflare switch)

**Status:** IN PROGRESS (small fix track: no migration, no auth/RBAC change, no new ingest surface).
Lane `HA-MONITOR`, part of the crew HA plan (Tier 0, owner approved 15 Oct).

## Journey

The owner wants to hear about an outage from a watcher that does not live on our own servers and
does not alert through our own WhatsApp path. On 11 Aug n8n answered `/healthz` while the bot was
dead for 18 h, so liveness alone is not enough: the real paths (scheduler tick, n8n DB read + MCP
call) must prove themselves by heartbeat. When Cloudflare itself is the outage (18 Nov 2025, the
dashboard login was down too), the owner needs an API-only switch that unproxies the Sorento hosts
and nothing else in the shared zone.

## Deliverables

1. Scheduler heartbeat: `HC_SCHEDULER_URL`, pinged by a real scheduled job; unset = no-op; never
   raises; short timeout; fire-and-forget.
2. n8n heartbeat workflow JSON + import steps under `documentation/runbooks/`.
3. `documentation/runbooks/monitoring.md` (Uptime Kuma monitors, watch the watcher, Healthchecks
   checks, alert routing, respond.io "webhook disabled" email alert, Cloudflare switch section).
4. `scripts/ops/cf-proxy.py status|off|on [--yes]`, allowlisted hosts only, dry run by default,
   unit tests with a mocked API.

## Out of scope

Any prod step (owner runs the runbook). No app behaviour change.
