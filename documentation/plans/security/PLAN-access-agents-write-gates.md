# PLAN - access-agents write gates

**Status:** In progress. Small fix track (owner rulings 18 Sep + 24 Sep 2026): no migration, no
new permission, one file of route dependencies plus its tests. Red-first.
**UAC:** `documentation/plans/security/access-agents-write-gates-acceptance-criteria.md`
**Classification:** CORE (`user_management` is base-platform; not an installable module).
**Lane:** ACCESS-AGENTS-AUTH (crew), branch `claude/access-agents-auth-1n9g8n`.
**Found by:** the ACCESS-MODEL lane's security review (5 Oct 2026, PR #1434). Pre-existing on
main.
**Follows:** `PLAN-user-management-read-gates.md` (the GETs in the same file, same defect class,
same fix shape) and the `contact-access-types` write gates landed 1 Oct 2026
(`app/api/v1/user_management/contact_access_types.py`, the `MANAGE` constant).

## The defect

Every write route in `sorento_crm_backend/app/api/v1/user_management/access_agents.py` takes
`Depends(get_current_user)` only. That proves a session exists and nothing about role. The six
GETs in the same file were gated on `user_management.access_agents.view` by the read-gates lane;
the ten writes beside them were not touched, so any staff login can create, rename, deactivate
or delete a chatbot access agent, rewrite its team routing, change which fields it may reveal,
and add or remove the contact grants that decide which dealer may talk to it.

The frontend hides nothing either: `actions.tsx` gates only the Delete item on
`user_management.access_agents.delete`; the Add and Edit modals render for anyone holding
`.view`. The backend dependency is the entire gate.

## Audit (origin/main at 05ddec69)

| # | Route | Line | Today | Gate after this plan |
| --- | --- | --- | --- | --- |
| 1 | `POST /access-agents/` | 92 | `get_current_user` | `user_management.access_agents.add` |
| 2 | `PUT /access-agents/{agent_id}` | 109 | `get_current_user` | `user_management.access_agents.edit` |
| 3 | `DELETE /access-agents/{agent_id}` | 128 | `get_current_user` | `user_management.access_agents.delete` |
| 4 | `PUT /access-agents/{agent_id}/teams` | 162 | `get_current_user` | `user_management.access_agents.edit` |
| 5 | `PUT /access-agents/{agent_id}/field-access` | 222 | `get_current_user` | `user_management.access_agents.edit` |
| 6 | `POST /access-agents/{agent_id}/contact-access` | 273 | `get_current_user` | `user_management.access_agents.edit` |
| 7 | `PUT /access-agents/{agent_id}/contact-access/{contact_id}` | 295 | `get_current_user` | `user_management.access_agents.edit` |
| 8 | `DELETE /access-agents/{agent_id}/contact-access/{contact_id}` | 317 | `get_current_user` | `user_management.access_agents.edit` |
| 9 | `POST /access-agents/contact-access/lookup` | 336 | `get_current_user` | `user_management.access_agents.edit` |
| 10 | `POST /access-agents/{agent_id}/contact-access/{contact_id}/sync-contact-name` | 351 | `get_current_user` | `user_management.access_agents.edit` |

The six GETs (lines 27, 56, 73, 146, 191, 254) already carry `.view` and are not changed.

## Decisions

**D1 - reuse the registered CRUD slugs, no migration.** `app/rbac/permission_registry.py:36`
registers `_crud("user_management", "access_agents", ...)`, which yields `.view`, `.add`,
`.edit`, `.delete`. All four have been rows in `user_permissions` since migration
`061_seed_rbac_permissions` (and every `sync_permissions` call since). `.delete` is already
the slug the deferred-action engine checks for `access_agent.delete`
(`app/services/record_actions.py:1161`) and the slug the frontend's Delete item is gated on
(`actions.tsx:30`). The task card's fallback names (`access_agents.manage`,
`user_management.reference_data.manage`) are not needed: the first does not exist, and the
second is the shared reference-catalog slug, not an agent slug.

**D2 - one slug per verb, sub-resources are edits of the agent.** Create and delete of the agent
row itself take `.add` and `.delete`. Everything that reshapes an existing agent - its team
routing, its field reveals, and its contact grants (create, update, delete, name sync) - is an
edit of that agent and takes `.edit`. A contact grant is a child row with no screen of its own;
splitting it onto `.delete` would mean a user who may edit an agent could add a grant but not
remove the one they just added.

**D3 - the Respond.io lookup takes `.edit`.** `POST /contact-access/lookup` is a POST that
mutates nothing, but it queries Respond.io for an arbitrary contact by identifier and returns
the name: a directory lookup that exists only to pre-fill the add-grant dialog. No frontend
caller uses it today (grep over `sorento_crm_frontend/` finds none). It is gated on the same
slug as the dialog it serves rather than left open as a PII lookup.

**D4 - frontend untouched.** The admin role bypasses `check_user_has_permission` outright
(`app/services/user_service.py:1551`), so every admin-driven screen keeps working. A role
holding `.view` without `.add` / `.edit` now gets a 403 toast from the Add and Edit modals
instead of a silent success. Hiding those buttons behind `useHasPermission` is a small,
separate frontend change and is noted in the PR body, not folded into a security fix.

## Tests

`sorento_crm_backend/tests/test_access_agents_write_gates.py`, written before the fix:

- one 403 per write route for a caller holding only `.view` (the strongest wrong caller: it
  can open every screen), with the slug named in the detail;
- one 200 per write route for a caller holding the right slug, each with a real round trip
  through the service on a scratch schema, except the two Respond.io calls which are stubbed
  at the service method;
- a structural sweep: every non-GET route mounted under `/access-agents` carries
  `require_permission`, so a new write cannot land ungated;
- the six GETs still answer `.view` (regression guard, cross-checked with
  `test_user_management_read_gates.py`).

## Coordination

PR #1434 (ACCESS-MODEL) touches `chatbot_roles.py`, not this file. No overlap.
