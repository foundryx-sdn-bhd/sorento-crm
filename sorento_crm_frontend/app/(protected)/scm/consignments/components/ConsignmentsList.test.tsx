/**
 * ConsignmentsList - AC-CONSIGN-CRM UAC-20: a plain DataGrid list of consignment orders only:
 * number (link to /scm/consignments/<id>), date, customer, status, qty ordered / delivered /
 * returned as sums of the order's lines. Asks the hook for docType 'consignment'. No Add.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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
  usePathname: () => '/scm/consignments',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(async () => ({ listing_key: '/scm/consignments', config: null })),
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

const useSalesOrders = vi.fn();
vi.mock('../../hooks/useSalesOrders', () => ({
  useSalesOrders: (...a: unknown[]) => useSalesOrders(...a),
  consignmentsPagerQuery: {
    listQueryKey: () => ['scm-consignments'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
}));

import { ConsignmentsList } from './ConsignmentsList';
import { fmtDate } from '../../lib/format';

type Row = Record<string, unknown>;

function line(over: Row): Row {
  return { id: `l-${Math.random()}`, sku: 'SKU', product_name: 'P', uom: 'PCS', ...over };
}

const ROWS: Row[] = [
  {
    id: 'cg-1',
    so_number: 'ZZT-CG-0001',
    doc_type: 'consignment',
    customer_name: 'ZZT CONSIGN CUSTOMER A',
    status: 'open',
    order_date: '2026-06-10',
    lines: [
      line({ qty_ordered: 1, qty_delivered: 0, qty_returned: 0 }),
      line({ qty_ordered: 1, qty_delivered: 1, qty_returned: 1 }),
      line({ qty_ordered: 1, qty_delivered: 1, qty_returned: 1 }),
    ],
  },
  {
    id: 'cg-2',
    so_number: 'ZZT-CG-0002',
    doc_type: 'consignment',
    customer_name: 'ZZT CONSIGN CUSTOMER B',
    status: 'closed',
    order_date: '2024-10-05',
    lines: [
      line({ qty_ordered: 4, qty_delivered: 4, qty_returned: 2 }),
      line({ qty_ordered: 1, qty_delivered: 1, qty_returned: 0 }),
    ],
  },
];

function renderList() {
  useSalesOrders.mockReturnValue({
    data: { data: ROWS, pagination: { total: 2, page: 1, limit: 25 }, empty: false },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConsignmentsList />
    </QueryClientProvider>,
  );
}

beforeEach(() => useSalesOrders.mockReset());

describe('UAC-20: ConsignmentsList', () => {
  it('asks the hook for consignments only', async () => {
    renderList();
    await screen.findByText('ZZT-CG-0001');
    expect(useSalesOrders).toHaveBeenCalledWith(expect.objectContaining({ docType: 'consignment' }));
  });

  it('a row has the number as a link to its consignment page, date, customer and status', async () => {
    renderList();
    const link = await screen.findByRole('link', { name: 'ZZT-CG-0001' });
    expect(link.getAttribute('href')).toMatch(/^\/scm\/consignments\/cg-1(\?|$)/);
    const row = link.closest('tr') as HTMLElement;
    expect(within(row).getByText(fmtDate('2026-06-10'))).toBeInTheDocument();
    expect(within(row).getByText('ZZT CONSIGN CUSTOMER A')).toBeInTheDocument();
    expect(within(row).getByText('Outstanding')).toBeInTheDocument();
    const second = screen.getByRole('link', { name: 'ZZT-CG-0002' }).closest('tr') as HTMLElement;
    expect(within(second).getByText('Completed')).toBeInTheDocument();
  });

  it('qty ordered / delivered / returned are the sums of the order lines', async () => {
    renderList();
    const row = (await screen.findByText('ZZT-CG-0001')).closest('tr') as HTMLElement;
    const cells = within(row).getAllByRole('cell').map((c) => c.textContent?.trim());
    // 3 ordered, 2 delivered, 2 returned, in that column order
    const at = cells.indexOf('3');
    expect(at).toBeGreaterThanOrEqual(0);
    expect(cells.slice(at, at + 3)).toEqual(['3', '2', '2']);
    const second = (await screen.findByText('ZZT-CG-0002')).closest('tr') as HTMLElement;
    const c2 = within(second).getAllByRole('cell').map((c) => c.textContent?.trim());
    const at2 = c2.indexOf('5');
    expect(c2.slice(at2, at2 + 3)).toEqual(['5', '5', '2']);
  });

  it('has no Add button (consignments come only from AutoCount)', async () => {
    renderList();
    await screen.findByText('ZZT-CG-0001');
    expect(screen.queryByRole('button', { name: /^add/i })).toBeNull();
  });
});
