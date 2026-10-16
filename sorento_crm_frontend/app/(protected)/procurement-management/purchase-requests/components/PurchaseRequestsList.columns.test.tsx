import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Capture the columns the list hands to the table, so the list's real column gate is exercised.
const captured = vi.hoisted(() => ({ columns: [] as Array<Record<string, unknown>> }));
vi.mock('@tanstack/react-table', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-table')>();
  return {
    ...actual,
    useReactTable: (opts: { columns: Array<Record<string, unknown>> }) => {
      captured.columns = opts.columns;
      return actual.useReactTable(opts as never);
    },
  };
});
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/procurement-management/sponsorship-forms',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('../hooks/usePurchaseRequests', () => ({
  useBulkDeletePurchaseRequests: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePurchaseRequests: () => ({
    data: { data: [], pagination: { total: 0 } },
    isLoading: false,
    isPlaceholderData: false,
    refetch: vi.fn(),
    isFetching: false,
    error: null,
  }),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => false }));
vi.mock('@/hooks/useListStateFromUrl', () => ({ useListStateFromUrl: () => undefined }));
vi.mock('@/services/userSelectService', () => ({ getUserLookup: vi.fn().mockResolvedValue([]) }));

// Render DataGridColumnHeader as a plain element exposing its title so we can
// assert which header the variable column produces, without the full DataGrid.
vi.mock('@/components/ui/data-grid-column-header', () => ({
  DataGridColumnHeader: ({ title }: { title: string }) => <span>{title}</span>,
}));
// LookupBoundLabel pulls a react-query hook; stub it to its raw value.
vi.mock('@/components/common/LookupBoundLabel', () => ({
  default: ({ value }: { value: string }) => <span>{value}</span>,
}));

import PurchaseRequestsList, { purposeOrSponsorSubjectColumn } from './PurchaseRequestsList';
import type { PurchaseRequest } from '../types/purchaseRequest.types';

function renderHeader(column: ReturnType<typeof purposeOrSponsorSubjectColumn>) {
  const headerFn = column.header as (ctx: { column: unknown }) => React.ReactElement;
  render(headerFn({ column: {} }));
}

function renderCell(
  column: ReturnType<typeof purposeOrSponsorSubjectColumn>,
  row: Partial<PurchaseRequest>,
) {
  const cellFn = column.cell as (ctx: { row: { original: Partial<PurchaseRequest> } }) => React.ReactNode;
  render(<div>{cellFn({ row: { original: row } })}</div>);
}

describe('purposeOrSponsorSubjectColumn', () => {
  it('uses a Sponsor Subject column for sponsorship forms', () => {
    const col = purposeOrSponsorSubjectColumn('sponsorship_form');
    expect((col as { accessorKey?: string }).accessorKey).toBe('sponsor_subject');
    renderHeader(col);
    expect(screen.getByText('Sponsor Subject')).toBeInTheDocument();
  });

  it('renders the sponsor subject value plus the Others detail', () => {
    const col = purposeOrSponsorSubjectColumn('sponsorship_form');
    renderCell(col, { sponsor_subject: 'others', sponsor_subject_other: 'VIP booth' });
    expect(screen.getByText(/others/)).toBeInTheDocument();
    expect(screen.getByText(/: VIP booth/)).toBeInTheDocument();
  });

  it('uses a Purpose column for purchase requests', () => {
    const col = purposeOrSponsorSubjectColumn('purchase_request');
    expect((col as { accessorKey?: string }).accessorKey).toBe('purpose');
    renderHeader(col);
    expect(screen.getByText('Purpose')).toBeInTheDocument();
  });

  it('defaults to Purpose for the combined (unfiltered) list', () => {
    const col = purposeOrSponsorSubjectColumn(undefined);
    expect((col as { accessorKey?: string }).accessorKey).toBe('purpose');
    renderHeader(col);
    expect(screen.getByText('Purpose')).toBeInTheDocument();
  });
});

describe('PurchaseRequestsList columns (SF-SALES-TYPE)', () => {
  function columnsFor(requestType: 'sponsorship_form' | 'purchase_request') {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PurchaseRequestsList requestType={requestType} />
      </QueryClientProvider>,
    );
    return captured.columns;
  }

  it('the sponsorship form list has a Sales Type column', () => {
    const cols = columnsFor('sponsorship_form');
    const col = cols.find((c) => c.accessorKey === 'sales_type');
    expect(col).toBeDefined();
    expect((col!.meta as { headerTitle: string }).headerTitle).toBe('Sales Type');
  });

  it('the purchase request list still has a Sales Type column', () => {
    const cols = columnsFor('purchase_request');
    expect(cols.some((c) => c.accessorKey === 'sales_type')).toBe(true);
  });
});
