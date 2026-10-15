/**
 * AC-CONSIGN-CRM UAC-06: the Order Inquiry header list (Documents view) shows "Consignment"
 * in the S/O no cell when the header's `so_doc_type` is consignment.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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

vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/order-inquiries',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));
vi.mock('@/services/userSelectService', () => ({
  getUserLookup: vi.fn(async () => []),
}));

vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => false }));

let headers: Array<Record<string, unknown>> = [];
vi.mock('../../_shared/hooks/useOrderInquiry', () => ({
  useOrderInquiryHeaders: () => ({
    data: { data: headers, total: headers.length },
    isLoading: false,
    isPlaceholderData: false,
    isFetching: false,
    refetch: vi.fn(),
    error: null,
  }),
  useOrderInquiryWorklistSummary: () => ({ data: { agents: [], projects: [] } }),
  // OI-STAGED-NOTIFY: harness only, the list now reads these (no assertion changed).
  useReserveStatusSummary: () => ({ data: undefined }),
  useParkedPreview: () => ({ data: undefined, isLoading: false }),
  useSendParked: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // OI-RESEND-PURCHASING: harness only (no assertion changed).
  useHandoverResendPreview: () => ({ data: undefined, isLoading: false, error: null }),
  useSendHandoverResend: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { OrderInquiryHeadersList } from './OrderInquiryHeadersList';

function header(over: Record<string, unknown>) {
  return {
    id: 'h-1',
    inquiry_no: 'OI-0001',
    so_number: 'CG000123',
    sales_order_id: 'so-1',
    status: 'open',
    raised_at: '2026-10-01T00:00:00',
    ...over,
  };
}

function renderList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OrderInquiryHeadersList />
    </QueryClientProvider>,
  );
}

describe('UAC-06: S/O no cell carries the Consignment label', () => {
  it('a consignment header shows Consignment', () => {
    headers = [header({ so_doc_type: 'consignment' })];
    renderList();
    const row = screen.getByText('CG000123').closest('tr') as HTMLElement;
    expect(within(row).getByText('Consignment')).toBeInTheDocument();
  });

  it('a sales_order header shows no Consignment label', () => {
    headers = [header({ so_number: 'SO000123', so_doc_type: 'sales_order' })];
    renderList();
    const row = screen.getByText('SO000123').closest('tr') as HTMLElement;
    expect(within(row).queryByText('Consignment')).toBeNull();
  });
});
