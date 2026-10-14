# PLAN - Series brand scope (SERIES-BRAND-SCOPE)

**Status:** building (a)(b)(c), track M (no migration, no auth change). Scope (d) sets/families awaiting card addendum.
**UAC:** `series-brand-scope-14oct-acceptance-criteria.md`
**Card:** crew ask 14 Oct 2026, owner answers Q1-Q5 = (a).

## Facts (origin/main 760ed152b)

- Brand is stored, never read by pricing: `sorento_crm_backend/app/models/projects.py:821-825`.
- Category picker: `GET /api/v1/master-data/product-categories/select`, `limit(100)` at
  `sorento_crm_backend/app/api/v1/master_data/categories.py:122-145`; FE hook
  `sorento_crm_frontend/app/(protected)/master-data-management/shared/hooks/use-product-category-select-query.ts:8`
  (no params, `staleTime: Infinity`); options built at `SeriesDetailClient.tsx:127-136`.
- Membership: `is_in_series` `project_pricing_service.py:753-787`, `SeriesMembership` `:85-95`,
  `series_membership` `:740-750`; callers `project_quotation_service.py:566,947` and the live
  check `api/v1/projects/quotations.py:319`.
- Rows: `series_product_rows` `project_pricing_service.py:651-680`, route
  `quotations.py:829-848`; set pricing `set_series_product_pricing` `:683-725` (404 when no link).
- Table: `SeriesProductsTable.tsx` over `InlineLineTable` (no per-row delete gate, no paging).

## Design (simplest thing)

Preloaded rows are **derived, not stored**. The rows endpoint returns stored `series_products`
rows UNION active products of `series.brand_id` in the series categories (+descendants) that have
no stored row, with blank numbers. A row is written only when somebody types a number. This one
choice satisfies C4, C5, C8 and Q1/Q4 with no sync step, no diff rule and no new column:
untouched rows vanish with their category, new catalogue products appear at once, and nothing
typed is ever lost. Membership rule (b) makes the derived rows standard by the same scope.

### Backend
1. `categories.py` `/select`: optional `brand_id` query param. When given: categories with
   `EXISTS (active product of that brand in it)`, plus all their ancestors (walk
   `parent_category_id` upward). Drop `limit(100)` for every caller (126 categories on dev
   across both companies; the list is small by nature). Company scope stays the ORM's.
2. `project_pricing_service.py`:
   - `SeriesMembership` gains `brand_id: Optional[str]`; `series_membership` reads it from the series.
   - `is_in_series`: named product -> True; else category match AND (`brand_id is None` OR
     `product.brand_id == brand_id`).
   - new `series_scope_product_ids(db, series)` -> active products with the series brand in
     `series_category_ids`; empty when no brand or no categories. One query.
   - `series_product_rows`: stored rows + derived rows for scope ids without a stored row, sorted
     by product code; each row carries `in_scope: bool` (product in brand+category scope,
     stored or not).
   - `set_series_product_pricing`: when no link exists and the product is in scope, create the
     link with the numbers (upsert); otherwise 404 as today.
3. `SeriesProductRowResponse` gains `in_scope: bool = False`.

### Frontend
1. `useProductCategorySelectQuery(brandId?)`: query key includes brandId; passes `brand_id`.
   Other callers unchanged (no arg).
2. `SeriesDetailClient`: pass `brandId`; selected categories missing from options keep their
   label from `row.category_names` (A5). After create/update, invalidate the rows query
   (`[...SERIES_KEY, 'rows', id]` is under `SERIES_KEY`, so the existing invalidate covers it:
   verify).
3. `SeriesProductRow` type gains `in_scope`; `InlineLineTable` gains optional
   `canDeleteRow?: (row) => boolean`; the series table passes `(row) => !row.in_scope`.
4. C10: measure in the browser pass; page/virtualise only if slow.

## Tests (red first, tester)
- pytest `tests/test_series_brand_scope.py` (Postgres `_pg_fixture`, own seeded chain): A1-A4,
  B1-B4, B5, C1-C3, C4, C5, C7, C8, plus the live check endpoint for B2.
- vitest: `SeriesProductsTable.test.tsx` (C6), hook test for `brand_id` param (A6),
  `SeriesDetailClient` selected-chip label (A5) if cheap.

## Out of scope / triggers
- Scope (d) sets/families: separate addendum card, then its own slice in this lane.
- Backfill of stored badges on existing quotations (Q5 a): trigger = owner reports a stale badge.
