# UAC - Series filters (SERIES-FILTERS)

**Plan:** `PLAN-series-filters-15oct.md`
**Owner request (15 Oct 2026):** "for the brand I need to be able to multi select also, or I can
leave empty to say all brand, same goes for categories, can leave empty for all [categories]" and
"this list in [series detail page] needs pagination otherwise very long list".
**Card answer (crew, 15 Oct):** Q1 = (a): both empty = every active product. R1-R6 approved.

Screen: Dashboards > Project Sales > Configuration > Series > a series.

## A. Brand is a multi-select

- **A1** The Brand field on the series page is a multi-select (the same `SearchableMultiSelect` as
  Categories). It can hold zero, one or many brands. Placeholder when empty: "All brands".
- **A2** Saving sends the picked brands WHOLE (`brand_ids`, not a delta); reloading the page shows
  the same brands. Removing every brand and saving stores no brand.
- **A3** The series API (list, create, update) returns `brand_ids` and `brand_names` (same order,
  by brand name). No UUID is shown in the UI.
- **A4** Existing series keep their brand: the migration copies today's `brand_id` into the new
  link table, so a series that showed SORENTO before still shows SORENTO after.
- **A5** The series list page Brand column shows every picked brand name, comma-separated, and
  "All brands" when none is picked. Its search matches any of the names.
- **A6** "Unsaved changes" (Save enabled) reacts to a brand change exactly as it does to a
  category change.

## B. One scope rule, everywhere

A product is IN a series when it is named on the series (by hand or by sheet), OR when BOTH:
- brand matches: no brand picked, or the product's brand is one of the picked brands; AND
- category matches: no category picked, or the product's category is a picked category or a
  descendant of one.

- **B1** Brands [A], categories [X]: products of A under X (unchanged).
- **B2** Brands [A, B], categories [X]: products of A or B under X.
- **B3** No brand, categories [X]: products of every brand under X, including products with no
  brand at all.
- **B4** Brands [A], no category: every product of A, any category.
- **B5** No brand, no category: every active product of the company.
- **B6** A product named on the series is always in it, whatever its brand or category
  (unchanged).
- **B7** Only the series' own company's products count (company scope unchanged).
- **B8** The Products table preload (derived rows), the "in scope" flag on each row, the price
  edit on a derived row, and the non-standard alert on quotation lines (live line check, line
  save, version recompute) all follow this one rule. A product with brand C on a series of
  brands [A, B] with no categories is non-standard; the same product on a series with no brand
  and no category is standard.
- **B9** A quotation with no series still treats every catalogue line as standard; an
  off-catalogue line is still non-standard (unchanged).
- **B10** Sheet load and set lines are unchanged: the sheet resolves codes catalogue-wide, and a
  set is standard only by its own series row.

## C. Category picker follows the picked brands

- **C1** With brands picked, the Categories list offers the categories holding an active product
  of ANY picked brand, plus their parent categories. With no brand picked it offers every
  active category (unchanged).
- **C2** A picked category that drops out of the list after a brand change keeps its name as a
  chip (unchanged behaviour, now for many brands).

## D. Products table is paged on the server

- **D1** The Products table shows 50 rows per page, ordered by product code, with the app's page
  controls (previous / next and "from - to of total").
- **D2** The total ("5285 products") is the server's count of every row in the series (named
  plus in scope), not the size of the page.
- **D3** Search (code or description) runs on the server over the whole series, not the current
  page, and jumps back to page 1. With a search the line reads "N of TOTAL products".
- **D4** Editing a price on page 3 keeps the table on page 3 after the save.
- **D5** The server never loads the whole scope into memory to draw one page or to price one
  product: one count and one page query for a page, one membership check for a price edit.
- **D6** The page works at 375px and 1280px wide with no sideways page scroll.
- **D7** The Sets table is unchanged.

## E. Adding a product by hand works for any product

- **E1** Picking a product whose code sorts after the first 100 catalogue codes (for example a
  code starting with "Z" on dev) in "Add a product" adds it to the series. Today it fails: the
  page sends the product's id where the code belongs and the import reports it unmatched.
- **E2** A price and percentage typed on the new row are saved with it (unchanged two-step
  write: add by code, then price).

## F. Release safety

- **F1** The migration is additive: a new `projects.series_brands` table, backfilled from
  `projects.series.brand_id`; `brand_id` stays (no longer read or written) until a later cleanup.
- **F2** A read-only SQL report lists every series with brands, category count, named count,
  products today vs after, and open quotations, for the owner to run on prod before release
  (`crew/state/reports/SERIES-FILTERS-prod-impact.sql`). Dev result: "ZZT set pass" (SORENTO,
  0 categories, 14 named, 1 open quotation) goes from 14 to 5182 products.
