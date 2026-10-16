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

// OI-BULK-RESERVE v4 fix round 2 (AC-BR-25/26, F8): the multi-OI mode of the same dialog,
// used by the cross-OI Lines page, lists every LINE (not one row per order inquiry).
// Pinned API: `inquiries` stays and its presence selects multi mode (the OI count in the
// title and summary is `inquiries.length`); `lines` now carries the multi-mode rows, each
// with optional `description`, `inquiry_no` and `result`. `inquiryNo` / `recipients` are
// single-OI props and are ignored in multi mode.
type Inquiries = NonNullable<React.ComponentProps<typeof ReserveConfirmDialog>['inquiries']>;
type Line = React.ComponentProps<typeof ReserveConfirmDialog>['lines'][number];
const INQUIRIES: Inquiries = [
  { inquiry_id: 'oi-1', inquiry_no: 'OI-2609-0001', reserve_count: 2, reject_count: 0 },
  { inquiry_id: 'oi-2', inquiry_no: 'OI-2609-0002', reserve_count: 0, reject_count: 1 },
];
const MULTI_LINES: Line[] = [
  {
    row_id: 'm1',
    inquiry_no: 'OI-2609-0001',
    item_code: 'CB313',
    description: 'Basin mixer chrome',
    qty_requested: 88,
    qty_reserved: 88,
    balance: 0,
    location: 'BRW',
    reason: null,
  },
  {
    row_id: 'm2',
    inquiry_no: 'OI-2609-0001',
    item_code: 'CKS1050',
    description: 'Cistern kit slim',
    qty_requested: 44,
    qty_reserved: 20,
    balance: 24,
    location: 'MWH',
    reason: 'only 20 on hand',
  },
  {
    row_id: 'm3',
    inquiry_no: 'OI-2609-0002',
    item_code: 'C-FH14',
    description: 'Flush handle 14',
    qty_requested: 12,
    qty_reserved: 0,
    balance: 12,
    location: 'BRW',
    reason: 'No stock',
  },
];

function renderMulti(over: Partial<React.ComponentProps<typeof ReserveConfirmDialog>> = {}) {
  return renderDialog({
    lines: MULTI_LINES,
    inquiryNo: '',
    recipients: undefined,
    inquiries: INQUIRIES,
    ...over,
  });
}

function rowOfItem(itemCode: string): HTMLElement {
  return screen.getByText(itemCode).closest('tr') as HTMLElement;
}

function cellsOf(tr: HTMLElement): string[] {
  return within(tr)
    .getAllByRole('cell')
    .map((cell) => cell.textContent?.trim() ?? '');
}

const HEADER_SELECTOR = 'th, th *, [role="columnheader"], [role="columnheader"] *';

