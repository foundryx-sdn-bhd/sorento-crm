/**
 * SO-TRANSFERABLE: AutoCount's `Transferable` flag on the sales-order list (owner, 1 Oct 2026).
 *
 * A column (Yes / No / Not stated) and a filter that reaches the query as
 * `transferable: yes | no | unknown`. The filtering itself is the backend's, pinned in
 * `tests/scm/test_so_transferable.py::test_the_list_carries_and_filters_on_it`.
 *
 * Setup copied from `SalesOrdersList.filters.test.tsx` (the same mocks the list needs).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}
if (!window.ResizeObserver) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
Element.prototype.scrollIntoView = vi.fn();

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

// The real `DateRangePicker` is a Popover + react-day-picker Calendar - its own behaviour
// (enforcing from <= to by construction) is not this list's concern and has no repo pattern
// for driving a Calendar grid under jsdom. Stood in for here with a single button that fires
// `onChange` with both ends at once - the ONE-FACT contract the real widget guarantees - so
// what this suite asserts is that the list wires the range into both query params, not that
// the calendar itself works.
vi.mock('@/components/ui/date-range-picker', () => ({
  DateRangePicker: (props: {
    id?: string;
    from?: string | null;
    to?: string | null;
    onChange: (next: { from: string | null; to: string | null }) => void;
    placeholder?: string;
  }) => (
    <button
      type="button"
      id={props.id}
      onClick={() => props.onChange({ from: '2026-03-01', to: '2026-03-31' })}
    >
      {props.from && props.to ? `${props.from} - ${props.to}` : (props.placeholder ?? 'Pick a date range')}
    </button>
  ),
}));

const push = vi.fn();
// PARTIAL. The grid and its toolbar reach for other exports of this module, and replacing it
// wholesale left them undefined - which showed up as a grid stuck on its loading skeleton
// rather than as an error.
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push }),
  usePathname: () => '/scm/sales-orders',
  useSearchParams: () => new URLSearchParams(),
}));

const EMPTY = { data: [], isLoading: false };
vi.mock('../../hooks/useScmOptions', () => ({
  useCustomerOptions: () => ({
    data: [{ value: '300-R009', label: 'Rowenda Kitchen Sdn Bhd' }],
    isLoading: false,
  }),
  // The Add-sales-order modal renders alongside the list and reaches for these.
  useOrderTypeOptions: () => EMPTY,
  useProductOptions: () => EMPTY,
  useSupplierOptions: () => EMPTY,
  useCategoryOptions: () => EMPTY,
  useWarehouseOptions: () => EMPTY,
}));

// The grid asks for the user's saved column order via this hook, which reads the route to
// build its listing key. With `next/navigation` mocked the hook goes down its fetching path
// and the grid sits on its loading skeleton, so it is stubbed the same way the detail suite
// stubs it.
// The Plan action asks whether this user may open the fulfilment board. `useHasPermission`
// reaches for the NextAuth session, which is not mounted under jsdom, so it is stubbed the
// same way the proforma-invoice view's own suite stubs it.
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

// The remembered sort/filter view (PLAN-listing-view-memory) reads this service under
// `useListingViewPreferences`. Stubbed to resolve fast with nothing stored, so the gated
// data fetch unblocks on the next tick rather than hanging on a real network call.
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(async () => ({ listing_key: '/scm/sales-orders', config: null })),
  upsertUserListColumnConfig: vi.fn(async (listingKey: string, payload: unknown) => ({
    listing_key: listingKey,
    config: payload,
  })),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));

const useSalesOrders = vi.fn();
vi.mock('../../hooks/useSalesOrders', () => ({
  useSalesOrders: (...a: unknown[]) => useSalesOrders(...a),
  useSalesOrderAgents: () => ({ data: [], isLoading: false }),
  useCreateSalesOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateSalesOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteSalesOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResetSalesOrderPlanning: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateDoFromSalesOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import SalesOrdersList from './SalesOrdersList';
import type { SalesOrder } from '../../types/scm.types';

function order(over: Partial<SalesOrder> = {}): SalesOrder {
  return {
    id: 'so-1',
    so_number: 'SO900001',
    order_type: 'project',
    order_type_label: 'Project',
    customer_code: '300-R009',
    customer_name: 'Rowenda Kitchen Sdn Bhd',
    market_segment: null,
    priority: 'normal',
    status: 'open',
    order_date: '2026-07-01',
    requested_delivery_date: '2026-09-01',
    total_qty: 12,
    committed_qty: 12,
    lines: [],
    source: 'inquiry',
    internal_note: null,
    stock_locations: [],
    linked_purchase_orders: [],
    created_at: '2026-07-01T00:00:00',
    ...over,
  } as SalesOrder;
}

function renderList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SalesOrdersList />
    </QueryClientProvider>,
  );
}

function stub(rows: SalesOrder[] = [order()]) {
  useSalesOrders.mockReturnValue({
    data: {
      data: rows,
      pagination: { total: rows.length, page: 1, limit: 25 },
      empty: !rows.length,
    },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  });
}

/** The arguments of the most recent list query. */
function lastQuery(): Record<string, unknown> {
  return useSalesOrders.mock.calls[useSalesOrders.mock.calls.length - 1][0] as Record<
    string,
    unknown
  >;
}

async function openFilters() {
  fireEvent.pointerDown(
    screen.getByRole('button', { name: /^Filters/ }),
    { ctrlKey: false, button: 0 },
  );
  await screen.findByText('Outstanding qty');
}

beforeEach(() => {
  useSalesOrders.mockReset();
  push.mockReset();
});

describe('SalesOrdersList - Transferable (SO-TRANSFERABLE)', () => {
  it('shows the flag as Yes / No / Not stated in its own column', async () => {
    stub([
      order({ id: 'so-f', so_number: 'SO422024', is_transferable: false }),
      order({ id: 'so-t', so_number: 'SO422049', is_transferable: true }),
      order({ id: 'so-u', so_number: 'SO422099', is_transferable: null }),
    ]);
    renderList();

    expect(await screen.findByText('SO422024')).toBeInTheDocument();
    expect(screen.getAllByText('Transferable').length).toBeGreaterThan(0);
    const cell = (soNumber: string) => {
      const row = screen.getByText(soNumber).closest('tr');
      expect(row).not.toBeNull();
      return row as HTMLElement;
    };
    expect(within(cell('SO422024')).getByText('No')).toBeInTheDocument();
    expect(within(cell('SO422049')).getByText('Yes')).toBeInTheDocument();
    expect(within(cell('SO422099')).getByText('Not stated')).toBeInTheDocument();
  });

  it('sends the transferable filter the user picked', async () => {
    stub();
    renderList();
    await openFilters();

    expect(lastQuery()).toMatchObject({ transferable: null });

    fireEvent.click(screen.getByRole('combobox', { name: /transferable/i }));
    fireEvent.click(await screen.findByRole('option', { name: 'No' }));

    await waitFor(() => {
      expect(lastQuery()).toMatchObject({ transferable: 'no' });
    });
  });

  it('offers "Not stated" as its own answer', async () => {
    stub();
    renderList();
    await openFilters();

    fireEvent.click(screen.getByRole('combobox', { name: /transferable/i }));
    fireEvent.click(await screen.findByRole('option', { name: 'Not stated' }));

    await waitFor(() => {
      expect(lastQuery()).toMatchObject({ transferable: 'unknown' });
    });
  });
});
