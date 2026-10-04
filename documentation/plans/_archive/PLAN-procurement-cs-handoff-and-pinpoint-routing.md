# PLAN: Procurement CS Handoff Stage + Per-Salesman Pin-Point Routing

**Status:** Phase 1 + Phase 2 code complete (2026-06-10). Backend merged (migration 231, `RespondContactCsRouting` model, pin-point resolver override in `form_sla_service`, `CsRoutingService` + contact CS-routing endpoints, procurement `_finalize_request`/process/close + routes, 2 RBAC slugs). FE: contact CS-Assignment section + procurement process/close buttons/dialogs/badges, off mocks against live API. Tests green: pytest 11 new (45 incl. regression), vitest 2, FE prod build OK, tsc clean. **Remaining (manual, D12 - user's task before the flow works end-to-end):** configure the `customer_service` team_set under the `purchase_request` agent (tier-1 + members), an SLA policy with tier-1, and the two `FormSLAConfig` rows (`start_event="approved"`, `resolve_event="resolved"`). Phase 3 (code review) pending.

RBAC note (refinement of D11): process/close use **shared workflow slugs** `procurement.purchase_requests.process` / `.close` covering both PR and sponsorship - matching the existing shared `procurement.purchase_requests.send_for_approval` precedent and the single shared router. Not 4 slugs. CRUD slugs stay separated per request_type.
**Owner:** Claude + Jayson
**Created:** 2026-06-10

## Problem

Two gaps:

1. **No CS stage after PR/sponsorship approval.** Complaints have an after-approve
   customer-service form-SLA stage with "Processed by CS" / "Closed" actions and
   corresponding statuses. Purchase requests and sponsorship forms emit the
   `approved` form-SLA event on approval (`procurement_service.submit_approval`,
   action="approved") but **no CS stage spawns** - there is nothing for CS to pick
   up, process, and close.

2. **CS assignment is round-robin only.** When a form-SLA CS stage spawns it calls
   `get_next_assignee(agent_id, team_id)` = pure round-robin. There is no way to
   route a specific salesman's forms to a specific CS PIC. The business need
   (reference spreadsheet): each salesman (a `respond_contact`) is handled by a
   designated CS person - contact-u/LEENA/contact-o→AISHAH, BRENDON/ERIC→MARYAM, etc.
   Many salesmen → one CS; one salesman → exactly one CS per use_case.

## Solution summary

1. Add a **per-salesman → CS PIC pin** ("pin-point" assignment) as an **override
   layer** over the existing round-robin. New table `respond_contact_cs_routing`.
   At CS-stage spawn the resolver looks up a pin for `(respond_contact_id, use_case)`;
   if a valid pin exists → assign that user; otherwise → existing round-robin.
   Every failure mode degrades to round-robin - approve never errors.

2. Replicate the complaint **CS handoff flow** onto PR + sponsorship: a one-stage
   form-SLA CS config that starts on `approved` and resolves on `resolved`, plus
   "Processed by CS" → status `processed_by_cs` and "Mark as closed" → status
   `closed` actions on both detail screens.

3. Config UI lives **on the respond-contact detail page** - a CS-Assignment section
   with two rows (Purchase Request, Sponsorship Form), each a dropdown of
   `Round-robin (default)` + CS team members.

## Decision record (grilling session 2026-06-10)

| # | Decision |
|---|----------|
| D1 | **Override + fallback, not replace.** Resolver order: (1) look up pin by `(respond_contact_id, use_case)`; (2) valid pin → assign that user; (3) else → existing round-robin `get_next_assignee`. Approve flow stays resilient; pins roll out incrementally. |
| D2 | **Dedicated table** `respond_contact_cs_routing(id, respond_contact_id FK, cs_pic_user_id FK→users, use_case, is_active, timestamps)`, unique `(respond_contact_id, use_case)`. Not a column on `respond_contacts` (keeps routing config out of contact identity), not `ContactAgentAccess` (no semantic overload). |
| D3 | Override resolver lives at the **single choke point** `form_sla_service._start_for_config` (around the `get_next_assignee` call, ~line 414). All form-SLA stages flow through it; complaint, PR, sponsorship all benefit from one code path / one test surface. |
| D4 | **`use_case` NOT NULL, strict per-use-case rows.** Resolver only consults the table for `use_case ∈ {purchase_request, sponsorship_form}`. **Complaint never reads the table** → complaint always round-robin (admin won't configure complaint pins). No NULL-default "applies-everywhere" semantics - a row says exactly what it routes. |
| D5 | **CS PIC must be a tier-1 team member** of the use_case's CS team. Validate at config-save (reject pinning a non-member / inactive user) AND at resolve-time (stale pin → fall back to round-robin + `logger.warning`, never 500). |
| D6 | **Escalate normally past tier 1.** Override pins only the *initial* tier-1 assignee; tier-2+ escalation uses the existing round-robin escalation team. Override swaps only the `get_next_assignee` result; `agent_id` / `team_set_code` / `current_tier=1` set identically; round-robin cursor **not advanced** on override; `_notify_assignee` fires the same. |
| D7 | **No salesman flag.** Pinning is a conscious admin act per contact. Config surface = respond-contact detail page (`user-management/contacts/[id]`), new `ContactCsRoutingTable.tsx` beside `ContactAccessAgentsTable.tsx`. CS candidates = tier-1 `TeamMember`s of the `customer_service` team_set under the **`purchase_request` agent** (same team for both use_cases; selections independent - PR→member X, sponsorship→member Y allowed). |
| D8 | **One-stage CS form-SLA config per use_case** (simpler than complaint's two-stage). `emit_event` matches `start_event` directly, so a config with `start_event="approved"`, `resolve_event="resolved"` spawns at approve and closes on finalize - no `next_config_id` chain. Two rows: `source_entity_type` = `purchase_request` and `sponsorship_form`; `stage_code="customer_service"`, `agent_code="purchase_request"`, `team_set_code="customer_service"`. |
| D9 | **Mirror complaint CS handoff exactly.** Reuse status strings on `purchase_requests.status`: `approved` (CS stage active) → `processed_by_cs` or `closed`. Two actions, two endpoints (`/purchase-requests/{id}/process`, `/.../close`), each emits form-SLA `resolved` (closes the CS tracker) and sends a status-update message via the existing `send_text_or_template` choke point. Same strings for PR + sponsorship; `request_type` discriminates. |
| D10 | **WhatsApp template handling already covered.** Procurement send sites already route through `send_text_or_template(..., use_case=use_case_for_purchase_request(header))` (`procurement_service.py:3542,4948`, per PLAN-whatsapp-template-fallback D1/D6/D11). New process/close status messages reuse it - no closed-window gap, nothing extra to build. |
| D11 | **Separate FE screens + separate RBAC per request_type** (keep current split). New slugs (4): `procurement.purchase_requests.process` / `.close`, `procurement.sponsorship_forms.process` / `.close`. Replicate the process/close button block + status badges in both detail screens (`procurement-management/purchase-requests` + `.../sponsorship-forms`). |
| D12 | **Admin configures structural pieces** (the `customer_service` team_set under `purchase_request` agent, the SLA policy + tier-1, the two `FormSLAConfig` rows) via existing admin UI - same as complaint was set up. Code/migration scope = only the new table, RBAC slugs, status/endpoints, FE. No seed migration. |
| D13 | **No backfill, no live-reassign.** (15a) PRs approved before the CS stage is configured get no tracker - clean cutover, only new approvals enter CS. (15b) Re-pinning a salesman is forward-looking; existing trackers keep their assignee (pin applies at spawn only, like the round-robin cursor). |

## Resolver contract (D1 - D6, the core of this feature)

In `form_sla_service._start_for_config`, before calling `get_next_assignee`:

1. If `config.source_entity_type ∈ {purchase_request, sponsorship_form}` AND
   `contact_id` is not NULL:
 - `SELECT cs_pic_user_id FROM respond_contact_cs_routing WHERE respond_contact_id=:cid AND use_case=:uc AND is_active`
 - If a row exists AND `cs_pic_user_id` is an active member of `team_id`
     (the resolved tier-1 team): build the assignee dict
     `{id, email, name, respond_user_id}` for that user, **do not advance the
     round-robin cursor**, proceed.
 - Else (`contact_id` NULL, no row, stale/inactive/non-member pin): fall through.
2. Fall-through → `get_next_assignee(agent_id, team_id)` (round-robin), log a warning
   on the stale-pin case.
3. Everything downstream (tracker fields, due dates, `_notify_assignee`) identical
   to the round-robin path.

## Expected API contract (Phase 1 output)

```
# Pin CRUD (on the respond contact)
GET    /api/v1/.../contacts/{contact_id}/cs-routing
         → [{ use_case, cs_pic_user_id, cs_pic_name }]
PUT    /api/v1/.../contacts/{contact_id}/cs-routing/{use_case}
         body { cs_pic_user_id }  → upsert (validated against CS team membership)
DELETE /api/v1/.../contacts/{contact_id}/cs-routing/{use_case}
         → clear pin (revert to round-robin)
GET    /api/v1/.../cs-routing/candidates
         → [{ id, name, email }]  tier-1 members of purchase_request agent's
            customer_service team (feeds dropdown + save-time validation)

# CS handoff actions (mirror complaint)
POST   /api/v1/procurement/purchase-requests/{id}/process   body { note? }
POST   /api/v1/procurement/purchase-requests/{id}/close     body { note? }
POST   /api/v1/procurement/sponsorship-forms/{id}/process   body { note? }
POST   /api/v1/procurement/sponsorship-forms/{id}/close     body { note? }
```

## Reference (existing code this mirrors / touches)

- Complaint CS handoff: `complaints_service.py` `mark_processed_by_cs` (1264),
  `close_complaint` (1285), `_finalize_complaint` (1306, emits `resolved` at ~1396).
  Endpoints `complaints.py:1113` (`/process`), `:1143` (`/close`). FE
  `ComplaintDetail.tsx` (buttons ~245/269, dialogs ~468/514), `useComplaints.ts`
  (142/158), `lib/complaint-status.ts` (pill + label maps).
- Round-robin assignee: `user_service.AccessAgentService.get_next_assignee` (1352).
- Form-SLA spawn/resolve: `form_sla_service._start_for_config` (375),
  `_resolve_for_active` (486), `emit_event` (141, matches `start_event` directly at 191).
- Procurement approve moment: `procurement_service.submit_approval` (5463;
  status="approved" at 5502; `emit_form_event("approved")` at 5550). `contact_id`
  set at creation (4084), present at approve. `request_type` (model 322).
- Procurement send choke point: `procurement_service.py:3542,4948`.
- Respond-contact detail FE: `user-management/contacts/[id]/page.tsx` +
  `components/ContactAccessAgentsTable.tsx` (pattern to mirror).
- RBAC convention: `permission_registry.py:129-132`
  (`procurement.purchase_requests.*`, `procurement.sponsorship_forms.*`).

## Phase 1 - Frontend prototype (mocks only)

- [ ] Respond-contact detail page: `ContactCsRoutingTable.tsx` section - two rows
  (Purchase Request, Sponsorship Form), each dropdown `Round-robin (default)` + mock
  CS members. States: unset (round-robin), pinned, stale-pin warning, save/error.
- [ ] PR detail + Sponsorship detail screens: "Processed by CS" button (status
  `approved` + perm) and "Mark as closed" dropdown item, each opening an
  `AlertDialog` with optional note (mirror `ComplaintDetail`). Status badges for
  `processed_by_cs` / `closed`.
- [ ] Document the API contract above at the top of the new service file.
- [ ] Playwright MCP verification via sidebar; screenshot golden + edge states.
- [ ] No backend code, no tests yet.

## Phase 2 - Backend + wiring + tests

Backend:
- [ ] Migration: `respond_contact_cs_routing` table + unique `(respond_contact_id, use_case)`.
- [ ] Pin CRUD service + endpoints + `cs-routing/candidates` (tier-1 members of
  `purchase_request` agent's `customer_service` team) + save-time membership validation.
- [ ] Resolver override in `_start_for_config` (D1 - D6) - confined to PR/sponsorship use_cases.
- [ ] PR/sponsorship `process` + `close` service methods + endpoints (mirror
  `_finalize_complaint`): set status, emit `resolved`, send status message via
  `send_text_or_template`. 4 endpoints (PR + sponsorship × process + close).
- [ ] Status strings (`processed_by_cs`, `closed`) wired into procurement status
  handling + list/detail serialization.
- [ ] RBAC slugs (4): `procurement.{purchase_requests,sponsorship_forms}.{process,close}`.
- [ ] (Admin task, not code) configure agent/team, SLA policy, two `FormSLAConfig` rows.

Frontend:
- [ ] Replace mocks with real hooks/services/api-client; delete fixtures.

Tests (land here):
- [ ] pytest: resolver branches (valid pin / no pin / NULL contact / stale pin →
  round-robin / non-PR use_case ignored), cursor-not-advanced on override, pin CRUD
  happy/auth/validation, process+close happy/auth/validation, `resolved` closes tracker.
- [ ] vitest: CS-routing section states, process/close dialogs, status badges, input gating.
- [ ] Playwright e2e: pin a salesman → approve a PR → CS tracker assigned to pinned
  PIC → process/close round-trip.
- [ ] Playwright MCP re-verify against live stack.

## Phase 3 - Code review

- [ ] `/code-review` (or `ultra` if big) on the merged branch; fix findings.
- [ ] PR with Phase 1 screenshots, contract-vs-shipped check, PR-CHECKLIST.md pass.
