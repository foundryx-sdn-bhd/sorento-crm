# PLAN - Reporting foundation (Sponsorship report as report #1)

Status: S1 (Phase 1, frontend against mocks) BUILT and browser-verified 2026-08-26 on
branch `feat/reporting-foundation`. S2 (Phase 2, kernel, test-first) BUILT 2026-08-26 on the
same branch: registry, engine, xlsx renderer skeleton, views service, routes, migration 422
and the RQ export task, with 96 pytest over a synthetic dataset. S3 (sponsorship dataset +
definition, and the frontend swapped off the mock) BUILT and browser-verified 2026-08-26:
34 pytest on a seeded chain, 25 vitest, the S1 mock deleted, and the report shown on
:3090/:8091 against the 18 real approved 2026 forms. S5 (local 2025 fixture, test-first)
BUILT 2026-08-26: `scripts/dev/load_sponsorship_2025_fixture.py`, the workbook committed as
`tests/fixtures/sponsorship_2025.xlsx`, 28 pytest, and the 214 forms loaded on the local
copy with all 12 monthly totals equal to the client's GRAND TOTAL cells. S4 (Excel export,
test-first) BUILT and browser-verified 2026-08-26: `engine.run_workbook` + the renderer's
SUMMARY plus one sheet per month, the export queue knob, and the AC-D5 diff against the
client's own workbook. **All five slices are built**; 176 pytest across the reporting
suite, 26 vitest. Phase 3 review fixes applied 2026-08-26 (see "Contract points settled in
review"): 196 pytest across the reporting suite, 58 vitest. S6 (workbook fidelity, see
"S6 - workbook fidelity" below) BUILT and browser-verified 2026-08-26 on :3090/:8091: month
chips, the whole period on one dense page, the fixed four-year delivery band with stable
ids, single-level headers merged over both header rows, "-" for zero money, and the
client's own title block, headers, widths, RM accounting format and SUMMARY tail rows in
the export. 210 pytest across the reporting suite, 92 vitest (reports + shared DataGrid +
listing column preferences); the JAN'25 export read back with openpyxl carries GRAND TOTAL
14,850,000.00 / 29,195.00 in F25 / G25 and the SUMMARY closes on the client's own
257,076,027.91 / 518,605.38.
S6 review fixes applied 2026-08-26 (the deltas below, each pinned by its own test): the
delivery date is offerable as a column of its own, the reload skeleton is clamped, the month
chips and the period selects agree, a custom period reads DD/MM/YYYY, the GRAND TOTAL label
stays inside the table, a saved view is validated before it is stored or published, a default
view no longer freezes its year, column memory belongs to the report default, and the shared
DataGrid keeps a column group whole through a drag. 221 pytest across the reporting suite,
109 vitest (reports + shared DataGrid + listing column preferences).
UAC: reporting-foundation-acceptance-criteria.md (governing)
Source: `Sorento/phase-2/User Requirements/Project/SPONSORSHIP REPORT JAN-Dec'25.xlsx`
Review artifact: `.lavish/reporting-foundation.html`

## Why a foundation and not a page

