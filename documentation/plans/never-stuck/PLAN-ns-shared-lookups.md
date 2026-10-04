# PLAN: NS-SHARED-LOOKUPS (never-stuck lever L10)

Status: Merge-ready (full track: RBAC read change), awaiting CI and the owner hand test. Lane NS-SHARED-LOOKUPS, PR #1423, branch
`claude/ns-shared-lookups-l10-rhjtxi`.

Source: `documentation/reference/NEVER-STUCK-UI.md` S4.6 and
`documentation/reports/AUDIT-never-stuck-2026-10-01.md` section 5 (both from PR #1416). Owner
ruling 1 Oct 2026 ("ok lookups"). UAC: `ns-shared-lookups-acceptance-criteria.md` alongside.

## Journey

A salesperson with only the project-sales grants opens Project Sales. Today the pipeline board
says "No pipeline stages configured", the status-move buttons are missing on every project and
lead, the owner / assignee pickers are empty, and the UOM / brand cells in the quotation, SO and
PO line editors offer nothing. The backend refuses each of those reads because they sit behind
an admin slug of the module that owns the table (`system.statuses.view`,
`user_management.users.view`, the master-data `.view` slugs, `reference_data.view`). After this
lane those shared lookups answer any signed-in user, read-only, with the fields a picker needs
and nothing more. Every write stays on its current gate.

## Owner ruling, per lookup

| Lookup | Ruling | Backend change |
|---|---|---|
| People picker | open, id + name, active only | NEW `GET /user-management/users/lookup` (`get_current_user`, staff callers only). Lists active, untrashed staff (a role other than `portal_user` / `guest`, not `is_integration`); a portal contact calling it gets 403 `people_lookup_staff_only` (security review round 1). `respond_user_id` only on the `respond_synced=true` opt-in the SLA and complaint assignee filters need. `/users/select` stays on `user_management.users.view`: it returns email, Respond.io ids and filters by phone. |
| Project / task / lead status flow | salesperson-readable, same pattern as the quotation-approval graph | NEW `GET /project-sales/status-graph/{project\|project_task\|project_lead}` on `projects.projects.view` (the same slug `quotation-approval-graph` uses, `quotation_documents.py:395`). Any other entity is 404. `/system/statuses/graph/*` stays admin. No record counts. |
| Contact access types, market segments | open read, editing locked | `GET /contact-access-types/` and `GET /market-segments/` relaxed from `reference_data.view` to `get_current_user`. Both return catalog rows only (code, name, description, flags), no personal data. `/contact-access-types/all` and `/{code}` keep their slugs. The access-type POST / PUT / DELETE took only sign-in; they now need `reference_data.manage`, the market-segment writes' slug (crew ruling 1 Oct), and the admin screen hides them without it. |
| Units, brands, categories, countries | open read | the four `/master-data/*/select` routes answer any signed-in session; an API key still needs its act-as user's `.view` slug (`require_session_or_api_key_permission`). Responses narrowed to a select schema (see below). List, detail and write routes keep their slugs. |
| Roles list | KEEP LOCKED | none. `/roles/select` stays on `user_management.roles.view`. The consumers are admin screens; their in-place "no access" comes from #1418 (L5: the role hook throws, `SearchableSelect` renders the refusal). |

Why a new people route rather than relaxing `/users/select`: that route's payload (email,
`respond_user_id`, `respond_synced`) and filters (`phone`, `respond_contact_id`, `unlinked`,
`trashed`, `company_id`) serve the user-admin and identity-linking screens. Relaxing it would
widen what a salesperson can learn about colleagues (look a user up by phone). A second, narrow
route is the smaller exposure.

Why a new graph route rather than relaxing `/system/statuses/graph`: the admin route serves every
registered entity (sales orders, dealer-kit, inbound shipments) and can return live record
counts. The project-sales one serves exactly the three graphs project-sales screens ride.

## Select schema (master data)

Narrowed to the fields the frontend consumers read: UOM `id, uom_code, uom_name, decimal_places`
(divisibility, front-planning plan 6.4); brand `id, brand_code, brand_name, is_active`; category
`id, category_code, category_name, is_active`; country `id, code, name` (unchanged). Admin
configuration (chatbot weights and limits, brand access levels, purchasing flags, timestamps,
product counts) leaves these four responses.

## Frontend

Kept to URL switches so it does not collide with #1418 (NS-FETCH-STATES), which owns the
picker / list failure rendering (L2, L3, L5) on the same files:

- `services/userSelectService.ts` gains `getUserLookup()`; business-module pickers (project
  sales, tickets, complaints, SLA, resource management, the purchase-request assignee filter, the
  notes and internal-comment @-mentions) switch to it. Filters over past records pass
  `include_inactive`. Admin screens (user management, roles, automation, integrations, settings)
  stay on `getUsersSelect`, and so does the purchase-request approver picker: it sends the
  approval link to the chosen person's email, which the lookup never carries.
- The project-sales status-graph fetch switches to `/project-sales/status-graph/{entity}`.
- Master-data, contact-access-type and market-segment consumers need no change: same URL, the
  gate moved.

## Audit rows this closes (29)

Appendix ids as the audit counts them (A-F rows by table position, E by its own `#`):

- People: A5, A6, A8 (user half), A14, A15, C14, C15 (user half), C16 (user half), E9, E10, E18,
  E20, E30. Top-40 #9.
- Status graphs: A1, A3. Top-40 #6, #7.
- Access types / market segments: C11, C12, D13, E6, F1, F2. Top-40 #20, #21.
- Master-data selects: A11, A12, A13, B15, D14, D25.
- Roles (kept locked, in-place no access via #1418): C1, C15 (role half), C16 (role half), E24.
  Top-40 #13.

Not in scope (same shape, different gate; per-screen lanes): parties / types / series pickers
(A4, A7, A9, A10, Top-40 #10, #22), complaint root-cause / resolution selects (E5, #19),
respond-workspaces select (C13), suppliers select (C37).

## Tests (red first)

`sorento_crm_backend/tests/test_ns_shared_lookups.py`: per lookup, a caller with no slugs gets
200 and only the minimal fields; inactive and trashed users are absent; the query does not match
email; each status graph opens on `projects.projects.view` and 403s without it; non-project
entities 404; the admin routes (`/users/select`, `/system/statuses/graph`, `/roles/select`,
`/contact-access-types/all`, master-data list) still 403; every write on the opened resources
still 403s. `tests/test_user_management_read_gates.py` is updated: its reference-catalog pin
encoded the gate the owner has now lifted. Frontend: vitest for `getUserLookup` and the graph
URL.
