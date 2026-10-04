# PLAN - Customer importer

**Status:** Phase 1 + Phase 2 implemented on `feat/customer-importer` (uncommitted), plus two
fix passes (see the last two sections). Verified against real data: the 4,196-row AutoCount
debtor export imports **4,195 / 4,195 successful, 0 skipped, 0 failed** through the live UI,
down from 58 failures before the second fix pass. Phase 3 (review) not started. The last two
frontend changes (A2, A3) are not browser-verified - vitest evidence only, see the second fix
pass.
**Contract:** `documentation/plans/UAC-customer-importer.md` (every step below cites its AC)
**Executors:** planning here; implementation `coder` in a worktree; review `reviewer` + `/code-review`

## Shape

No new mechanism. This is the seventh entity to ride the existing queued-import pattern
(stock, DO, GRN, products, warehouses, SPO). Every piece already exists; the work is one
service, one task, one route, one dialog, one alias seed, and the tests.

```
customers list toolbar -> CustomerImportDialog (Test | Confirm)
  -> POST /api/v1/order-management/customers/import   (202, or 400 on no company scope)
     -> JobService.create_job(job_type="customer_import")   # snapshots company_id (303)
     -> store_import_source_file(job, original_bytes)
     -> enqueue_job(process_customer_import, queue_name="imports")
        -> _apply_import_job_scope  -> set_company_scope(frozenset({company}))
           -> customer_import_service.apply(db, rows, outcome)   # ORM writes only
              -> ImportOutcome per row -> flush() -> completion_counts() / finalize()
  -> notifyImportQueued() -> drawer polls -> system-management/import-jobs detail
```

## Phase 1 - frontend

Thin by design: the dialog is a copy of a settled pattern, not a new interaction. Build it
against a stubbed `onTest` / `onUpload` first so the states are exercised before the backend
exists, then wire in Phase 2.

1. `app/(protected)/order-management/customers/components/CustomerImportDialog.tsx`
 - Copy behaviour from `procurement-management/spo-allocations/components/SPOImportDialog.tsx`:
     `Test` then `Confirm`, warning-confirm `AlertDialog`, `sonner` toasts, `.xlsx,.xls`.
 - Use shared `components/common/FileDropzone.tsx`, NOT the hand-rolled drag handlers in
     SPO/GRN (AC-5.2).
 - Test result renders: created / updated / unchanged / needs-a-look counts, unmapped header
     names, and the first N row problems with a show-all toggle (AC-4.3, AC-5.6).
2. `.../customers/services/customerImportService.ts` - `validateCustomerImport(file)` and
   `importCustomers(file)`. Use `extractApiError`; do not hand-roll error parsing.
3. Wire `Import` into the customers list toolbar beside `Add` (AC-5.1). On 202:
   `notifyImportQueued()` (AC-5.3).
4. Stub hooks return each state: valid, invalid, warnings-only, partial. Screenshot the golden
   path plus the partial case for the PR.

## Phase 2 - backend, then wire off mocks

Order matters: 1 to 3 are independently testable before the route exists.

1. **Model fix** - `app/models/order.py`, `Customer.__table_args__`: replace the pre-305
   `Index("uq_customers_code_name_lower", lower(code), lower(name), unique=True)` with
   `Index("uq_customers_company_code_name_lower", "company_id", lower(code), lower(name),
   unique=True)` (AC-2.5). Do this **first** - AC-2.4's test asserts the opposite of what the
   stale model builds, so it fails for the wrong reason until this lands.

2. **Outcome code** - `app/services/import_outcome_codes.py`: add
   `CODE_EXISTS_UNDER_OTHER_NAME = "code_exists_under_other_name"` plus its `LABELS` entry
   (AC-6.1). One code, nothing else.

