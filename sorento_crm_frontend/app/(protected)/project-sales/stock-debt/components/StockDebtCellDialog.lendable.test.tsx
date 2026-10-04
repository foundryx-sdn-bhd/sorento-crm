/**
 * STOCK-DEBT-LENDABLE (owner, 30 Sep 2026, option B): the cell dialog's two new things.
 *
 * AC-V1 the `order_back` pill ("order back 88"; "short 12 · order back 88" when short
 * outranks, the two halves adding up to the shortfall), AC-V2 the Covered by wording (a lent on-hand entry says whose stock it was,
 * a lending line lists "Lent to SO... (N)" linking the receiving order). The page stays
 * read-only (owner: "this is a dashboard view only"): no Rebalance, no write.
 *
 * Same harness as `StockDebtCellDialog.test.tsx`: the service is mocked, never the
 * component.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  StockDebtCell,
  StockDebtDemandLine,
} from '../types/stockDebt.types';

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

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));

const getStockDebtCell = vi.fn();
vi.mock('../services/stockDebtService', () => ({
  getStockDebtList: vi.fn(),
  getStockDebtCell: (...args: unknown[]) => getStockDebtCell(...args),
}));

import { StockDebtCellDialog } from './StockDebtCellDialog';

const RECEIVER: StockDebtDemandLine = {
  so_number: 'SO396071',
  agent_code: 'LEENA',
  warehouse_code: 'BRW-BB',
  required_date: '2026-09-01',
  open_qty: 32,
  qty_ordered: 32,
  qty_delivered: 0,
  assigned_qty: 32,
  assigned_source: 'On hand BRW-BB',
  short_qty: 0,
  status: 'covered',
  sales_order_id: 'so-396071',
  lent_qty: 0,
  assigned_from: [
    {
      kind: 'on_hand',
      ref: 'On hand BRW-BB (from SO381065)',
      spo_number: null,
      spo_line_number: null,
      qty: 32,
      lent_from_so_number: 'SO381065',
    },
  ],
};

const LENDER: StockDebtDemandLine = {
  so_number: 'SO381065',
  agent_code: 'LEENA',
  warehouse_code: 'BRW-BB',
  required_date: '2027-03-29',
  open_qty: 88,
  qty_ordered: 88,
  qty_delivered: 0,
  assigned_qty: 0,
  assigned_source: null,
  short_qty: 88,
  status: 'order_back',
  sales_order_id: 'so-381065',
  lent_qty: 88,
  assigned_from: [
    {
      kind: 'lent',
      ref: 'Lent to SO396071 (32)',
      qty: 32,
      so_number: 'SO396071',
      sales_order_id: 'so-396071',
    },
    {
      kind: 'lent',
      ref: 'Lent to SO402118 (29)',
      qty: 29,
      so_number: 'SO402118',
      sales_order_id: 'so-402118',
    },
    {
      kind: 'lent',
      ref: 'Lent to SO404890 (27)',
      qty: 27,
      so_number: 'SO404890',
      sales_order_id: 'so-404890',
    },
  ],
};

const SEP_CELL: StockDebtCell = {
  demand: [RECEIVER],
  supply: [],
  demand_total_qty: 32,
  supply_total_qty: 0,
};

const MAR_CELL: StockDebtCell = {
  demand: [LENDER],
  supply: [],
  demand_total_qty: 88,
  supply_total_qty: 0,
};

function renderDialog(cell: StockDebtCell, month = '2026-09', balance = -44) {
  getStockDebtCell.mockResolvedValue(cell);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <StockDebtCellDialog
        productId="p1"
        productCode="SRTSS8710"
        productName="Stainless steel sink 871"
        month={month}
        monthLabel="Sep 26"
        balance={balance}
        book="project"
        onClose={() => {}}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('StockDebtCellDialog, lendable landed pins', () => {
  it('AC-V1: a lending line reads "order back N" in its own pill', async () => {
    renderDialog(MAR_CELL, '2027-03', -88);
    const row = (await screen.findByText('SO381065')).closest(
      'tr',
    ) as HTMLElement;
    const pill = within(row).getByText('order back 88');
    expect(pill.className).toMatch(/violet/);
    expect(within(row).queryByText('short 88')).not.toBeInTheDocument();
  });

  it('AC-V1: short outranks, and the lend is still said', async () => {
    renderDialog(
      {
        ...MAR_CELL,
        demand: [{ ...LENDER, open_qty: 100, short_qty: 100, status: 'short' }],
      },
      '2027-03',
      -100,
    );
    const row = (await screen.findByText('SO381065')).closest(
      'tr',
    ) as HTMLElement;
    // The two halves add up to the 100 the month books: 12 never had cover, 88 was lent.
    expect(within(row).getByText('short 12')).toBeInTheDocument();
    expect(within(row).getByText('order back 88')).toBeInTheDocument();
    expect(within(row).queryByText('short 100')).not.toBeInTheDocument();
  });

  it('AC-V1: the order back figure is what is still owed, never more than the shortfall', async () => {
    // A line that lent 88 and re-covered 86 of it elsewhere is owed 2, not 88.
    renderDialog(
      {
        ...MAR_CELL,
        demand: [
          { ...LENDER, assigned_qty: 86, short_qty: 2, status: 'order_back' },
        ],
      },
      '2027-03',
      -2,
    );
    const row = (await screen.findByText('SO381065')).closest(
      'tr',
    ) as HTMLElement;
    expect(within(row).getByText('order back 2')).toBeInTheDocument();
  });

  it('AC-V2: a receiving line names whose stock it holds', async () => {
    renderDialog(SEP_CELL);
    const row = (await screen.findByText('SO396071')).closest(
      'tr',
    ) as HTMLElement;
    expect(
      within(row).getByText('On hand BRW-BB (from SO381065)'),
    ).toBeInTheDocument();
    expect(within(row).getByText('covered')).toBeInTheDocument();
  });

  it('AC-V2: a lending line lists every receiver, linking the receiving order', async () => {
    renderDialog(MAR_CELL, '2027-03', -88);
    const row = (await screen.findByText('SO381065')).closest(
      'tr',
    ) as HTMLElement;
    const link = within(row).getByRole('link', {
      name: 'Lent to SO396071 (32)',
    });
    expect(link).toHaveAttribute('href', '/scm/sales-orders/so-396071');
    expect(within(row).getByText('Lent to SO402118 (29)')).toBeInTheDocument();
    expect(within(row).getByText('Lent to SO404890 (27)')).toBeInTheDocument();
  });
});
