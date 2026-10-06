# PLAN - list API permission gates (LIST-API-PERMS)

**Status:** Implemented on the lane branch, in review. Phase 2 (backend + tests), tester-first.
No Phase 1 prototype: no screen changes. Not the small fix track (the diff is an RBAC change),
so `reviewer` and `security-reviewer` both run.
**UAC:** `documentation/plans/security/list-api-perms-acceptance-criteria.md`
**Branch:** `claude/list-api-perms-p7t75u` (crew lane `LIST-API-PERMS`, PR #1498, base `main`)
**Follows:** `PLAN-user-management-read-gates.md` (same defect class, user_management routers).
**Coordinates with:** crew lane MENU-PERM-GATE (frontend menu slugs must match the slug table
below; the final table is posted as a crew-note on both PRs).

## The defect

`Depends(get_current_user)` and `Depends(get_current_user_or_api_key)` prove a session or a key
exists. They prove nothing about role. The promotions list
(`app/api/v1/marketing/promotions.py`) and the complaints list
(`app/api/v1/complaints/complaints.py`) were reachable by any signed-in user, for example a
retail salesperson holding only `ideation.board.view`. Sibling routes already gate correctly:
`app/api/v1/user_management/settings.py:244` uses
`Depends(require_permission("user_management.settings.view"))`, and
`app/api/v1/complaints/complaint_root_causes.py:27` uses
`Depends(require_permission_with_api_key("master_data.complaint_root_causes.view"))` on a route
that an API key may also call.

Menu `permission:` keys are cosmetic here too: `config/menu.config.tsx` carries no permission on
any Marketing, Complaints, Forms or Resources entry and on most SLA entries (only Conversations,
Message Snippets and KPI Dashboard have one); `moduleKey` hides a module for the whole tenant,
not per user. The backend dependency is the entire gate.

## How the gate resolves for each principal (`app/dependencies.py`)

- **Session (Bearer):** `require_permission_with_api_key(slug)` (`dependencies.py:501`) resolves the
  principal through `get_current_user_or_api_key` (`dependencies.py:624`) and runs
  `UserPermissionService.check_user_has_permission(user_id, slug)`
  (`app/services/user_service.py:1538`): superadmin/admin pass, everyone else needs a role that
  holds the slug. `require_any_permission_with_api_key(slugs)` (`dependencies.py:549`) is the same
  with any-of semantics.
- **API key (`X-API-Key`):** `get_current_user_or_api_key` calls
  `resolve_integration_principal` (`app/services/integration_auth.py:47`), which maps the key to
  its `integrations` row and returns that row's `act_as_user_id` as the user dict with
  `auth_method = "integration_api_key"`. The same permission check then runs against that act-as
  user. Migration `297_seed_integrations.py` seeds the `integration_n8n` / `integration_mcp`
  roles by copying Admin's full permission set; migration `300_env_key_belongs_to_n8n.py` moves the
  legacy shared `EXTERNAL_API_KEY` onto the n8n integration. Every slug in the table below predates
  migration 297 in `app/rbac/permission_registry.py` (verified against the registry at commit
  `0f49c9db`, the commit that added 297), so every integration role holds them and no API-key
  caller changes behaviour. The n8n workflows are not in this repo; n8n acts with the
  Admin-equivalent `integration_n8n` role, so no slug here can refuse it.
- **Lookups (owner ruling 1 Oct 2026, never-stuck L10):** `require_session_or_api_key_permission`
  (`dependencies.py:476`) lets any signed-in session read a lookup list while an API key still needs
  the slug. Used for the type / folder / SLA-policy pickers only, matching
  `app/api/v1/master_data/brands.py:140` and siblings.

## Slug table

Every slug is an EXISTING entry in `app/rbac/permission_registry.py`; no migration, no role
seeding. Dependency key: **P** = `require_permission_with_api_key(slug)`, **S** =
`require_permission(slug)` (session-only route), **L** = `require_session_or_api_key_permission(slug)`
(lookup: any session reads, API key needs the slug), **ANY** = `require_any_permission_with_api_key`
over the named list (a route another module's detail page reads as its own feature admits with
that screen's slug; the lists are constants at the top of each router).

"FE readers" is from the caller map (services -> hooks -> pages). "API callers" names who calls
the route with `X-API-Key` today, from `sorento_crm_mcp/sorento_crm_mcp/catalog.py`.

### Marketing (`/api/v1/marketing`)

| Route | Gate | Slug(s) | FE readers | API callers |
|---|---|---|---|---|
| `GET /promotions/` | ANY `PROMOTION_LIST_READERS` | `marketing.promotions.view`, `dealer_kit.page.view` | Promotions list; Dealer Kit brochure / flyer / page promotion pickers (`brochureImageService.ts`); the attachment modal's link picker (empties on 403) | MCP `catalog.py:285` |
| `GET /promotions/{id}` | P | `marketing.promotions.view` | promotion detail, edit, attachments tab | none |
| `GET /promotions/{id}/products` | P | `marketing.promotions.view` | no live caller (`PromotionProductsGrid` is unimported) | none |
| `GET /promotion-products/` | ANY `PROMOTION_PRODUCT_READERS` | `marketing.promotion_products.view`, `master_data.products.view` | Promotion Products page; product detail Promotions tab | MCP `catalog.py:317` |
| `GET /promotion-attachments/`, `/{id}` | P | `marketing.promotion_attachments.view` | Promotion Attachments page; `/{id}` has no caller | MCP `catalog.py:359` |
| `GET /promotion-attachments/promotion/{id}` | ANY `PROMOTION_ATTACHMENT_READERS` | `marketing.promotion_attachments.view`, `marketing.promotions.view` | promotion detail / edit attachments tab | none |
| `GET /promotion-types/`, `/{id}` | L | `marketing.promotion_types.view` | Promotion Types page; picker on the promotion form | none |
| `GET /campaigns/`, `/{id}` | P | `marketing.campaigns.view` | Campaigns pages | none |
| `GET /campaign-types/`, `/{id}` | L | `marketing.campaigns.view` (no own slug; the campaign form's type lookup) | campaign form only | none |

### Complaints (`/api/v1/complaints-management`)

| Route | Gate | Slug(s) | FE readers | API callers |
|---|---|---|---|---|
| `GET /complaints/` | P | `complaint_management.complaints.view` | Complaints list | MCP `catalog.py:1153` |
| `GET /complaints/analytics` | P | `complaint_management.complaints.view` | none | MCP `catalog.py:1179` |
| `GET /complaints/{id}`, `/{id}/fulfilment-orders` | P | `complaint_management.complaints.view` | complaint detail, edit | none |
| `GET /complaints/{id}/conversation` | S | `complaint_management.complaints.view` | complaint detail conversation panel | n/a (session only) |

### SLA (`/api/v1/sla-management`)

| Route | Gate | Slug(s) | FE readers | API callers |
|---|---|---|---|---|
| `GET /sla-policies/` | L | `sla_management.sla_policies.view` | SLA Policies page; policy picker in Form SLA Config dialog and the AI Agents form | none |
| `GET /sla-policies/{id}`, `/{id}/tiers` | P | `sla_management.sla_policies.view` | SLA Policies pages only | none |
| `GET /conversation-sla-tracking/dashboard` | P | `sla_management.conversation_sla_tracking.view` | no live caller (old dashboard removed) | MCP `catalog.py:1038` |
| `GET /conversation-sla-tracking/` | P | `sla_management.conversation_sla_tracking.view` | Conversation SLA Tracking page; Form SLA Tracking page (`scope=form`) | MCP `catalog.py:1050` |
| `GET /conversation-sla-tracking/event-logs` (no `tracking_id`) | inline, `_require_event_log_reader` | `sla_management.escalation_logs.view` | SLA Event Logs page | MCP `catalog.py:1061` |
| `GET /conversation-sla-tracking/event-logs?tracking_id=` | inline, any of `TRACKER_READERS` | see below | event table on tracker detail and on the SLA tab of the owning record | same |
| `GET /conversation-sla-tracking/by-source`, `/{id}`, `/{id}/conversation` | ANY `TRACKER_READERS` | `sla_management.conversation_sla_tracking.view`, `sla_management.escalation_logs.view`, `complaint_management.complaints.view`, `procurement.stock_inquiries.view`, `procurement.purchase_requests.view`, `procurement.sponsorship_forms.view`, `tickets.tickets.view` | tracker detail pages; SLA tab and handling lock on complaint / stock inquiry / purchase request / sponsorship / ticket detail; `/{id}/conversation` also the home My Pending ticket drawer's chat records | none |

`TRACKER_READERS` is a per-record gate: a tracker is read by its UUID, and the screens that read
it are the owning record's detail pages. A per-record slug does not open the cross-record lists
(tested).

### Forms (`/api/v1/forms-management`)

| Route | Gate | Slug(s) | FE readers | API callers |
|---|---|---|---|---|
| `GET /forms/`, `/{id}` | P | `forms.forms.view` | Forms list, detail, builder; attachment modal's form link picker (empties on 403) | MCP `catalog.py:1028` |
| `GET /revision-configs` | P | `user_management.settings.view` | Settings -> Portal Revisions only | none |

### Resources (`/api/v1/resource-management`)

| Route | Gate | Slug(s) | FE readers | API callers |
|---|---|---|---|---|
| `GET /attachments/` | ANY `ATTACHMENT_CATALOGUE_READERS` | `resource.attachments.view`, `complaint_management.complaints.view`, `procurement.stock_inquiries.view`, `procurement.purchase_requests.view`, `procurement.sponsorship_forms.view`, `procurement.packing_lists.view`, `master_data.products.view`, `projects.projects.view`, `marketing.promotions.view`, `forms.forms.view`, `dealer_kit.page.view` | Resources; the "link existing file" picker on complaint, SI, PR, sponsorship, packing list, product, project, promotion, form and flyer screens | MCP `catalog.py:402,434` |
| `GET /attachments/drive` | P | `resource.attachments.view` | Resources only | none |
| `GET /attachments/collision-check` | ANY `ATTACHMENT_UPLOAD_READERS` | `resource.attachments.view`, `resource.attachments.upload` | upload dialog (Resources, Packing Lists) | none |
| `GET /attachments/current-stock-list` | ANY `STOCK_LIST_READERS` | `resource.attachments.view`, `inventory.stock.view` | Inventory Stock page | MCP `catalog.py:452` |
| `GET /directories/tree` | L | `resource.attachment_directories.view` | folder picker on upload and link dialogs across modules | none |
| `GET /directories/`, `/{id}` | P | `resource.attachment_directories.view` | no FE caller | none |
| `GET /attachment-types/` | L | `resource.attachment_types.view` | Attachment Types page; type picker on Packing Lists upload and Project Sales documents | none |
| `GET /attachment-types/{id}` | P | `resource.attachment_types.view` | Attachment Types form only | none |

### Left as they are (and why)

Self-scoped reads: the route filters by the caller's own id or visible team, so the data is the
caller's own and a module view slug would hide a user's own tasks from them.
`/conversation-sla-tracking/my-pending`, `/team-pending`, `/visible-users`,
`/{id}/takeover-state`, `/{id}/ticket`, `/{id}/comments`, `/{id}/conversation/page|search`,
`/{id}/media` (assignee-or-manager scoped in the service, `fetch_conversation_thread_page`);
`/form-sla-tracking` (per source entity, read by the handling lock on that entity's own detail
page); `/form-actions/current`, `/form-actions/eligibility` (per entity and actor);
`/conversation-events/stream` (entitled contacts only, `conversation_events.py:38`);
`/upload-activity` (the caller's own upload sessions, read from the global header).

Per-attachment reads (`GET /attachments/{id}`, `/{id}/download`, `/{id}/metadata`,
`/{id}/preview-url`) serve attachments linked from complaint, procurement, product, form,
promotion, proforma invoice, loading plan, project and price-tag pages for users who hold THAT
record's view slug. The right gate is the linked record's own slug (as the audit read gate does
per entity type), which is a different lane. Named here so the exposure is not hidden.

`GET /forms-management/revision-configs/enabled`: config flags (which form types allow a portal
revision), read by the procurement detail pages; deliberately ungated per its own docstring
(`revision_configs.py:154`), holds no record data.

Write routes that are also missing a slug (`POST/PUT/DELETE /sla-policies` and tiers, every
marketing create/update/delete, forms, directories, attachment types) are out of this lane's scope
(list endpoints only) and reported as a follow-up in the PR body.

## Tests

`tests/test_list_api_perms.py`: real app, real `UserPermissionService`, roles seeded on a blank
schema (the `tests/test_audit_read_gate.py` harness). For every gated route: 403 without the slug
(the body names it), 200 with exactly it, superadmin/admin bypass, the API-key path (a real
`integration_api_keys` row through `resolve_integration_principal`, no principal override) 200
when the act-as user holds the slug and 403 when it does not; lookup routes 200 for a session with
no slug and 403 for a key without it; each cross-module reader admitted by its own slug; a
per-record slug refused on the cross-record lists; the session-only route refusing a key with 401.
Red with the gates stashed: 78 failed / 102 passed. Green: 180 passed.
