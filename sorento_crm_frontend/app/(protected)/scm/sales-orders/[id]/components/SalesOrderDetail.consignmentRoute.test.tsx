/**
 * SalesOrderDetail - AC-CONSIGN-CRM UAC-21: the consignment route (docType="consignment")
 * backs out to /scm/consignments, and a consignment opened on the sales-orders route is
 * replaced to /scm/consignments/<id>. Harness copied from `SalesOrderDetail.consignment.test.tsx`.
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
const replaceCalls: string[] = [];
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
      replaceCalls.push(url);
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
const detailActionsProps: Array<{ pager?: { detailPath?: string; listQueryKey?: unknown } }> = [];
vi.mock('@/components/common/DetailActions', () => ({
  __esModule: true,
  default: (props: { pager?: { detailPath?: string } }) => {
    detailActionsProps.push(props);
    return null;
  },
}));

vi.mock('../../../hooks/useSalesOrders', () => ({
  consignmentsPagerQuery: {
    listQueryKey: () => ['scm-consignments'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
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
import type { SalesOrder, SalesOrderLine } from '../../../types/scm.types';

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

function renderDetail(props: { docType?: 'consignment' } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SalesOrderDetail id="so-1" {...(props as object)} />
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
  replaceCalls.length = 0;
  detailActionsProps.length = 0;
  useSalesOrder.mockReset();
  updateSalesOrderMutateAsync.mockReset().mockResolvedValue({ planning_change_batch: null });
  searchParams = new URLSearchParams();
  hasPermission = false;
});



const lines = () => [
  {
    id: 'l-1', sku: 'SKU-A', product_name: 'A', qty_ordered: 10, qty_delivered: 0,
    uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
    line_no: 1, source: 'autocount', qty_returned: 0,
  },
];

function stub(over: Record<string, unknown>) {
  useSalesOrder.mockReturnValue({ data: so(over), isLoading: false, isError: false });
}

describe('UAC-21: consignment route back link and pager', () => {
  it('docType="consignment" backs out to /scm/consignments, labelled Back to consignments', () => {
    useSalesOrder.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    renderDetail({ docType: 'consignment' });
    const back = screen.getByRole('link', { name: /Back to consignments/i });
    expect(back.getAttribute('href')).toMatch(/^\/scm\/consignments(\?|$)/);
    expect(screen.queryByText('Back to sales orders')).toBeNull();
  });

  it('the sales-orders route keeps Back to sales orders', () => {
    useSalesOrder.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    renderDetail();
    const back = screen.getByRole('link', { name: /Back to sales orders/i });
    expect(back.getAttribute('href')).toMatch(/^\/scm\/sales-orders(\?|$)/);
  });

  it('docType="consignment" walks the consignment list: pager detailPath /scm/consignments', () => {
    stub({ doc_type: 'consignment', lines: lines() });
    renderDetail({ docType: 'consignment' });
    const last = detailActionsProps[detailActionsProps.length - 1];
    expect(last?.pager?.detailPath).toBe('/scm/consignments');
    expect(last?.pager?.listQueryKey).toEqual(expect.anything());
    expect((last?.pager as { listQueryKey: (p: unknown) => unknown }).listQueryKey({})).toEqual([
      'scm-consignments',
    ]);
  });
});

describe('UAC-21: a consignment on the sales-orders route lands on the consignment page', () => {
  it('replaces to /scm/consignments/<id>', () => {
    stub({ doc_type: 'consignment', lines: lines() });
    renderDetail();
    expect(replaceCalls.some((u) => u.startsWith('/scm/consignments/so-1'))).toBe(true);
  });

  it('a plain sales order is not redirected', () => {
    stub({ doc_type: 'sales_order', lines: lines() });
    renderDetail();
    expect(replaceCalls.some((u) => u.startsWith('/scm/consignments'))).toBe(false);
  });
});
