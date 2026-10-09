/**
 * Reads a spreadsheet file into the `SpreadsheetWorkbook` model (PLAN-link-xlsx-viewer-10oct,
 * section 2.2). Pure: no DOM. Formulas are never evaluated (the cached result only); images,
 * links, comments and macros never enter the model.
 */
import type ExcelJS from 'exceljs';
import type * as XLSXTypes from 'xlsx';

import type { CellStyle, SheetMerge, SpreadsheetSheet, SpreadsheetWorkbook } from './types';

export const MAX_SHEETS = 50;
export const MAX_ROWS = 5000;
export const MAX_COLS = 100;
const DEFAULT_COL_CHARS = 8.43;
const PIN_SCAN_ROWS = 30;
export const MAX_MERGES = 10_000;
/** Colour of a `[Red]` number format on a negative value. */
const RED = '#ff0000';

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot + 1).toLowerCase() : '';
}

export async function parseWorkbook(
  bytes: ArrayBuffer,
  filename: string,
): Promise<SpreadsheetWorkbook> {
  const ext = extensionOf(filename);
  const workbook =
    ext === 'xls' || ext === 'csv' ? await parseWithSheetJs(bytes, ext) : await parseWithExcelJs(bytes);
  return workbook;
}

// ---------------------------------------------------------------- shared helpers

function argbToCss(argb: string | undefined): string | undefined {
  if (!argb || !/^[0-9a-f]{8}$/i.test(argb)) return undefined;
  if (argb.slice(0, 2).toLowerCase() === '00') return undefined; // fully transparent
  return `#${argb.slice(2).toLowerCase()}`;
}

function dateToSerial(date: Date): number {
  return date.getTime() / 86_400_000 + 25_569;
}

function widthPx(chars: number): number {
  return Math.round(chars * 7 + 5);
}

// ---------------------------------------------------------------- exceljs (xlsx / xlsm)

function plainValue(value: ExcelJS.CellValue): {
  raw: string | number | boolean | Date | null;
  error?: string;
} {
  if (value === null || value === undefined) return { raw: null };
  if (value instanceof Date) return { raw: value };
  if (typeof value !== 'object') return { raw: value as string | number | boolean };
  const v = value as unknown as Record<string, unknown>;
  if ('richText' in v) {
    return { raw: (v.richText as { text: string }[]).map((t) => t.text).join('') };
  }
  if ('error' in v) return { raw: null, error: String(v.error) };
  if ('formula' in v || 'sharedFormula' in v) {
    const result = v.result as ExcelJS.CellValue | undefined;
    if (result === undefined || result === null) return { raw: null };
    return plainValue(result);
  }
  if ('text' in v) return { raw: String(v.text ?? '') };
  return { raw: null };
}

function formatNumber(SSF: typeof XLSXTypes.SSF, raw: number | Date, fmt: string | undefined): string {
  const value = raw instanceof Date ? dateToSerial(raw) : raw;
  try {
    return SSF.format(fmt || 'General', value);
  } catch {
    return String(value);
  }
}

async function parseWithExcelJs(bytes: ArrayBuffer): Promise<SpreadsheetWorkbook> {
  const [{ default: ExcelJSLib }, XLSX] = await Promise.all([import('exceljs'), import('xlsx')]);
  const wb = new ExcelJSLib.Workbook();
  await wb.xlsx.load(bytes);

  const sheets: SpreadsheetSheet[] = [];
  for (const ws of wb.worksheets) {
    if (sheets.length >= MAX_SHEETS) break;
    if (ws.state && ws.state !== 'visible') continue;
    sheets.push(readExcelJsSheet(ws, XLSX.SSF));
  }
  if (sheets.length === 0) throw new Error('No sheets');
  return { sheets };
}

interface MergeBox {
  r1: number;
  c1: number;
  r2: number;
  c2: number;
}

function excelJsMerges(ws: ExcelJS.Worksheet): MergeBox[] {
  // `_merges` is exceljs's own address map; `ws.model` would rebuild every row to read it.
  const map = (ws as unknown as { _merges?: Record<string, { model: Record<string, number> }> })._merges;
  const out: MergeBox[] = [];
  for (const entry of Object.values(map ?? {})) {
    if (out.length >= MAX_MERGES) break;
    const m = entry.model;
    out.push({ r1: m.top - 1, c1: m.left - 1, r2: m.bottom - 1, c2: m.right - 1 });
  }
  return out;
}

