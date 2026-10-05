# UAC - access-agents write gates

**Status:** Acceptance contract for `PLAN-access-agents-write-gates.md`. Written BEFORE
implementation. Follow-up to `PLAN-user-management-read-gates.md`, which gated the GETs in the
same router and left the writes as they were.

Legend: each criterion is **Given / When / Then**, testable. **REG** = explicit regression guard.

No `Journey` section: no screen changes, no new step, no data model. A permission dependency is
added to ten routes that already exist and already serve the same screens.

**Threat statement.** Every non-GET route in
`sorento_crm_backend/app/api/v1/user_management/access_agents.py` is `Depends(get_current_user)`
only. Any authenticated staff session, whatever its role, can create or delete a chatbot access
agent, change its team routing and field reveals, and add or remove the per-contact grants that
decide which dealer may talk to which agent.

---

## Item 1 - the agent row

UAC1.1 - **Given** a caller holding `user_management.access_agents.view` and nothing else,
**when** it calls `POST /api/v1/user-management/access-agents/`, **then** 403 and the detail
names `user_management.access_agents.add`.
UAC1.2 - **Given** a caller WITH `.add`, **when** it posts a valid agent, **then** 201 and the
row exists.
UAC1.3 - `PUT /access-agents/{agent_id}` behaves as 1.1 / 1.2 against
`user_management.access_agents.edit`.
UAC1.4 - `DELETE /access-agents/{agent_id}` behaves as 1.1 / 1.2 against
`user_management.access_agents.delete`, and the row is gone on 200.

## Item 2 - routing and reveals

UAC2.1 - `PUT /access-agents/{agent_id}/teams` and `PUT /access-agents/{agent_id}/field-access`
return 403 naming `user_management.access_agents.edit` for a `.view`-only caller, and 200 for a
caller holding `.edit`.
UAC2.2 - the field-access 403 fires BEFORE any field-access row is read (same property the
read gate has, UAC2.3 of the read-gates contract).

## Item 3 - contact grants

UAC3.1 - `POST /access-agents/{agent_id}/contact-access`,
`PUT /access-agents/{agent_id}/contact-access/{contact_id}`,
`DELETE /access-agents/{agent_id}/contact-access/{contact_id}` and
`POST /access-agents/{agent_id}/contact-access/{contact_id}/sync-contact-name` return 403 naming
`user_management.access_agents.edit` for a `.view`-only caller, and 201 / 200 / 200 / 200 for a
caller holding `.edit`.
UAC3.2 - `POST /access-agents/contact-access/lookup` returns 403 naming `.edit` for a
`.view`-only caller and 200 for a caller holding `.edit`. The Respond.io call is stubbed; the
gate is what is under test.
UAC3.3 - the contacts list `access_differs_from` filter follows the copy permission: a caller without `.edit` cannot copy agent grants, so contacts that differ from the source only on agent grants are not listed for them (and are listed for a caller holding `.edit`). Guarded by the two A2.5 tests in `tests/test_contacts_access_filters.py` (lane ACCESS-FILTER-FIX, 5 Oct 2026).

## Item 4 - structural coverage and regression

UAC4.1 - **Given** the routes mounted under `/api/v1/user-management/access-agents`, **then**
every non-GET route has `require_permission` in its dependency chain. A new ungated write fails
this test by name.
UAC4.2 (REG) - the six GETs in the same file still answer 403 without `.view` and 200 with it
(already asserted in `tests/test_user_management_read_gates.py`; re-asserted here for the two
that share a path with a gated write, so a slug mix-up on the PUT cannot pass unnoticed).
UAC4.3 (REG) - no migration: `.add`, `.edit`, `.delete` are already registered in
`app/rbac/permission_registry.py`. A test asserts the three slugs are in `PERMISSION_REGISTRY`.
