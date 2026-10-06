# UAC - list API permission gates (LIST-API-PERMS)

**Status:** Acceptance contract for `PLAN-list-api-perms.md`. Written BEFORE implementation;
Items 5 to 7 added when the review rounds widened the contract (any-of gates, typed tracker
reads, scoped ticket reads, the route-walk guard). Every criterion maps to a test in
`sorento_crm_backend/tests/test_list_api_perms.py`.

Legend: each criterion is **Given / When / Then**, testable. No Journey section: no screen
changes, no new step, no data model. The threat statement is the framing.

**Threat statement.** The list and detail GETs in `marketing/`, `complaints/`, `sla/`, `forms/`
and `resources/` depend only on `get_current_user` or `get_current_user_or_api_key`. The menu
`permission:` keys in the frontend hide links only. Any authenticated session, including a role
holding none of the module's permissions, can read every promotion, every complaint (customer
names, phone numbers, free text), every SLA tracking record, every form and the attachment
catalogue.

---

## Item 1 - every gated route refuses a session without the slug

UAC1.1 - **Given** a user whose only permission is `ideation.board.view`, **when** they call any
P, S or ANY route in the plan's slug table with a session, **then** the response is 403 and the
detail names the required slug (for an ANY route, the whole list).
UAC1.2 - **Given** a user holding exactly the route's own slug, **when** they call the route,
**then** the response is 200 (a detail route on a random id: 404, the handler ran).
UAC1.3 - **Given** an `admin`, **when** they call the route, **then** 200 (the service's existing
superadmin/admin bypass; `admin` is the tested role, `superadmin` shares the same branch in
`UserPermissionService.check_user_has_permission`).

## Item 2 - the API-key path is unchanged for a key whose act-as user holds the slug

UAC2.1 - **Given** an integration whose act-as user holds the route's slug, **when** the route is
called with that integration's `X-API-Key` and no Bearer token, **then** 200.
UAC2.2 - **Given** an integration whose act-as user does NOT hold the slug, **when** the same
call is made, **then** 403 (the key is not a bypass).
UAC2.3 (REG) - the API-key assertions run through the real `get_current_user_or_api_key` and
`resolve_integration_principal`, not a dependency override.

## Item 3 - lookup routes stay open to sessions, closed to keys without the slug

UAC3.1 - **Given** a session with no permission at all, **when** it calls `GET /promotion-types/`,
`GET /campaign-types/`, `GET /sla-policies/`, `GET /directories/tree` or
`GET /attachment-types/`, **then** 200.
UAC3.2 - **Given** an API key whose act-as user lacks the lookup's slug, **when** it calls the
same route, **then** 403.

## Item 4 - nothing else changes

UAC4.1 (REG) - a session-only route gated with `require_permission` keeps refusing `X-API-Key`
with 401 (no new key door is opened); the scoped ticket reads do the same.
UAC4.2 (REG) - the self-scoped routes listed in the plan's "Left as they are" section keep
answering a user with no module slug (not tested here; named so the scope is explicit).

## Item 5 - any-of gates: another module's screen reads the route with its own slug

UAC5.1 - **Given** a user holding only the other screen's slug (for example
`master_data.products.view` for `GET /promotion-products/`, `dealer_kit.page.view` for
`GET /promotions/`, `inventory.stock.view` for `GET /attachments/current-stock-list`,
`resource.attachment_directories.view` for `GET /attachments/drive`), **when** they call the
route, **then** 200.
UAC5.2 - **Given** each entry of every reader list on its own, **when** that user calls the
route, **then** it is admitted; and each list holds exactly the slugs the plan's table names.
UAC5.3 - **Given** a user holding a per-record slug only (for example
`complaint_management.complaints.view`), **when** they call a cross-record list
(`GET /conversation-sla-tracking/`, `/dashboard`, `/event-logs`, `GET /attachments/drive`,
`GET /promotions/` with `master_data.products.view`), **then** 403.

## Item 6 - a tracker is read with the slug of the record it belongs to

UAC6.1 - **Given** a complaint's form-SLA tracker, **when** a user holding only
`complaint_management.complaints.view` reads `GET /{id}`, `GET /by-source?source_entity_type=complaint`
or `GET /event-logs?tracking_id={id}`, **then** 200; **when** a user holding only
`tickets.tickets.view` or `procurement.stock_inquiries.view` reads the same, **then** 403.
UAC6.2 - **Given** a conversation ticket (no source row), **when** a user holding only
`complaint_management.complaints.view` reads `GET /{id}`, **then** 403; a user holding
`sla.kpi.view` alone (the KPI dashboard's click-through) gets 200.
UAC6.3 - the source-type map covers every `FORM_SLA_TYPES` entry.

## Item 7 - scoped ticket reads: outsiders get 404, the assignee needs no slug

UAC7.1 - **Given** a real conversation ticket assigned to A, **when** a user B holding
`sla_management.conversation_sla_tracking.view`, `tickets.tickets.view` and
`complaint_management.complaints.view` reads `GET /{id}/conversation` or `GET /{id}/takeover-state`,
**then** 404 (never 403: the id is not confirmed).
UAC7.2 - **Given** the same ticket, **when** A, holding no permission at all, reads either,
**then** 200.
UAC7.3 - **Given** a complaint's form-SLA tracker, **when** a user holding only
`complaint_management.complaints.view` reads `GET /{id}/conversation`, **then** 200; a
bystander gets 403 naming that slug.

## Item 8 - the next bare GET cannot land unnoticed

UAC8.1 - **Given** `app.routes`, **when** the guard walks every GET under the five routers,
**then** each either carries a permission dependency or is in the allowlist with its reason, and
the allowlist names no route that is gated or gone.
