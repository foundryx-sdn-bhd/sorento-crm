/**
 * OI-RESEND-PURCHASING: tick order inquiries on the Documents list, "Send to purchasing", one
 * preview, one Send. Services are mocked (not the hooks) so hook-to-service wiring is tested.
 * Harness copied from `OrderInquiryHeadersList.reserveStatus.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
  ResizeObserverStub;
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
  useListingColumnPreferences: () => ({
    resetToDefaults: async () => {},
    isLoading: false,
  }),
}));
vi.mock('@/services/userSelectService', () => ({
  getUserLookup: vi.fn(async () => []),
}));

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
vi.mock(
  '../../_shared/services/orderInquiryReserveService',
  async (importOriginal) => {
    const actual =
      await importOriginal<
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
  },
);

let headers: Array<Record<string, unknown>> = [];
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- the signature the list service is called with
const listSpy = vi.fn(async (_params: Record<string, unknown>) => ({
  data: headers,
  total: headers.length,
  page: 1,
  limit: 25,
}));
vi.mock(
  '../../_shared/services/orderInquiryService',
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import('../../_shared/services/orderInquiryService')
      >();
    return {
      ...actual,
      listOrderInquiryHeaders: (params: Record<string, unknown>) =>
        listSpy(params),
      getOrderInquiryWorklistSummary: vi.fn(async () => ({
        agents: [],
        projects: [],
      })),
    };
  },
);

const previewResendSpy = vi.fn();
const sendResendSpy = vi.fn();
vi.mock('../../_shared/services/orderInquiryHandoverResendService', () => ({
  previewHandoverResend: (ids: string[]) => previewResendSpy(ids),
  sendHandoverResend: (ids: string[]) => sendResendSpy(ids),
}));

import { OrderInquiryHeadersList } from './OrderInquiryHeadersList';
import { lastSentText } from '../../_shared/components/HandoverResendDialog';

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

function email(id: string, no: string, lastSent: unknown = null) {
  return {
    inquiry_id: id,
    inquiry_no: no,
    to: { name: 'Purchasing Buyer' },
    cc: [{ name: 'Aina' }],
    inquiry_count: 1,
    line_count: 3,
    subject: `OI: BRW-BB @ SO416690 (Resent) ${no}`,
    body_html: `<p>body-${no}</p>`,
    last_sent: lastSent,
    attachments: [`${no}.xlsx`],
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

async function tickAndOpen(nos: string[]) {
  for (const no of nos)
    fireEvent.click(
      await screen.findByRole('checkbox', { name: `Select ${no}` }),
    );
  fireEvent.click(
    await screen.findByRole('button', { name: /send to purchasing/i }),
  );
  return screen.findByRole('dialog');
}

beforeEach(() => {
  vi.clearAllMocks();
  searchParamsValue = '';
  headers = [
    header({ id: 'h-1', inquiry_no: 'OI-0001' }),
    header({ id: 'h-2', inquiry_no: 'OI-0002' }),
    header({ id: 'h-3', inquiry_no: 'OI-0003' }),
  ];
  permissions = new Set(['projects.order_inquiry.action']);
  getSummarySpy.mockImplementation(async () => ({ ...SUMMARY }));
  previewResendSpy.mockImplementation(async () => ({
    emails: [email('h-1', 'OI-0001'), email('h-2', 'OI-0002')],
    inquiry_total: 2,
    unresolved: [],
    skipped: [
      { inquiry_id: 'h-3', inquiry_no: 'OI-0003', reason: 'nothing_open' },
    ],
    automation_enabled: false,
  }));
  sendResendSpy.mockImplementation(async () => ({
    emails_sent: 2,
    items_sent: 2,
    skipped: [],
  }));
});

describe('Send to purchasing from the Documents list', () => {
  it('ticked OIs open one preview with the off warning, the skip line and Last sent', async () => {
    renderList();
    const dialog = await tickAndOpen(['OI-0001', 'OI-0002', 'OI-0003']);
    await waitFor(() =>
      expect(previewResendSpy).toHaveBeenCalledWith(['h-1', 'h-2', 'h-3']),
    );
    expect(within(dialog).getByText('Send to purchasing')).toBeInTheDocument();
    expect(
      await within(dialog).findByText(
        'The automatic email to purchasing is switched off. This send goes out anyway.',
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText('OI-0003: nothing open, not sent'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText('No handover email on record'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText('Attached: OI-0001.xlsx'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('tab', { name: 'OI-0002' }),
    ).toBeInTheDocument();
  });

  it('Send posts only the sendable OIs and closes', async () => {
    renderList();
    const dialog = await tickAndOpen(['OI-0001', 'OI-0002', 'OI-0003']);
    fireEvent.click(
      await within(dialog).findByRole('button', { name: 'Send 2 emails' }),
    );
    await waitFor(() =>
      expect(sendResendSpy).toHaveBeenCalledWith(['h-1', 'h-2']),
    );
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    // A clean send clears the ticks.
    expect(
      screen.getByRole('checkbox', { name: 'Select OI-0001' }),
    ).not.toBeChecked();
  });

  it('a partial failure keeps the dialog open on the OI that did not go', async () => {
    sendResendSpy.mockImplementation(async () => ({
      emails_sent: 1,
      items_sent: 1,
      skipped: [{ inquiry_id: 'h-2', inquiry_no: 'OI-0002', reason: 'failed' }],
    }));
    renderList();
    const dialog = await tickAndOpen(['OI-0001', 'OI-0002']);
    fireEvent.click(
      await within(dialog).findByRole('button', { name: 'Send 2 emails' }),
    );
    await waitFor(() =>
      expect(previewResendSpy).toHaveBeenLastCalledWith(['h-2']),
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('a preview that fails closes the dialog instead of loading forever', async () => {
    previewResendSpy.mockImplementation(async () => {
      throw new Error('Choose at least one order inquiry.');
    });
    renderList();
    await tickAndOpen(['OI-0001']);
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
  });

  it('without the action grant there is nothing to tick', async () => {
    permissions = new Set(['projects.order_inquiries.acknowledge']);
    renderList();
    await screen.findByText('OI-0001');
    expect(
      screen.queryByRole('checkbox', { name: 'Select OI-0001' }),
    ).not.toBeInTheDocument();
  });
});

describe('lastSentText', () => {
  it('names who resent it, or says automatic', () => {
    expect(
      lastSentText({ at: '2026-10-15T08:02:00Z', kind: 'resent', by: 'Aina' }),
    ).toBe('Last sent to purchasing: 15/10/2026, 4:02 pm (resent by Aina)');
    expect(
      lastSentText({ at: '2026-10-13T02:41:00Z', kind: 'automatic', by: null }),
    ).toBe('Last sent to purchasing: 13/10/2026, 10:41 am (automatic)');
    expect(lastSentText(null)).toBe('No handover email on record');
  });
});