3. **Alias seed migration** - new revision seeding `import_field_alias` for
   `doc_type='customer'` (AC-4.2). Idempotent (`ON CONFLICT DO NOTHING` / guarded insert), in
   the shape the five existing doc types use. Cover at minimum: debtor code / customer code /
   account no / A/C code -> `customer_code`; debtor name / customer name / company name ->
   `customer_name`; and the obvious spellings for `email`, `phone_number`, `mobile_number`,
   `registered_name`, `trading_name`, `registration_number`, `industry`, `website`, `country`,
   `tax_id`, `salutation`, `first_name`, `last_name`, `market_segment_code`. **No
   `customer_type` alias** (ruling D1, UAC AC-3.5): `Debtor Type` carries Trade / Cash / Local.

4. **Reader** - `app/services/customer_import_reader.py`. Model on
   `app/services/scm/reorder_level_reader.py`: header row is the first row that resolves a
   customer code (AutoCount exports carry title lines above the table), returns
   `rows / problems / unmapped_headers / missing_columns / total_rows`. Required columns
   `("customer_code", "customer_name")` (AC-4).

5. **Service** - `app/services/customer_import_service.py`, two entry points sharing one
   resolver so Test and Confirm can never disagree (the `reorder_level_import_service`
   contract):
 - `preview(db, file_data) -> dict` - `persist=False` `ImportOutcome`, writes nothing.
 - `apply(db, file_data, outcome, actor) -> dict`.
 - Match per AC-1.1 on `(company scope, lower(btrim(code)), lower(btrim(name)))` via the ORM.
 - Per row: no match -> insert; match with changed values -> update only AC-3.1 fields;
     match with no change -> `outcome.unchanged()`, no write (AC-3.3).
 - `market_segment_code` fill-if-empty only (AC-3).
 - Blank cell = not supplied; never clear a populated field (AC-3.2).
 - Near-name check (AC-1.6): on insert, if a row exists under the same code in scope whose
     name is trigram-similar above threshold, still insert but record
     `code_exists_under_other_name` on the success outcome. Threshold and the exact `similarity()`
     call go in the service with a comment naming the two live examples from AC-1.6. Exact
     matches are already handled as updates and never reach this branch.
 - `IntegrityError` on insert -> `outcome.fail(code=UPSERT_ERROR)`, row fails, file continues.

6. **Task** - `app/tasks/import_tasks.py`: `process_customer_import(db_job_id, file_data,
   filename, user_id)` and `validate_customer_import(file_data, company_scope=...)`. Follow
   `process_grn_lines_import` exactly: `_apply_import_job_scope` first, `ImportOutcome`,
   `flush()`, `complete_job(**outcome.completion_counts(), result=outcome.finalize(...))`.

7. **Route** - `app/api/v1/order_management/customers.py`,
   `POST /customers/import`, 202, `validate_only` query param, mirroring
   `app/api/v1/procurement/grn.py:152-212`:
 - extension guard; `maybe_strip` for macro workbooks, retaining the pre-strip bytes;
 - `validate_only=true` -> run the validator at `get_company_scope(db)` and return 200
     `{valid, errors, warnings, summary}`;
 - **before creating the job**: if `active_company_id_from_scope(db)` is None, raise 400
     "Select a single company before importing customers." (AC-2.3);
 - `JobService.create_job(job_type="customer_import", ...)`,
     `store_import_source_file(job, source_bytes, source_name, source_ctype)` (AC-5.5),
     `db.commit()`, `enqueue_job(..., queue_name="imports", job_timeout=3600,
     job_id=str(job.job_id))`, `update_job_with_rq_id`.
 - Permission: a new `order_management.customers.import` slug, granted to the roles that
     already hold `order_management.customers.create`. Do not reuse a procurement slug.

8. **Wire FE off mocks.** Delete stubs; real service calls.

### Tests (land here, not deferred)

**pytest, Postgres only** - `tests/test_customer_import.py`. Every test seeds its own chain
with a marker prefix; no `LIMIT 1` off an existing table, no assertion about a live row. CI's
database is empty.

- insert a new customer -> `created`, `company_id` stamped from scope.
- same code + same name, changed email -> `updated`, only AC-3.1 fields moved.
- identical row -> `unchanged`, no write, `updated` counter not bumped (AC-3.3).
- blank email cell against a populated email -> value preserved (AC-3.2).
- `account_owner_user_id`, `notes`, `is_active` set on the existing row -> untouched by a
  re-import that names them (AC-3).
