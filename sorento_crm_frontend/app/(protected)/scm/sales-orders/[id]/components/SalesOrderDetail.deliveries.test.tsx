/**
 * SalesOrderDetail - the truck button and the "Deliveries" lightbox on the Lines tab
 * (SO-DELIVERIES-VIEW, AC-SD20, AC-SD21, AC-SD29).
 *
 * The service is mocked at the SERVICE module (`getSalesOrderDeliveries`) and the real
 * `useSalesOrderDeliveries` hook runs on top of it, because "fetched once" is a property of
 * the hook's `enabled` flag, not of the screen.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within, waitFor } from '@testing-library/react';
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
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));
vi.mock(
  '@/app/(protected)/inventory-management/stock-transfers/components/StockTransfersPanel',
  () => ({ StockTransfersPanel: () => <div /> }),
);

const useSalesOrder = vi.fn();
vi.mock('../../../hooks/useSalesOrders', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  salesOrdersPagerQuery: {
    listQueryKey: () => ['scm-sales-orders'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useSalesOrder: (...a: unknown[]) => useSalesOrder(...a),
  useDeleteSalesOrder: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useSalesOrders: () => ({ data: { data: [], pagination: { total: 0, page: 1, limit: 25 } } }),
  useUpdateSalesOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useScmOptions', () => ({
  useWarehouseOptions: () => ({ data: [{ value: 'BRW', label: 'Brickworks' }], isLoading: false }),
}));
vi.mock('../../../services/scmOptionsService', () => ({
  SELECT_PAGE_SIZE: 50,
  searchCustomerOptions: vi.fn(async () => []),
  searchProductOptions: vi.fn(async () => []),
}));
vi.mock('../../hooks/useSalesAgentOptions', () => ({ useSalesAgentOptions: () => ({ options: [] }) }));

const getSalesOrderDeliveries = vi.fn();
vi.mock('../../../services/salesOrderService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getSalesOrderUoms: () => Promise.resolve([{ id: 'uom-pcs', uom_code: 'PCS', uom_name: 'Pieces' }]),
  getSalesOrderDeliveries: (...a: unknown[]) => getSalesOrderDeliveries(...a),
}));

import { SalesOrderDetail } from './SalesOrderDetail';
import type { SalesOrder, SalesOrderLine } from '../../../types/scm.types';

const LINE_A = '5b0e8c3a-1f2d-4e6a-9b7c-0d1e2f3a4b01';
const LINE_B = '5b0e8c3a-1f2d-4e6a-9b7c-0d1e2f3a4b02';

function order(): SalesOrder {
  return {
    id: 'so-1',
    so_number: 'SO419866',
    order_type: 'project',
    order_type_label: 'Project',
    demand_class: 'project',
    customer_code: '300-R009',
    customer_name: 'Rowenda Kitchen Sdn Bhd',
    market_segment: 'Project',
    priority: 'normal',
    status: 'open',
    order_date: '2026-09-04',
    requested_delivery_date: '2026-09-04',
    total_qty: 59,
    committed_qty: 59,
    total_amount: '1000.00',
    line_count: 2,
    open_line_count: 2,
    stock_locations: ['BRW'],
    source: 'manual',
    internal_note: null,
    lines: [
      {
        id: LINE_A,
        sku: 'WESERP10B',
        product_name: 'Wall Hung Basin',
        qty_ordered: 43,
        qty_delivered: 31,
        qty_cancelled: 0,
        outstanding_qty: 12,
        uom: 'PCS',
        unit_price: '10.00',
        discount: '0.00',
        line_total: '430.00',
        warehouse_code: 'BRW',
        line_status: 'open',
        required_date: '2026-09-04',
      },
      {
        id: LINE_B,
        sku: 'SRTWC8518-SH-200',
        product_name: 'Close Coupled WC',
        qty_ordered: 16,
        qty_delivered: 11,
        qty_cancelled: 0,
        outstanding_qty: 5,
        uom: 'PCS',
        unit_price: '10.00',
        discount: '0.00',
        line_total: '160.00',
        warehouse_code: 'BRW',
        line_status: 'open',
        required_date: '2026-09-04',
      },
    ],
    created_at: '2026-09-04T02:00:00',
  } as SalesOrder;
}

const DELIVERIES = {
  lines: [
    {
      line_id: LINE_A,
      deliveries: [
        {
          order_id: '77aa0001-0000-4000-8000-000000000001',
          do_number: '202609-1082',
          do_date: '2026-09-07',
          location: 'BRW',
          status: 'Picked Up / In Transit',
          actual_delivery_date: '2026-09-07',
          qty: 8,
        },
      ],
      cancels: [],
    },
    { line_id: LINE_B, deliveries: [], cancels: [] },
  ],
  groups: [],
};

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SalesOrderDetail id="so-1" />
    </QueryClientProvider>,
  );
}

function openLines() {
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Lines' }), { button: 0, ctrlKey: false });
}

function clickEdit() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Sales order options' }), {
    button: 0,
    ctrlKey: false,
    pointerType: 'mouse',
  });
  fireEvent.click(screen.getByRole('menuitem', { name: /^Edit$/ }));
}

beforeEach(() => {
  cleanup();
  useSalesOrder.mockReset().mockReturnValue({ data: order(), isLoading: false, isError: false });
  getSalesOrderDeliveries.mockReset().mockResolvedValue(DELIVERIES);
  searchParams = new URLSearchParams();
});

describe('SalesOrderDetail - the truck button (AC-SD20)', () => {
  it('gives every line an icon-only "Deliveries for <SKU>" button in the first column', () => {
    renderDetail();
    openLines();

    const first = screen.getByRole('button', { name: 'Deliveries for WESERP10B' });
    const second = screen.getByRole('button', { name: 'Deliveries for SRTWC8518-SH-200' });
    expect(first).toHaveAttribute('title', 'Deliveries for WESERP10B');
    expect(second).toBeInTheDocument();
    expect((first.textContent ?? '').trim()).toBe('');
    // first cell of its row, under an empty header
    const row = first.closest('tr') as HTMLElement;
    expect(row.querySelectorAll('td')[0].contains(first)).toBe(true);
    expect((screen.getAllByRole('columnheader')[0].textContent ?? '').trim()).toBe('');
  });

  it('keeps the button in edit mode', () => {
    renderDetail();
    clickEdit();
    openLines();

    expect(screen.getByRole('button', { name: 'Deliveries for WESERP10B' })).toBeInTheDocument();
  });

  it('opens "Deliveries · <SKU>" with the line figures, and Escape closes it', async () => {
    renderDetail();
    openLines();

    fireEvent.click(screen.getByRole('button', { name: 'Deliveries for WESERP10B' }));

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('Deliveries · WESERP10B')).toBeInTheDocument();
    for (const label of ['Ordered', 'Delivered', 'Cancelled', 'Outstanding', 'Location', 'Delivery date']) {
      expect(dialog.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(await dialog.findByText('202609-1082')).toBeInTheDocument();

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

describe('SalesOrderDetail - one fetch (AC-SD21)', () => {
  it('does not fetch until a lightbox opens', () => {
    renderDetail();
    openLines();

    expect(screen.getByRole('button', { name: 'Deliveries for WESERP10B' })).toBeInTheDocument();
    expect(getSalesOrderDeliveries).not.toHaveBeenCalled();
  });

  it('fetches once for the SO, however many lines are opened', async () => {
    renderDetail();
    openLines();

    fireEvent.click(screen.getByRole('button', { name: 'Deliveries for WESERP10B' }));
    await waitFor(() => expect(getSalesOrderDeliveries).toHaveBeenCalledTimes(1));
    expect(getSalesOrderDeliveries).toHaveBeenCalledWith('so-1');
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: 'Deliveries for SRTWC8518-SH-200' }));
    expect(await screen.findByText('Deliveries · SRTWC8518-SH-200')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: 'Deliveries for WESERP10B' }));
    expect(await screen.findByText('Deliveries · WESERP10B')).toBeInTheDocument();

    expect(getSalesOrderDeliveries).toHaveBeenCalledTimes(1);
  });
});
