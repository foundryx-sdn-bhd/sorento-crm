/**
 * NUMBER-FIELDS fix round 1b (item 13b, S8): a purchase request line Quantity is required. Blank
 * -> nothing is sent, the box is aria-invalid with a sentence under it, the form stays open.
 * Browser pass saw PR26-0385 Edit save quantity NULL on a cleared box.
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


describe('PurchaseRequestForm - round 1b', () => {
  it('13b: a loaded line with a NULL quantity shows the sentence on load and Update stays blocked', async () => {
    mockRequest = {
      id: 'req-1',
      request_number: 'PR-0002',
      request_type: 'purchase_request',
      sales_type: 'project',
      customer_name: 'CUSTOMER A SDN BHD',
      project_title: 'Tower A',
      lines: [{ ...LINE, quantity: null }],
    };
    renderForm(true);
    await screen.findByRole('button', { name: /^Update$/ });
    // The load-time sentence comes from an async form.trigger() in the load effect, so it can
    // land after the Update button: wait for it rather than reading the DOM once.
    expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
    expect(qtyBox()).toHaveAttribute('aria-invalid', 'true');
    fireEvent.click(screen.getByRole('button', { name: /^Update$/ }));
    expect(updateMutateAsync).not.toHaveBeenCalled();
    expect(updateAndReplyMutateAsync).not.toHaveBeenCalled();
    fireEvent.change(qtyBox(), { target: { value: '3' } });
    await waitFor(() => expect(screen.queryByText(MESSAGE)).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: /^Update$/ }));
    await waitFor(() => expect(updateMutateAsync).toHaveBeenCalled());
  });

  it('S8: clearing qty or the unit price clears the derived sponsorship total', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <PurchaseRequestForm defaultRequestType="sponsorship_form" />
      </QueryClientProvider>,
    );
    const box = (f: string) =>
      container.querySelector(`input[name="products.0.${f}"]`) as HTMLInputElement;
    fireEvent.change(box('quantity'), { target: { value: '5' } });
    fireEvent.change(box('unit_price'), { target: { value: '10' } });
    expect(box('total').value).toBe('50');
    fireEvent.change(box('quantity'), { target: { value: '' } });
    expect(box('total').value).toBe('');
    fireEvent.change(box('quantity'), { target: { value: '5' } });
    expect(box('total').value).toBe('50');
    fireEvent.change(box('unit_price'), { target: { value: '' } });
    expect(box('total').value).toBe('');
  });
});

describe('PurchaseRequestForm - round 2b narrow cell', () => {
  it('the line Quantity sentence wraps under the box (not clipped by the cell)', async () => {
    mockRequest = {
      id: 'req-1',
      request_number: 'PR-0002',
      request_type: 'purchase_request',
      sales_type: 'project',
      customer_name: 'CUSTOMER A SDN BHD',
      project_title: 'Tower A',
      lines: [{ ...LINE, quantity: null }],
    };
    renderForm(true);
    await screen.findByRole('button', { name: /^Update$/ });
    const el = await screen.findByText(MESSAGE);
    expect(el).toHaveClass('whitespace-normal');
    expect(/break-words|wrap-break-word|\[overflow-wrap:anywhere\]/.test(el.className)).toBe(true);
  });
});
