# PLAN: AutoCount cross-repo contract (shared-service plan 22, Appendix A)

**Status:** APPROVED 2026-08-30 (brief from the foundryx-shared-service session, Appendix A of
`foundryx-shared-service/documentation/plans/sprint-4/22-autocount-db-etl.md`, ACs AC-22-25..28).
Slices: A1-A4 DONE, review fixes DONE
(`fix(external): review pass on the AutoCount contract (slugs declared, dependent lines cancelled
not deleted, anchor ambiguity)`), PR next (A1 = commit
`feat(external): company-anchored ingest/read (A1)` on this branch; a commit cannot carry its own
sha, so the hash is in the handoff).
**Branch:** `feat/autocount-cross-repo-contract` off `origin/main` e1ba232ca.
**Worktree:** `.claude/worktrees/autocount-contract` (backend only; no dev server needed).
**UAC:** `autocount-cross-repo-contract-acceptance-criteria.md` alongside.

The shared service's `SorentoSink` (`service_backend/modules/autocount/sinks_sorento.py`) is coded
against the shapes below verbatim. Anything that deviates from Appendix A is listed in section 7
and is reported back to that session.

## 0. What already exists (measured on `origin/main`)

| Thing | State |
| --- | --- |
| `POST /api/v1/external/ingest/{entity}` + `POST /api/v1/external/read/{entity}` | Live for 6 masters (`product_categories`, `units_of_measure`, `warehouses`, `suppliers`, `customers`, `products`). Per-record SAVEPOINT verdicts, always-200, `?dry_run=true`, `MAX_BATCH=1000`. `app/api/v1/external/ingest.py`, `app/services/master_ingest_service.py`. |
| Company anchor | NONE on ingest/read. `MasterIngestService._apply` raw-INSERTs without `company_id` (all 6 masters are `CompanyScopedMixin`; migration 305 makes the column NOT NULL) and `_lookup_id` adopts by code with no company predicate. GRN already has `pin_scope_to_companies` (`app/api/v1/external/utils.py`) + `tests/test_external_company_anchor_scope.py`. |
| Integration principal | `get_external_api_user` -> `{"id", "integration_id", "integration_name", ...}`. `integrations.config_json` is JSONB (`foundryx-esb` row holds `{"base_url": ...}`). |
| Companies | `companies.code` unique (`SRT`, `MOCHA` on dev); `companies.autocount_ref` nullable, empty today. |
| `sales_agents` | Table exists (`app/models/sales_agent.py`), NOT `CompanyScopedMixin`: `company_id` NULL = shared row; unique index `coalesce(company_id, nil)` + `sales_agent`. Slugs `master_data.sales_agents.{view,add,edit,delete}` registered; `integration_foundryx_esb` holds view+edit, only `admin` holds delete. |
| `public.sales_orders` / `public.purchase_orders` | `app/models/order.py:360` / `app/models/procurement.py:627`, both `CompanyScopedMixin`, headers carry `source_system`/`source_ref`/(SO) `source_doc_no`, lines carry `source_system`/`source_ref`. SO statuses in use: `open`, `partially_delivered`, `fulfilled`; PO: `draft`, `active`, `partial`, `received`, `closed`, `cancelled`. Slugs `scm.sales_orders.*` / `scm.purchase_orders.*` exist ON THE DEV COPY OF PRODUCTION ONLY (a retired migration put them there; measured in the review: they are declared nowhere in the app, and no SCM route gates on them - the screens use `scm.dashboard.view` / `scm.reorder.run`). esb holds view+edit, only admin holds delete. |
| `IntegrationReferenceService` | `SUPPORTED_ENTITY_TYPES` allowlist did NOT contain `sales_agents` (measured in A2; section 0's original claim was wrong, and `test_integration_reference` pins the set exactly, so A2 adds it to both). Still does NOT contain `sales_orders` / `purchase_orders` (it has legacy `orders`/`order_lines`). `resolve()` returns `str`. A3 adds `sales_orders` + `purchase_orders` (and the exact-set test). |
| Grant migration pattern | `alembic/versions/414_product_set_grant_sweep.py` (create-if-absent slug, sweep grant from a source slug, `ON CONFLICT DO NOTHING`, mirrored downgrade). Head: `444_notify_email_on_mention`. |
| Stale branch | `sorento_crm-autocount` (PR #46, 300 commits behind main) carries an older SO/PO ingester keyed on `so_number = AC-{DocKey}` with wholesale line replacement and no company anchor. Reference only; nothing is cherry-picked. |

## 1. A1 - Company-anchored external ingest (gates everything)

**Resolution order** (one helper, `resolve_company_anchor(db, payload, principal) -> str`, in
`app/api/v1/external/company_anchor.py`):

1. Explicit `companyCode` at the top level of the request body (`{"companyCode": "SRT", "records": [...]}`,
   `{"companyCode": "SRT", "source_refs": [...]}`). Matched against `companies.code`, then
   `companies.autocount_ref`; case-insensitive on both. Unknown code -> 422 `UNKNOWN_COMPANY`.
2. Integration binding: `integrations.config_json ->> 'company_code'` for the calling
   `integration_id`, resolved the same way. A binding AND an explicit code that disagree -> 422
   `COMPANY_ANCHOR_AMBIGUOUS`.
3. Neither -> 422 `COMPANY_ANCHOR_REQUIRED`.

FOUR codes, not three. Two more came out of the review, and both were cases where the answer was
being decided by something nobody chose:

* A binding that resolves to NOTHING -> 422 `COMPANY_BINDING_INVALID`, naming the bound value and
  `config_json.company_code`. It used to report `UNKNOWN_COMPANY` quoting a value the caller had
  never sent, in a request whose own `companyCode` was fine - so the ESB would have gone looking
  for the fault in the one field it had got right. The binding is still resolved even when the body
  names a company: a stale binding is a defect to fix, not one to route around.
* A code matching MORE THAN ONE active company -> 422 `COMPANY_ANCHOR_AMBIGUOUS` stating how many.
  `companies.code` is unique but `autocount_ref` has no unique index, so `LIMIT 1` answered with
  whichever row the scan reached first and filed a whole sync under an arbitrary company.

The helper calls `set_company_scope(db, frozenset({company_id}))` so the ORM filter + auto-stamp
agree with the raw SQL below, and returns the id. Inactive company -> 422 `UNKNOWN_COMPANY`.

**Applied to:** `POST /external/ingest/{entity}`, `POST /external/read/{entity}`,
`POST /external/ingest/{entity}/deletions` (A4). Not applied to the other external routers (GRN et al
keep `pin_scope_to_companies`; out of scope).

**MasterIngestService changes** (`company_id` passed into the constructor):

- INSERT path stamps `company_id` for every `CompanyScopedMixin` table. For `sales_agents`
  (shared master) the insert writes `company_id = NULL`: the table's doctrine is "one row serves
  both companies", and partitioning it would split one agent's demand class (model docstring).
- `_lookup_id` adoption is scoped: `WHERE code = :v AND company_id = :cid` for scoped tables,
  `WHERE code = :v AND (company_id IS NULL OR company_id = :cid)` for `sales_agents`.
  `_product_columns` category/UoM lookups use the same scoped helper.
- Ref resolution keyed within company: after `refs.resolve()` returns an id for a scoped table,
  the row's `company_id` is checked; a mismatch is `FAILED` with
  `errors={"source_ref": "linked to a record in another company"}`, never an update.
- `_diff`, `_update` unchanged (they address by id).

**MasterReadService:** `read` resolves within the anchor the same way; a ref whose row belongs to
another company reports under `not_found`.

**Tests** (`tests/test_external_company_anchor_scope.py` EXTENDED, plus
`tests/test_master_ingest_routes.py` updated to send `companyCode`):

- ingest without an anchor -> 422 `COMPANY_ANCHOR_REQUIRED`; with unknown code -> 422; with
  integration binding and no body code -> 200.
- NULL-company regression: a created warehouse carries the anchor's `company_id`.
- Cross-company adoption blocked: the same code in company B, ingest anchored to A creates a new A
  row and leaves B's untouched. Pinned on `products`, not `warehouses`: `Warehouse.warehouse_code`
  still carries the pre-305 GLOBAL `unique=True` in the model, so a schema built by `create_all`
  cannot hold one code in two companies, while `Product` was brought into step with migration 305's
  composite. That model drift is real and out of scope here - it bites only schemas built from the
  models, never production.
- Cross-company ref: a source_ref linked to a B row, pushed under A -> `failed`, row unchanged.
- read under A for a ref that resolves to a B row -> `not_found`.

## 2. A2 - `sales_agents` EntitySpec

- `CanonicalSalesAgent(_Canonical)`: `code` (1..100), `description` (<=255), `is_active` (default
  true), `person_label` (<=100, optional).
- `EntitySpec("sales_agents", CanonicalSalesAgent, "sales_agent", _sales_agent_columns)`; columns
  written: `sales_agent`, `description`, `is_active`, `person_label`. `internal_note`, `follow_up`,
  `demand_class`, `location_group`, `source` are never in the column set, so a re-sync cannot touch
  them. Code stored upper-cased + trimmed (matches `sales_agent_service`).
- `_READ_COLUMNS["sales_agents"]` = code/description/is_active/person_label.
- `INGEST_PERMISSIONS["sales_agents"] = "master_data.sales_agents.edit"`,
  `READ_PERMISSIONS[...] = ".view"`, `DELETE_PERMISSIONS[...] = ".delete"`.
- Migration `445_autocount_grant_sweep` (shortened from `445_autocount_contract_grant_sweep`:
  alembic's own `alembic_version.version_num` is `varchar(32)` and that id was 34 characters,
  which `test_alembic_revision_ids` catches): sweep `master_data.sales_agents.delete`,
  `scm.sales_orders.delete`, `scm.purchase_orders.delete` onto every role holding the matching
  `.edit` slug (measured: for the six existing masters the `.edit` and `.delete` holder lists are
  identical, so this is the shape the surface already has).
- All eight `scm.sales_orders.*` / `scm.purchase_orders.*` slugs are DECLARED, in
  `app/rbac/permission_registry.py` (`_crud`) and created-if-absent by migration 445. They existed
  only on the dev copy of production, so on CI and on any fresh database neither the sweep's target
  nor its SOURCE was there: the sweep was a no-op and the document push would have 403'd for ever.
  `sync_permissions` skips a slug that exists, so the two paths cannot conflict.

## 3. A3 - Document ingest (SO + PO)

Endpoints: `POST /api/v1/external/ingest/sales_orders`, `POST /api/v1/external/ingest/purchase_orders`,
read-back `POST /api/v1/external/read/sales_orders|purchase_orders`. Same envelope, same verdicts,
same `dry_run`. `DocumentIngestService` in `app/services/document_ingest_service.py`; targets
`public.sales_orders` + `public.sales_order_lines`, `public.purchase_orders` + `public.purchase_order_lines`
(`app.models.order.SalesOrder`, `app.models.procurement.PurchaseOrder`; NEVER the `projects.*`
tables of the same name).

**Record shape (SO):**

```json
{
  "source_ref": "SO:1234",              // AutoCount DocKey, header idempotency key
  "so_number": "SO-000123",            // AutoCount DocNo; the adopt-by-number key on first sync
  "customer_ref": "DEBTOR:300-R009",   // integration ref of the customer (optional)
  "sales_agent_ref": "AGENT:SEAN I",   // integration ref of the sales agent (optional)
  "doc_date": "2026-08-30",
  "requested_delivery_date": "2026-09-15",
  "status": "open",                    // canonical: open | partial | fulfilled | closed | cancelled
  "internal_note": "...",              // AutoCount pushes this as raw RTF; stripped to plain text at ingest (app/utils/rtf.py)
  "ref": "THE MET KL",                 // optional, AutoCount SO.Ref, mapped by the peer - used only to resolve the project label (PLAN-so-project-label.md) when the note names none
  "po_ref": "PO-4500 123",             // optional, the customer's own PO number (AutoCount UDFNote via the shared-service mapping), max 255. SENT = written (blank clears), ABSENT = left untouched. Never feeds the project label (PLAN-so-po-ref.md)
  "lines": [
    {
      "source_ref": "SO:1234:1",       // AutoCount DtlKey, per-line key
      "product_ref": "ITEM:ABC-1",     // integration ref of the product (REQUIRED to resolve)
      "warehouse_ref": "LOC:BRW",      // integration ref of the warehouse (optional)
      "qty_ordered": 10, "qty_delivered": 4,
      "unit_price": 12.5, "discount": 0, "line_total": 125,
      "uom": "PCS", "required_date": "2026-09-15"
    }
  ]
}
```

PO is the same with `po_number`, `supplier_ref`, `issue_date`, `expected_date`, `currency`, lines with
`qty_ordered`, `qty_received`, `unit_cost`, `discount`, `line_total`, `uom`, `currency`, `expected_date`.
`extra="forbid"` on every model.

**Header ladder:** `refs.resolve("sales_orders", source_ref)` (within company) -> adopt by
`so_number` within company where the row has no integration reference (a number already linked to
another ref is `failed`, `ReferenceConflict`) -> create. On create `source_system='autocount'`,
`source_ref`, `source_doc_no = so_number`, `company_id = anchor`.

**Master refs:** `customer_ref`/`supplier_ref`/`sales_agent_ref`/`product_ref`/`warehouse_ref` are
integration references of the respective master entity types; an unknown one raises
`MissingReference` -> the whole record is `retryable`, nothing written. A missing OPTIONAL ref key
(absent/null) leaves the FK NULL; a present-but-unknown ref is retryable.

**Status mapping** (canonical -> Sorento): SO `open->open`, `partial->partially_delivered`,
`fulfilled->fulfilled`, `closed->closed`, `cancelled->cancelled`. PO `open->active`,
`partial->partial`, `fulfilled->received`, `closed->closed`, `cancelled->cancelled`. Unknown string
-> `failed` (`errors={"status": ...}`). Cancelled is an UPDATE; the row and its lines stay.

**Lines:** upsert by `(header_id, source_ref)` on the line table's `source_ref` column
(`source_system='autocount'`); lines of the header whose `source_ref` is not in the payload are
removed (including ref-less lines an earlier extract import created - the push is authoritative),
**unless something else references that line**, in which case it is `line_status='cancelled'` in
place with its id and its quantities untouched. `scm.loading_plan_line.po_line_id` is ON DELETE
CASCADE, so a delete there destroys a loading plan's rows outright; `stock_transfers.so_line_id`,
`scm.order_link_claim`, `projects.order_inquiry_rows.po_line_id`, `planning_change`,
`spo_allocations.po_line_id` and `picking_lines.po_line_id` are SET NULL and would be silently
orphaned. Worst on the FIRST sync of an adopted document, where every pre-existing line is ref-less
and all of them are replaced at once. The question is asked by the same probe the deletion endpoint
uses (`app/services/dependent_probe.py`, one copy, imported by both).
`line_status` = `cancelled` when the header is cancelled, else `fulfilled` when
`qty_delivered >= qty_ordered > 0`, else `open`. Every line row carries `company_id = anchor`.

**Read-back** returns the header fields in canonical names plus `lines[]` in the same shape, with
`entity_id` per header and per line. A master the header points at that carries no integration
reference reads back as `null` rather than as an invented ref, and a stored status outside the
canonical five (a locally raised PO sits in `draft`) reads back as itself. `internal_note` is
stored and read back as plain text with the RTF stripped on ingest, so a read-back diff against
the pushed RTF value is expected, not a bug.

**As built.** `DocumentIngestService` is parameterised by two `DocumentSpec` instances and exposes
the SAME constructor and `ingest()` signature as `MasterIngestService`, so the existing
`POST /external/ingest/{entity}` picks one on the entity name and there is no second router;
`DocumentReadService` mirrors `MasterReadService.current_state` on the read side.
`IngestOutcome` / `IngestResult` / `RecordResult` / `MissingReference` / `_field_errors` /
`_value_changed` / `_lookup_id` / `_is_company_scoped` are imported from the master service, not
forked. The writes go through the ORM models rather than raw SQL (the master service's shape):
`sales_orders`, `sales_order_lines`, `purchase_orders` and `purchase_order_lines` all exist a
SECOND time in the `projects` schema, so an unqualified table name in raw SQL is answered by
`search_path` - and `public.`-qualifying it instead would point a scratch-schema test at the real
database. The anchor is still stamped by hand on top of the ORM auto-stamp. Header-level diff only
on a dry run; lines are not diffed (a per-line before/after over a 200-line order buries the one
change that matters).

## 4. A4 - Deletion endpoint

`POST /api/v1/external/ingest/{entity}/deletions`, body `{"source_refs": [...]}` (+ optional
`companyCode`), `?dry_run=true`, batch <= 1000 (413 above). Guard: the router-level ingest guard
(`.edit`) plus a route-level `require_external_permission_for_path(DELETE_PERMISSIONS)` (`.delete`).
Entities: the 7 masters + `sales_orders` + `purchase_orders`.

Per ref, inside its own SAVEPOINT:

1. `refs.resolve()` within company -> none -> `not_found`.
2. Dependent check: every FK in `pg_catalog` that references the target table (cached per table
   per process) is probed with `SELECT EXISTS (... WHERE fk_col = :id)`. For documents the own
   line table is skipped as a referrer and the LINE table's referrers are probed instead. This
   is deliberate: `customers -> sales_orders.customer_id` is `ON DELETE SET NULL`, so a bare
   `DELETE` would "succeed" by orphaning every order. A dependent anywhere = no hard delete.
3. No dependents -> `DELETE` (lines cascade) -> `refs.unlink()` -> verdict `deleted`.
4. Dependents (or an `IntegrityError` on the DELETE) -> fallback update -> verdict `deactivated`:
   `is_active=false` for masters; `products`: `is_discontinued=true` and `is_active` untouched
   (`product.py:177`); `sales_orders`/`purchase_orders`: `status='cancelled'` (the document
   equivalent; they have no `is_active`). The integration reference stays linked.
5. Anything else -> `failed` + `errors`.

Response: `{"dry_run": bool, "summary": {"total","deleted","deactivated","not_found","failed"},
"records": [{"source_ref","outcome","entity_id", "errors"?}]}`.

**As built.** `DeletionService` (`app/services/deletion_service.py`) takes the SAME constructor as
the two ingest services and is mounted on `ingest_router` as `POST /{entity}/deletions`, so the
router's `.edit` guard applies and the route adds its own `.delete` one. `/{entity}` matches a
single path segment, so the two routes cannot shadow each other - pinned by a test that ingests a
warehouse after the deletion route exists. Body guards, batch cap and anchor resolution run in the
same order as the ingest, for the same reason.

Three details the section above did not fix:

* A reference resolving into ANOTHER company reads as `not_found`, not as a failure. It is not this
  caller's row, and distinguishing the two would confirm that a record exists somewhere it may not
  look.
* The document line probe is ONE query per referrer, joining the referrer to the line table on the
  header's FK, rather than loading the line ids and probing each. Same question, no array binding,
  and it does not grow with the line count.
* The referrer list is NOT cached. One catalogue query per record is cheap at this batch size; the
  trigger for caching is a deletion batch appearing in the slow-query log, and it is written down in
  the service rather than built now.

## 5. Order of work

A1 (tests first, then code) -> A2 -> A3 -> A4, all on one branch. Each slice: pytest on the touched
suites + `tests/test_external_permission_coverage.py`; full external suite before the PR. Report to
the shared-service session after A1 and after A4.

## 6. Out of scope

- Re-anchoring the other external routers (GRN/SPO/packing-list keep `pin_scope_to_companies`).
- The stale `sorento_crm-autocount` branch (PR #46) and its 12-entity mirror.
- Frontend. No UI changes; the mirror is read through existing pages.

## 7. Deviations from Appendix A (reported to the shared-service session)

1. `companyCode` is a top-level body field on all three calls, matched against `companies.code`
   then `companies.autocount_ref` (case-insensitive). Integration binding = `config_json.company_code`.
2. Document status vocabulary is fixed here: `open | partial | fulfilled | closed | cancelled`.
3. Document lines carry `product_ref` / `warehouse_ref` (integration refs), not codes.
4. Deletion of a document with dependents = `status='cancelled'` (verdict `deactivated`); there is
   no `is_active` on documents.
5. The dependent check is a FK probe, not "let the DELETE fail": SET NULL FKs would otherwise let a
   customer with orders hard-delete.
7. Document quantities and money read back as JSON NUMBERS, not strings: they are `Decimal` in the
   model and FastAPI's encoder renders a Decimal as a number. Same as the masters' `list_price` /
   `credit_limit` on this surface today, so it is stated rather than changed - but a sink that
   round-trips a float through a 4-dp quantity has to say so, not assume it.
8. A document line is addressed by the line's own `source_ref` (AutoCount's DtlKey). Two lines
   carrying the same one inside a single record is a validation failure, not last-one-wins.
9. A line absent from the payload that something else references is CANCELLED in place, not
   removed: same id, same quantities, `line_status='cancelled'`. The sink must expect a read-back
   to keep listing it, and must not treat its continued presence as a failed delete.
10. Two further 422 codes on the anchor: `COMPANY_BINDING_INVALID` (the integration's binding names
    no live company - a Sorento configuration fault, not the request's) and
    `COMPANY_ANCHOR_AMBIGUOUS` when one `companyCode` matches two active companies, which
    `autocount_ref` permits.
6. `sales_agents` rows are created SHARED (`company_id NULL`). Two companies pushing the same agent
   code under different source_refs: the second push is `failed` (`ReferenceConflict`, the row is
   already linked). ACCEPTED by the shared service 2026-08-30: it mints the agent ref UNQUALIFIED
   as `agent:{CODE}` (upper/trim), never company-qualified, so every company's push hits the same
   ref -> same shared row -> `updated`. Sorento side must therefore: (a) exempt shared tables from
   the A1 cross-company ref check (a NULL `company_id` row is visible to every anchor), (b) let
   `link()` re-link the same (source_system, entity_type, source_ref) from a different
   `integration_id` in place (it already does: it overwrites `integration_id`/`last_synced_at`),
   (c) pin this with a test: same `agent:X` pushed under company A then company B -> `created` then
   `updated`, one row. Master refs on document lines stay `{DatabaseName}:{key}` as pushed.

## 8. `products` wire addition: `bar_code` (2 Sep 2026, price-tag-feedback-r2 S7)

Flagged for the connector team - not part of Appendix A, added on the Sorento side for the tag
designer's barcode layer (issue #480):

- `CanonicalProduct` (`app/schemas/canonical_masters.py`) gains an optional `bar_code` field
  (`max_length=100`), alongside the existing `code` / `name` / `category_code` / `uom_code` /
  `brand_code` / `list_price` / `cost_price` / `is_active`.
- **Overwrite rule, not "last write wins":** a non-empty `bar_code` on an ingest overwrites
  `products.barcode`; an empty string or an absent field leaves whatever is already stored
  untouched - including a value typed by hand on the CRM product master, which the sink must not
  clobber by sending `bar_code: ""` as a matter of habit on a record it has no barcode for. Same
  shape as every other field here: sent means authoritative, omitted means "I have nothing to say
  about this column." See `master_ingest_service._product_columns` for the implementation and
  `tests/test_master_ingest.py::TestBarcodeOverwritePolicy` for the pinned (stored, incoming) ->
  result table.
- `products.barcode` is nullable and indexed, never unique: a placeholder product may carry none,
  and two products sharing one (or none) must not block either from syncing.

## 9. Contract version 2 (5 Sep 2026, `PLAN-autocount-document-ingest-v2.md`)

`GET /api/v1/external/contract` -> `{"version": 2, "entities": [...]}` (slug
`integration.contract.read`). The ESB gates every v2 key behind its own consumer-connection
setting; the endpoint is advisory. Every v2 key is optional and `extra="forbid"` stays, so a v1
payload still validates. Deviations from the v1 behaviour above, all deliberate:

1. **Master fallbacks on documents.** Headers accept `customer_code`/`customer_name`/`agent_code`
   (SO) and `supplier_code`/`supplier_name`/`agent_code` (PO, SPO; `agent_code` accepted and
   ignored there); every line accepts `product_code`/`product_name`/`warehouse_code` and
   `product_ref` becomes optional when `product_code` is sent. Resolution: ref -> code (upper/trim,
   anchor company) -> name (suppliers only, `(RMB)`-style suffix stripped) -> back-create for
   suppliers, sales agents (shared row) and customers (only when BOTH code and name are sent, pair
   keyed). Products and warehouses are never created. A SENT ref that does not resolve no longer
   fails the record when a code/name is also sent: the ladder falls through and the created or
   matched row is registered under that ref. `sales_orders.debtor_code` is always written from
   `customer_code`. Caps: `customer_code`/`supplier_code` 50 chars.
2. **Warnings.** A record verdict carries `warnings: [..]` when non-empty, fixed vocabulary:
   `customer_created`, `customer_unresolved`, `supplier_created`, `agent_created`,
   `unclassified_demand`, `warehouse_unresolved`, `ref_mismatch`. A back-create is reported, never
   silent. `ref_mismatch` = the master resolved by code/name is already registered under a
   DIFFERENT ref than the one sent; the original ref stays, the record lands (found on the first
   live run: the ESB had minted master refs with the watermark in the key).
3. **Unresolvable warehouse (D10).** A SENT `warehouse_ref`/`warehouse_code` that resolves to
   nothing lands the line with `warehouse_id = NULL` (SPO rows keep `location_code`) plus
   `warehouse_unresolved`, replacing the v1 `retryable`. Products stay `retryable`.
4. **`partial` sales orders (D6a).** Canonical `partial` on a SALES order is stored as `open`
   and reads back as `open` (the per-line `qty_delivered` carries the partial fact; every SCM
   reader keys on `open`). Purchase orders keep `partial -> partial`.
5. **Demand classification (D4).** SO header accepts optional `order_type` (fill-only, never
   restated). `demand_class` is derived: stored `order_type` -> payload `order_type` -> the agent's
   class -> the customer's market segment; never blanked or downgraded; when nothing classifies the
   record still lands with `unclassified_demand`. `demand_class` itself is rejected as a key.
   One exception (`PLAN-demand-class-agent-arrival.md`): a class is re-decided, once, the first
   time an agent with a demand class of its own arrives on a later push naming one; a push that
   carries no agent at all never blanks a stored `sales_agent_id`.
6. **Line adoption at cutover (D11).** For a header adopted by number whose lines carry no
   `source_ref`, incoming lines adopt existing rows in three passes: (product, warehouse-or-NULL,
   outstanding) with position tie-break; (product, warehouse-or-NULL) when exactly one remains;
   position when counts agree. Adopted rows keep their id and get `source_ref`/`source_system`.
   Optional `line_number` (int) on every line is position only, never stored. Every document verdict
   carries `lines: {adopted, created, updated, deleted, cancelled}` (dry run too). A push names
   the WHOLE document: lines not in the payload are deleted, or cancelled in place when referenced.
7. **`shipping_orders` entity.** `POST /ingest|read/shipping_orders`, `/ingest/shipping_orders/deletions`
   (slugs `scm.shipping_orders.edit|view|delete`). There is no header table: rows land in
   `spo_allocations` with `source_ref` (DtlKey) and `source_doc_ref` (DocKey); header `entity_id`
   is `null`; read-back is keyed by the DocKey. Line identity by DtlKey; xlsx-era rows adopted by
   (product, location) with the D11 passes; a leftover row on a re-push is CLOSED in place, never
   deleted (hard delete only through the deletions call, and only when unreferenced). A second
   DocKey claiming an `spo_number` with open rows is `failed`. Quantities are rounded half-up to the
   integer columns. `spo_number` max 50 chars.
8. **`SPO-` numbers under `purchase_orders` are `failed`** (`errors.po_number`), never written.
9. **`from_so_numbers: [str]`** on purchase-order and shipping-order lines -> one
   `scm.order_link_claim` per (so_number, po_number, product_code) with `source='autocount'`
   and the line/row id, then `resolve()`; unknown SO numbers stay claimed until the SO arrives.
   Max 50 entries per line; `lines` max 2000 per document.
10. **Post-write hooks, non-dry only, best-effort:** SO -> plan-exception snapshot + batch;
    PO -> CRM-raised recommendation POs superseded and order inquiries relinked
    (`trigger="autocount_ingest"`). `planning_change` batches are NOT produced by ingest (backlog).
11. **Error bodies.** Non-domain failures return `errors: {"_": "internal error; see server logs"}`;
    cross-company conflicts are filed under the field (`errors.customer_ref`, ...) with the wording
    "already claimed outside this company anchor".
12. **Document-level line hard delete stays behind `.edit`** (a push with fewer lines removes
    unreferenced lines), while `/deletions` needs `.delete` on top. Stated choice: the push is the
    book of record. Shipping-order rows are the exception (always closed).
13. **Permission consequence of back-create (recorded):** a document `.edit` slug can mint
    suppliers/customers/agents. Accepted for the single trusted ESB; gating on the master's own
    `.edit` slug is backlog BL-057 with the trigger "a narrowly-scoped integration".
14. **`integration_references` is global** (no company column). Refs are minted
    `{DatabaseName}:{AutoKey}` on the ESB side, so two AutoCount books cannot collide; a second
    book connecting is the trigger for BL-056.
15. **Customers master: `credit_limit` and `payment_terms_days` are accepted and ignored.** No
    column exists on `customers` for either (found on the first live customers push, a v1 defect);
    they are dropped until Sorento carries AR terms. Every master-ingest error body is sanitized
    like the documents' (`internal error; see server logs`).

## 10. Contract v2.1 (ingest parity) (6 Sep 2026, `PLAN-ingest-parity-standardisation.md` S4)

Moved here verbatim from that plan's own section 4, which now points back to this section:
the version bump is a contract-surface change and this file is the ESB-facing contract of
record, not the internal ingest-parity plan.

`GET /api/v1/external/contract` -> `"version": "2.1"` (a STRING; a bare int can never express a
point release of the same major contract). New top-level keys `fields_added`, `fields_removed`,
`status_optional`, `absent_vs_null`, `warnings` (`app/api/v1/external/contract.py`).

Additions: products `is_discontinued`, `remark`, `brand_code`; customers `market_segment_code`,
`region`; sales_orders `customer_segment`, `customer_region`; shipping_orders `container_number`,
`is_shipping_order`; `status` optional on all three documents.

Removals (D15 end state): `CanonicalCustomer.credit_limit`, `.payment_terms_days`,
`.payment_terms_code`; `CanonicalSupplier.payment_terms_code`. These moved from
"accepted-and-warned `deprecated_field`" (the v2/S0-S3 contract) to fully REMOVED:
`extra="forbid"` now rejects a payload naming any of them with a field-named validation error
and `IngestOutcome.FAILED`, never retryable. Suppliers' contact/address block stays written (S0).

Semantics: on masters, absent = untouched, null = cleared (`absent_vs_null: true`).

Warnings vocabulary added: `category_created`, `uom_created`, `brand_created`,
`segment_unknown`, `lines.dropped`, `received_locked`, `container_unresolved`,
`supplier_ambiguous`. `retryable` no longer answers a missing product ref (the line is dropped
instead, counted under `lines.dropped`).

Retirement (AC-P4-1), same slice: the SO/PO history importers (`so_history_service.py`,
`po_history_service.py`, the `/purchase-history/*` and `/sales-history/*` routes, the RQ tasks
`process_sales_history_import` / `process_po_history_import`) were deleted outright. Closed
history now arrives through this same document-ingest contract instead of a separate
banded-report upload. `app/services/scm/history_sources.py`'s `source_system` stamp constants
(`scm_po_history`, `scm_spo_history`, `scm_so_history`) are NOT retired - rows already written
under them stay in the database and `outstanding_import_service` must keep recognising the
stamps forever, regardless of whether the writer that produced them still exists. The Order
Inquiry sheet (Project Sales, ADR 0010) is unaffected and keeps its `/order-inquiry/*` routes.

## 11. Contract v2.4 (products code-wins) (20 Sep 2026, `PLAN-ingest-products-code-wins.md`, SR0)

Full design in that plan; UAC `ingest-products-code-wins-acceptance-criteria.md`. Recorded here,
short, for the same reason section 10 is: a version bump is a contract-surface change and this
file is the ESB-facing contract of record.

`GET /api/v1/external/contract` -> `"version": "2.4"`. `fields_added` gains `products_deletions:
["codes"]`; `field_notes` gains the two SR0 notes on `products`/`products_deletions`.

Products only: `MasterIngestService._apply_scoped`'s adopt-by-code branch no longer raises
`ReferenceConflict` when the matched row's existing reference is under the SAME source system
this push links under (9,067 SRT products already claimed by SO/PO line ingest, since the
AutoCount HTTP source exposes no numeric item key). It resolves instead - the record updates, the
STORED reference stays exactly as it is, the incoming one is never written, and the verdict
carries the `ref_mismatch` warning (the constant document-line resolution already uses for the
identical situation). A reference under a DIFFERENT source system still conflicts, and every other
master entity's adopt branch is unchanged.

`POST /ingest/products/deletions` gains an optional `codes` (source_ref -> code) body field, read
only for `products`: when the reference misses, the code is looked up the same
case/whitespace-insensitive way adoption does, and proceeds only when the match is unlinked or
under the same source system - same predicate, same warning.

## 12. billing_documents (contract 2.6)

27 Sep 2026, finance S0 (#1309). Plan: `documentation/plans/finance/PLAN-finance-billing-documents-27sep.md`
(3.3); UAC `finance-billing-documents-27sep-acceptance-criteria.md` S0. The CRM side is built; the
shared service's sink is not. This section is the contract of record for it.

**Endpoints.** The existing surface, one new entity, same envelope and verdicts as every other:

| Call | Slug | Body |
| --- | --- | --- |
| `POST /api/v1/external/ingest/billing_documents` (`?dry_run=true` optional) | `finance.billing_documents.edit` | `{"companyCode": "SRT", "records": [...]}` |
| `POST /api/v1/external/ingest/billing_documents/deletions` (`?dry_run=true` optional) | `.edit` and `finance.billing_documents.delete` | `{"companyCode": "SRT", "source_refs": [...]}` |
| `POST /api/v1/external/read/billing_documents` | `finance.billing_documents.view` | `{"companyCode": "SRT", "source_refs": [...]}` |

`GET /api/v1/external/contract` answers `"version": "2.6"`, lists `billing_documents` in
`entities`, every record key and every `lines.<key>` under `fields_added.billing_documents`, and
the rules below under `field_notes.billing_documents`. Batch cap 1000 records (413
`BATCH_TOO_LARGE` above it); a body without a `records` (or `source_refs`) array is 422
`INVALID_BODY`; the company anchor is unchanged (422 `COMPANY_ANCHOR_REQUIRED` with no
`companyCode` and no binding, 422 on an unknown or ambiguous code). The migration grants the
three slugs the doors need to every role holding `scm.sales_orders.edit` (Q16, built as (a)).

**Record shape** (`CanonicalBillingDocument`, unknown keys refused):

```json
{
  "source_ref": "SRT_DB:IV:88213",
  "document_type": "invoice",
  "doc_no": "IV-2609/0142",
  "doc_date": "2026-09-26",
  "status": "posted",
  "source_modified_at": "2026-09-26T09:14:02",
  "customer_ref": "SRT_DB:300-R009", "customer_code": "300-R009", "customer_name": "...",
  "agent_code": "SEAN I",
  "currency_code": "MYR", "currency_rate": 1,
  "net_total": 1000.00, "tax_total": 100.00, "total": 1100.00, "local_net_total": 1000.00,
  "against_doc_no": null, "against_source_ref": null,
  "ref": "THE MET KL", "description": "...",
  "lines": [
    {
      "source_ref": "SRT_DB:IV:88213:1", "line_number": 1,
      "product_ref": "SRT_DB:ABC-1", "product_code": "ABC-1", "description": "...",
      "uom": "PCS", "quantity": 10, "unit_price": 100.00, "discount_amount": 0,
      "net_amount": 1000.00, "tax_code": "SV-10", "tax_rate": 10, "tax_amount": 100.00,
      "line_total": 1100.00,
      "from_doc_type": "DO", "from_doc_no": "DO-2609/0077", "from_line_ref": "SRT_DB:SO:1234:1"
    }
  ]
}
```

Required: `source_ref`, `document_type` (`invoice`, `cash_sale`, `credit_note`, `debit_note`),
`doc_no`, `doc_date`, `status` (`posted`, `cancelled`), `net_total`, `tax_total`, `total`,
`local_net_total`; per line `source_ref` and `quantity`. `currency_code` defaults to `MYR` and
`currency_rate` to 1. Money and quantities are JSON numbers. A record fails, with the field
named in `errors`, when: a key is unknown; `total` is not `net_total + tax_total` within 0.01
(`errors.total`); a line on an `invoice` or `cash_sale` has a negative quantity
(`errors["lines.N.quantity"]`); two lines share a `source_ref` or there are more than 2000
(`errors.lines`); the type or status is outside its list. The rest of the batch lands.

**Idempotency key.** `(company, document_type, source_ref)`: the `integration_references` row
(entity type `billing_documents`, one per document, scoped to the anchor company) and a unique
index on the table behind it. `source_ref` is `{database}:{IV|CS|CN|DN}:{DocKey}` (A2), so an
IV and a CN sharing a DocKey number are two documents; a ref already naming a document of
another type is refused (`errors.document_type`). Lines are keyed by their own `source_ref`
(DtlKey) within the document.

**Resolution.** Customer (`customer_ref`, then `customer_code`), sales agent (`agent_code`) and
product (`product_ref`, then `product_code`) are linked only when they resolve inside the anchor
company (a sales agent may also be a shared row). They are never created and a record is never
`retryable` for them: an unresolved one lands null with the code kept (`debtor_code`,
`agent_code`, `item_code`) and the warning `customer_unresolved`, `agent_unresolved` or
`product_unresolved`. A line may name no product at all (a charge line). A credit or debit note
links its document by `against_source_ref`, else by `against_doc_no` when exactly one invoice,
cash sale or debit note in the company carries that number; the number is kept either way, and
when that document lands later its ingest fills the link. `from_line_ref` links the line to the
anchor's sales order line whose `source_ref` it is; the three `from_*` values are kept as sent.

**Verdicts.** `created`, `updated`, `failed`, and new in 2.6 `unchanged`: the push is identical to
what is stored, or older than it by `source_modified_at` (warning `stale_ignored`); nothing is
written. `summary.unchanged` appears only when non-zero: read a missing key as 0. Each record
carries `entity_id` and `lines: {created, updated, deleted}`. Two concurrent FIRST pushes of the
same document can collide on the unique index; the loser answers `failed` with a `conflict`
error, and re-pushing it answers `unchanged` or `updated`.

**Versions, cancels and deletes.** A push names the whole document: an omitted optional field is
stored as null and a line not sent is deleted (the one exception to `absent_vs_null` on this
surface). A line sent again keeps its id. When both stored and incoming `source_modified_at`
exist and the incoming one is older, nothing is written. A push WITHOUT `source_modified_at`
always applies and clears the stored one, which switches the guard off for that document until
a timestamped push lands: always send it, backfill included. An offset timestamp is converted to
UTC; a naive one is taken as UTC. `status: "cancelled"` is an update: the
row and its lines stay, and every total leaves it out. `/deletions` hard-deletes a document and
its lines (`deleted`), or sets it `cancelled` (`deactivated`) when a credit or debit note still
points at it; an unknown or other-company ref is `not_found`.

**Backfill.** History arrives through the same endpoint, oldest first, invoices and cash sales
before the credit and debit notes of the same day, in batches of up to 1000. The CRM has
no start-date floor and no start-date setting; the start date is shared-service configuration
(the owner plans 1 Jan 2023, ruling Q2). Re-running any batch is safe. Each company pushes under its
own `companyCode`.

**Assumptions for the shared service to confirm** (plan 3.3.6; A9 is settled by ruling Q6: the
NDA scope is header, line, customer, agent, product and tax fields, nothing on bank, GL or
payments):

- **A1.** AutoCount exposes IV, CS, CN and DN headers with item detail (Sales-module tables, not
  the AR-module ones).
- **A2.** DocKey is unique per document table, not across them, so the ref carries the type; the
  line ref appends DtlKey.
- **A3.** Each header has a last-modified timestamp the ETL already watermarks on
  (`source_modified_at`).
- **A4.** A CN (and DN) names the invoice it is against at header level. If AutoCount only has
  per-line or knock-off allocations, ruling Q4 is revisited.
- **A5.** Headers carry `CurrencyCode`, `CurrencyRate` and a local (MYR) net amount
  (`local_net_total`); lines carry a tax code, rate and amount, net and gross.
- **A6.** A line transferred from a DO or SO names its source line, and the shared service can
  map a DO line back to its SO line ref (`from_line_ref`) when the IV came from a DO.
- **A7.** Cancelled documents stay in AutoCount with a Cancelled flag; deleted ones vanish and the
  ETL detects them as deletions.
- **A8.** Agent and customer codes on billing documents are the codes the SO feed sends.
- **A10.** The agent code on an IV transferred from an SO is the SO's agent code in the normal
  case; the shared service reports how often they differ (feeds Q15).

## 13. delivery_orders, goods_receive_notes, branches (contract 2.7)

29 Sep 2026, #1354 S2. Plan: `documentation/plans/autocount/PLAN-autocount-grn-do-ingest-29sep.md`
(section 1, of which this is a copy); UAC `autocount-grn-do-ingest-29sep-acceptance-criteria.md`.
The CRM side is built; the shared service's HTTP source, cursor and sink (S1, S4) are not. This
section is the contract of record for them. References to 2.1 and 2.3 below are the plan's
(the branch table, and the DO line columns).

### 13.1 Doors

| Call | Slug (existing, except the new `order_management.branches.*`) | Body |
| --- | --- | --- |
| `POST /api/v1/external/ingest/delivery_orders` (`?dry_run=true` optional) | `order_management.orders.edit` | `{"companyCode", "book", "records": [DO, ...]}` |
| `POST /api/v1/external/ingest/goods_receive_notes` (`?dry_run=true`) | `procurement.grn.edit` | `{"companyCode", "book", "records": [GRN, ...]}` |
| `POST /api/v1/external/ingest/branches` (`?dry_run=true`) | `order_management.branches.edit` (new) | `{"companyCode", "book", "records": [Branch, ...]}` |
| `POST /api/v1/external/ingest/delivery_orders/deletions` (`?dry_run=true`) | `.edit` + `order_management.orders.delete` | `{"companyCode", "book", "doc_date_from", "doc_date_to", "doc_keys": [..]}` |
| `POST /api/v1/external/ingest/goods_receive_notes/deletions` (`?dry_run=true`) | `.edit` + `procurement.grn.delete` | same |
| `POST /api/v1/external/read/delivery_orders` | `order_management.orders.view` | `{"companyCode", "source_refs": ["db1:DO:55120", ...]}` |
| `POST /api/v1/external/read/goods_receive_notes` | `procurement.grn.view` | `{"companyCode", "source_refs": ["db1:GRN:771", ...]}` |

`branches` has no read and no deletions door (404 `UNKNOWN_ENTITY`; its `.view` / `.delete` slugs exist only so every permission map covers every entity); trigger for adding them: a
branch deleted in AutoCount that must disappear from the CRM.

`GET /api/v1/external/contract` answers `"version": "2.7"` and lists the three entities.

### 13.2 Envelope

- `companyCode`: the company anchor, unchanged (422 `COMPANY_ANCHOR_REQUIRED` /
  `UNKNOWN_COMPANY` / `COMPANY_ANCHOR_AMBIGUOUS` / `COMPANY_BINDING_INVALID`).
- `book`: the AutoCount company book the records came from, the path segment of the vendor URL
  (`db1` = Sorento, ruling V9). Required on the three entities; 1 to 20 characters of
  `[A-Za-z0-9_-]`, else 422 `INVALID_BODY`.
- `records`: up to 1000 (413 `BATCH_TOO_LARGE` above). A body without a `records` array is 422
  `INVALID_BODY`.
- `backfill` (DO-HISTORY, additive, optional, `delivery_orders` only): `true` on a history
  replay batch (the 2023-09-01 DocDate backfill), never on the hourly poll. The batch is ingested
  exactly as without it, but writes no per-DO audit row and no per-row embedding event for the
  DO or its lines. Instead, in the batch's own transaction, it writes one `IMPORT` audit row
  (`entity_type` `orders`, the company, `new_values` with `backfill`, `book`, `records`,
  `doc_date_from` / `doc_date_to`, the verdict counts and `items`: source ref, order id and
  outcome of every created or updated DO), and queues one embedding event per written DO in one
  insert. Absent or `false` = today's behaviour. Not a boolean, or `true` on another entity: 422
  `INVALID_BODY`. The contract version string is not bumped for it (optional key;
  PLAN-do-history-backfill-13oct.md section 5).

### 13.3 Record = the AutoCount record exactly as the vendor API returned it

The shared service does NOT map fields. Each record is the header object from
`deliveryorderbyLastModified` / `...bydocdate` (or the GRN equivalents), with its `Details`
array, as returned. Unknown keys are accepted and kept: the whole record is stored as sent in
one JSON column (`source_record`, ruling Q1). The CRM reads these keys:

- DO header: `DocKey`, `DocNo`, `DocDate`, `DocStatus`, `Cancelled`, `BranchCode`,
  `DebtorCode`, `DebtorName`, `DeliverAddr1..4`, `DeliverContact`, `DeliverPhone1`,
  `SalesAgent`, `ShipVia`, `ShipInfo`, `Ref`, `RefDocNo`, `Remark1..4`, `Description`,
  `CurrencyCode`, `CurrencyRate`, `Total`, `Tax`, `NetTotal`, `LocalNetTotal`,
  `CreatedTimeStamp`, `LastModified`, `Details`.
- GRN header: the same minus `BranchCode`, `DebtorCode/Name`, `DeliverAddr*`, `SalesAgent`,
  plus `CreditorCode`, `CreditorName`, `SupplierDONo`, `PurchaseAgent`.
- Line (`Details[]`): `DtlKey`, `Seq`, `ItemCode`, `Description`, `Qty`, `FOCQty`, `UOM`,
  `UnitPrice`, `Discount`, `DiscountAmt`, `SubTotal`, `Tax`, `Location`, `BatchNo`,
  `DeliveryDate`, `ProjNo`; DO adds `YourPONo`, `YourPODate`; GRN adds `OurPONo`, `OurPODate`.
  When the vendor adds them: `FromDocType`, `FromDocNo`, `FromDocDtlKey`.
- Branch: `BranchCode`, `BranchName`, `AccNo` (see Q1 below).

Required: `DocKey` (integer or integer string), `DocNo`, `DocDate` (ISO date or datetime, or
`yyyyMMdd`); per line `DtlKey` (integer). `Details` absent is an empty list. At most 2000 lines;
`DtlKey` unique within the document. `LastModified` should always be sent (see 13.6). A record
over 1 MB serialised fails (`errors.record`).

`source_ref` (echoed in every verdict and used by the read door) is derived by the CRM:
`{book}:DO:{DocKey}` / `{book}:GRN:{DocKey}`. Lines: `{book}:DO:{DocKey}:{DtlKey}`. Branches:
`{book}:BR:{AccNo}:{BranchCode}`.

### 13.4 Idempotency and provenance

- Key: `(company, book, DocKey)`, a partial unique index on `orders (company_id, source_book,
  doc_key)` and `picking_headers (company_id, source_book, doc_key)`; lines by `(header,
  dtl_key)`. Branches by `(company, book, AccNo, BranchCode)`.
- Provenance on the row: `source_book`, `doc_key`, `source_modified_at` (LastModified),
  `source_record` (the record as sent), `last_synced_at`. No `integration_references` row: the
  key lives on the row because the row can predate the feed (adoption, 13.5).

### 13.5 Adopt by number

A DO whose `DocNo` equals an existing `orders.order_number` in the anchor company that has no
`doc_key` (created by the tracking upload's Master sheet or the DO detail import) is ADOPTED:
the row keeps its id and every tracking column, gains the AutoCount identity, and AutoCount
writes its own columns (2.3). Verdict `updated` with warning `adopted_by_doc_no`. Same for a GRN
on `picking_headers.picking_number` (`picking_type='goods_received'`).

Existing lines of an adopted document (no `dtl_key`) are matched one to one to the incoming
lines: DO by (product, warehouse, quantity), GRN by (product, quantity), first fit in `Seq`
order; a matched line keeps its id (and, on a GRN, its `spo_allocation_id` / `po_line_id`). An
unmatched old line is deleted. When a deleted GRN line carried an SPO or PO link, the record
carries warning `legacy_links_released` and the SPO receipt is recomputed.

A `DocNo` held by a row that already has a DIFFERENT `doc_key` is `failed` with
`errors.DocNo`. The rest of the batch lands.

### 13.6 Stale guard, unchanged, cancel

- `LastModified` is Malaysia time when naive (ruling V1), converted and stored with its zone.
- When both stored and incoming `LastModified` exist and the incoming one is OLDER: nothing is
  written, `unchanged` + warning `stale_ignored`.
- Otherwise (newer, equal or absent) the resolved values are compared with what is stored:
  identical answers `unchanged` and writes nothing (not even `last_synced_at`); any difference
  answers `updated`. So an equal timestamp with new content (for example the vendor adding the
  `FromDoc*` fields) still lands.
- `Cancelled` truthy (`T`, `Y`, `1`, `true`, `True`) is an update: DO `is_cancelled=true`, GRN
  `is_cancelled=true` and `picking_status='cancelled'`; the row and lines stay.

### 13.7 Resolution (masters are linked, never created)

- Customer (`DebtorCode`) in the anchor; unresolved: `customer_id` null, code and name kept,
  warning `customer_unresolved`.
- Product (`ItemCode`) and warehouse (`Location`): the line columns are NOT NULL on both tables,
  so an unresolved code makes the record `retryable` with `errors["Details.N.ItemCode"]` /
  `errors["Details.N.Location"]` (the SO / PO ingest rule: products and warehouses come through
  the same feed and arrive). A GRN line with no `Location` lands with no warehouse.
- A Details row with no `ItemCode` (a description-only row) is not written as a line; it stays in
  `source_record`, counted in `lines.skipped`, warning `line_without_item`.
- Branch: `BranchCode` + `DebtorCode` against the branch table (2.1); unresolved: code kept, name
  null, warning `branch_unresolved`. Empty `BranchCode` resolves nothing and warns nothing.

### 13.8 Links

| Link | Rule |
| --- | --- |
| DO line -> SO line (exact) | `FromDocDtlKey` present and `FromDocType` absent or `SO`: the anchor's `sales_order_lines` row whose `source_ref` ends in `:{FromDocDtlKey}` (the SO feed's `{database}:{DocKey}:{DtlKey}`), inside the SO numbered `FromDocNo` when sent. Exactly one match fills `order_lines.sales_order_line_id`; none or several: null, warning `so_line_unresolved`. |
| GRN line -> PO line or SPO line (exact) | `FromDocDtlKey` present: the anchor's `purchase_order_lines` or `spo_allocations` row whose `source_ref` ends in `:{FromDocDtlKey}` (or equals it), inside `FromDocNo` when sent. Exactly one across both fills `po_line_id` or `spo_allocation_id`; else null, warning `po_line_unresolved`. |
| DO -> SO (document number) | Only when `RefDocNo` is non-empty: the anchor's one `sales_orders` row with `so_number = RefDocNo` fills `orders.sales_order_id`; none or several: null, warning `sales_order_unresolved`. The line link stays null. |
| GRN line -> PO / SPO (document number) | Only when the line's `OurPONo` is non-empty and no exact link: the anchor's one `purchase_orders` row with `po_number = OurPONo` fills `picking_lines.purchase_order_id` and `from_doc_type='PO'`; else when an `spo_allocations` row carries `spo_number = OurPONo`, `from_doc_type='SPO'` (no SPO header table to point at); else warning `purchase_order_unresolved`. The line link stays null; the FIFO SPO matcher is NOT run and `spo_number_raw` is not written. |

`from_doc_type`, `from_doc_no`, `from_dtl_key` are stored as sent on every line.

**Later fill (Q10 a).** An unlinked line fills when (a) a later push of the same document
carries the link (content changed, 13.6), or (b) the SO / PO it names has arrived by the DB
transfer: at the end of every non-dry DO / GRN batch the CRM fills, for the anchor company,
every null link whose stored `from_dtl_key` / `RefDocNo` / `OurPONo` now resolves by the rules
above (one set-based UPDATE per link kind). Trigger for hooking the DB transfer itself: a DO
measured waiting more than a day for a link its SO already carries.

**Counter.** Every record's `lines` carries `linked` and `unlinked` (lines with no line-level
link) next to `created`, `updated`, `deleted`, `adopted`, `skipped`; a batch with unlinked lines
logs `ingest.unlinked_lines entity=.. company=.. count=..`. The S3 verification screen reads the
dry-run's per-record counters.

### 13.9 Verdicts and error shape

Unchanged from 2.6: always 200, `{dry_run, summary{total, created, updated, failed, retryable,
unchanged?}, records[{source_ref, outcome, entity_id, errors?, warnings?, lines?}]}`, one
SAVEPOINT per record. Warning vocabulary added in 2.7: `adopted_by_doc_no`, `branch_unresolved`,
`line_without_item`, `so_line_unresolved`, `po_line_unresolved`, `sales_order_unresolved`,
`purchase_order_unresolved`, `legacy_links_released`, `restored`.

### 13.10 Deletions (the sweep)

AutoCount hard-deletes and shows nothing (ruling V6). The shared service's sweep calls
byDocDate for a date range, compares with what it pushed, and sends the vanished `DocKey`s:

```json
{"companyCode": "SRT", "book": "db1", "doc_date_from": "2026-08-15", "doc_date_to": "2026-09-28",
 "doc_keys": [55120, 55121]}
```

For each key: no row in the anchor with that `(book, doc_key)`: `not_found`. A row whose stored
`DocDate` is outside the range: `failed`, `errors.doc_date` (the sweep only speaks for the days
it read). Otherwise the row is marked cancelled (DO `is_cancelled`, GRN `is_cancelled` +
`picking_status='cancelled'`) and `source_vanished_at` is set: `deactivated`. **Never deleted.**
Repeating it answers `deactivated` and writes nothing. Response shape = the existing deletions
shape. `doc_keys` over 1000: 413; missing dates, `from > to`, or missing `doc_keys`: 422
`INVALID_BODY`. A later push of a vanished DocKey (the sweep was wrong) clears
`source_vanished_at`, takes `Cancelled` from the payload and answers `updated` + `restored`.

### 13.11 Branches

`POST /ingest/branches`: each record a `branchbypage` row as returned. Required `BranchCode`.
Key `(company, book, AccNo or '', BranchCode)`. Verdicts `created` / `updated` / `unchanged` /
`failed`. When a branch's name changes (or a branch arrives after its DOs), every AutoCount DO in
the anchor with that `(book, DebtorCode = AccNo, BranchCode)` gets the new `branch_name`.

### 13.12 What the shared service must add (S1, S4)

HTTP source for `branchbypage`, `goodsreceivenotebyLastModified` / `...bydocdate`,
`deliveryorderbyLastModified` / `...bydocdate` per book; hourly poll on byLastModified for
yesterday and today (Malaysia time); backfill by DocDate one day per call from 1 Jan 2023;
branches daily and before the backfill; the deletion sweep over a trailing DocDate window
(45 days, scout Q9); push each record verbatim with the envelope above; the per-entity pull /
push switch (ruling Q5).

## 14. ar_outstanding (contract 2.10) and the billing pull

17 Oct 2026, lane AC-BILLING (PR #1770). Card: `documentation/plans/finance/CARD-ac-billing-17oct.md`;
UAC `finance-billing-documents-27sep-acceptance-criteria.md` S5; AutoCount schema reference
`documentation/plans/finance/REF-autocount-billing-schema.md`. The CRM side is built in this lane; the
shared service's billing presets and AR sweep are not (they need SELECT on the billing and AR tables,
after 25 Oct). Owner 15 Oct: outstanding per invoice and per customer, read-only; crew answers 17 Oct
Q1 (a) AutoCount's own debtor figure, Q3 (a) a per-debtor snapshot, Q4 (a) exact DO-line hop, Q5 (a)
amount and due date only (no receipts, knock-offs or ageing).

### 14.1 Doors

| Call | Slug | Body |
| --- | --- | --- |
| `POST /api/v1/external/ingest/ar_outstanding` (`?dry_run=true` optional) | `finance.billing_documents.edit` | `{"companyCode": "SRT", "records": [...]}` |
| `POST /api/v1/external/read/ar_outstanding` | `finance.billing_documents.view` | `{"companyCode": "SRT", "source_refs": [...]}` |

No deletions door: a debtor with nothing open is pushed with `total_outstanding: 0` and `documents: []`.
`GET /api/v1/external/contract` answers `"version": "2.10"`.

### 14.2 Record (one per debtor, the whole truth for that debtor)

```json
{
  "source_ref": "SRT_DB:debtor:1234",
  "customer_ref": "SRT_DB:1234", "customer_code": "300-R009",
  "as_of": "2026-10-26T09:00:00",
  "total_outstanding": 15230.40,
  "documents": [
    {"source_ref": "SRT_DB:IV:88213", "outstanding": 1100.00, "due_date": "2026-11-25"},
    {"source_ref": "SRT_DB:CN:4410", "outstanding": -200.00, "due_date": null}
  ]
}
```

- `source_ref` `{db}:debtor:{Debtor.AutoKey}`; `customer_ref` / `customer_code` resolve the customer
  (ref, then code) inside the anchor company; unresolved is `failed` with `errors.customer_ref`, never created.
- `total_outstanding`: AutoCount's own figure for the debtor in MYR (Q1 (a)), written to
  `customers.ar_outstanding`; `as_of` to `customers.ar_as_of`.
- `documents`: every OPEN AR item of the debtor whose AR row comes from an IV, CS, DN (`Outstanding`) or a
  CN (unapplied `NetTotal - KnockOffAmt`, sent negative), keyed by the billing document's 2.6 ref
  `{db}:{IV|CS|CN|DN}:{DocKey}`. AR entries keyed straight into AR are in the total, not in the list.
- A stored billing document of that customer with a non-zero outstanding that the push does not list is set
  to 0 (paid). A listed ref not in the CRM (or not that customer's) is skipped: warning `documents_unknown`.
- Stale guard on `as_of` against the stored `ar_as_of`: older is `unchanged` + `stale_ignored`; identical is
  `unchanged`. A first push for the debtor ref is `created`, later ones `updated`.

### 14.3 What the shared service must add

- **Billing presets (sink 2.6, section 12).** Four SQL presets (IV, CS, CN, DN) on the `_linked_preset`
  pattern, `key_columns=("DocKey",)`, watermark `LastModified`, `doc_date_column="DocDate"`, `from_date`
  2023-01-01; header and line mapping per the schema reference sections 2-3; the billing line cut (keep
  non-zero `SubTotal`, drop pure text lines); line fingerprint `COUNT(*), SUM(Qty), SUM(SubTotal), SUM(Tax),
  MAX(DtlKey), COUNT(FromDocDtlKey)` (+ `SUM(TransferedQty)` on IVDTL / CSDTL) in the header and the sweep;
  push IV and CS before CN and DN; reconcile vanishings to `/billing_documents/deletions`.
- **Line source refs.** `FromDocType='SO'`: the SO line's own ref `{db}:{SO.DocKey}:{SODTL.DtlKey}`.
  `'DO'`: `{book}:DO:{DocKey}:{DtlKey}` with the DO feed's book (section 13), so the CRM finds its own DO
  line exactly by `(book, doc_key, dtl_key)` and takes that line's sales order line. The hop is decided when the invoice is pushed, so the DO
  backfill must reach a day before that day's invoices are pushed; an invoice pushed before its DO stays
  unlinked until the invoice is pushed again. `'IV'` (CN lines):
  `{db}:IV:{DocKey}:{DtlKey}`. NULL `FromDocDtlKey`: send `from_doc_type` / `from_doc_no`, no ref.
- **AR sweep (sink 2.10).** Over `ARInvoice`, `ARDN`, `ARCN` per `DebtorCode`; a per-debtor fingerprint
  `SUM(Outstanding), COUNT(open), MAX(LastModified)` picks the debtors to push (a knock-off may not bump any
  `LastModified`); hourly, plus a daily full pass. AR row -> billing ref by `SourceType` / `SourceKey` if
  those columns exist, else `DocNo` + `DebtorCode` (to confirm on access).
