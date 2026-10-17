/**
 * AC-FP-10: a saved project link shows again on the PR / SF edit form (#1689), and the
 * picker is the shared clearable "Project (optional)" one.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/procurement-management/purchase-requests/req-1/edit',
}));

let mockRequest: unknown = undefined;
const updateMutateAsync = vi.fn().mockResolvedValue({});
vi.mock('../hooks/usePurchaseRequests', () => ({
  usePurchaseRequest: () => ({ data: mockRequest, isLoading: false }),
  useCreatePurchaseRequest: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdatePurchaseRequest: () => ({ mutateAsync: updateMutateAsync, isPending: false }),
  useUpdatePurchaseRequestAndReply: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('../services/purchaseRequestService', () => ({
  searchProjectsForLink: vi.fn().mockResolvedValue([]),
  getOrCreateViewLink: vi.fn(),
}));
vi.mock('@/hooks/usePublicViewLinksEnabled', () => ({ usePublicViewLinksEnabled: () => false }));
vi.mock('@/components/common/LookupBoundField', () => ({ __esModule: true, default: () => null }));
vi.mock(
  '@/app/(protected)/master-data-management/shared/components/RequestorContactSelect',
  () => ({ RequestorContactSelect: () => null }),
);
vi.mock('./PurchaseRequestAttachmentsSection', () => ({ __esModule: true, default: () => null }));

import PurchaseRequestForm from './PurchaseRequestForm';

function renderEdit(type: 'purchase_request' | 'sponsorship_form' = 'purchase_request') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PurchaseRequestForm requestId="req-1" expectedRequestType={type} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  updateMutateAsync.mockClear();
  mockRequest = undefined;
});

describe('PurchaseRequestForm project picker', () => {
  it('shows the saved project label on edit, not the placeholder', () => {
    mockRequest = {
      id: 'req-1',
      request_type: 'purchase_request',
      project_id: '11111111-1111-4111-8111-111111111111',
      project_code: 'PRJ-000142',
      project_label: 'PRJ-000142 - Residensi Damai Phase 1',
      lines: [],
    };
    renderEdit();
    expect(screen.getByText('PRJ-000142 - Residensi Damai Phase 1')).toBeTruthy();
    expect(screen.queryByText('Search a project by code or title')).toBeNull();
  });

  it('is labelled Project (optional) with no explanation text', () => {
    mockRequest = { id: 'req-1', request_type: 'purchase_request', project_id: null, lines: [] };
    renderEdit();
    expect(screen.getByText('Project (optional)')).toBeTruthy();
    expect(screen.queryByText(/never registered/i)).toBeNull();
  });

  for (const type of ['purchase_request', 'sponsorship_form'] as const) {
    describe(type, () => {
      const base = {
        id: 'req-1',
        request_type: type,
        request_number: 'X-0001',
        sales_type: 'cash_sales',
        customer_name: 'CUSTOMER A',
        project_id: '11111111-1111-4111-8111-111111111111',
        project_label: 'PRJ-000142 - Residensi Damai Phase 1',
        lines: [{ item_code: 'ITEM-A', quantity: 1, remark: null, unit_price: 1, total: 1 }],
      };

      it('shows the saved label and saves the same project_id', async () => {
        mockRequest = base;
        renderEdit(type);
        expect(screen.getByText('PRJ-000142 - Residensi Damai Phase 1')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: /^Update$/ }));
        await waitFor(() => expect(updateMutateAsync).toHaveBeenCalled());
        expect(updateMutateAsync.mock.calls[0][0].data.project_id).toBe('11111111-1111-4111-8111-111111111111');
      });

      it('clearing the picker saves project_id null', async () => {
        mockRequest = base;
        renderEdit(type);
        fireEvent.pointerDown(screen.getByRole('button', { name: 'Clear selection' }));
        fireEvent.click(screen.getByRole('button', { name: /^Update$/ }));
        await waitFor(() => expect(updateMutateAsync).toHaveBeenCalled());
        expect(updateMutateAsync.mock.calls[0][0].data.project_id).toBeNull();
      });
    });
  }
});
