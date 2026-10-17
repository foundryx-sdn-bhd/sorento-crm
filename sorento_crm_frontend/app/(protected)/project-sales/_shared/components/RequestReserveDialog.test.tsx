/**
 * STOCK-DEBT-SNAPSHOT AC-F6 / AC-F6b: the ONE shared "Request CS to reserve" dialog.
 *
 * Moved from `order-inquiries/[id]/components/ReserveRequestDialog.test.tsx`: the old
 * suite's assertions (AC-RS-22/23/75/89 and the reviewer fix rounds) re-pointed at the new
 * props. The dialog now takes the OI's live worklist rows and builds its own lines
 * (`useReserveRowOptions` + `rowRemaining`, formerly inline in `OrderInquiryDetail.tsx`) and
 * sends through `useCreateOrderInquiryReserveRequest(inquiryId)`, so the service is mocked at
 * the service boundary (hook -> service stays real) and the stock-grid read is mocked at the
 * `useReserveRowOptions` hook.
 *
 * New in this file: `defaultWarehouseId` wins when it is among a row's options, else the
 * hook's own default; Park this first is unticked on open.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));
import { toast } from '@/lib/toast';

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
  note?: string | null;
  park?: boolean;
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AC-RS-22 (moved): one line per row, Requested = remaining, Location = the pool', () => {
  it('shows item code, delivery date and remaining for every row', async () => {
    renderDialog();
    expect(await screen.findByText('B2155-NL-BLUE')).toBeInTheDocument();
    expect(screen.getByText('CKS1050')).toBeInTheDocument();
    expect(screen.getAllByText(/90|30/).length).toBeGreaterThanOrEqual(2);
  });

  it('Remaining is qty less linked, reserved and bundled; Requested defaults to it and cannot exceed it', async () => {
    renderDialog({
      rows: [
        worklistRow({ qty: '100', linked_qty: '10', reserved_qty: '20', bundled_qty: '5' }),
      ] as never,
    });
    const requested = (await screen.findByLabelText(/requested/i)) as HTMLInputElement;
    expect(requested).toHaveValue(65);
    expect(requested).toHaveAttribute('max', '65');
  });

  it('Location defaults to the hook default pool for each row', async () => {
    renderDialog();
    expect(await screen.findAllByText('BRW')).not.toHaveLength(0);
    expect(screen.getByText('MWH')).toBeInTheDocument();
  });

  it('Send calls the reserve-request service for THIS inquiry with { rows, note, park } and closes on success', async () => {
    const onOpenChange = vi.fn();
    const onSent = vi.fn();
    renderDialog({ onOpenChange, onSent });

    fireEvent.click(await screen.findByRole('button', { name: /send request/i }));

    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    const [inquiryId, payload] = createSpy.mock.calls[0] as [string, SentPayload];
    expect(inquiryId).toBe('oi-1');
    expect(payload.rows).toEqual([
      { row_id: 'row-1', qty_requested: 90, warehouse_id: 'BRW' },
      { row_id: 'row-2', qty_requested: 30, warehouse_id: 'MWH' },
    ]);
    expect(payload.note ?? null).toBeNull();
    expect(payload.park).toBe(false);

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(onSent).toHaveBeenCalledTimes(1);
  });

  it('the success toast is the hook own text, not a new one', async () => {
    renderDialog();
    fireEvent.click(await screen.findByRole('button', { name: /send request/i }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Request #1 sent to Eling'));
  });

  it('a failed send keeps the dialog open and does not call onSent', async () => {
    createSpy.mockRejectedValueOnce(new Error('Failed to send that reserve request'));
    const onOpenChange = vi.fn();
    const onSent = vi.fn();
    renderDialog({ onOpenChange, onSent });
    fireEvent.click(await screen.findByRole('button', { name: /send request/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to send that reserve request'));
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(onSent).not.toHaveBeenCalled();
  });
});

describe('STOCK-DEBT-SNAPSHOT AC-F6: defaultWarehouseId', () => {
  it('wins over the hook default when it is among a row own options', async () => {
    renderDialog({ defaultWarehouseId: 'BRW-BB' });
    fireEvent.click(await screen.findByRole('button', { name: /send request/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    const [, payload] = createSpy.mock.calls[0] as [string, SentPayload];
    // row-1 offers BRW-BB, so the decision pool wins; row-2 does not, so its own default stays.
    expect(payload.rows).toEqual([
      { row_id: 'row-1', qty_requested: 90, warehouse_id: 'BRW-BB' },
      { row_id: 'row-2', qty_requested: 30, warehouse_id: 'MWH' },
    ]);
  });

  it('is ignored when it is not among the options, and so is null', async () => {
    renderDialog({ defaultWarehouseId: 'NOT-A-POOL' });
    fireEvent.click(await screen.findByRole('button', { name: /send request/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    const [, payload] = createSpy.mock.calls[0] as [string, SentPayload];
    expect(payload.rows.map((row) => row.warehouse_id)).toEqual(['BRW', 'MWH']);
  });
});

describe('reviewer fix round (moved): date format and typed 0', () => {
  it('a raw ISO delivery_date prints dd/mm/yyyy', async () => {
    renderDialog({ rows: [ROWS[0]] as never });
    await screen.findByText('B2155-NL-BLUE');
    expect(screen.getByText(/01\/10\/2026/)).toBeInTheDocument();
    expect(screen.queryByText(/2026-10-01/)).not.toBeInTheDocument();
  });

  it('typing 0 into Requested keeps the 0, leaves Send enabled and blocks with a message (NUMBER-FIELDS)', async () => {
    renderDialog({ rows: [ROWS[0]] as never });
    const requestedInput = (await screen.findByLabelText(/requested/i)) as HTMLInputElement;
    fireEvent.change(requestedInput, { target: { value: '0' } });
    expect(requestedInput.value).toBe('0');
    const sendButton = screen.getByRole('button', { name: /send request/i });
    expect(sendButton).toBeEnabled();
    fireEvent.click(sendButton);
    expect(createSpy).not.toHaveBeenCalled();
    expect(await screen.findByText('Enter a whole number between 1 and 90.')).toBeInTheDocument();
  });
});

describe('AC-RS-75 (moved): the body is ONE DataGrid', () => {
  it('is a fixed, resizable DataGrid table with the five headers and one row per OI row', async () => {
    renderDialog();
    const table = await screen.findByRole('table');
    expect(table.className).toMatch(/table-fixed/);
    expect(table.querySelector('th[style*="width"]')).toBeInTheDocument();
    const scoped = within(table);
    for (const name of ['Product', 'Delivery date', 'Remaining', 'Requested', 'Location']) {
      expect(scoped.getByRole('columnheader', { name })).toBeInTheDocument();
    }
    expect(scoped.queryByRole('columnheader', { name: 'Due' })).not.toBeInTheDocument();
    expect(scoped.getAllByRole('row')).toHaveLength(3);
  });

  it('Note stays BELOW the grid, outside the table', async () => {
    renderDialog();
    const table = await screen.findByRole('table');
    expect(table.contains(screen.getByLabelText(/note/i))).toBe(false);
  });

  it('each row keeps its own Requested, and Send posts the edited figure', async () => {
    renderDialog();
    const inputs = (await screen.findAllByLabelText(/requested/i)) as HTMLInputElement[];
    expect(inputs).toHaveLength(2);
    fireEvent.change(inputs[0], { target: { value: '40' } });
    expect(inputs[1].value).toBe('30');
    fireEvent.click(screen.getByRole('button', { name: /send request/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    const [, payload] = createSpy.mock.calls[0] as [string, SentPayload];
    expect(payload.rows[0]).toEqual({ row_id: 'row-1', qty_requested: 40, warehouse_id: 'BRW' });
    expect(payload.rows[1]).toEqual({ row_id: 'row-2', qty_requested: 30, warehouse_id: 'MWH' });
  });
});

describe('AC-RS-89 (moved): no drag grips, one overflow-x-auto ancestor', () => {
  it('renders no "Drag column to reorder" wrapper on any header', async () => {
    renderDialog();
    await screen.findByRole('table');
    expect(document.querySelector('[aria-label="Drag column to reorder"]')).not.toBeInTheDocument();
  });

  it('the table carries exactly ONE overflow-x-auto ancestor', async () => {
    renderDialog();
    const table = await screen.findByRole('table');
    let ancestor: HTMLElement | null = table.parentElement;
    let count = 0;
    while (ancestor) {
      if (ancestor.classList.contains('overflow-x-auto')) count += 1;
      ancestor = ancestor.parentElement;
    }
    expect(count).toBe(1);
  });
});
