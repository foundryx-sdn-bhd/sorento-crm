/** NUMBER-FIELDS round 3 (R7, R8): bad input in a line Unit price or Discount refuses Save with the sentence. */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
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

// A `let`, not a literal return, so the `?edit=1` auto-open test can point it at a URL
// carrying the param without a second mock module.
let searchParams = new URLSearchParams();
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

// The tab lives in the URL, not component state (see `handleTabChange`), so `openTab`
// below has to reach a REAL `router.replace` - one that feeds back into `searchParams`
// and wakes up every subscriber - or the controlled `<Tabs>` never sees the new value
// and stays on whichever tab it opened on.
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

// The header's Plan primary (S2, R3) reaches for the NextAuth session through this hook,
// which is not mounted under jsdom - stubbed off, the same way the sales-orders LIST suite
// stubs it, since this file's own tests are about Edit under the gear, not about the
// permission gate (that is a browser-pass check, AC-S2-6/10).
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

// The Transfers tab renders the SAME grid the Transfers page does; that component has its
// own tests, so here only the wiring (which order it is pinned to) has to be proven.
vi.mock(
  '@/app/(protected)/inventory-management/stock-transfers/components/StockTransfersPanel',
  () => ({
    StockTransfersPanel: (props: { salesOrderId?: string; showFilters?: boolean }) => (
      <div data-testid="stock-transfers-panel" data-sales-order={props.salesOrderId}>
        {String(props.showFilters)}
      </div>
    ),
  }),
);

const useSalesOrder = vi.fn();
const updateSalesOrderMutateAsync = vi.fn();
vi.mock('../../../hooks/useSalesOrders', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  salesOrdersPagerQuery: {
    listQueryKey: () => ['scm-sales-orders'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useSalesOrder: (...a: unknown[]) => useSalesOrder(...a),
  // The record's gear renders the shared action set, whose Delete needs this.
  useDeleteSalesOrder: () => ({ isPending: false, mutateAsync: vi.fn() }),
  // The header's prev/next pager reads the same list the user came from. An empty page
  // holds no record, so the pager renders nothing and these tests stay about the record.
  useSalesOrders: () => ({ data: { data: [], pagination: { total: 0, page: 1, limit: 25 } } }),
  useUpdateSalesOrder: () => ({ mutateAsync: updateSalesOrderMutateAsync, isPending: false }),
}));

vi.mock('../../../hooks/useScmOptions', () => ({
  useWarehouseOptions: () => ({
    data: [
      { value: 'BRW-BB', label: 'Brickworks Batu Berendam' },
      { value: 'BRW-IB', label: 'Brickworks Iskandar' },
      { value: 'KL-01', label: 'Kuala Lumpur 01' },
    ],
    isLoading: false,
  }),
}));

// The Customer and Product selects are SERVER-SEARCHED now - `fetchOptions`, not a static
// array - because the two masters behind them hold 6,397 and ~22,000 rows. Mocked as the
// component calls them: `(query, pageIndex) => Promise<Option[]>`.
vi.mock('../../../services/scmOptionsService', () => ({
  SELECT_PAGE_SIZE: 50,
  searchCustomerOptions: vi.fn(async () => [
    { value: '300-R009', label: 'Rowenda Kitchen Sdn Bhd' },
  ]),
  searchProductOptions: vi.fn(async () => [
    { value: 'CW-BASIN-450', label: 'CW-BASIN-450 · Ceramic Wash Basin 450mm' },
  ]),
}));

vi.mock('../../hooks/useSalesAgentOptions', () => ({
  useSalesAgentOptions: () => ({
    options: [
      { value: 'agent-jeremy', label: 'JR001 · JEREMY' },
      { value: 'agent-cindy', label: 'CL002 · CONTACT W' },
    ],
  }),
}));

// `getSalesOrderUoms` is the only export the component reads off this service module - the
// UoM select's own options. Everything else the component uses from it (`updateSalesOrder`)
// is reached through the fully-mocked `useUpdateSalesOrder` above, never this module.
vi.mock('../../../services/salesOrderService', () => ({
  getSalesOrderUoms: () =>
    Promise.resolve([
      { id: 'uom-pcs', uom_code: 'PCS', uom_name: 'Pieces' },
      { id: 'uom-box', uom_code: 'BOX', uom_name: 'Box' },
    ]),
}));

import { SalesOrderDetail } from './SalesOrderDetail';
import type { SalesOrder, SalesOrderLine } from '../../../types/scm.types';

function so(): SalesOrder {
  return {
    id: 'so-1',
    so_number: 'SO426364',
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
    total_qty: 46,
    committed_qty: 46,
    total_amount: '300.00',
    line_count: 2,
    open_line_count: 2,
    stock_locations: ['BRW-BB'],
    source: 'manual',
    internal_note: null,
    lines: [
      {
        id: 'l-1',
        sku: 'SRTWCFT001',
        product_name: 'Floor-standing toilet',
        qty_ordered: 30,
        qty_delivered: 0,
        uom: 'PCS',
        unit_price: '10.00',
        discount: null,
        line_total: '300.00',
        warehouse_code: 'BRW-BB',
        line_status: 'open',
        required_date: '2026-08-30',
      } as SalesOrderLine,
      {
        id: 'l-2',
        sku: 'SRTBASIN16',
        product_name: 'Basin',
        qty_ordered: 16,
        qty_delivered: 0,
        uom: 'PCS',
        unit_price: '5.00',
        discount: null,
        line_total: '80.00',
        warehouse_code: 'BRW-BB',
        line_status: 'open',
        required_date: '2026-08-30',
      } as SalesOrderLine,
    ],
    created_at: '2026-07-16T02:00:00',
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

function openTab(name: 'General' | 'Lines') {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false });
}

function clickEdit() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Sales order options' }), {
    button: 0,
    ctrlKey: false,
    pointerType: 'mouse',
  });
  fireEvent.click(screen.getByRole('menuitem', { name: /^Edit$/ }));
}


import { typeBadInput } from '@/test-utils/badInput';

function open() {
  useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
  renderDetail();
  clickEdit();
  openTab('Lines');
}

beforeEach(() => {
  cleanup();
  useSalesOrder.mockReset();
  updateSalesOrderMutateAsync.mockReset();
  searchParams = new URLSearchParams();
});

describe('SalesOrderDetail bad number input on a line (R7, R8)', () => {
  it.each([
    ['R7 Unit price', 'Unit price on SRTWCFT001'],
    ['R8 Discount', 'Discount on SRTWCFT001'],
  ])('%s: Save is refused, the sentence shows under that box, nothing is sent', (_name, label) => {
    open();
    const box = screen.getByLabelText(label);
    typeBadInput(box);
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));
    expect(screen.getByText('Enter a number of at least 0, or leave it blank.')).toBeInTheDocument();
    expect(screen.getByLabelText(label)).toHaveAttribute('aria-invalid', 'true');
    expect(updateSalesOrderMutateAsync).not.toHaveBeenCalled();
  });
});
