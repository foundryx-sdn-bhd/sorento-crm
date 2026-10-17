# PLAN - New product application (NEW-PRODUCT-APP)

Lane NEW-PRODUCT-APP, size L, cloud. Status: Review. Owner OKed card + mock v3 on 18 Oct (Q1 = portal too); crew PQ1 (b), PQ2 (a). Built: migration,
staff + portal BE and FE; reviewer round 1 fixed (6b68759f, 34a488d1); browser pass + hand test next.
Track: full (migration + new permissions). PR #1769, branch `claude/new-product-application-0u631d`.

Owner 15 Oct: stock inquiry about a new product code -> inquiry responded -> new product application ->
purchasing opens the product code after receiving the application.

- Behaviour card: `CARD-new-product-application-17oct.md` (the rules; this plan does not restate them).
- UAC: `new-product-application-acceptance-criteria.md`.
- Mock: `mockups/new-product-application-v3.html` (v1, v2 kept for the record).

## Design (simplest thing)

Copies the Sample Request (#1718) shape for the record and the quotation approval (#1698) for approve / lock /
revise. Paths: BE = `sorento_crm_backend/app/`, FE = `sorento_crm_frontend/`.

### Data (migration `npa_0001_new_product_apps`, parent = main's head at build time)

Public schema (procurement domain, like `stock_inquiries`), additive, idempotent (`IF NOT EXISTS`).

- `new_product_applications`: `id`, `company_id` NOT NULL, `application_no` (`NPA-000001`, unique per company),
  `status` VARCHAR(20) CHECK in (draft, submitted, approved, rejected, completed, cancelled), `applicant_id` -> users,
  `stock_inquiry_id` -> stock_inquiries ON DELETE SET NULL, `customer_id` -> customers SET NULL, `customer_name`,
  `project_name`, `needed_by` DATE, `remark`, `submitted_at`, `decided_at`, `decided_by` -> users,
  `reject_reason`, `cancelled_at`, `cancel_reason`, `completed_at`, `created_at`, `updated_at`.
- `new_product_application_lines`: `id`, `application_id` CASCADE, `line_no`, `proposed_code`, `description`,
  `further_description`, `category_id` -> product_categories, `base_uom_id` -> units_of_measure, `brand_id`,
  `item_type_id`, `supplier_id` -> suppliers, `supplier_item_code`, `cost_price` / `selling_price` NUMERIC(12,2),
  `expected_qty` NUMERIC(12,3) (all NULL-able), `remark`, `opened_product_id` -> products ON DELETE SET NULL,
  `closed_reason`, `resolved_by` -> users, `resolved_at`. CHECK: not both opened and closed.
- Permissions `procurement.new_product_applications.view|create|approve|open_code` in
  `rbac/permission_registry.py` + migration grants: all four to superadmin/admin; `create` copied from holders of
  `procurement.stock_inquiries.edit` (the `sr_0001` pattern).

### BE

- `models/new_product_application.py` (`CompanyScopedMixin`, `__audit_track__`, number stamp copied from
  `models/sample_request.py` `next_request_no`).
- `services/new_product_application_service.py`: create / update draft, `assert_unlocked`, submit (incl. from
  Rejected), approve, reject, revise, cancel, link / close / undo line, auto-complete, list / detail / serialize,
  product search (reuses `sample_request_service.search_products`), recipients (`approver_user_ids` shape from
  `project_notify_service.py:70`, with the slug as a parameter), notify after commit.
- `api/v1/procurement/new_product_applications.py`, mounted in the procurement router; select endpoint for
  `item_types` (none exists today).
- Stock inquiry detail payload gains `new_product_applications: [{id, application_no, status, opened_codes}]`.
- `record_actions.py`: `new_product_application.delete` (WINDOW_DESTRUCTIVE, OWN_RECORD) +
  `pending_actions.py` pre-check, as `sample_request.delete`.
- `audit_logs.py`: entity type -> `procurement.new_product_applications.view`.

### FE

`app/(protected)/procurement-management/new-product-applications/` (page, new, `[id]`, `[id]/edit`, loading.tsx,
components List / Detail / Form / Lines, hooks, services, types), menu entry after Stock Inquiries,
`StockInquiryDetail.tsx` gear item + "New product applications" row.

## Test list (red first, by the tester before the coder)

pytest `tests/test_new_product_application.py` on the Postgres fixture: one test per AC-NPA-01..53 BE criterion,
plus: company isolation (404 / not listed / ORM None), lock guard on every write route, number never reissued after
delete, notifications fire after commit only and a raising sender does not undo the move.
vitest: form pre-fill from an inquiry (text only, no debtor id), no auto-pick in the link search, clearable number
fields send null, Submit / Approve / Reject / Revise buttons per viewer and status, inquiry row empty state.

Whole-tree guards to run locally before handoff (they only run in the full suite, see #1763):
`tests/test_company_scope.py` (`expected_owned` count), `tests/test_list_api_perms.py`, audit entity type tests,
FE `loading-inventory.test.tsx`, `data-grid.nested.inventory.test.ts`, `config/menu.config.test.ts`. Run the full
backend suite and the full vitest suite once before asking for the `ci` label.

## Data

Real examples: `crew-data-request` on PR #1769 (17 Oct). Rows stay out of the repo; the mock uses one masked case.

## API contract (as built, BE green on 2bd5343b)

Staff, base `/api/v1/procurement/new-product-applications` (module `procurement`):

| Method + path | Who | Body / query | Answer |
|---|---|---|---|
| GET `` | any of view/create/approve/open_code (403 otherwise) | `status` (comma list), `q`, `stock_inquiry_id`, `applicant_id`, `page`, `limit`, `sort` (application_no, status, created_at, submitted_at, needed_by, customer_name), `dir` | `{items: Summary[], total, page, limit}`; create-only callers see their own |
| POST `` | create | Write | 201 Detail (draft) |
| GET `/prefill?stock_inquiry_id=` | create | | Write-shaped body copied as text from the inquiry + `stock_inquiry_number` |
| GET `/products?q=` | open_code | | `[{id, product_code, product_name, brand_name, ...}]` (empty q = []) |
| GET `/item-types?q=` | any NPA slug | | `[{id, item_type_code, item_type_name}]` |
| GET `/{id}` | owner or view/approve/open_code | | Detail |
| PUT `/{id}` | create + owner | Write (lines replace all) | Detail; 422 `new_product_application_locked` when submitted/approved; 409 when completed/cancelled |
| POST `/{id}/submit` | owner | | Detail; 422 names "Line N: ..." |
| POST `/{id}/approve` | approve | | Detail; 409 unless submitted |
| POST `/{id}/reject` | approve | `{reason}` required | Detail |
| POST `/{id}/revise` | owner or approve | `{reason}` required | Detail (draft); 409 once a line is resolved |
| POST `/{id}/cancel` | owner | `{reason?}` | Detail; 409 once a line is resolved |
| POST `/{id}/lines/{lineId}/link` | open_code | `{product_id}` | Detail (auto Completed when every line is done) |
| POST `/{id}/lines/{lineId}/close` | open_code | `{reason}` required | Detail |
| POST `/{id}/lines/{lineId}/undo` | open_code | | Detail (Completed returns to Approved) |
| Delete draft | owner | pending action `new_product_application.delete`, entity_type `new_product_application` | countdown, hard delete |

Write: `{stock_inquiry_id?, customer_id?, customer_name?, project_name?, needed_by? (YYYY-MM-DD), remark?,
lines?: [{proposed_code?, description?, further_description?, category_id?, base_uom_id?, brand_id?,
item_type_id?, supplier_id?, supplier_item_code?, cost_price?, selling_price?, expected_qty?, remark?}]}`.
Numbers are strings or numbers; null clears.

Summary: `id, application_no, status, status_label, source, applicant_id, applicant_name, stock_inquiry_id,
stock_inquiry_number, customer_name, project_name, first_line, lines_total, lines_done, opened_codes, needed_by,
submitted_at, created_at`.
Detail = Summary + `stock_inquiry_product_code, customer_id, customer_code, remark, decided_at, decided_by,
decided_by_name, reject_reason, revise_reason, cancelled_at, cancel_reason, completed_at, updated_at, lines[]`;
line = Write line + `id, line_no, category_code, category_name, base_uom_code, base_uom_name, brand_name,
item_type_name, supplier_code, supplier_name, opened_product_id, opened_product_code, opened_product_name,
opened_product_active, closed_reason, resolved_by, resolved_by_name, resolved_at` (money as "185.00", qty as "55").

Portal, base `/api/v1/public/portal` (X-Portal-Token, grant `new_product_application`, 403
`FORM_TYPE_NOT_VISIBLE` without it): GET/POST `/submissions/new_product_application` (list answers
`{data, total, page, limit}`), GET/PUT `/submissions/new_product_application/{id}`, POST `.../{id}/submit`,
`.../{id}/revise {reason}`, `.../{id}/cancel`; GET `/new-product-application/inquiries` (own responded),
`/new-product-application/prefill?stock_inquiry_id=` (own responded only, else 404),
`/new-product-application/lookups/{item-groups|uoms|brands|item-types}?q=` (`[{id, code, name}]`),
`/new-product-application/customers?q=`. Portal lines have NO supplier_*/cost_price keys and refuse them (422)
while `PORTAL_SUPPLIER_AND_COST` is off (crew PQ1 (b)).
