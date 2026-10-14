# UAC - CS routing conditions (product lines, debtor type) + No CS finish

**Status:** Contract (owner answered the card 14 Oct 2026) · **Domain:** forms / procurement / customers
**Plan:** `documentation/plans/forms/PLAN-form-routing-conditions.md`
**Card:** `documentation/plans/forms/form-routing-conditions-behaviour-card.md` (Q1 a, Q2 a, Q3 a, Q4 a, Q5 b)

Tags: `[BE]` backend · `[FE]` frontend · `[T]` unit/service test · `[MIG]` migration.

## Journey

1. An admin opens a salesman's contact page, CS routing rules (existing screen). For Sponsorship Form they
   add a rule at the top: condition **Product lines** equals **No lines**, CS PIC **No CS (finish at
   approval)**. They save.
2. The salesman submits a Sponsorship Form with no product (PSSF26-0404 "Others: Rehda Annual dinner").
   The manager approves it. The customer gets the usual "approved" WhatsApp. The form shows
   **Completed**: no CS task, no CS email, no Processed / Close buttons, no portal revision, no Void.
3. A form with product lines matches the next rule and goes to its CS PIC exactly as today.
4. On the PR/SF form the salesman picks the customer from the customer list (code shown). The form
   remembers which customer. For a PR, Sales type fills in from the customer's debtor type
   (Project cash or Cash sales -> Cash Sales, Project -> Project); the salesman may still change it.
5. The admin can route by **Customer debtor type** (e.g. Project cash -> CS PIC Z) for PR and SF.
6. Office staff see and edit **Debtor type** on a customer (Customers edit + detail). After the release
   every 302- account reads Project cash, 301 "CASH (...)" walk-ins are blank, everything else Project.
   New customers (AutoCount sync, order import, manual) get the same rule on create.

## Group DT - customer debtor type

- **DT-1 `[MIG][T]`** GIVEN the migration runs, THEN `customers.debtor_type VARCHAR(20)` NULL exists with a
  check allowing only `project`, `project_cash`, `cash_sales` (or NULL).
- **DT-2 `[MIG][T]`** GIVEN customers before the migration: `302-E003 EMPIRE RESOURCES (PROJECT-CASH)`,
  `302-C069 CHARLES & YANG RENO` (no tag), `300-G101 GLOBAL INGRESS SDN BHD (PROJECT)`,
  `301-C039 CASH (PERSON A)`, `301-C066 PERSON B (PROJECT-CASH)`, a Mocha `300-` account, WHEN the
  backfill runs, THEN they read `project_cash`, `project_cash`, `project`, NULL, `project`, `project`.
  Rule: code starts `302-` -> project_cash; code starts `301-` AND name starts with the word CASH -> NULL;
  else project. Re-running the backfill changes nothing already set.
- **DT-3 `[BE][T]`** GIVEN a new customer created through the ORM with no debtor_type (AutoCount master
  ingest `_insert`, order import `_upsert_customer`, manual create, customer import, lead wizard), THEN the
  DT-2 rule fills it. GIVEN a debtor_type supplied on create, THEN it is kept. An update never re-derives it.
- **DT-4 `[BE][T]`** GET customer (detail + list rows) returns `debtor_type`; PUT/PATCH accepts one of the
  three values or null; anything else -> 422. Debtor type steers CS routing, so it has the Account level
  gate (`customers.py:244`): a CHANGE on update, or a non-null value on create, needs
  `order_management.customers.edit` (403 otherwise).
- **DT-5 `[FE][T]`** Customers edit form shows a clearable **Debtor type** select (Project / Project cash /
  Cash sales) and saves it; customer detail shows the label (empty state "-" when blank), same place
  and pattern as Account level.

## Group PC - form customer link + sales type auto-fill

- **PC-1 `[MIG][T]`** `purchase_requests.customer_id UUID NULL` FK `customers(id) ON DELETE SET NULL`,
  indexed. Existing rows untouched (NULL).
