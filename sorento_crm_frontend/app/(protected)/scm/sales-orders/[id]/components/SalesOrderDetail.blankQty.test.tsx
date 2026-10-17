/**
 * NUMBER-FIELDS (UAC2.1, UAC2.4, UAC1.1): clearing the Qty ordered box on an existing line
 * must never cancel it. Blank is "no value yet": it stays blank, Save sends nothing and the
 * field says why. A typed 0 is still the book's way of cancelling a line.
 * Fixture mirrors dev data: SO426364, existing line SRTWCFT001 qty 30.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
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
    total_qty: 30,
    committed_qty: 30,
    total_amount: '300.00',
    line_count: 1,
    open_line_count: 1,
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

const MESSAGE = 'Enter a number of at least 0.';

function openQty(): HTMLInputElement {
  useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
  renderDetail();
  clickEdit();
  openTab('Lines');
  return screen.getByDisplayValue('30') as HTMLInputElement;
}

beforeEach(() => {
  cleanup();
  useSalesOrder.mockReset();
  updateSalesOrderMutateAsync.mockReset().mockResolvedValue({ planning_change_batch: null });
  searchParams = new URLSearchParams();
});

describe('SalesOrderDetail - a blank quantity never cancels a line', () => {
  it('keeps the quantity box empty after the digits are cleared (not 0)', () => {
    const qty = openQty();
    fireEvent.change(qty, { target: { value: '' } });
    expect(qty.value).toBe('');
    expect(screen.queryByDisplayValue('0')).not.toBeInTheDocument();
  });

  it('refuses Save with a blank quantity: nothing sent, message and aria-invalid on the box', async () => {
    const qty = openQty();
    fireEvent.change(qty, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    expect(updateSalesOrderMutateAsync).not.toHaveBeenCalled();
    expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
    expect(qty).toHaveAttribute('aria-invalid', 'true');
    // The session stays open.
    expect(screen.getByRole('button', { name: 'Save sales order' })).toBeInTheDocument();
  });

  it('still lets a typed 0 cancel the line', async () => {
    const qty = openQty();
    fireEvent.change(qty, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await waitFor(() => expect(updateSalesOrderMutateAsync).toHaveBeenCalledTimes(1));
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    expect(body.lines[0]).toMatchObject({ id: 'l-1', qty_ordered: 0 });
  });

  it('clears the message as soon as a valid quantity is typed', async () => {
    const qty = openQty();
    fireEvent.change(qty, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));
    expect(await screen.findByText(MESSAGE)).toBeInTheDocument();

    fireEvent.change(qty, { target: { value: '5' } });
    expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
    expect(qty).not.toHaveAttribute('aria-invalid', 'true');
  });
});