The workbook is 12 identical monthly detail tables plus one pivot (sales agent x month,
two measures) over the same rows. Every operational report the client will ask for next
decomposes the same way: a flat dataset, then a layout over it. Scale here means the number
of reports, not row volume (~214 sponsorship forms a year), so the design optimises the
marginal cost of report #2: one dataset function + one definition + one permission seed,
and a two-line route wrapper. No BI tool (second auth, no company scoping, cannot emit the
client's formatted workbook); no user-built designer (weeks, unasked for). Definitions are
JSON-serialisable, so moving them into a table later is a migration, not a rewrite.

## Decisions taken (user, 2026-08-26)

1. **Month bucket = `approved_at` by default**, and the date basis is a user filter
   (approved / form date `request_date` / submitted `submitted_at`).
2. **Default statuses = approved + processed_by_cs**; the status filter can widen.
3. **OTHERS column dropped** (empty in all 12 sheets).
4. **Sponsorship dataset is company-agnostic** (`scope="none"`, declared explicitly).
   `purchase_requests` has no `company_id`, and 0 of 48 forms link to a project, so scoping
   through the project (as `sponsorship_link_service` does) would blank the report.
5. **2025 workbook is loaded on the LOCAL copy only**, as a verification fixture. Never
   prod. Prod reporting starts from 2026.
6. **Navigation under the owning module**: sidebar Procurement > Sponsorship Forms >
   Report, plus a Report button on the Sponsorship Forms listing toolbar. No top-level hub
   in v1; the catalog endpoint stays so one can be added later.
7. **Saved views are personal + shared**: a holder of `reports.views.publish` can publish a
   view as shared and mark one shared view the default for everyone.
8. **Configurability is user-level, no developer**: filters, detail columns (show / hide /
   drag-reorder via the existing DataGrid column preferences), summary dims + measures from
   the dataset catalog, saved views. A column NOT in the catalog needs a developer to add it
   to the dataset select; a formula builder is out of scope.

## What the data says (local prod copy, 2026-08-26)

- 48 sponsorship forms (`request_type='sponsorship_form'`), Feb 2025 to Jun 2026; 26 have
  no `request_date` (drafts). Status: draft 16, submitted 12, approved 11,
  processed_by_cs 7, rejected 2.
- Workbook 2025 has ~214 forms (PSSF25-001..214), never in the CRM.
- `total_project_value` numeric on 1 of 22 dated rows; `total_project_value_text` carries
  the "BULK ORDER EST RM1.6MIL" style. Lines (`purchase_request_lines.total`) on ~half.
- `request_number` already follows PSSF{yy}-#### (numbering rule shared with PR/SI/CMP).
- Orphan slugs `report.view` / `report.export` exist in `user_permissions`, referenced by
  no code. Left alone; not reused (their meaning is unknown).

## Column mapping (sponsorship dataset catalog)

| Workbook column | Catalog column | Source | Tag |
|---|---|---|---|
| PS NO | `request_number` | header | dimension |
| SALES AGENT | `sales_agent` | `requested_by_contact` display name, fallback `requested_by` | dimension |
| CUSTOMER NAME | `customer_name` | header | dimension |
| PROJECT TITLE | `project_title` | `projects.title` when `project_id` set, else header text | dimension |
| SPONSER PROJECT | `sponsor_subject` | lookup label; `sponsor_subject_other` appended when `others` | dimension |
| PROJECT VALUE | `project_value` | `total_project_value` | measure (money) |
| (text value) | `project_value_text` | `total_project_value_text` | text, detail only |
| SAMPLE PRICE | `sample_price` | `SUM(lines.total)` subquery, `COALESCE(total, quantity*unit_price)` | measure (money) |
| EXPECTED YEAR OF DELIVERY | `expected_delivery_year` | `EXTRACT(year FROM expected_delivery_date)` | dimension, rendered as YearTicks group |
| (month bucket) | `month` | `date_trunc('month', <date_basis>)` | dimension (derived from the chosen date basis) |
| - | `status`, `approved_at`, `request_date`, `submitted_at`, `expected_delivery_date`, `approver`, `purpose`, `delivery_address`, `pic` | header | extra catalog columns, hidden by default |

## Architecture

```
app/services/reports/
  registry.py        ReportDefinition, Dataset, Column, params (DateBasis, Period, Select),
                     layouts (Detail, Pivot, YearTicks), Workbook/Sheet; register(); get(); catalog()
  engine.py          run(db, definition, params, view) -> ReportResult
                     - resolves date basis, binds period + selects, applies scope
                     - detail: select visible columns, ORDER BY, cap (422 "export instead")
                     - pivot: GROUP BY row_dim, col_dim in SQL; matrix + totals in Python
  xlsx_renderer.py   ReportResult + Workbook spec -> openpyxl bytes (title block, header
                     groups via merged cells, totals as VALUES, one sheet per split_by value)
  views_service.py   report_views CRUD + publish/default rules
  datasets/sponsorship_forms.py     select + catalog + scope="none" + date columns
  definitions/sponsorship.py        REPORT (default view + workbook spec); registered on import
app/api/v1/reports/
  GET  /reports                       catalog, permission-filtered
  GET  /reports/{key}                 meta: params, catalog, default view, my + shared views
  POST /reports/{key}/run             {params, view?} -> ReportResult (sync)
  POST /reports/{key}/export          -> download row + RQ job on "imports" queue (same as PR PDF)
  GET/POST/PUT/DELETE /reports/{key}/views[/{id}]   personal; publish + set-default gated
  mounted under require_module_enabled_with_api_key("procurement") for now, module key
  "reports" deferred until a second module owns a report
app/tasks/report_export_tasks.py    generate_report_xlsx(download_id, key, params, view, user_id)
alembic: report_views table; permissions procurement.sponsorship_forms.report (granted to
  holders of procurement.sponsorship_forms.view) and reports.views.publish (granted to
  holders of procurement.sponsorship_forms.edit); idempotent raw SQL like 362.
sorento_crm_frontend/
  app/(protected)/procurement-management/sponsorship-forms/report/page.tsx  wrapper
  components/reports/ReportPage.tsx        generic: filter bar from meta, Detail (DataGrid,
                                           listing_key = procurement.sponsorship_forms.report::detail),
                                           Summary (pivot table), Configure summary, Views, Export
  components/reports/{ReportFilterBar,ReportPivotTable,ConfigureSummaryDialog,ReportViewsMenu}.tsx
  components/reports/__mocks__/sponsorshipReport.fixtures.ts   S1 ONLY, deleted in S3
  services/reportService.ts; hooks/useReports.ts (useReportMeta / useReportRun /
                                           useReportViews / useReportViewMutations / useReportExport)
  config/menu.config.tsx: Report entry under Sponsorship Forms (both menu copies)
  purchase-requests/components/PurchaseRequestsList.tsx: `reportPermission` prop adds a
                                           Report action to the listing toolbar
```

### Contract points settled while building S1 (binding on S2)

The review artifact (`.lavish/reporting-foundation.html` section 4) is a sketch; these are the
three places the screen needed more than it showed, and they are what the frontend now sends and
reads.

- **`POST /run` body is `{params, view: ReportViewConfig | null}`** - the view AS IT STANDS on
  screen, saved or not, so an unsaved change runs without being saved first. `null` = the report
  default.
- **`view.detail.columns` empty means the whole catalog.** The screen always asks for the whole
  catalog and hides client-side, so ticking a column is instant and a hidden column is still
  offerable in the Columns panel (AC-B7). The EXPORT sends exactly the visible columns in the
  visible order (AC-C5), which is where AC-A5's "requested columns in the requested order" is
  exercised.
- **`col_dim.value_labels`** (optional `{value: label}` map) is added to the pivot layout. The
  header needs `Jan'26`, and the frontend must not invent a formatting rule per dimension. The
  frontend falls back to the raw value when the map is absent.
- **`column_groups[].source`** names the catalog dimension the group was derived from, alongside
  `label` and `keys`. A saved view stores the SOURCE, because the member keys are data-dependent
  (one tick column per year present) and a view written in 2026 must still mean "show the delivery
  years" in 2027.

### Contract points settled while building S2 (binding on S3)

- **The 422 envelope is FLAT.** `AppException` is serialised by the global handler in
  `app/main.py` as its own detail dict, so a capped run answers
  `{"message": "...", "code": "REPORT_CAPPED", "capped": true}` and an invalid param answers
  `{"message": "Unknown param 'foo'", "code": "REPORT_INVALID_PARAMS"}` - NOT nested under
  `detail`. `extractApiError` already reads `message`; `reportService.ts` documents this.
- **Mine vs Shared is by OWNERSHIP, not by `is_shared`** (captain, S2). Mine = the views the
  caller owns, published ones included (the menu badges them Shared); Shared = other users'
  published views. The S1 mock, `ReportViewsMenu` and `ReportPage`'s default-view lookup were
  corrected in the same commit - the page now looks for `is_default` across both lists,
  because the account that published the default sees it under Mine.
- **`GET /reports/{key}` returns the shared default view as `default_view`** when one is set,
  falling back to the definition's own. So the screen never has to merge two sources.
- **The export names the file, the task does not.** The route computes
  `<title>-<period>.xlsx` and writes it on the download row; the task reads it back. One
  place decides the name.
- **Dataset scope has teeth.** `scope="none"` adds no predicate; `scope="company"` requires a
  `company_column` and ANDs `admin_listing_company_filter`. The sponsorship dataset is
  `scope="none"` (decision 4), so nothing exercises the company arm in production yet, and
  `generate_report_xlsx` runs at the all-companies scope - a `scope="company"` dataset must
  add an enqueuer-company argument to that task before it ships.

### Contract points settled while building S3 (binding on S4 and S5)

- **An unattributed form is grouped under `Unassigned`.** The pivot drops a row whose row
  dimension is blank, so a form with no requestor at all was counted in the DETAIL total and
  missing from the summary GRAND total: on the live 2026 data that was 6,948.00 against
  6,548.00 on one screen. `sales_agent` therefore coalesces to a named bucket, which is also
  what the filter offers and what the workbook will print.
- **`sponsor_subject` reads `<lookup label>: <free text>`** when the subject is `others`
  ("Others: Sales Gallery"), the same shape the form's own listing and detail page use.
- **`status` is the LABEL, not the slug** ("Processed by CS"), from one table shared with
  the status filter's options, so the filter and the column can never disagree.
- **The year list spans all three date bases**, not just the default one: a user who switches
  to Form date must be able to pick the year their forms are dated in. An empty dataset falls
  back to the current year rather than an empty dropdown.
- **The default period is `date.today().year`**, resolved at import, not a year baked into a
  release.
- **The workbook title block is company "Sorento", department "PROJECT SALES"** (AC-D2 input
  for S4).
- **The dataset reaches `projects.projects`, `respond_contacts`, the lookup tables and the
  request lines as CORE tables, never mapped entities.** `Project` is company-scoped, and the
  ORM's `do_orm_execute` listener splices that scope into any statement naming the mapped
  class - which is exactly the blanking `scope="none"` exists to avoid.
- **The frontend is off the mock.** `components/reports/__mocks__/`, `readMockScenario` and
  the `?mock=` forced states are deleted; every service function goes through
  `lib/api-client` with `extractApiError`. The capped 422 is detected by reading `capped` /
  `code` off a CLONE of the response while the message still comes from the shared extractor.

### Contract points settled while building S5 (binding on S4)

- **The month is the TAB NAME, never cell A4.** The client's `Dec'25` sheet carries
  1 Nov 2025 in A4. Trusting it files 24 December forms under November: invisible in a
  year total, wrong in every summary cell. The loader reports the mismatch rather than
  correcting the workbook.
- **`PSSF25- 001` is normalised to `PSSF25-0001`**, the CRM's own `PSSF{yy}-####` rule, so
  the fixture rows sort and search like every real form.
- **AC-D5's numbers, verified on the local copy.** All 12 sheets: parsed month total ==
  the sheet's GRAND TOTAL, to the cent. Year: project value 257,076,027.91, sample price
  518,605.38, which are the SUMMARY sheet's own C28 / C29.
- **The 2025 fixture has NO `Unassigned` row and NO ticked delivery year.** Every workbook
  row names an agent, and H..K is empty on all 214 rows, so a 2025 export renders the
  Expected-year group with no member columns. That is a fact about the data, not a bug.
- **The summary pivot for 2025 has 15 agent rows, not the workbook's 17.** `CONTACT J`
  and `BASER` are zero rows on the client's SUMMARY sheet and appear on no monthly sheet;
  `CINDY` and `CONTACT W` both resolve to the one contact `CONTACT W` and merge into a
  single row of 33 forms. Per-agent rows therefore differ from the client's sheet by
  design; the totals do not.
- **`ACT`, `KH LIM` and `JAMYN` have no contact** (22 rows). They keep the typed name and
  group under it, which is what the report's `sales_agent` fallback is for.
- **Undo is one statement:** `DELETE FROM purchase_requests WHERE source = 'fixture_2025'`
  (lines cascade).

### Contract points settled while building S4

- **`engine.run_workbook()` is the export path, `engine.run()` the screen's.** The workbook
  needs what a `ReportResult` does not carry: the months of the period (so an empty month
  still gets a sheet) and which month each detail row falls in. Rather than widen the wire
  shape the screen reads, the export calls `run_workbook`, which returns a plain dataclass
  (`WorkbookData`: the summary plus a list of named `WorkbookSheet`s). It is never
  serialised. The renderer therefore knows nothing about months, dates or filters - it is
  handed a summary and a list of named sheets and draws them.
- **One query, split in Python.** `run_workbook` selects the detail once with the month
  bucket (`date_trunc` on the chosen date basis, the same expression the `month` dimension
  uses) and groups the rows by it, rather than running the engine twelve times. Totals are
  recomputed per sheet from the RAW values, not by adding up rounded strings.
- **Tick-group members are derived over the WHOLE period, once.** Derived per sheet,
  January would come out with one delivery-year column and March with two, and the twelve
  tables would stop being the same table.
- **An empty `detail.columns` means the DEFINITION'S default columns for a workbook**, not
  the whole catalog. The screen and the file differ here on purpose: the screen asks for
  everything and hides client-side, which is what makes ticking a column instant (AC-B7),
  but a file has no Columns panel and a twenty-column sheet is unreadable. `run` keeps the
  whole-catalog meaning; `engine.workbook_columns()` holds the difference.
- **The detail total row is labelled `GRAND TOTAL`**, matching the client's own monthly
  sheets (the S2 skeleton said `TOTAL`).
- **A tick group is exported by its SOURCE, never by the year columns it became.** The
  grid's leaf ids are `expected_delivery_year__2026`; the backend catalog has no such
  column, so `POST /export` answered 422 "Unknown detail column" for any result with a
  ticked year. Found in S4, fixed in `ReportPage.visibleDetail()` with
  `collapseDetailColumns` and covered by a vitest. Hiding one member of a group now hides
  none of them: the group is one choice, which is also what makes a view written in 2026
  still mean "show the delivery years" in 2027 (the S1 contract point).
- **A column id survives its column.** Switching the period from 2026 (which has a ticked
  delivery year) to 2025 (which has none) leaves the user's column ORDER naming
  `expected_delivery_year__2026`, and TanStack logs "Column with id ... does not exist" on
  every render. `ReportPage` now filters the order to the ids the CURRENT result holds. Two
  transient warnings still appear on the frame where the switch lands, from the shared
  DataGrid's own prefs hook; the grid renders correctly and the steady state is clean, so
  the shared component is left alone.
- **`REPORT_EXPORT_QUEUE` (`settings.report_export_queue`, default `imports`).** The
  default IS the production queue the deployed worker drains; the knob exists because RQ
  workers are SHARED across worktrees on this machine, so a lane verifying an export needs
  a queue a sibling checkout's worker will not steal from.
- **AC-D5 loads its own rows.** The diff test runs the S5 loader's parser and `load()`
  into the test's scratch schema; it never reads the developer copy that
  `--apply` writes to.

### Contract points settled in review (Phase 3)

- **A blank dimension value is a NAMED BUCKET, `(blank)`, on both axes, sorted last.** The
  pivot used to `continue` past a blank row or column value, so the Summary grand total
  silently disagreed with the Detail total for every dimension except `sales_agent` (which
  S3 had coalesced to `Unassigned` in the dataset, one column at a time). The fix is in the
  ENGINE, so it holds for every dimension of every report. The dataset's `Unassigned`
  coalesce stays: it is the better label for that column, and it is what the filter offers.
- **Dimension values are ranked NATURALLY, not lexically.** "Agent 2" before "Agent 10",
  with `(blank)` last. Lexical order on any dimension carrying a number reads as a sorting
  bug.
- **Set as default requires the view to be SHARED, or the caller to own it.** It used to
  flip `is_shared` on whatever id it was handed, so a holder of `reports.views.publish`
  could expose somebody else's PRIVATE view to everyone by id alone. The owner may still
  publish and default in one step, which is the screen's own flow; anyone else gets a 409.
- **Listing column preferences are the REPORT DEFAULT's memory; a saved view carries its
  own columns** (revised in the S6 review; it used to read "last write wins"). The
  preferences row is one per LAYOUT, not one per view, so writing a view's columns into
  `procurement.sponsorship_forms.report::detail` made the next visit open on THAT view's
  columns while the Views menu named a different one, and Save and Export then recorded
  them under that name. The grid now reads and writes the listing key only while no saved
  view is applied; under a view it follows the view alone and persists nothing. AC-C1 ("my
  choice sticks between visits") is about the report default, AC-C3 ("selecting a view
  applies it atomically") about a view. Consequence, accepted: once a shared DEFAULT view
  is published the report opens on it, so a user's own column memory applies from Reset to
  report default onwards.
- **Times are bucketed in MALAYSIA time.** `approved_at` / `submitted_at` are stored naive
  UTC, and the whole CRM reads MYT, so a form approved 31 Jul 17:30 UTC belongs to August.
  The shift (`AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kuala_Lumpur'`) is applied once, to the
  date basis, and to every `date`-typed catalog column and the dataset's year list, so the
  period predicate, the month bucket, the year filter and the printed date cannot disagree.
  A DATE basis (`request_date`) is left alone: it carries no time to shift.
- **The default period resolves per REQUEST, not at import.** `date.today().year` evaluated
  when the module loaded froze a long-lived process on its boot year. `PeriodParam.default`
  now takes a callable (`registry.current_year_period`), the definition's default view names
  no period at all, and `engine.view_config` fills any unnamed param from the param's own
  default. `registry.validate` resolves that default at import, so a period the engine
  cannot read is a definition error rather than a 422 on every page open.
- **Params have ONE source: the top-level `params`, else the view's own.** `view.params`
  used to be dead on the wire, so a body carrying only a view ran on the definition's
  defaults - applying a saved view silently changed the period back. The filter bar still
  wins when it sends params, because that is what is on screen.
- **`POST /export` validates the VIEW, not just the params.** Same resolver the workbook
  uses, so an unknown column is a 422 at the button rather than a failed row in My Downloads
  a minute later.
- **`ReportResult.capped` is gone.** It was always `false`: a capped run is the 422 that
  carries `capped: true`, so a result in hand was never capped.
- **The workbook writes real dates, and never overwrites a total with its own label.**
  Date columns are date cells with a `DD/MM/YYYY` format (a text date cannot be sorted or
  filtered, which is most of what a file is opened to do). `GRAND TOTAL` opens the row as
  the client's sheets do, unless that cell carries a total, in which case it moves to the
  first cell that does not.
- **`PUT /reports/{key}/views/{id}` was removed.** No frontend caller; the menu offers Save,
  Publish, Set default and Delete. A Rename can add it back with its own UI.
- **The Views menu names the owner of a Shared view** (small muted text). A column of bare
  names says nothing about whose view each one is; on Mine the answer is always me.
- **Deferred, deliberately:** `scope="company"` is not fail-closed yet (no dataset declares
  it; a TODO in `engine._predicates` names the trigger), and `POST /run` still computes both
  layouts on every call (the row set is ~214 a year).

### Column reorder, fixed and unfixed (2026-08-26)

- **A dragged column order now SURVIVES the reload (fixed).** The grid read the saved order
  back, applied it, and then PUT the report's DEFAULT order over it, so a reorder lasted
  exactly one page load while show / hide persisted normally. Two causes, one per side.
  `ReportPage` rebuilt its whole `gridOverride` from the render's `effectiveGrid` in BOTH
  `onColumnOrderChange` and `onColumnVisibilityChange`, and `useListingColumnPreferences`
  applies a saved config by calling `setColumnOrder` and `setColumnVisibility` back to back
  in one effect - so the visibility call, reading the stale render value, threw away the
  order the order call had just applied. Both handlers are functional updates now, so two
  sets in one tick compose. The order was then written back because the hook's
  `skipSaveOnceRef` was consumed by the save effect run that happens in the SAME commit as
  the apply (with the pre-apply fingerprints, since no render has happened yet), leaving the
  run that carried the applied state free to save it. The hook now also records what the
  server holds (`persistedRef`, a fingerprint of order + visibility + sizing as applied) and
  never writes an identical payload - which is a no-op for every listing except that it
  stops one redundant PUT per page open. Pinned by
  `components/reports/ReportPage.orderMemory.test.tsx`, which runs the real preferences hook
  against a mocked column-config service.
- **Browser evidence (:3090 / :8091, 2026-08-26):** sidebar to Project Sales Admin >
  Sponsorship Report, drag `Project title` to the front, two reloads. Network shows one GET
  per load and exactly ONE PUT (the drag), and the header row reads
  `Project title | PS No | Sales agent | ...` on both reloads. No console errors.
- **KEYBOARD reorder does not work on ANY listing, and that is PRE-EXISTING (not fixed
  here).** Focusing a column grip and pressing Space, ArrowRight, Space announces
  "Draggable item <id> was moved over droppable area <id>" - `over` resolves to the dragged
  column itself - and nothing moves; mouse drag is unaffected. Cause:
  `components/ui/data-grid-table-dnd.tsx` builds its keyboard sensor as
  `useSensor(KeyboardSensor, {})`, with no `coordinateGetter: sortableKeyboardCoordinates`,
  so dnd-kit's default getter shifts the pointer 25px per arrow press and never leaves the
  active column's own rect. That line is unchanged since the repo's first commit
  (`git log -S "useSensor(KeyboardSensor"` reports only `8b7057f85`), and the S1 group-header
  change touched only `disabled` on group / placeholder headers, whose ids are not in the
  sortable items list either way. Reproduced live on the Purchase Requests listing, a flat
  grid whose DataGrid code is identical to `origin/main`: same announcement, same no-op.
  Left alone deliberately - it is a shared-DataGrid accessibility gap affecting every
  listing, not a reporting one, and widening this branch to carry it would hide it.

### Shared components touched (no-ops for every flat listing)

Column groups needed three small corrections in the shared DataGrid, each of which is a no-op on a
grid without groups: `colSpan={header.colSpan}` on the head cell; a footer-row filter so the
mirror group row does not render as an empty strip; and the drag handler skipping group and
placeholder headers so one column id is never registered twice with dnd-kit. The Columns panel now
reads `getAllLeafColumns()` instead of `getAllColumns()`, which is the same list on a flat grid but
is the only way a grouped column's members can be shown or hidden.

`report_views`: id uuid, report_key text, owner_user_id text FK users, name text, view jsonb
({params, detail:{columns,order}, pivot:{rows,cols,measures}}), is_shared bool default false,
is_default bool default false, created_at, updated_at. Unique (report_key, owner_user_id, name).
At most one `is_default` per report_key (partial unique index). Personal views are visible to
their owner only; shared views to anyone with the report permission.

## Slices

| # | Slice | Ships | Proves |
|---|---|---|---|
| S1 | FE mock (Phase 1) DONE | `ReportPage` + wrapper route against a mocked ReportResult: filter bar (date basis, period, agent, status), Detail with Columns show/hide + drag, Summary pivot, Configure summary dialog, Views menu (Mine / Shared, Save, Publish, Set default), Export button; sidebar entry + listing Report button; 375 / 1280 | The §4 contract is what the screen needs; user sees the shape before backend |
| S2 | Kernel (Phase 2, test-first) DONE | registry, engine, xlsx renderer, views service, routes, migration (table + slugs), RQ task. pytest on a SYNTHETIC dataset registered by the test, not sponsorship | The kernel is generic; report #2 costs what §Why says |
| S3 | Sponsorship dataset + definition DONE | dataset select + catalog; definition; the frontend swapped off the S1 mock; seeded-chain pytest asserting Summary cell = sum of Detail rows for the same agent/month, blanks vs zero, date basis switch changes the month; vitest for the page states, Configure summary and the Views menu | Report #1 on the real page with real 2026 rows |
| S4 | Excel export DONE | workbook = SUMMARY + one sheet per month (title block, header groups, totals as values); diff test against the committed 2025 fixture layout | Cell-for-cell match on the local copy |
| S5 | Local 2025 fixture DONE | `scripts/dev/load_sponsorship_2025_fixture.py`: refuses non-local `DATABASE_URL`; source stamped `fixture_2025`; idempotent on `request_number`; agent names matched to `respond_contacts` by name, unresolved rows REPORTED not guessed; `tests/fixtures/sponsorship_2025.xlsx` committed (real sample) | JAN-DEC'25 regenerates locally and matches the client's sheet totals |

| S6 | Workbook fidelity DONE | Month chips, one page of dense rows, a FIXED four-year tick group with stable ids, single-level headers merged over both header rows, "-" for zero money, the free text alone for an "others" subject, the hidden Others column; the client's title block, uppercase headers, widths, RM accounting format, labelled GRAND TOTAL and the SUMMARY tail rows; the 2025 diff test extended to cell positions | The default report READS like the register the team keeps by hand, on screen and in Excel |

Order: S1 -> S2 -> S3 -> S5 -> S4 -> S6 (S6's diff test needs S4's export). Each slice is a PR;
S2 and S3 may share a branch if S2 is small enough to review together.

## Risks

- **Fixture leaking to prod.** Loader checks the DB host is local and stamps `source`; never
  referenced by deploy.sh or any migration.
- **Kernel over-generalisation.** Two layouts, three param types. Anything a third report
  needs is added when it arrives.
- **Pivot cardinality.** Cells capped (5,000) and detail rows capped (5,000) on the sync
  path; export uncapped.
- **Formula-vs-value totals.** The workbook uses SUM formulas; the export writes values the
  engine computed, so Excel cannot disagree with the screen. Stated in UAC D4.
- **Permission drift.** `projects.reports.view` is declared in forecast.py and never seeded;
  not touched. New slugs are seeded by migration with a pytest asserting the seed.
- **Data honesty.** Most 2026 forms carry no project value; blanks, not zeros. The
  "N of M rows have a project value" footnote was REMOVED in review (ADR 1d, no
  explanatory prose on screen): the blank cells already say it.
- **Contact name churn.** `sales_agent` resolves LIVE from the contact FK, so a renamed
  contact regroups history. Accepted (same rule as `requested_by_contact_name`).

## S6 - workbook fidelity

**Why.** The five slices built a report that is CORRECT: the numbers match the client's own
workbook to the cent. They did not build one that LOOKS like it. Asked to compare the
export against `SPONSORSHIP REPORT JAN-Dec'25.xlsx`, the user's decision (2026-08-26) was
that the report on its DEFAULT configuration must read as close to the workbook as the CRM
allows, on screen and in Excel. That is a fidelity slice, not a feature: the register the
team keeps by hand is the thing they recognise, and a file that carries the same numbers in
a different shape still has to be re-read before it is trusted.

**What it is not.** Nothing sponsorship-specific enters the kernel. Every item below is
either a generic capability (a fixed tick group, a header/width map on the workbook spec, a
vertically merged single-level header in the shared DataGrid) or a VALUE set in
`definitions/sponsorship.py`. Report #2 inherits the mechanism and supplies its own words.
The export still follows whatever columns are on screen (AC-C5 unchanged).

**The reference.** `sorento_crm_backend/tests/fixtures/sponsorship_2025.xlsx`, read with
openpyxl: monthly sheets carry the title block in rows 2-5 (company 26pt bold, SPONSORSHIP
22pt, a `mmm-yy` DATE cell in A4, `DEPARTMENT:` in row 5), a two-row header at 6-7 with
every single-level label merged vertically (A6:A7 ... G6:G7, L6:L7) and
`EXPECTED YEAR OF DELIVERY` merged across H6:K6 over the four years 2025-2028, data from
row 8, and a bold GRAND TOTAL line whose money sits in F and G. SUMMARY carries the agent
in A, twelve month groups of two measures across B..Y, `TOTAL VALUE (BY SALESMAN)` over
Z..AA, `TOTAL SALES` on row 26 and two labelled year-total rows on 28 and 29.

### Decisions (S6)

1. **The four delivery-year columns are FIXED, not derived.** Period year .. year+3, with
   STABLE ids `expected_delivery_year_1..4` and the actual years as labels. This is the
   workbook's own band (2025-2028), and it is also the repair for the S4 defect that
   survived review: a derived id (`expected_delivery_year__2026`) outlives the result it
   came from, so switching the period left the user's saved column order naming a column
   that no longer exists and TanStack logged it on every render. A stable id cannot go
   stale. `TickGroup.members` is the generic knob (a callable of the query context);
   unset, it keeps the derived-from-present behaviour the synthetic kernel dataset uses.
2. **A single-level header spans both header rows.** TanStack puts a PLACEHOLDER above an
   ungrouped column when any column is grouped, which draws an empty band over most of the
   header. The shared DataGrid now renders that placeholder as the column's real header
   with `rowSpan`, and skips the leaf underneath it - the merged cell the workbook has. A
   grid with one header row has no placeholders, so every flat listing is untouched.
3. **The whole period is on screen at once.** The register is ~214 rows a year and the
   workbook has no pages; a page control here only hides rows from a total the user is
   reading. The page size IS the row count, rows are dense, and the seven default columns
   plus the four year ticks are sized to fit 1280 without a horizontal scroll.
4. **Month chips, not a month picker.** The workbook's unit of work is a monthly sheet, so
   the year period offers All / Jan .. Dec as one click. A chip is a `month_range` period
   with `from_month == to_month`, which the engine, the export and the sheet naming already
   understand; a single-month period labels itself `Jan'25` rather than `Jan'25 to Jan'25`.
5. **Zero is "-" and so is missing.** On screen and in the file. The client's sheet prints
   `RM -` for a zero and a bare `-` for a value they never had, and both readings are "no
   money here". Money cells carry the accounting format `_-"RM"* #,##0.00_-;...`, which is
   what puts the RM and the dash where Excel expects them.
6. **`Others: Sales Gallery` becomes `Sales Gallery`.** The workbook's SPONSHER PROJECT
   column holds the free text alone. This SUPERSEDES the S3 contract point that copied the
   form listing's `<label>: <free text>` shape into the report.
7. **The sheet's OTHERS column is the form's `purpose`,** hidden by default, and it keeps
   the catalog key `purpose` so a view saved before S6 still resolves. The LABEL is what
   the user reads, and it reads Others.
8. **The company name comes from the DEFINITION** (`WorkbookSpec.company_name`, here
   `SORENTO SDN BHD`), and `system_settings.name` is the fallback ONLY for a definition
   that names no company (report #2 may name none). Settings wins nothing: the live
   install's `system_settings.name` still reads the template's `Metronic`, and a letterhead
   is the legal name the client puts on the paper, not a setting nobody has corrected.
9. **The display fonts are not reproduced.** The client's file uses Bell MT and Algerian
   for the two title lines. Neither is a font this system ships, and a missing font
   substitutes silently per machine, so the export mirrors the SIZES, the weight, the
   merges, the borders and the widths and leaves the family alone.
10. **The GRAND TOTAL label sits in the column immediately before the first measure.** The
    client types it in D (two columns before the money); "immediately before the first
    measure" is the rule that holds for any column set, including one the user reordered.
    Two fallbacks keep it inside the table when the columns leave no room (AC-G9): the
    first column carrying no total when the preferred one does, and column 1 of a bordered
    row directly above the totals when every visible column is a totalled measure. It used
    to be written one column PAST the table there, outside the border and the print area.

**Pre-S6 saved column orders.** An order saved before the band was fixed names the old
derived ids (`expected_delivery_year__2026`), which no current result holds, so the screen
drops them and the four stable year columns fall in at the far RIGHT of that user's grid
until they drag them back once (or reset the columns). One drag, once, per user who had
saved an order.

### What changed (S6)

| Id | Change | Where |
|---|---|---|
| G1 | Month chips under Period; single-month period label | `ReportFilterBar.tsx`, `engine.resolve_period` |
| G2 | No pagination, page size = row count, dense rows, tuned sizes | `ReportPage.tsx`, dataset column sizes |
| G3 | Fixed four-year tick group with stable ids | `registry.TickGroup.members` + `period_year_span`, `engine._tick_values` |
| G4 | Single-level headers span both header rows | `data-grid-table.tsx`, `data-grid-table-dnd.tsx` |
| G5 | Zero money reads "-"; sponsor subject drops the prefix | `ReportPage.tsx`, `datasets/sponsorship_forms.py` |
| G6 | `purpose` is labelled Others | `datasets/sponsorship_forms.py` |
| G7 | Title block rows 2-5, real date cell, DEPARTMENT row; the letterhead is the definition's company name, settings only when it names none | `xlsx_renderer.py`, `WorkbookSpec`, `engine.company_name` |
| G8 | Uppercase configured headers, vertical + horizontal merges, widths | `xlsx_renderer.py`, `WorkbookSpec.headers` / `column_widths` |
| G9 | Accounting RM format, "-" for missing, labelled GRAND TOTAL | `xlsx_renderer.py` |
| G10 | SUMMARY: TOTAL SALES + one labelled year-total row per measure | `xlsx_renderer.py`, `WorkbookSpec` labels |
| G11 | The 2025 diff test asserts cell POSITIONS as well as numbers | `tests/test_report_workbook_2025.py` |
