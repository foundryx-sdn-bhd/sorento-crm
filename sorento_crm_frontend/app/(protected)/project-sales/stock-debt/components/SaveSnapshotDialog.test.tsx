/**
 * STOCK-DEBT-SNAPSHOT AC-F1: the Save snapshot modal. Name defaults to `Stock Debt dd/mm/yyyy`,
 * a read-only line names the product count and the filters, Save posts the board's current
 * filters (the object Export sends), success toasts "Snapshot saved" with an Open action and an
 * error toasts the extracted message (AC-7: "Narrow the filters first").
 *
 * Props: { open, onOpenChange, filters, productCount }. The service is mocked at its module
 * boundary so hook -> service wiring is real.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => '/project-sales/stock-debt',
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));
import { toast } from '@/lib/toast';

const createSnapshot = vi.fn();
vi.mock('../services/stockDebtSnapshotService', () => ({
  createSnapshot: (...args: unknown[]) => createSnapshot(...args),
}));

import { SaveSnapshotDialog } from './SaveSnapshotDialog';

const LOCATION_ID = '8f3b2c1e-4a5d-4e6f-9a7b-0c1d2e3f4a5b';

const FILTERS = {
  query: 'SRT',
  onlyDebt: true,
  book: 'project',
  supplierIds: [],
  locations: [LOCATION_ID],
  locationsMode: 'include',
  dateFrom: '2023-01-01',
  dateTo: '2027-05-31',
  showAux: true,
  projected: false,
};

function renderDialog(over: Partial<React.ComponentProps<typeof SaveSnapshotDialog>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onOpenChange = vi.fn();
  const utils = render(
    <QueryClientProvider client={qc}>
      <SaveSnapshotDialog
        open
        onOpenChange={onOpenChange}
        filters={FILTERS as never}
        productCount={550}
        {...over}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onOpenChange };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-15T06:00:00Z'));
});
afterEach(() => {
  vi.useRealTimers();
});

describe('AC-F1 Save snapshot modal', () => {
  it('opens with Name defaulted to "Stock Debt dd/mm/yyyy"', async () => {
    renderDialog();
    expect(await screen.findByLabelText(/^name$/i)).toHaveValue('Stock Debt 15/10/2026');
  });

  it('names the product count and the filters in a read-only line, never a raw id', async () => {
    renderDialog();
    await screen.findByLabelText(/^name$/i);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('550 products');
    expect(dialog).toHaveTextContent('Project');
    expect(dialog).toHaveTextContent('01/01/2023');
    expect(dialog).toHaveTextContent('31/05/2027');
    expect(dialog.textContent).not.toContain(LOCATION_ID);
  });

  it('Save posts the typed name and the filters it was given', async () => {
    createSnapshot.mockResolvedValue({ id: 's-1', name: 'Month end' });
    const { onOpenChange } = renderDialog();

    const name = await screen.findByLabelText(/^name$/i);
    fireEvent.change(name, { target: { value: 'Month end' } });
    fireEvent.click(screen.getByRole('button', { name: /^save snapshot$/i }));

    await waitFor(() => expect(createSnapshot).toHaveBeenCalledTimes(1));
    expect(createSnapshot).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Month end', filters: FILTERS }),
    );
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('success toasts "Snapshot saved" with an Open action that goes to the snapshot', async () => {
    createSnapshot.mockResolvedValue({ id: 's-1', name: 'Stock Debt 15/10/2026' });
    renderDialog();

    fireEvent.click(await screen.findByRole('button', { name: /^save snapshot$/i }));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const [message, options] = (toast.success as unknown as { mock: { calls: unknown[][] } }).mock
      .calls[0] as [string, { action: { label: string; onClick: () => void } }];
    expect(message).toBe('Snapshot saved');
    expect(options.action.label).toBe('Open');
    options.action.onClick();
    expect(push).toHaveBeenCalledWith('/project-sales/stock-debt/snapshots/s-1');
  });

  it('an error toasts the extracted message and leaves the modal open', async () => {
    createSnapshot.mockRejectedValue(new Error('Narrow the filters first'));
    const { onOpenChange } = renderDialog();

    fireEvent.click(await screen.findByRole('button', { name: /^save snapshot$/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Narrow the filters first'));
    expect(toast.success).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('Save is disabled while the name is blank', async () => {
    renderDialog();
    fireEvent.change(await screen.findByLabelText(/^name$/i), { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: /^save snapshot$/i })).toBeDisabled();
    expect(createSnapshot).not.toHaveBeenCalled();
  });

  it('Save is disabled while the save is in flight (no double post)', async () => {
    createSnapshot.mockReturnValue(new Promise(() => {}));
    renderDialog();
    fireEvent.click(await screen.findByRole('button', { name: /^save snapshot$/i }));
    await waitFor(() => expect(createSnapshot).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: /^save snapshot$|saving/i })).toBeDisabled();
  });

  it('Cancel closes without saving', async () => {
    const { onOpenChange } = renderDialog();
    fireEvent.click(await screen.findByRole('button', { name: /^cancel$/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(createSnapshot).not.toHaveBeenCalled();
  });
});
