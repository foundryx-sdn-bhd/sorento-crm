/**
 * SalesOrderDetail - AutoCount's `Transferable` flag on the General (header) tab
 * (SO-TRANSFERABLE, owner 1 Oct 2026).
 *
 * Read-only in view AND edit, worded "From AutoCount": AutoCount owns the flag, and an F
 * order is skipped by Stock Debt and the fulfilment ladder until AutoCount flips it to T.
 * Setup copied from `SalesOrderDetail.autocountOrder.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}
Element.prototype.scrollIntoView = vi.fn();

let searchParams = new URLSearchParams();
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

const searchParamsListeners = new Set<() => void>();
function replaceSearchParams(next: URLSearchParams) {
  searchParams = next;
  searchParamsListeners.forEach((listener) => listener());
}
vi.mock('next/navigation', () => ({
  usePathname: () => '/scm/sales-orders/so-1',
  useRouter: () => ({
    push: vi.fn(),
    replace: (url: string) => {
      const qIndex = url.indexOf('?');
      replaceSearchParams(new URLSearchParams(qIndex >= 0 ? url.slice(qIndex + 1) : ''));
    },
  }),
  useSearchParams: () =>
    React.useSyncExternalStore(
      (listener) => {
        searchParamsListeners.add(listener);
        return () => searchParamsListeners.delete(listener);
      },
      () => searchParams,
    ),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock(
  '@/app/(protected)/inventory-management/stock-transfers/components/StockTransfersPanel',
  () => ({
    StockTransfersPanel: () => <div data-testid="stock-transfers-panel" />,
  }),
);

// AC-S2-6: the header's Plan primary is gated on `projects.projects.view`, same as the
// list's own "Plan selected". A `let`, flipped per-test, the same convention
// `SalesOrdersGrid.test.tsx` already uses for this hook.
let hasPermission = false;
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => hasPermission,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

const useSalesOrder = vi.fn();
const updateSalesOrderMutateAsync = vi.fn();
vi.mock('../../../hooks/useSalesOrders', () => ({
  salesOrdersPagerQuery: {
    listQueryKey: () => ['scm-sales-orders'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useSalesOrder: (...a: unknown[]) => useSalesOrder(...a),
  useDeleteSalesOrder: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useSalesOrders: () => ({ data: { data: [], pagination: { total: 0, page: 1, limit: 25 } } }),
  useUpdateSalesOrder: () => ({ mutateAsync: updateSalesOrderMutateAsync, isPending: false }),
}));

vi.mock('../../../hooks/useScmOptions', () => ({
  useWarehouseOptions: () => ({ data: [], isLoading: false }),
}));

vi.mock('../../../services/scmOptionsService', () => ({
  SELECT_PAGE_SIZE: 50,
  searchCustomerOptions: vi.fn(async () => []),
  searchProductOptions: vi.fn(async () => []),
}));

vi.mock('../../hooks/useSalesAgentOptions', () => ({
  useSalesAgentOptions: () => ({ options: [] }),
}));

vi.mock('../../../services/salesOrderService', () => ({
  getSalesOrderUoms: () => Promise.resolve([]),
}));

import { SalesOrderDetail } from './SalesOrderDetail';
import type { SalesOrder } from '../../../types/scm.types';

function so(over: Partial<SalesOrder> = {}): SalesOrder {
  return {
    id: 'so-1',
    so_number: 'SO-2026/07-0042',
    order_type: 'project',
    order_type_label: 'Project',
    demand_class: 'project',
    customer_code: '300-R009',
    customer_name: 'Rowenda Kitchen Sdn Bhd',
    market_segment: 'Project',
    priority: 'normal',
    status: 'open',
    order_date: '2026-07-16',
    requested_delivery_date: '2026-08-30',
    total_qty: 320,
    committed_qty: 320,
    total_amount: '31985.00',
    line_count: 1,
    open_line_count: 1,
    stock_locations: ['BRW-BB'],
    source: 'manual',
    internal_note: null,
    lines: [
      {
        id: 'l-1',
        sku: 'CW-BASIN-450',
        product_name: 'Ceramic Wash Basin 450mm',
        qty_ordered: 320,
        qty_delivered: 0,
        uom: 'PCS',
        unit_price: '100.00',
        discount: '15.00',
        line_total: '31985.00',
        warehouse_code: 'BRW-BB',
        line_status: 'open',
        required_date: '2026-08-30',
        line_no: 1,
        source: 'autocount',
      },
    ],
    created_at: '2026-07-16T02:00:00',
    ...over,
  } as SalesOrder;
}

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SalesOrderDetail id="so-1" />
    </QueryClientProvider>,
  );
}

function openTab(name: 'General' | 'Lines' | 'Delivery' | 'Transfers') {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false });
}

/** Radix opens its menu on pointer-down. */
function openGear() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Sales order options' }), {
    button: 0,
    pointerType: 'mouse',
  });
}

beforeEach(() => {
  cleanup();
  useSalesOrder.mockReset();
  updateSalesOrderMutateAsync.mockReset().mockResolvedValue({ planning_change_batch: null });
  searchParams = new URLSearchParams();
  hasPermission = false;
});


function orderRegion() {
  return screen.getByRole('region', { name: 'Order' });
}

describe('SO-TRANSFERABLE: the Transferable field on the General tab', () => {
  it.each([
    [false, 'No'],
    [true, 'Yes'],
    [null, 'Not stated'],
  ])('is_transferable=%s reads "%s", marked From AutoCount', (flag, words) => {
    useSalesOrder.mockReturnValue({
      data: so({ is_transferable: flag, source: 'autocount' }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('General');

    const region = orderRegion();
    const label = within(region).getByText('Transferable');
    const field = label.parentElement as HTMLElement;
    expect(within(field).getByText(words)).toBeInTheDocument();
    expect(within(field).getByText('From AutoCount')).toBeInTheDocument();
  });

  it('does not claim AutoCount on an order that did not come from it', () => {
    useSalesOrder.mockReturnValue({
      data: so({ is_transferable: null, source: 'manual' }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('General');

    const field = within(orderRegion()).getByText('Transferable').parentElement as HTMLElement;
    expect(within(field).getByText('Not stated')).toBeInTheDocument();
    expect(within(field).queryByText('From AutoCount')).toBeNull();
  });

  it('stays a read-only value in the edit session', () => {

    useSalesOrder.mockReturnValue({
      data: so({ is_transferable: false }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }));
    openTab('General');

    const region = orderRegion();
    expect(within(region).queryByLabelText('Transferable')).toBeNull();
    const field = within(region).getByText('Transferable').parentElement as HTMLElement;
    expect(within(field).getByText('No')).toBeInTheDocument();
  });
});
