/**
 * STOCK-DEBT-SNAPSHOT AC-F6c: the two "Send all parked ..." Actions items and their
 * `ParkedSendDialog`, extracted from `OrderInquiryHeadersList.tsx` into one hook the Order
 * inquiries list and the snapshot page both use. Services are mocked (not the hooks), the
 * same way `OrderInquiryHeadersList.reserveStatus.test.tsx` does, so hook -> service wiring
 * is part of what is tested.
 */
import React from 'react';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

let permissions = new Set<string>();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => permissions.has(slug),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const SUMMARY = {
  parked: 2,
  needs_reserve: 0,
  notice_parked: 0,
  reserved: 0,
  my_parked_requests: 2,
  my_parked_notices: 3,
};
const getSummarySpy = vi.fn(async () => ({ ...SUMMARY }));
const EMAIL = {
  to: { name: 'Jayson Foundryx' },
  cc: [],
  inquiry_count: 2,
  line_count: 7,
  subject: 'Reserve requests: 2 order inquiries',
  body_html: '<p>2 order inquiries need your reserve</p>',
};
const previewRequestsSpy = vi.fn(async () => ({ emails: [EMAIL], unresolved: [] }));
const previewNoticesSpy = vi.fn(async () => ({ emails: [EMAIL], unresolved: [] }));
const sendRequestsSpy = vi.fn(async () => ({ emails_sent: 1, items_sent: 2 }));
const sendNoticesSpy = vi.fn(async () => ({ emails_sent: 1, items_sent: 3 }));
vi.mock('../services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/orderInquiryReserveService')>();
  return {
    ...actual,
    getReserveStatusSummary: () => getSummarySpy(),
    previewParkedReserveRequests: () => previewRequestsSpy(),
    previewParkedReserveNotices: () => previewNoticesSpy(),
    sendParkedReserveRequests: () => sendRequestsSpy(),
    sendParkedReserveNotices: () => sendNoticesSpy(),
  };
});

import { useParkedSendActions } from './useParkedSendActions';

const ACK = 'projects.order_inquiries.acknowledge';
const RESERVE = 'projects.order_inquiries.reserve';

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useParkedSendActions(), { wrapper });
}

/** Re-renders the hook's dialog node alongside a fresh read of the hook result. */
function DialogHost({ result }: { result: { current: ReturnType<typeof useParkedSendActions> } }) {
  return <>{result.current.dialog}</>;
}

beforeEach(() => {
  vi.clearAllMocks();
  permissions = new Set([ACK, RESERVE]);
  getSummarySpy.mockImplementation(async () => ({ ...SUMMARY }));
});

describe('items and counts', () => {
  it('a holder of both permissions gets both items with N from the own-parked summary', async () => {
    const { result } = setup();
    await waitFor(() =>
      expect(result.current.actions.map((a) => a.label)).toEqual([
        'Send all parked requests to CS (2)',
        'Send all parked reserves to purchasing (3)',
      ]),
    );
    expect(result.current.actions.every((a) => a.disabled === false)).toBe(true);
    expect(result.current.actions.every((a) => typeof a.onClick === 'function')).toBe(true);
    expect(new Set(result.current.actions.map((a) => a.key)).size).toBe(2);
  });

  it('acknowledge alone sees only the requests item', async () => {
    permissions = new Set([ACK]);
    const { result } = setup();
    await waitFor(() => expect(getSummarySpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(result.current.actions.map((a) => a.label)).toEqual(['Send all parked requests to CS (2)']),
    );
  });

  it('reserve alone sees only the reserves item', async () => {
    permissions = new Set([RESERVE]);
    const { result } = setup();
    await waitFor(() =>
      expect(result.current.actions.map((a) => a.label)).toEqual([
        'Send all parked reserves to purchasing (3)',
      ]),
    );
  });

  it('neither permission gives no actions at all', async () => {
    permissions = new Set();
    const { result } = setup();
    await waitFor(() => expect(getSummarySpy).toHaveBeenCalled());
    expect(result.current.actions).toEqual([]);
  });

  it('is greyed at 0 with the reason "Nothing of yours is parked"', async () => {
    getSummarySpy.mockResolvedValue({ ...SUMMARY, my_parked_requests: 0, my_parked_notices: 0 });
    const { result } = setup();
    await waitFor(() =>
      expect(result.current.actions.map((a) => a.label)).toEqual([
        'Send all parked requests to CS (0)',
        'Send all parked reserves to purchasing (0)',
      ]),
    );
    for (const action of result.current.actions) {
      expect(action.disabled).toBe(true);
      expect(action.disabledReason).toBe('Nothing of yours is parked');
    }
  });
});

describe('opening and sending', () => {
  it('pressing the requests item opens the ParkedSendDialog on the requests preview', async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.actions).toHaveLength(2));
    const view = render(<DialogHost result={result} />);

    act(() => result.current.actions[0].onClick());
    view.rerender(<DialogHost result={result} />);

    await waitFor(() => expect(previewRequestsSpy).toHaveBeenCalled());
    expect(previewNoticesSpy).not.toHaveBeenCalled();
    expect(await screen.findByText('Send all parked requests to CS')).toBeInTheDocument();
  });

  it('pressing the reserves item opens it on the purchasing preview', async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.actions).toHaveLength(2));
    const view = render(<DialogHost result={result} />);

    act(() => result.current.actions[1].onClick());
    view.rerender(<DialogHost result={result} />);

    await waitFor(() => expect(previewNoticesSpy).toHaveBeenCalled());
    expect(await screen.findByText('Send all parked reserves to purchasing')).toBeInTheDocument();
  });

  it('Send in the dialog sends the own parked requests', async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.actions).toHaveLength(2));
    const view = render(<DialogHost result={result} />);

    act(() => result.current.actions[0].onClick());
    view.rerender(<DialogHost result={result} />);

    fireEvent.click(await screen.findByRole('button', { name: /send 1 email/i }));
    await waitFor(() => expect(sendRequestsSpy).toHaveBeenCalledTimes(1));
    expect(sendNoticesSpy).not.toHaveBeenCalled();
  });

  it('the dialog node renders nothing visible until an item is pressed', async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.actions).toHaveLength(2));
    render(<DialogHost result={result} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(previewRequestsSpy).not.toHaveBeenCalled();
  });
});
