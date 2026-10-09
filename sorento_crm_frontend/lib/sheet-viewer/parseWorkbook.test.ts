// @vitest-environment node
/**
 * LINK-XLSX-VIEWER parser (AC-P1..P11). Every fixture is generated here from invented data.
 * Red until `lib/sheet-viewer/parseWorkbook.ts` exists.
 */
import ExcelJS from 'exceljs';
import * as XLSX from 'xlsx';
import { describe, expect, it } from 'vitest';
import { parseWorkbook } from '@/lib/sheet-viewer/parseWorkbook';

async function toBytes(wb: ExcelJS.Workbook): Promise<ArrayBuffer> {
  const buf = (await wb.xlsx.writeBuffer()) as unknown as Uint8Array;
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
}

const QTY = '#,##0.00';

/** Structure of a packing list: header block A1:B4, bold header row 7, 16 lines, bold TOTAL row. */
async function packingList(): Promise<ArrayBuffer> {
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet('Packing List');
  ws.getColumn(1).width = 20;
  ws.getCell('A1').value = 'Date';
  ws.getCell('B1').value = new Date(Date.UTC(2026, 8, 2));
  ws.getCell('B1').numFmt = 'mm-dd-yy';
  ws.getCell('A2').value = 'Invoice';
  ws.getCell('B2').value = 'INV-0001';
  ws.getCell('A3').value = 'Consignee';
  ws.getCell('B3').value = 'Example Trading Sdn Bhd';
  ws.getCell('A4').value = 'Port';
  ws.getCell('B4').value = 'Port Example';
  ['No', 'Code', 'Description', 'Unit', 'Qty', 'Price', 'Amount'].forEach((h, i) => {
    const cell = ws.getCell(7, i + 1);
    cell.value = h;
    cell.font = { bold: true };
  });
  for (let i = 0; i < 16; i += 1) {
    const r = 8 + i;
    ws.getCell(r, 1).value = i + 1;
    ws.getCell(r, 2).value = `ITEM-${i + 1}`;
    ws.getCell(r, 3).value = `Basin sample ${i + 1}`;
    ws.getCell(r, 4).value = 'PCS';
    ws.getCell(r, 5).value = 40 + i;
    ws.getCell(r, 5).numFmt = QTY;
    ws.getCell(r, 6).value = 10;
    ws.getCell(r, 6).numFmt = QTY;
    ws.getCell(r, 7).value = (40 + i) * 10;
    ws.getCell(r, 7).numFmt = QTY;
  }
  ws.getCell('A24').value = 'TOTAL';
  ws.getCell('E24').value = { formula: 'SUM(E8:E23)', result: 12345.5 } as ExcelJS.CellFormulaValue;
  ws.getCell('G24').value = { formula: 'SUM(G8:G23)', result: 99999 } as ExcelJS.CellFormulaValue;
  for (const addr of ['A24', 'E24', 'G24']) {
    ws.getCell(addr).font = { bold: true };
    if (addr !== 'A24') ws.getCell(addr).numFmt = QTY;
  }
  return toBytes(wb);
}

async function single(fill: (ws: ExcelJS.Worksheet) => void, name = 'S1') {
  const wb = new ExcelJS.Workbook();
  fill(wb.addWorksheet(name));
  return (await parseWorkbook(await toBytes(wb), 'f.xlsx')).sheets[0];
}

describe('AC-P1 packing list', () => {
  it('reads formatted values and the cached TOTAL, never a recomputed sum', async () => {
    const wbk = await parseWorkbook(await packingList(), 'packing.xlsx');
    expect(wbk.sheets).toHaveLength(1);
    const s = wbk.sheets[0];
    expect(s.title).toBe('Packing List');
    expect(s.excelChrome).toBe(true);
    expect(s.rows[7][4]).toBe('40.00');
    expect(s.rows[0][1]).toBe('09-02-26');
    expect(s.rows[23][0]).toBe('TOTAL');
    expect(s.rows[23][6]).toBe('99,999.00'); // cached 99999, not the true sum
    expect(s.rows[23][4]).toBe('12,345.50');
  });
});

describe('AC-P2 formula without cached result', () => {
  it('reads empty text', async () => {
    const s = await single((ws) => {
      ws.getCell('A1').value = { formula: 'SUM(B1:B2)' } as ExcelJS.CellFormulaValue;
      ws.getCell('B1').value = 1;
    });
    expect(s.rows[0][0]).toBe('');
  });
});