- `market_segment_code`: NULL -> filled; already set -> not overwritten (AC-3).
- same code, different name -> second row created, both survive (AC-1.2).
- **same code + same name under two companies -> both rows exist** (AC-2.4). This is the test
  that fails against the stale model, so it pins step 1.
- near-name under the same code -> inserted AND `code_exists_under_other_name` recorded (AC-1.6).
- unrelated name under the same code -> inserted, no flag (AC-1.5).
- missing `customer_name` -> skipped `MISSING_REQUIRED_FIELD`, rest of file imports (AC-5.6).
- mixed file (create + update + unchanged + skip) -> counters correct.
- route: no single company scope -> 400, no job row created (AC-2.3).
- route: happy path -> 202, `import_jobs.company_id` snapshotted, source file row written.
- auth denial on the new permission slug.

**vitest** - `CustomerImportDialog.test.tsx`: loading, empty, error, valid-test-result,
warnings-confirm, partial-result states; `notifyImportQueued` called on 202.

**playwright** - `e2e/customer-import.spec.ts`: sidebar -> Order Management -> Customers ->
Import -> upload a real committed fixture -> assert the drawer opens and the job appears.
Fixture goes in `e2e/fixtures/` as a real sample file, not a stub.

## Phase 3 - review

`reviewer` agent, then `/code-review`. PR description carries the Phase 1 screenshot, the three
AC-1/2/3 decisions, and confirmation that the alias seed is idempotent.

## Risks

- **The stale model (AC-2.5) inverts a test.** Step 1 exists to stop an hour lost to a
  correct test failing on a schema the model built wrong.
- **Near-name threshold is a judgement.** Too loose and every cash-sale row flags; too tight
  and typos slip through. Start strict, tune against the first real file, and log the count in
  the Test result so it is visible before Confirm.
- **AutoCount header spellings are unverified.** No sample export in hand. The alias seed is a
  best guess; the first real file will need alias rows added, which is a data change, not a
  release (AC-4.1). Say so in the PR rather than claiming the mapping is proven.
- **`_segment_of` reads a customer by `customer_code` alone with `limit(1)`**
  (`outstanding_import_service.py:446`). Given 225 names on one code, that already picks a row
  non-deterministically. Out of scope here, but it is a live bug adjacent to this work and
  should get its own ticket.

## Implementation notes (coder, 2026-08-13)

Deviations from the plan above, all deliberate:

1. **`down_revision` is `6f86dd016850`, not `352_scm_product_lifecycle_decision`.** 352 is
   already claimed as a parent by the merge revision `6f86dd016850`, which is the single
   filesystem head (`alembic heads`). Chaining onto 352 would have forked two heads.
2. **Two migrations, not one.** `353_customer_import_aliases` seeds the alias set;
   `354_customer_import_permission` inserts `order_management.customers.import` and grants it
   to every role that already holds `order_management.customers.**add**`. The plan said
   `.create`; that slug does not exist - `_crud()` emits `.add`.
3. **The alias seed is also replayed in `scripts/bootstrap_env.py`.** CI builds its database
   with `create_all` + `stamp head`, so a migration BODY never runs there: without the replay
   the alias table has no `customer` rows in CI and every upload reports every column
   unmapped. Same gap as 311 / 338 / 347, which are replayed for exactly this reason.
4. **`similarity()` is schema-qualified** from `pg_extension`, cached per process. An
   unqualified call fails on any session whose `search_path` excludes the extension's schema -
   including the scratch schema `blank_session()` builds - so the near-name flag would have
   silently vanished in the tests that exist to prove it.
5. **Three behaviours the plan did not specify**, now recorded in UAC AC-6.3: an unrecognised
   `market_segment_code` (a foreign key) is dropped and REPORTED rather than failing the row;
   an over-length cell fails that row alone with the column named rather than reaching
   Postgres, where the `DataError` would abort the whole transaction; a repeated (code, name)
   inside ONE file is skipped on the second occurrence, so the preview and the import agree.
