# Form SLA Tracking - Implementation Plan & Handoff

Status: shipped + verified end-to-end. This doc captures the design, what landed, the gotchas hit during verification, and the residual work for the next contributor.

## Goal

Attach SLA trackers to four forms - `stock_inquiry`, `purchase_request`, `sponsorship_form`, `complaint` - independent of the existing **Conversation SLA Tracking** flow. Each tracker has response_time / resolution_time / assignee / tier escalation / in-app + email notification, configurable per form type from the FE.

Plus: add a **Reject** action at the `submitted` state of `purchase_request` / `sponsorship_form` (before "Send for Approval") that sends a Respond.io update message to the contact.

## User-locked design decisions

1. **Storage** - reuse existing `conversation_sla_tracking` table (already has `source_entity_type` / `source_entity_id`); do not create a parallel form-tracker table.
2. **Trigger config** - per-form-type DB table (`form_sla_configs`) with typed columns (start_event / respond_event / resolve_event), FE-configurable.
3. **Multi-stage chain** - chain via `next_config_id` (stage 1 resolves → orchestrator auto-spawns stage 2 tracker).
4. **Multi-event respond/resolve** - comma-separated events stored in `respond_event` / `resolve_event` (e.g. `"approved,rejected"`); FE shows multi-checkbox dropdown.
5. **Notification channels** - in-app + email via `NotificationService.create_with_channel_preferences` (NOT n8n + Respond.io comments - that's the conversation SLA path).
6. **Permission for reject-at-submitted** - same slug as Send for Approval (`procurement.purchase_requests.send_for_approval`).

## Critical files (back end)

- `sorento_crm_backend/app/models/sla.py` - `FormSLAConfig` model
- `sorento_crm_backend/app/services/form_sla_service.py` - `FormSLAOrchestrator` + `emit_form_event(...)` helper
- `sorento_crm_backend/app/services/sla_service.py` - existing `ConversationSLATrackingService`; orchestrator delegates `mark_responded` / `mark_resolved` to its `update_tracking`
- `sorento_crm_backend/app/services/procurement_service.py` - emit hooks at every state-transition site for stock_inquiry + purchase_request + sponsorship_form; new `reject_submitted` method
- `sorento_crm_backend/app/services/complaints_service.py` - emit hooks for complaint transitions
- `sorento_crm_backend/app/services/portal_service.py` - emit `submit` event on `submit_draft` (covers all 4 form types from portal)
- `sorento_crm_backend/app/api/v1/sla/__init__.py` - mounts `form_sla_config` + `sla_tracking` (with `by-source`)
- `sorento_crm_backend/app/api/v1/sla/form_sla_config.py` - CRUD routes
- `sorento_crm_backend/app/api/v1/sla/sla_tracking.py` - added `GET /by-source?source_entity_type&source_entity_id` returning all trackers for a form row
- `sorento_crm_backend/app/api/v1/procurement/purchase_requests.py` - `POST /{id}/reject-submitted` route
- `sorento_crm_backend/app/scheduler/task_scheduler.py` - registers `form_sla_overdue_scan` handler
- `sorento_crm_backend/app/rbac/permission_registry.py` - new permission slugs
- `sorento_crm_backend/app/schemas/sla.py` - `FormSLAConfig*` schemas
- `sorento_crm_backend/app/schemas/procurement.py` - `RejectSubmittedRequest` schema

## Critical files (front end)

- `sorento_crm_frontend/app/(protected)/sla-management/_shared/formSLAService.ts` - service + types + event option map
- `sorento_crm_frontend/app/(protected)/sla-management/_shared/FormSLATrackingTab.tsx` - DataGrid list of trackers per form (mirrors `ConversationSLATrackingList` look)
- `sorento_crm_frontend/app/(protected)/sla-management/_shared/FormDetailWithSLATabs.tsx` - Tabs wrapper (Details / SLA Tracking) used by all 4 form detail pages
- `sorento_crm_frontend/app/(protected)/sla-management/form-sla-config/page.tsx` + `components/FormSLAConfigList.tsx` + `components/FormSLAConfigDialog.tsx` - admin UI
- `sorento_crm_frontend/app/(protected)/procurement-management/purchase-requests/components/PurchaseRequestDetail.tsx` - Reject button + AlertDialog (visible in `draft`/empty `approval_status` when user has `procurement.purchase_requests.send_for_approval`)
- `sorento_crm_frontend/app/(protected)/procurement-management/purchase-requests/services/purchaseRequestService.ts` - `rejectSubmittedPurchaseRequest(id, reason)`
- `sorento_crm_frontend/app/(protected)/procurement-management/purchase-requests/[id]/page.tsx`, `sponsorship-forms/[id]/page.tsx`, `stock-inquiries/[id]/page.tsx`, `complaint-management/complaints/[id]/page.tsx` - each wraps the existing detail in `FormDetailWithSLATabs`
- `sorento_crm_frontend/config/menu.config.tsx` - sidebar entry `Form SLA Configuration` under SLA Management group

## Migrations applied

| Rev | File | What |
|-----|------|------|
| 178 | `178_form_sla_configs.py` | Create `form_sla_configs` table |
| 179 | `179_seed_form_sla_overdue_scan.py` | Seed `form_sla_overdue_scan` row in `scheduled_tasks` (every 2 min) |
| 180 | `180_drop_respond_contact_unique.py` | Drop legacy `respond_contact_id_unique` constraint, replace with partial unique only for active conversation-only trackers |
| 181 | `181_drop_legacy_respond_contact_unique_index.py` | Drop second legacy partial-unique index `uq_conversation_sla_tracking_respond_contact_id` |

## Architecture

### Orchestrator (`FormSLAOrchestrator`)

Single funnel `emit_event(source_entity_type, source_entity_id, event_name, *, contact_id=None, actor_user_id=None)`:
- Loads all active `form_sla_configs` for `source_entity_type`.
- For each config, evaluates whether `event_name` matches `start_event` / `respond_event` / `resolve_event` (comma-separated lists supported via tokenized split).
- On `start` → spawns a new `ConversationSLATracking` row (skip if active tracker for this stage already exists). Resolves tier-1 assignee via `AccessAgentService.get_team_id_by_tier(agent_id, 1, team_set_code=config.team_set_code)` + `get_next_assignee` (round-robin). Fires in-app + email via `NotificationService.create_with_channel_preferences`.
- On `respond` → `is_responded=True` (delegates to existing `ConversationSLATrackingService.update_tracking`).
- On `resolve` → `is_resolved=True`. If `config.next_config_id` is set, immediately starts the next stage tracker.

All exceptions inside `emit_event` are caught + logged. Defensive `db.rollback()` on top-level config-lookup failure so a missing migration doesn't leave a poisoned session for the parent transaction.

### Overdue scanner

Registered as scheduled-task handler `form_sla_overdue_scan` (interval 2 min). Method `FormSLAOrchestrator.scan_overdue_and_escalate()`:
- Query `ConversationSLATracking` where `is_resolved=false` AND `source_entity_type IN form_types` AND (`due_at < now` OR `due_at_resolution < now`).
- Skip if already escalated since `current_tier_started_at` (idempotent).
- Bump `current_tier += 1`. Look up next-tier team via `AccessAgentService`. Round-robin new assignee. Recompute `due_at` / `due_at_resolution` from policy tier KPIs. Reset `escalated_at` / `escalation_reason`. Write `escalation` event log. Fire `_notify_assignee(kind="escalated")`.

Conversation SLA path is untouched - it still escalates via n8n hitting `/integration/escalate`. The new scanner filters on `source_entity_type` to only act on form trackers.

### Emit hook coverage (where `emit_form_event` fires)

**Stock inquiry** (`procurement_service.py`):
- `submit_inquiry_for_project_sales` → `submit`
- `project_sales_approve_inquiry` → `project_sales_approve`
- `project_sales_reject_inquiry` → `project_sales_reject`
- `purchasing_reject_inquiry` → `purchasing_decide`
- `update_and_reply` (purchasing reply path, line ~3107 sets status=responded) → `purchasing_respond`
- `create_inquiry` → `submit` when status lands non-`new` (covers external API)
- `_resubmit_rejected_inquiry` → `submit` when resulting status non-`new`

**Purchase request / sponsorship form** (`procurement_service.py`):
- `create_external_request` → `submit`
- `set_pending_approval` → `send_for_approval`
- new `reject_submitted` method → `reject_submitted`
- `submit_approval` (public approval token consume) → `approved` / `approval_rejected`

**Complaint** (`complaints_service.py`):
- `create_complaint` → `submit`
- `update_complaint_and_reply` (line ~914 sets status=responded) → `technical_team_response`
- `decide_complaint` → `approved` / `rejected`

**Portal** (`portal_service.py`, `submit_draft`):
- All 4 form types → `submit` after status flip. **This is the actual entry point for portal submissions** - necessary because portal sets status directly and bypasses the API state-transition methods.

The orchestrator's `_start_for_config` is idempotent (skips if active tracker for the stage already exists), so multiple emit sites covering the same logical transition is safe.

## form_sla_configs schema

```
id              uuid pk
source_entity_type text  not null  -- stock_inquiry | purchase_request | sponsorship_form | complaint
stage_code      text  not null    -- e.g. "project_sales", "purchasing", "main"
policy_id       uuid  fk sla_policies.id  not null
agent_code      text  not null    -- AccessAgent.code (e.g. "lead_time_enquiries")
team_set_code   text  nullable    -- AgentTeam.code (which team set within the agent)
start_event     text  not null    -- comma-separated allowed
respond_event   text  nullable    -- comma-separated allowed
resolve_event   text  nullable    -- comma-separated allowed
next_config_id  uuid  fk form_sla_configs.id  nullable  -- chain to next stage
is_active       bool  default true
created_at, updated_at  timestamps
```

Partial unique index `uq_form_sla_configs_type_stage_active` on `(source_entity_type, stage_code) WHERE is_active = true` - allows re-creating after deactivation.

## Multi-event support

Backend orchestrator splits `start_event` / `respond_event` / `resolve_event` on `,` and trims tokens. So a complaint config can have `resolve_event = "approved,rejected"` - both events resolve the tracker. FE dialog uses a `MultiEventSelect` Popover-with-Checkbox component (`FormSLAConfigDialog.tsx`); list view renders each event as a separate chip via `EventChips` helper (`FormSLAConfigList.tsx`).

## Allowed events per form type

Hardcoded in `formSLAService.ts:FORM_SLA_EVENT_OPTIONS`. Source of truth is the back end emit hook list - any event added to FE must also be emitted by a service transition, or the dropdown choice will silently no-op.

```
stock_inquiry: submit, project_sales_approve, project_sales_reject, purchasing_decide, purchasing_respond
purchase_request / sponsorship_form: submit, send_for_approval, reject_submitted, approved, approval_rejected
complaint: submit, technical_team_response, approved, rejected
```

## Reject-at-submitted

- Route: `POST /api/v1/procurement/purchase-requests/{request_id}/reject-submitted`, body `{ rejection_reason: string (min 1) }`. Gated by new permission `procurement.purchase_requests.send_for_approval`.
- Service: `PurchaseRequestService.reject_submitted(request_id, rejection_reason, actor_user_id)`. Validates `approval_status` empty/`draft`, sets `approval_status='rejected'` + `approval_comments=rejection_reason` + `approved_at=now` + `approved_by=actor`. Calls existing `_notify_contact_on_approval_rejected(header)` (Respond.io message). Emits `reject_submitted` SLA event.
- FE: Reject button rendered in `PurchaseRequestDetail.tsx` next to "Change to pending approval" when `approval_status` is empty/`draft` and user has `procurement.purchase_requests.send_for_approval`. Opens `AlertDialog` with mandatory `rejection_reason` textarea + destructive Confirm.

## Permissions

Added to `permission_registry.py` (seeded into DB via startup `sync_permissions(db)` or manual `python -c "from app.rbac.permission_registry import sync_permissions; sync_permissions(SessionLocal())"`):

- `procurement.purchase_requests.send_for_approval` - gates Send for Approval AND Reject-submitted (same scope).
- `sla_management.form_sla_config.view`
- `sla_management.form_sla_config.manage`

Admin / superadmin role bypass active in `UserPermissionService` - admins inherit all known permissions, no manual grant needed.

## FE: SLA tab list (DataGrid)

`FormSLATrackingTab.tsx` mirrors `ConversationSLATrackingList` look - columns: Stage, Policy, Tier, Assigned To, Initiated At, Due (response), Due (resolution), Time elapsed, Response (time-remaining or response-time), Resolution (time-remaining or resolution-duration), Status, Agent. Click row → `router.push('/sla-management/conversation-sla-tracking/{id}')`. Reuses the existing detail page (Tracking Information / Response time / Resolution time collapsibles + Event Log tab + Test Override dialog) since trackers live in the same table - zero duplication.

Empty state guides admin to Form SLA Configuration page.

## Verification (already completed in previous session)

End-to-end run via Playwright MCP - login, configure 5 stages (one per form, plus stock_inquiry chain stage 2), exercise all 4 forms:

- **Complaint**: submit → tracker spawned → `technical_team_response` → responded → `approved` → resolved.
- **Stock inquiry chain**: submit → `project_sales` stage tracker (assignee = person18@example.com). `project_sales_approve` → stage 1 resolved + **stage 2 auto-spawned** for `purchasing` team set with new tier-1 assignee. `purchasing_respond` → stage 2 responded. `purchasing_decide` → stage 2 resolved.
- **Purchase request reject (UI)**: Reject button visible on draft, AlertDialog mandatory reason, confirm → `approval_status=rejected`, comments persisted, SLA tracker resolved. "Change to pending approval" → resubmit; status flips back to pending.
- **Sponsorship form reject (UI)**: same flow with different team set (`project_sales_cc`), different tier-1 assignee (person15@example.com).
- **Email**: 5/5 in-app sent, 3/5 emails sent (person18@example.com x2 stages + person23@example.com). 2 email failures were SMTP DNS errors from local network - not code issue. Web-push fails are expected (VAPID not configured).
- **Portal**: tested via `PortalService.submit_draft` end-to-end - backfill confirmed for SI-20260510-0001 and SI-20260510-0002.

## Gotchas hit during build (do not re-tread)

1. **`respond_contact_id` had two legacy unique constraints** (`respond_contact_id_unique` named constraint + partial `uq_conversation_sla_tracking_respond_contact_id` index). Both forced one tracker per contact, blocking multi-stage chains and multi-form-per-contact. Fixed via migrations 180 + 181. Conversation-tracker uniqueness is now enforced at the service layer (existing `create_tracking` active-conflict check) plus a new partial unique only for active conversation-only trackers.

2. **Permissions weren't auto-seeded into DB** when added to `PERMISSION_REGISTRY` - startup runs `sync_permissions(db)` only on first boot for fresh tenants. After adding new slugs, run `sync_permissions(db)` once or restart the app. FE `useHasPermission` hits `/users/me/permissions` which returns the union - admin's "all known slugs" path won't include slugs that aren't in `user_permissions` table.

3. **Portal submit bypasses both `create_inquiry` and `submit_inquiry_for_project_sales`** - `PortalService.submit_draft` mutates `row.status` directly and commits. Without an explicit emit hook there, no SLA tracker would spawn for portal-submitted forms. The hook is in `submit_draft` after `_post_submit_notify`.

4. **Multi-stage chain spawn** runs inside the same `_resolve_for_active` call. If the next config requires an agent/team that's not configured, the spawn raises - caught at the per-config level, logged, but the parent resolve still committed. So previous stage stays resolved even if next stage spawn fails. That's intentional - fix the agent/team config and re-fire by manually calling `emit_form_event(db, ..., "next_stage_start_event", ...)`.

5. **SLA policy `response_hours` / `resolution_hours` are integers**. To exercise overdue escalation in tests with the actual scheduler (every 2 min), the lowest you can go is 1 hour. For faster verification, use the existing `Test override` dialog on the tracker detail page to backdate `current_tier_started_at` (recomputes due_at to past now).

6. **`format_duration` helper expects milliseconds**. Backend `response_time` / `resolution_duration` are stored as Decimal hours - multiply by `3_600_000` before passing to the helper (already done in `FormSLATrackingTab.tsx`).

7. **`NotificationService.create()` has a buggy idempotent branch** - references `send_in_app` / `send_email` / `send_web_push` vars that don't exist in that scope (line ~50). Use `create_with_channel_preferences()` instead, which we do.

8. **Comma-separated event matching** - orchestrator splits on `,` and trims. FE `MultiEventSelect` joins with `,` (no space). Don't re-add spaces or both ends will mismatch.

## Residual / suggested follow-ups

- **Tighten existing Send-Approval-Link route** - `set_pending_approval` and `send_approval_link` currently rely on `get_current_user` only (no `require_permission`). Plan called for tightening both to `procurement.purchase_requests.send_for_approval`. Deferred to avoid breaking existing flows; should be a separate PR with stakeholder sign-off.
- **Per-form "Back to form" navigation** from the SLA detail page. Currently clicking a tracker row in the form's SLA tab navigates to `/sla-management/conversation-sla-tracking/{id}` and the detail page's back button goes to the conversation SLA list. Could thread a `?back={form_url}` query param.
- **CRUD for `agent_code` selection in the FE dialog is a free-text Input** - should be a Select populated from `/api/v1/user-management/access-agents`. Same for `team_set_code` (depends on selected agent).
- **Multi-tenant** - orchestrator + scanner use `DEFAULT_TENANT_ID`; plug in real tenant resolution when that lands.
- **Email delivery channel reliability** - production SMTP unreachable from dev network during verification (DNS error). Make sure prod SMTP is configured before relying on form SLA email channel.
- **Web-push** - disabled (`VAPID not configured`); enable if push notifications are wanted alongside in-app + email.
- **Backend tests for form SLA** - none added in this scope. Suggested: `tests/test_form_sla.py` covering `emit_form_event` for each (form_type, event), chain spawn, reject-at-submitted, and the overdue scanner. Existing pytest baseline: 240 passed / 20 failed (pre-existing); after my changes: 242 / 18 (net +2). No regressions in SLA / forms / procurement tests.
- **Backfill helper for existing rows** - for any form rows submitted before this feature shipped, run:

```python
from app.database import SessionLocal
from app.services.form_sla_service import emit_form_event
from app.models.procurement import StockInquiry, PurchaseRequestHeader
from app.models.complaints import Complaint

db = SessionLocal()
# Stock inquiries past "new"
for inq in db.query(StockInquiry).filter(StockInquiry.status != "new").all():
    emit_form_event(db, "stock_inquiry", str(inq.id), "submit", contact_id=inq.contact_id)
# Complaints
for c in db.query(Complaint).all():
    emit_form_event(db, "complaint", str(c.id), "submit", contact_id=c.contact_id)
# PR + Sponsorship
for pr in db.query(PurchaseRequestHeader).all():
    emit_form_event(db, pr.request_type or "purchase_request", str(pr.id), "submit", contact_id=pr.contact_id)
db.close()
```

`_start_for_config` is idempotent (skips if active tracker exists), so this is safe to re-run.

## Activation steps for an admin in a fresh environment

1. Apply migrations: `alembic upgrade head` (must reach at least 181).
2. Start backend with scheduler enabled (default). Confirm `form_sla_overdue_scan` is in `scheduled_tasks` and `enabled=true`.
3. Sync permissions if upgrading existing DB: `python -c "from app.database import SessionLocal; from app.rbac.permission_registry import sync_permissions; sync_permissions(SessionLocal())"`.
4. Ensure SMTP_* env vars are set so email channel works. (`STORAGE_*`, etc. unrelated.)
5. Create at least one `SLAPolicy` with tier 1 (and tier 2/3 if escalation is wanted) via SLA Management → SLA Policies.
6. Configure `AccessAgent` + `Team` + `TeamMember` rows for each agent/team_set/tier you intend to use (User Management → AI Agents and Teams).
7. Navigate SLA Management → **Form SLA Configuration**. Click `Add stage`. Pick form type, stage code, SLA policy, agent_code, team_set_code, and select start/respond/resolve event(s). For multi-stage forms (stock_inquiry), create stage 1 first, then stage 2, then edit stage 1 and set `next_config_id` to stage 2.
8. (Optional) Set custom email/SMTP and verify test users receive `New SLA assignment` email on form submit.

## Reference data (existing repo state)

Access agents available out of the box:

- `complaint` - set `complaint` (3 tiers), set `customer_service` (1 tier)
- `lead_time_enquiries` - set `project_sales` (1 tier), set `purchasing` (3 tiers, multi-team), set `purchasing_c` (3 tiers), `customer_service` (1 tier), `customer_service_c` (3 tiers), `project_sales_c` (1 tier)
- `purchase_request` - set `project_sales` (3 tiers), `project_sales_cc` (3 tiers), `customer_service` (1 tier), `customer_service_c` (3 tiers)
- Plus: `general_enquiries`, `incoming_stock_enquiries`, `marketing_form`, `order_enquiries`, `conversation_analysis`

Currently seeded SLA policies: `NORMAL` (T1 72h/72h, T2 24h/24h, T3 24h/24h), `TEST_FAST` (1h all tiers, used for verification).

## Recommended runtime flow (per form)

| Form | Stage | Start event | Respond event | Resolve event | Chain |
|------|-------|-------------|---------------|---------------|-------|
| stock_inquiry | project_sales | `submit` | - | `project_sales_approve, project_sales_reject` | → purchasing |
| stock_inquiry | purchasing | `project_sales_approve` | `purchasing_respond` | `purchasing_decide` | - |
| purchase_request | main | `submit` | `send_for_approval` | `approved, approval_rejected, reject_submitted` | - |
| sponsorship_form | main | `submit` | `send_for_approval` | `approved, approval_rejected, reject_submitted` | - |
| complaint | main | `submit` | `technical_team_response` | `approved, rejected` | - |

(Different agent_code / team_set_code per stage as appropriate. The above is what was used in verification.)

## API surface (for n8n / external integrators)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/sla-management/form-sla-config` | List stage configs (filter: `source_entity_type`, `is_active`) |
| `GET /api/v1/sla-management/form-sla-config/{id}` | Single config |
| `POST /api/v1/sla-management/form-sla-config` | Create |
| `PUT /api/v1/sla-management/form-sla-config/{id}` | Update |
| `DELETE /api/v1/sla-management/form-sla-config/{id}` | Hard delete (clears `next_config_id` back-refs first) |
| `GET /api/v1/sla-management/conversation-sla-tracking/by-source?source_entity_type&source_entity_id` | All trackers for a form row (multi-stage chain) |
| `POST /api/v1/procurement/purchase-requests/{id}/reject-submitted` | Reject before send-for-approval (mandatory `rejection_reason`) |

The conversation SLA `n8n integration/escalate` endpoint is unchanged. The form SLA escalation runs entirely server-side via `form_sla_overdue_scan` scheduled task - no n8n involvement required.

## Quick smoke test (post-deploy)

```bash
# 1. Migrations applied
alembic current   # should show 181_drop_legacy_respond_contact_unique_index or later

# 2. New routes registered
curl -s http://localhost:8000/openapi.json | jq -r '.paths | keys[]' | grep -E "form-sla-config|reject-submitted|by-source"

# 3. Permissions seeded
python -c "
from app.database import SessionLocal
from app.models.user import UserPermission
db = SessionLocal()
for s in ['procurement.purchase_requests.send_for_approval','sla_management.form_sla_config.view','sla_management.form_sla_config.manage']:
    print(s, '->', 'OK' if db.query(UserPermission).filter(UserPermission.slug == s).first() else 'MISSING')
"

# 4. Scheduled task seeded
python -c "
from app.database import SessionLocal
from app.models.scheduled_task import ScheduledTask
db = SessionLocal()
t = db.query(ScheduledTask).filter(ScheduledTask.key == 'form_sla_overdue_scan').first()
print(t.key, t.enabled, t.interval_value, t.interval_unit, t.next_run_at)
"
```
