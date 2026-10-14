/**
 * STOCK-DEBT-SAVED-VIEWS: the Location multi-select (AC-F1..F5) and the saved-views menu
 * wiring (AC-V1..V4) on the Stock Debt board. `SavedViewsMenu` is mocked to capture the
 * props the board hands it; its own behaviour has its own tests.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StockDebtListResponse } from '../types/stockDebt.types';

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

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

type MenuProps = {
  listingKey: string;
  currentViewId: string | null;
  currentConfig: { quick_filters?: Record<string, string> | null };
  onApply: (view: unknown) => void;
};
let menuProps: MenuProps | null = null;
vi.mock('@/components/list/SavedViewsMenu', () => ({
  SavedViewsMenu: (props: MenuProps) => {
    menuProps = props;
    return <div data-testid="saved-views-menu" />;
  },
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

function envelope(total = 1): StockDebtListResponse {
  return {
    data: [
      {
        product_id: 'p1',
        product_code: 'SRTWB242',
        product_name: 'Sorento basin 242',
        months: [{ key: '2026-10', balance: -5, tone: 'red' }],
        tba: 0,
        undated: 0,
        unlocated: 0,
        supplier_id: null,
        supplier_name: null,
        category_code: null,
        pool_on_hand: 0,
        pool_po_qty: 0,
        pool_incoming_qty: 0,
        dealer_outstanding: 0,
        total: -5,
      },
    ],
    pagination: { total, page: 1, limit: 25 },
    months: ['2026-10'],
    tba_month: '2030-01',
    groups: ['BB', 'ED', 'FG'],
    totals: {
      months: { '2026-10': -5 },
      tba: 0,
      undated: 0,
      unlocated: 0,
      total: -5,
      pool_on_hand: 0,
      pool_po_qty: 0,
      pool_incoming_qty: 0,
      dealer_outstanding: 0,
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
  fireEvent.pointerDown(screen.getByRole('button', { name: /^Filters/ }), { button: 0 });
  await screen.findByText('Book');
}

/** The Location multi-select trigger: the one sitting under the "Location" label. */
function locationTrigger(): HTMLElement {
  const label = screen.getByText('Location', { selector: 'label' });
  const trigger = label.parentElement?.querySelector(
    '[data-slot="searchable-multi-select-trigger"]',
  );
  expect(trigger).not.toBeNull();
  return trigger as HTMLElement;
}

function pick(...codes: string[]) {
  fireEvent.click(locationTrigger());
  for (const code of codes) fireEvent.click(screen.getByRole('option', { name: code }));
}

function applyView(quick_filters: Record<string, string>) {
  act(() => {
    menuProps!.onApply({
      id: 'v1',
      name: 'Preset',
      is_shared: false,
      is_default: false,
      owner_name: null,
      view: { filters: null, sort: [], columns: [], column_order: [], quick_filters },
    });
  });
}

const FULL_VIEW = {
  book: 'project',
  groups: 'ED',
  date_from_days: '0',
  date_to_days: '30',
  only_debt: 'true',
  projected: 'false',
  suppliers: '',
  query: '',
};

beforeEach(() => {
  vi.clearAllMocks();
  menuProps = null;
  getStockDebtList.mockResolvedValue(envelope());
  getStockDebtCell.mockResolvedValue({ demand: [], supply: [] });
  exportStockDebt.mockResolvedValue({ id: 'd1' });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('Location filter (AC-F1..F4)', () => {
  it('offers exactly the envelope groups as options', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    fireEvent.click(locationTrigger());
    const options = screen.getAllByRole('option').map((o) => o.textContent?.trim());
    expect(options).toEqual(['BB', 'ED', 'FG']);
  });

  it('one pick reaches the list query and reads "Location: ED"', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    pick('ED');

    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({ groups: ['ED'] }),
      ),
    );
    expect(screen.getByText('Location: ED')).toBeInTheDocument();
  });

  it('two picks read "Locations: BB, ED"', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    pick('BB', 'ED');

    expect(screen.getByText('Locations: BB, ED')).toBeInTheDocument();
    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({ groups: ['BB', 'ED'] }),
      ),
    );
  });

  it('shows no Location chip under Book = Retail', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    pick('ED');
    expect(screen.getByText('Location: ED')).toBeInTheDocument();
    // The Filters panel stays open behind the closed select, so Book is right there.
    fireEvent.keyDown(document.body, { key: 'Escape' });
    if (!screen.queryByRole('radio', { name: 'Retail' })) await openFilters();
    fireEvent.click(screen.getByRole('radio', { name: 'Retail' }));

    expect(screen.queryByText('Location: ED')).not.toBeInTheDocument();
  });
});

