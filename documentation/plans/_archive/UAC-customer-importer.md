# UAC - Customer importer

**Status:** Approved decisions, awaiting plan sign-off. No code written.
**Spec origin:** `firstmate/data/customer-importer-spec.md` (captain, 2026-08-13)
**Binding context:** `documentation/adr/0007-a-dealer-is-a-customer-not-a-company.md`

## Journey

**Who.** Office master-data staff: the person who keeps the customer book in step with
AutoCount. They already hold a debtor listing export. They arrive from the sidebar at
**Order Management -> Customers**, the list they already use every day.

**What the system already knows.** The active company (from the session scope), the full
existing customer book for that company, and how a client's own column headings map onto our
fields (`import_field_alias`). None of it is asked for.

**Step 1 - choose the file.** An `Import` button sits in the customers list toolbar beside
`Add`, exactly where GRN and SPO put theirs. One decision: which file.

**Step 2 - "did I get this right?".** They press `Test`. Nothing is written. The system reads
the file at the same company scope the real import will run at, and answers in the file's own
terms: how many rows would be created, updated, left unchanged, and how many need a human
look. Any column heading it could not place is named. The single decision here is whether
that summary matches what they meant to upload.

**Step 3 - confirm.** They press `Confirm`. The job queues, the upload drawer opens, the feed
polls every 5s. They can leave the page; the work is not tied to the tab.

**Step 4 - read what happened.** The drawer shows the job reaching a terminal state and links
to the import-job detail, where every source row carries its own outcome and reason. A file
with three bad rows out of 900 imports 897 and says so.

**What they hold at the end.** A customer book in step with AutoCount, the original file
retained for tracing, and a per-row record of what changed. What they also hold is the
guarantee that nothing they curate by hand was touched: account owners, market segments,
notes and active flags are the system's, not the spreadsheet's.

## The three settled questions

### AC-1 Upsert key

The live constraint, read from the database, not from a migration file:

```
uq_customers_company_code_name_lower
  UNIQUE (company_id, lower(btrim(customer_code)), lower(btrim(customer_name)))
```

Migration 220 gave the two-column expression shape; migration **305** prepended
`company_id` and flipped `customers.company_id` NOT NULL. 220 alone is not the live key.

- **AC-1.1** A row matches an existing customer only on all three parts. Matching MUST use
  `lower(btrim(...))`, identical to the index, or "new" and "already exists" disagree and the
  insert takes a `23505`.
- **AC-1.2** `customer_code` alone is never identity. Verified against live data: Sorento holds
  2,391 customers across 1,453 codes; `301-S007` carries 225 distinct names, `301-C001` 99.
- **AC-1.3** `company_id` is taken from the job's company scope and is **never** a file column.
- **AC-1.4** The importer never renames. Name is half the key, so a changed name is a new row
  by definition. Renaming stays a UI edit.
- **AC-1.5** A code that already exists under a *different* name is a normal insert, not a
  conflict. `301-C001` is a cash-sale bucket holding person names and phone numbers
  (`CASH (SRT) - 016-000 0505`, `ABDUL RAUF`); flagging every such row would fire 99 times on
  one code and mean nothing.
- **AC-1.6** Only a **near** name match under the same code is flagged, using the `pg_trgm`
  similarity already on this table (migration 169). `CASH (SRT) - AISAH SHAMSUDlN` against
  `CASH (SRT) - AISAH SHAMSUDIN` flags; `ABDUL RAUF` against `AIMAN` does not. The row still
  inserts; the flag rides on a success outcome and appears in the job detail for a human.
- **AC-1.7** The importer never dedupes, merges or normalises the cash-bucket rows, and a
  code's high name count is not reported as a data-quality problem.

### AC-2 Company scoping

- **AC-2.1** Mechanism reused verbatim: `JobService.create_job` snapshots the caller's active
  company onto `import_jobs.company_id` via `active_company_id_from_scope` (migration 303);
  `_apply_import_job_scope` (`import_tasks.py:80`) reads it back and calls
  `set_company_scope(db, frozenset({company_id}))`; `Customer` carries `CompanyScopedMixin`, so
  `before_insert` auto-stamps and reads isolate.
- **AC-2.2** All writes go through the ORM. No raw SQL INSERT or UPDATE. Raw SQL bypasses the
  stamp; the row then violates the NOT NULL or lands invisible to every scoped read, which
  presents as "the import silently did nothing".
- **AC-2.3** A NULL company snapshot **refuses the job at the route**, HTTP 400, before
  anything queues. The generic path logs a warning and runs system-scoped for back-compat;
  for an Owned table that means either failing closed on row 4,000 or writing across the
  partition. Customers are Owned per ADR 0007, so no single company means no import.
