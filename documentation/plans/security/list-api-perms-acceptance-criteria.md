# UAC - list API permission gates (LIST-API-PERMS)

**Status:** Acceptance contract for `PLAN-list-api-perms.md`. Written BEFORE implementation.

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
route in the plan's slug table with a session, **then** the response is 403 and the detail names
the required slug.
UAC1.2 - **Given** a user holding exactly the route's slug, **when** they call the route, **then**
the response is 200.
UAC1.3 - **Given** a `superadmin` or `admin`, **when** they call the route, **then** 200 (the
service's existing bypass).

## Item 2 - the API-key path is unchanged for a key whose act-as user holds the slug

UAC2.1 - **Given** an integration whose act-as user holds the route's slug, **when** the route is
called with that integration's `X-API-Key` and no Bearer token, **then** 200.
UAC2.2 - **Given** an integration whose act-as user does NOT hold the slug, **when** the same
call is made, **then** 403 (the key is not a bypass).
UAC2.3 (REG) - the API-key assertions run through the real `get_current_user_or_api_key` and
`resolve_integration_principal`, not a dependency override.

## Item 3 - lookup routes stay open to sessions, closed to keys without the slug

UAC3.1 - **Given** a session with no permission at all, **when** it calls `GET /promotion-types/`,
`GET /campaign-types/`, `GET /directories/` or `GET /attachment-types/`, **then** 200.
UAC3.2 - **Given** an API key whose act-as user lacks the lookup's slug, **when** it calls the
same route, **then** 403.

## Item 4 - nothing else changes

UAC4.1 (REG) - a session-only route gated with `require_permission` keeps refusing `X-API-Key`
with 401 (no new key door is opened).
UAC4.2 (REG) - the self-scoped routes listed in the plan's "Left as they are" section keep
answering a user with no module slug (not tested here; named so the scope is explicit).
