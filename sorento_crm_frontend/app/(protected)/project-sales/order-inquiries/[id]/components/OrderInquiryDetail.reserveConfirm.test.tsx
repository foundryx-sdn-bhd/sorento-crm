/**
 * AC-SN-41: Reserve (N) opens a confirmation instead of committing; the box decides
 * `park_notice`. Harness follows `OrderInquiryDetail.reserveStaging.test.tsx`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  OrderInquiryHeaderDetail,
  OrderInquiryWorklistRow,
} from '../../../_shared/types/orderInquiry.types';
import type {
  CommitReservePayload,
  OrderInquiryReserveRequest,
} from '../../../_shared/services/orderInquiryReserveService';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/project-sales/order-inquiries/oi-1',
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { id: 'u-1' } }, status: 'authenticated' }),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('@/components/ui/tooltip', async () => {
  const actual = await vi.importActual<typeof import('@/components/ui/tooltip')>(
    '@/components/ui/tooltip',
  );
  return { ...actual, TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</> };
});

const toastSuccessSpy = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccessSpy(...a),
    error: vi.fn(),
    warning: vi.fn(),
    dismiss: vi.fn(),
  },
}));

const HEADER = {
  id: 'oi-1',
  inquiry_no: 'OI-2609-0612',
  legacy_inquiry_no: null,
  raised_at: '2026-09-05T08:00:00',
  raised_by_name: 'Eling',
  sales_order_id: 'so-1',
  project_sales_order_id: 'pso-1',
  so_number: 'SO123456',
  so_date: '2026-09-01',
  customer_name: 'Optad',
  customer_code: 'CUST-1',
  project_id: null,
  project_title: null,
  agent_name: null,
  lines_total: 1,
  lines_to_confirm: 0,
  qty_total: '107',
  status: 'outstanding',
  order_type: null,
  raise_history: [],
} as unknown as OrderInquiryHeaderDetail;

const REQUESTED_ROW = {
  id: 'row-requested',
  order_inquiry_id: 'oi-1',
  item_code: 'ZZT-REQUESTED',
  qty: '107',
  delivery_date: null,
  supplier: null,
  po_number: null,
  location: 'BRW-BB',
  product_id: 'prod-1',
  verb: 'ORDER',
  note: null,
  state: 'raised',
  ack_state: 'awaiting',
  links: [],
  linked_qty: '0',
  bundled_qty: '0',
  reserved_qty: '0',
  reserve_state: 'requested',
  requested_qty: '107',
} as unknown as OrderInquiryWorklistRow;

const OPEN_REQUEST: OrderInquiryReserveRequest = {
  id: 'rr-1',
  order_inquiry_id: 'oi-1',
  ordinal: 2,
  state: 'requested',
  requested_by: 'user-1',
  requested_by_name: 'Joey',
  requested_at: '2026-09-22T09:00:00',
  note: null,
  reserved_by_name: null,
  reserved_at: null,
  cancelled_at: null,
  first_to_name: null,
  rows: [
    {
      id: 'reqrow-1',
      row_id: 'row-requested',
      item_code: 'ZZT-REQUESTED',
      qty_requested: '107',
      warehouse_id: 'wh-brw',
      location: 'BRW',
      qty_reserved: null,
      reason: null,
    },
  ],
};

vi.mock('../../../_shared/services/orderInquiryService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../_shared/services/orderInquiryService')>();
  return {
    ...actual,
    getOrderInquiryHeader: vi.fn(async () => HEADER),
    getOrderInquiryHeaderLines: vi.fn(async () => [REQUESTED_ROW]),
    getOrderInquiryHeaderRelatedDocuments: vi.fn(async () => ({ purchase_orders: [], spos: [] })),
    listOrderInquiryHeaders: vi.fn(async () => ({ data: [], total: 0, page: 1, limit: 25 })),
  };
});

const commitSpy = vi.fn<
  (id: string, payload: CommitReservePayload & { park_notice?: boolean }) => Promise<OrderInquiryReserveRequest[]>
>(async () => [{ ...OPEN_REQUEST, state: 'reserved' }]);
const recipientsSpy = vi.fn(async (_inquiryId: string, _rowIds?: string[]) => ({
  to: { name: 'Joey' },
  cc: [{ name: 'Eling' }],
}));
vi.mock('../../../_shared/services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../_shared/services/orderInquiryReserveService')
  >();
  return {
    ...actual,
    getOrderInquiryReserveRequests: vi.fn(async () => [OPEN_REQUEST]),
    commitOrderInquiryReserve: (id: string, payload: CommitReservePayload) => commitSpy(id, payload),
    getReserveNoticeRecipients: (id: string, rowIds?: string[]) => recipientsSpy(id, rowIds),
  };
});

vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(async () => undefined),
  getCurrentPendingAction: vi.fn(async () => ({ pending: null, last_outcome: null })),
}));
vi.mock('../../../_shared/services/fulfilmentPlanningService', () => ({
  getStockDetail: vi.fn(async () => ({
    locations: [{ warehouse_id: 'wh-brw', location: 'BRW', available_qty: '107' }],
  })),
}));

import { OrderInquiryDetail } from './OrderInquiryDetail';
import { getOrderInquiryHeaderLines } from '../../../_shared/services/orderInquiryService';
import { getOrderInquiryReserveRequests } from '../../../_shared/services/orderInquiryReserveService';

function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OrderInquiryDetail id="oi-1" />
    </QueryClientProvider>,
  );
}

async function stageAndPressReserve() {
  await screen.findByText('ZZT-REQUESTED');
  const gridRow = screen.getAllByText('ZZT-REQUESTED').map((e) => e.closest('tr')).find(Boolean) as HTMLElement;
  fireEvent.click(within(gridRow).getByLabelText('Reserve'));
  await screen.findByText('Reserve 107 @ BRW');
  fireEvent.click(screen.getByRole('button', { name: /^reserve \(1\)$/i }));
}

beforeEach(() => vi.clearAllMocks());

describe('AC-SN-41 reserve confirmation', () => {
  it('Reserve (N) opens the confirmation and does not commit', async () => {
    renderDetail();
    await stageAndPressReserve();

    const dialog = await screen.findByRole('dialog', { name: /reserve 1 line on OI-2609-0612/i });
    expect(within(dialog).getByText(/notice to purchasing: to joey, cc eling/i)).toBeInTheDocument();
    expect(recipientsSpy).toHaveBeenCalledWith('oi-1', ['row-requested']);
    expect(commitSpy).not.toHaveBeenCalled();
  });

  it('Cancel commits nothing and keeps the staged chip', async () => {
    renderDetail();
    await stageAndPressReserve();
    const dialog = await screen.findByRole('dialog', { name: /reserve 1 line/i });
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));

    expect(commitSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /reserve 1 line/i })).not.toBeInTheDocument());
    expect(screen.getByText('Reserve 107 @ BRW')).toBeInTheDocument();
  });

  it('unticked: commits with park_notice false and the usual toast', async () => {
    renderDetail();
    await stageAndPressReserve();
    fireEvent.click(await screen.findByRole('button', { name: 'Reserve and send to Joey' }));

    await waitFor(() => expect(commitSpy).toHaveBeenCalledTimes(1));
    expect(commitSpy.mock.calls[0][1]).toMatchObject({ park_notice: false });
    await waitFor(() =>
      expect(toastSuccessSpy).toHaveBeenCalledWith(expect.stringMatching(/reserved, .* notified/i)),
    );
  });

  it('D5: the sent toast names the confirmation To person, not the requester', async () => {
    recipientsSpy.mockImplementation(async () => ({ to: { name: 'Niki' }, cc: [{ name: 'Eling' }] }));
    try {
      renderDetail();
      await stageAndPressReserve();
      fireEvent.click(await screen.findByRole('button', { name: 'Reserve and send to Niki' }));

      await waitFor(() => expect(commitSpy).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(toastSuccessSpy).toHaveBeenCalledWith('Reserved, Niki notified'));
    } finally {
      recipientsSpy.mockImplementation(async () => ({ to: { name: 'Joey' }, cc: [{ name: 'Eling' }] }));
    }
  });

  it('ticked: commits with park_notice true and the parked toast', async () => {
    renderDetail();
    await stageAndPressReserve();
    fireEvent.click(await screen.findByLabelText(/park notice first/i));
    fireEvent.click(screen.getByRole('button', { name: 'Reserve and park notice' }));

    await waitFor(() => expect(commitSpy).toHaveBeenCalledTimes(1));
    expect(commitSpy.mock.calls[0][1]).toMatchObject({ park_notice: true });
    await waitFor(() =>
      expect(toastSuccessSpy).toHaveBeenCalledWith(
        'Reserved. Notice to Joey parked; send it from Order inquiries > Actions.',
      ),
    );
  });

  it('S2: an amend-only commit asks for the recipients of the STAGED row ids', async () => {
    const reservedRow = {
      ...REQUESTED_ROW,
      id: 'row-reserved',
      item_code: 'ZZT-RESERVED',
      reserve_state: 'reserved',
      reserved_qty: '30',
    } as unknown as OrderInquiryWorklistRow;
    const answered: OrderInquiryReserveRequest = {
      ...OPEN_REQUEST,
      id: 'rr-0',
      ordinal: 1,
      state: 'reserved',
      reserved_by_name: 'Eling',
      rows: [
        {
          ...OPEN_REQUEST.rows[0],
          id: 'reqrow-0',
          row_id: 'row-reserved',
          item_code: 'ZZT-RESERVED',
          qty_requested: '50',
          qty_reserved: '30',
        },
      ],
    };
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValue([reservedRow]);
    vi.mocked(getOrderInquiryReserveRequests).mockResolvedValue([answered]);
    renderDetail();
    await screen.findByText('ZZT-RESERVED');
    const gridRow = screen.getAllByText('ZZT-RESERVED').map((e) => e.closest('tr')).find(Boolean) as HTMLElement;
    fireEvent.click(within(gridRow).getByLabelText('Amend reserve'));
    await screen.findByRole('dialog', { name: /ZZT-RESERVED/i });
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: '20 went back' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await screen.findByText('Amend to 10');
    fireEvent.click(screen.getByRole('button', { name: /^reserve \(1\)$/i }));

    await waitFor(() => expect(recipientsSpy).toHaveBeenCalledWith('oi-1', ['row-reserved']));
    const dialog = await screen.findByRole('dialog', { name: /reserve 1 line/i });
    expect(within(dialog).getByText('Notice to purchasing: To Joey, Cc Eling')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Reserve and send to Joey' })).toBeInTheDocument();
  });
});
