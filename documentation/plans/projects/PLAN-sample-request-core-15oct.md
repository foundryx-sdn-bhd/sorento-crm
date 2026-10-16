# PLAN: SR-CORE - Sample Request (consignment requisition) in project management

Status: Waiting on owner - card SETTLED (owner Q1 16 Oct 00:21, crew Q2-Q5 = (a)); mock v2
(`mockups/sample-request-core.html`, rebuilt from existing components after owner notes 16 Oct 08:54) awaiting owner OK; UAC drafted; this plan drafted 16 Oct. No product code until
crew relays the owner's mock OK. Track: full (migration, new permissions, portal ingest). Slice 1 of 3 (SR-STOCK,
SR-RETURN follow).

Discovery: `state/reports/SAMPLE-REQUEST-DISCOVERY-15oct.md` (crew), owner rulings 16 Oct 00:10.
Card: `CARD-sample-request-core-15oct.md` (the behaviour contract). UAC: `sample-request-core-15oct-acceptance-criteria.md`.
Paths: BE = `sorento_crm_backend/app/`, FE = `sorento_crm_frontend/`.

## Shape (simplest thing that works)

One model pair, one service, one staff router, one portal router, one FE feature folder. No registry, no new
abstraction. Every surface copies an existing sibling and names it below, with the justification that earned it.

| Concern | Copies | Why that sibling |
|---|---|---|
| Portal kind gated per contact | `price_tag_request`: `GRANTABLE_PORTAL_FORM_TYPES` BE `services/portal_service.py:95`, `_assert_visible` BE `api/v1/public/portal_price_tag.py:65-73`, literal-path router mounted before the generic one BE `api/v1/public/__init__.py:30-41` (D49) | Same Q3 answer (opt-in per contact, not every dealer); the generic `/submissions/{kind}` handler (BE `api/v1/public/portal.py:981-1182`) has no line grid |
| Request number SR-000001 | `StockTransfer` numbering BE `models/stock_transfer.py:147-183` | Per-company sequential, no numbering-rule row needed |
| Status flow | status engine entity registration BE `modules/projects/status_entities.py:51-56` + default graph seeder like `seed_quotation_approval_graph` BE `services/project_seed_service.py:737`, called from the seed summary at `:895` | Card rule 10: editable labels, history, and SR-STOCK inserts states between Processing and Customer confirmed |
| Settings switches | plain boolean columns on `SystemSetting` (BE `models/user.py:320`, like `deferred_action_seconds` `:443`), `SystemSettingUpdate` BE `api/v1/user_management/settings.py:~85`, GET dict `:324`, update impl `:753` | One preference = one column (CLAUDE.md); both manual dict builders (lesson) |
| PDF | `render_html` BE `services/pdf_render.py:153` | Same as PR / stock-inquiry PDFs |
| Project link for portal | `projects_for_contact` BE `services/sponsorship_link_service.py:46` | The sponsorship picker already answers "which projects may this contact name" |
| Debtor lookup for portal | `lookup_debtors` BE `api/v1/public/portal.py:499` | Existing portal debtor search |
| Dash-blind product fold | `func.upper(func.regexp_replace(Product.product_code, "[^A-Za-z0-9]", "", "g"))` BE `services/product_code_family.py:79` | Card rule 5; the fold already exists as an expression |
| Staff product picker | `GET /master-data/products/select` BE `api/v1/master_data/products_select.py:19` (ilike on code/name `:51-58`) | Add the folded-code OR clause there; it only widens hits, so every existing caller keeps its results |
| Stock panel | `OrderInquiryStockGrid` FE `app/(protected)/project-sales/_shared/components/OrderInquiryStockGrid.tsx:31` AS IS | Owner brief: reuse as is |
| Project tab | `TABS` FE `app/(protected)/project-sales/[projectId]/components/ProjectDetailClient.tsx:74-86` | New entry before `samples` (`:80`) |
| Feedback row on Delivered | `ProjectSample` BE `models/projects.py:1558` | Card rule 13 |
| Sidebar | `config/menu.config.tsx:147-185` Project Sales group | New item `/project-sales/sample-requests` |

## Slices (commits on this lane branch, folded by the captain)

### S1 Migration + models (BE)
- `alembic/versions/<n>_sample_requests.py` (revision id <= 32 chars; `./scripts/alembic-reparent.sh` onto main's head
  at the pre-PR gate). Creates `projects.sample_requests`, `projects.sample_request_lines` exactly as the card's
  "Data" section, CHECKs (project_id OR project_title; delivery_mode in `driver|self_deliver|self_collect`;
  loan_unit in `days|months`; goods_from in `demo|new|either`; source in `staff|portal`; qty >= 1; loan_value >= 1;
  NOT (delivery_date IS NOT NULL AND deliver_immediately)). Adds `system_settings.sample_request_require_project`
  and `sample_request_require_quotation` BOOLEAN NOT NULL DEFAULT false. Inserts the three permissions
  `projects.sample_requests.view|create|process` with idempotent raw SQL (pattern
  `alembic/versions/362_promotion_type_permissions.py`), granting view+create to holders of `projects.projects.edit`
  and process to admin/superadmin roles only (the coordinator role gets it by an admin grant; a permission granted to
  nobody would read as broken, so admin holds it). Downgrade drops only these objects.
