/**
 * NUMBER-FIELDS UAC4.1: OI-2610-0057, line CB6638 (Remaining 212). Requested can be cleared and
 * retyped; only the final value is checked when the request is sent or parked.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const OPTIONS_BY_KEY: Record<string, { default: string; options: { value: string; label: string }[] }> = {
  'row-1': {
    default: 'BRW',
    options: [
      { value: 'BRW', label: 'BRW' },
      { value: 'BRW-BB', label: 'BRW-BB' },
    ],
  },
  'row-2': { default: 'MWH', options: [{ value: 'MWH', label: 'MWH' }] },
};
vi.mock('../hooks/useReserveRowOptions', () => ({
  useReserveRowOptions: (entries: Array<{ key: string }>) =>
    Object.fromEntries(
      entries.map((entry) => {
        const own = OPTIONS_BY_KEY[entry.key];
        return [
          entry.key,
          {
            isLoading: false,
            defaultWarehouseId: own?.default ?? null,
            options: own?.options ?? [],
            availableQtyByWarehouseId: {},
          },
        ];
      }),
    ),
}));

// eslint-disable-next-line @typescript-eslint/no-unused-vars
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

function worklistRow(over: Record<string, unknown>) {
  return {
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
    ...over,
  };
}

const ROWS = [
  worklistRow({}),
  worklistRow({
    id: 'row-2',
    item_code: 'CKS1050',
    product_id: 'prod-2',
    location: 'MWH',
    delivery_date: '2026-10-05',
    qty: '30',
  }),
];

function renderDialog(overrides: Partial<React.ComponentProps<typeof RequestReserveDialog>> = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <RequestReserveDialog
        inquiryId="oi-1"
        open
        onOpenChange={vi.fn()}
        rows={ROWS as never}
        {...overrides}
      />
    </QueryClientProvider>,
  );
}

type SentPayload = {
  rows: Array<{ row_id: string; qty_requested: string | number; warehouse_id: string }>;
};

const MSG = 'Enter a whole number between 1 and 212.';

function openOne() {
  renderDialog({ rows: [worklistRow({ item_code: 'CB6638', qty: '212' })] as never });
  return screen.findByLabelText(/requested/i) as Promise<HTMLInputElement>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('NUMBER-FIELDS: Requested on CB6638', () => {
  it('can be cleared (stays empty) and retyped', async () => {
    const input = await openOne();
    expect(input).toHaveValue(212);
    fireEvent.change(input, { target: { value: '' } });
    expect(input.value).toBe('');
    fireEvent.change(input, { target: { value: '2' } });
    expect(input.value).toBe('2');
  });

  it('empty then Park request: nothing sent, message shown, dialog stays open', async () => {
    const onOpenChange = vi.fn();
    renderDialog({ onOpenChange, rows: [worklistRow({ item_code: 'CB6638', qty: '212' })] as never });
    const input = (await screen.findByLabelText(/requested/i)) as HTMLInputElement;
    fireEvent.click(screen.getByLabelText(/park this first/i));
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Park request' }));

    expect(createSpy).not.toHaveBeenCalled();
    expect(await screen.findByText(MSG)).toBeInTheDocument();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('300 (over the 212 remaining) is kept as typed and blocked with the same message', async () => {
    const input = await openOne();
    fireEvent.change(input, { target: { value: '300' } });
    expect(input.value).toBe('300');
    fireEvent.click(screen.getByRole('button', { name: /send request/i }));
    expect(createSpy).not.toHaveBeenCalled();
    expect(await screen.findByText(MSG)).toBeInTheDocument();
  });

  it('0 is not rewritten to 1 and is blocked with the message', async () => {
    const input = await openOne();
    fireEvent.change(input, { target: { value: '0' } });
    expect(input.value).toBe('0');
    fireEvent.click(screen.getByRole('button', { name: /send request/i }));
    expect(createSpy).not.toHaveBeenCalled();
    expect(await screen.findByText(MSG)).toBeInTheDocument();
  });

  it('4 is sent as qty_requested 4, and the message clears once 4 is typed', async () => {
    const input = await openOne();
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /send request/i }));
    expect(await screen.findByText(MSG)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: '4' } });
    expect(screen.queryByText(MSG)).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute('aria-invalid', 'true');

    fireEvent.click(screen.getByRole('button', { name: /send request/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    const [, payload] = createSpy.mock.calls[0] as [string, SentPayload];
    expect(payload.rows[0]).toMatchObject({ row_id: 'row-1', qty_requested: 4 });
  });
});
