/**
 * LINK-XLSX-VIEWER (AC-V1..V6): the additive Excel-style props on SpreadsheetViewer.
 * AC-V7 is the untouched SpreadsheetViewer.test.tsx. Invented data only.
 *
 * Contract choices pinned here (plan section 2.3):
 * - `excelChrome`: role=columnheader cells read A, B, C...; each body row carries its sheet row number.
 * - A merged cell is the gridcell carrying `aria-colspan` / `aria-rowspan` OR an inline
 *   `grid-column` / `grid-row` of `span N`.
 * - Sticky = the `sticky` class or inline `position: sticky` on the row or the cell.
 * - `searchMode="highlight"` shows a search box (role searchbox) with no `searchColumns` needed.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SpreadsheetViewer, type SpreadsheetWorkbook } from './SpreadsheetViewer';

const ROWS = [
  ['Code', 'Description', 'Qty'],
  ['A-1', 'Basin white', '40.00'],
  ['B-2', 'Tap chrome', '12.50'],
  ['C-3', 'Basin mixer', '7.00'],
  ['D-4', 'Shower rail', '3.00'],
  ['TOTAL', '', '62.50'],
];

function wb(extra: Record<string, unknown> = {}): SpreadsheetWorkbook {
  return {
    sheets: [
      {
        title: 'Packing List',
        columns: [],
        rows: ROWS,
        excelChrome: true,
        ...extra,
      } as never,
      { title: 'Notes', columns: [], rows: [['Other sheet cell']], excelChrome: true } as never,
    ],
  };
}

const cellOf = (text: string) => screen.getByText(text).closest('[role="gridcell"]') as HTMLElement;
const rowOf = (text: string) => screen.getByText(text).closest('[role="row"]') as HTMLElement;
const isSticky = (...els: HTMLElement[]) =>
  els.some((el) => el.className.includes('sticky') || el.style.position === 'sticky');

describe('AC-V1 excelChrome', () => {
  it('shows column letters and the sheet row number on every row', () => {
    render(<SpreadsheetViewer workbook={wb()} />);
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual(expect.arrayContaining(['A', 'B', 'C']));
    expect(heads).not.toContain('Code');
    expect(within(rowOf('A-1')).getByText('2')).toBeInTheDocument();
    expect(within(rowOf('TOTAL')).getByText('6')).toBeInTheDocument();
  });
});

describe('AC-V2 widths, alignment, bold, fill', () => {
  it('applies columnWidths, right-aligns numbers, draws bold and fill', () => {
    const cells = ROWS.map((r) => r.map(() => null as unknown));
    cells[0][0] = { bold: true, fill: '#ffff00' };
    const { container } = render(
      <SpreadsheetViewer workbook={wb({ columnWidths: [211, 133, 97], cells })} />,
    );
    const html = container.innerHTML;
    expect(html).toContain('211px');
    expect(html).toContain('133px');
    expect(html).toContain('97px');
    const qty = cellOf('40.00');
    expect(qty.className.includes('text-end') || qty.style.textAlign === 'right').toBe(true);
    const head = cellOf('Code');
    expect(head.className.match(/font-(bold|semibold)/) || head.style.fontWeight).toBeTruthy();
    expect(head.style.backgroundColor).not.toBe('');
  });
});

describe('AC-V3 pinned rows and frozen columns', () => {
  it('keeps a pinned row sticky from its first render (row 0), outside any frozen column', () => {
    render(<SpreadsheetViewer workbook={wb({ pinnedRows: [0], frozenCols: 0 })} />);
    // "Description" is column B: not frozen, so only the pinned row itself can make it sticky.
    expect(isSticky(rowOf('Description'))).toBe(true);
    expect(isSticky(rowOf('Tap chrome'))).toBe(false);
  });

  it('pins a lower row once the grid has scrolled past it, and not before', () => {
    render(<SpreadsheetViewer workbook={wb({ pinnedRows: [3], frozenCols: 0 })} />);
    expect(isSticky(rowOf('Basin mixer'))).toBe(false);
    const scroller = screen.getByTestId('spreadsheet-scroller');
    Object.defineProperty(scroller, 'scrollTop', { configurable: true, value: 5000 });
    act(() => {
      fireEvent.scroll(scroller);
    });
    expect(isSticky(rowOf('Basin mixer'))).toBe(true);
    expect(isSticky(rowOf('Tap chrome'))).toBe(false);
  });

  it('keeps frozen columns sticky at the left', () => {
    render(<SpreadsheetViewer workbook={wb({ frozenCols: 1 })} />);
    expect(isSticky(cellOf('A-1'))).toBe(true);
    expect(isSticky(cellOf('Basin white'))).toBe(false);
  });
});

describe('AC-V4 merges', () => {
  it('spans a merged cell visually across its columns and rows', () => {
    render(
      <SpreadsheetViewer workbook={wb({ merges: [{ r: 1, c: 1, rowSpan: 2, colSpan: 2 }] })} />,
    );
    const merged = cellOf('Basin white');
    expect(merged.style.gridColumn).toMatch(/span 2/);
    expect(merged.style.gridRow).toMatch(/span 2/);
  });

  it('a vertical merge does not shift the cells to its right in the covered row', () => {
    const rows = [
      ['h1', 'h2', 'h3', 'h4', 'h5'],
      ['a1', 'b1', 'merged', 'd1', 'e1'],
      ['a2', 'b2', '', 'Dval', 'Eval'],
    ];
    render(
      <SpreadsheetViewer workbook={wb({ rows, merges: [{ r: 1, c: 2, rowSpan: 2, colSpan: 1 }] })} />,
    );
    const row = rowOf('Dval');
    const d = cellOf('Dval');
    const explicit = parseInt(d.style.gridColumn, 10);
    // Track 1 is the row-number column, so sheet column D (4th) sits in track 5.
    const track = Number.isNaN(explicit) ? Array.from(row.children).indexOf(d) + 1 : explicit;
    expect(track).toBe(5);
  });
});

describe('zero-width (hidden) columns', () => {
  it('renders no text for a column of width 0', () => {
    render(<SpreadsheetViewer workbook={wb({ columnWidths: [100, 0, 100] })} />);
    expect(screen.queryByText('Basin white')).not.toBeInTheDocument();
    expect(screen.getByText('A-1')).toBeInTheDocument();
    expect(screen.getByText('40.00')).toBeInTheDocument();
  });
});

describe('AC-V5 highlight search', () => {
  it('marks every match, steps through them, hides no row, and clears', async () => {
    const { container } = render(<SpreadsheetViewer workbook={wb()} searchMode="highlight" />);
    const rowsBefore = screen.getAllByRole('row').length;
    await userEvent.type(screen.getByRole('searchbox'), 'basin');
    expect(container.querySelectorAll('mark')).toHaveLength(2);
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next match' }));
    expect(screen.getByText('2 of 2')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Previous match' }));
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(rowsBefore);
    await userEvent.clear(screen.getByRole('searchbox'));
    expect(container.querySelectorAll('mark')).toHaveLength(0);
    expect(screen.queryByText(/ of 2/)).not.toBeInTheDocument();
  });
});

describe('AC-V6 tabs at the bottom', () => {
  it('puts the tab strip after the grid and switches sheet', async () => {
    render(<SpreadsheetViewer workbook={wb()} tabsPosition="bottom" />);
    const tabs = screen.getByRole('tablist');
    const grid = screen.getByRole('grid');
    expect(grid.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await userEvent.click(screen.getByRole('tab', { name: 'Notes' }));
    expect(screen.getByText('Other sheet cell')).toBeInTheDocument();
    expect(screen.queryByText('Basin white')).not.toBeInTheDocument();
  });
});