- `models/sample_request.py`: `SampleRequest(Base, CompanyScopedMixin)`, `SampleRequestLine`; `__table_args__`
  schema `projects`. Numbering helper copied from `models/stock_transfer.py:147-183`.
- Status entity `sample_request` registered in `modules/projects/status_entities.py` `register()`; seeder
  `seed_sample_request_graph` in `services/project_seed_service.py` with keys `draft, submitted, processing,
  customer_confirmed, delivered, cancelled` and the edges of card rule 10, same wholesale guard as `:737`.

### S2 Service (BE) `services/sample_request_service.py`
- `create_draft(db, *, actor, data, source, contact_id=None, space_id=None)`; `update_draft`; `submit`; `move(to_key,
  payload)`; `cancel(reason)`; `list_for(...)`; `get_for(...)`; `render_pdf(req)`.
- Validation on submit (AC-SR-02/03/06/21/22): each refusal raises `AppException(422, message=<field sentence>,
  code=...)`; product checks on every write (AC-SR-10): product and alt product must exist, be active and visible in
  the company scope.
- Role rules (AC-SR-31/32): owner = `salesperson_id == actor` (staff) or `contact_id == token.contact_id` (portal);
  process moves need `projects.sample_requests.process`. Coordinator cancel of Processing needs a reason.
- On `delivered`: set `delivered_at`, `return_due` (date + loan; months via `dateutil.relativedelta`), and when
  `quotation_version_id` is set create a `ProjectSample` (submitted_on = delivered date) (AC-SR-33).
- Activity event per move when `project_id` is set (AC-SR-34), through the existing project activity writer.
- `request_no` assigned on draft create (AC-SR-01).
- List rows never carry `site_contact_phone` / `ship_to_address` (AC-SR-54): separate list and detail schemas.

### S3 Staff API `api/v1/projects/sample_requests.py`, mounted in `api/v1/projects/__init__.py:28-60`
- `GET /project-sales/sample-requests` (queue; filters status, project_id, q; DataGrid params; default open statuses;
  sort needed date asc, immediate first) - `projects.sample_requests.view`, or own rows only with `create`.
- `GET /project-sales/projects/{project_id}/sample-requests` (tab list).
- `POST /project-sales/sample-requests` (draft), `PUT /{id}` (draft only), `POST /{id}/submit`,
  `POST /{id}/move` `{to, date?, reason?}`, `PUT /{id}/internal` `{consignment_no, po_no}` (process),
  `GET /{id}`, `GET /{id}/pdf`.
- Settings: the two booleans in `SystemSettingUpdate`, the GET dict and `_update_general_settings_impl`, plus
  `/settings/app-config` so the FE form knows whether to require project / quotation without settings.view.

### S4 Portal API `api/v1/public/portal_sample_request.py`, mounted before `portal.router` in `api/v1/public/__init__.py`
- `sample_request` added to `GRANTABLE_PORTAL_FORM_TYPES` (NOT `SUPPORTED_TYPES`), `_KIND_LABELS`
  (`services/portal_form_visibility_service.py:38-47`), FE `lib/portal-form-kinds.ts` labels.
- Literal paths `/submissions/sample_request` (list, create), `/submissions/sample_request/{id}` (get, put draft),
  `/{id}/submit`, `/{id}/cancel`, `/{id}/pdf`, plus `/sample-request/products?q=` (folded search, active only,
  top 20) and reuse of the existing portal debtor + project lookups.
- Every route calls `_assert_visible` first (AC-SR-40) and loads by `(id, contact_id, space_id)` (AC-SR-41).
  A project id not in `projects_for_contact` is 422 (AC-SR-42). Salesperson = the contact's linked user
  (`price_tag_request_service.sales_agent_for_contact`), null when none.

### S5 FE staff
- `app/(protected)/project-sales/sample-requests/services/sampleRequestService.ts` (through `lib/api-client`,
  `extractApiError`, `buildDataGridParams`), hooks `useSampleRequestQuery` / `useSampleRequestMutations`.
- Mock v2 (owner notes 16 Oct 08:54): staff pages copy Purchase Request / Sponsorship Form / Stock Inquiry exactly;
  current status pill only, no flow visual. Each element maps to its component in `mockups/sample-request-core.html`.
- List `app/(protected)/project-sales/sample-requests/page.tsx`: Container + `PageHeader` title only, then the
  `PurchaseRequestsList` build (`procurement-management/purchase-requests/components/PurchaseRequestsList.tsx:467-606`):
  DataGrid + `DataGridListToolbar` (ListSearchInput, custom Filters popover, export, Create as `primaryAction`),
  status column `STATUS_PILL_BASE` + `statusPillClass` (`lib/status-pill.ts:97`), `rowHref`. Fixed layout + resizable
  per CLAUDE.md.