describe('Saved views menu (AC-V1..V4)', () => {
  it('renders the menu with the stock debt listing key', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');

    expect(screen.getByTestId('saved-views-menu')).toBeInTheDocument();
    expect(menuProps?.listingKey).toBe('projects.stock_debt.view::stock-debt');
  });

  it('currentConfig reflects the picked book and locations', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    fireEvent.click(screen.getByRole('radio', { name: 'Project' }));
    pick('BB', 'ED');

    await waitFor(() =>
      expect(menuProps?.currentConfig.quick_filters).toMatchObject({
        book: 'project',
        groups: 'BB,ED',
      }),
    );
  });

  it('applying a view sets book, location and a delivery range from today', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 9, 14, 10));
    renderBoard();
    await screen.findByText('SRTWB242');

    applyView(FULL_VIEW);

    expect(await screen.findByText('Book: Project')).toBeInTheDocument();
    expect(screen.getByText('Location: ED')).toBeInTheDocument();
    expect(screen.getByText('Delivery: 14 Oct 26 to 13 Nov 26')).toBeInTheDocument();
    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({
          book: 'project',
          groups: ['ED'],
          dateFrom: '2026-10-14',
          dateTo: '2026-11-13',
          onlyDebt: true,
        }),
      ),
    );
  });

  it('recomputes the range from the day the view is applied', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 9, 20, 10));
    renderBoard();
    await screen.findByText('SRTWB242');

    applyView(FULL_VIEW);

    expect(await screen.findByText('Delivery: 20 Oct 26 to 19 Nov 26')).toBeInTheDocument();
  });

  it('"No view" resets every filter to the screen default', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    applyView(FULL_VIEW);
    expect(await screen.findByText('Book: Project')).toBeInTheDocument();

    act(() => menuProps!.onApply(null));

    await waitFor(() => {
      expect(screen.queryByText('Book: Project')).not.toBeInTheDocument();
      expect(screen.queryByText('Location: ED')).not.toBeInTheDocument();
      expect(screen.queryByText(/^Delivery:/)).not.toBeInTheDocument();
    });
    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({
          book: 'all',
          groups: [],
          dateFrom: '',
          dateTo: '',
          onlyDebt: true,
          query: '',
        }),
      ),
    );
  });
});

describe('Location and saved views reach every consumer (kill-test survivors)', () => {
  it('AC-F2: the cell drill is fetched under the picked locations', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    pick('ED');
    fireEvent.keyDown(document.body, { key: 'Escape' });

    fireEvent.click(
      await screen.findByRole('button', { name: 'SRTWB242, Oct 26, balance -5' }),
    );

    await waitFor(() => expect(getStockDebtCell).toHaveBeenCalled());
    const call = getStockDebtCell.mock.calls.at(-1)!;
    expect(call[0]).toBe('p1');
    expect(call[6]).toEqual(['ED']);
  });

  it('AC-F2: the export is started under the picked locations', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    pick('ED');
    fireEvent.keyDown(document.body, { key: 'Escape' });

    fireEvent.click(screen.getByRole('button', { name: 'Export' }));
    const buttons = await screen.findAllByRole('button', { name: 'Export' });
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => expect(exportStockDebt).toHaveBeenCalled());
    expect(exportStockDebt.mock.calls[0][0]).toMatchObject({ groups: ['ED'] });
  });

  it('AC-F4: under Retail the Filters badge does not count Location', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    pick('ED');
    fireEvent.click(await screen.findByRole('radio', { name: 'Retail' }));

    // Book = Retail is the only active filter; ED is picked but ignored by the backend.
    await screen.findByText('Book: Retail');
    const trigger = screen.getByRole('button', { name: /^Filters/, hidden: true });
    expect(trigger.textContent?.trim()).toBe('Filters1');
  });

  it('AC-V3: applying a view sets search, supplier, only-debt and projected', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');

    applyView({
      query: 'SRTWB',
      suppliers: 's1',
      only_debt: 'false',
      projected: 'true',
      book: 'all',
      groups: '',
      date_from_days: '',
      date_to_days: '',
    });

    expect(await screen.findByRole('textbox', { name: 'Search products' })).toHaveValue('SRTWB');
    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({
          query: 'SRTWB',
          supplierIds: ['s1'],
          onlyDebt: false,
          projected: true,
        }),
      ),
    );
  });

  it('AC-F5: picking a location returns to page 1', async () => {
    getStockDebtList.mockResolvedValue(envelope(60));
    renderBoard();
    await screen.findByText('SRTWB242');

    fireEvent.click(await screen.findByRole('button', { name: 'Go to next page' }));
    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({ pageIndex: 1 }),
      ),
    );

    await openFilters();
    pick('ED');

    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({ groups: ['ED'], pageIndex: 0 }),
      ),
    );
  });
});
