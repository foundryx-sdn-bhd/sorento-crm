/**
 * ConsignmentsList - AC-CONSIGN-CRM UAC-20: a plain DataGrid list of consignment orders only:
 * number (link to /scm/consignments/<id>), date, customer, status, qty ordered / delivered /
 * returned as sums of the order's lines. Asks the hook for docType 'consignment'. No Add.
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

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), custom: vi.fn() },
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

let granted = new Set<string>(['projects.projects.view', 'scm.reorder.run']);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => granted.has(slug),
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
const resetPlanning = vi.fn(async () => ({ so_number: 'ZZT-CG-0001', planned: true, removed: {} }));
vi.mock('../../hooks/useSalesOrders', () => ({
  useSalesOrders: (...a: unknown[]) => useSalesOrders(...a),
  useResetSalesOrderPlanning: () => ({ mutateAsync: resetPlanning, isPending: false }),
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

beforeEach(() => {
  useSalesOrders.mockReset();
  push.mockReset();
  resetPlanning.mockClear();
  granted = new Set<string>(['projects.projects.view', 'scm.reorder.run']);
});

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
    // CONSIGN-DELIVERED-FIX: worded by where the goods are, never Outstanding / Completed.
    expect(within(row).getByText('At customer')).toBeInTheDocument();
    expect(within(row).queryByText('Outstanding')).toBeNull();
    const second = screen.getByRole('link', { name: 'ZZT-CG-0002' }).closest('tr') as HTMLElement;
    expect(within(second).getByText('At customer')).toBeInTheDocument();
  });

  it('status reads Fully returned when every unit came back, and Cancelled stays Cancelled', async () => {
    useSalesOrders.mockReturnValue({
      data: {
        data: [
          { ...ROWS[1], id: 'cg-3', so_number: 'ZZT-CG-0003', lines: [line({ qty_ordered: 2, qty_delivered: 2, qty_returned: 2 })] },
          { ...ROWS[1], id: 'cg-4', so_number: 'ZZT-CG-0004', status: 'cancelled' },
        ],
        pagination: { total: 2, page: 1, limit: 25 },
        empty: false,
      },
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ConsignmentsList />
      </QueryClientProvider>,
    );
    const back = (await screen.findByText('ZZT-CG-0003')).closest('tr') as HTMLElement;
    expect(within(back).getByText('Fully returned')).toBeInTheDocument();
    const voided = screen.getByText('ZZT-CG-0004').closest('tr') as HTMLElement;
    expect(within(voided).getByText('Cancelled')).toBeInTheDocument();
  });

  it('At customer is ordered less returned, after the three qty columns', async () => {
    renderList();
    const second = (await screen.findByText('ZZT-CG-0002')).closest('tr') as HTMLElement;
    const c2 = within(second).getAllByRole('cell').map((c) => c.textContent?.trim());
    const at2 = c2.indexOf('5');
    // 5 ordered, 5 delivered, 2 returned, 3 at the customer
    expect(c2.slice(at2, at2 + 4)).toEqual(['5', '5', '2', '3']);
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

  it('the Customer header is not sortable (the backend has no customer_name sort key)', async () => {
    renderList();
    await screen.findByText('ZZT-CG-0001');
    const header = screen.getAllByRole('columnheader').find((h) => /^Customer$/i.test(h.textContent?.trim() ?? ''));
    expect(header).toBeDefined();
    const sortButtons = within(header as HTMLElement)
      .queryAllByRole('button')
      .filter((b) => !/drag/i.test(b.getAttribute('aria-label') ?? b.textContent ?? ''));
    expect(sortButtons).toEqual([]);
    expect((header as HTMLElement).getAttribute('aria-sort')).toBeNull();
  });

  it('has no Add button (consignments come only from AutoCount)', async () => {
    renderList();
    await screen.findByText('ZZT-CG-0001');
    expect(screen.queryByRole('button', { name: /^add/i })).toBeNull();
  });
});

// CONSIGN-PLAN UAC-10: the list gets the sales order list's planning buttons.
describe('CONSIGN-PLAN UAC-10: Plan selected', () => {
  it('rows have a select checkbox and Plan selected (0) is offered disabled', async () => {
    renderList();
    expect(await screen.findByLabelText('Select ZZT-CG-0001')).toBeInTheDocument();
    expect(screen.getByLabelText('Select ZZT-CG-0002')).toBeInTheDocument();
    const button = await screen.findByRole('button', { name: /^Plan selected \(0\)$/ });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(push).not.toHaveBeenCalled();
  });

  it('opens the board on the ticked consignments, by document number', async () => {
    renderList();
    fireEvent.click(await screen.findByLabelText('Select ZZT-CG-0001'));
    fireEvent.click(await screen.findByLabelText('Select ZZT-CG-0002'));
    fireEvent.click(await screen.findByRole('button', { name: /^Plan selected \(2\)$/ }));
    expect(push).toHaveBeenCalledWith(
      '/project-sales/fulfilment-planning?orders=' +
        encodeURIComponent('ZZT-CG-0001,ZZT-CG-0002'),
    );
  });

  it('offers no Plan selected without projects.projects.view', async () => {
    granted = new Set<string>(['scm.reorder.run']);
    renderList();
    await screen.findByText('ZZT-CG-0001');
    expect(screen.queryByRole('button', { name: /^Plan selected/ })).toBeNull();
  });
});

// CONSIGN-PLAN UAC-11: the Actions menu is housekeeping only.
describe('CONSIGN-PLAN UAC-11: Actions menu', () => {
  async function openActions() {
    const trigger = await screen.findByRole('button', { name: 'Actions' });
    fireEvent.keyDown(trigger, { key: 'Enter' });
  }

  it('holds Reset planning (N) and Refresh, and never Upload or Add', async () => {
    renderList();
    await openActions();
    const reset = await screen.findByRole('menuitem', { name: /^Reset planning \(0\)$/ });
    expect(reset).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('menuitem', { name: 'Refresh' })).toBeInTheDocument();
    const names = screen.getAllByRole('menuitem').map((m) => m.textContent ?? '');
    expect(names.filter((n) => /upload|add/i.test(n))).toEqual([]);
  });

  it('Reset planning (1) confirms and resets the ticked consignment', async () => {
    renderList();
    fireEvent.click(await screen.findByLabelText('Select ZZT-CG-0001'));
    await openActions();
    fireEvent.click(await screen.findByRole('menuitem', { name: /^Reset planning \(1\)$/ }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('ZZT-CG-0001');
    fireEvent.click(screen.getByRole('button', { name: 'Reset planning' }));
    await waitFor(() => expect(resetPlanning).toHaveBeenCalledWith({ id: 'cg-1', rewindBook: false }));
  });

  it('has no Reset planning without scm.reorder.run, but keeps Refresh', async () => {
    granted = new Set<string>(['projects.projects.view']);
    renderList();
    await openActions();
    expect(await screen.findByRole('menuitem', { name: 'Refresh' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /Reset planning/ })).toBeNull();
  });
});
