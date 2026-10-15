# PLAN - Series filters (SERIES-FILTERS)

**Status:** building, track L (additive migration). Mock skipped (owner exception: existing
components only). Card answered 15 Oct: Q1 = (a), R1-R6 approved.
**UAC:** `series-filters-15oct-acceptance-criteria.md`

## Facts (origin/main 9fc082f60)

- One brand per series is a column: `ProjectSeries.brand_id`
  `sorento_crm_backend/app/models/projects.py:823-826`. Categories are a link table
  `ProjectSeriesCategory` `:840-858`.
- Scope today needs brand AND categories: `series_scope_product_ids`
  `app/services/project_pricing_service.py:656-675` (empty without either);
  `is_in_series` `:949-989` fails a product whose category is not covered (`:986`), brand narrows
  via `SeriesMembership.brand_id` `:89-101`, filled by `series_membership` `:930-946`.
- Readers of the rule: rows `series_product_rows` `:690-725`; price edit on a derived row
  `set_series_product_pricing` `:728-766` (calls the full scope twice, `:752`, `:765`); live
  verdict `app/api/v1/projects/quotations.py:361`; line save
  `app/services/project_quotation_service.py:645`; version recompute `:1106`.
- Not readers: sheet load `apply_series_product_codes` (catalogue-wide codes), set lines
  `is_set_in_series` `project_pricing_service.py:798-815` (series_sets only), the import task
  `app/tasks/project_series_tasks.py:91-98`, MCP (no series tool).
- API: create/update `quotations.py:750-807` (`ProjectSeries(**body)` at `:765` would choke on an
  unknown `brand_ids` key, so pop it like `category_ids`); serializer `_serialize_series`
  `:1165-1262` (brand name lookup `:1225-1233`, output `:1249-1250`); rows route `:863-885`
  returns `_envelope` (`:71-76`, everything on one page). Schemas
  `app/schemas/projects.py:846-897`.
- Category picker `GET /master-data/product-categories/select?brand_id=` takes ONE brand
  `app/api/v1/master_data/categories.py:123-170`; FE hook
  `master-data-management/shared/hooks/use-product-category-select-query.ts:6-30`.
- Purge lists series link models `app/modules/projects/purge.py:96-97,195-196`.
- FE: `SeriesDetailClient.tsx:52-58` (brandId state), `:228-240` (single `SearchableSelect`),
  `:243-254` (categories, placeholder "No categories"), dirty `:94-104`, save body `:111-117`;
  `SeriesListClient.tsx:61,79-85` (brand column + search); types
  `_shared/types/project.types.ts:813-814`; rows hook `_shared/hooks/useProjects.ts:1214-1218`;
  service `_shared/services/projectService.ts:1262-1270`.
- Add-by-hand bug: `SeriesProductsTable.tsx:246-251` re-fetches `getProductsForLineSelect(undefined)`
  (first 100 by code, `app/api/v1/master_data/products_select.py:16,23,68`), misses the picked
  id, sends the UUID as the code, import reports it unmatched. `draft.product_code` already
  holds the picked code (`onOptionSelected` `:76-79`).
- App pager: `components/ui/data-grid-pagination.tsx` (`DataGridPagination`, 169 users) reads
  `useDataGrid()`; `CustomerBranchesTab.tsx:19-113` drives it with `manualPagination`.
- Dev data (`sorento_cagent_stack`, read-only): one series "ZZT set pass" = SORENTO, 0
  categories, 14 named, 1 open quotation; company's active SORENTO products 5182.

## Design (simplest thing)

### Migration `sf_0001_series_brands` (down_revision = main head at re-parent time)
`projects.series_brands (series_id FK projects.series CASCADE, brand_id FK brands CASCADE,
PK both)`; `INSERT ... SELECT id, brand_id FROM projects.series WHERE brand_id IS NOT NULL ON
CONFLICT DO NOTHING`. Downgrade drops the table. `brand_id` stays; the model docstring says it is
superseded and no longer read or written. Model `ProjectSeriesBrand` beside
`ProjectSeriesCategory`, no company column (reached through the scoped series), audit entity type
`project_series_brands`; add it to `purge.py` next to the category link.
Same DDL, idempotent, in `crew/state/migrations/SERIES-FILTERS.sql` for `crew migrate`.

