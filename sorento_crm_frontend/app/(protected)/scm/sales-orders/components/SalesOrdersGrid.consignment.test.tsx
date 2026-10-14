/**
 * SalesOrdersGrid - AC-CONSIGN-CRM UAC-06: a consignment reads "Consignment" in the list.
 * Mocking copied from `SalesOrdersGrid.autocountSource.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
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

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const push = vi.fn();
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push }),
  usePathname: () => '/scm/sales-orders',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(async () => ({ listing_key: '/scm/sales-orders', config: null })),
  upsertUserListColumnConfig: vi.fn(async (listingKey: string, payload: unknown) => ({
    listing_key: listingKey,
    config: payload,
  })),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));

let hasPermission = true;
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => hasPermission,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

const EMPTY = { data: [], isLoading: false };
vi.mock('../../hooks/useScmOptions', () => ({
  useCustomerOptions: () => EMPTY,
  useOrderTypeOptions: () => EMPTY,
  useProductOptions: () => EMPTY,
  useSupplierOptions: () => EMPTY,
  useCategoryOptions: () => EMPTY,
  useWarehouseOptions: () => EMPTY,
}));

vi.mock('../hooks/useSalesAgentOptions', () => ({
  useSalesAgentOptions: () => ({ options: [], isLoading: false }),
}));

// The filter popover uses the standard SearchableSelect, mocked as a native <select> - the
// same stand-in `SalesOrdersGrid.test.tsx` already uses so the options land in the DOM
// without driving a cmdk popover.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    id,
    value,
    onChange,
    options = [],
    placeholder,
  }: {
    id?: string;
    value?: string;
    onChange?: (v: string) => void;
    options?: Array<{ value: string; label: string }>;
    placeholder?: string;
  }) => (
    <select
      id={id}
      aria-label={placeholder}
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
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

import SalesOrdersGrid from './SalesOrdersGrid';
import type { SalesOrder } from '../../types/scm.types';

function order(over: Partial<SalesOrder> = {}): SalesOrder {
  return {
    id: 'so-1',
    so_number: 'SO900001',
    order_type: 'project',
    order_type_label: 'Project',
    customer_code: '300-R009',
    customer_name: 'ROWENDA KITCHEN SDN BHD',
    market_segment: 'Retail',
    priority: 'normal',
    status: 'open',
    order_date: '2026-07-01',
    requested_delivery_date: '2026-09-01',
    total_qty: 12,
    committed_qty: 12,
    lines: [],
    source: 'autocount',
    stock_locations: [],
    linked_purchase_orders: [],
    awaiting_purchase_orders: 0,
    order_inquiries: [],
    created_at: '2026-07-01T00:00:00',
    ...over,
  } as SalesOrder;
}

function stub(rows: SalesOrder[]) {
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

function renderGrid() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SalesOrdersGrid />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  push.mockReset();
  hasPermission = true;
});

describe('UAC-06: Consignment label in the SO list', () => {
  it('a consignment row shows Consignment in its SO number cell', async () => {
    stub([order({ doc_type: 'consignment' } as Partial<SalesOrder>)]);
    renderGrid();
    const row = (await screen.findByText('SO900001')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Consignment')).toBeInTheDocument();
  });

  it('a sales_order row shows no Consignment label', async () => {
    stub([order({ doc_type: 'sales_order' } as Partial<SalesOrder>)]);
    renderGrid();
    const row = (await screen.findByText('SO900001')).closest('tr') as HTMLElement;
    expect(within(row).queryByText('Consignment')).toBeNull();
  });
});