describe('F8 ReserveConfirmDialog multi-OI mode', () => {
  it('titles the dialog with the line count and the order inquiry count', () => {
    renderMulti();
    expect(
      screen.getByRole('dialog', { name: 'Reserve 3 lines on 2 order inquiries' }),
    ).toBeInTheDocument();
  });

  it('uses the singular for one line on one order inquiry', () => {
    renderMulti({
      lines: [MULTI_LINES[0]],
      inquiries: [{ inquiry_id: 'oi-1', inquiry_no: 'OI-2609-0001', reserve_count: 1, reject_count: 0 }],
    });
    expect(
      screen.getByRole('dialog', { name: 'Reserve 1 line on 1 order inquiry' }),
    ).toBeInTheDocument();
    expect(screen.getByText('1 line on 1 order inquiry · Reserve 1 · Reject 0')).toBeInTheDocument();
  });

  it('lists the line columns with OI no first and Result last, and drops the per-OI grid', () => {
    renderMulti();
    const headers = screen
      .getAllByRole('columnheader')
      .map((header) => header.textContent?.trim());
    expect(headers).toEqual([
      'OI no',
      'Product',
      'Requested',
      'Reserve',
      'Balance to buy',
      'Location',
      'Reason',
      'Result',
    ]);
    expect(screen.queryByText('Reject', { selector: HEADER_SELECTOR })).not.toBeInTheDocument();
  });

  it('prints one row per line with OI no, item code, description, quantities, location and reason', () => {
    renderMulti();
    expect(screen.getAllByRole('row')).toHaveLength(1 + MULTI_LINES.length);

    const cb = cellsOf(rowOfItem('CB313'));
    expect(cb[0]).toBe('OI-2609-0001');
    expect(cb[1]).toContain('CB313');
    expect(cb[1]).toContain('Basin mixer chrome');
    expect(cb.slice(2)).toEqual(['88', '88', '0', 'BRW', '', '']);

    const cks = cellsOf(rowOfItem('CKS1050'));
    expect(cks[0]).toBe('OI-2609-0001');
    expect(cks[1]).toContain('Cistern kit slim');
    expect(cks.slice(2)).toEqual(['44', '20', '24', 'MWH', 'only 20 on hand', '']);

    const cfh = cellsOf(rowOfItem('C-FH14'));
    expect(cfh[0]).toBe('OI-2609-0002');
    expect(cfh.slice(2)).toEqual(['12', '0', '12', 'BRW', 'No stock', '']);
  });

  it('shows the description muted under the code, truncated with a title', () => {
    renderMulti();
    const description = screen.getByText('Basin mixer chrome');
    expect(description).toHaveAttribute('title', 'Basin mixer chrome');
    expect(description.className).toContain('truncate');
    expect(description.className).toContain('text-muted-foreground');
  });

  it('prints the summary: lines, order inquiries, Reserve (qty above 0) and Reject (qty 0)', () => {
    renderMulti();
    expect(
      screen.getByText('3 lines on 2 order inquiries · Reserve 2 · Reject 1'),
    ).toBeInTheDocument();
  });

  it('names no notice recipient, keeps "Park notice first" unticked, primary reads Reserve and send', () => {
    renderMulti();
    expect(screen.queryByText(/notice to purchasing/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/park notice first/i)).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Reserve and send' })).toBeEnabled();
  });

  it('unticked confirm calls onConfirm(false); ticked renames the button and calls onConfirm(true)', async () => {
    renderMulti();
    fireEvent.click(screen.getByRole('button', { name: 'Reserve and send' }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(false));

    onConfirm.mockClear();
    fireEvent.click(screen.getByLabelText(/park notice first/i));
    expect(screen.queryByRole('button', { name: 'Reserve and send' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reserve and park notice' }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(true));
  });

  const MIXED: Line[] = [
    { ...MULTI_LINES[0], result: { ok: true } },
    { ...MULTI_LINES[1], result: { ok: true } },
    { ...MULTI_LINES[2], result: { ok: false, message: 'Row already answered' } },
  ];

  it('shows Saved or the server message on every line once results are in', () => {
    renderMulti({ lines: MIXED });
    expect(cellsOf(rowOfItem('CB313')).at(-1)).toBe('Saved');
    expect(cellsOf(rowOfItem('CKS1050')).at(-1)).toBe('Saved');
    expect(cellsOf(rowOfItem('C-FH14')).at(-1)).toBe('Row already answered');
    const message = screen.getByText('Row already answered');
    expect(message).toHaveAttribute('title', 'Row already answered');
    expect(message.className).toContain('truncate');
  });

  it('a failed result without a message reads Failed', () => {
    renderMulti({ lines: [{ ...MULTI_LINES[2], result: { ok: false } }] , inquiries: [INQUIRIES[1]] });
    expect(cellsOf(rowOfItem('C-FH14')).at(-1)).toBe('Failed');
  });

  it('when done, the footer is only Close', () => {
    renderMulti({ done: true, lines: MIXED });
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).queryByRole('button', { name: /^reserve and/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/park notice first/i)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('disables the primary button while pending', () => {
    renderMulti({ isPending: true });
    expect(screen.getByRole('button', { name: /^reserve and/i })).toBeDisabled();
  });

  it('never prints the word Stage', () => {
    renderMulti();
    expect(screen.queryByText(/\bstage/i)).not.toBeInTheDocument();
  });
});

describe('F8 ReserveConfirmDialog with 100 lines across 7 order inquiries', () => {
  const HUNDRED: Line[] = Array.from({ length: 100 }, (_, i) => ({
    row_id: `h${i}`,
    inquiry_no: `OI-2609-${String((i % 7) + 1).padStart(4, '0')}`,
    item_code: `ITEM-${String(i).padStart(3, '0')}`,
    description: `Description ${i}`,
    qty_requested: 10,
    qty_reserved: i % 10 === 0 ? 0 : 10,
    balance: i % 10 === 0 ? 10 : 0,
    location: 'BRW',
    reason: i % 10 === 0 ? 'No stock' : null,
  }));
  const HUNDRED_INQUIRIES: Inquiries = Array.from({ length: 7 }, (_, k) => ({
    inquiry_id: `oi-${k + 1}`,
    inquiry_no: `OI-2609-${String(k + 1).padStart(4, '0')}`,
    reserve_count: 0,
    reject_count: 0,
  }));

  it('titles it and prints the summary with Reserve 90 and Reject 10', () => {
    renderMulti({ lines: HUNDRED, inquiries: HUNDRED_INQUIRIES });
    expect(
      screen.getByRole('dialog', { name: 'Reserve 100 lines on 7 order inquiries' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('100 lines on 7 order inquiries · Reserve 90 · Reject 10'),
    ).toBeInTheDocument();
  });

  it('renders all 100 line rows, first and last included', () => {
    renderMulti({ lines: HUNDRED, inquiries: HUNDRED_INQUIRIES });
    expect(screen.getAllByRole('row')).toHaveLength(101);
    expect(screen.getByText('ITEM-000')).toBeInTheDocument();
    expect(screen.getByText('ITEM-099')).toBeInTheDocument();
  });

  it('keeps the header sticky inside a bounded, vertically scrolling grid scroller', () => {
    renderMulti({ lines: HUNDRED, inquiries: HUNDRED_INQUIRIES });
    const dialog = screen.getByRole('dialog');
    const thead = dialog.querySelector('thead') as HTMLElement;
    expect(thead.className).toContain('sticky');
    expect(thead.className).toContain('top-0');

    const scroller = dialog.querySelector('[data-slot="data-grid-scroller"]') as HTMLElement;
    expect(scroller.className).toMatch(/max-h-/);
    expect(scroller.className).toMatch(/overflow-y-(auto|scroll)/);
  });

  it('keeps the dialog within the viewport and the footer reachable', () => {
    renderMulti({ lines: HUNDRED, inquiries: HUNDRED_INQUIRIES });
    const dialog = screen.getByRole('dialog');
    expect(dialog.className).toContain('max-h-[85dvh]');
    expect(within(dialog).getByRole('button', { name: 'Reserve and send' })).toBeEnabled();
  });
});

describe('F8 ReserveConfirmDialog single-OI mode shows a description when given', () => {
  it('prints the description under the item code, with no OI no or Result column', () => {
    renderDialog({
      lines: [{ ...LINES[0], description: 'Basin mixer chrome' }, LINES[1]],
    });
    expect(screen.getByText('Basin mixer chrome')).toBeInTheDocument();
    expect(screen.queryByText('OI no', { selector: HEADER_SELECTOR })).not.toBeInTheDocument();
    expect(screen.queryByText('Result', { selector: HEADER_SELECTOR })).not.toBeInTheDocument();
  });
});