function hasRawValue(value: ExcelJS.CellValue): boolean {
  const { raw, error } = plainValue(value);
  return error !== undefined || (raw !== null && raw !== '');
}

function readExcelJsSheet(ws: ExcelJS.Worksheet, SSF: typeof XLSXTypes.SSF): SpreadsheetSheet {
  // Pass 1, no formatting: the real last row and column, and the hidden rows.
  const hidden: number[] = [];
  let lastRow = 0;
  let lastCol = 0;
  ws.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    if (row.hidden) hidden.push(rowNumber);
    let any = false;
    row.eachCell({ includeEmpty: false }, (cell, colNumber) => {
      if (cell.type === 1 /* ValueType.Merge: a covered cell echoes its head */) return;
      if (!hasRawValue(cell.value)) return;
      any = true;
      if (colNumber > lastCol) lastCol = colNumber;
    });
    if (any && !row.hidden) lastRow = Math.max(lastRow, rowNumber);
  });
  hidden.sort((a, b) => a - b);
  /** Visible rows above sheet row `n` (1-based): n's 0-based position when it is visible. */
  const visibleAbove = (n: number): number => {
    let lo = 0;
    let hi = hidden.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (hidden[mid] < n) lo = mid + 1;
      else hi = mid;
    }
    return n - 1 - lo;
  };

  const totalRows = visibleAbove(lastRow + 1);
  const rowCount = Math.min(totalRows, MAX_ROWS);
  const colCount = Math.min(lastCol, MAX_COLS);
  const rows: string[][] = Array.from({ length: rowCount }, () => []);
  const cells: (CellStyle | null)[][] = Array.from({ length: rowCount }, () => []);
  const boldRows: boolean[] = new Array(rowCount).fill(false);
  const hiddenSet = new Set(hidden);

  // Pass 2: format only the rows that will be shown.
  ws.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    if (hiddenSet.has(rowNumber) || rowNumber > lastRow) return;
    const pos = visibleAbove(rowNumber);
    if (pos >= rowCount) return;
    let nonEmpty = 0;
    let allBold = true;
    row.eachCell({ includeEmpty: false }, (cell, colNumber) => {
      const c = colNumber - 1;
      if (c >= colCount || cell.type === 1) return;
      const { raw, error } = plainValue(cell.value);
      let text = '';
      let negativeRed = false;
      if (error) text = error;
      else if (typeof raw === 'boolean') text = raw ? 'TRUE' : 'FALSE';
      else if (raw instanceof Date || typeof raw === 'number') {
        text = formatNumber(SSF, raw, cell.numFmt);
        const numeric = raw instanceof Date ? 1 : raw;
        negativeRed = numeric < 0 && /\[red\]/i.test(cell.numFmt ?? '');
      } else if (raw !== null) text = String(raw);
      if (text === '') return;
      rows[pos][c] = text;
      nonEmpty += 1;
      const font = cell.font ?? {};
      if (!font.bold) allBold = false;
      const style: CellStyle = {};
      if (font.bold) style.bold = true;
      if (font.italic) style.italic = true;
      const align = cell.alignment?.horizontal;
      if (align === 'left' || align === 'center' || align === 'right') style.align = align;
      if (cell.alignment?.wrapText) style.wrap = true;
      const fill = cell.fill;
      if (fill && fill.type === 'pattern' && fill.pattern === 'solid') {
        const css = argbToCss((fill.fgColor as { argb?: string } | undefined)?.argb);
        if (css) style.fill = css;
      }
      const color = negativeRed ? RED : argbToCss((font.color as { argb?: string } | undefined)?.argb);
      if (color) style.color = color;
      cells[pos][c] = Object.keys(style).length ? style : null;
    });
    boldRows[pos] = nonEmpty >= 2 && allBold;
  });

  const outRows = rows.map((r) => Array.from({ length: colCount }, (_, c) => r[c] ?? ''));
  const outCells = cells.map((r) => Array.from({ length: colCount }, (_, c) => r[c] ?? null));

  const sheet: SpreadsheetSheet = {
    title: ws.name,
    columns: [],
    rows: outRows,
    cells: outCells,
    excelChrome: true,
    columnWidths: Array.from({ length: colCount }, (_, c) => {
      const col = ws.getColumn(c + 1);
      if (col.hidden) return 0;
      const chars = col.width ?? ws.properties.defaultColWidth ?? DEFAULT_COL_CHARS;
      return widthPx(chars);
    }),
    merges: excelJsMerges(ws)
      .filter((m) => m.c1 < colCount && (m.r2 > m.r1 || m.c2 > m.c1) && !hiddenSet.has(m.r1 + 1))
      .map((m): SheetMerge => {
        const r = visibleAbove(m.r1 + 1);
        return {
          r,
          c: m.c1,
          rowSpan: visibleAbove(m.r2 + 2) - r,
          colSpan: Math.min(m.c2, MAX_COLS - 1) - m.c1 + 1,
        };
      })
      .filter((m) => m.r < rowCount),
  };
  if (hidden.length > 0) {
    const numbers: number[] = [];
    for (let n = 1; numbers.length < rowCount; n += 1) if (!hiddenSet.has(n)) numbers.push(n);
    sheet.rowNumbers = numbers;
  }
  if (totalRows > MAX_ROWS) {
    sheet.truncatedRows = true;
    sheet.totalRows = totalRows;
  }
  if (lastCol > MAX_COLS) sheet.truncatedCols = true;

  const view = ws.views?.[0] as { state?: string; xSplit?: number; ySplit?: number } | undefined;
  if (view?.state === 'frozen' && ((view.ySplit ?? 0) > 0 || (view.xSplit ?? 0) > 0)) {
    const y = Math.min(visibleAbove((view.ySplit ?? 0) + 1), rowCount);
    if (y > 0) sheet.pinnedRows = Array.from({ length: y }, (_, i) => i);
    if ((view.xSplit ?? 0) > 0) sheet.frozenCols = Math.min(view.xSplit as number, colCount);
  } else {
    const header = boldRows.slice(0, PIN_SCAN_ROWS).findIndex((b) => b);
    if (header >= 0) sheet.pinnedRows = [header];
  }
  return sheet;
}

