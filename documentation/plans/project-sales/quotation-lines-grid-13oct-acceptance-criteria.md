# UAC: Quotation lines grid (13 Oct)

Status: DRAFT. Written against the recommended answers of the mock v1 card
(`documentation/mockups/QUOTATION-UX/index.html#q`); rows marked (Qn) change with the owner's answer.

| AC | Owner point | Criterion |
|---|---|---|
| AC-QLG01 | 1 | The Lines tab of a quotation (form and read view) is the app DataGrid. Rows reorder by dragging a handle; the new order is what Save stores (array position, as today). |
| AC-QLG02 | 1 | Columns can be resized and reordered by drag; order and widths come back after a reload (column preferences key `projects.projects.view::quotation-lines`). |
| AC-QLG03 | 8 | A section bar is a full-width row that moves with the line it opens. Add a section, the section label and the continuous line numbering behave as today. |
| AC-QLG04 | 2 | At a 1280 px viewport with the sidebar open, the grid has no horizontal scroll and no header is truncated. |
| AC-QLG05 | 2 | Photo is a 40 px thumbnail; clicking it opens AttachmentPreviewModal. Empty states are today's ("No photo chosen", "No photo on file"). |
| AC-QLG06 | 2 (Q1) | A product with exactly one "Product Photos" image and no chosen photo shows that image as its photo, on the quotation and on every other consumer of `product_image_service`. Several images and none chosen still reads "No photo chosen". |
| AC-QLG07 | 2 (Q2) | Drawing is a 40 px thumbnail of the product's first "Technical Specifications" image (upload order). Clicking opens the viewer, which steps through every drawing of the quotation. A product with N > 1 drawings shows N on the thumbnail. No drawing reads "no drawing". |
| AC-QLG08 | 2 (Q2) | The typed Tech spec box is gone from the grid. PDF and Excel print the drawing in TECHNICAL SPEC; a line that still carries typed spec text prints it under the description. |
| AC-QLG09 | 2 (Q2) | Issuing a version stamps the drawing it printed on each line; a later change of the product's drawings does not change a re-download of that version. |
| AC-QLG10 | 3 (Q3) | A line with no product shows the badge "Custom item"; the picker placeholder is "Pick a product"; the error is "A custom item needs a description". The words "off-catalog" appear nowhere on the screen. |
| AC-QLG11 | 4 | Brand is a clearable SearchableSelect over the brands master. Picking a product still fills it. The saved value is the brand name. |
| AC-QLG12 | 5 | Description is a multi-line box that grows with its text; line breaks survive Save, the read view, the PDF and the Excel. |
| AC-QLG13 | 6 (Q5) | Each product line shows a stock chip with Available for the chosen stock group (on hand - SO qty + SPO qty, the stock-detail rule). Green when it covers the quoted qty, amber when partly, red at zero or below. A custom item shows no chip. |
| AC-QLG14 | 6 | Clicking the chip opens a read-only popover with the Order Inquiry stock table for the group, then the site pools, then incoming SPOs. The numbers equal the Order Inquiry stock grid for the same product and group. |
| AC-QLG15 | 6 (Q5) | A "Stock at" dropdown on the Lines tab chooses the group (fulfilment-planning groups); the choice is remembered in the browser. |
| AC-QLG16 | 7 (Q4) | No "Counts per" column. Line details (row menu) holds Complete set, "Priced per" (Each house unit / Each bathroom / Each facility / Whole site (common area), empty = Not set) and Notes. No grid cell wraps a select onto two rows. |
| AC-QLG17 | 8 | Rate only is a tick under the line total; a rate-only line shows "rate only" and is left out of the scope total (as today). |
| AC-QLG18 | - | Unchanged: one Save for every scope, staged remove with Restore, search hides rows without renumbering, below-floor and non-standard badges, frozen versions read-only. |
| AC-QLG19 | - | At 375 px the page does not scroll sideways; only the grid scrolls inside its card. |
