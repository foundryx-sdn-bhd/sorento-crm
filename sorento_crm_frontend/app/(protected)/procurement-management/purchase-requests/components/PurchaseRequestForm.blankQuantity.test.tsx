/**
 * NUMBER-FIELDS fix round 1 (finding D): a purchase request line Quantity is required. Blank
 * -> nothing is sent, the box is aria-invalid with a sentence under it, the form stays open.
 * Browser pass saw PR26-0385 Edit save quantity NULL on a cleared box.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/procurement-management/sponsorship-forms/new',
}));

const createMutateAsync = vi.fn().mockResolvedValue({});
const updateMutateAsync = vi.fn().mockResolvedValue({});
const updateAndReplyMutateAsync = vi.fn().mockResolvedValue({});
let mockRequest: unknown = undefined;
vi.mock('../hooks/usePurchaseRequests', () => ({
  usePurchaseRequest: () => ({ data: mockRequest, isLoading: false }),
  useCreatePurchaseRequest: () => ({ mutateAsync: createMutateAsync, isPending: false }),
  useUpdatePurchaseRequest: () => ({ mutateAsync: updateMutateAsync, isPending: false }),
  useUpdatePurchaseRequestAndReply: () => ({
    mutateAsync: updateAndReplyMutateAsync,
    isPending: false,
  }),
}));

vi.mock('../services/purchaseRequestService', () => ({
  searchProjectsForLink: vi.fn().mockResolvedValue([]),
  getOrCreateViewLink: vi.fn(),
}));

vi.mock('@/hooks/usePublicViewLinksEnabled', () => ({
  usePublicViewLinksEnabled: () => false,
}));

vi.mock('@/components/common/LookupBoundField', () => ({
  __esModule: true,
  default: () => null,
}));

vi.mock(
  '@/app/(protected)/master-data-management/shared/components/RequestorContactSelect',
  () => ({ RequestorContactSelect: () => null }),
);

vi.mock('./PurchaseRequestAttachmentsSection', () => ({
  __esModule: true,
  default: () => null,
}));

import PurchaseRequestForm from './PurchaseRequestForm';



const MESSAGE = /^Enter a (whole )?number/;
const LINE = { item_code: 'ITEM-A', quantity: 2, remark: null, unit_price: null, total: null };

function renderForm(edit: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      {edit ? (
        <PurchaseRequestForm requestId="req-1" expectedRequestType="purchase_request" />
      ) : (
        <PurchaseRequestForm />
      )}
    </QueryClientProvider>,
  );
}

function qtyBox(): HTMLInputElement {
  return document.querySelector('input[name="products.0.quantity"]') as HTMLInputElement;
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockRequest = undefined;
});

describe('PurchaseRequestForm - a blank line Quantity is refused', () => {
  it('edit: Update with the Quantity cleared sends nothing and says why under the box', async () => {
    mockRequest = {
      id: 'req-1',
      request_number: 'PR-0001',
      request_type: 'purchase_request',
      customer_name: 'CUSTOMER A SDN BHD',
      project_title: 'Tower A',
      lines: [LINE],
    };
    renderForm(true);
    await screen.findByRole('button', { name: /^Update$/ });
    fireEvent.change(qtyBox(), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^Update$/ }));

    expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
    expect(updateMutateAsync).not.toHaveBeenCalled();
    expect(qtyBox()).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: /^Update$/ })).toBeInTheDocument();
  });

  it('create: Create with an item code but no Quantity sends nothing and says why', async () => {
    renderForm(false);
    fireEvent.change(screen.getByPlaceholderText('Item code'), { target: { value: 'ITEM-A' } });
    fireEvent.click(screen.getByRole('button', { name: /^Create$/ }));

    expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
    expect(createMutateAsync).not.toHaveBeenCalled();
    expect(qtyBox()).toHaveAttribute('aria-invalid', 'true');
  });
});
