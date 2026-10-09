# PLAN: short-link page opens a sheet as a full-screen Excel viewer (LINK-XLSX-VIEWER)

Status: building (owner approved mock v2 + crew answers Q1-Q5, 10 Oct 2026). Track: M, lead
pattern (tester -> coder -> reviewer + security-reviewer, public page parsing untrusted files).
No migration, no permission change.
UAC: `link-xlsx-viewer-10oct-acceptance-criteria.md` (same folder)
Mock: `documentation/mockups/LINK-XLSX-VIEWER/index.html` (v2, invented figures)
Parent: `CARD-photo-send-links-09oct.md` (#1541, the `/f/{code}` page and its public routes).
Sibling: `documentation/plans/PLAN-excel-preview-26sep.md` (S1 shipped `SpreadsheetViewer` in
#1270; S2, the stored-file feeder + SheetJS bump, not started).

## 0. Owner / crew answers (10 Oct 2026, crew a66969903)

| Q | Ruling |
| --- | --- |
| Q1 | Own read-only grid; xlsx/xlsm parsed with `exceljs` (already in `package.json`, MIT) for styles; number formats via SheetJS SSF; `@tanstack/react-virtual` for rows. |
| Q2 | Pin the file's freeze pane when set; else auto-pin the first all-bold row; else only column letters + row numbers. |
| Q3 | Backend `/content` answers 413 over 10 MB; parse in a Web Worker; caps 50 sheets, 5,000 rows x 100 cols per sheet with a "Showing first N rows" line + Download; no formula eval, no macros, no external links, no images. |
| Q4 | Only `/f/{code}` now. The CRM `AttachmentPreviewModal` keeps `ExcelSlide`; swapping it is a follow-up (= excel-preview S2). |
| Q5 | Committed mock and test fixtures use invented figures, same structure as the real packing list. |
| - | SheetJS 0.18.5 CVEs: note in the PR; the bump is its own lane (excel-preview S2, owner ruling Q7 26 Sep). |

## 1. What exists today (measured)

- Page: `sorento_crm_frontend/app/(public)/f/[code]/page.tsx:149-168` - filename, View + Download,
  `AttachmentPreviewModal` opened at load.
- Preview: `components/common/AttachmentPreviewModal.tsx:579-750` `ExcelSlide` - SheetJS text,
  200 x 40 cap, cells truncated at 240px (:735).
- Bytes: `fileLinkService.ts:71` -> `GET /api/v1/public/file-links/{code}/content`
  (`sorento_crm_backend/app/api/v1/public/file_links.py:133-157`): rate limited, unknown and expired
  = same 404, `Cache-Control: no-store`, no size cap (reads the whole object, :142).
- Grid: `components/common/SpreadsheetViewer.tsx` (#1270) - virtualised rows, line sheet tabs +
  "..." sheet list, sticky header row, first column pinned, row search over named columns. Used by
  `app/(protected)/scm/low-stock-report/components/LowStockReportView.tsx`. Tests:
  `SpreadsheetViewer.test.tsx`.

## 2. Design

Simplest thing: extend `SpreadsheetViewer` additively (a second grid would duplicate the
virtualiser, tabs and sheet list), add one parser module, swap the page's sheet branch.

### 2.1 Backend (one route change)

`file_links.py` `/content`: after `download_bytes`, `len(content) > MAX_SHEET_PREVIEW_BYTES`
(10 MB, module constant) -> 413 `{"detail": "This file is too big to show here."}`, unless the
request carries `?download=1` (the Download button), which serves the bytes as today. Expiry,
404 sameness, rate limit, headers unchanged. (A HEAD-before-download is not built: storage
already returns the object in one call; trigger = a real file over 50 MB.)

### 2.2 Parser: `sorento_crm_frontend/lib/sheet-viewer/parseWorkbook.ts` (pure, no DOM)

`parseWorkbook(bytes: ArrayBuffer, filename: string): Promise<SpreadsheetWorkbook>` returning the
existing model extended (2.3). Rules:

- `.xlsx`/`.xlsm` -> exceljs `Workbook.xlsx.load`. `.xls`/`.csv` -> SheetJS (`xlsx`) as today,
  values + formatted text only.
- Sheets: first 50 in workbook order, hidden sheets skipped.
- Per sheet: rows 1..min(lastRow, 5000), cols 1..min(lastCol, 100); `truncatedRows`,
  `truncatedCols`, `totalRows` recorded. Fully blank trailing rows/cols dropped.
- Cell text: formula cell -> its cached `result` only (never evaluated; no cached result -> '');
  number/date -> SheetJS `SSF.format(numFmt, value)` (date -> Excel serial first); format error
  -> plain `String(value)`; rich text -> joined plain text; hyperlink -> its text, never a link;
  error values -> their code (`#DIV/0!`).
- Style kept: bold, italic, horizontal align, wrap, fill (solid ARGB), font colour, plus the
  numFmt's `[Red]` negative colour. Images, drawings, comments, data validation, external links,
  macros (`vbaProject.bin`) are never read into the model.
- Column widths: Excel char width -> px (`Math.round(w * 7 + 5)`, default 8.43); hidden columns 0.
- Merges: `{ r, c, rowSpan, colSpan }` from exceljs `model.merges`, clipped to the caps.
- Pinned rows/cols: `views[0].state === 'frozen'` -> `ySplit` rows / `xSplit` cols; else the
  first row within the first 30 that has >= 2 non-empty cells, all bold -> pin only that row
  (owner Q2; mock: header block rows 1-4 scroll away, row 7 sticks); else nothing pinned.
- Worker: `parseWorkbook.worker.ts` runs it; `parseWorkbookInWorker(bytes, name)` posts the
  ArrayBuffer (transfer), times out at 20 s (terminate -> "cannot be shown"), falls back to the
  main thread only when `Worker` is undefined (tests). UNVERIFIED: Turbopack dev + `next build`
  bundle `new Worker(new URL('./parseWorkbook.worker.ts', import.meta.url))`; the coder proves it
  in the browser pass, else inline the worker via a Blob URL.

### 2.3 `SpreadsheetViewer` additions (all optional; low stock report unchanged)

`SpreadsheetSheet` gains: `columnWidths?: number[]`, `cells?: (CellStyle | null)[][]`
(parallel to `rows`), `merges?`, `pinnedRows?: number[]` (row indexes kept sticky under the
header), `frozenCols?: number`, `excelChrome?: boolean` (column letters A, B, ... as the header +
a sticky row-number column; `columns` then unused), `truncatedRows?`, `totalRows?`.
Viewer prop `searchMode?: 'filter' | 'highlight'` (default `filter`, today's behaviour);
`highlight` marks every matching cell (same `<mark>` style as `AttachmentPreviewModal.tsx:565`),
shows "i of n" with previous/next buttons that scroll the virtualiser to the match, never hides
rows, and searches every column. Prop `tabsPosition?: 'top' | 'bottom'` (mock: bottom).

### 2.4 Page: `app/(public)/f/[code]/page.tsx` sheet branch

Full-height layout (`min-h-dvh flex flex-col`): top bar = filename (truncate) + Download
(icon-only under `sm`, labelled above; `aria-label="Download"`), then the viewer filling the rest.
No View button, no modal. States: loading (Loader2), too big (413) "This file is too big to show
here." + Download, unreadable / timeout / no sheets "This file cannot be shown here." + Download,
expired + error unchanged. Download uses `?download=1`. Footnote when truncated: "Showing first
5,000 rows. Download for the full sheet." (columns: "... first 100 columns ..."). Grid has
`touch-action: pan-x pan-y pinch-zoom`; viewport meta unchanged (pinch allowed).

## 3. Tickets (one lane, one PR)

1. tester: red pytest (413 cap + `download=1` bypass) and red vitest (parser rules with generated
   fixture workbooks, viewer additions, page states). Fixtures generated in-test with exceljs,
   invented data, no binary from prod.
2. coder: make them green; no edits to tester assertions without lead sign-off.
3. reviewer + security-reviewer (public page, untrusted parse) + agent-browser 375/1280 with wide
   sheet, long text, merges, many sheets, freeze pane, 5,000+ rows, >10 MB file.
4. hand test script, crew test copy.

## 4. Risks

- Zip bomb inside 10 MB: exceljs inflates everything; the worker + 20 s timeout keeps the page
  alive and ends in "cannot be shown".
- exceljs bundle (~256 KB gz) loads only inside the worker chunk on this public page.
- Vertical merges across virtualised rows: the merged cell draws from its top row; when the top
  row is scrolled out the covered rows show blank (accepted).
