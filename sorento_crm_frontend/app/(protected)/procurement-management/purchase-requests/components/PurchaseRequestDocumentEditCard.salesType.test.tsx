/**
 * RED tests - FORM-ROUTING-CONDITIONS: the EDIT screen shows Sales Type for a PR (a customer
 * pick sets it, so it must be visible and changeable), never for an SF. The lookup-bound
 * select renders its fallback (Project / Cash Sales), as the create form does with no binding.
 * Harness: PurchaseRequestDocumentEditCard.customerLink.test.tsx.
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
vi.mock('@/components/common/LookupBoundField', () => ({
  __esModule: true,
  default: ({ renderFallback }: { renderFallback: () => React.ReactNode }) => <>{renderFallback()}</>,
}));
vi.mock(
  '@/app/(protected)/master-data-management/shared/components/RequestorContactSelect',
  () => ({ RequestorContactSelect: () => null }),
);
vi.mock('./PurchaseRequestAttachmentsSection', () => ({ __esModule: true, default: () => null }));

import PurchaseRequestForm from './PurchaseRequestForm';

type Kind = 'purchase_request' | 'sponsorship_form';

let salesOnSf = true;
function loaded(kind: Kind) {
  return {
    id: 'req-1',
    request_type: kind,
    request_number: 'PR-0001',
    customer_name: 'CUSTOMER A SDN BHD',
    customer_id: 'cust-A',
    sales_type: kind === 'purchase_request' || salesOnSf ? 'project' : null,
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

/** The combobox inside the "Sales Type" field. */
async function salesTypeSelect(): Promise<HTMLElement> {
  const caption = await screen.findByText(/^Sales Type/);
  let node: HTMLElement | null = caption.parentElement;
  while (node) {
    const box = node.querySelector('[role="combobox"]');
    if (box) return box as HTMLElement;
    node = node.parentElement;
  }
  throw new Error('no Sales Type select');
}

it('PR edit: a Sales Type select shows the loaded Project', async () => {
  renderEdit('purchase_request');
  const box = await salesTypeSelect();
  expect(box).toHaveTextContent('Project');
});

it('PR edit: picking a cash_sales customer changes the VISIBLE Sales Type, which can be set back and saved', async () => {
  renderEdit('purchase_request');
  const input = await customerInput();

  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'ZZT' } });
  fireEvent.click(await screen.findByRole('option', { name: /CUSTOMER B SDN BHD/ }));
  await waitFor(async () => expect(await salesTypeSelect()).toHaveTextContent('Cash Sales'));

  fireEvent.click(await salesTypeSelect());
  fireEvent.click(await screen.findByRole('option', { name: 'Project' }));
  const data = await save();

  expect(data.customer_id).toBe('cust-B');
  expect(data.sales_type).toBe('project');
});

it('SF edit: the Sales Type field shows the loaded value', async () => {
  salesOnSf = true;
  renderEdit('sponsorship_form');
  await customerInput();
  expect(await salesTypeSelect()).toHaveTextContent('Project');
});

it('SF edit: picking a cash_sales customer changes the visible Sales Type and saving sends it', async () => {
  salesOnSf = true;
  renderEdit('sponsorship_form');
  const input = await customerInput();

  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'ZZT' } });
  fireEvent.click(await screen.findByRole('option', { name: /CUSTOMER B SDN BHD/ }));
  await waitFor(async () => expect(await salesTypeSelect()).toHaveTextContent('Cash Sales'));
  const data = await save();

  expect(data.customer_id).toBe('cust-B');
  expect(data.sales_type).toBe('cash_sales');
});

it('SF edit: a blank Sales Type still saves (not required)', async () => {
  salesOnSf = false;
  renderEdit('sponsorship_form');
  await customerInput();
  expect(await salesTypeSelect()).toBeTruthy();
  const data = await save();

  expect(data.sales_type ?? null).toBeNull();
  salesOnSf = true;
});
