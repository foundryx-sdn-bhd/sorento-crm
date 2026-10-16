/**
 * RED - SF-SALES-TYPE reviewer finding: editing an EXISTING sponsorship form whose sales_type is
 * null must not get Project from the binding default. The edit card mounts before the form's
 * reset effect, so when the binding's lookup data arrives AFTER the reset, LookupBoundField
 * (not mocked here) wrongly treats the field as a new form and applies default_value.
 * Harness: PurchaseRequestDocumentEditCard.salesType.test.tsx, but the lookup hook is real-shaped
 * and held in "loading" until the form has been reset.
 */
import React from 'react';
import { it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/procurement-management/sponsorship-forms/req-1/edit',
}));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    apiFetch: vi.fn(
      async () => ({ ok: true, status: 200, json: async () => [] }) as unknown as Response,
    ),
  };
});

// Lookup data is held back until release(); every subscribed component re-renders then.
const lookup = vi.hoisted(() => ({
  released: false,
  listeners: new Set<() => void>(),
  release() {
    this.released = true;
    this.listeners.forEach((l) => l());
  },
}));
const SALES_TYPE_DATA = {
  set_key: 'procurement_sales_type',
  set_name: 'Sales Type',
  options: [
    { value: 'project', label: 'Project', keywords: [], is_active: true },
    { value: 'cash_sales', label: 'Cash Sales', keywords: [], is_active: true },
  ],
  default_value: 'project',
};
vi.mock('@/hooks/useLookupOptionsByBinding', async () => {
  const React = await import('react');
  return {
    useLookupOptionsByBinding: (_table: string, column: string) => {
      const [, force] = React.useReducer((n: number) => n + 1, 0);
      React.useEffect(() => {
        lookup.listeners.add(force);
        return () => {
          lookup.listeners.delete(force);
        };
      }, []);
      if (column !== 'sales_type') return { data: undefined, isLoading: false };
      return lookup.released
        ? { data: SALES_TYPE_DATA, isLoading: false }
        : { data: undefined, isLoading: true };
    },
  };
});

const updateMutateAsync = vi.fn().mockResolvedValue({});
vi.mock('../hooks/usePurchaseRequests', () => ({
  usePurchaseRequest: () => ({
    data: {
      id: 'req-1',
      request_type: 'sponsorship_form',
      request_number: 'PSSF-0001',
      customer_name: 'CUSTOMER A SDN BHD',
      customer_id: 'cust-A',
      sales_type: null,
      project_title: 'Tower A',
      lines: [{ item_code: 'ITEM-A', quantity: 2, remark: null, unit_price: 15, total: 30 }],
    },
    isLoading: false,
  }),
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
vi.mock(
  '@/app/(protected)/master-data-management/shared/components/RequestorContactSelect',
  () => ({ RequestorContactSelect: () => null }),
);
vi.mock('./PurchaseRequestAttachmentsSection', () => ({ __esModule: true, default: () => null }));

import PurchaseRequestForm from './PurchaseRequestForm';

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  lookup.released = false;
  lookup.listeners.clear();
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

it('SF edit with a null sales_type: lookup data arriving after the form reset does not apply Project', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PurchaseRequestForm requestId="req-1" expectedRequestType="sponsorship_form" />
    </QueryClientProvider>,
  );
  // The form has loaded the record and reset (customer name is filled) while the lookup is still loading.
  await waitFor(() => {
    const el = document.querySelector('input[value="CUSTOMER A SDN BHD"], textarea');
    if (!el) throw new Error('form not reset yet');
  });
  await act(async () => {
    lookup.release();
  });

  fireEvent.click(await screen.findByRole('button', { name: /^Update$/ }));
  await waitFor(() => expect(updateMutateAsync).toHaveBeenCalled(), { timeout: 3000 });
  const data = updateMutateAsync.mock.calls[0][0].data as Record<string, unknown>;

  expect(data.sales_type ?? null).toBeNull();
  const caption = screen.getByText(/^Sales Type/);
  expect(caption.parentElement as HTMLElement).not.toHaveTextContent('Project');
});
