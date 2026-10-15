/**
 * STOCK-DEBT-SAVED-VIEWS: the Location multi-select (AC-R7..R10) and the saved-views menu
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

const LOCATIONS = [
  { id: 'w-brw-ib', code: 'BRW-IB', name: 'BRW-IB' },
  { id: 'w-hq', code: 'HQ', name: 'HEADQUARTER' },
  { id: 'w-mwh-ib', code: 'MWH-IB', name: 'MWH-IB' },
  { id: 'w-brw', code: 'BRW', name: 'BRW' },
];

const RETAIL_LOCATIONS = [{ id: 'w-brw', code: 'BRW', name: 'BRW' }];

function envelope(total = 1, book = 'all'): StockDebtListResponse {
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
    locations: book === 'retail' ? RETAIL_LOCATIONS : LOCATIONS,
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
  locations: 'w-brw-ib',
  locations_mode: 'include',
  date_from_days: '0',
  date_to_days: '30',
  only_debt: 'true',
  projected: 'false',
  suppliers: '',
  query: '',
};

const lastList = () => getStockDebtList.mock.calls.at(-1)![0];

beforeEach(() => {
  vi.clearAllMocks();
  menuProps = null;
  getStockDebtList.mockImplementation(async (p: { book?: string }) => envelope(1, p?.book));
  getStockDebtCell.mockResolvedValue({ demand: [], supply: [] });
  exportStockDebt.mockResolvedValue({ id: 'd1' });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('Location filter on the real warehouses (AC-R7, AC-R9)', () => {
  it('offers exactly the envelope locations, labelled by code and name', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    fireEvent.click(locationTrigger());
    const options = screen.getAllByRole('option').map((o) => o.textContent?.trim());
    expect(options).toEqual(['BRW-IB', 'HQ (HEADQUARTER)', 'MWH-IB', 'BRW']);
  });

  it('is enabled under Book = Retail', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    fireEvent.click(screen.getByRole('radio', { name: 'Retail' }));

    expect(locationTrigger()).toBeEnabled();
    expect(locationTrigger()).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('one pick sends its warehouse id and reads "Location: BRW-IB"', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    pick('BRW-IB');

    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({ locations: ['w-brw-ib'], locationsMode: 'include' }),
      ),
    );
    expect(screen.getByText('Location: BRW-IB')).toBeInTheDocument();
  });

  it('two picks read "Locations: BRW-IB, MWH-IB"', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    pick('BRW-IB', 'MWH-IB');

    expect(screen.getByText('Locations: BRW-IB, MWH-IB')).toBeInTheDocument();
  });

  it('keeps the Location chip under Book = Retail and counts it as a filter', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    pick('BRW');
    fireEvent.keyDown(document.body, { key: 'Escape' });
    if (!screen.queryByRole('radio', { name: 'Retail' })) await openFilters();
    fireEvent.click(screen.getByRole('radio', { name: 'Retail' }));

    await screen.findByText('Book: Retail');
    // The Retail facet holds BRW, so the chip is its code, not "Unknown location".
    expect(screen.getByText('Location: BRW')).toBeInTheDocument();
    expect(screen.queryByText(/Unknown location/)).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: /^Filters/, hidden: true });
    expect(trigger.textContent?.trim()).toBe('Filters2');
  });

  it('a picked id missing from the facet reads "Unknown location" and shows no id', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    applyView({ ...FULL_VIEW, locations: 'w-gone-0000', date_from_days: '', date_to_days: '' });

    expect(await screen.findByText(/Unknown location/)).toBeInTheDocument();
    await openFilters();
    expect(document.body.textContent).not.toContain('w-gone-0000');
    expect(document.body.innerHTML).not.toContain('w-gone-0000');
  });

  it('the open Location select shows "Unknown location" for a missing id, never the id', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    applyView({ ...FULL_VIEW, locations: 'w-gone-0000', date_from_days: '', date_to_days: '' });
    await screen.findByText(/Unknown location/);
    await openFilters();

    const field = locationTrigger().parentElement as HTMLElement;
    expect(field.textContent).toMatch(/Unknown location/);
    expect(field.innerHTML).not.toContain('w-gone-0000');
    expect(document.body.innerHTML).not.toContain('w-gone-0000');
    for (const el of document.querySelectorAll('[aria-label]')) {
      expect(el.getAttribute('aria-label')).not.toContain('w-gone-0000');
    }
  });

  it('switching Book to one whose facet lacks the pick shows no id in the select', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    pick('BRW-IB');
    fireEvent.keyDown(document.body, { key: 'Escape' });
    if (!screen.queryByRole('radio', { name: 'Retail' })) await openFilters();
    fireEvent.click(screen.getByRole('radio', { name: 'Retail' }));
    await screen.findByText('Book: Retail');

    const field = locationTrigger().parentElement as HTMLElement;
    expect(field.textContent).toMatch(/Unknown location/);
    expect(document.body.innerHTML).not.toContain('w-brw-ib');
  });

  it('clearing the chip clears the picks and returns to Only', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    fireEvent.click(screen.getByRole('radio', { name: 'All except' }));
    pick('BRW-IB');
    await waitFor(() =>
      expect(lastList()).toMatchObject({ locations: ['w-brw-ib'], locationsMode: 'exclude' }),
    );

    fireEvent.click(
      screen.getByRole('button', { name: 'Clear filter: Location: all except BRW-IB' }),
    );

    await waitFor(() =>
      expect(lastList()).toMatchObject({ locations: [], locationsMode: 'include' }),
    );
    expect(screen.queryByText(/all except/)).not.toBeInTheDocument();
  });

  it('no raw warehouse id appears on the board', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    pick('BRW-IB', 'MWH-IB');
    expect(document.body.textContent).not.toMatch(/w-brw-ib|w-mwh-ib|w-hq/);
  });
});

describe('Saved views menu (AC-V1..V4, AC-R10)', () => {
  it('renders the menu with the stock debt listing key', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');

    expect(screen.getByTestId('saved-views-menu')).toBeInTheDocument();
    expect(menuProps?.listingKey).toBe('projects.stock_debt.view::stock-debt');
  });

  it('currentConfig stores locations as ids with the mode, never groups', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    fireEvent.click(screen.getByRole('radio', { name: 'Project' }));
    pick('BRW-IB', 'MWH-IB');

    await waitFor(() =>
      expect(menuProps?.currentConfig.quick_filters).toMatchObject({
        book: 'project',
        locations: 'w-brw-ib,w-mwh-ib',
        locations_mode: 'include',
      }),
    );
    expect(menuProps?.currentConfig.quick_filters).not.toHaveProperty('groups');
    expect(menuProps?.currentConfig.quick_filters).not.toHaveProperty('groups_mode');
  });

  it('applying a view sets book, location and a delivery range from today', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 9, 14, 10));
    renderBoard();
    await screen.findByText('SRTWB242');

    applyView(FULL_VIEW);

    expect(await screen.findByText('Book: Project')).toBeInTheDocument();
    expect(screen.getByText('Location: BRW-IB')).toBeInTheDocument();
    expect(screen.getByText('Delivery: 14 Oct 26 to 13 Nov 26')).toBeInTheDocument();
    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({
          book: 'project',
          locations: ['w-brw-ib'],
          locationsMode: 'include',
          dateFrom: '2026-10-14',
          dateTo: '2026-11-13',
          onlyDebt: true,
        }),
      ),
    );
  });

  it('applying a view with locations_mode exclude restores it', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    applyView({ ...FULL_VIEW, book: 'all', locations_mode: 'exclude',
      date_from_days: '', date_to_days: '' });
    expect(await screen.findByText('Location: all except BRW-IB')).toBeInTheDocument();
    await waitFor(() =>
      expect(lastList()).toMatchObject({ locations: ['w-brw-ib'], locationsMode: 'exclude' }),
    );
  });

  it('a view with only the old groups key applies as Every location, Only', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    applyView({ book: 'project', groups: 'ED', groups_mode: 'exclude', date_from_days: '',
      date_to_days: '' });
    await screen.findByText('Book: Project');
    expect(screen.queryByText(/^Locations?:/)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(lastList()).toMatchObject({ locations: [], locationsMode: 'include' }),
    );
  });

  it('"No view" resets every filter, Location and its mode included', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    applyView({ ...FULL_VIEW, locations_mode: 'exclude' });
    expect(await screen.findByText('Location: all except BRW-IB')).toBeInTheDocument();

    act(() => menuProps!.onApply(null));

    await waitFor(() => {
      expect(screen.queryByText('Book: Project')).not.toBeInTheDocument();
      expect(screen.queryByText(/^Locations?:/)).not.toBeInTheDocument();
      expect(screen.queryByText(/^Delivery:/)).not.toBeInTheDocument();
    });
    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({
          book: 'all',
          locations: [],
          locationsMode: 'include',
          dateFrom: '',
          dateTo: '',
          onlyDebt: true,
          query: '',
        }),
      ),
    );
  });
});

describe('Location reaches every consumer (AC-R8)', () => {
  it('the cell drill is fetched under the picked ids', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    pick('BRW-IB');
    fireEvent.keyDown(document.body, { key: 'Escape' });

    fireEvent.click(
      await screen.findByRole('button', { name: 'SRTWB242, Oct 26, balance -5' }),
    );

    await waitFor(() => expect(getStockDebtCell).toHaveBeenCalled());
    const call = getStockDebtCell.mock.calls.at(-1)!;
    expect(call[0]).toBe('p1');
    expect(call[6]).toEqual(['w-brw-ib']);
    expect(call[7]).toBe('include');
  });

  it('the cell drill carries All except', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    fireEvent.click(screen.getByRole('radio', { name: 'All except' }));
    pick('BRW-IB');
    fireEvent.keyDown(document.body, { key: 'Escape' });
    fireEvent.click(
      await screen.findByRole('button', { name: 'SRTWB242, Oct 26, balance -5' }),
    );
    await waitFor(() => expect(getStockDebtCell).toHaveBeenCalled());
    const call = getStockDebtCell.mock.calls.at(-1)!;
    expect(call[6]).toEqual(['w-brw-ib']);
    expect(call[7]).toBe('exclude');
  });

  it('the export carries the ids and the mode', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    fireEvent.click(screen.getByRole('radio', { name: 'All except' }));
    pick('BRW-IB');
    fireEvent.keyDown(document.body, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Export' }));
    const buttons = await screen.findAllByRole('button', { name: 'Export' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(exportStockDebt).toHaveBeenCalled());
    expect(exportStockDebt.mock.calls[0][0]).toMatchObject({
      locations: ['w-brw-ib'], locationsMode: 'exclude',
    });
  });

  it('applying a view sets search, supplier, only-debt and projected', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');

    applyView({
      query: 'SRTWB',
      suppliers: 's1',
      only_debt: 'false',
      projected: 'true',
      book: 'all',
      locations: '',
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
});

describe('Location mode: Only / All except (AC-R8, AC-R9)', () => {
  async function pickExcept(...codes: string[]) {
    await screen.findByText('SRTWB242');
    await openFilters();
    fireEvent.click(screen.getByRole('radio', { name: 'All except' }));
    pick(...codes);
  }

  it('defaults to Only', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    expect(screen.getByRole('radio', { name: 'Only' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'All except' })).not.toBeChecked();
  });

  it('All except BRW-IB sends mode exclude and reads "Location: all except BRW-IB"', async () => {
    renderBoard();
    await pickExcept('BRW-IB');

    await waitFor(() =>
      expect(lastList()).toMatchObject({ locationsMode: 'exclude', locations: ['w-brw-ib'] }),
    );
    expect(screen.getByText('Location: all except BRW-IB')).toBeInTheDocument();
  });

  it('two picks read "Locations: all except BRW-IB, MWH-IB"', async () => {
    renderBoard();
    await pickExcept('BRW-IB', 'MWH-IB');
    expect(screen.getByText('Locations: all except BRW-IB, MWH-IB')).toBeInTheDocument();
  });

  it('currentConfig reflects the mode', async () => {
    renderBoard();
    await pickExcept('BRW-IB');
    await waitFor(() =>
      expect(menuProps?.currentConfig.quick_filters).toMatchObject({
        locations: 'w-brw-ib', locations_mode: 'exclude',
      }),
    );
  });
});

describe('Page 1 on a Location change (AC-R8)', () => {
  it('picking a location returns to page 1', async () => {
    getStockDebtList.mockResolvedValue(envelope(60));
    renderBoard();
    await screen.findByText('SRTWB242');

    fireEvent.click(await screen.findByRole('button', { name: 'Go to next page' }));
    await waitFor(() => expect(lastList()).toMatchObject({ pageIndex: 1 }));

    await openFilters();
    pick('BRW-IB');

    await waitFor(() =>
      expect(lastList()).toMatchObject({ locations: ['w-brw-ib'], pageIndex: 0 }),
    );
  });

  it('switching Only to All except on page 2 returns to page 1', async () => {
    getStockDebtList.mockResolvedValue(envelope(60));
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();
    pick('BRW-IB');

    fireEvent.click(screen.getByRole('button', { name: 'Go to next page', hidden: true }));
    await waitFor(() =>
      expect(lastList()).toMatchObject({ locations: ['w-brw-ib'], pageIndex: 1 }),
    );

    fireEvent.click(await screen.findByRole('radio', { name: 'All except' }));

    await waitFor(() =>
      expect(lastList()).toMatchObject({ locationsMode: 'exclude', pageIndex: 0 }),
    );
  });
});
