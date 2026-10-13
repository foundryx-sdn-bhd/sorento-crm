# PLAN: Quotation lines grid (13 Oct)

Status: BUILDING, fix round 1 after review (size L: one additive migration; security-reviewer joins
because the line write accepts attachment ids). Owner answers 14 Oct recorded in the UAC. PR #1643.

Lane: QUOTATION-UX, branch `crew/quotation-ux`. Source: owner notes 13 Oct on Project Sales >
Project > New quotation > Lines (screenshots 607 to 612 in the crew brief). Mock v2:
`documentation/mockups/QUOTATION-UX/index.html`. Acceptance criteria:
`quotation-lines-grid-13oct-acceptance-criteria.md`. Lane test DB: `sorento_qux_ci`
(`sorento_crm_backend/.env.ci-tests`, gitignored).

## Facts the plan rests on

- Editor: `app/(protected)/project-sales/[projectId]/components/QuotationVersionEditor.tsx` over
  `_shared/components/InlineLineTable.tsx` (`:808-870`). Save sends the staged array; order is array
  position, `sort_order` is not sent (`:136-143`), so a drag only permutes the staged array.
  InlineLineTable has no reorder. PO, SO and series editors also use it and are NOT touched.
- `components/ui/data-grid-table-dnd-rows.tsx` (`DataGridTableDndRows` `:98`, handle `:41`, aria
  "Drag to reorder") has row drag + column resize but no column drag, `<tfoot>`, full-width or
  expanded rows. `data-grid-table-dnd.tsx` has column drag + footer + expanded rows, no row drag.
  Editable DataGrid with row drag precedent: `master-data-management/product-specifications/
  components/SpecRulesGrid.tsx:549-640`. Read-only row-drag precedent:
  `[projectId]/components/SalesOrderLinesTable.tsx:415-464, 541-552`.
- Line photo: `product_image_service.py` (one decision, `is_primary`, `_photograph` filter
  `:105-114`); per line: `project_quotation_image_service.line_images` (`:54-119`); frozen at issue
  by `freeze_version_images` (`:135-164`, called at `project_quotation_document_service.py:572`).
  `image_attachment_id` is ALREADY client-writable (`LINE_FIELDS` `project_quotation_service.py:63-85`,
  schema `schemas/projects.py:1224`) with no check that it belongs to the product: fixed here.
- Attachment kinds by `attachment_types.type_name`: "Product Photos" (2,308 products in dev),
  "Technical Specifications" (1,180 products; 56 with several). Precedent for matching on the name:
  `services/dealer_kit/product_images.py:167-168`.
- "Off-catalog" = `product_id` empty (FE `:1055-1058`, `:119-123`; BE `:522-529`, `:649-657`,
  `:900`). "Counts per" = `unit_type`, changes no total (`:659-661`) and prints nowhere.
- PDF `project_quotation_pdf_service.py:346-400` (image column from stamped id, TECHNICAL SPEC =
  text at `:396`); Excel `project_quotation_excel_service.py:223, 367, 421-434`. Issue-only routes
  (`api/v1/projects/quotation_documents.py:607, 664`).
- Stock: `GET /project-sales/fulfilment-planning/stock-detail?product_id&group=` (`api/v1/projects/
  fulfilment_planning.py:919-970`, service `project_fulfilment_board_service.py:909-1293`), FE
  `useStockDetail` (`_shared/hooks/useFulfilmentPlanning.ts:405-427`), table `CellStockTable`
  (`fulfilment-planning/components/CellStockTable.tsx:238`), OI wrapper
  `_shared/components/OrderInquiryStockGrid.tsx:31` (location -> group via `groupOfWarehouseCode`).

## API contract

Line write (bulk item and single line), new or changed fields:
- `image_attachment_id: str | null` (existing) and `drawing_attachment_id: str | null` (new).
  Validated only when the value differs from the stored one: must be an attachment linked to the
  line's product through `product_attachments`, image mime, not deleted, same company; drawing must
  also be of type "Technical Specifications". Set on a line with no product, or invalid: 422,
  codes `quotation_line_image_invalid` / `quotation_line_drawing_invalid`.