- **AC-2.4** Cross-company duplication is legal and must not be treated as a collision.
  Verified live: **884 code+name pairs already exist under both Sorento and Mocha** (884 of
  Mocha's 893 rows). Two byte-identical `301-C001 / CASH (SRT) - AISAH SHAMSUDIN` rows differ
  only by `company_id`. Multi-company customer data is live today, not prospective.
- **AC-2.5** `app/models/order.py:90` still declares the pre-305 global index
  `uq_customers_code_name_lower` with no `company_id`; `products` was updated to its composite
  (`product.py:196`) and customers was not. The model must be corrected to
  `uq_customers_company_code_name_lower` in this change. Until it is, any test building its
  schema from `Base.metadata.create_all` (`blank_session()`) gets the **global** index, so an
  AC-2.4 test fails on the test schema and passes in production - inverted, and it reads as a
  broken importer.

### AC-3 What a re-import may never overwrite

| Field | Rule |
|---|---|
| `id`, `created_at`, `created_by` | never - identity and provenance |
| `company_id` | never - set once at insert; a re-import must not move a customer across the partition |
| `customer_code`, `customer_name` | never - they are the key |
| `account_owner_user_id` | never - a human sales assignment; no debtor listing carries it, and a silent reassignment is not noticed until commission is wrong. 0 of 3,284 rows populated today |
| `market_segment_code` | fill-if-empty only - NULL on 3,276 of 3,284, and it decides SCM demand class and fulfilment priority (`outstanding_import_service._segment_of`). Filling a blank is a gift; overwriting a curated one silently re-prioritises live orders |
| `notes` | never - human free text |
| `is_active` | never in v1 - absence from a file is not a deactivation; a one-branch export would deactivate the rest of the book |
| `customer_type` | insert only - see AC-3.5 |

- **AC-3.1** Freely updatable, file is source of truth: `email`, `phone_number`,
  `mobile_number`, `registered_name`, `trading_name`, `registration_number`, `industry`,
  `website`, `country`, `tax_id`, `salutation`, `first_name`, `last_name`.
- **AC-3.2** A blank cell means "not supplied", never "clear the field". This is the most
  likely way a customer importer destroys real data: a sparse export wiping populated columns.
- **AC-3.3** No-op when unchanged: outcome `unchanged`, no write, not counted as `updated`.
  Only rows whose values actually changed count as updated. (Follows
  `PLAN-spo-import-upsert.md` decision 3.)
- **AC-3.4** `billing_address` is out of scope. JSONB with an unspecified shape and 0 of 3,284
  rows populated; a flat spreadsheet column cannot express it honestly. Deferred, not silently
  dropped.
- **AC-3.5** `customer_type` is **set on insert and never moved by a re-import** (ruling D1;
  the first draft of this file said "updatable" in AC-3.1 and "derived" in AC-4, which was a
  contradiction, not two rules). It is the discriminator the app branches on, all 3,284 live
  rows read `company`, and a real AutoCount listing's `Debtor Type` column carries
  Trade / Cash / Local - vocabulary nothing in the app recognises. So: no alias row ships for
  it (migration 353 seeds none, and a `Debtor Type` column therefore reports as unmapped), and
  the service holds it in `INSERT_ONLY_FIELDS`. An admin who really does export our own values
  can add the alias row deliberately; even then a later file cannot flip an existing
  customer's type.
- **AC-3.6** This guarantee is stated **here and in the user guide, never in the dialog.** The
  import dialog's description used to spell it out ("Account owners, market segments, notes and
  active flags are never overwritten"), which is a feature explanation inside the UI - the
  cursor rule and `ADR-PRODUCT-STANDARDS` both refuse it. The dialog keeps a one-clause
  description because Radix points the modal's `aria-describedby` at it (see AC-5.7); the rule
  it describes lives in this table.

## AC-4 Required, optional, derived

- **Required:** `customer_code`, `customer_name`. A row missing either is skipped with
  `MISSING_REQUIRED_FIELD` and named in the job detail.
- **Optional:** every field in AC-3.1, plus `market_segment_code` under the fill-if-empty rule.
- **Derived:** `company_id` (job scope), `id`, `created_at`, `is_active` (true on insert only),
  `customer_type` (defaults `company`; all 3,284 live rows are `company`, and no alias maps it,
  so in practice every row gets the default - see AC-3.5).
- **AC-4.1** Headers resolve through `AliasResolver.for_doc_type(db, "customer")`, so a
  client's own spelling is an alias row rather than a release.
- **AC-4.2** The `customer` alias set is **seeded by migration**, not left for an admin to
  populate. The five existing doc types each ship 10 to 31 rows; an empty alias table means the
  first upload reports every column unmapped. Migration 353 seeds **65 rows**.
- **AC-4.3** Unmapped headers are reported by name in the Test result, never silently ignored.
- **AC-4.4** Two of the 65 rows come from a real 4,196-row AutoCount debtor export read on
  2026-08-13, not from a guess: **`("customer_code", "Code")`** and
  **`("phone_number", "Phone 1")`**. AutoCount heads the key column plain `Code` and numbers
  its phone columns, so without the first row the real file could not be read at all. `Code`
  is the one bare one-word alias the seed carries; a bare `Name` is still refused, because it
  is ambiguous between the debtor name and the registered name while `Code` in a debtor
  listing is not. This is AC-4.1 working as designed: the fix was two data rows, not a
  release.

## AC-5 Entry point and behaviour parity

- **AC-5.1** `Import` button in the `order-management/customers` list toolbar, same placement
  as GRN and SPO.
- **AC-5.2** Dialog copies GRN/SPO **behaviour** (Test, then Confirm, queue, toast) but uses the
  shared `components/common/FileDropzone.tsx`. GRN and SPO predate it and hand-roll their
  drag-and-drop; six newer dialogs use the shared one. Copy the settled shape, not the stale
  primitive.
- **AC-5.3** On queue: `notifyImportQueued()` from `components/upload-activity/useImportJobDrawer.ts`.
  No per-page status bar - that is what the drawer replaced.
- **AC-5.4** Uses standard job records, so the job appears in `system-management/import-jobs`
  with `ImportJobRowsCard` and `OutcomeBreakdownCard` populated, with no new screen.
- **AC-5.5** Source file retained via `store_import_source_file(job, ...)` before commit.
- **AC-5.6** Partial success is honest: one bad row never fails the file. Terminal counts come
  from `outcome.completion_counts()` and `outcome.finalize(...)`.
- **AC-5.7** **Every surface the flow opens carries a description, and the console stays
  clean.** Radix points a modal's `aria-describedby` at its `Description` node and warns
  `Missing 'Description' or 'aria-describedby={undefined}' for {DialogContent}` when the node
  is absent, twice per open under React StrictMode. Two consequences: the import dialog's
  `DialogDescription` is required (one clause, AC-3.6), and the upload drawer - which this
  flow opens on queue (AC-5.3) - carries a screen-reader-only `SheetDescription`. The warning
  is fixed by supplying a description, never by passing `aria-describedby={undefined}`: the
  drawer reaches a screen reader as a title with no body either way, and the suppression form
  hides the next surface that genuinely has none.

## AC-6 Outcome vocabulary

Existing codes carry most of it. Reused: `CREATED`, `UPDATED`, `UNCHANGED`,
`MISSING_REQUIRED_FIELD`, `ROW_ERROR`, `DB_ERROR`, `UPSERT_ERROR`.

- **AC-6.1** `CODE_EXISTS_UNDER_OTHER_NAME`, label "Inserted; similar name
  already on this code". Genuinely new - `ALREADY_EXISTS` and `DUPLICATE_LINE` both assert the
  row was *not* written, which is the opposite. Rides on `OUTCOME_CREATED`, counts as success.
- **AC-6.2** No other new codes without saying why here. Two more were added, with reasons:
 - **`MARKET_SEGMENT_NOT_RECOGNISED`**, label "Imported; market segment not recognised, left
    unset". Reason: the segment decides SCM demand class and fulfilment priority, and a
    file-level `unknown_market_segments` list no screen renders is not a trace - 40 customers
    could land with a NULL segment under a job reporting "40 created, no warnings". Rides on
    whichever success outcome the row earned (created / updated / unchanged); never a skip.
 - **`DUPLICATE_IN_FILE`**, label "The same row appears earlier in this file". Reason:
    `DUPLICATE_LINE` was reused for this at first, but its shared label reads "Identical line
    already exists on this order" and a customer job has no order. The GRN and SPO importers
    depend on that existing meaning, so the label could not simply be reworded.

### AC-6.3 Behaviours the first draft left unspecified

Settled during implementation, recorded here because each is a decision a reader would
otherwise have to infer from code:

- **An unrecognised `market_segment_code` is dropped, and the row still imports.** The column
  is a foreign key, so the alternative is failing a whole customer over one optional column.
  The dropped spelling is reported at file level (a `Test` warning naming the spelling and how
  many rows lost a segment) **and** per row via `MARKET_SEGMENT_NOT_RECOGNISED`.
- **A cell longer than its column fails that one row, not the file.** Postgres rejects an
  over-length varchar with a `DataError` that poisons the enclosing transaction, so the row is
  failed BEFORE the write with a reason naming the column (`ROW_ERROR`). Truncating would store
  something the file did not say.
- **The same (code, name) twice in one file: the second occurrence is skipped**
  (`DUPLICATE_IN_FILE`). It states nothing the first did not, and counting it as a second
  create would make the preview disagree with the import, which writes it once.
- **Report furniture under the table is not a data row.** An AutoCount export's trailing
  `*** END OF REPORT ***` (which lands in the debtor-NAME column in our own committed fixture),
  a `Page 1 of 2` line, a totals line or a rule line is skipped silently and excluded from
  `total_rows`. Matched on the WHOLE cell after stripping decoration, and only ever on a row
  already missing half the key, so a debtor genuinely called "Total Home Solutions" is still
  reported as a bad row rather than disappearing.
- **The dry run needs a single active company too** (ruling D2, amending AC-2.3): the refusal
  is above the `validate_only` branch, not only on the queueing path. 884 code+name pairs are
  held by both Sorento and Mocha, so an all-companies preview answers "0 new, 884 unchanged"
  about a book the import would not write, and Confirm 400s a moment later. A preview that
  cannot be trusted is worse than no preview.
- **The confirm dialog's skip claim comes from `summary.would_skip`.** `warnings` mixes row
  problems, unrecognised columns, unrecognised segments and the near-name aggregate, and only
  row problems skip; counting warnings told the operator a clean file's rows were being
  dropped.
- **`total_rows` is published as soon as the sheet is read**, before the first write, so the
  upload drawer shows progress instead of 0/0 for the whole run.

## AC-7 The model and the database must agree about `customers`

Added after the first real file. A 4,196-row AutoCount debtor export produced **58
`upsert_error` rows against a preview that had promised zero failures**: three `customers`
columns were narrower in the database than the model declared, and the importer's
over-length pre-check was reading the model. `phone_number` was `varchar(20)` against a
declared `String(50)`, and 58 rows carry a longer one (`016-000 0503 (MR.CHAEH)`,
`95-0000558/013-0000504`). This is the **third** time this one table has drifted from its
model (the stale unique index of AC-2.5 was the first).

- **AC-7.1 The three drifted columns are widened to what the model has always declared.**
  Migration `355_customers_length_drift`: `phone_number` 20 -> 50, `industry` 100 -> 120,
  `website` 255 -> 500. The model is the intent, so the database moves to it; the model is
  never narrowed to match a drifted column. Widening only, so it is non-destructive and
  Postgres does not rewrite the table. The downgrade narrows the same three and Postgres
  refuses it outright if any stored value no longer fits - stated on the revision.
- **AC-7.2 The over-length pre-check reads its limits from the LIVE SCHEMA, never from
  model metadata.** `customer_import_service.column_limits(db)` reflects the `customers`
  columns for the session's own schema (honouring the test suite's `schema_translate_map`,
  which catalog reflection does not) and falls back to the model's lengths with a warning
  only if the catalog cannot be read at all. Read once per import, never per row, and never
  cached across imports - a cached limit would outlive the migration that widened the column
  inside a long-lived worker. This is the AC that makes the preview honest: on a drifted
  database the rows are named BEFORE the operator presses Confirm, instead of failing after.