- **PC-2 `[BE][T]`** Portal `GET /public/portal/lookups/debtors?q=` returns customers matching name or code
  (active only, limit, the contact's companies via the portal company scope), each
  `{customer_id, debtor_name, customer_code, sales_type}` (no debtor_type on the portal: it is not shown);
  `debtor_name` keeps today's key so existing consumers (PriceTagRequestForm) keep working.
  `sales_type` = mapping of debtor_type: project -> `project`, project_cash -> `cash_sales`,
  cash_sales -> `cash_sales`, NULL -> null. The mapping lives in ONE constant.
- **PC-3 `[BE][T]`** Portal PR/SF draft save + submit accept `customer_id` (editable field whitelist,
  `portal_service._editable_fields`); an unknown customer_id, one outside the contact's companies, or a
  NEWLY linked inactive one -> 422 (same message for all); `""` = unlink (null), never a 500. When a
  customer is linked, the stored `customer_name` is that customer's name (server-derived), so the
  printed name and the routing customer cannot differ. A revision diff never shows `customer_id`
  (no UUID in UI); a re-pick shows as the Customer name change. AI-extract filling a new customer
  name clears `customer_id`.
- **PC-4 `[BE][T]`** Internal `GET /procurement/purchase-requests/lookups/customers?q=` returns the same
  item shape as PC-2 plus `debtor_type`. Gate = the PR edit route's (`purchase_requests.py:279`): an authenticated user
  inside the procurement module guard, no extra permission slug (none exists for PR view/edit);
  unauthenticated -> 401. Internal PR/SF create/update and update-and-reply accept `customer_id` with
  the same PC-3 checks (company scope, 422, server-derived name).
- **PC-5 `[FE][T]`** Portal SubmissionForm "Customer name" picker shows code as meta; picking a customer
  sets `customer_name` + `customer_id`; typing free text clears `customer_id`. For a PR, picking a
  customer whose `sales_type` is non-null sets Sales type to it; the field stays editable; picking a
  customer with null `sales_type` leaves Sales type unchanged.
- **PC-6 `[FE][T]`** Internal PurchaseRequestForm "Customer Name" becomes the same search (free text still
  allowed), same rules as PC-5.

## Group RC - routing conditions

- **RC-1 `[BE][T]`** `routable_fields('purchase_request')` and `('sponsorship_form')` include
  `{field:'product_lines', label:'Product lines', type:'enum', options:[has: Has lines, none: No lines]}`
  and `{field:'customer_debtor_type', label:'Customer debtor type', type:'enum',
  options:[project: Project, project_cash: Project cash, cash_sales: Cash sales]}`.
- **RC-2 `[BE][T]`** At CS-stage start the form values the rules see include `product_lines` = `has` when
  at least one line has a non-blank (trimmed) `item_code`, else `none` (0 lines, or only blank-code
  lines like PSSF26-0345); and `customer_debtor_type` = the linked customer's CURRENT debtor_type
  (NULL when no customer_id or blank type). If loading the form values fails, the No CS check is
  false and the form routes as before (a failure never completes a form).
- **RC-3 `[BE][T]`** A rule `customer_debtor_type equals project_cash -> CS PIC Z` wins for a PR whose
  customer is 302-; `not_equals project_cash` matches a free-text PR (NULL rule).

## Group NC - No CS outcome + Completed

- **NC-1 `[MIG][T]`** `respond_contact_cs_routing.cs_pic_user_id` becomes nullable. NULL = **No CS**.
- **NC-2 `[BE][T]`** Rules PUT (`replace_all`) and `upsert` accept `cs_pic_user_id: null` as No CS (no
  eligibility check); list returns `cs_pic_user_id: null, cs_pic_name: null`. Two rules with the same
  conditions are still rejected whatever their outcome.
- **NC-3 `[BE][T]`** GIVEN an SF whose requestor's winning rule is No CS, WHEN it is approved, THEN no CS
  tracker is created, `status = 'completed'`, `approval_status = 'approved'`, the approval-stage tracker
  resolves as today, and the approved automation runs with no CS PIC recipient (nothing sent to a CS PIC).
  The customer gets only the existing approved message (no second message).
- **NC-4 `[BE][T]`** GIVEN the winning rule is a PIC rule or nothing matches, THEN behaviour is unchanged
  (pinned PIC / round-robin, status stays `approved`).
- **NC-5 `[BE][T]`** A `completed` form: Processed by CS / Close -> 4xx (needs approved); Void -> conflict
  (terminal, like processed_by_cs); portal revision refused as terminal ("is completed and can no
  longer be revised"), whatever the allowed-statuses config says (same as processed_by_cs).
- **NC-6 `[FE][T]`** Rule editor CS PIC dropdown offers **No CS (finish at approval)**; picking it saves
  `cs_pic_user_id: null`; a reloaded rule shows it selected; a rule with no choice at all is still
  skipped on save as today.
- **NC-7 `[FE][T]`** `completed` shows as **Completed** (status pill, PR list, PR detail, portal label,
  portal-revision status options for PR + SF). PR/SF detail offers no Void on a Completed form.
- **NC-8 `[BE][T]`** The Sponsorships report counts Completed forms by default (`DEFAULT_STATUSES`) and
  offers Completed in its Status filter (`STATUS_LABELS`).
