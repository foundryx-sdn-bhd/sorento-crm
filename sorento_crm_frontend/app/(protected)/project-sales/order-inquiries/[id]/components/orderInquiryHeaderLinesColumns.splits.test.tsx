/**
 * OI-LINE-SPLIT (UAC D3): the order inquiries Lines tab shows a split line as "Split N" and
 * its expanded content lists the splits read-only: Qty, Date (dd/mm/yyyy), Change, and the
 * line number it sits on (never an id). A sibling file to
 * `orderInquiryHeaderLinesColumns.test.tsx`, so that suite is untouched.
 *
 * The chip may live in any column; every cell is rendered and the text pooled.
 */
import React from 'react';
import { renderHook, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('../../../_shared/components/OrderInquiryStockGrid', () => ({
  OrderInquiryStockGrid: () => <div data-testid="stock-grid" />,
}));
vi.mock('@/app/(protected)/project-sales/_shared/components/SoLineAttachmentsButton', () => ({
  SoLineAttachmentsButton: () => null,
}));

import { useOrderInquiryHeaderLinesColumns } from './orderInquiryHeaderLinesColumns';
import type { OrderInquiryWorklistRow } from '../../../_shared/types/orderInquiry.types';

const SPLITS = [
  { id: 's1', qty: '4', delivery_date: '2026-11-02', is_cancel: false, change: 'REMAIN',
    sits_on_line_id: 'l10', sits_on_line_no: 10, remark: '' },
  { id: 's2', qty: '4', delivery_date: '2026-11-09', is_cancel: false, change: 'DELAY',
    sits_on_line_id: 'l20', sits_on_line_no: 20, remark: 'site not ready' },
  { id: 's3', qty: '2', delivery_date: null, is_cancel: true, change: 'CANCEL BALANCE',
    sits_on_line_id: 'l10', sits_on_line_no: 10, remark: '' },
];

function linesRow(overrides: Record<string, unknown> = {}): OrderInquiryWorklistRow {
  return {
    id: 'row-1',
    verb: 'ORDER',
    state: 'raised',
    qty: '10',
    item_code: 'CSK14B',
    product_id: 'p1',
    linked_qty: '0',
    bundled_qty: '0',
    splits: [],
    ...overrides,
  } as unknown as OrderInquiryWorklistRow;
}

function cellsText(row: OrderInquiryWorklistRow): string {
  const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
  let pooled = '';
  for (const column of result.current as unknown as Array<{
    cell?: (ctx: unknown) => React.ReactNode;
  }>) {
    if (typeof column.cell !== 'function') continue;
    try {
      const ctx = {
        row: { original: row, getIsExpanded: () => false, toggleExpanded() {} },
        getValue: () => undefined,
        column: {},
        table: {},
      };
      const { container, unmount } = render(<>{column.cell(ctx)}</>);
      pooled += ` ${container.textContent ?? ''}`;
      unmount();
    } catch {
      /* a cell that needs a fuller table context is not the one carrying the chip */
    }
  }
  return pooled;
}

function expandedFor(row: OrderInquiryWorklistRow) {
  const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
  const expand = (result.current as unknown as Array<{
    id?: string;
    meta?: { expandedContent?: (line: unknown) => React.ReactNode };
  }>).find((c) => typeof c.meta?.expandedContent === 'function');
  expect(expand).toBeDefined();
  return expand!.meta!.expandedContent!(row);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('OI Lines tab: splits (UAC D3)', () => {
  it('a split line carries a "Split 3" chip', () => {
    expect(cellsText(linesRow({ splits: SPLITS }))).toContain('Split 3');
  });

  it('an unsplit line carries no Split chip', () => {
    expect(cellsText(linesRow({ splits: [] }))).not.toMatch(/Split \d/);
  });

  it('the expanded content lists each split: qty, dd/mm/yyyy date, change word, and the line it sits on', () => {
    render(<>{expandedFor(linesRow({ splits: SPLITS }))}</>);
    const text = document.body.textContent ?? '';
    expect(text).toContain('02/11/2026');
    expect(text).toContain('09/11/2026');
    expect(text).toContain('REMAIN');
    expect(text).toContain('DELAY');
    expect(text).toContain('CANCEL BALANCE');
    expect(text).toMatch(/line 20/i);
    expect(text).toMatch(/line 10/i);
    expect(text).toContain('site not ready');
    expect(text).not.toMatch(/\bl10\b|\bl20\b/); // no ids on screen
    expect(text).not.toMatch(/tranche/i);
  });

  it('the expanded content of an unsplit line lists no splits', () => {
    render(<>{expandedFor(linesRow({ splits: [] }))}</>);
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/REMAIN|DELAY|CANCEL BALANCE/);
    expect(text).not.toMatch(/line \d+/i);
  });
});