- **AC-7.3 A drift is a failing test, not a debugging session.** A migration only resets the
  clock. `tests/test_customers_schema_drift.py` compares every varchar width the model
  declares against the width the database has, names the three columns a real export hit,
  and proves the comparison is not vacuous by narrowing a column for real inside a
  rolled-back transaction and watching the guard report the new width. On a `create_all`
  database (CI) the sweep is a tautology and says so; it bites on the migration-built dev and
  production databases, which is where the drift can exist.
- **AC-7.4 A row the database refuses carries the database's own complaint, naming the
  column.** `_db_failure_reason` reads the psycopg2 `diag` for the primary message, column
  and constraint. Postgres reports **no column name** for a too-long value, so the row's own
  fields longer than the width in the message are named instead
  (`column phone_number holds 24 characters`). "could not be saved" alone cost a reader a
  worker traceback to diagnose a one-line problem. Reaching this branch at all means the
  pre-check's limit was wrong, which is exactly the drift the message then names.

## Out of scope

- Renaming an existing customer (AC-1.4).
- `billing_address` (AC-3.4).
- Deactivating customers absent from the file (AC-3, `is_active`).
- Widening or otherwise changing `uq_customers_company_code_name_lower`. It is already correct.
- `customer_contacts` rows. This importer writes `customers` only.
