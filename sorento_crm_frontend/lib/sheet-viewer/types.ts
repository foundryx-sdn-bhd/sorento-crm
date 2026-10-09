/** The normalised workbook model `SpreadsheetViewer` renders and `parseWorkbook` produces. */

export type SpreadsheetCell = string | number | null;

export interface CellStyle {
  bold?: boolean;
  italic?: boolean;
  align?: 'left' | 'center' | 'right';
  wrap?: boolean;
  /** CSS colour, solid fill. */
  fill?: string;
  /** CSS colour, text. */
  color?: string;
}

export interface SheetMerge {
  r: number;
  c: number;
  rowSpan: number;
  colSpan: number;
}

export interface SpreadsheetSheet {
  title: string;
  columns: string[];
  rows: SpreadsheetCell[][];
  /** Marks a sheet worth noticing in the tab strip (the low stock report's "- Low" sheets). */
  flagged?: boolean;
  /** What an empty sheet says instead of a bare header. */
  emptyText?: string;
  /** Pixel width per column. */
  columnWidths?: number[];
  /** Parallel to `rows`; null where a cell has no style. */
  cells?: (CellStyle | null)[][];
  merges?: SheetMerge[];
  /** Row indexes kept sticky under the header. */
  pinnedRows?: number[];
  /** Leading columns kept sticky on the left. */
  frozenCols?: number;
  /** Column letters as the header and a sticky row-number column; `columns` is then unused. */
  excelChrome?: boolean;
  truncatedRows?: boolean;
  truncatedCols?: boolean;
  totalRows?: number;
}

export interface SpreadsheetWorkbook {
  sheets: SpreadsheetSheet[];
}
