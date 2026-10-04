# UAC: NS-SHARED-LOOKUPS (never-stuck lever L10)

Plan: `PLAN-ns-shared-lookups.md`. "Restricted salesperson" = a signed-in, active user whose
roles grant the project-sales slugs (`projects.projects.view` / `.edit`) and nothing from user
management, system or master data.

## People picker

- UAC1.1 `GET /api/v1/user-management/users/lookup` answers any signed-in STAFF user (a role
  other than `portal_user` / `guest`) with 200 and a list of `{id, name}` objects. With
  `respond_synced=true` each row also carries `respond_user_id` (the SLA and complaint assignee
  filters key on it) and only Respond.io-synced users are listed. A portal caller gets 403
  `people_lookup_staff_only`.
- UAC1.2 Only ACTIVE, non-trashed staff are listed (no portal contacts, no integration
  accounts). `include_inactive=true` adds deactivated staff, for filters over past records
  (SLA, escalation logs, complaints, order inquiries, purchase requests); assignment pickers never
  pass it.
- UAC1.3 `?query=` narrows by name. It does not match on email.
- UAC1.4 No session: 401. `GET /users/select` still needs `user_management.users.view`.
- UAC1.5 The project owner, task assignee, escalation, lead assign / informant, order-inquiry
  "raised by", ticket watchers, complaint assignee, SLA assignee and folder "uploaded by" pickers
  list people for the restricted salesperson (any of these whose module they can open).

## Status flow

- UAC2.1 `GET /api/v1/project-sales/status-graph/{project|project_task|project_lead}` answers a
  `projects.projects.view` holder with the resolved graph (same shape as the admin route, no
  record counts). `?scope_id=` resolves a template's fork, falling back to the default.
- UAC2.2 Without `projects.projects.view`: 403 `Permission required: projects.projects.view`.
- UAC2.3 Any other entity type (quotation, sales_order, dealer-kit, inbound shipment, unknown):
  404. `/system/statuses/graph/*` still needs `system.statuses.view`.
- UAC2.4 The restricted salesperson sees the pipeline board columns, the status-move buttons on a
  project and a lead, and the task status dropdown.

## Contact access types, market segments

- UAC3.1 `GET /contact-access-types/` and `GET /market-segments/` answer any signed-in user.
- UAC3.2 `GET /contact-access-types/all`, `/contact-access-types/{code}` and every POST / PUT /
  DELETE on both catalogs keep their current slugs.
- UAC3.3 Promotions, file upload, brand forms and contact dialogs show the access-level and
  segment choices for a user without `reference_data.view`.

## Units, brands, categories, countries

- UAC4.1 The four `/master-data/{units-of-measure,brands,product-categories,countries}/select`
  routes answer any signed-in user (and an API key) with 200.
- UAC4.2 Each returns only the select fields (no chatbot weights or limits, no brand access
  levels or purchasing flag, no timestamps or product counts).
- UAC4.3 List, detail and write routes for the four keep their `.view` / `.edit` slugs.
- UAC4.4 The UOM and brand cells in the quotation, SO and PO line editors and the SCM filter bar
  category list are populated for the restricted salesperson.

## Roles (kept locked)

- UAC5.1 `GET /user-management/roles/select` still needs `user_management.roles.view`.
- UAC5.2 A screen that reads it shows an in-place "no access" in the picker, not an empty list
  or a crash (rendering delivered by #1418, L5; verified here once both are on main).

## Never widen write

- UAC6.1 No route that writes changes its dependency in this lane.
