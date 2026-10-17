/**
 * NEW-PRODUCT-APP staff New / Edit form (card rules 1-3). Hooks and the network are mocked;
 * the form's own rules run: the pre-fill copies text only, an emptied number box is sent as
 * null, and a Submit the server refuses keeps the user on the form with the draft saved once.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { NewProductApplicationPrefill } from '@/types/newProductApplication';

const mock = vi.hoisted(() => ({
  push: vi.fn(),
  createAsync: vi.fn(),
  updateAsync: vi.fn(),
  submitAsync: vi.fn(),
  prefill: undefined as unknown,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mock.push, replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));
vi.mock('../hooks/useNewProductApplications', () => ({
  useNewProductApplicationMutations: () => ({
    create: { mutateAsync: mock.createAsync, isPending: false },
    update: { mutateAsync: mock.updateAsync, isPending: false },
    submit: { mutateAsync: mock.submitAsync, isPending: false },
  }),
  useNewProductApplicationQuery: () => ({ data: undefined, isLoading: false }),
  useNewProductApplicationPrefillQuery: () => ({
    data: mock.prefill,
    isLoading: false,
  }),
  useNewProductApplicationLookups: () => ({
    itemGroups: async () => [],
    uoms: async () => [],
    brands: async () => [],
    itemTypes: async () => [],
    suppliers: async () => [],
  }),
}));
vi.mock('../services/newProductApplicationService', () => ({
  searchStockInquiryOptions: vi.fn(async () => []),
}));
vi.mock(
  '@/app/(protected)/procurement-management/purchase-requests/services/customerLookupService',
  () => ({ lookupCustomersForRequest: vi.fn(async () => []) }),
);

import NewProductApplicationForm from './NewProductApplicationForm';

const PREFILL: NewProductApplicationPrefill = {
  stock_inquiry_id: 'si-1',
  stock_inquiry_number: 'SI26-0153',
  customer_id: null,
  customer_name: 'VST GROUP OF COMPANIES',
  project_name: 'WESTIN RESIDENCE',
  needed_by: '2028-01-17',
  remark: null,
  lines: [
    {
      proposed_code: 'SRTKS2433 & SRTKS2406',
      description: 'Kitchen sinks',
      expected_qty: '55',
    },
  ],
};

function renderForm(
  props: { applicationId?: string; stockInquiryId?: string } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <NewProductApplicationForm {...props} />
    </QueryClientProvider>,
  );
}

const saveDraft = () =>
  fireEvent.click(screen.getByRole('button', { name: /save as draft/i }));
const lastBody = () => mock.createAsync.mock.calls.at(-1)?.[0];

beforeEach(() => {
  vi.clearAllMocks();
  mock.prefill = undefined;
  mock.createAsync.mockResolvedValue({ id: 'npa-1' });
  mock.updateAsync.mockResolvedValue({ id: 'npa-1' });
  mock.submitAsync.mockResolvedValue({ id: 'npa-1' });
});

describe('pre-fill from a responded stock inquiry', () => {
  it('puts the code text in line 1 and sends customer_id null', async () => {
    mock.prefill = PREFILL;
    renderForm({ stockInquiryId: 'si-1' });

    const code = await screen.findByLabelText('Proposed code');
    expect((code as HTMLInputElement).value).toBe('SRTKS2433 & SRTKS2406');
    expect((screen.getByLabelText('Customer') as HTMLInputElement).value).toBe(
      'VST GROUP OF COMPANIES',
    );

    saveDraft();
    await waitFor(() => expect(mock.createAsync).toHaveBeenCalledTimes(1));
    const body = lastBody();
    expect(body.customer_id).toBeNull();
    expect(body.customer_name).toBe('VST GROUP OF COMPANIES');
    expect(body.stock_inquiry_id).toBe('si-1');
    expect(body.lines).toHaveLength(1);
    expect(body.lines[0].proposed_code).toBe('SRTKS2433 & SRTKS2406');
    expect(body.lines[0].category_id).toBeNull();
    expect(body.lines[0].base_uom_id).toBeNull();
  });
});

describe('number fields', () => {
  it('sends null, never 0, once a figure is cleared', async () => {
    mock.prefill = PREFILL;
    renderForm({ stockInquiryId: 'si-1' });

    const qty = (await screen.findByLabelText(
      'Expected quantity',
    )) as HTMLInputElement;
    expect(qty.value).toBe('55');
    fireEvent.change(qty, { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Cost price'), {
      target: { value: '12.5' },
    });
    fireEvent.change(screen.getByLabelText('Cost price'), {
      target: { value: '' },
    });

    saveDraft();
    await waitFor(() => expect(mock.createAsync).toHaveBeenCalledTimes(1));
    const line = lastBody().lines[0];
    expect(line.expected_qty).toBeNull();
    expect(line.cost_price).toBeNull();
    expect(line.selling_price).toBeNull();
  });
});

describe('Submit', () => {
  it('stays on the form when the server refuses, and reuses the saved draft on the next press', async () => {
    mock.prefill = PREFILL;
    mock.submitAsync.mockRejectedValueOnce(
      new Error('Line 1: choose an item group'),
    );
    renderForm({ stockInquiryId: 'si-1' });
    await screen.findByLabelText('Proposed code');

    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }));
    await waitFor(() => expect(mock.submitAsync).toHaveBeenCalledTimes(1));
    expect(mock.push).not.toHaveBeenCalled();
    expect(mock.createAsync).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }));
    await waitFor(() => expect(mock.submitAsync).toHaveBeenCalledTimes(2));
    expect(mock.createAsync).toHaveBeenCalledTimes(1);
    expect(mock.updateAsync).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(mock.push).toHaveBeenCalledWith(expect.stringContaining('npa-1')),
    );
  });
});
