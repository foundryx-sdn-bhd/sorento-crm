# PLAN - list API permission gates (LIST-API-PERMS)

**Status:** Implemented on the lane branch, reviewed (reviewer + security-reviewer, findings
resolved below), awaiting CI and the owner's hand test. Phase 2 (backend + tests),
tester-first. No Phase 1 prototype: no screen changes. Not the small fix track (the diff is an
RBAC change), so `reviewer` and `security-reviewer` both ran.
**UAC:** `documentation/plans/security/list-api-perms-acceptance-criteria.md`
**Branch:** `claude/list-api-perms-p7t75u` (crew lane `LIST-API-PERMS`, PR #1498, base `main`)
**Follows:** `PLAN-user-management-read-gates.md` (same defect class, user_management routers).
**Coordinates with:** crew lane MENU-PERM-GATE (PR #1499): its menu slugs and this table agree;
its Files entry admits either Resources slug, so the drive listing does too.

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
  with any-of semantics. Where the slug set depends on the record (the SLA tracker reads), the
  handler calls that same dependency's inner function with the computed list
  (`sla_tracking.py`, `_require_any`), so bypass, `module_guard_strict` and the 403 wording are
  never re-implemented.
- **API key (`X-API-Key`):** `get_current_user_or_api_key` calls
  `resolve_integration_principal` (`app/services/integration_auth.py:47`), which maps the key to
  its `integrations` row and returns that row's `act_as_user_id` as the user dict with
  `auth_method = "integration_api_key"` (`integration_auth.py:118`). The same permission check
  then runs against that act-as user; there is no key bypass. Migration
  `297_seed_integrations.py` seeds the `integration_n8n` / `integration_mcp` roles by copying
  Admin's permission rows; migration `300_env_key_belongs_to_n8n.py` moves the legacy shared
  `EXTERNAL_API_KEY` onto the n8n integration. Every slug in the table below predates migration
  297 in `app/rbac/permission_registry.py` (verified against the registry at commit `0f49c9db`,
  the commit that added 297), so every integration role holds them and no API-key caller
  changes behaviour. The n8n workflows are not in this repo; n8n acts with the Admin-equivalent
  `integration_n8n` role. Before deploy, a read-only query on the prod copy should confirm
  `integration_n8n` and `integration_mcp` hold every slug the MCP catalogue's routes need (the
  roles hold copied permission rows, not the admin bypass).
- **Lookups (owner ruling 1 Oct 2026, never-stuck L10):** `require_session_or_api_key_permission`
  (`dependencies.py:476`) lets any signed-in session read a lookup list while an API key still needs
  the slug. Used for the type / folder-tree / SLA-policy pickers only, matching
  `app/api/v1/master_data/brands.py:140` and siblings.

## Slug table

Every slug is an EXISTING entry in `app/rbac/permission_registry.py`; no migration, no role
seeding. Dependency key: **P** = `require_permission_with_api_key(slug)`, **S** =
`require_permission(slug)` (session-only route), **L** = `require_session_or_api_key_permission(slug)`
(lookup: any session reads, API key needs the slug), **ANY** = `require_any_permission_with_api_key`
over the named list (a route another module's screen reads as its own feature admits with that
screen's slug; the lists are constants below the imports of each router, and
`tests/test_list_api_perms.py` pins their exact contents).

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
| `GET /complaints/{id}/conversation/window-state`, `/chat-template` (shared chat-panel router, `_respond_chat_template_routes.py`, `read_dependencies`) | S | `complaint_management.complaints.view` | complaint detail chat composer | n/a |

### SLA (`/api/v1/sla-management`)

| Route | Gate | Slug(s) | FE readers | API callers |
|---|---|---|---|---|
| `GET /sla-policies/` | L | `sla_management.sla_policies.view` | SLA Policies page; policy picker in Form SLA Config dialog and the AI Agents form | none |
| `GET /sla-policies/{id}`, `/{id}/tiers` | P | `sla_management.sla_policies.view` | SLA Policies pages only | none |
| `GET /conversation-sla-tracking/dashboard` | P | `sla_management.conversation_sla_tracking.view` | no live caller (old dashboard removed) | MCP `catalog.py:1038` |
| `GET /conversation-sla-tracking/` | P | `sla_management.conversation_sla_tracking.view` | Conversation SLA Tracking page; Form SLA Tracking page (`scope=form`) | MCP `catalog.py:1050` |
| `GET /conversation-sla-tracking/integration/due-escalations` | P | `sla_management.conversation_sla_tracking.view` | none (n8n escalation runner; every breaching conversation with its phone number, previously open to any session) | n8n |
| `GET /conversation-sla-tracking/event-logs` (no `tracking_id`) | inline `_require_event_log_reader` | `sla_management.escalation_logs.view` | SLA Event Logs page | MCP `catalog.py:1061` |
| `GET /conversation-sla-tracking/event-logs?tracking_id=` | inline, `tracker_reader_slugs(row.source_entity_type)` after the 404 | see below | event table on tracker detail and on the SLA tab of the owning record | same |
| `GET /conversation-sla-tracking/by-source` | inline, `tracker_reader_slugs(source_entity_type)` | see below | SLA tab and handling lock on complaint / stock inquiry / purchase request / sponsorship / ticket detail | none |
| `GET /conversation-sla-tracking/{id}` | inline, `tracker_reader_slugs(row.source_entity_type)` after the 404 | see below | tracker detail pages; SLA tab of the owning record; KPI dashboard row click-through | none |
| `GET /conversation-sla-tracking/{id}/conversation` | session only; conversation ticket: `can_user_act_on_tracking` else 404; form tracker: `tracker_reader_slugs` | see below | tracker detail chat records; My Pending ticket drawer | n/a |
| `GET /conversation-sla-tracking/{id}/takeover-state` | session only; `can_user_act_on_tracking` else 404 (`sla_takeover_service.get_takeover_row`) | n/a | My Pending contested-task banner | n/a |

`tracker_reader_slugs(type)` = `SLA_READERS` (`sla_management.conversation_sla_tracking.view`,
`sla_management.escalation_logs.view`, `sla.kpi.view`) + `SOURCE_TYPE_READERS[type]`:
`complaint` -> `complaint_management.complaints.view`, `stock_inquiry` ->
`procurement.stock_inquiries.view`, `purchase_request` -> `procurement.purchase_requests.view`,
`sponsorship_form` -> `procurement.sponsorship_forms.view`, `ticket` -> `tickets.tickets.view`,
`workflow_submission` -> `workflow_forms.submissions.view`, `price_tag_request` ->
`dealer_kit.price_tag_requests.view` (the map covers `FORM_SLA_TYPES` exactly, tested). A
conversation ticket (no source row) is read by the SLA module alone. So a complaints viewer
reads a complaint's tracker and not a purchase request's; a per-record slug does not open the
cross-record lists (tested).

### Forms (`/api/v1/forms-management`)

| Route | Gate | Slug(s) | FE readers | API callers |
|---|---|---|---|---|
| `GET /forms/`, `/{id}` | P | `forms.forms.view` | Forms list, detail, builder; attachment modal's form link picker (empties on 403) | MCP `catalog.py:1028` |
| `GET /revision-configs` | P | `user_management.settings.view` | Settings -> Portal Revisions only | none |

### Resources (`/api/v1/resource-management`)

| Route | Gate | Slug(s) | FE readers | API callers |
|---|---|---|---|---|
| `GET /attachments/` | ANY `ATTACHMENT_CATALOGUE_READERS` | `resource.attachments.view`, `complaint_management.complaints.view`, `procurement.stock_inquiries.view`, `procurement.purchase_requests.view`, `procurement.sponsorship_forms.view`, `procurement.packing_lists.view`, `master_data.products.view`, `projects.projects.view`, `marketing.promotions.view`, `forms.forms.view`, `dealer_kit.page.view` | Resources; the "link existing file" picker on complaint, SI, PR, sponsorship, packing list, product, project, promotion, form and flyer screens | MCP `catalog.py:402,434` |
| `GET /attachments/drive` | ANY `DRIVE_READERS` | `resource.attachments.view`, `resource.attachment_directories.view` | Files page (its MENU-PERM-GATE entry admits either) | none |
| `GET /attachments/collision-check` | ANY `ATTACHMENT_UPLOAD_READERS` | `resource.attachments.view`, `resource.attachments.upload` | upload dialog (Resources, Packing Lists) | none |
| `GET /attachments/current-stock-list` | ANY `STOCK_LIST_READERS` | `resource.attachments.view`, `inventory.stock.view` | Inventory Stock page | MCP `catalog.py:452` |
| `GET /directories/tree` | L | `resource.attachment_directories.view` | folder picker on upload and link dialogs across modules | none |
| `GET /directories/`, `/{id}` | P | `resource.attachment_directories.view` | no FE caller | none |
| `GET /attachment-types/` | L | `resource.attachment_types.view` | Attachment Types page; type picker on Packing Lists upload and Project Sales documents | none |
| `GET /attachment-types/{id}` | P | `resource.attachment_types.view` | Attachment Types form only | none |

**On `ATTACHMENT_CATALOGUE_READERS`:** eleven slugs, because the catalogue is every module's file
picker by design. The gate therefore refuses only a role outside every module (the lane's
bystander) and adds little beyond that; the catalogue itself is not narrowed by the caller's
module. Narrowing it (an `entity_type` filter for callers without `resource.attachments.view`) is
a follow-up, listed below. One more reader by construction: `project_mcp_bootstrap.py:97` grants
`projects.projects.view` at startup to every role an integration acts as, so every API key can
list the catalogue (found in the live API pass, where a key acting as the test's retail user
gave that user's role the slug). Integration roles are Admin-equivalent today (migration 297),
so nothing changes for them; it matters once those roles are narrowed.

### Left as they are (and why)

Self-scoped reads, verified in the service code by the security review: the route filters by
the caller's own id or visible team, so the data is the caller's own and a module view slug
would hide a user's own tasks from them. `/conversation-sla-tracking/my-pending`,
`/team-pending`, `/visible-users` (`sla_service.py:1554-1580`); `/{id}/ticket`,
`/{id}/conversation/page|search`, `/{id}/media` (`_ticket_tracking_in_scope`,
`sla_service.py:6008`); `/{id}/comments` (`ticket_comment_service.py:185`);
`/conversation-events/stream` (`entitled_contacts`, `conversation_events.py:38`);
`/upload-activity` (`uploaded_by == caller`, `upload_activity.py:289`);
`/revision-configs/enabled` (four booleans of configuration).

Named exposure, follow-up lane (no scope is enforced today; the plan previously called some of
these "per entity", which was wrong):

- Per-attachment reads `GET /attachments/{id}`, `/{id}/download`, `/{id}/metadata`,
  `/{id}/preview-url`: only company scope applies (`CompanyScopedMixin`, NULL-company rows are
  visible to all). They serve attachments linked from complaint, procurement, product, form,
  promotion, proforma invoice, loading plan, project and price-tag pages; the right gate is the
  linked record's own slug, as the audit read gate does per entity type.
- `GET /form-sla-tracking` (handler name, WhatsApp phone, escalation reason for any entity id),
  `GET /form-actions/current`, `/eligibility` (requester name, action label, failure text): the
  entity-type slug, with the `SOURCE_TYPE_READERS` map above.
- The shared chat-panel GETs `window-state` / `chat-template` on the SLA mount (and the
  procurement mounts, outside this lane): the ticket scope. The complaints mount is gated here.
- `PUT /forms-management/revision-configs/{type}` stays session-only while its GET now needs
  `user_management.settings.view`.
- Write routes missing a slug (`POST/PUT/DELETE /sla-policies` and tiers, every marketing
  create/update/delete, forms, directories, attachment types): out of this lane's scope (list
  endpoints only).

`tests/test_list_api_perms.py::test_every_get_under_the_five_routers_is_gated_or_listed` walks
`app.routes` and fails on any GET under the five routers that carries no permission dependency
and is not in its allowlist with a reason, so the next bare GET cannot land unnoticed. It found
the shared chat-panel GETs and `/integration/due-escalations` during this lane.

## Review resolutions

Security reviewer (ready, two in-lane fixes recommended): 1 legacy `/{id}/conversation`
unscoped -> scoped as its paged sibling (form trackers keep the record-slug rule, which the
reviewer's `require_ticket_in_scope` suggestion would have 404ed); 2 `TRACKER_READERS` not tied
to the record's type -> `SOURCE_TYPE_READERS`; 3 `takeover-state` not self-scoped -> 404 outside
`can_user_act_on_tracking`; 4, 6, 7, 14 -> follow-ups above; 5 `/integration/due-escalations`
-> gated; 8 -> the route-walk test; 9 (`/directories/tree?deleted=true` shows Trash folder
names to any session) -> accepted, folder names only; 11 strict-mode parity -> the inline
checks now call the shared dependency; 13 -> cross-type refusals tested.

Reviewer (not mergeable until resolved): 1 plan/UAC drift -> this revision; 2 form-SLA chat
records 404 -> fixed as above; 3 -> `SOURCE_TYPE_READERS`; 4 KPI click-through -> `sla.kpi.view`
in `SLA_READERS`; 5 scoped tests could not fail -> real seeded tickets, outsider 404 and
assignee 200; 6 -> shared dependency reused; 7 constants between imports -> moved below the
imports; 8 -> the catalogue note above. Kill gaps K2/K3/K8 -> every reader-list entry is
admitted alone and each list's exact contents are asserted.

## Tests

`tests/test_list_api_perms.py`: real app, real `UserPermissionService`, roles seeded on a blank
schema (the `tests/test_audit_read_gate.py` harness), two real trackers seeded (a conversation
ticket with an assignee, a complaint's form tracker). For every gated route: 403 without the
slug (the body names it), 200 with exactly it, superadmin/admin bypass, the API-key path (a real
`integration_api_keys` row through `resolve_integration_principal`, no principal override) 200
when the act-as user holds the slug and 403 when it does not; lookup routes 200 for a session with
no slug and 403 for a key without it; each cross-module reader admitted by its own slug; every
reader-list entry admitted alone and each list's exact contents pinned; a per-record slug refused
on the cross-record lists and on a tracker of another type; the scoped ticket reads 404 to an
outsider holding every module slug and 200 to the assignee holding none; session-only routes
refusing a key with 401; the route-walk guard. Nine existing route tests whose principal stub
has no users row now answer the role lookup as admin.
