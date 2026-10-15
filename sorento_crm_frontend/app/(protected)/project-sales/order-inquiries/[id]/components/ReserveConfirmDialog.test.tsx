/** AC-SN-41: the reserve confirmation dialog (props pinned by the lane brief). */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReserveConfirmDialog } from './ReserveConfirmDialog';

const LINES = [
  { row_id: 'r1', item_code: 'B2155-NL-BLUE', qty_requested: 90, qty_reserved: 60, balance: 30, location: 'BRW', reason: 'only 60 on hand' },
  { row_id: 'r2', item_code: 'CKS1050', qty_requested: 30, qty_reserved: 30, balance: 0, location: 'MWH', reason: null },
];
const RECIPIENTS = { to: { name: 'Joey' }, cc: [{ name: 'Eling' }] };

const onConfirm = vi.fn(async (_park: boolean) => undefined);
const onOpenChange = vi.fn();

function renderDialog(over: Partial<React.ComponentProps<typeof ReserveConfirmDialog>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReserveConfirmDialog
        open
        onOpenChange={onOpenChange}
        inquiryNo="OI-2609-0612"
        lines={LINES}
        recipients={RECIPIENTS}
        onConfirm={onConfirm}
        {...over}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('AC-SN-41 ReserveConfirmDialog', () => {
  it('titles the dialog with the line count and OI number', () => {
    renderDialog();
    expect(screen.getByRole('dialog', { name: 'Reserve 2 lines on OI-2609-0612' })).toBeInTheDocument();
  });

  it('lists the staged lines under the six columns', () => {
    renderDialog();
    for (const h of ['Product', 'Requested', 'Reserve', 'Balance to buy', 'Location', 'Reason']) {
      expect(screen.getByText(h, { selector: 'th, th *, [role="columnheader"], [role="columnheader"] *' })).toBeInTheDocument();
    }
    expect(screen.getByText('B2155-NL-BLUE')).toBeInTheDocument();
    expect(screen.getByText('only 60 on hand')).toBeInTheDocument();
    expect(screen.getByText('CKS1050')).toBeInTheDocument();
  });

  it('states who the notice goes to', () => {
    renderDialog();
    expect(screen.getByText('Notice to purchasing: To Joey, Cc Eling')).toBeInTheDocument();
  });

  it('"Park notice first" is unticked on open, button reads Reserve and send to Joey', () => {
    renderDialog();
    expect(screen.getByLabelText(/park notice first/i)).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Reserve and send to Joey' })).toBeInTheDocument();
  });

  it('unticked confirm calls onConfirm(false)', async () => {
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Reserve and send to Joey' }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(false));
  });

  it('ticked renames the button and calls onConfirm(true)', async () => {
    renderDialog();
    fireEvent.click(screen.getByLabelText(/park notice first/i));
    expect(screen.queryByRole('button', { name: 'Reserve and send to Joey' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reserve and park notice' }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(true));
  });

  it('Cancel closes without confirming', () => {
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('is unticked again after close and reopen', () => {
    const qc = new QueryClient();
    const ui = (open: boolean) => (
      <QueryClientProvider client={qc}>
        <ReserveConfirmDialog
          open={open}
          onOpenChange={onOpenChange}
          inquiryNo="OI-2609-0612"
          lines={LINES}
          recipients={RECIPIENTS}
          onConfirm={onConfirm}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(ui(true));
    fireEvent.click(screen.getByLabelText(/park notice first/i));
    rerender(ui(false));
    rerender(ui(true));
    expect(screen.getByLabelText(/park notice first/i)).not.toBeChecked();
  });

  it('disables the primary button while pending', () => {
    renderDialog({ isPending: true });
    expect(screen.getByRole('button', { name: /^reserve and/i })).toBeDisabled();
  });

  it('within the dialog, nothing is sent until the button is pressed', () => {
    renderDialog();
    expect(within(screen.getByRole('dialog')).getByText(/notice to purchasing/i)).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