// ---------------------------------------------------------------- SheetJS (xls / csv)

async function parseWithSheetJs(bytes: ArrayBuffer, ext: string): Promise<SpreadsheetWorkbook> {
  const XLSX = await import('xlsx');
  const book =
    ext === 'csv'
      ? XLSX.read(new TextDecoder('utf-8').decode(bytes), { type: 'string' })
      : XLSX.read(new Uint8Array(bytes), { type: 'array' });
  const hiddenSheets = book.Workbook?.Sheets ?? [];
  const sheets: SpreadsheetSheet[] = [];
  book.SheetNames.forEach((name, index) => {
    if (sheets.length >= MAX_SHEETS) return;
    if (hiddenSheets[index]?.Hidden) return;
    sheets.push(readSheetJsSheet(XLSX, name, book.Sheets[name]));
  });
  if (sheets.length === 0) throw new Error('No sheets');
  return { sheets };
}

/**
 * The declared `!ref` is not the content (a stale used-range marker once cost 18 s in
 * AttachmentPreviewModal), so the bounds come from the cells that hold text.
 */
function readSheetJsSheet(XLSX: typeof XLSXTypes, name: string, ws: XLSXTypes.WorkSheet): SpreadsheetSheet {
  const found: { r: number; c: number; text: string }[] = [];
  let minR = Infinity;
  let maxR = -1;
  let minC = Infinity;
  let maxC = -1;
  for (const key of Object.keys(ws ?? {})) {
    if (key.startsWith('!')) continue;
    const text = XLSX.utils.format_cell(ws[key]);
    if (text === '' || text == null) continue;
    const { r, c } = XLSX.utils.decode_cell(key);
    found.push({ r, c, text: String(text) });
    if (r < minR) minR = r;
    if (r > maxR) maxR = r;
    if (c < minC) minC = c;
    if (c > maxC) maxC = c;
  }
  if (found.length === 0) return { title: name, columns: [], rows: [], excelChrome: true };
  const rowCount = Math.min(maxR - minR + 1, MAX_ROWS);
  const colCount = Math.min(maxC - minC + 1, MAX_COLS);
  const rows: string[][] = Array.from({ length: rowCount }, () => new Array<string>(colCount).fill(''));
  for (const { r, c, text } of found) {
    if (r - minR < rowCount && c - minC < colCount) rows[r - minR][c - minC] = text;
  }
  const sheet: SpreadsheetSheet = {
    title: name,
    columns: [],
    rows,
    excelChrome: true,
    firstRowNumber: minR + 1,
    firstColIndex: minC,
  };
  if (maxR - minR + 1 > MAX_ROWS) sheet.truncatedRows = true;
  if (maxC - minC + 1 > MAX_COLS) sheet.truncatedCols = true;
  return sheet;
}
