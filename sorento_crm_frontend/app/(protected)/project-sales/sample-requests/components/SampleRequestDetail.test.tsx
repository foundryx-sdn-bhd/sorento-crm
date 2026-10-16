/**
 * Staff sample request detail: the expanded line stock, who sees which action, and the
 * debtor-code badge. Hooks, the pager and the shell are mocked; the page's own rules run.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { SampleRequestDetail as Detail } from '@/types/sampleRequest';

const state = vi.hoisted(() => ({
  perms: new Set<string>(),
  userId: 'u-owner',
  data: undefined as unknown,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
  usePathname: () => '/project-sales/sample-requests/r-1',
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => state.perms.has(slug),
}));
vi.mock('@/hooks/useEffectiveUserId', () => ({ useEffectiveUserId: () => state.userId }));
vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: () => ({ countdown: null, isBlocked: false, start: vi.fn() }),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/components/audit/AuditTrail', () => ({
  default: () => <div data-testid="audit-trail" />,
}));
vi.mock('@/components/common/DetailActions', () => ({
  default: ({ gear, primary }: { gear?: React.ReactNode; primary?: React.ReactNode }) => (
    <div>
      {gear}
      {primary}
    </div>
  ),
}));
vi.mock('@/components/common/DetailActionsMenu', () => ({
  DetailActionsMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenuItem: ({
    children,
    onSelect,
  }: {
    children: React.ReactNode;
    onSelect?: () => void;
  }) => <button onClick={() => onSelect?.()}>{children}</button>,
}));
vi.mock('../../_shared/components/OrderInquiryStockGrid', () => ({
  OrderInquiryStockGrid: ({ productId }: { productId: string }) => (
    <div data-testid="stock-grid">{productId}</div>
  ),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: () => Promise.resolve(),
    isLoading: false,
  }),
}));
vi.mock('../hooks/useSampleRequests', () => ({
  sampleRequestsPagerQuery: {},
  useSampleRequestQuery: () => ({
    data: state.data,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useSampleRequestMutations: () => ({
    submit: { mutate: vi.fn(), isPending: false },
    move: { mutateAsync: vi.fn(), isPending: false },
    saveInternal: { mutate: vi.fn(), isPending: false },
  }),
}));
vi.mock('../services/sampleRequestService', () => ({ downloadSampleRequestPdf: vi.fn() }));

import SampleRequestDetail from './SampleRequestDetail';

function Page() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <SampleRequestDetail requestId="r-1" />
    </QueryClientProvider>
  );
}

function detail(over: Partial<Detail> = {}): Detail {
  return {
    id: 'r-1',
    request_no: 'SR-000001',
    status: 'draft',
    status_label: 'Draft',
    source: 'staff',
    salesperson_id: 'u-owner',
    salesperson_name: 'Owner',
    customer_id: 'c-1',
    customer_code: 'D-001',
    customer_name: 'Acme Sdn Bhd',
    project_id: null,
    project_code: null,
    project_name: null,
    request_date: null,
    submitted_at: null,
    confirmed_at: null,
    delivered_at: null,
    return_due: null,
    cancelled_at: null,
    cancel_reason: null,
    consignment_no: null,
    po_no: null,
    created_at: '2026-10-10T01:00:00',
    updated_at: null,
    lines: [
      {
        id: 'l-1',
        line_no: 1,
        product_id: 'p-1',
        product_code: 'TILE-1',
        product_name: 'Tile One',
        brand_name: 'Brand',
        product_active: true,
        qty: 2,
        remark: null,
      },
    ],
    ...over,
  } as Detail;
}

beforeEach(() => {
  state.perms = new Set();
  state.userId = 'u-owner';
  state.data = detail();
});
afterEach(cleanup);

describe('SampleRequestDetail', () => {
  it('shows no quotation, alternative product or immediate arrangement', () => {
    render(<Page />);
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/quotation/i);
    expect(text).not.toMatch(/alternative/i);
    expect(text).not.toMatch(/immediate/i);
    expect(screen.getByText('Request Delivery Date')).toBeTruthy();
  });

  it('expands a line into the stock grid for its product', () => {
    render(<Page />);
    expect(screen.queryByTestId('stock-grid')).toBeNull();
    fireEvent.click(screen.getByLabelText('Stock for line 1'));
    expect(screen.getByTestId('stock-grid').textContent).toBe('p-1');
  });

  it('gives the owner with create Submit, Edit and Delete draft on a draft', () => {
    state.perms = new Set(['projects.sample_requests.create']);
    render(<Page />);
    expect(screen.getByText('Submit')).toBeTruthy();
    expect(screen.getByText('Edit')).toBeTruthy();
    expect(screen.getByText('Delete draft')).toBeTruthy();
  });

  it('hides Submit, Edit and Delete draft from a non-owner', () => {
    state.perms = new Set(['projects.sample_requests.create']);
    state.userId = 'u-other';
    render(<Page />);
    expect(screen.queryByText('Submit')).toBeNull();
    expect(screen.queryByText('Edit')).toBeNull();
    expect(screen.queryByText('Delete draft')).toBeNull();
  });

  it('offers Change to processing to a process holder only', () => {
    state.data = detail({ status: 'submitted', status_label: 'Submitted' });
    state.perms = new Set(['projects.sample_requests.process']);
    state.userId = 'u-coord';
    const { unmount } = render(<Page />);
    expect(screen.getByText('Change to processing')).toBeTruthy();
    unmount();

    state.perms = new Set(['projects.sample_requests.create']);
    state.userId = 'u-owner';
    render(<Page />);
    expect(screen.queryByText('Change to processing')).toBeNull();
    // the owner may still cancel their own submitted request
    expect(screen.getByText('Cancel request')).toBeTruthy();
  });

  it('badges a customer with no debtor code', () => {
    state.data = detail({ customer_id: null, customer_code: null, customer_name: 'Walk-in Co' });
    render(<Page />);
    expect(screen.getByText('No debtor code')).toBeTruthy();
  });

  it('does not badge a customer with a debtor code', () => {
    render(<Page />);
    expect(screen.queryByText('No debtor code')).toBeNull();
  });

  it('shows the audit trail only to a view holder', () => {
    const { unmount } = render(<Page />);
    expect(screen.queryByTestId('audit-trail')).toBeNull();
    unmount();
    state.perms = new Set(['projects.sample_requests.view']);
    render(<Page />);
    expect(screen.getByTestId('audit-trail')).toBeTruthy();
  });
});