- Detail `[id]/page.tsx`: `PageHeader` + `BackToList`, `FormDetailWithSLATabs` (Details / Comments / SLA Tracking;
  `sample_request` added to the comment + SLA kinds, BE `services/form_comment_service.py:38`,
  `form_sla_service.py:33-42`), inner header as `PurchaseRequestDetail.tsx:596-614` with the pill in the subtitle
  like `StockInquiryDetail.tsx:378-396`, `DetailActions` (ListPager, gear `DetailActionsMenu` with Print, one plain
  button per allowed move, Edit last), document card as `PurchaseRequestDetail.tsx:1207-1345`, lines via
  `PanelDataGrid` (as `PurchaseRequestLineItemsGrid`), `AuditTrail` below.
- New / Edit are full pages (`new/page.tsx`, `[id]/edit/page.tsx`) like New Purchase Request, owner note 4/5 "reuse
  our system component"; same fields in the same order as the detail card.
- Lines editor: `SearchableSelect` per line over `products/select`; zero hits shows the card rule 5 sentence
  (AC-SR-12); "or" alternative is a second clearable `SearchableSelect`.
- Each line row expands `OrderInquiryStockGrid({productId, location: 'BRW'})` (AC-SR-53).
- Project tab: `TABS` entry `sample-requests` before `samples`, panel `SampleRequestsPanel`.
- Menu item under Project Sales gated on `projects.sample_requests.view`.
- System Settings: two switches in the existing General settings form.

### S6 FE portal
- `app/(auth)/portal/c/[slug]/sample_request/{page.tsx,new/page.tsx,[id]/page.tsx}` copied from the
  `price_tag_request` folder layout. `SampleRequestForm` copies `PriceTagRequestForm.tsx` structure: plain shell
  (`:2573-2640`, no branded header), collapsible `components/common/FormSection.tsx` sections (Customer & project,
  Lines, Delivery, Additional Information), project picker = portal `AsyncCombobox` + `lookupProjects` as
  `SubmissionForm.tsx:3031`, line table + `SearchableSelect` item picker (`:2725`, `:3324`) with the not-in-list
  sentence as `emptyMessage`, segmented control (`:2844`), footer Save Draft / Submit (`:2983`). Read-only view:
  same sections, "Status:" pill (`:2066`), gear menu with Print and Cancel while Submitted (AC-SR-43).

### S7 Print
- `templates/sample_request.html` (or inline HTML like the stock-inquiry PDF) rendered by `render_html`; board row
  first, lines with brand + model (+ "OR <alt>"), footer ticks, internal box (AC-SR-60/61).

## Test list (tester writes these RED first, Phase 2)

BE `tests/test_sample_requests.py` (Postgres fixture `tests/_pg_fixture.py`, own seeded chain: company, customer,
project, quotation version, products incl. one inactive, users with/without process):
1. draft create numbers SR-000001 then SR-000002 per company (AC-SR-01)
2. submit refusals, one per missing field, message names the field (AC-SR-02)
3. date XOR immediate (AC-SR-03); loan default + return_due days/months (AC-SR-04); goods_from default (AC-SR-05)
4. board ON without label refused; board never in lines (AC-SR-06)
5. unknown / inactive / other-company product and alt product -> 422 (AC-SR-10); qty 0 -> 422 (AC-SR-13)
6. folded search "tpe9101" lists TPE-9101 on both staff and portal search (AC-SR-11)
7. settings GET/PUT round trip, default false (AC-SR-20); switch ON refuses typed title, old request still opens and
   prints (AC-SR-21); quotation switch + foreign version (AC-SR-22)
8. every legal and illegal move (AC-SR-30); edit-only user gets 403 on process moves and internal fields; owner edit
   after submit refused (AC-SR-31/32)
9. delivered with version creates ProjectSample, without does not (AC-SR-33); activity event (AC-SR-34)
10. portal: no grant -> 403 on every route; other contact's id -> 403; project outside lookup -> 422 (AC-SR-40/41/42)
11. list responses carry no phone/address keys (AC-SR-54)
12. PDF returns `application/pdf`, starts `%PDF`, board label present in the HTML (AC-SR-60); stranger -> 403 (AC-SR-61)
13. migration up/down on a clean DB, single head (AC-SR-70)

FE vitest: lines editor not-in-list message and no auto-pick (AC-SR-12), submit-button field errors (AC-SR-02),
portal landing shows the card only when granted (AC-SR-40), tab order (AC-SR-50).

BR (agent-browser in the sandbox): AC-SR-01, 43, 50-53, 55 at 375 and 1280, sidebar-first navigation.

## Review

`reviewer` + `security-reviewer` (portal ingest + new permissions + company scoping) in parallel with the browser
pass. Kill test on the tester's tests.

## Open / risks

- The coordinator role name in prod is not in the repo; the migration grants `process` to admin roles only and the
  admin grants the coordinator. Say so in the hand-test.
- `products/select` change widens hits for every caller; covered by a test that the old exact-ilike hits remain.
