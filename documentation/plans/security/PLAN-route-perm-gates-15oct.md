# PLAN - route permission gates (calendar, scheduled tasks, SLA policies)

**Status:** In review (crew lane ROUTE-PERM-GATES, branch `crew/route-perm-gates`). Size S, RBAC
change, so reviewer and security-reviewer both run (`PRINCIPLES.md` small fix track excludes
permission changes). No migration, no new permission slug. Red-first.
**UAC:** `documentation/plans/security/route-perm-gates-acceptance-criteria.md`
**Found by:** the security review of PR #1703 (SETTINGS-PERM-GATE). Pre-existing on main.
**Follows:** `PLAN-access-agents-write-gates.md` (same defect class, same fix shape).
**Out of scope (brief):** `user_management/settings.py` (#1703) and the n8n webhook URLs (owner
decision pending). Reads are unchanged.

## The defect

Twelve writes in three routers depended on `get_current_user` (or `get_current_user_or_api_key`)
alone. That proves a session exists and nothing about role, so any staff session, a portal
user's session, or an admin viewing as someone else could make them.

## Audit (origin/main at 01e275d4c)

| Router | Route | Gate before | Gate after |
|---|---|---|---|
| `system/calendar.py` | `PUT /system/work-calendar-config` | signed in | `system.work_calendar.edit` |
| `system/calendar.py` | `POST /system/public-holidays` | signed in | `system.work_calendar.add` |
| `system/calendar.py` | `PUT /system/public-holidays/{id}` | signed in | `system.work_calendar.edit` |
| `system/calendar.py` | `DELETE /system/public-holidays/{id}` | signed in | `system.work_calendar.delete` |
| `system/scheduled_tasks.py` | `PATCH /system/scheduled-tasks/{id}` | signed in | admin / superadmin role |
| `system/scheduled_tasks.py` | `POST /system/scheduled-tasks/{id}/run-now` | signed in | admin / superadmin role |
| `sla/sla_policies.py` | `POST /sla-management/sla-policies/` | signed in or API key | `sla_management.sla_policies.add` |
| `sla/sla_policies.py` | `PUT /sla-policies/{id}` | signed in or API key | `.edit` |
| `sla/sla_policies.py` | `DELETE /sla-policies/{id}` | signed in or API key | `.delete` |
| `sla/sla_policies.py` | `POST /sla-policies/{id}/tiers` | signed in or API key | `.edit` |
| `sla/sla_policies.py` | `PUT /sla-policies/{id}/tiers/{tier_id}` | signed in or API key | `.edit` |
| `sla/sla_policies.py` | `DELETE /sla-policies/{id}/tiers/{tier_id}` | signed in or API key | `.edit` |

## Decisions

- **D1 Work calendar takes its registry slug.** `app/rbac/permission_registry.py`
  `_crud("system", "work_calendar", "Work Calendar")` already defines `.view/.add/.edit/.delete`,
  and the integration read of the same calendar carries `.view`
  (`app/api/v1/external/permissions.py` `"work-calendar"`). The screen stays
  `<RequireAccess superadmin>`, so nobody sees a change on it.
- **D2 Scheduled tasks take the admin role.** The registry has no scheduled-tasks slug. The screen
  is `<RequireAccess superadmin>` (`scheduled-tasks/page.tsx`) and `superadminOnly` in
  `config/menu.config.tsx`, which is the admin / superadmin role. The writes take that same gate
  (a local `require_admin` dependency on the effective user). Adding a slug waits until someone
  other than an admin needs to manage cron jobs.
- **D3 SLA policies take their registry slugs; tiers take `.edit`.** `_crud("sla_management",
  "sla_policies", ...)`; the reads already carry `.view`. A tier is part of its policy (the create
  body carries the tiers, the tiers table sits on the policy's page), so adding, changing or
  removing one edits the policy.
- **D4 The SLA API-key path stays.** `require_permission_with_api_key` holds an integration key to
  the same slug through its act-as user, as the reads already do and as 39 other write routes
  do. On dev data (`sorento_cagent_stack`) the three integration roles hold all three slugs.
- **D5 Frontend offers a write only to a caller the server accepts** (`useHasPermission`, the
  pattern #1703 used): SLA list Create (`.add`); detail Edit (`.edit`), Delete (`.delete`); Add
  Tier and the tier row actions (`.edit`); the create and edit pages answer no access in place.
  The scheduled task detail page gets `<RequireAccess superadmin>` like its list.

## Who sees a change (dev data, read-only query)

SLA writes `.add/.edit/.delete`: admin, director, warehouse_manager and the three integration
roles. `.view` only: customer_service, guest, project.customer.service, warehouse_executive. Those
four lose the Create, Edit, Delete and tier buttons on SLA Policies, which until now answered
with a write they were never meant to have. Admins see nothing new.

## Tests

- `sorento_crm_backend/tests/test_route_write_gates.py`: per write, no slug and view-only get 403
  naming the slug with nothing changed; the slug holder and the admin (real bypass) succeed; the
  SLA API-key principal is refused without the slug and succeeds with it; scheduled tasks refuse
  a non-admin holding every other slug and run-now never starts; reads stay open.
- `sorento_crm_frontend/.../sla-policies/sla-policy-write-gates.test.tsx` and
  `.../scheduled-tasks/[id]/page.test.tsx`: actions hidden without the slug or role, shown with.
