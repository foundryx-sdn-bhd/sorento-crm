# PLAN: WA-MSG-TRIM - fewer outgoing WhatsApp messages

Status: in review, PR #1454 (small fix track: data migration only, no auth/RBAC, no new ingest surface)

## Why

Since 1 Oct 2026 Meta charges every outgoing WhatsApp message, service replies inside the 24h
window included (about US$0.014 each in Malaysia, passed through by Respond.io). The dev
snapshot (to 18 Sep) shows about 9,459 outgoing messages per 30 days. Owner (3 Oct 2026) wants
fewer of them. All three changes below are owner decisions; no behaviour card is needed.

## Changes

1. Compact stock view for every contact.
   - Global default stock visibility row (seeded detailed by migration 416): detailed -> compact.
     The code floor `DEFAULT_MODE` stays detailed: it is reached only when no default row
     exists (create_all test databases); the default row has no delete route, so a migrated
     database never falls back to it.
   - Access-type and contact policy rows with mode=detailed -> compact (access types too, or
     a contact on a detailed access type keeps the detailed view). mode=availability rows
     untouched.
   - Additive, idempotent alembic data migration with a working downgrade.
   - Compact keeps the zero-stock "try these instead" suggestions (owner, 3 Oct 2026):
     `inventory_service.py` suppresses them for `availability` only; AC-B14 amended in
     `documentation/plans/_archive/inventory/stock-visibility-policy-acceptance-criteria.md`.
2. n8n `sub-sendmsg` chunk limit 1800 -> 3900 chars (text), buttons body 1000 -> 1024
   (WhatsApp interactive body text limit). Split at line/paragraph boundaries only, as today.
   Lives in prod n8n (workflow aoydkG1dbItXR5jXFEQsP), not in this repo: applied by crew on
   3 Oct 2026 17:01 with the owner's go; this lane ships no n8n change.
3. Out-of-scope escalation sends ONE message instead of two ("please wait" + "routed to PIC"):
   "Routed to your PIC, they will reply shortly."

Rejected by the owner (not in scope): debouncing / merging inbound messages, moving
complaint/SLA notices off WhatsApp, removing Respond.io auto messages.

## Tests

Red-first: a pytest commit that fails on main, then the fix.
