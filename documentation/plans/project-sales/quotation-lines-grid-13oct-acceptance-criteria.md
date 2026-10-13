# UAC: Quotation lines grid (13 Oct)

Status: AGREED. Owner answers to the mock v1 card, 14 Oct: Q1 (a) + choose when several, Q2 (a) +
choose when several (same picker), Q3 "Custom item", Q4 (a) "Priced per" in Line details, Q5 (a)
"Stock at" dropdown remembered per browser reusing the Fulfilment Planning / Order Inquiry stock
components. Mock v2 approved: `documentation/mockups/QUOTATION-UX/index.html`.

| AC | Owner point | Criterion |
|---|---|---|
| AC-QLG01 | 1 | The Lines tab of a quotation (form and read view) is the app DataGrid. In edit mode rows reorder by dragging a handle; the order on screen is the order Save stores (array position, as today). |
| AC-QLG02 | 1 | Columns can be resized and reordered by drag; order and widths come back after a reload (column preferences key `projects.projects.view::quotation-lines`). |
| AC-QLG03 | 8 | A section bar is a full-width row that moves with the line it opens. Add a section, the section label and continuous line numbering behave as today. |
| AC-QLG04 | 2 | At a 1280 px viewport with the sidebar open, the grid has no horizontal scroll and no header is truncated. |
| AC-QLG05 | 2 | Photo is a 40 px thumbnail; clicking it opens AttachmentPreviewModal. Lines with no photo show today's empty states. |
| AC-QLG06 | 2 (Q1) | A product with no chosen photo and exactly one "Product Photos" image uses that image as its photo, for every reader of `product_image_service` (quotation, brochure, 3D models). Several and none chosen: state `not_chosen`. A drawing ("Technical Specifications") is never counted as a photo. |
| AC-QLG07 | 2 (Q1) | When a product has several photos, the editor offers a picker (candidate thumbnails); the pick is stored on the line as `image_attachment_id`, saved with the quotation, and wins over the product's own answer. The product's attachments are not changed. |
| AC-QLG08 | 2 (Q2) | Drawing is a 40 px thumbnail of the line's drawing: the picked one (`drawing_attachment_id`), else the product's first "Technical Specifications" image by upload time. A product with N > 1 drawings shows N on the thumbnail and opens the same picker. No drawing: "no drawing". Custom item: none. |
| AC-QLG09 | 2 (Q2) | The server refuses (422) a photo or drawing id that is not an image attachment of the line's product (drawing: of type "Technical Specifications"), that is deleted, or that is set on a line with no product. An unchanged stored id is not re-checked. |
| AC-QLG10 | 2 (Q2) | Issuing a version fills `drawing_attachment_id` on every product line that has none (fill only, never overwrite), exactly like the photo. A later change to the product's drawings does not change a re-download of that issue. A revision copies both ids. |
| AC-QLG11 | 2 (Q2) | The issue PDF and Excel print the line's drawing image in TECHNICAL SPEC. Typed tech-spec text, where a line still has some, prints under the description. The typed Tech spec box is gone from the grid. |
| AC-QLG12 | 3 (Q3) | A line with no product shows the badge "Custom item"; the picker placeholder is "Pick a product"; the error (screen and 422) is "A custom item needs a description". "Off-catalog" appears nowhere on the quotation screens. |
| AC-QLG13 | 4 | Brand is a clearable SearchableSelect over the brands master. Picking a product still fills it. The saved value is the brand name. |
| AC-QLG14 | 5 | Description is a multi-line box that grows with its text; line breaks survive Save, the read view, the PDF and the Excel. |
| AC-QLG15 | 6 (Q5) | Each product line shows a stock chip with Available for the chosen group (on hand - SO qty + SPO qty, the stock-detail rule). Green when it covers the quoted qty, amber when 0 < Available < qty, red at 0 or below. A custom item shows no chip. |
| AC-QLG16 | 6 | Clicking the chip opens a read-only popover built from the existing stock components (`useStockDetail` + `CellStockTable`): the group's locations with subtotal, then the site pools, then incoming SPOs. Numbers equal the stock-detail endpoint for the same product and group. |
| AC-QLG17 | 6 (Q5) | A "Stock at" dropdown on the Lines tab lists the fulfilment-planning groups; the choice is remembered in the browser and survives a reload. |
| AC-QLG18 | 7 (Q4) | No "Counts per" column. Line details (row menu) holds Complete set, "Priced per" (Each house unit / Each bathroom / Each facility / Whole site (common area), empty = Not set) and Notes. No grid cell wraps a select onto two rows. |
| AC-QLG19 | 8 | Rate only is a tick under the line total; a rate-only line shows "rate only" and is left out of the scope total (as today). |
| AC-QLG20 | - | Unchanged: one Save for every scope, staged remove with Restore, search hides rows without renumbering, below-floor and non-standard badges, frozen and issued versions read-only (no picker, no drag). |
| AC-QLG21 | - | At 375 px the page does not scroll sideways; only the grid scrolls inside its card. |