- Description-required 422 message: "A custom item needs a description." (code unchanged).

Line response, new fields (declared on the response model):
- `drawing_attachment_id`.
- `product_image.candidates[]` and `technical_drawing.candidates[]`: `{attachment_id, filename, url,
  preview_url}` (signed thumbnail + original). `product_image.candidate_count` counts "Product
  Photos" images only.
- `technical_drawing`: `{state, url, preview_url, attachment_id, filename, candidate_count,
  candidates}`, state `chosen` (picked or stamped) | `default` (first by `attachments.created_at`,
  then id) | `no_drawings` | `off_catalog`.

## Slices (one branch, one PR)

1. **Migration** `qlg_0001_drawing_attachment`: `projects.quotation_lines.drawing_attachment_id
   UUID NULL REFERENCES attachments(id) ON DELETE SET NULL`. Model, `LINE_FIELDS`, schemas, revise
   copy (`project_quotation_service.py:340-355`). Crew SQL: `crew/state/migrations/QUOTATION-UX.sql`.
2. **Photo rule** (`product_image_service`): no `is_primary` + exactly one "Product Photos" image =
   `chosen` with that image, in every reader. Candidates and `candidate_count` = "Product Photos"
   images only.
3. **Line images + drawings** (`project_quotation_image_service`): candidates for both, drawing
   resolution, freeze fills `drawing_attachment_id` too (fill only). Validation of picked ids in the
   line write.
4. **Print**: PDF and Excel TECHNICAL SPEC = the stamped drawing image; legacy `technical_spec` text
   under the description; description line breaks kept (PDF `white-space: pre-line` or `<br>`).
5. **Shared grid** (FE): `DataGridTableDndRows` gains `<tfoot>` from `columnDef.footer`, full-width
   rows (section bars), expanded rows (`meta.expandedContent`) and column drag. Existing callers
   render unchanged.
6. **Quotation grid** (FE): `QuotationLinesGrid` replaces the InlineLineTable use; same staging
   contract (`:675-696`), validation, search filter and footer rule. Columns per mock section 1;
   Description `Textarea`; Brand `SearchableSelect` over `useBrandSelectQuery`; UOM + stock chip
   under Qty; list price under rate; rate-only tick under total; row menu (Line details, Move up/down,
   Insert line below, Insert section above, Remove). Line details row: Complete set, Priced per,
   Notes. Picker popover for photo and drawing writes `image_attachment_id` /
   `drawing_attachment_id` into the draft; a product change clears both. Column prefs key
   `projects.projects.view::quotation-lines`.
7. **Stock chip** (FE): `QuotationStockChip` = `useStockDetail(productId, null, null, group)` and
   `(..., 'pools')`; popover = `CellStockTable` (as `OrderInquiryStockGrid` renders it) with order-only
   columns and document chevrons hidden by a new prop; incoming SPOs listed. "Stock at" dropdown on
   the Lines tab from the fulfilment-planning warehouses (`groupOfWarehouseCode`), value in
   localStorage under `quotation-lines.stock-group` (try/catch), default first group.
8. **Wording**: Custom item / Pick a product / "A custom item needs a description" / recompute note
   (`QuotationVersionEditor.tsx:969-976`), Priced per labels.

## Tests (tester writes them first, red, from the UAC)

Backend (pytest, `SORENTO_ENV_FILE=.env.ci-tests`, `-n 2` max): extend
`tests/test_product_image_service.py`, `test_project_quotation_images.py`,
`test_project_quotation_bulk_lines.py`, `test_project_quotation_pdf.py`,
`test_project_quotation_excel.py`, `test_project_quotation_document.py` (issue freeze), plus the
migration (column exists, nullable, FK SET NULL).
Frontend (vitest): `QuotationVersionEditor.test.tsx` and new `QuotationLinesGrid.test.tsx`,
`QuotationStockChip.test.tsx`, `data-grid-table-dnd-rows` footer/full-width/expanded test.
Browser: 1280 sidebar open (no horizontal scroll, no truncated header), 375 (no page scroll).
