/**
 * RED test - FORM-ROUTING-CONDITIONS NC-7 (PR list).
 *
 * A completed form (status `completed`, approval `approved`) reads "Completed" in the list's
 * status column, not "Approved". Guard row: an approved form still reads "Approved".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/procurement-management/purchase-requests',
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('@/services/userSelectService', () => ({ getUserLookup: vi.fn().mockResolvedValue([]) }));
vi.mock('@/components/common/LookupBoundLabel', () => ({
  default: ({ value }: { value: string }) => <span>{value}</span>,
}));

vi.mock('./PurchaseRequestBulkDeleteDialog', () => ({ __esModule: true, default: () => null }));

const usePurchaseRequestsMock = vi.fn();
vi.mock('../hooks/usePurchaseRequests', () => ({
  usePurchaseRequests: (...a: unknown[]) => usePurchaseRequestsMock(...a),
}));

import PurchaseRequestsList from './PurchaseRequestsList';

function row(over: Record<string, unknown>) {
  return {
    id: 'pr-1',
    request_type: 'sponsorship_form',
    request_number: 'PSSF26-0404',
    customer_name: 'CUSTOMER A',
    status: 'draft',
    approval_status: null,
    revision_no: 0,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...over,
  };
}

function renderList(rows: unknown[]) {
  usePurchaseRequestsMock.mockReturnValue({
    data: { data: rows, pagination: { total: rows.length, page: 1, limit: 50 } },
    isLoading: false,
    isPlaceholderData: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PurchaseRequestsList requestType="sponsorship_form" />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('PurchaseRequestsList status label', () => {
  it('guard: an approved form reads "Approved"', async () => {
    renderList([row({ status: 'approved', approval_status: 'approved' })]);
    expect(await screen.findByText('Approved', { selector: 'span' })).toBeInTheDocument();
  });

  it('a completed form reads "Completed", not "Approved"', async () => {
    renderList([row({ status: 'completed', approval_status: 'approved' })]);
    await screen.findByText('PSSF26-0404');
    expect(screen.getByText('Completed', { selector: 'span' })).toBeInTheDocument();
    expect(screen.queryByText('Approved', { selector: 'span' })).not.toBeInTheDocument();
  });
});