### Backend
1. `project_pricing_service.py`:
   - `series_brand_ids(db, series_id) -> Set[str]`; `set_series_brands(db, series, brand_ids)`
     reconciled like `set_series_categories` (`:992-1012`).
   - `SeriesMembership.brand_id` becomes `brand_ids: Set[str]` plus a flag for "categories
     nominated" (empty categories = any). `series_membership` fills both.
   - `is_in_series`: named -> True; else brand ok (`not brand_ids or product.brand_id in
     brand_ids`) AND category ok (`no categories nominated or product.category_id in covered`).
   - One SQL filter builder for the scope (active, company via ORM, brand ok, category ok) used
     by: a paged rows query, a count, and a single-product membership check. Drop the Python
     set of every scope id from the price-edit path.
   - `series_product_rows(db, series_id, *, page, limit, query)` returns `(rows, total)`: rows =
     named products UNION scope products, LEFT JOIN `series_products` for the numbers, filtered
     by `query` (code or name, ILIKE), ordered by product code, OFFSET/LIMIT in SQL.
2. Routes: create/update pop `brand_ids` and call `set_series_brands`; `_serialize_series`
   returns `brand_ids` + `brand_names` (by name) in one query for the page; rows route takes
   `page` (default 1), `limit` (default 50, max 100), `query`, and returns real pagination.
3. Schemas: `ProjectSeriesCreate/Update.brand_ids: List[str]` (Update optional, sent whole);
   `ProjectSeriesResponse.brand_ids/brand_names`; `brand_id`/`brand_name` leave the API.
4. Category select: `brand_id` becomes a repeatable list param (`?brand_id=a&brand_id=b`), each
   UUID validated (422 on a malformed one, `test_r4` stays), union of qualifying categories +
   ancestors; no param = all (unchanged).

### Frontend
1. `SeriesDetailClient`: `brandIds: string[]` with `SearchableMultiSelect` (placeholder "All
   brands"); categories placeholder "All categories"; dirty + save body use `brand_ids`; category
   hook takes the brand list.
2. `useProductCategorySelectQuery(brandIds?: string | string[])`: key + query string carry every
   id; other callers unchanged.
3. `SeriesListClient`: Brand column = `brand_names.join(', ')` or "All brands"; search over all.
4. Types: `ProjectSeries.brand_ids/brand_names`; rows service/hook take `{page, limit, query}`
   through `buildDataGridParams` and return `{data, total}`; `placeholderData: keepPreviousData`.
5. `SeriesProductsTable`: server search (debounced, resets to page 1), page state 50, wrap in
   `DataGrid` with a headless `manualPagination` table and render `DataGridPagination` under the
   rows; count line from server total. `onCreate` sends `draft.product_code` (R6).

## Tests (red first, tester)
- pytest `tests/test_series_filters.py` (`_pg_fixture.blank_session`, own seeded chain): A2-A4
  (round trip, backfill shape via the model), B1-B9 (rows + `is_in_series` + live verdict + line
  save), C1 (two brands), D1-D5 (page 1/2 of a 120-row series, total, server search across
  pages, limit cap 100, price edit on a derived row of page 2), E1 at API level is FE only.
- Existing tests that encode the old rule are UPDATED in the red commit, not deleted:
  `test_series_brand_scope.py:338` (C7 "no brand or no categories shows stored rows only" ->
  now all-of-brand / every product), any test seeding `ProjectSeries(brand_id=...)` moves to
  `brand_ids` (`test_series_brand_scope.py`, `test_set_quote_line_review.py`).
- vitest: `SeriesDetailClient.test.tsx` (multi brand, save body, dirty), `SeriesProductsTable.test.tsx`
  (pager, server search params, E1: picking a product NOT in the first-100 mock adds it by code),
  `SeriesListClient.test.tsx` (joined names, "All brands").

## Run
`SORENTO_ENV_FILE=.env.ci-tests` (lane DB `sorento_sf_ci`), pytest at most `-n 2`, vitest
`--maxWorkers=2` on touched files only.