6. **Near-name threshold: 0.75**, measured rather than guessed. See the comment on
   `NEAR_NAME_THRESHOLD` for the four measured pairs it sits between.
7. **`CountTile` moved to `components/common/`**; the SCM path re-exports it, so the customer
   dialog and the SCM upload dialogs render one tile rather than two copies.

## Fix pass (coder, 2026-08-13, after review)

Review found the implementation sound and the tests and reporting not. What changed:

1. **Two AC-3 guard tests were vacuous** and are now real. They fed headers
   (`Notes`, `Active`, `Account Owner`, `Company Id`) that migration 353 seeds no alias for, so
   the headers never resolved and the tests proved only "an unmapped header is ignored" - they
   still passed with the guards deleted. Both now seed hostile alias rows for every protected
   field, assert through `AliasResolver` that each header really does resolve, and then assert
   the values are untouched and the row reads `unchanged`. Mutation-verified (see the report).
2. **The permission-grant test runs migration 354's `upgrade()`** against a seeded role inside a
   rolled-back transaction, instead of asserting two string constants that stay true if the
   grant SQL is deleted. Plus an idempotency test, because the dev database replays migrations.
3. **The confirm dialog no longer claims a skip that did not happen.** The skip sentence comes
   from `summary.would_skip`; the warning count is stated separately in SPO's neutral wording.
4. **`customer_type` is insert-only** (ruling D1) - out of `UPDATABLE_FIELDS`, into
   `INSERT_ONLY_FIELDS`, and both alias rows removed from 353 (edited in place: uncommitted).
5. **`validate_only` is refused without a single company** (ruling D2): the scope check moved
   above the branch.
6. **A dropped market segment carries a per-row outcome code**
   (`MARKET_SEGMENT_NOT_RECOGNISED`, ruling D3), so the rows are named on the job detail
   instead of only appearing as a file-level list no screen renders.
7. **`DUPLICATE_IN_FILE`** replaces the reused `DUPLICATE_LINE`, whose shared label speaks of an
   order line (AC-6.2 reason recorded in the UAC).
8. **`total_rows` is published as soon as the sheet is read** via an `on_total_rows` callback on
   `apply()`, matching `process_grn_lines_import`, so the drawer stops reading 0/0.
9. **A report footer under the table is not a data row.** `*** END OF REPORT ***` in the
   debtor-name column (row 13 of the committed fixture) was counted in `total_rows` and reported
   as a bad row on every real export.
10. **One label, not two:** the backend drawer map now reads "Customer Import", matching both
    frontend job-type maps.

Not done: browser verification (no server available to the implementing agent) and the
Playwright spec has never been executed - `e2e/customer-import.spec.ts` is written against
the real fixture `e2e/fixtures/debtor-listing.xlsx` and needs a stack plus
`CUSTOMER_IMPORT_E2E_EMAIL` / `_PASSWORD`. The worker must be restarted before any manual
test: `app/tasks/import_tasks.py` changed and the worker has no reload.

## Second fix pass (coder, 2026-08-13, after the first real file)

The first fix pass was review feedback. This one is evidence: the real **4,196-row AutoCount
debtor export** was run through the live UI and the preview and the import **disagreed**.

**What happened.** Preview: 0 failures. Import: **58 rows `upsert_error`**. Three `customers`
columns were narrower in the database than `app/models/order.py` declared - `phone_number`
`varchar(20)` against `String(50)`, `industry` `varchar(100)` against `String(120)`, `website`
`varchar(255)` against `String(500)` - and 58 rows of the export carry a `Phone 1` longer than
20 characters (`016-000 0503 (MR.CHAEH)`, `95-0000558/013-0000504`). The over-length pre-check
read the MODEL's lengths, so all 58 passed the preview and Postgres then refused every one of
them with `StringDataRightTruncation`.

**After the fix pass the same file imports 4,195 / 4,195 successful, 0 skipped, 0 failed.**
Preview and apply agree on every bucket. (4,195 data rows out of 4,196 sheet rows: the
trailing `*** END OF REPORT ***` line is report furniture, not a row - UAC AC-6.3.)

