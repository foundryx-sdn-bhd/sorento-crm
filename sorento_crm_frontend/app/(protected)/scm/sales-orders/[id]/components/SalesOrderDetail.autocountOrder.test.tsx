/**
 * SalesOrderDetail - sales order lines in AutoCount order, a Source column, and the Plan CTA.
 *
 * Plan: `documentation/plans/scm/PLAN-so-lines-autocount-order.md`.
 * UAC: `documentation/plans/scm/so-lines-autocount-order-acceptance-criteria.md`, S2.
 *
 *   AC-S2-1  first data column is No. (after the truck column), showing `line_no`; `-` when null.
 *   AC-S2-2  clicking No. sorts numerically (1, 2, 3, 10, 11, never lexicographic).
 *   AC-S2-3  default order is the server order - no client default sort.
 *   AC-S2-4  Source column renders AutoCount / Order inquiry / Upload / Absorbed history /
 *            Manual as a Badge.
 *   AC-S2-6  header primary is Plan, linking to
 *            `/project-sales/fulfilment-planning?orders=<so_number>`; hidden without
 *            `projects.projects.view`, and no Edit primary button either way.
 *   AC-S2-7  gear dropdown holds Edit then Delete; Edit opens the edit session.
 *   AC-S2-8  in an edit session the header shows only Save / Cancel; No. is a read-only
 *            value, never an input.
 *   AC-S2-6b (owner-side finding, 21 Sep) the Plan primary additionally requires
 *            `demand_class === 'project'`; any other demand class (e.g. `retail`) shows no
 *            Plan link even with the permission, and the gear still holds Edit / Delete - the
 *            fulfilment board only accepts project demand.
 *
 * Mocking pattern copied wholesale from `SalesOrderDetail.test.tsx` (render helpers, query
 * mocks, DataGrid-in-jsdom fixtures) per the tester brief - this file adds only the
 * assertions the slice is new.
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

describe('AC-S2-1: No. is the first data column', () => {
  it('shows line_no, and a dash when it is null', () => {
    useSalesOrder.mockReturnValue({
      data: so({
        lines: [
          { id: 'l-1', sku: 'SKU-A', product_name: 'A', qty_ordered: 10, qty_delivered: 0,
            uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
            line_no: 7, source: 'autocount' },
          { id: 'l-2', sku: 'SKU-B', product_name: 'B', qty_ordered: 5, qty_delivered: 0,
            uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
            line_no: null, source: 'manual' },
        ],
        line_count: 2,
        open_line_count: 2,
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    expect(screen.getByRole('columnheader', { name: 'No.' })).toBeInTheDocument();
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    // AC-SD20: the truck column (empty header) now leads; No. is the first DATA column.
    expect(headers[0]).toBe('');
    expect(headers[1]).toBe('No.');

    const rowA = screen.getByText('SKU-A').closest('tr') as HTMLElement;
    const rowB = screen.getByText('SKU-B').closest('tr') as HTMLElement;
    // Scoped to the SECOND cell (No. is the first data column, after the truck cell) - `required_date: null` also
    // prints its own "-" further along the row, so a bare `getByText('-')` on the whole
    // row is ambiguous.
    const firstCell = (row: HTMLElement) => within(row).getAllByRole('cell')[1];
    expect(within(firstCell(rowA)).getByText('7')).toBeInTheDocument();
    expect(within(firstCell(rowB)).getByText('-')).toBeInTheDocument();
  });
});

describe('AC-S2-2 / AC-S2-3: numeric sort, server order by default', () => {
  const NUMBERED_LINES: SalesOrderLine[] = [
    { id: 'l-1', sku: 'SKU-1', product_name: 'One', qty_ordered: 1, qty_delivered: 0,
      uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
      line_no: 1, source: 'autocount' },
    { id: 'l-10', sku: 'SKU-10', product_name: 'Ten', qty_ordered: 1, qty_delivered: 0,
      uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
      line_no: 10, source: 'autocount' },
    { id: 'l-2', sku: 'SKU-2', product_name: 'Two', qty_ordered: 1, qty_delivered: 0,
      uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
      line_no: 2, source: 'autocount' },
    { id: 'l-11', sku: 'SKU-11', product_name: 'Eleven', qty_ordered: 1, qty_delivered: 0,
      uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
      line_no: 11, source: 'autocount' },
    { id: 'l-3', sku: 'SKU-3', product_name: 'Three', qty_ordered: 1, qty_delivered: 0,
      uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
      line_no: 3, source: 'autocount' },
  ];

  const skuOrder = () =>
    screen
      .getAllByRole('row')
      .map((row) => row.textContent ?? '')
      .filter((text) => text.includes('SKU-'))
      .map((text) => text.match(/SKU-\d+/)?.[0] ?? '');

  beforeEach(() => {
    useSalesOrder.mockReturnValue({
      data: so({ lines: NUMBERED_LINES, line_count: 5, open_line_count: 5 }),
      isLoading: false,
      isError: false,
    });
  });

  it('AC-S2-3: renders in the fixture (server) order with no default client sort', () => {
    renderDetail();
    openTab('Lines');

    expect(skuOrder()).toEqual(['SKU-1', 'SKU-10', 'SKU-2', 'SKU-11', 'SKU-3']);
  });

  it('AC-S2-2: clicking No. sorts numerically, never lexicographically', () => {
    renderDetail();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: 'No.' }));

    expect(skuOrder()).toEqual(['SKU-1', 'SKU-2', 'SKU-3', 'SKU-10', 'SKU-11']);
  });
});

describe('AC-S2-4: Source column', () => {
  const CASES: Array<[string, string]> = [
    ['autocount', 'AutoCount'],
    ['inquiry', 'Order inquiry'],
    ['upload', 'Upload'],
    ['history', 'Absorbed history'],
    ['manual', 'Manual'],
  ];

  it.each(CASES)('renders %s as %s', (source, label) => {
    useSalesOrder.mockReturnValue({
      data: so({
        lines: [
          { id: 'l-1', sku: 'SKU-A', product_name: 'A', qty_ordered: 10, qty_delivered: 0,
            uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
            line_no: 1, source },
        ],
        line_count: 1,
        open_line_count: 1,
      }),
      isLoading: false,
      isError: false,
    });
    const { unmount } = renderDetail();
    openTab('Lines');

    expect(screen.getByRole('columnheader', { name: 'Source' })).toBeInTheDocument();
    const row = screen.getByText('SKU-A').closest('tr') as HTMLElement;
    expect(within(row).getByText(label)).toBeInTheDocument();
    unmount();
  });
});

describe('AC-S2-6: header primary is Plan, gated on projects.projects.view', () => {
  it('with the permission: a Plan link to the fulfilment board, on this one order', () => {
    hasPermission = true;
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    const link = screen.getByRole('link', { name: /Plan/ });
    expect(link).toHaveAttribute(
      'href',
      `/project-sales/fulfilment-planning?orders=${encodeURIComponent('SO-2026/07-0042')}`,
    );
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  it('without the permission: no Plan link and no Edit primary button', () => {
    hasPermission = false;
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.queryByRole('link', { name: /Plan/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
  });
});

describe('AC-S2-6b: Plan additionally requires demand_class === project', () => {
  it('AC-S2-6b-1: permission + retail demand_class -> no Plan link; gear still has Edit and Delete', () => {
    hasPermission = true;
    useSalesOrder.mockReturnValue({
      data: so({ demand_class: 'retail' }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.queryByRole('link', { name: /Plan/ })).not.toBeInTheDocument();

    openGear();
    const items = screen.getAllByRole('menuitem').map((item) => (item.textContent ?? '').trim());
    expect(items).toEqual(['Edit', 'Delete']);
  });

  it('AC-S2-6b-2: permission + project demand_class -> Plan link present', () => {
    hasPermission = true;
    useSalesOrder.mockReturnValue({
      data: so({ demand_class: 'project' }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByRole('link', { name: /Plan/ })).toBeInTheDocument();
  });
});

describe('AC-S2-7: Edit moved into the gear, above Delete', () => {
  it('the gear lists Edit then Delete; Edit opens the edit session', () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    openGear();
    const items = screen.getAllByRole('menuitem').map((item) => (item.textContent ?? '').trim());
    expect(items).toEqual(['Edit', 'Delete']);

    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }));

    expect(screen.getByRole('button', { name: 'Save sales order' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });
});

describe('AC-S2-8: in the edit session, only Save/Cancel show; No. stays a read-only value', () => {
  it('the header carries only Save / Cancel, and No. is text, not an input', () => {
    useSalesOrder.mockReturnValue({
      data: so({
        lines: [
          { id: 'l-1', sku: 'SKU-A', product_name: 'A', qty_ordered: 10, qty_delivered: 0,
            uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open', required_date: null,
            line_no: 7, source: 'autocount' },
        ],
        line_count: 1,
        open_line_count: 1,
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }));

    expect(screen.getByRole('button', { name: 'Save sales order' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sales order options' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Plan/ })).not.toBeInTheDocument();

    openTab('Lines');
    // In an edit session the Product cell becomes a select (SKU is no longer a plain span),
    // so the row is found by that control's own accessible label instead - the same
    // convention `SalesOrderDetail.test.tsx` uses for an editing row.
    const row = screen.getByLabelText('Product on SKU-A').closest('tr') as HTMLElement;
    // Cell 0 holds the Deliveries truck button; the line number is cell 1.
    const firstCell = within(row).getAllByRole('cell')[1];
    expect(within(firstCell).getByText('7')).toBeInTheDocument();
    expect(within(firstCell).queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(within(firstCell).queryByRole('textbox')).not.toBeInTheDocument();
  });
});
