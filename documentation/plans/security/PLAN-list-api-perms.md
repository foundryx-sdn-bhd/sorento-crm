# PLAN - list API permission gates (LIST-API-PERMS)

**Status:** In progress. Phase 2 (backend + tests), tester-first. No Phase 1 prototype: no screen
changes. Not the small fix track (the diff is an RBAC change), so `reviewer` and
`security-reviewer` both run.
**UAC:** `documentation/plans/security/list-api-perms-acceptance-criteria.md`
**Branch:** `claude/list-api-perms-p7t75u` (crew lane `LIST-API-PERMS`, base `main`)
**Follows:** `PLAN-user-management-read-gates.md` (same defect class, user_management routers).
**Coordinates with:** crew lane MENU-PERM-GATE (frontend menu slugs must match the slug table
below; the final table is posted as a crew-note on both PRs).

## The defect

`Depends(get_current_user)` and `Depends(get_current_user_or_api_key)` prove a session or a key
exists. They prove nothing about role. The promotions list
(`app/api/v1/marketing/promotions.py:135`) and the complaints list
(`app/api/v1/complaints/complaints.py:268`) are reachable by any signed-in user, for example a
retail salesperson holding only `ideation.board.view`. Sibling routes already gate correctly:
`app/api/v1/user_management/settings.py:244` uses
`Depends(require_permission("user_management.settings.view"))`, and
`app/api/v1/complaints/complaint_root_causes.py:27` uses
`Depends(require_permission_with_api_key("master_data.complaint_root_causes.view"))` on a route
that an API key may also call.

## How the gate resolves for each principal (`app/dependencies.py`)

- **Session (Bearer):** `require_permission_with_api_key(slug)` (`dependencies.py:501`) resolves the
  principal through `get_current_user_or_api_key` (`dependencies.py:624`) and runs
  `UserPermissionService.check_user_has_permission(user_id, slug)`
  (`app/services/user_service.py:1538`): superadmin/admin pass, everyone else needs a role that
  holds the slug.
- **API key (`X-API-Key`):** `get_current_user_or_api_key` calls
  `resolve_integration_principal` (`app/services/integration_auth.py:47`), which maps the key to
  its `integrations` row and returns that row's `act_as_user_id` as the user dict with
  `auth_method = "integration_api_key"`. The same permission check then runs against that act-as
  user. Migration `297_seed_integrations.py` seeds the `integration_n8n` / `integration_mcp`
  roles by copying Admin's full permission set; migration `300_env_key_belongs_to_n8n.py` moves the
  legacy shared `EXTERNAL_API_KEY` onto the n8n integration. Every slug in the table below predates
  migration 297 in `app/rbac/permission_registry.py` (verified against the registry at commit
  `0f49c9db`, the commit that added 297), so every integration role holds them and no API-key
  caller changes behaviour.
- **Pickers (owner ruling 1 Oct 2026, never-stuck L10):** `require_session_or_api_key_permission`
  (`dependencies.py:476`) lets any signed-in session read a lookup list while an API key still needs
  the slug. Used in this PR for the type / directory lookups only, matching
  `app/api/v1/master_data/brands.py:140` and siblings.

## Slug table

Every slug is an EXISTING entry in `app/rbac/permission_registry.py`; no migration, no role
seeding. "API callers" names who calls the route with `X-API-Key` today:
`sorento_crm_mcp/sorento_crm_mcp/catalog.py` paths (MCP) and the n8n integration (its workflows
are not in this repo; it acts with the Admin-equivalent `integration_n8n` role, so it is unaffected
by any slug in this table).

Dependency key: **P** = `require_permission_with_api_key(slug)`, **S** =
`require_permission(slug)` (session-only routes), **L** = `require_session_or_api_key_permission(slug)`
(lookup: any session reads, API key needs the slug).

### Marketing (`/api/v1/marketing`)