describe('AC-P3 column widths', () => {
  it('maps char width to px by the rule and uses the default for unset columns', async () => {
    const s = await single((ws) => {
      ws.getColumn(1).width = 20;
      ws.getCell('A1').value = 'a';
      ws.getCell('B1').value = 'b';
      ws.getCell('C1').value = 'c';
      ws.getColumn(3).width = 30;
    });
    expect(s.columnWidths?.[0]).toBe(Math.round(20 * 7 + 5));
    expect(s.columnWidths?.[1]).toBe(Math.round(8.43 * 7 + 5));
    expect(s.columnWidths?.[2]).toBe(Math.round(30 * 7 + 5));
  });
});

describe('AC-P4 merges', () => {
  it('come back as 0-based {r, c, rowSpan, colSpan}', async () => {
    const s = await single((ws) => {
      ws.getCell('A1').value = 'x';
      ws.getCell('C9').value = 'wide';
      ws.mergeCells('C9:E10');
    });
    expect(s.merges).toContainEqual({ r: 8, c: 2, rowSpan: 2, colSpan: 3 });
  });
});

describe('AC-P5 cell styles', () => {
  it('carries bold, italic, fill, align, wrap and the [Red] negative colour', async () => {
    const s = await single((ws) => {
      const a = ws.getCell('A1');
      a.value = 'styled';
      a.font = { bold: true, italic: true };
      a.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFFF00' } };
      a.alignment = { horizontal: 'center', wrapText: true };
      const b = ws.getCell('B1');
      b.value = -5;
      b.numFmt = '#,##0.00;[Red]-#,##0.00';
      ws.getCell('C1').value = 'plain';
    });
    const a = s.cells?.[0]?.[0];
    expect(a?.bold).toBe(true);
    expect(a?.italic).toBe(true);
    expect(a?.align).toBe('center');
    expect(a?.wrap).toBe(true);
    expect(String(a?.fill)).toMatch(/ffff00/i);
    expect(s.rows[0][1]).toMatch(/^-5\.00$/);
    expect(String(s.cells?.[0]?.[1]?.color)).toMatch(/red|ff0000/i);
    expect(s.cells?.[0]?.[2]?.bold).toBeFalsy();
  });
});

describe('AC-P6 / AC-P7 pinning', () => {
  it('uses the freeze pane when set', async () => {
    const s = await single((ws) => {
      for (let r = 1; r <= 5; r += 1) for (let c = 1; c <= 3; c += 1) ws.getCell(r, c).value = `${r}-${c}`;
      ws.views = [{ state: 'frozen', xSplit: 1, ySplit: 2 }];
    });
    expect(s.pinnedRows).toEqual([0, 1]);
    expect(s.frozenCols).toBe(1);
  });

  it('pins only the first all-bold row (index 6 for the packing list) without a freeze pane', async () => {
    const s = (await parseWorkbook(await packingList(), 'packing.xlsx')).sheets[0];
    expect(s.pinnedRows).toEqual([6]);
  });

  it('pins nothing when no row is bold', async () => {
    const s = await single((ws) => {
      ws.getCell('A1').value = 'a';
      ws.getCell('B1').value = 'b';
      ws.getCell('A2').value = 'c';
    });
    expect(s.pinnedRows ?? []).toEqual([]);
  });
});

describe('AC-P8 caps', () => {
  it('keeps 50 of 60 sheets and skips hidden ones', async () => {
    const wb = new ExcelJS.Workbook();
    for (let i = 1; i <= 60; i += 1) wb.addWorksheet(`Sheet ${i}`).getCell('A1').value = i;
    wb.getWorksheet('Sheet 2')!.state = 'hidden';
    const out = await parseWorkbook(await toBytes(wb), 'many.xlsx');
    expect(out.sheets).toHaveLength(50);
    expect(out.sheets.map((s) => s.title)).not.toContain('Sheet 2');
  });

  it('keeps 5,000 of 6,000 rows and flags it', async () => {
    const s = await single((ws) => {
      for (let r = 1; r <= 6000; r += 1) ws.getCell(r, 1).value = `r${r}`;
    });
    expect(s.rows).toHaveLength(5000);
    expect(s.truncatedRows).toBe(true);
    expect(s.totalRows).toBe(6000);
  });

  it('keeps 100 of 120 columns and flags it', async () => {
    const s = await single((ws) => {
      for (let c = 1; c <= 120; c += 1) ws.getCell(1, c).value = `c${c}`;
    });
    expect(s.rows[0]).toHaveLength(100);
    expect(s.truncatedCols).toBe(true);
  });
});

describe('AC-P9 rich text, hyperlinks, errors', () => {
  it('joins rich text, gives link text only, and error codes', async () => {
    const s = await single((ws) => {
      ws.getCell('A1').value = { richText: [{ text: 'Hel' }, { text: 'lo', font: { bold: true } }] };
      ws.getCell('B1').value = { text: 'Site', hyperlink: 'https://example.test/secret' };
      ws.getCell('C1').value = { error: '#DIV/0!' } as ExcelJS.CellErrorValue;
    });
    expect(s.rows[0][0]).toBe('Hello');
    expect(s.rows[0][1]).toBe('Site');
    expect(s.rows[0][2]).toBe('#DIV/0!');
    expect(JSON.stringify(s)).not.toContain('example.test');
  });
});

