/**
 * AC-SN-40 (moved from `ReserveRequestDialog.park.test.tsx`) and STOCK-DEBT-SNAPSHOT AC-F6:
 * "Park this first" behaves exactly as on the OI page, unticked on open.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));
import { toast } from '@/lib/toast';

vi.mock('../hooks/useReserveRowOptions', () => ({
  useReserveRowOptions: (entries: Array<{ key: string }>) =>
    Object.fromEntries(
      entries.map((entry) => [
        entry.key,
        {
          isLoading: false,
          defaultWarehouseId: 'BRW',
          options: [{ value: 'BRW', label: 'BRW' }],
          availableQtyByWarehouseId: {},
        },
      ]),
    ),
}));

const createSpy = vi.fn(async (_inquiryId: string, _payload: unknown) => ({
  id: 'rr-1',
  ordinal: 1,
  first_to_name: 'Eling',
}));
vi.mock('../services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/orderInquiryReserveService')>();
  return {
    ...actual,
    createOrderInquiryReserveRequest: (inquiryId: string, payload: unknown) =>
      createSpy(inquiryId, payload),
  };
});

import { RequestReserveDialog } from './RequestReserveDialog';

const ROWS = [
  {
    id: 'row-1',
    item_code: 'B2155-NL-BLUE',
    product_id: 'prod-1',
    location: 'BRW-BB',
    delivery_date: '2026-10-01',
    qty: '90',
    linked_qty: '0',
    reserved_qty: '0',
    bundled_qty: '0',
    verb: 'ORDER',
    state: 'raised',
    reserve_state: null,
  },
];

function renderDialog(open = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const ui = (o: boolean) => (
    <QueryClientProvider client={qc}>
      <RequestReserveDialog inquiryId="oi-1" open={o} onOpenChange={vi.fn()} rows={ROWS as never} />
    </QueryClientProvider>
  );
  const utils = render(ui(open));
  return { ...utils, rerenderOpen: (o: boolean) => utils.rerender(ui(o)) };
}

beforeEach(() => vi.clearAllMocks());

describe('park tickbox', () => {
  it('shows "Park this first" unticked with the Send request button', async () => {
    renderDialog();
    expect(await screen.findByLabelText(/park this first/i)).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Send request' })).toBeInTheDocument();
  });

  it('unticked sends park: false', async () => {
    renderDialog();
    await screen.findByLabelText(/park this first/i);
    fireEvent.click(screen.getByRole('button', { name: 'Send request' }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    expect((createSpy.mock.calls[0] as unknown[])[1]).toMatchObject({ park: false });
  });

  it('ticked renames the button to Park request, sends park: true and toasts the parked text', async () => {
    renderDialog();
    fireEvent.click(await screen.findByLabelText(/park this first/i));
    expect(screen.queryByRole('button', { name: 'Send request' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Park request' }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    expect((createSpy.mock.calls[0] as unknown[])[1]).toMatchObject({ park: true });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        'Request #1 parked. Send it from Order inquiries > Actions.',
      ),
    );
  });

  it('is unticked again after close and reopen', async () => {
    const { rerenderOpen } = renderDialog();
    fireEvent.click(await screen.findByLabelText(/park this first/i));
    expect(screen.getByLabelText(/park this first/i)).toBeChecked();
    rerenderOpen(false);
    rerenderOpen(true);
    expect(await screen.findByLabelText(/park this first/i)).not.toBeChecked();
  });
});