| Route | Today | Gate | Slug | API callers |
|---|---|---|---|---|
| `GET /promotions/` (`promotions.py:44`) | `get_current_user_or_api_key` | P | `marketing.promotions.view` | MCP `catalog.py:285` |
| `GET /promotions/{id}` (`promotions.py:216`) | same | P | `marketing.promotions.view` | none found |
| `GET /promotions/{id}/products` (`promotions.py:500`) | same | P | `marketing.promotions.view` | none found |
| `GET /promotion-products/` (`promotion_products.py:208`) | same | P | `marketing.promotion_products.view` | MCP `catalog.py:317` |
| `GET /promotion-attachments/` (`promotion_attachments.py:29`) | same | P | `marketing.promotion_attachments.view` | MCP `catalog.py:359` |
| `GET /promotion-attachments/{id}` (`:150`) | same | P | `marketing.promotion_attachments.view` | none found |
| `GET /promotion-attachments/promotion/{id}` (`:223`) | same | P | `marketing.promotion_attachments.view` | none found |
| `GET /promotion-types/`, `/{id}` (`promotion_types.py:32,51`) | same | L | `marketing.promotion_types.view` | none found |
| `GET /campaigns/`, `/{id}` (`campaigns.py:16,33`) | same | P | `marketing.campaigns.view` | none found |
| `GET /campaign-types/`, `/{id}` (`campaign_types.py:15,33`) | same | L | `marketing.campaigns.view` (no own slug; it is the campaign form's type lookup) | none found |

### Complaints (`/api/v1/complaints-management`)

| Route | Today | Gate | Slug | API callers |
|---|---|---|---|---|
| `GET /complaints/` (`complaints.py:242`) | `get_current_user_or_api_key` | P | `complaint_management.complaints.view` | MCP `catalog.py:1153` |
| `GET /complaints/analytics` (`:297`) | same | P | `complaint_management.complaints.view` | MCP `catalog.py:1179` |
| `GET /complaints/{id}` (`:566`) | same | P | `complaint_management.complaints.view` | none found |
| `GET /complaints/{id}/fulfilment-orders` (`:546`) | same | P | `complaint_management.complaints.view` | none found |
| `GET /complaints/{id}/conversation` (`:462`) | `get_current_user` | S | `complaint_management.complaints.view` | n/a (session only) |

### SLA (`/api/v1/sla-management`)

| Route | Today | Gate | Slug | API callers |
|---|---|---|---|---|
| `GET /sla-policies/`, `/{id}`, `/{id}/tiers` (`sla_policies.py:19,46,117`) | `get_current_user_or_api_key` | P | `sla_management.sla_policies.view` | none found |
| `GET /conversation-sla-tracking/dashboard` (`sla_tracking.py:363`) | same | P | `sla_management.conversation_sla_tracking.view` | MCP `catalog.py:1038` |
| `GET /conversation-sla-tracking/` (`:790`) | same | P | `sla_management.conversation_sla_tracking.view` | MCP `catalog.py:1050` |
| `GET /conversation-sla-tracking/by-source` (`:2092`) | same | P | `sla_management.conversation_sla_tracking.view` | none found |
| `GET /conversation-sla-tracking/event-logs` (`:2120`) | same | P | `sla_management.escalation_logs.view` | MCP `catalog.py:1061` |
| `GET /conversation-sla-tracking/{id}` (`:2384`) | same | P | `sla_management.conversation_sla_tracking.view` | none found |
| `GET /conversation-sla-tracking/{id}/conversation` (`:2155`) | same | P | `sla_management.conversation_sla_tracking.view` | none found |

### Forms (`/api/v1/forms-management`)

| Route | Today | Gate | Slug | API callers |
|---|---|---|---|---|
| `GET /forms/`, `/{id}` (`forms.py:30,123`) | `get_current_user_or_api_key` | P | `forms.forms.view` | MCP `catalog.py:1028` |

### Resources (`/api/v1/resource-management`)

| Route | Today | Gate | Slug | API callers |
|---|---|---|---|---|
| `GET /attachments/` (`attachments.py:233`) | `get_current_user_or_api_key` | P | `resource.attachments.view` | MCP `catalog.py:402,434` |
| `GET /attachments/drive` (`:401`) | same | P | `resource.attachments.view` | none found |
| `GET /attachments/collision-check` (`:378`) | same | P | `resource.attachments.view` | none found |
| `GET /attachments/current-stock-list` (`:550`) | same | P | `resource.attachments.view` | MCP `catalog.py:452` |
| `GET /directories/`, `/tree`, `/{id}` (`directories.py:49,64,84`) | same | L | `resource.attachment_directories.view` | none found |
| `GET /attachment-types/`, `/{id}` (`attachment_types.py:16,42`) | same | L | `resource.attachment_types.view` | none found |

### Left as they are (and why)

Self-scoped reads: the route filters by the caller's own id or visible team, so the data is the
caller's own and a module view slug would hide a user's own tasks from them.
`/conversation-sla-tracking/my-pending`, `/team-pending`, `/visible-users`,
`/{id}/takeover-state`, `/{id}/ticket`, `/{id}/comments`, `/{id}/conversation/page|search`,
`/{id}/media`; `/form-sla-tracking` (per source entity, read from that entity's own detail page);
`/form-actions/current`, `/form-actions/eligibility` (per entity and actor);
`/conversation-events/stream` (entitled contacts only, `conversation_events.py:38`);
`/upload-activity` (the caller's own upload sessions).

Per-attachment reads (`GET /attachments/{id}`, `/{id}/download`, `/{id}/metadata`,
`/{id}/preview-url`) serve attachments linked from complaint, form, product and packing-list
detail pages for users who hold THAT record's view slug and not `resource.attachments.view`.
Gating them on the Resources slug would break those pages; the right gate is the linked record's
own slug, which is a different lane. Listed here so the exposure is named, not hidden.

`GET /forms-management/revision-configs` and `/revision-configs/enabled-map`: config flags (which
form types allow a portal revision), read by every form detail page; no slug exists for them and
they hold no record data.

Write routes that are also missing a slug (`POST/PUT/DELETE /sla-policies` and tiers, every
marketing create/update/delete, forms, directories, attachment types) are out of this lane's scope
(list endpoints only) and reported as a follow-up in the PR body.

## Tests

`tests/test_list_api_perms.py`: real app, real `UserPermissionService`, roles seeded on a blank
schema (the `tests/test_audit_read_gate.py` harness). For every gated route: 403 without the slug,
200 with it, and the API-key path (a real `integration_api_keys` row through
`resolve_integration_principal`, no dependency override) 200 when the act-as user holds the slug
and 403 when it does not. Lookup routes: 200 for a session with no slug, 403 for a key without it.
