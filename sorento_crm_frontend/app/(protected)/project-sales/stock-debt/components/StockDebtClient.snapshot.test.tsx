/**
 * STOCK-DEBT-SNAPSHOT AC-F1: the board's toolbar gains "Snapshots" (a link to the list) and
 * "Save snapshot" (opens the save modal) beside Export. The save modal receives the SAME
 * filters object Export sends (minus split) and the filtered product count.
 *
 * `SaveSnapshotDialog` has its own suite; it is stubbed here to capture its props.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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
  previewStockDebtExport: () => ({ rows: 550, sheets: 1 }),
}));

const saveDialogProps: Array<Record<string, unknown>> = [];
vi.mock('./SaveSnapshotDialog', () => ({
  SaveSnapshotDialog: (props: Record<string, unknown>) => {
    saveDialogProps.push(props);
    return props.open ? <div role="dialog" aria-label="Save snapshot stub" /> : null;
  },
}));

import { StockDebtClient } from './StockDebtClient';

function envelope() {
  return {
    data: [
      {
        product_id: 'p1',
        product_code: 'SRTWB242',
        product_name: 'Sorento basin 242',
        months: [{ key: '2026-08', balance: -16, tone: 'red' }],
        tba: 0,
        undated: 0,
        unlocated: 0,
        supplier_id: null,
        supplier_name: null,
        category_code: null,
        total: -16,
        pool_on_hand: 0,
        pool_po_qty: 0,
        pool_incoming_qty: 0,
        dealer_outstanding: 0,
      },
    ],
    pagination: { total: 550, page: 1, limit: 25 },
    months: ['2026-08'],
    tba_month: '2030-01',
    groups: [],
    locations: [],
    totals: {
      months: { '2026-08': -16 },
      tba: 0,
      undated: 0,
      unlocated: 0,
      total: -16,
      pool_on_hand: 0,
      pool_po_qty: 0,
      pool_incoming_qty: 0,
      dealer_outstanding: 0,
    },
    suppliers: [],
    sheet_counts: { supplier: 0, category: 0, supplier_category: 0 },
  };
}

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <StockDebtClient />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  saveDialogProps.length = 0;
  window.localStorage.clear();
  getStockDebtList.mockResolvedValue(envelope());
  exportStockDebt.mockResolvedValue({ id: 'd-1', status: 'pending' });
});

describe('AC-F1 toolbar', () => {
  it('has a Snapshots link to the snapshots list', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    const link = screen.getByRole('link', { name: /snapshots/i });
    expect(link).toHaveAttribute('href', '/project-sales/stock-debt/snapshots');
  });

  it('has a Save snapshot button next to Export', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    const save = screen.getByRole('button', { name: /save snapshot/i });
    const exportButton = screen.getByRole('button', { name: /^export$/i });
    expect(save.parentElement).toBe(exportButton.parentElement);
  });

  it('Save snapshot opens the modal; it is closed until pressed', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    expect(screen.queryByRole('dialog', { name: 'Save snapshot stub' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /save snapshot/i }));

    expect(await screen.findByRole('dialog', { name: 'Save snapshot stub' })).toBeInTheDocument();
  });

  it('hands the modal the filtered product count', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    fireEvent.click(screen.getByRole('button', { name: /save snapshot/i }));
    await waitFor(() => expect(saveDialogProps.at(-1)?.open).toBe(true));
    expect(saveDialogProps.at(-1)?.productCount).toBe(550);
  });

  it('hands the modal the board filters, the same object Export sends', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await waitFor(() => expect(saveDialogProps.at(-1)?.productCount).toBe(550));
    const saved = saveDialogProps.at(-1)?.filters as Record<string, unknown>;
    expect(saved).toMatchObject({
      query: '',
      onlyDebt: true,
      book: 'all',
      supplierIds: [],
      dateFrom: '',
    });
    expect(saved).not.toHaveProperty('split');

    // Export sends exactly those fields plus the chosen split.
    fireEvent.click(screen.getByRole('button', { name: /^export$/i }));
    const popover = await screen.findByRole('dialog');
    fireEvent.click(within(popover).getByRole('button', { name: /^export$/i }));
    await waitFor(() => expect(exportStockDebt).toHaveBeenCalledTimes(1));
    expect(exportStockDebt).toHaveBeenCalledWith(expect.objectContaining({ ...saved, split: 'none' }));
  });
});
