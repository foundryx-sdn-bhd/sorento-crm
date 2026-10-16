/**
 * RED tests - FORM-ROUTING-CONDITIONS PC-6 on the EDIT screen (PurchaseRequestDocumentEditCard).
 *
 * Customer Name is the customer search there too: a pick sets customer_id (+ Sales type on a PR
 * from the item's sales_type), free text clears customer_id, and saving without touching the
 * customer keeps the link. Driven through PurchaseRequestForm in edit mode; the lookup goes
 * through `@/lib/api` apiFetch so the test does not depend on the picker's wiring.
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

const ITEMS = [
  {
    customer_id: 'cust-B',
    debtor_name: 'CUSTOMER B SDN BHD',
    customer_code: '302-ZZT002',
    debtor_type: 'project_cash',
    sales_type: 'cash_sales',
  },
];
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    apiFetch: vi.fn(async (url: string) => {
      const ok = (body: unknown) =>
        ({ ok: true, status: 200, json: async () => body }) as unknown as Response;
      return ok(String(url).includes('/lookups/customers') ? ITEMS : []);
    }),
  };
});

const updateMutateAsync = vi.fn().mockResolvedValue({});
let mockRequest: unknown = undefined;
vi.mock('../hooks/usePurchaseRequests', () => ({
  usePurchaseRequest: () => ({ data: mockRequest, isLoading: false }),
  useCreatePurchaseRequest: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdatePurchaseRequest: () => ({ mutateAsync: updateMutateAsync, isPending: false }),
  useUpdatePurchaseRequestAndReply: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('../services/purchaseRequestService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/purchaseRequestService')>()),
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

type Kind = 'purchase_request' | 'sponsorship_form';

function loaded(kind: Kind) {
  return {
    id: 'req-1',
    request_type: kind,
    request_number: 'PR-0001',
    customer_name: 'CUSTOMER A SDN BHD',
    customer_id: 'cust-A',
    sales_type: kind === 'purchase_request' ? 'project' : null,
    project_title: 'Tower A',
    lines: [{ item_code: 'ITEM-A', quantity: 2, remark: null, unit_price: 15, total: 30 }],
  };
}

function renderEdit(kind: Kind) {
  mockRequest = loaded(kind);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PurchaseRequestForm requestId="req-1" expectedRequestType={kind} />
    </QueryClientProvider>,
  );
}

/** The edit card labels its fields with a DocField caption, not a <label for>. */
async function customerInput(): Promise<HTMLInputElement | HTMLTextAreaElement> {
  const caption = await screen.findByText('Customer Name');
  const box = caption.parentElement as HTMLElement;
  return (await waitFor(() => {
    const el = box.querySelector('input, textarea');
    if (!el) throw new Error('no customer input');
    return el;
  })) as HTMLInputElement | HTMLTextAreaElement;
}

async function save(): Promise<Record<string, unknown>> {
  fireEvent.click(await screen.findByRole('button', { name: /^Update$/ }));
  await waitFor(() => expect(updateMutateAsync).toHaveBeenCalled(), { timeout: 3000 });
  return updateMutateAsync.mock.calls[0][0].data as Record<string, unknown>;
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe.each<Kind>(['purchase_request', 'sponsorship_form'])('edit %s customer picker', (kind) => {
  it('free text replaces the name and sends customer_id null', async () => {
    renderEdit(kind);
    const input = await customerInput();

    fireEvent.change(input, { target: { value: 'WALK-IN BUYER' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    fireEvent.keyDown(input, { key: 'Enter' });
    const data = await save();

    expect(data.customer_name).toBe('WALK-IN BUYER');
    expect(data.customer_id).toBeNull();
  });

  it('saving without touching the customer keeps customer_id', async () => {
    renderEdit(kind);
    await customerInput();

    const data = await save();

    expect(data.customer_id).toBe('cust-A');
    expect(data.customer_name).toBe('CUSTOMER A SDN BHD');
  });
});

it('edit PR: picking customer B sends customer_id cust-B and sales_type cash_sales', async () => {
  renderEdit('purchase_request');
  const input = await customerInput();

  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'ZZT' } });
  fireEvent.click(await screen.findByRole('option', { name: /CUSTOMER B SDN BHD/ }));
  const data = await save();

  expect(data.customer_id).toBe('cust-B');
  expect(data.customer_name).toBe('CUSTOMER B SDN BHD');
  expect(data.sales_type).toBe('cash_sales');
});
