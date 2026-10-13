/** LINK-XLSX-VIEWER: `fill` lets the grid take the parent's height instead of the --grid-max-h cap. */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SpreadsheetViewer } from './SpreadsheetViewer';

const wb = { sheets: [{ title: 'S', columns: [], rows: [['a']], excelChrome: true }] };

describe('SpreadsheetViewer fill', () => {
  it('keeps the height cap by default and drops it with fill', () => {
    const { rerender } = render(<SpreadsheetViewer workbook={wb} />);
    expect(screen.getByTestId('spreadsheet-scroller').className).toContain('max-h-');
    rerender(<SpreadsheetViewer workbook={wb} fill />);
    const scroller = screen.getByTestId('spreadsheet-scroller');
    expect(scroller.className).not.toContain('max-h-');
    expect(scroller.className).toContain('flex-1');
  });
});
