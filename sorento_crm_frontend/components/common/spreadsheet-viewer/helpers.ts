import { splitHighlightSegments } from '@/lib/textHighlight';
import type {
  SpreadsheetCell,
  SpreadsheetSheet,
} from '@/lib/sheet-viewer/types';

export { splitHighlightSegments };

/** Rows read when sizing columns: enough to see a typical cell, cheap on 50,000 rows. */
export const WIDTH_SAMPLE = 200;

const numberFormat = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 4,
});

export function formatCount(n: number): string {
  return numberFormat.format(n);
}

export function cellText(value: SpreadsheetCell | undefined): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'number' ? numberFormat.format(value) : value;
}

/** Excel column letters: 0 -> A, 25 -> Z, 26 -> AA. */
export function columnLetters(index: number): string {
  let n = index + 1;
  let out = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

const NUMERIC_TEXT = /^[-+(]?[\d,]*\.?\d+%?\)?$/;

/** A formatted cell reads as a number: right-aligned like Excel does. */
export function looksNumeric(text: string): boolean {
  return NUMERIC_TEXT.test(text);
}

export function columnCount(sheet: SpreadsheetSheet): number {
  if (!sheet.excelChrome) return sheet.columns.length;
  let max = Math.max(sheet.columns.length, sheet.columnWidths?.length ?? 0);
  for (const row of sheet.rows) max = Math.max(max, row.length);
  return max;
}

/** A column is numeric when its first filled cells are numbers. */
export function numericColumns(sheet: SpreadsheetSheet): boolean[] {
  return Array.from({ length: columnCount(sheet) }, (_, col) => {
    for (let i = 0; i < Math.min(sheet.rows.length, WIDTH_SAMPLE); i += 1) {
      const value = sheet.rows[i][col];
      if (value === null || value === undefined || value === '') continue;
      return typeof value === 'number';
    }
    return false;
  });
}

export function sizeColumns(sheet: SpreadsheetSheet): number[] {
  return Array.from({ length: columnCount(sheet) }, (_, col) => {
    const given = sheet.columnWidths?.[col];
    if (given !== undefined) return given;
    let longest = sheet.columns[col]?.length ?? 1;
    for (let i = 0; i < Math.min(sheet.rows.length, WIDTH_SAMPLE); i += 1) {
      longest = Math.max(longest, cellText(sheet.rows[i][col]).length);
    }
    return Math.min(320, Math.max(72, Math.round(longest * 7.5) + 24));
  });
}

export interface MergeIndex {
  /** Top-left cell key `r:c` -> its (clamped) spans. */
  heads: Map<string, { rowSpan: number; colSpan: number }>;
  /** Every other cell a merge covers. */
  covered: Set<string>;
  /** Rows holding the head of a merge that spans more than one row. */
  tallRows: Set<number>;
  /** Merges spanning more than one row, for finding the ones a scroll window cuts through. */
  tall: { r: number; rowSpan: number }[];
}

/** Hostile files declare overlapping merges; this bounds the covered-cell work per sheet. */
const MERGE_WORK_BUDGET = 1_000_000;

export function indexMerges(sheet: SpreadsheetSheet, cols: number): MergeIndex {
  const heads: MergeIndex['heads'] = new Map();
  const covered = new Set<string>();
  const tallRows = new Set<number>();
  const tall: MergeIndex['tall'] = [];
  let work = 0;
  for (const m of sheet.merges ?? []) {
    if (m.c >= cols || m.r >= sheet.rows.length) continue;
    const colSpan = Math.min(m.colSpan, cols - m.c);
    const rowSpan = Math.min(m.rowSpan, sheet.rows.length - m.r);
    if (colSpan === 1 && rowSpan === 1) continue;
    work += colSpan * rowSpan;
    if (work > MERGE_WORK_BUDGET) break;
    heads.set(`${m.r}:${m.c}`, { rowSpan, colSpan });
    if (rowSpan > 1) {
      tallRows.add(m.r);
      tall.push({ r: m.r, rowSpan });
    }
    for (let r = m.r; r < m.r + rowSpan; r += 1) {
      for (let c = m.c; c < m.c + colSpan; c += 1) {
        if (r !== m.r || c !== m.c) covered.add(`${r}:${c}`);
      }
    }
  }
  return { heads, covered, tallRows, tall };
}

export interface Match {
  r: number;
  c: number;
}

/** Every cell holding `needle` (lowercase), in reading order, across every column. */
export function findMatches(sheet: SpreadsheetSheet, needle: string): Match[] {
  if (!needle) return [];
  const out: Match[] = [];
  sheet.rows.forEach((row, r) => {
    row.forEach((value, c) => {
      if (cellText(value).toLowerCase().includes(needle)) out.push({ r, c });
    });
  });
  return out;
}
