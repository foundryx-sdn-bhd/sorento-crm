# UAC: short-link Excel viewer (LINK-XLSX-VIEWER)

Plan: `PLAN-link-xlsx-viewer-10oct.md`. Mock: `documentation/mockups/LINK-XLSX-VIEWER/index.html`.
Every fixture is invented data generated in the test (public repo).

## Backend (`GET /api/v1/public/file-links/{code}/content`)

- AC-B1 A sheet link whose bytes are 10 MB or less answers 200 with the bytes (unchanged).
- AC-B2 A sheet link whose bytes are over 10 MB (10 * 1024 * 1024 + 1) answers 413 with
  `{"detail": "This file is too big to show here."}` and no file bytes.
- AC-B3 The same over-cap link with `?download=1` answers 200 with the full bytes and the
  existing headers (Content-Disposition, nosniff, no-store).
- AC-B4 Unknown / expired codes still answer the same 404 with and without `download=1`; the
  rate limit still applies to both.

## Parser (`lib/sheet-viewer/parseWorkbook.ts`)

- AC-P1 Packing-list fixture (structure of the owner's file: header block A1:B4 with dates in
  `mm-dd-yy`, bold header row 7, 16 lines with `#,##0.00`, bold TOTAL row of `SUM` formulas with
  cached results) -> one sheet; row 8 col E reads `40.00`; B1 reads `09-02-26`; TOTAL cells read
  the cached result formatted (`#,##0.00`), never a recomputed value (fixture caches a value that
  differs from the true sum; the parser must show the cached one).
- AC-P2 A formula cell with no cached result reads `''`.
- AC-P3 Column widths come from the file (char width -> px, default width for unset columns).
- AC-P4 Merges come from the file as `{r, c, rowSpan, colSpan}`.
- AC-P5 Bold / italic / fill / align / wrap carried per cell; `[Red]` negative numbers carry red.
- AC-P6 Freeze pane `ySplit: 2, xSplit: 1` -> pinned rows 0..1, `frozenCols: 1`.
- AC-P7 No freeze pane -> the first all-bold row (>= 2 non-empty cells, within first 30 rows)
  is the only pinned row (row index 6 for the packing list); no bold row -> none pinned.
- AC-P8 Caps: 60 sheets -> 50 kept; 6,000 rows -> 5,000 kept, `truncatedRows` true,
  `totalRows` 6000; 120 columns -> 100 kept, `truncatedCols` true. Hidden sheets skipped.
- AC-P9 Rich text joins to plain text; a hyperlink cell gives its text only (no url in the
  model); an error cell gives its code (`#DIV/0!`).
- AC-P10 `.csv` and `.xls` still parse (values + formatted text, no styles).
- AC-P11 Garbage bytes reject with an error (page shows "cannot be shown").

## Viewer (`SpreadsheetViewer` additions)

- AC-V1 `excelChrome`: header shows A, B, C ... and every row shows its sheet row number.
- AC-V2 Column widths applied from `columnWidths`; numbers right-aligned; bold/fill drawn.
- AC-V3 Pinned rows stay sticky under the letters while the grid scrolls; frozen columns stay
  sticky at the left.
- AC-V4 Merged cell spans its columns (and rows).
- AC-V5 `searchMode="highlight"`: typing "basin" marks every matching cell, shows "1 of N",
  next/previous step through matches; no row is hidden; clearing restores.
- AC-V6 Sheet tabs at the bottom (`tabsPosition="bottom"`); switching tabs shows that sheet.
- AC-V7 Low stock report usage unchanged (existing `SpreadsheetViewer.test.tsx` green untouched).

## Page (`/f/{code}`, sheet link)

- AC-F1 Opens straight into the viewer full screen: filename + Download in the top bar, no
  "View" button, no modal.
- AC-F2 413 -> "This file is too big to show here." + Download button.
- AC-F3 Parse failure or 20 s timeout -> "This file cannot be shown here." + Download button.
- AC-F4 Download fetches `.../content?download=1` and saves under the link's filename.
- AC-F5 Truncated sheet shows "Showing first 5,000 rows. Download for the full sheet."
- AC-F6 Expired / error wording unchanged; a "file" link still redirects to its https url.
- AC-F7 Usable at 375 and 1280: no page-level horizontal scroll, grid scrolls both ways inside
  itself, pinch zoom not blocked, Download reachable.
