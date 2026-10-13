# PLAN: Quotation lines grid (13 Oct)

Status: DRAFT, waiting for the owner's answers to the mock v1 card (Q1 to Q5). Track: M, becomes
L-steps if Q2 (a) is chosen (one additive column + its migration).

Lane: QUOTATION-UX. Source: owner notes 13 Oct night on Project Sales > Project > New quotation >
Lines (screenshots 607 to 612 in the crew brief). Mock: `documentation/mockups/QUOTATION-UX/index.html`.
Acceptance criteria: `quotation-lines-grid-13oct-acceptance-criteria.md`.

## Facts the plan rests on

- The editor is `app/(protected)/project-sales/[projectId]/components/QuotationVersionEditor.tsx`
  over `_shared/components/InlineLineTable.tsx`. Save sends the staged array; order is array
  position, `sort_order` is not sent (`QuotationVersionEditor.tsx:136-143`), so a drag only has to
  permute the staged array.
- InlineLineTable has no reorder at all; its header comment (`:304-314`) lists why it was not
  DataGrid. PO, SO and series editors also use it and are NOT touched by this lane.
- `components/ui/data-grid-table-dnd-rows.tsx` (`DataGridTableDndRows`, `:98`) has row drag and
  column resize but no column drag, no `<tfoot>`, no group/full-width rows and no expanded rows;
  `data-grid-table-dnd.tsx` has column drag + footer + expanded rows but no row drag. `rowsDraggable`
  in `data-grid.tsx:183,311` is read by nothing. Editable DataGrid precedent with row drag:
  `master-data-management/product-specifications/components/SpecRulesGrid.tsx:549-640`.
- "Off-catalog" = `product_id` empty (FE `:1055-1058`, `:119-123`; BE
  `project_quotation_service.py:522-529`, `:649-657`).
- "Counts per" = `unit_type`; changes no total (`:659-661`) and no print.
- Drawings = attachments of type "Technical Specifications" with an image mime (dev: 1,180 products).
- Stock = `GET /project-sales/fulfilment-planning/stock-detail?product_id&group=` (route
  `api/v1/projects/fulfilment_planning.py:919-970`), FE `useStockDetail`
  (`_shared/hooks/useFulfilmentPlanning.ts:405-427`), table `CellStockTable` via
  `_shared/components/OrderInquiryStockGrid.tsx:31`.

## Slices (one branch, one PR)

1. **Shared grid capability** (FE): extend `DataGridTableDndRows` with `<tfoot>` from
   `columnDef.footer`, full-width rows (a row-level `renderFullWidth`) used by section bars, and
   expanded rows (`meta.expandedContent`), plus column drag (reuse the horizontal SortableContext of
   `data-grid-table-dnd.tsx`). Existing callers (`SalesOrderLinesTable`) must render unchanged.
2. **Quotation grid** (FE): new `QuotationLinesGrid` replacing the `InlineLineTable` use at
   `QuotationVersionEditor.tsx:808-870`; same staging contract (`:675-696`), same validation, same
   search filter, same footer rule. Columns per mock section 1; Description `Textarea`; Brand
   `SearchableSelect` over `useBrandSelectQuery`; UOM + stock chip under Qty; list price under rate;
   rate-only tick under total; row menu (Line details, Move up/down, Insert, Remove).
3. **Wording** (FE + BE message): Custom item, Pick a product, "A custom item needs a description"
   (BE 422 message at `project_quotation_service.py:653`), recompute summary text (`:969-976`),
   Priced per in Line details.
4. **Drawing** (BE + FE): line payload gains `technical_drawings` (attachment id, filename, signed
   thumb + preview url) per line, built like `project_quotation_image_service.line_images`. If Q2 (a):
   additive column `drawing_attachment_id` on `projects.quotation_lines`, stamped at issue like
   `image_attachment_id` (fill only, never overwrite); PDF and Excel render the drawing in TECHNICAL
   SPEC via `product_image_service.render`; legacy `technical_spec` text printed under description.
5. **Photo rule** (BE, if Q1 (a)): `product_image_service` treats a product with exactly one
   "Product Photos" image and no chosen one as chosen-by-default, in every query of that module
   (`_photograph` filter stays the one place).
6. **Stock chip** (FE): `QuotationStockChip` reads `useStockDetail(productId, null, null, group)`
   for the group and `pools`, popover = `OrderInquiryStockGrid`/`CellStockTable` with a new prop to
   hide order-only columns and document chevrons. "Stock at" dropdown on the Lines tab, value in
   localStorage (try/catch), default the first fulfilment-planning group.

## Tests (tester writes first, from the UAC)

- vitest: grid renders DataGrid with drag handles; drag reorders the staged array; section bar moves
  with its line; footer total excludes rate-only; Custom item wording; no "Counts per" column;
  Priced per in details; brand select; description keeps newlines; stock chip tone by qty vs
  available; popover numbers equal the stock-detail payload.
- pytest: line payload carries drawings; first drawing by upload order; drawing stamped at issue and
  not overwritten (Q2 a); single-photo default (Q1 a) across `product_image_service` readers; 422
  message wording; PDF/Excel print the drawing and the multi-line description.
- browser: 1280 (sidebar open) no horizontal scroll, headers not truncated; 375 page does not
  scroll sideways.
