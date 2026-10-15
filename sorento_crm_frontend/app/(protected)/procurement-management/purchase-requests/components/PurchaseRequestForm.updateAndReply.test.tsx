/**
 * RED tests - FORM-ROUTING-CONDITIONS review blocker: the Update & Reply dialog built its own
 * partial formData, so once the service body sends `?? null` it nulled customer_id, pic,
 * sales_type, sponsor_subject(_other), delivery_address and total_project_value on the server.
 * The payload must carry the loaded values unchanged. Harness: the sponsorship unit-price test.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/procurement-management/sponsorship-forms/new',
}));

const createMutateAsync = vi.fn();
const updateAndReplyMutateAsync = vi.fn().mockResolvedValue({});
let mockRequest: unknown = undefined;
vi.mock('../hooks/usePurchaseRequests', () => ({
  usePurchaseRequest: () => ({ data: mockRequest, isLoading: false }),
  useCreatePurchaseRequest: () => ({ mutateAsync: createMutateAsync, isPending: false }),
  useUpdatePurchaseRequest: () => ({ mutateAsync: vi.fn(), isPending: false }),
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


function renderEditForm(expectedRequestType: 'purchase_request' | 'sponsorship_form') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PurchaseRequestForm requestId="req-1" expectedRequestType={expectedRequestType} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockRequest = undefined;
});

async function sendReply(expectedRequestType: 'purchase_request' | 'sponsorship_form') {
  renderEditForm(expectedRequestType);
  fireEvent.click(await screen.findByRole('button', { name: /Update & Reply/i }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('Message to send'), {
    target: { value: 'Updated the form.' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: /^Update & Reply$/i }));
  await waitFor(() => expect(updateAndReplyMutateAsync).toHaveBeenCalled());
  return updateAndReplyMutateAsync.mock.calls[0][0].data.formData as Record<string, unknown>;
}

const COMMON = {
  id: 'req-1',
  request_number: 'PR-0001',
  customer_name: 'CUSTOMER A SDN BHD',
  customer_id: 'cust-302',
  pic: 'CS PIC X 0123456789',
  delivery_address: '1 Jalan Zzt, Selangor',
  total_project_value: 125000,
  project_title: 'Tower A',
  lines: [{ item_code: 'ITEM-A', quantity: 2, remark: null, unit_price: 15, total: 30 }],
};

describe('PurchaseRequestForm Update & Reply keeps the loaded fields', () => {
  it('purchase request: customer_id, pic, sales_type, delivery_address, total_project_value', async () => {
    mockRequest = { ...COMMON, request_type: 'purchase_request', sales_type: 'cash_sales' };

    const formData = await sendReply('purchase_request');

    expect(formData.customer_id).toBe('cust-302');
    expect(formData.pic).toBe('CS PIC X 0123456789');
    expect(formData.sales_type).toBe('cash_sales');
    expect(formData.delivery_address).toBe('1 Jalan Zzt, Selangor');
    expect(formData.total_project_value).toBe(125000);
  });

  it('sponsorship form: customer_id, pic, sponsor_subject and its Others text, delivery_address, value', async () => {
    mockRequest = {
      ...COMMON,
      request_number: 'SF-0001',
      request_type: 'sponsorship_form',
      sponsor_subject: 'others',
      sponsor_subject_other: 'Annual dinner',
    };

    const formData = await sendReply('sponsorship_form');

    expect(formData.customer_id).toBe('cust-302');
    expect(formData.pic).toBe('CS PIC X 0123456789');
    expect(formData.sponsor_subject).toBe('others');
    expect(formData.sponsor_subject_other).toBe('Annual dinner');
    expect(formData.delivery_address).toBe('1 Jalan Zzt, Selangor');
    expect(formData.total_project_value).toBe(125000);
  });
});