describe('AC-P10 csv and xls', () => {
  it('parses a csv', async () => {
    const bytes = new TextEncoder().encode('Code,Qty\nA-1,5\nB-2,7\n');
    const out = await parseWorkbook(bytes.buffer.slice(0) as ArrayBuffer, 'list.csv');
    expect(out.sheets[0].rows.map((r) => r.slice(0, 2))).toEqual([
      ['Code', 'Qty'],
      ['A-1', '5'],
      ['B-2', '7'],
    ]);
  });

  it('parses an xls', async () => {
    const book = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(book, XLSX.utils.aoa_to_sheet([['Code', 'Qty'], ['A-1', 5]]), 'Old');
    const arr = XLSX.write(book, { bookType: 'biff8', type: 'array' }) as ArrayBuffer | number[];
    const bytes = arr instanceof ArrayBuffer ? arr : new Uint8Array(arr).buffer;
    const out = await parseWorkbook(bytes, 'old.xls');
    expect(out.sheets[0].title).toBe('Old');
    expect(out.sheets[0].rows[1].slice(0, 2)).toEqual(['A-1', '5']);
  });
});

describe('AC-P11 garbage', () => {
  it('rejects', async () => {
    const junk = new Uint8Array(512).map((_, i) => (i * 37) % 251).buffer;
    await expect(parseWorkbook(junk, 'junk.xlsx')).rejects.toBeTruthy();
  });
});

describe('declared ranges (xls / csv)', () => {
  function xlsBytes(ws: XLSX.WorkSheet): ArrayBuffer {
    const book = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(book, ws, 'Old');
    const arr = XLSX.write(book, { bookType: 'biff8', type: 'array' }) as ArrayBuffer | number[];
    return arr instanceof ArrayBuffer ? arr : new Uint8Array(arr).buffer;
  }

  it('a declared A1:IV65536 range with 2 data rows still reads 2 rows, trimmed columns, no truncation', async () => {
    const ws = XLSX.utils.aoa_to_sheet([
      ['Code', 'Qty'],
      ['A-1', 5],
    ]);
    ws['!ref'] = 'A1:IV65536';
    const s = (await parseWorkbook(xlsBytes(ws), 'old.xls')).sheets[0];
    expect(s.rows).toHaveLength(2);
    expect(s.rows[0]).toHaveLength(2);
    expect(s.truncatedRows).toBeFalsy();
    expect(s.truncatedCols).toBeFalsy();
  });

  it('a range starting at B3 keeps sheet row and column positions', async () => {
    const ws: XLSX.WorkSheet = {
      B3: { t: 's', v: 'x' },
      C3: { t: 's', v: 'y' },
      B4: { t: 'n', v: 1 },
      C4: { t: 'n', v: 2 },
      '!ref': 'B3:C4',
    };
    const s = (await parseWorkbook(xlsBytes(ws), 'old.xls')).sheets[0];
    expect(s.rows).toHaveLength(2);
    expect(s.firstRowNumber).toBe(3);
    expect(s.firstColIndex).toBe(1);
  });
});

describe('booleans, merge cap, hidden rows and columns', () => {
  it('shows booleans as TRUE / FALSE', async () => {
    const s = await single((ws) => {
      ws.getCell('A1').value = true;
      ws.getCell('B1').value = false;
    });
    expect(s.rows[0].slice(0, 2)).toEqual(['TRUE', 'FALSE']);
  });

  it('caps merges at 10,000 per sheet', async () => {
    const s = await single((ws) => {
      for (let r = 1; r <= 210; r += 1) {
        for (let p = 0; p < 50; p += 1) {
          ws.getCell(r, 2 * p + 1).value = 'm';
          ws.mergeCells(r, 2 * p + 1, r, 2 * p + 2);
        }
      }
    });
    expect(s.merges).toHaveLength(10000);
  }, 60000);

  it('skips hidden rows and gives hidden columns width 0', async () => {
    const s = await single((ws) => {
      for (let r = 1; r <= 5; r += 1) {
        ws.getCell(r, 1).value = `row${r}`;
        ws.getCell(r, 2).value = `col2-${r}`;
      }
      ws.getRow(3).hidden = true;
      ws.getColumn(2).hidden = true;
    });
    expect(s.rows.map((r) => r[0])).toEqual(['row1', 'row2', 'row4', 'row5']);
    expect(s.columnWidths?.[1]).toBe(0);
  });
});
