/**
 * STOCK-DEBT-PROJECTION (owner, 12 Oct 2026): the "Projected balance" switch. Off by
 * default; on, the list and the drill are asked for `projected`, a chip names it, and the
 * export follows it. The drill states how the projected figure is made up.
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
const getStockDebtCell = vi.fn();
const exportStockDebt = vi.fn();

vi.mock('../services/stockDebtService', () => ({
  getStockDebtList: (...args: unknown[]) => getStockDebtList(...args),
  getStockDebtCell: (...args: unknown[]) => getStockDebtCell(...args),
  exportStockDebt: (...args: unknown[]) => exportStockDebt(...args),
  previewStockDebtExport: () => ({ rows: 1, sheets: 1 }),
}));

import { StockDebtClient } from './StockDebtClient';

function envelope(): StockDebtListResponse {
  const row = {
    product_id: 'p1',
    product_code: 'SRTWT2215',
    product_name: null,
    months: [{ key: '2026-12', balance: -1436, tone: 'red' }],
    tba: 0,
    undated: 0,
    unlocated: 0,
    supplier_id: null,
    supplier_name: null,
    category_code: null,
    total: -1436,
    pool_on_hand: 191,
    pool_po_qty: 5000,
    pool_incoming_qty: 0,
    dealer_outstanding: -135,
  };
  return {
    data: [row as unknown as StockDebtRow],
    pagination: { total: 1, page: 1, limit: 25 },
    months: ['2026-12'],
    tba_month: '2029-01',
    groups: [],
    locations: [],
    totals: {
      months: { '2026-12': -1436 },
      tba: 0,
      undated: 0,
      unlocated: 0,
      total: -1436,
      pool_on_hand: 191,
      pool_po_qty: 5000,
      pool_incoming_qty: 0,
      dealer_outstanding: -135,
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

async function openFilters() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Filters' }), { button: 0 });
  await screen.findByText('Book');
}

async function switchOn() {
  await openFilters();
  const toggle = screen.getByRole('switch', { name: 'Projected balance' });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(getStockDebtList).toHaveBeenLastCalledWith(expect.objectContaining({ projected: true })),
  );
  fireEvent.keyDown(document.body, { key: 'Escape' });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  getStockDebtList.mockResolvedValue(envelope());
  getStockDebtCell.mockResolvedValue({
    demand: [],
    supply: [],
    demand_total_qty: 0,
    supply_total_qty: 0,
    projection: {
      carried_in: 0,
      month_balance: -377,
      reserve_supply: 0,
      reserve_sources: [],
      projected: -377,
      carried_out: 0,
    },
  });
  exportStockDebt.mockResolvedValue({ id: 'd1' });
});

afterEach(() => vi.restoreAllMocks());

describe('StockDebtClient projected balance', () => {
  it('is off by default and asks for the month view', async () => {
    renderBoard();
    await screen.findByText('SRTWT2215');
    expect(getStockDebtList).toHaveBeenLastCalledWith(
      expect.objectContaining({ projected: false }),
    );
    expect(screen.queryByText('Projected balance')).toBeNull();
  });

  it('asks for the projection and shows a chip that turns it off', async () => {
    renderBoard();
    await screen.findByText('SRTWT2215');
    await switchOn();

    const chip = await screen.findByText('Projected balance');
    expect(chip).toBeInTheDocument();
    const clear = screen.getByRole('button', { name: /Projected balance/i });
    fireEvent.click(clear);
    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({ projected: false }),
      ),
    );
  });

  it('the export follows the switch', async () => {
    renderBoard();
    await screen.findByText('SRTWT2215');
    await switchOn();

    fireEvent.click(screen.getByRole('button', { name: 'Export' }));
    const buttons = await screen.findAllByRole('button', { name: 'Export' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(exportStockDebt).toHaveBeenCalled());
    expect(exportStockDebt.mock.calls[0][0]).toMatchObject({ projected: true });
  });

  it('the drill is asked for the projection and states how it is made up', async () => {
    renderBoard();
    await screen.findByText('SRTWT2215');
    await switchOn();

    const cell = await screen.findByRole('button', { name: /SRTWT2215, Dec 26/ });
    fireEvent.click(cell);
    const line = await screen.findByTestId('stock-debt-projection-line');
    expect(getStockDebtCell).toHaveBeenCalledWith(
      'p1', '2026-12', '', undefined, 'all', true,
    );
    expect(line.textContent).toBe(
      'Project bins -377 · Projected -377',
    );
  });

  it('the drill names reserve supply, the carry in and the carry forward when they are not zero', async () => {
    getStockDebtCell.mockResolvedValue({
      demand: [],
      supply: [],
      demand_total_qty: 0,
      supply_total_qty: 0,
      projection: {
        carried_in: 10,
        month_balance: -38,
        reserve_supply: 5000,
        reserve_sources: [{ label: 'HQ reserve PO', qty: 5000 }],
        projected: 4972,
        carried_out: 4972,
      },
    });
    renderBoard();
    await screen.findByText('SRTWT2215');
    await switchOn();

    fireEvent.click(await screen.findByRole('button', { name: /SRTWT2215, Dec 26/ }));
    const line = await screen.findByTestId('stock-debt-projection-line');
    expect(line.textContent).toBe(
      'Project bins -38 · Carried in +10 · Reserve supply 5,000 (HQ reserve PO 5,000) applied to this month · Projected +4,972 · Carried forward +4,972',
    );
  });
});
