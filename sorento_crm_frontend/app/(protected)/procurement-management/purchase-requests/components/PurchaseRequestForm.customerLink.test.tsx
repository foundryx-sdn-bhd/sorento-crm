/**
 * RED test - FORM-ROUTING-CONDITIONS PC-6 (internal PurchaseRequestForm).
 *
 * "Customer Name" becomes the same customer search as the portal's (free text still allowed).
 * Picking a customer sets `customer_id` and, on a PR, Sales type from the item's `sales_type`.
 *
 * Mocks the HTTP seam (`@/lib/api` apiFetch) for GET .../lookups/customers so the test does
 * not depend on the FE service function name the coder picks. Harness: the sponsorship
 * unit-price test next to this file. If the form's wiring proves too heavy for this to go
 * green cleanly, say so rather than loosen it.
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
  usePathname: () => '/procurement-management/purchase-requests/new',
}));

const ITEMS = [
  {
    customer_id: 'cust-302',
    debtor_name: 'CUSTOMER A SDN BHD',
    customer_code: '302-ZZT001',
    debtor_type: 'project_cash',
    sales_type: 'cash_sales',
  },
  {
    customer_id: 'cust-301',
    debtor_name: 'CASH (PERSON A)',
    customer_code: '301-ZZTC01',
    debtor_type: null,
    sales_type: null,
  },
];

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    apiFetch: vi.fn(async (url: string) => {
      const ok = (body: unknown) =>
        ({ ok: true, status: 200, json: async () => body }) as unknown as Response;
      if (String(url).includes('/purchase-requests/lookups/customers')) return ok(ITEMS);
      return ok([]);
    }),
  };
});

const createMutateAsync = vi.fn().mockResolvedValue({ id: 'new-1' });
vi.mock('../hooks/usePurchaseRequests', () => ({
  usePurchaseRequest: () => ({ data: undefined, isLoading: false }),
  useCreatePurchaseRequest: () => ({ mutateAsync: createMutateAsync, isPending: false }),
  useUpdatePurchaseRequest: () => ({ mutateAsync: vi.fn(), isPending: false }),
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

function renderForm(kind: 'purchase_request' | 'sponsorship_form' = 'purchase_request') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PurchaseRequestForm defaultRequestType={kind} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

async function pickCustomer(label: RegExp) {
  const input = screen.getByLabelText('Customer Name');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'ZZT' } });
  fireEvent.click(await screen.findByRole('option', { name: label }));
}

describe('PurchaseRequestForm customer picker (PC-6)', () => {
  it('picking a customer submits its customer_id, name and the mapped sales_type', async () => {
    renderForm();

    await pickCustomer(/CUSTOMER A SDN BHD/);
    fireEvent.click(screen.getByRole('button', { name: /Create/i }));

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalled(), { timeout: 3000 });
    expect(createMutateAsync.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        customer_id: 'cust-302',
        customer_name: 'CUSTOMER A SDN BHD',
        sales_type: 'cash_sales',
      }),
    );
  });

  it('free text is still allowed and sends no customer_id', async () => {
    renderForm();

    fireEvent.change(screen.getByLabelText('Customer Name'), { target: { value: 'WALK-IN BUYER' } });
    fireEvent.click(screen.getByRole('button', { name: /Create/i }));

    // sales_type is required on a PR, so the form may refuse; either way no customer_id goes out.
    await new Promise((r) => setTimeout(r, 300));
    for (const [payload] of createMutateAsync.mock.calls) {
      expect((payload as { customer_id?: unknown }).customer_id ?? null).toBeNull();
    }
    expect((screen.getByLabelText('Customer Name') as HTMLInputElement).value).toBe('WALK-IN BUYER');
  });
});

describe('PurchaseRequestForm customer picker on a sponsorship form (SF-SALES-TYPE)', () => {
  function fillLine() {
    fireEvent.change(screen.getByPlaceholderText('Item code'), { target: { value: 'ITEM-A' } });
    const qty = document.querySelector('input[name$=".quantity"]') as HTMLInputElement | null;
    if (qty) fireEvent.change(qty, { target: { value: '2' } });
    const up = document.querySelector('input[name$=".unit_price"]') as HTMLInputElement | null;
    if (up) fireEvent.change(up, { target: { value: '10' } });
  }

  it('picking a customer submits its mapped sales_type', async () => {
    renderForm('sponsorship_form');
    await pickCustomer(/CUSTOMER A SDN BHD/);
    fillLine();
    fireEvent.click(screen.getByRole('button', { name: /Create/i }));

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalled(), { timeout: 3000 });
    expect(createMutateAsync.mock.calls[0][0]).toEqual(
      expect.objectContaining({ customer_id: 'cust-302', sales_type: 'cash_sales' }),
    );
  });

  it('a free-text customer with no sales type is not blocked and shows no sales_type error', async () => {
    renderForm('sponsorship_form');
    fireEvent.change(screen.getByLabelText('Customer Name'), { target: { value: 'WALK-IN BUYER' } });
    fillLine();
    fireEvent.click(screen.getByRole('button', { name: /Create/i }));

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalled(), { timeout: 3000 });
    const payload = createMutateAsync.mock.calls[0][0] as { sales_type?: unknown };
    expect(payload.sales_type ?? null).toBeNull();
    expect(screen.getByText(/^Sales Type/)).toBeInTheDocument();
  });
});
