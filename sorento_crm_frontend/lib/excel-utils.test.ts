/**
 * Tests for the xlsm sheet-selection rule used by TemplateUploadDialog imports
 * (docs/plans/PLAN-stock-list-xlsm-macro-upload.md).
 *
 * `.xlsx`/`.xls` keep first-sheet behavior; `.xlsm` prefers the "Template"
 * sheet, accepts a single-sheet workbook, and rejects multi-sheet workbooks
 * without a Template sheet - mirroring the backend 422.
 */
import { describe, expect, it } from 'vitest';

import { parseExcelFile, resolveImportSheetName, resolveNamedSheet } from './excel-utils';

describe('resolveImportSheetName', () => {
  it('keeps first-sheet behavior for xlsx', () => {
    expect(resolveImportSheetName('stock.xlsx', ['Active Loc', 'Template'])).toBe('Active Loc');
  });

  it('keeps first-sheet behavior for xls', () => {
    expect(resolveImportSheetName('stock.xls', ['Sheet1', 'Sheet2'])).toBe('Sheet1');
  });

  it('prefers the Template sheet for xlsm (case-insensitive)', () => {
    expect(
      resolveImportSheetName('stock balance - Macro Version.xlsm', [
        'Active Loc',
        'Master',
        'Template',
      ]),
    ).toBe('Template');
    expect(resolveImportSheetName('stock.XLSM', ['Master', 'template'])).toBe('template');
  });

  it('accepts a single-sheet xlsm without a Template sheet', () => {
    expect(resolveImportSheetName('single.xlsm', ['Sheet1'])).toBe('Sheet1');
  });

  it('throws for multi-sheet xlsm without a Template sheet', () => {
    expect(() => resolveImportSheetName('bad.xlsm', ['Active Loc', 'Master'])).toThrowError(
      /Template/,
    );
  });
});

describe('parseExcelFile (xlsm)', () => {
  async function buildWorkbookFile(filename: string, sheets: Record<string, unknown[][]>) {
    const xlsx = await import('xlsx');
    const wb = xlsx.utils.book_new();
    for (const [name, rows] of Object.entries(sheets)) {
      xlsx.utils.book_append_sheet(wb, xlsx.utils.aoa_to_sheet(rows), name);
    }
    const out = xlsx.write(wb, { type: 'array', bookType: 'xlsx' });
    return new File([out], filename, { type: 'application/octet-stream' });
  }

  it('parses the Template sheet of a macro workbook, not the first sheet', async () => {
    const file = await buildWorkbookFile('stock balance - Macro Version.xlsm', {
      'Active Loc': [['Loc'], ['BRW']],
      Master: [['ignored'], ['x']],
      Template: [
        ['Item Code', 'Item Description', 'Location', 'On Hand Qty'],
        ['ABC-123', 'Widget', 'BRW', 10],
      ],
    });

    const rows = await parseExcelFile(file);
    expect(rows).toEqual([
      { 'Item Code': 'ABC-123', 'Item Description': 'Widget', Location: 'BRW', 'On Hand Qty': 10 },
    ]);
  });

  it('rejects a multi-sheet macro workbook without a Template sheet', async () => {
    const file = await buildWorkbookFile('bad.xlsm', {
      'Active Loc': [['Loc'], ['BRW']],
      Master: [['ignored'], ['x']],
    });

    await expect(parseExcelFile(file)).rejects.toThrowError(/Template/);
  });

  it('keeps first-sheet parsing for xlsx', async () => {
    const file = await buildWorkbookFile('plain.xlsx', {
      First: [['A'], [1]],
      Template: [['B'], [2]],
    });

    const rows = await parseExcelFile(file);
    expect(rows).toEqual([{ A: 1 }]);
  });
});

describe('resolveNamedSheet (DO-COMPARE-SIM, AC-CMM-10/11)', () => {
  it('picks the configured sheet case-insensitively, ignoring a Template sheet', () => {
    expect(resolveNamedSheet(['Master', 'Template', 'Transaction Log'], 'master')).toBe('Master');
  });

  it('picks Master from the Order Tracking workbook shape', () => {
    expect(
      resolveNamedSheet(
        ['Config', 'Raw Data', 'Master', 'Sheet1', 'Daily Tracking', 'Overall Tracking'],
        'Master',
      ),
    ).toBe('Master');
  });

  it('throws naming the configured sheet and the sheets found', () => {
    expect(() => resolveNamedSheet(['A', 'B'], 'Master')).toThrowError(
      "Sheet 'Master' not found (found sheets: A, B).",
    );
  });
});

describe('parseExcelFile with { sheetName } (DO-COMPARE-SIM, AC-CMM-10/11)', () => {
  async function buildWorkbookFile(filename: string, sheets: Record<string, unknown[][]>) {
    const xlsx = await import('xlsx');
    const wb = xlsx.utils.book_new();
    for (const [name, rows] of Object.entries(sheets)) {
      xlsx.utils.book_append_sheet(wb, xlsx.utils.aoa_to_sheet(rows), name);
    }
    const out = xlsx.write(wb, { type: 'array', bookType: 'xlsx' });
    return new File([out], filename, { type: 'application/octet-stream' });
  }

  it('reads Master, never Template, from an .xlsm that has both', async () => {
    const file = await buildWorkbookFile('Order Listing.xlsm', {
      Template: [['Item Code'], ['FROM-TEMPLATE']],
      Master: [['Doc No', 'Item Code'], ['DO-1', 'FROM-MASTER']],
    });
    const rows = await parseExcelFile(file, { sheetName: 'Master' });
    expect(rows).toEqual([{ 'Doc No': 'DO-1', 'Item Code': 'FROM-MASTER' }]);
  });

  it('parses a workbook with no Template sheet from the named sheet', async () => {
    const file = await buildWorkbookFile('Order Tracking.xlsm', {
      Config: [['x'], [1]],
      'Raw Data': [['y'], [2]],
      Master: [['Doc. No.', 'Cancel'], ['DO-9', 'F']],
      'Overall Tracking': [['z'], [3]],
    });
    const rows = await parseExcelFile(file, { sheetName: 'master' });
    expect(rows).toEqual([{ 'Doc. No.': 'DO-9', Cancel: 'F' }]);
  });

  it('rejects a workbook without the configured sheet', async () => {
    const file = await buildWorkbookFile('bad.xlsm', { A: [['a'], [1]], B: [['b'], [2]] });
    await expect(parseExcelFile(file, { sheetName: 'Master' })).rejects.toThrowError(
      /Sheet 'Master' not found \(found sheets: A, B\)\./,
    );
  });
});
