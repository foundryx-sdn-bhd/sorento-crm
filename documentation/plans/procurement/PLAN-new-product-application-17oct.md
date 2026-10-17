# PLAN - New product application (NEW-PRODUCT-APP)

Lane NEW-PRODUCT-APP, size L, cloud. Status: Plan. Card + mock v2 filed 17 Oct (crew answered Q2-Q5; Q1 with the
owner); mock refill on a real masked case pending crew's rows; no code until the owner OKs card + mock.
Track: full (migration + new permissions). PR #1769, branch `claude/new-product-application-0u631d`.

Owner 15 Oct: stock inquiry about a new product code -> inquiry responded -> new product application ->
purchasing opens the product code after receiving the application.

- Behaviour card: `CARD-new-product-application-17oct.md` (the rules; this plan does not restate them).
- UAC: `new-product-application-acceptance-criteria.md`.
- Mock: `mockups/new-product-application-v2.html` (v1 kept for the record).

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
