/** AC-SN-40: "Park this first" tickbox in the request lightbox. */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReserveRequestDialog } from './ReserveRequestDialog';

const ROWS = [
  {
    id: 'row-1',
    item_code: 'B2155-NL-BLUE',
    delivery_date: '2026-10-01',
    remaining: '90',
    defaultLocation: 'BRW',
    locationOptions: [{ value: 'BRW', label: 'BRW' }],
  },
];

const onSendSpy = vi.fn(async () => ({ id: 'rr-1', ordinal: 1, first_to_name: 'Eling' }));

function renderDialog(open = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (o: boolean) => (
    <QueryClientProvider client={qc}>
      <ReserveRequestDialog
        open={o}
        onOpenChange={vi.fn()}
        rows={ROWS as never}
        onSend={onSendSpy as never}
      />
    </QueryClientProvider>
  );
  const utils = render(ui(open));
  return { ...utils, rerenderOpen: (o: boolean) => utils.rerender(ui(o)) };
}

beforeEach(() => vi.clearAllMocks());

describe('AC-SN-40 park tickbox', () => {
  it('shows "Park this first" unticked with the Send request button', async () => {
    renderDialog();
    const box = await screen.findByLabelText(/park this first/i);
    expect(box).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Send request' })).toBeInTheDocument();
  });

  it('unticked sends park: false', async () => {
    renderDialog();
    await screen.findByLabelText(/park this first/i);
    fireEvent.click(screen.getByRole('button', { name: 'Send request' }));
    await waitFor(() => expect(onSendSpy).toHaveBeenCalledTimes(1));
    expect((onSendSpy.mock.calls[0] as unknown[])[0]).toMatchObject({ park: false });
  });

  it('ticked renames the button to Park request and sends park: true', async () => {
    renderDialog();
    fireEvent.click(await screen.findByLabelText(/park this first/i));
    expect(screen.queryByRole('button', { name: 'Send request' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Park request' }));
    await waitFor(() => expect(onSendSpy).toHaveBeenCalledTimes(1));
    expect((onSendSpy.mock.calls[0] as unknown[])[0]).toMatchObject({ park: true });
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
