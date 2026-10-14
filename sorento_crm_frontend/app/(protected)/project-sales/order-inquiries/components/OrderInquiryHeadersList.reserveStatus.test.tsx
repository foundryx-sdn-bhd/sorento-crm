/**
 * AC-SN-42 / AC-SN-43: the Reserve filter, chip, column and the toolbar Actions menu on the
 * Documents list. Services are mocked (not the hooks) so the wiring from hook to service is
 * part of what is tested.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

let searchParamsValue = '';
vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/order-inquiries',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(searchParamsValue),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));
vi.mock('@/services/userSelectService', () => ({ getUserLookup: vi.fn(async () => []) }));

let permissions = new Set<string>();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => permissions.has(slug),
}));

const SUMMARY = {
  parked: 17,
  needs_reserve: 0,
  notice_parked: 0,
  reserved: 0,
  my_parked_requests: 17,
  my_parked_notices: 3,
};
const getSummarySpy = vi.fn(async () => ({ ...SUMMARY }));
const previewRequestsSpy = vi.fn(async () => ({ emails: [], unresolved: [] }));
const previewNoticesSpy = vi.fn(async () => ({ emails: [], unresolved: [] }));
vi.mock('../../_shared/services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../_shared/services/orderInquiryReserveService')
  >();
  return {
    ...actual,
    getReserveStatusSummary: () => getSummarySpy(),
    previewParkedReserveRequests: () => previewRequestsSpy(),
    previewParkedReserveNotices: () => previewNoticesSpy(),
    sendParkedReserveRequests: vi.fn(),
    sendParkedReserveNotices: vi.fn(),
  };
});

let headers: Array<Record<string, unknown>> = [];
const listSpy = vi.fn(async (_params: Record<string, unknown>) => ({
  data: headers,
  total: headers.length,
  page: 1,
  limit: 25,
}));
vi.mock('../../_shared/services/orderInquiryService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../_shared/services/orderInquiryService')>();
  return {
    ...actual,
    listOrderInquiryHeaders: (params: Record<string, unknown>) => listSpy(params),
    getOrderInquiryWorklistSummary: vi.fn(async () => ({ agents: [], projects: [] })),
  };
});

import { OrderInquiryHeadersList } from './OrderInquiryHeadersList';

function header(over: Record<string, unknown>) {
  return {
    id: 'h-1',
    inquiry_no: 'OI-0001',
    so_number: 'SO000123',
    sales_order_id: 'so-1',
    status: 'open',
    raised_at: '2026-10-01T00:00:00',
    reserve_status: null,
    parked_by_name: null,
    ...over,
  };
}

function renderList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OrderInquiryHeadersList />
    </QueryClientProvider>,
  );
}

async function openReserveOptions() {
  const filtersButton = await screen.findByRole('button', { name: /filters/i });
  fireEvent.pointerDown(filtersButton, { button: 0, ctrlKey: false });
  fireEvent.click(filtersButton);
  fireEvent.click(await screen.findByLabelText('Reserve'));
}

beforeEach(() => {
  vi.clearAllMocks();
  searchParamsValue = '';
  headers = [header({ reserve_status: 'parked', parked_by_name: 'Joey' })];
  permissions = new Set(['projects.order_inquiries.acknowledge', 'projects.order_inquiries.reserve']);
  getSummarySpy.mockImplementation(async () => ({ ...SUMMARY }));
});

describe('AC-SN-42 Reserve filter, chip and column', () => {
  it('Filters offers Reserve with the four statuses and their summary counts', async () => {
    renderList();
    await waitFor(() => expect(getSummarySpy).toHaveBeenCalled());
    await openReserveOptions();
    for (const name of [
      'Parked request to reserve (17)',
      'Needs reserve (0)',
      'Parked reserve (0)',
      'Reserved (0)',
    ]) {
      expect(await screen.findByRole('option', { name })).toBeInTheDocument();
    }
  });

  it('choosing one sends reserve= to the list call, shows the chip and sets state to All', async () => {
    renderList();
    await waitFor(() => expect(getSummarySpy).toHaveBeenCalled());
    await openReserveOptions();
    fireEvent.click(await screen.findByRole('option', { name: 'Needs reserve (0)' }));

    await waitFor(() =>
      expect(listSpy).toHaveBeenLastCalledWith(
        expect.objectContaining({ reserve: 'needs_reserve', state: 'all' }),
      ),
    );
    expect(await screen.findByText('Reserve: Needs reserve')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'All' })).toBeChecked();
  });

  it('landing with ?reserve=needs_reserve has it applied', async () => {
    searchParamsValue = 'reserve=needs_reserve';
    renderList();
    await waitFor(() =>
      expect(listSpy).toHaveBeenCalledWith(expect.objectContaining({ reserve: 'needs_reserve' })),
    );
    expect(await screen.findByText('Reserve: Needs reserve')).toBeInTheDocument();
  });

  it('the Reserve column shows the pill and, for parked, the parker name', async () => {
    renderList();
    const row = (await screen.findByText('OI-0001')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Parked request to reserve')).toBeInTheDocument();
    expect(within(row).getByText('Joey')).toBeInTheDocument();
  });

  it('a Needs reserve row shows its pill and no parker name', async () => {
    headers = [header({ reserve_status: 'needs_reserve', parked_by_name: null })];
    renderList();
    const row = (await screen.findByText('OI-0001')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Needs reserve')).toBeInTheDocument();
    expect(within(row).queryByText('Joey')).not.toBeInTheDocument();
  });
});

describe('AC-SN-43 toolbar Actions menu', () => {
  it('an acknowledge holder sees "Send all parked requests to CS (17)" inside Actions, never as a top-bar button', async () => {
    permissions = new Set(['projects.order_inquiries.acknowledge']);
    renderList();
    await waitFor(() => expect(getSummarySpy).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /send all parked/i })).not.toBeInTheDocument();
    const trigger = await screen.findByRole('button', { name: /^actions$/i });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    const item = await screen.findByRole('menuitem', { name: 'Send all parked requests to CS (17)' });
    expect(item).not.toHaveAttribute('data-disabled');
    expect(screen.queryByRole('menuitem', { name: /parked reserves to purchasing/i })).not.toBeInTheDocument();
  });

  it('a reserve holder sees "Send all parked reserves to purchasing (3)"', async () => {
    permissions = new Set(['projects.order_inquiries.reserve']);
    renderList();
    await waitFor(() => expect(getSummarySpy).toHaveBeenCalled());
    const trigger = await screen.findByRole('button', { name: /^actions$/i });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    expect(
      await screen.findByRole('menuitem', { name: 'Send all parked reserves to purchasing (3)' }),
    ).toBeInTheDocument();
  });

  it('is disabled when the own count is 0', async () => {
    permissions = new Set(['projects.order_inquiries.acknowledge']);
    getSummarySpy.mockResolvedValue({ ...SUMMARY, my_parked_requests: 0 });
    renderList();
    await waitFor(() => expect(getSummarySpy).toHaveBeenCalled());
    const trigger = await screen.findByRole('button', { name: /^actions$/i });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    const item = await screen.findByRole('menuitem', { name: 'Send all parked requests to CS (0)' });
    expect(item).toHaveAttribute('data-disabled');
  });

  it('choosing the item loads the preview (opens the send dialog)', async () => {
    permissions = new Set(['projects.order_inquiries.acknowledge']);
    renderList();
    const trigger = await screen.findByRole('button', { name: /^actions$/i });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Send all parked requests to CS (17)' }));
    await waitFor(() => expect(previewRequestsSpy).toHaveBeenCalled());
  });

  it('no Actions menu without either permission', async () => {
    permissions = new Set();
    renderList();
    await screen.findByText('OI-0001');
    expect(screen.queryByRole('button', { name: /^actions$/i })).not.toBeInTheDocument();
  });
});
