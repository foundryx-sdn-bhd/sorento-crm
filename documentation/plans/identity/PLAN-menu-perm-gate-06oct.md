# PLAN - Sidebar hides what the effective user cannot open (MENU-PERM-GATE)

> Status: IN REVIEW 2026-10-06 (PR #1499, browser pass on a27a87f3). Small fix track (frontend only, no migration, no new slug,
> no auth change on the backend). Lane `MENU-PERM-GATE`, cloud sandbox.
> UAC: `menu-perm-gate-acceptance-criteria.md` alongside.

## The defect

Owner, 6 Oct 2026 (prod): impersonating a retail-salesperson user, the sidebar still shows
Marketing (Promotions x4, Campaigns), Complaints, SLA (6 items), Forms, Resources (Files, Trash,
Attachment Types), Dashboards, System > Messaging > Integrations, and the superadmin-only
System items.

Two causes, both in the frontend:

1. Those entries carry no `permission` / `permissionsAny` in
   `sorento_crm_frontend/config/menu.config.tsx`, so `filterMenuByPermission`
   (`app/components/layouts/demo1/components/sidebar-menu.tsx`) never drops them.
2. `superadminOnly` entries are filtered with `isSuperadminUser(session?.user)`: the REAL
   admin's NextAuth session. Impersonation swaps the backend principal through the
   `X-Impersonate-User-Id` header (`lib/api.ts`, `app/dependencies.py`
   `_maybe_apply_impersonation`) and `/users/me/permissions` already answers for the target, but
   the session's `roleName` stays the admin's, so superadmin-only items never hide.

## Mapping (entry -> existing registry slug of the page it opens)

Every slug below exists in `sorento_crm_backend/app/rbac/permission_registry.py` (line cited).
"List API gate" says what the page's list endpoint enforces today; `auth only` means the
separate lane LIST-API-PERMS adds the slug there, and this lane gates the menu with the slug
that lane will use.

| Menu entry | Page | Slug | Registry | List API gate today |
| --- | --- | --- | --- | --- |
| Dashboards | `/` (My pending widget + SLA KPI dashboard) | any of `sla_management.conversation_sla_tracking.view`, `sla.kpi.view` | :150, migration 236 | my-pending auth only; KPI `sla.kpi.view` |
| All Promotions | `/marketing-management/promotions` | `marketing.promotions.view` | :289 | auth only |
| Promotion Attachments | `/marketing-management/promotion-attachments` | `marketing.promotion_attachments.view` | :290 | auth only |
| Promotion Types | `/marketing-management/promotion-types` | `marketing.promotion_types.view` | :298 | auth only |
| Promotion Products | `/marketing-management/promotion-products` | `marketing.promotion_products.view` | :291 | auth only |
| Campaigns | `/marketing-management/campaigns` | `marketing.campaigns.view` | :292 | auth only |
| Complaints | `/complaint-management/complaints` | `complaint_management.complaints.view` | :114 | auth only |
| SLA Policies | `/sla-management/sla-policies` | `sla_management.sla_policies.view` | :149 | auth only |
| Conversation SLA Tracking | `/sla-management/conversation-sla-tracking` | `sla_management.conversation_sla_tracking.view` | :150 | auth only |
| Form SLA Tracking | `/sla-management/form-sla-tracking` (same list, `scope=form`) | `sla_management.conversation_sla_tracking.view` | :150 | auth only |
| My Team Tasks | `/sla-management/team-pending` (`/conversation-sla-tracking/team-pending`) | `sla_management.conversation_sla_tracking.view` | :150 | auth only |
| Form SLA Configuration | `/sla-management/form-sla-config` | `sla_management.form_sla_config.view` | :184 | enforced |
| SLA Event Logs | `/sla-management/escalation-logs` (`/conversation-sla-tracking/event-logs`) | `sla_management.escalation_logs.view` | :168 | auth only |
| Forms | `/forms-management/forms` | `forms.forms.view` | :301 | auth only |
| Files | `/resource-management/attachment-directories` (directory tree + attachments) | any of `resource.attachments.view`, `resource.attachment_directories.view` | :329, :333 | auth only |
| Trash | `/resource-management/trash` (attachments, `is_deleted`) | `resource.attachments.view` | :329 | auth only |
| Attachment Types | `/resource-management/attachment-types` | `resource.attachment_types.view` | :334 | auth only |
| Integrations | `/integration-management/integrations` | `integration.integrations.view` | :346 | enforced |

Nothing is invented: every module above already has a `.view` slug in the registry, so no entry
is left ungated. `dashboard.view` (:452) also exists, but no route enforces it and no migration
grants it, so Dashboards follows what the page actually renders instead.

`MENU_SIDEBAR_COMPACT` (demo2/3/6/10 shells, not the shipped demo1 shell) is left as is.

## Effective superadmin

`hooks/useEffectiveIsSuperadmin.ts`: while impersonating, the flag comes from the impersonation
session's target (`targetUser.role_name`, the target's first-assigned role slug, serialized by
`app/api/v1/user_management/impersonation.py::_serialize_target`), not the admin's session;
otherwise it is exactly `isSuperadminUser(session.user)` as before, `null` while the session
loads. The backend refuses to impersonate an admin or superadmin (`start_impersonation`), so in
practice the flag is `false` for the whole impersonation, and the hook stays correct if that
rule ever changes. Used by the sidebar and by `RequireAccess superadmin` so a hidden entry's page
is denied too.

Security review (6 Oct): the store is localStorage, so the target's role is ANDed with the
session's own answer. It can only narrow an admin's flag, never widen a non-admin's: a forged
entry reads `false`. (The backend ignores the view-as header for a non-admin and clears the
store on the first response anyway.) Not this lane's, logged for its own backend lane: the
routes behind Scheduled Tasks (`system/scheduled_tasks.py` PATCH and run-now), Work Calendar
(`system/calendar.py` PUT and holiday writes) and System Health (`system/health.py`) are
login-only on the backend, so for those pages the superadmin gate is browser-only today.

## Tests (red first)

- `lib/menu-access-filter.test.ts`: the real `MENU_SIDEBAR` through the pure filter. A user with
  only `ideation.board.view`, not superadmin, sees exactly `/ideas`. A non-superadmin holding
  every slug sees no `superadminOnly` path. A superadmin holding every slug sees every path.
- `hooks/useEffectiveIsSuperadmin.test.tsx`: admin not impersonating -> true; admin
  impersonating a non-admin target -> false; session loading -> null.
- `app/components/layouts/demo1/components/sidebar-menu.impersonation.test.tsx`: the rendered
  sidebar, admin session impersonating a retail salesperson with only `ideation.board.view`,
  shows Ideas and none of the groups above; the same admin not impersonating sees System.

## Hand test

`laneboard/scripts/<PR>.md`: owner impersonates a user with only `ideation.board.view` on a crew
copy and reads the sidebar.
