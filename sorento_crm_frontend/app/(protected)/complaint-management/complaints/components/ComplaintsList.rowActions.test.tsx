/**
 * S3-07 / D15 - the complaints list carries a row "..." menu.
 *
 * It had none while the record's gear held about ten items, so Delete and the PDF
 * were reachable only by opening the record. The two that need nothing but the
 * row's id come to the list; the rest (escalate, reassign, the SLA extension, the
 * edits, the reply) read the fetched complaint and its live SLA tracker, so they
 * stay on the record, per the D15 note.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, within } from '@testing-library/react';

import type { Complaint } from '../types/complaint.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/complaint-management/complaints',
  useSearchParams: () => new URLSearchParams(''),
}));

// Without this the grid never leaves its skeleton: the real hook fetches the saved
// column order and `isLoading` gates the body rows.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/services/userSelectService', () => ({
  getUserLookup: vi.fn(async () => []),
}));

vi.mock('../../complaint-root-causes/hooks/useComplaintRootCauses', () => ({
  useComplaintRootCausesSelect: () => ({ data: [] }),
}));
vi.mock('../../complaint-resolutions/hooks/useComplaintResolutions', () => ({
  useComplaintResolutionsSelect: () => ({ data: [] }),
}));

vi.mock('@/components/my-downloads/EntityDownloadsButton', () => ({
  EntityDownloadsButton: () => <span />,
}));

const exportPdf = vi.fn();
const rows = vi.fn();
vi.mock('../hooks/useComplaints', () => ({
  useComplaints: () => rows(),
  useExportComplaintPdf: () => ({ mutate: exportPdf, isPending: false }),
  useDeleteComplaint: () => ({ mutateAsync: vi.fn() }),
  useBulkDeleteComplaints: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import ComplaintsList from './ComplaintsList';

function complaint(over: Partial<Complaint> = {}): Complaint {
  return {
    id: 'c-1',
    complaint_number: 'CMP-0001',
    delivery_order_number: 'DO-9001',
    status: 'submitted',
    created_at: '2026-08-20T02:00:00',
    ...over,
  } as Complaint;
}

function renderList() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ComplaintsList />
    </QueryClientProvider>,
  );
}

/** Radix opens on pointerdown, which jsdom does not synthesize from a click. */
async function openRowMenu() {
  const trigger = await screen.findByRole('button', { name: 'complaint actions' });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'ArrowDown', code: 'ArrowDown' });
  return screen.findByRole('menu');
}

beforeEach(() => {
  vi.clearAllMocks();
  rows.mockReturnValue({
    data: { data: [complaint()], pagination: { total: 1, page: 1, limit: 50 } },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  });
});

describe('ComplaintsList row actions', () => {
  it('carries the two actions a row can run on its own, Delete last and in red', async () => {
    renderList();

    const menu = await openRowMenu();
    const labels = within(menu)
      .getAllByRole('menuitem')
      .map((item) => (item.textContent || '').trim());

    expect(labels).toEqual(['Download PDF', 'Delete']);
    expect(within(menu).getByRole('menuitem', { name: 'Delete' }).className).toContain(
      'text-destructive',
    );
  });

  it('exports the PDF for that row alone', async () => {
    const menu = (renderList(), await openRowMenu());

    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Download PDF' }));

    expect(exportPdf).toHaveBeenCalledWith('c-1');
  });

  it('confirms before it deletes, and names what is going', async () => {
    const menu = (renderList(), await openRowMenu());

    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Delete' }));

    const dialog = within(await screen.findByRole('alertdialog'));
    expect(dialog.getByText('Delete Complaint')).toBeInTheDocument();
    expect(dialog.getByText(/DO-9001/)).toBeInTheDocument();
  });

  it('offers no delete on a voided complaint, which cannot be deleted', async () => {
    rows.mockReturnValue({
      data: {
        data: [complaint({ status: 'voided' })],
        pagination: { total: 1, page: 1, limit: 50 },
      },
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    });

    const menu = (renderList(), await openRowMenu());

    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((item) => (item.textContent || '').trim()),
    ).toEqual(['Download PDF']);
  });
});