What changed, and why each one:

1. **F1 - migration `355_customers_length_drift`.** Widens the three columns to what the model
   has always declared (UAC AC-7.1). The model is the intent; the database moves to it.
   Widening is non-destructive and does not rewrite the table. The downgrade narrows and is
   documented as refusable by Postgres if a value no longer fits.
2. **F2 - `tests/test_customers_schema_drift.py`** (UAC AC-7.3). Three tests: the sweep over
   every varchar the model declares, the named regression on the three columns a real export
   hit, and an anti-vacuity test that narrows a column for real inside a rolled-back
   transaction and asserts the guard reports the new width. On CI's `create_all` schema the
   sweep is a tautology and the docstring says so - it bites on the migration-built dev and
   production databases, which is the only place the drift can exist.
3. **F3 - `customer_import_service.column_limits(db)`** (UAC AC-7.2). The pre-check now reads
   the LIVE schema, resolving the session's own schema so the scratch-schema tests reflect the
   table they are actually writing to. Once per import, never per row, never cached across
   imports (a cached limit would outlive the widening migration inside a long-lived worker).
   Falls back to the model's lengths with a warning only if the catalog is unreadable - losing
   the pre-check entirely would send an over-length value to Postgres, whose `DataError`
   poisons the whole transaction.
4. **F4 - a refused row carries the database's own complaint** (UAC AC-7.4). `_db_failure_reason`
   reads the psycopg2 `diag` (primary message, column, constraint); because Postgres names no
   column for a too-long value, the row's own over-width fields are named instead
   (`column phone_number holds 24 characters`). The previous "could not be saved" cost a reader
   a worker traceback to diagnose a one-line problem.
5. **Two alias rows, from the file rather than from a guess** (UAC AC-4.4): `("customer_code",
   "Code")` and `("phone_number", "Phone 1")`. AutoCount heads the key column plain `Code`, so
   without the first the real export could not be read at all. Migration 353 now seeds **65
   rows**. `Code` is the one bare one-word alias; a bare `Name` is still refused as ambiguous
   between the debtor name and the registered name.
6. **A2 - the Radix a11y warning** (UAC AC-5.7). Diagnosed, not silenced. The culprit is
   **neither** the import `Dialog` (it has a `DialogDescription`) **nor** the confirm
   `AlertDialog` - a missing description under an `AlertDialog` names `{AlertDialogContent}`,
   and the warning named `{DialogContent}`. It is the **upload activity drawer**
   (`components/upload-activity/UploadActivityDrawer.tsx`), whose `SheetContent` had no
   `SheetDescription`; `Sheet` is Radix Dialog underneath, so it warns under the Dialog name.
   The flow opens it on queue (AC-5.3), and `reactStrictMode: true` double-invokes the warning
   effect in dev, which is the "twice per open". Fixed by giving the drawer a
   screen-reader-only description; the panel now reaches a screen reader as a title with a
   body. This clears the warning for every import flow, not only customers.
7. **A3 - the dialog description is one clause.** It read "A debtor listing export. Account
   owners, market segments, notes and active flags are never overwritten." The second sentence
   is a feature explanation inside the UI, which the cursor rule and `ADR-PRODUCT-STANDARDS`
   both refuse. It is not deleted, because A2 needs a description to exist: it is now "Update
   the customer book from a debtor listing export.", and the never-overwritten guarantee is
   recorded in UAC AC-3 / AC-3.6. No test asserted the old string.

### The lesson worth keeping

**A length check that reads model metadata cannot detect a drifted column.** That is the whole
of it: `Customer.__table__.columns[...].type.length` is the model's opinion, and the row is
written against the database's. When the two disagree the pre-check certifies rows the database
is about to refuse - which is exactly why the preview promised zero failures and the apply
produced 58. A validation that must predict what the database will do has to ask the database.

This is the **third** model-versus-database drift on this one table: the stale unique index
(UAC AC-2.5) was the first, these three widths the second and third. Two of the three were
found by a user-visible failure rather than by review, which is why F2 exists - the guard turns
the next one into a failing test.
