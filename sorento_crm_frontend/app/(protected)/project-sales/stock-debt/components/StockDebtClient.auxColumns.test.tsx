/**
 * STOCKDEBT-AUX-COLS (AC-1, AC-8, AC-9, AC-10, AC-12 on the screen side): four auxiliary
 * columns between Product and the first month, a Switch in the filter popover, a per-browser
 * persisted choice, footer sums, and `show_aux` on the export request.
 *
 * WRITTEN BEFORE THE IMPLEMENTATION: none of the columns, the Switch, the storage key or the
 * `showAux` export param exist yet.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StockDebtListResponse, StockDebtRow } from '../types/stockDebt.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/project-sales/stock-debt',
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn().mockResolvedValue({ listing_key: 'x', config: null }),
  upsertUserListColumnConfig: vi.fn(),
  resetUserListColumnConfig: vi.fn(),
}));
// The toolbar's <SavedViewsMenu> has its own tests (StockDebtClient.savedViews.test.tsx,
// components/list/SavedViewsMenu.test.tsx); stubbed here so its session and per-user
// default reads stay out of what this file asserts about the board.
vi.mock('@/components/list/SavedViewsMenu', () => ({ SavedViewsMenu: () => null }));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const getStockDebtList = vi.fn();
const exportStockDebt = vi.fn();

vi.mock('../services/stockDebtService', () => ({
  getStockDebtList: (...args: unknown[]) => getStockDebtList(...args),
  getStockDebtCell: vi.fn().mockResolvedValue({ demand: [], supply: [] }),
  exportStockDebt: (...args: unknown[]) => exportStockDebt(...args),
  previewStockDebtExport: () => ({ rows: 1, sheets: 1 }),
}));

import { StockDebtClient } from './StockDebtClient';

const AUX_HEADERS = ['BRW On Hand Qty', 'BRW PO Qty', 'BRW Incoming Qty', 'Dealer O/standing Qty'];
const KEY = 'stock-debt.show-aux';

function envelope(): StockDebtListResponse {
  const r = {
    product_id: 'p1',
    product_code: 'SRTWB242',
    product_name: 'Sorento basin 242',
    months: [{ key: '2026-08', balance: 55, tone: 'green' }],
    tba: -100,
    undated: 0,
    unlocated: 0,
    supplier_id: null,
    supplier_name: null,
    category_code: null,
    total: -45,
    pool_on_hand: 40,
    pool_po_qty: 30,
    pool_incoming_qty: 60,
    dealer_outstanding: -47,
  };
  return {
    data: [r as unknown as StockDebtRow],
    pagination: { total: 1, page: 1, limit: 25 },
    months: ['2026-08'],
    tba_month: '2030-01',
    groups: ['BB'],
    totals: {
      months: { '2026-08': 55 },
      tba: -100,
      undated: 0,
      unlocated: 0,
      total: -45,
      pool_on_hand: 142,
      pool_po_qty: 134,
      pool_incoming_qty: 168,
      dealer_outstanding: -147,
    },
    suppliers: [],
    sheet_counts: { supplier: 0, category: 0, supplier_category: 0 },
  } as unknown as StockDebtListResponse;
}

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <StockDebtClient />
    </QueryClientProvider>,
  );
}

const headers = () => screen.getAllByRole('columnheader').map((c) => c.textContent?.trim());

async function openFilters() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Filters' }), { button: 0 });
  await screen.findByText('Book');
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  getStockDebtList.mockResolvedValue(envelope());
  exportStockDebt.mockResolvedValue({ id: 'd1' });
});

afterEach(() => vi.restoreAllMocks());

describe('StockDebtClient auxiliary columns', () => {
  it('shows the four columns, in order, between Product and the first month by default (AC-1, AC-8)', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');

    expect(headers()).toEqual(['Product', ...AUX_HEADERS, 'Aug 26', 'TBA', 'Total']);
  });

  /** The text of the cells under the four aux headers, found by header column index. */
  function auxCells(row: Element | null | undefined): string[] {
    const idx = AUX_HEADERS.map((h) =>
      screen.getAllByRole('columnheader').findIndex((c) => c.textContent?.trim() === h),
    );
    const cells = Array.from(row?.querySelectorAll('td, th') ?? []);
    return idx.map((i) => (i < 0 ? '<no column>' : (cells[i]?.textContent ?? '').trim()));
  }

  it('prints the row values per aux cell, the dealer figure negative (AC-1, AC-5b)', async () => {
    renderBoard();
    const product = await screen.findByText('SRTWB242');

    expect(auxCells(product.closest('tr'))).toEqual(['40', '30', '60', '-47']);
  });

  it('prints aux cells and footers as plain quantities: separators, no leading plus (AC-13)', async () => {
    const env = envelope();
    (env.data[0] as unknown as Record<string, number>).pool_on_hand = 1425;
    (env.totals as unknown as Record<string, number>).pool_po_qty = 1234;
    getStockDebtList.mockResolvedValue(env);
    const { container } = renderBoard();
    const product = await screen.findByText('SRTWB242');

    expect(auxCells(product.closest('tr'))).toEqual(['1,425', '30', '60', '-47']);
    expect(auxCells(container.querySelector('tfoot tr'))).toEqual([
      '142',
      '1,234',
      '168',
      '-147',
    ]);
  });

  it('puts the totals of all four in the footer (AC-10)', async () => {
    const { container } = renderBoard();
    await screen.findByText('SRTWB242');
    const footer = container.querySelector('tfoot')?.textContent ?? '';

    expect(footer).toContain('142');
    expect(footer).toContain('134');
    expect(footer).toContain('168');
    expect(footer).toMatch(/-147/);
  });

  it('hides the four columns through the "Show stock and PO columns" switch, without a filter chip (AC-8, AC-9)', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    const toggle = screen.getByRole('switch', { name: 'Show stock and PO columns' });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);

    await waitFor(() => expect(headers()).toEqual(['Product', 'Aug 26', 'TBA', 'Total']));
    // Hiding columns is not a filter: no chip for it.
    expect(screen.queryByText(/Show stock and PO columns/i, { selector: 'button, [data-slot="badge"]' }))
      .toBeNull();
    expect(screen.queryByRole('button', { name: /Clear .*stock and PO/i })).toBeNull();
  });

  it('remembers the choice per browser and restores it on mount (AC-9)', async () => {
    const first = renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    fireEvent.click(screen.getByRole('switch', { name: 'Show stock and PO columns' }));
    await waitFor(() => expect(headers()).toEqual(['Product', 'Aug 26', 'TBA', 'Total']));
    expect(window.localStorage.getItem(KEY)).not.toBeNull();
    first.unmount();

    renderBoard();
    await screen.findByText('SRTWB242');
    await waitFor(() => expect(headers()).toEqual(['Product', 'Aug 26', 'TBA', 'Total']));
  });

  it('still renders, columns on, when localStorage throws (AC-9)', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    renderBoard();
    await screen.findByText('SRTWB242');

    expect(headers()).toEqual(['Product', ...AUX_HEADERS, 'Aug 26', 'TBA', 'Total']);
    await openFilters();
    expect(() =>
      fireEvent.click(screen.getByRole('switch', { name: 'Show stock and PO columns' })),
    ).not.toThrow();
  });

  async function runExport() {
    fireEvent.click(screen.getByRole('button', { name: 'Export' }));
    const buttons = await screen.findAllByRole('button', { name: 'Export' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(exportStockDebt).toHaveBeenCalled());
    return exportStockDebt.mock.calls[0][0] as Record<string, unknown>;
  }

  it('export sends showAux true while the switch is on (AC-12)', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');

    expect(await runExport()).toMatchObject({ showAux: true });
  });

  it('export sends showAux false once the switch is off (AC-12)', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    fireEvent.click(screen.getByRole('switch', { name: 'Show stock and PO columns' }));
    await waitFor(() => expect(headers()).toEqual(['Product', 'Aug 26', 'TBA', 'Total']));
    fireEvent.keyDown(document.body, { key: 'Escape' });

    expect(await runExport()).toMatchObject({ showAux: false });
  });
});
