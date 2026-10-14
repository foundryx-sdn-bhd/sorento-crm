# PLAN - A product set is ONE quotation line (SET-QUOTE-LINE)

**Status:** draft, track L (additive migration, FE + BE). Pending owner answers on card Q1-Q5 + mockup v1 (crew ask 14 Oct 2026). Build starts only after the mock is approved.
**UAC:** `set-quote-line-14oct-acceptance-criteria.md`
**Mock:** `documentation/mockups/set-quote-line/index.html`
**Depends on:** lane SERIES-BRAND-SCOPE (PR #1680) scope (d) for the import slice (S4) only.

## Facts (origin/main 4577b9cb1)

- Line names one product: `sorento_crm_backend/app/models/projects.py:1416`; snapshot rule `:1394`.
- Line write: `_write_line` `project_quotation_service.py:695-783`, `_snapshot_product` `:493-511`,
  `LINE_FIELDS` `:65-86`, guardrails `_apply_guardrails` `:541-600`, revise copies columns by name
  `:369-399`, `recompute_version` `:902`, `assert_line_picks` `:637-685`.
- Pricing: `is_in_series` `project_pricing_service.py:753`, `resolve_floor` `:164-243`,
  `series_floor` `:866`, `series_pricing_map` `:633`, `_floor_from_rule` `:154`,
  `category_ancestry` `:101`, import `apply_series_product_codes` `:953`.
- Live check: `api/v1/projects/quotations.py:270-344`. Breach notify `:78-114`. Issue block:
  `project_quotation_approval_service.py:185-205`.
- Sets: `models/product_set.py:48,91`; `resolve_set_price` `product_set_service.py:68`;
  `driver_members` `:426`; complete sets `_decorate` `:386`.
- SO draft: `project_so_draft_service.py` `_explosion_for` `:1160`, `_package_components` `:950`,
  `_quotation_components` `:1018`, `_quoted_line` `:1248`; SO line `models/project_so.py:520`.
- PDF row `project_quotation_pdf_service.py:376-428`; Excel `project_quotation_excel_service.py:360-480`;
  sign page DOC `:1316-1335`. Images `project_quotation_image_service.py:95`.
- Precedent for product-or-set columns + CHECK: `models/scm.py:1368-1396`, `models/price_tag.py:210-260`.
- FE: grid `QuotationLinesGrid.tsx` (ProductCell :264, DescriptionCell :308, QtyCell :381, RateCell :462),
  editor `QuotationVersionEditor.tsx` (fetchProducts :181, fillFromProduct :227), draft mapping
  `quotationLineDraft.ts` (:37, :62, :97, :124), flags `QuotationLineFlags.tsx:29`,
  product-or-set picker `scm/components/productOrSetPicker.tsx:52,115`, set detail members
  `ProductSetDetail.tsx:435-560`, series page `SeriesDetailClient.tsx:283`, `InlineLineTable.tsx:324`.
- Dev (sorento_cagent_stack, read-only): 93 sets, 0 series, 0 floor rules.

## Design (simplest thing)

### Migration (one revision)
- `projects.quotation_lines`: `product_set_id UUID NULL REFERENCES public.product_sets(id) ON DELETE SET NULL`,
  `set_parts_snapshot JSONB NULL`, CHECK `NOT (product_id IS NOT NULL AND product_set_id IS NOT NULL)`,
  index on `product_set_id`.
- `projects.series_sets`: `series_id` FK `projects.series` CASCADE, `product_set_id` FK `product_sets`
  CASCADE, PK `(series_id, product_set_id)`, `selling_price NUMERIC(12,2) NULL`,
  `max_discount_pct NUMERIC(5,2) NULL`. Same shape as `series_products` (`projects.py:860-895`);
  reached only through the company-scoped series.
- Parts snapshot shape: `[{product_id, product_code, description, quantity, is_driver}]`. `is_driver`
  is stamped at save from `driver_member`, so the SO split is deterministic later.

### Backend
1. Model/schemas: line gains `product_set_id`, `set_parts_snapshot`; create/update/bulk schemas accept
   `product_set_id`; response returns `product_set_id` and `set_parts`. `LINE_FIELDS` + revise copy
   gain both columns.
2. `_write_line`: both ids -> 422 `quotation_line_one_target`. Set loaded through the scoped session
   (foreign company = 404). New pick of an inactive set = 422. `_snapshot_set` mirrors
   `_snapshot_product`: code, description (kept if set), list price via `resolve_set_price(...).resolved`,
   uom `SET` when null, parts snapshot refreshed only when `product_set_id` changes.
3. Pricing: `is_set_in_series(db, series_id, set_id)`; `resolve_set_floor(db, company_id, product_set,
   list_price, series_id)`: series_sets row via `series_floor`, else category ancestry of the driver
   part + system rule through `_floor_from_rule` (no product-level rule). Guardrails branch on
   `product_set_id`. `/line-verdict` accepts `product_set_id`. `recompute_version` covers set lines.
4. Series sets: `GET /series/{id}/sets`, `PUT /series/{id}/sets/{set_id}` (upsert numbers),
   `DELETE /series/{id}/sets/{set_id}`; same permission deps as the series products routes.
   `series_set_pricing_map` for the FE rate fill.
5. Import (S4, after #1680 merges): in `apply_series_product_codes`, sets resolved by the set tier get a
   `series_sets` row with the sheet numbers; family tier sets likewise; report entries say so.
6. SO draft: in `_explosion_for`, after the package tier and before `_quotation_components`: quoted
   line with `product_set_id` + parts snapshot -> components from the snapshot, driver at the quoted
   rate, others 0.00, numerator = part qty, denominator 1; `EXPLOSION_SET = "set"`.
7. Documents: PDF/Excel/sign rows print `complete_set` or, for a set line with it empty, the part codes
   joined by " + ". Images: a set line reads the driver part's product files (`line_images`,
   `assert_line_picks`).

### Frontend
1. Types/services: `QuotationLine.product_set_id`, `set_parts`; `StagedQuotationLine`/`toBody` carry
   `product_set_id`; series sets service + hooks.
2. Picker: `fetchProducts` -> product-or-set options (reuse `fetchProductOrSetOptions`, value `set:<id>`);
   `selectProduct` branches to `fillFromSet` (set detail via `getProductSet`).
3. ProductCell Set badge; DescriptionCell parts list; QtyCell complete sets from the set detail;
   LineFlags "Custom item" only when neither id; verdict passes `product_set_id`.
4. Series page: Sets card (`SeriesSetsTable` over `InlineLineTable`) with an add-set picker.

## Slices (one coder, continued)
- S1 migration + line save + guardrails + live check + revise (UAC A, B).
- S2 SO draft + PDF/Excel/sign + images (UAC D, E).
- S3 series sets routes + Sets card (UAC C1, C5).
- S4 import write on top of #1680 (UAC C2-C4).
- S5 grid picker + set line UI (UAC F).

## Tests (red first, tester; Postgres `_pg_fixture`, own seeded chain)
- pytest `tests/test_set_quote_line.py`: A1-A7, B1-B7 (incl. issue blocked + approval), C1-C5, D1-D2, E1-E3.
- vitest: `quotationLineDraft` set round trip, picker options/`set:` value, LineFlags custom-item rule,
  `SeriesSetsTable`.

## Security
security-reviewer: new write surface (series sets routes), cross-company set id on a line (IDOR),
permission parity with series products routes.

## Out of scope / triggers
- Quoting a FAMILY itself as a line: not a sellable thing; trigger = owner asks.
- Re-snapshotting parts on old lines when the set master changes: Q5 (a) says never.
