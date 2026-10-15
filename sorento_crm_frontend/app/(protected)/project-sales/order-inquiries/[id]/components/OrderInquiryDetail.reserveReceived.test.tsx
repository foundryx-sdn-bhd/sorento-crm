/**
 * OI-RESERVE-RECEIVED (UAC AC-RR-40..42, `oi-reserve-received-acceptance-criteria.md`).
 * The Lines tab's "Request CS to reserve" action reads the server's own
 * `reserve_requestable_qty` (AC-RR-5) instead of re-deriving "received" client-side:
 *  - a placed row with requestable > 0 is selectable and the dialog defaults to and caps
 *    at requestable (AC-RR-40);
 *  - requestable <= 0 with a PO/SPO link reads the "not received yet" tooltip (AC-RR-41);
 *  - every other tooltip is unchanged (AC-RR-42).
 * Same harness as `OrderInquiryDetail.reserveRemaining.test.tsx`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  OrderInquiryHeaderDetail,
  OrderInquiryWorklistRow,
} from '../../../_shared/types/orderInquiry.types';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/project-sales/order-inquiries/oi-1',
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));

// S1 (reviewer round): `OrderInquiryDetail` reads `useSession` directly now.
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { id: 'test-current-user' } }, status: 'authenticated' }),
}));

// Under jsdom nothing answers the column-preferences fetch `DataGrid` starts, so the
// Lines tab grid renders skeletons forever and no row is assertable
// (project_datagrid_jsdom_rows_mockable.md).
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

// Radix Tooltip only mounts TooltipContent's portal on hover, which a plain render+query
// cannot see (`orderInquiryWorklistColumns.test.tsx`'s own precedent) - rendered inline
// instead so the disabled menu item's own reason is queryable without simulating hover.
vi.mock('@/components/ui/tooltip', async () => {
  const actual = await vi.importActual<typeof import('@/components/ui/tooltip')>(
    '@/components/ui/tooltip',
  );
  return {
    ...actual,
    TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  };
});

const HEADER: OrderInquiryHeaderDetail = {
  id: 'oi-1',
  inquiry_no: 'OI-2609-0001',
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
  lines_total: 2,
  lines_to_confirm: 0,
  qty_total: '30',
  status: 'outstanding',
  order_type: null,
  raise_history: [],
};

function reserveRow(over: Partial<OrderInquiryWorklistRow>): OrderInquiryWorklistRow {
  return {
    id: 'row-partial',
    order_inquiry_id: 'oi-1',
    item_code: 'ZZT-PARTIAL',
    qty: '139',
    delivery_date: null,
    supplier: null,
    po_number: null,
    // No stock location on either fixture row: `useReserveRowOptions` short-circuits to
    // its own empty result with no network call at all when a row carries neither a
    // location nor a product id, which is all this test needs - the Requested field's
    // default/max, never the Location select.
    location: null,
    product_id: null,
    verb: 'ORDER',
    note: null,
    state: 'raised',
    ack_state: 'awaiting',
    links: [],
    linked_qty: '0',
    bundled_qty: '0',
    reserved_qty: '0',
    reserve_state: null,
    ...over,
  } as unknown as OrderInquiryWorklistRow;
}

const SPO_LINK = {
  id: 'lk-1',
  kind: 'spo',
  document: 'SPO-000123',
  qty: '1260',
  received: true,
};

const PLACED_RECEIVED = reserveRow({
  id: 'row-received',
  item_code: 'SRT6638',
  qty: '1260',
  state: 'placed',
  linked_qty: '1260',
  links: [SPO_LINK],
  reserve_requestable_qty: 1260,
} as Partial<OrderInquiryWorklistRow>);

const PLACED_OPEN = reserveRow({
  id: 'row-open',
  item_code: 'ZZT-OPEN',
  qty: '100',
  state: 'placed',
  linked_qty: '100',
  links: [{ ...SPO_LINK, id: 'lk-2', qty: '100', received: false }],
  reserve_requestable_qty: 0,
} as Partial<OrderInquiryWorklistRow>);

const RESERVED_NO_LINK = reserveRow({
  id: 'row-reserved',
  item_code: 'ZZT-NOLINK',
  qty: '50',
  state: 'raised',
  reserved_qty: '50',
  reserve_state: 'reserved',
  reserve_requestable_qty: 0,
} as Partial<OrderInquiryWorklistRow>);

const ACTIONED = reserveRow({
  id: 'row-actioned',
  item_code: 'ZZT-ACTIONED',
  qty: '20',
  state: 'actioned',
  reserve_requestable_qty: 0,
} as Partial<OrderInquiryWorklistRow>);

const OPEN_REQUEST_ROW = reserveRow({
  id: 'row-requested',
  item_code: 'ZZT-REQUESTED',
  qty: '30',
  state: 'raised',
  reserve_state: 'requested',
  reserve_requestable_qty: 30,
} as Partial<OrderInquiryWorklistRow>);

// AC-RR-43: 1092 on a received SPO, an open request for all 1092, CS stages 435.
const BALANCE_ROW = reserveRow({
  id: 'row-balance',
  item_code: 'ZZT-BAL',
  qty: '1092',
  state: 'placed',
  linked_qty: '1092',
  links: [{ ...SPO_LINK, id: 'lk-3', qty: '1092' }],
  reserve_state: 'requested',
  requested_qty: '1092',
  reserve_requestable_qty: 1092,
  location: 'BRW-BB',
  product_id: 'prod-1',
} as Partial<OrderInquiryWorklistRow>);

const BALANCE_REQUEST = {
  id: 'rr-1',
  order_inquiry_id: 'oi-1',
  ordinal: 1,
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
      row_id: 'row-balance',
      item_code: 'ZZT-BAL',
      qty_requested: '1092',
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
    getOrderInquiryHeaderLines: vi.fn(async () => [
      PLACED_RECEIVED,
      PLACED_OPEN,
      RESERVED_NO_LINK,
      ACTIONED,
      OPEN_REQUEST_ROW,
      BALANCE_ROW,
    ]),
    getOrderInquiryHeaderRelatedDocuments: vi.fn(async () => ({ purchase_orders: [], spos: [] })),
    listOrderInquiryHeaders: vi.fn(async () => ({ data: [], total: 0, page: 1, limit: 25 })),
    acknowledgeOrderInquiryRowsByFilter: vi.fn(async () => ({ acknowledged: 0, results: [] })),
    acknowledgeOrderInquiryRows: vi.fn(async () => ({ acknowledged: 0, results: [] })),
    autoPlaceOrderInquiryRows: vi.fn(async () => ({ linked: 0, results: [] })),
  };
});

vi.mock('../../../_shared/services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../_shared/services/orderInquiryReserveService')
  >();
  return {
    ...actual,
    createOrderInquiryReserveRequest: vi.fn(async () => ({
      id: 'rr-1', ordinal: 1, first_to_name: 'Eling',
    })),
    getOrderInquiryReserveRequests: vi.fn(async () => [BALANCE_REQUEST]),
    getReserveNoticeRecipients: vi.fn(async () => ({ to: { name: 'Joey' }, cc: [] })),
  };
});

vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(async () => undefined),
  getCurrentPendingAction: vi.fn(async () => ({ pending: null, last_outcome: null })),
}));
vi.mock('../../../_shared/services/fulfilmentPlanningService', () => ({
  getStockDetail: vi.fn(async () => ({
    locations: [{ warehouse_id: 'wh-brw', location: 'BRW', available_qty: '1092' }],
  })),
}));

import { OrderInquiryDetail } from './OrderInquiryDetail';

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

function openGearMenu() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Order inquiry options' }), {
    button: 0,
  });
}

async function selectOnly(item: string) {
  renderDetail();
  await screen.findByText(item);
  fireEvent.click(await screen.findByLabelText(`Select ${item}`));
  openGearMenu();
  return screen.findByRole('menuitem', { name: /request cs to reserve/i });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AC-RR-40: a placed row with requestable > 0 can be requested', () => {
  it('enables the action and defaults and caps the dialog at requestable (1260)', async () => {
    const item = await selectOnly('SRT6638');
    expect(item).not.toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(item);

    const requestedInput = (await screen.findByLabelText('Requested')) as HTMLInputElement;
    expect(requestedInput.value).toBe('1260');
    expect(requestedInput.max).toBe('1260');
  });
});

describe('AC-RR-41: requestable <= 0 on a row with a PO/SPO link', () => {
  it('disables the action with the "not received yet" tooltip', async () => {
    const item = await selectOnly('ZZT-OPEN');
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(
      screen.getByText('ZZT-OPEN: on a PO/SPO not received yet, nothing left to request'),
    ).toBeInTheDocument();
  });
});

describe('AC-RR-42: the other tooltips are unchanged', () => {
  it('actioned: not open for a reserve request', async () => {
    const item = await selectOnly('ZZT-ACTIONED');
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('ZZT-ACTIONED: not open for a reserve request')).toBeInTheDocument();
  });

  it('requestable <= 0 with no PO/SPO link: has nothing left to request', async () => {
    const item = await selectOnly('ZZT-NOLINK');
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('ZZT-NOLINK: has nothing left to request')).toBeInTheDocument();
  });

  it('open request: already has an open reserve request', async () => {
    const item = await selectOnly('ZZT-REQUESTED');
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(
      screen.getByText('ZZT-REQUESTED: already has an open reserve request'),
    ).toBeInTheDocument();
  });
});

describe('AC-RR-43: the confirm dialog Balance counts the received part', () => {
  it('1092 on a received SPO, requested 1092, CS stages 435: Balance to buy shows 657', async () => {
    renderDetail();
    await screen.findByText('ZZT-BAL');
    const gridRow = screen.getAllByText('ZZT-BAL').map((e) => e.closest('tr')).find(Boolean) as HTMLElement;
    fireEvent.click(within(gridRow).getByLabelText('Edit reserve'));
    await screen.findByRole('dialog', { name: /ZZT-BAL/i });
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '435' } });
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: 'BRW only has 435' } });
    fireEvent.click(screen.getByRole('button', { name: /^stage$/i }));
    await screen.findByText(/Reserve 435 @/);
    fireEvent.click(screen.getByRole('button', { name: /^reserve \(1\)$/i }));

    const dialog = await screen.findByRole('dialog', { name: /reserve 1 line/i });
    const header = within(dialog).getByText('Balance to buy');
    const index = Array.from(header.parentElement!.children).indexOf(header);
    const cells = within(dialog).getAllByRole('cell');
    const balanceCell = cells[index];
    expect(balanceCell).toHaveTextContent('657');
  });
});
