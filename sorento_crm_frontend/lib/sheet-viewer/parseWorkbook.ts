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

function parseRange(ref: string): { r1: number; c1: number; r2: number; c2: number } | null {
  const m = /^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/i.exec(ref);
  if (!m) return null;
  const col = (letters: string) =>
    letters.toUpperCase().split('').reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1;
  const r1 = Number(m[2]) - 1;
  const c1 = col(m[1]);
  return { r1, c1, r2: m[4] ? Number(m[4]) - 1 : r1, c2: m[3] ? col(m[3]) : c1 };
}

function widthPx(chars: number): number {
  return Math.round(chars * 7 + 5);
}

// ---------------------------------------------------------------- exceljs (xlsx / xlsm)

function plainValue(value: ExcelJS.CellValue): { raw: string | number | Date | null; error?: string } {
  if (value === null || value === undefined) return { raw: null };
  if (value instanceof Date) return { raw: value };
  if (typeof value !== 'object') return { raw: value as string | number };
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

function readExcelJsSheet(ws: ExcelJS.Worksheet, SSF: typeof XLSXTypes.SSF): SpreadsheetSheet {
  const merges = (((ws.model as { merges?: string[] }).merges ?? []) as string[])
    .map(parseRange)
    .filter((m): m is NonNullable<typeof m> => m !== null);
  const covered = new Set<string>();
  for (const m of merges) {
    for (let r = m.r1; r <= Math.min(m.r2, MAX_ROWS - 1); r += 1) {
      for (let c = m.c1; c <= Math.min(m.c2, MAX_COLS - 1); c += 1) {
        if (r !== m.r1 || c !== m.c1) covered.add(`${r}:${c}`);
      }
    }
  }

  const rows: (string | number | null)[][] = [];
  const cells: (CellStyle | null)[][] = [];
  const boldRows: boolean[] = [];
  let lastRowWithText = 0;
  let lastColWithText = 0;

  ws.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    const r = rowNumber - 1;
    let anyText = false;
    const kept = r < MAX_ROWS;
    const textRow: string[] = [];
    const styleRow: (CellStyle | null)[] = [];
    let nonEmpty = 0;
    let allBold = true;
    row.eachCell({ includeEmpty: false }, (cell, colNumber) => {
      const c = colNumber - 1;
      if (covered.has(`${r}:${c}`)) return;
      const { raw, error } = plainValue(cell.value);
      let text = '';
      let negativeRed = false;
      if (error) text = error;
      else if (raw instanceof Date || typeof raw === 'number') {
        text = formatNumber(SSF, raw, cell.numFmt);
        const numeric = raw instanceof Date ? 1 : raw;
        negativeRed = numeric < 0 && /\[red\]/i.test(cell.numFmt ?? '');
      } else if (raw !== null) text = String(raw);
      if (text === '') return;
      anyText = true;
      lastColWithText = Math.max(lastColWithText, colNumber);
      if (!kept || c >= MAX_COLS) return;
      textRow[c] = text;
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
      styleRow[c] = Object.keys(style).length ? style : null;
    });
    if (anyText) lastRowWithText = Math.max(lastRowWithText, rowNumber);
    if (kept) {
      rows[r] = Array.from(textRow, (t) => t ?? '');
      cells[r] = Array.from(styleRow, (s) => s ?? null);
      boldRows[r] = nonEmpty >= 2 && allBold;
    }
  });

  const rowCount = Math.min(lastRowWithText, MAX_ROWS);
  const colCount = Math.min(lastColWithText, MAX_COLS);
  const outRows: (string | number | null)[][] = [];
  const outCells: (CellStyle | null)[][] = [];
  for (let r = 0; r < rowCount; r += 1) {
    const text = rows[r] ?? [];
    const style = cells[r] ?? [];
    outRows.push(Array.from({ length: colCount }, (_, c) => text[c] ?? ''));
    outCells.push(Array.from({ length: colCount }, (_, c) => style[c] ?? null));
  }

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
    merges: merges
      .filter((m) => m.r1 < rowCount && m.c1 < colCount && (m.r2 > m.r1 || m.c2 > m.c1))
      .map(
        (m): SheetMerge => ({
          r: m.r1,
          c: m.c1,
          rowSpan: Math.min(m.r2, MAX_ROWS - 1) - m.r1 + 1,
          colSpan: Math.min(m.c2, MAX_COLS - 1) - m.c1 + 1,
        }),
      ),
  };
  if (lastRowWithText > MAX_ROWS) {
    sheet.truncatedRows = true;
    sheet.totalRows = lastRowWithText;
  }
  if (lastColWithText > MAX_COLS) sheet.truncatedCols = true;

  const view = ws.views?.[0] as { state?: string; xSplit?: number; ySplit?: number } | undefined;
  if (view?.state === 'frozen' && ((view.ySplit ?? 0) > 0 || (view.xSplit ?? 0) > 0)) {
    const y = Math.min(view.ySplit ?? 0, rowCount);
    if (y > 0) sheet.pinnedRows = Array.from({ length: y }, (_, i) => i);
    if ((view.xSplit ?? 0) > 0) sheet.frozenCols = Math.min(view.xSplit as number, colCount);
  } else {
    const header = boldRows.slice(0, PIN_SCAN_ROWS).findIndex((b) => b);
    if (header >= 0 && header < rowCount) sheet.pinnedRows = [header];
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
  const hidden = book.Workbook?.Sheets ?? [];
  const sheets: SpreadsheetSheet[] = [];
  book.SheetNames.forEach((name, index) => {
    if (sheets.length >= MAX_SHEETS) return;
    if (hidden[index]?.Hidden) return;
    const ws = book.Sheets[name];
    let range: string | undefined;
    let truncatedRows = false;
    let truncatedCols = false;
    const ref = ws?.['!ref'];
    if (ref) {
      const r = XLSX.utils.decode_range(ref);
      const endRow = Math.min(r.e.r, r.s.r + MAX_ROWS - 1);
      const endCol = Math.min(r.e.c, r.s.c + MAX_COLS - 1);
      truncatedRows = r.e.r > endRow;
      truncatedCols = r.e.c > endCol;
      range = XLSX.utils.encode_range({ s: r.s, e: { r: endRow, c: endCol } });
    }
    const aoa = XLSX.utils.sheet_to_json<unknown[]>(ws, {
      header: 1,
      blankrows: true,
      defval: '',
      raw: false,
      ...(range ? { range } : {}),
    });
    const rows = aoa.map((row) => row.slice(0, MAX_COLS).map((c) => (c == null ? '' : String(c))));
    while (rows.length > 0 && rows[rows.length - 1].every((c) => c === '')) rows.pop();
    const sheet: SpreadsheetSheet = { title: name, columns: [], rows, excelChrome: true };
    if (truncatedRows) sheet.truncatedRows = true;
    if (truncatedCols) sheet.truncatedCols = true;
    sheets.push(sheet);
  });
  if (sheets.length === 0) throw new Error('No sheets');
  return { sheets };
}
