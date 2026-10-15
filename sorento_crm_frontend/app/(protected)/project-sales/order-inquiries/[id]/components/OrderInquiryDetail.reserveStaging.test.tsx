/**
 * `PLAN-oi-request-cs-reserve.md` section 6e (round 4, 24 Sep): CS reserves line by
 * line on the Lines grid itself - no per-row dialog, no multi-row dialog, no header
 * badge, no `Confirm all`. `oi-request-cs-reserve-acceptance-criteria.md`
 * AC-RS-83..89.
 *
 * TEST-FIRST (Phase 2): today (round 3 code, still on `OrderInquiryDetail.tsx`) the
 * Lines grid's own State cell renders `ReservePill` AS the click target (a single
 * button aria-label "Reserve", text "Request to reserve" with NO qty), which opens
 * `ReserveRowDialog` (tabs Reserve/History) - there is no staged map, no per-row
 * tick/pencil/undo icon set, no header `Reserve (N)` CTA, no State filter, and no
 * `ReserveLineHistoryDialog`. A red here is "no such chip" / "no such icon button" /
 * "the commit spy was never called" - the plan's own stated round-4 behaviour not
 * existing yet - never an import typo. Harness copied from
 * `OrderInquiryDetail.reserveIcon.test.tsx` (same mocked services, DataGrid
 * column-preferences stub, permission override).
 *
 * Field-name note for the captain: the worklist row type
 * (`_shared/types/orderInquiry.types.ts`) carries no field for "the open request's own
 * requested qty on this row" today (only `reserve_state` and `reserved_qty`) - AC-RS-83
 * needs that figure to print `Request to reserve 107`. This suite invents
 * `requested_qty` on the row fixture for it; the real implementation may resolve it off
 * the reserve-requests read instead (`openRequestForRow(...).qtyRequested`, already
 * plumbed) rather than adding a column - not this tester's call.
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
  OrderInquiryReserveHistoryEntry,
  OrderInquiryReserveRequest,
} from '../../../_shared/services/orderInquiryReserveService';

const replaceSpy = vi.fn();
let searchParamsValue = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceSpy }),
  usePathname: () => '/project-sales/order-inquiries/oi-1',
  useSearchParams: () => new URLSearchParams(searchParamsValue),
}));

let reservePermissionOverride: boolean | null = null;
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (permission: string) =>
    permission === 'projects.order_inquiries.reserve' && reservePermissionOverride !== null
      ? reservePermissionOverride
      : true,
}));

let sessionUserId = 'test-current-user';
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { id: sessionUserId } }, status: 'authenticated' }),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('@/components/ui/tooltip', async () => {
  const actual = await vi.importActual<typeof import('@/components/ui/tooltip')>(
    '@/components/ui/tooltip',
  );
  return {
    ...actual,
    TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  };
});

const toastSuccessSpy = vi.fn();
const toastErrorSpy = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccessSpy(...args),
    error: (...args: unknown[]) => toastErrorSpy(...args),
    warning: vi.fn(),
    dismiss: vi.fn(),
  },
}));

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
  lines_total: 3,
  lines_to_confirm: 0,
  qty_total: '30',
  status: 'outstanding',
  order_type: null,
  raise_history: [],
};

function row(over: Partial<OrderInquiryWorklistRow>): OrderInquiryWorklistRow {
  return {
    id: 'row-plain',
    order_inquiry_id: 'oi-1',
    item_code: 'ZZT-PLAIN',
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
    reserve_state: null,
    ...over,
  } as unknown as OrderInquiryWorklistRow;
}

const PLAIN_ROW = row({ id: 'row-plain', item_code: 'ZZT-PLAIN', reserve_state: null });
// Invented field, see the file-header note.
const REQUESTED_ROW = row({
  id: 'row-requested',
  item_code: 'ZZT-REQUESTED',
  reserve_state: 'requested',
  qty: '107',
  ...({ requested_qty: '107' } as Record<string, unknown>),
});
const RESERVED_ROW = row({
  id: 'row-reserved',
  item_code: 'ZZT-RESERVED',
  reserve_state: 'reserved',
  reserved_qty: '30',
});
// 6e.4 (AC-RS-83b): CS answered this one with Reserve 0.
const DECLINED_ROW = row({
  id: 'row-declined',
  item_code: 'ZZT-DECLINED',
  reserve_state: 'declined',
  reserved_qty: '0',
});

// The finished request that answered RESERVED_ROW (30 of 50 at DC1) and DECLINED_ROW
// (0 of 40) - the anchor Amend and History read (6e.4, B3).
const ANSWERED_REQUEST: OrderInquiryReserveRequest = {
  id: 'rr-0',
  order_inquiry_id: 'oi-1',
  ordinal: 1,
  state: 'reserved',
  requested_by: 'user-1',
  requested_by_name: 'Joey',
  requested_at: '2026-09-20T09:00:00',
  note: null,
  reserved_by_name: 'Eling',
  reserved_at: '2026-09-21T09:00:00',
  cancelled_at: null,
  first_to_name: null,
  rows: [
    {
      id: 'reqrow-0',
      row_id: 'row-reserved',
      item_code: 'ZZT-RESERVED',
      qty_requested: '50',
      warehouse_id: 'wh-dc1',
      location: 'DC1',
      qty_reserved: '30',
      reason: 'DC1 has 30',
    },
    {
      id: 'reqrow-0b',
      row_id: 'row-declined',
      item_code: 'ZZT-DECLINED',
      qty_requested: '40',
      warehouse_id: 'wh-brw',
      location: 'BRW',
      qty_reserved: '0',
      reason: 'none on hand',
    },
  ],
};

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
    getOrderInquiryHeaderLines: vi.fn(async () => [PLAIN_ROW, REQUESTED_ROW, RESERVED_ROW, DECLINED_ROW]),
    getOrderInquiryHeaderRelatedDocuments: vi.fn(async () => ({ purchase_orders: [], spos: [] })),
    listOrderInquiryHeaders: vi.fn(async () => ({ data: [], total: 0, page: 1, limit: 25 })),
    acknowledgeOrderInquiryRowsByFilter: vi.fn(async () => ({ acknowledged: 0, results: [] })),
    acknowledgeOrderInquiryRows: vi.fn(async () => ({ acknowledged: 0, results: [] })),
    autoPlaceOrderInquiryRows: vi.fn(async () => ({ linked: 0, results: [] })),
  };
});

const getReserveRequestsMock = vi.fn<(inquiryId: string) => Promise<OrderInquiryReserveRequest[]>>(
  async () => [OPEN_REQUEST, ANSWERED_REQUEST],
);
// AC-RS-87 / 6e.4: the inquiry-keyed commit, `(inquiryId, payload)`.
const commitReserveSpy = vi.fn<
  (inquiryId: string, payload: CommitReservePayload) => Promise<OrderInquiryReserveRequest[]>
>(async () => [{ ...OPEN_REQUEST, state: 'reserved' }]);
const HISTORY: OrderInquiryReserveHistoryEntry[] = [
  { kind: 'unreserved', qty: '20', location: 'DC1', reason: 'went back', actor_name: 'Eling', created_at: '2026-09-22T10:00:00' },
  { kind: 'reserved', qty: '50', location: 'DC1', reason: null, actor_name: 'Eling', created_at: '2026-09-21T10:00:00' },
  { kind: 'requested', qty: '50', location: 'DC1', reason: null, actor_name: 'Joey', created_at: '2026-09-20T10:00:00' },
];
const historyMock = vi.fn<
  (requestId: string, rowId: string) => Promise<OrderInquiryReserveHistoryEntry[]>
>(async () => HISTORY);
vi.mock('../../../_shared/services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../_shared/services/orderInquiryReserveService')
  >();
  return {
    ...actual,
    getOrderInquiryReserveRequests: (inquiryId: string) => getReserveRequestsMock(inquiryId),
    commitOrderInquiryReserve: (inquiryId: string, payload: CommitReservePayload) =>
      commitReserveSpy(inquiryId, payload),
    getOrderInquiryRowHistory: (requestId: string, rowId: string) => historyMock(requestId, rowId),
    // AC-SN-41: the confirmation reads who the notice goes to.
    getReserveNoticeRecipients: vi.fn(async () => ({
      to: { name: 'Joey' },
      cc: [{ name: 'Eling' }],
    })),
  };
});

const createPendingActionSpy = vi.fn(async ({ entityId }: { entityId: string }) => ({
  id: `pa-${entityId}`,
  action_key: 'order_inquiry_reserve_request.cancel',
  entity_type: 'order_inquiry_reserve_request',
  entity_id: entityId,
  commit_at: new Date(Date.now() + 10_000).toISOString(),
  window_seconds: 10,
}));
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) =>
    createPendingActionSpy(...(args as [{ entityId: string }])),
  cancelPendingAction: vi.fn(async () => undefined),
  getCurrentPendingAction: vi.fn(async () => ({ pending: null, last_outcome: null })),
}));

const getStockDetailMock = vi.fn(async (productId: string) => {
  if (productId === 'prod-1') {
    return {
      locations: [
        { warehouse_id: 'wh-brw', location: 'BRW', available_qty: '107' },
        { warehouse_id: 'wh-dc1', location: 'DC1', available_qty: '20' },
      ],
    };
  }
  return { locations: [] };
});
vi.mock('../../../_shared/services/fulfilmentPlanningService', () => ({
  getStockDetail: (...args: unknown[]) => getStockDetailMock(...(args as [string])),
}));

import { OrderInquiryDetail } from './OrderInquiryDetail';
import { getOrderInquiryHeaderLines } from '../../../_shared/services/orderInquiryService';

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

/** The grid TABLE row for an item code. */
function gridRowFor(itemCode: string): HTMLElement {
  const matches = screen.getAllByText(itemCode);
  const withinRow = matches
    .map((el) => el.closest('tr'))
    .find((tr): tr is HTMLTableRowElement => Boolean(tr));
  expect(withinRow).toBeTruthy();
  return withinRow as HTMLElement;
}

/** AC-SN-41: Reserve (N) now opens a confirmation; this is its unticked primary button. */
async function confirmReserve() {
  fireEvent.click(await screen.findByRole('button', { name: /^reserve and send to/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  searchParamsValue = '';
  reservePermissionOverride = null;
  sessionUserId = 'test-current-user';
  getReserveRequestsMock.mockResolvedValue([OPEN_REQUEST, ANSWERED_REQUEST]);
  vi.mocked(getOrderInquiryHeaderLines).mockResolvedValue([
    PLAIN_ROW,
    REQUESTED_ROW,
    RESERVED_ROW,
    DECLINED_ROW,
  ]);
});

describe('AC-RS-83: pills + per-row icon buttons, gated by the reserve permission', () => {
  it('with the permission: a requested line shows the amber pill AND Edit reserve; a reserved line shows the green pill AND Amend reserve + History; a plain line shows neither icon', async () => {
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    const requestedRow = gridRowFor('ZZT-REQUESTED');
    expect(within(requestedRow).getByText(/request to reserve 107/i)).toBeInTheDocument();
    expect(within(requestedRow).getByLabelText('Edit reserve')).toBeInTheDocument();

    const reservedRow = gridRowFor('ZZT-RESERVED');
    expect(within(reservedRow).getByText(/reserved 30/i)).toBeInTheDocument();
    expect(within(reservedRow).getByLabelText('Amend reserve')).toBeInTheDocument();
    expect(within(reservedRow).getByLabelText('History')).toBeInTheDocument();

    const plainRow = gridRowFor('ZZT-PLAIN');
    expect(within(plainRow).queryByLabelText('Edit reserve')).not.toBeInTheDocument();
    expect(within(plainRow).queryByLabelText('Amend reserve')).not.toBeInTheDocument();
    // `PLAN-oi-no-double-count-25sep.md` AC-ND-13 (owner ruling 26 Sep, G3): every line
    // carries exactly ONE History icon, the reserved line included - never a second one.
    expect(within(plainRow).getAllByLabelText('History')).toHaveLength(1);
    expect(within(reservedRow).getAllByLabelText('History')).toHaveLength(1);
  });

  it('AC-RS-83c: the icons sit in the same cell as the State pill; no Reserve actions column', async () => {
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    const pill = within(gridRowFor('ZZT-REQUESTED')).getByText(/request to reserve 107/i);
    const cell = pill.closest('td') as HTMLElement;
    expect(within(cell).getByLabelText('Reserve')).toBeInTheDocument();
    expect(within(cell).getByLabelText('Edit reserve')).toBeInTheDocument();

    const reservedPill = within(gridRowFor('ZZT-RESERVED')).getByText(/reserved 30/i);
    const reservedCell = reservedPill.closest('td') as HTMLElement;
    expect(within(reservedCell).getByLabelText('Amend reserve')).toBeInTheDocument();
    expect(within(reservedCell).getByLabelText('History')).toBeInTheDocument();

    expect(screen.queryAllByText('Reserve actions')).toHaveLength(0);
  });

  it('no header badge, no Confirm all button, no "Open reserve request" control anywhere on the page', async () => {
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    expect(screen.queryByRole('button', { name: /open reserve request/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /confirm all/i })).not.toBeInTheDocument();
  });

  it('without the permission: pills only, no action-cell icon buttons anywhere', async () => {
    reservePermissionOverride = false;
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    expect(screen.queryByLabelText('Edit reserve')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Amend reserve')).not.toBeInTheDocument();
    // AC-ND-13 (owner ruling 26 Sep, G3): the line's one History icon is not a reserve
    // action, so it stays without the permission - one per line, never more.
    for (const code of ['ZZT-PLAIN', 'ZZT-REQUESTED', 'ZZT-RESERVED', 'ZZT-DECLINED']) {
      expect(within(gridRowFor(code)).getAllByLabelText('History')).toHaveLength(1);
    }
    const requestedRow = gridRowFor('ZZT-REQUESTED');
    expect(within(requestedRow).getByText(/request to reserve 107/i)).toBeInTheDocument();
  });
});

describe('AC-RS-84: the tick stages the full requested qty at the default pool, no dialog, no write', () => {
  it('staging shows the dashed chip + Undo; nothing is posted; Undo restores the icons', async () => {
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    const requestedRow = gridRowFor('ZZT-REQUESTED');
    const tick = within(requestedRow).getByLabelText('Reserve');
    fireEvent.click(tick);

    const chip = await screen.findByText('Reserve 107 @ BRW');
    // AC-RS-83c: the staged chip and Undo stay in the State cell too.
    const chipCell = chip.closest('td') as HTMLElement;
    expect(within(chipCell).getByText(/request to reserve 107/i)).toBeInTheDocument();
    expect(within(chipCell).getByRole('button', { name: /undo/i })).toBeInTheDocument();
    expect(commitReserveSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /undo/i }));
    await waitFor(() =>
      expect(within(gridRowFor('ZZT-REQUESTED')).getByLabelText('Edit reserve')).toBeInTheDocument(),
    );
    expect(screen.queryByText('Reserve 107 @ BRW')).not.toBeInTheDocument();
  });
});

describe('AC-RS-85: Edit reserve opens ReserveLineForm; short qty needs a reason; Save shows the chip', () => {
  it('opens a dialog titled by the item code with a Save button; saves a short qty at DC1', async () => {
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    const requestedRow = gridRowFor('ZZT-REQUESTED');
    fireEvent.click(within(requestedRow).getByLabelText('Edit reserve'));

    expect(await screen.findByRole('dialog', { name: /ZZT-REQUESTED/i })).toBeInTheDocument();
    // 6e.4 (S2): mounted after the pools loaded - prefilled 107, nothing to explain.
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('107');
    const dialog = screen.getByRole('dialog', { name: /ZZT-REQUESTED/i });
    expect(within(dialog).getByRole('button', { name: /^save$/i })).toBeEnabled();

    fireEvent.click(screen.getByLabelText('Location'));
    fireEvent.click(await screen.findByText('DC1'));
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '20' } });
    // 6e.4 (S1): short of the request needs a reason, availability notwithstanding.
    expect(within(dialog).getByRole('button', { name: /^save$/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: 'partial stock only' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText('Reserve 20 @ DC1')).toBeInTheDocument();
    expect(commitReserveSpy).not.toHaveBeenCalled();
  });
});

describe('AC-RS-86: Amend reserve on a reserved line locks the location and stages an amend chip', () => {
  it('Location renders as read-only text; Save shows "Amend to 10"; Undo drops it', async () => {
    renderDetail();
    await screen.findByText('ZZT-RESERVED');

    const reservedRow = gridRowFor('ZZT-RESERVED');
    fireEvent.click(within(reservedRow).getByLabelText('Amend reserve'));

    expect(await screen.findByRole('dialog', { name: /ZZT-RESERVED/i })).toBeInTheDocument();
    expect(screen.queryByLabelText('Location')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: '20 went back' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText('Amend to 10')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /undo/i }));
    await waitFor(() =>
      expect(within(gridRowFor('ZZT-RESERVED')).getByLabelText('Amend reserve')).toBeInTheDocument(),
    );
  });
});

describe('AC-RS-87: header Reserve CTA, disabled while nothing staged, commits the staged map on click', () => {
  it('disabled with no staged rows; enabled "Reserve (2)" once two rows are staged; commit posts one call, toasts, clears staging', async () => {
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValue([PLAIN_ROW, REQUESTED_ROW, RESERVED_ROW]);
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    // Scoped by testid, not accessible name: the row-level tick icon (AC-RS-83) also
    // has the accessible name "Reserve", so `getByRole('button', { name: /^reserve$/i })`
    // matches both this CTA and that icon at once.
    const reserveCta = await screen.findByTestId('reserve-cta');
    expect(reserveCta).toHaveAccessibleName(/^reserve$/i);
    expect(reserveCta).toBeDisabled();
    // Owner, 24 Sep: CS's Reserve is the CTA colour (primary, like Confirm), not outline.
    expect(reserveCta.className).toContain('bg-primary');
    expect(reserveCta.className).not.toContain('border-input');

    fireEvent.click(within(gridRowFor('ZZT-REQUESTED')).getByLabelText('Reserve'));
    await screen.findByText('Reserve 107 @ BRW');

    fireEvent.click(within(gridRowFor('ZZT-RESERVED')).getByLabelText('Amend reserve'));
    await screen.findByRole('dialog', { name: /ZZT-RESERVED/i });
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: '20 went back' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await screen.findByText('Amend to 10');

    const enabledCta = screen.getByRole('button', { name: /^reserve \(2\)$/i });
    expect(enabledCta).toBeEnabled();

    fireEvent.click(enabledCta);
    await confirmReserve();

    await waitFor(() => expect(commitReserveSpy).toHaveBeenCalledTimes(1));
    // 6e.4: ONE inquiry-keyed call carrying lines of two different requests (the open
    // one and the finished one) - no request id is chosen client-side.
    const [inquiryId, payload] = commitReserveSpy.mock.calls[0];
    expect(inquiryId).toBe('oi-1');
    expect(payload.reserves).toEqual([
      expect.objectContaining({ row_id: 'row-requested', warehouse_id: 'wh-brw', qty_reserved: 107 }),
    ]);
    expect(payload.amendments).toEqual([
      expect.objectContaining({ row_id: 'row-reserved', qty_reserved: 10 }),
    ]);

    await waitFor(() => expect(toastSuccessSpy).toHaveBeenCalledWith(expect.stringMatching(/reserved, .* notified/i)));
    expect(screen.queryByText('Reserve 107 @ BRW')).not.toBeInTheDocument();
    expect(screen.queryByText('Amend to 10')).not.toBeInTheDocument();
  });

  it('a rejected commit leaves the staged chips in place and toasts the error', async () => {
    commitReserveSpy.mockRejectedValueOnce(new Error('boom'));
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    fireEvent.click(within(gridRowFor('ZZT-REQUESTED')).getByLabelText('Reserve'));
    await screen.findByText('Reserve 107 @ BRW');

    fireEvent.click(screen.getByRole('button', { name: /^reserve \(1\)$/i }));
    await confirmReserve();

    await waitFor(() => expect(toastErrorSpy).toHaveBeenCalled());
    expect(screen.getByText('Reserve 107 @ BRW')).toBeInTheDocument();
  });

  it('without the reserve permission, the header Reserve button is absent', async () => {
    reservePermissionOverride = false;
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    expect(screen.queryByRole('button', { name: /^reserve( \(\d+\))?$/i })).not.toBeInTheDocument();
  });
});

describe('AC-RS-88: the State SearchableMultiSelect filter, and ?reserve= preselecting it without opening a dialog', () => {
  it('offers the seven State options; selecting Request to reserve hides other lines', async () => {
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');
    await screen.findByText('ZZT-PLAIN');
    await screen.findByText('ZZT-RESERVED');

    const trigger = document.querySelector('[data-slot="searchable-multi-select-trigger"]');
    expect(trigger).toBeTruthy();
    fireEvent.click(trigger as Element);

    // Scoped to the listbox, not the whole screen: a row's own State pill (e.g. "To
    // buy") carries the same text as its matching filter option.
    const listbox = await screen.findByRole('listbox');
    const options = ['To buy', 'Partly on PO/SPO', 'On PO/SPO', 'Done', 'Request to reserve', 'Reserved', 'Not reserved'];
    for (const label of options) {
      expect(within(listbox).getByRole('option', { name: label })).toBeInTheDocument();
    }
    // 6e.4 (AC-RS-88b): this grid never shows a cancelled line, so no such option.
    expect(within(listbox).queryByRole('option', { name: 'Cancelled' })).not.toBeInTheDocument();

    fireEvent.click(within(listbox).getByRole('option', { name: 'Request to reserve' }));

    await waitFor(() => expect(screen.queryByText('ZZT-PLAIN')).not.toBeInTheDocument());
    expect(screen.getByText('ZZT-REQUESTED')).toBeInTheDocument();
  });

  it('AC-RS-85c: Not reserved filters to declined lines only', async () => {
    renderDetail();
    await screen.findByText('ZZT-DECLINED');

    fireEvent.click(document.querySelector('[data-slot="searchable-multi-select-trigger"]') as Element);
    const listbox = await screen.findByRole('listbox');
    fireEvent.click(within(listbox).getByRole('option', { name: 'Not reserved' }));

    await waitFor(() => expect(screen.queryByText('ZZT-RESERVED')).not.toBeInTheDocument());
    expect(screen.queryByText('ZZT-REQUESTED')).not.toBeInTheDocument();
    expect(screen.getAllByText('ZZT-DECLINED').length).toBeGreaterThan(0);
  });

  it('?reserve=<id> preselects Request to reserve and opens no dialog', async () => {
    searchParamsValue = 'reserve=rr-1';
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('ZZT-PLAIN')).not.toBeInTheDocument());
  });
});

describe('AC-RS-89: History opens a read-only events dialog; Cancel request sits in the Actions menu', () => {
  it('History lists the row events newest first through formatDateTime', async () => {
    renderDetail();
    await screen.findByText('ZZT-RESERVED');

    fireEvent.click(within(gridRowFor('ZZT-RESERVED')).getByLabelText('History'));

    const historyDialog = await screen.findByRole('dialog', { name: /history/i });
    // `PLAN-oi-no-double-count-25sep.md` S0 (owner ruling 26 Sep, G3): the reserve
    // history is the Reserve tab of the line's one History dialog now.
    const reserveTab = within(historyDialog).getByRole('tab', { name: 'Reserve' });
    fireEvent.mouseDown(reserveTab);
    fireEvent.click(reserveTab);
    // Anchored on the finished request that answered this line.
    await waitFor(() => expect(historyMock).toHaveBeenCalledWith('rr-0', 'row-reserved'));
    const unreserved = await within(historyDialog).findByText('Unreserved 20 @ DC1');
    const reserved = within(historyDialog).getByText('Reserved 50 @ DC1');
    const requested = within(historyDialog).getByText('Requested 50 @ DC1');
    // Rendered in the server's own order, newest first.
    expect(unreserved.compareDocumentPosition(reserved) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(reserved.compareDocumentPosition(requested) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(historyDialog).queryByText(/\d{4}-\d\d-\d\dT/)).not.toBeInTheDocument();
  });

  it('Cancel request is offered in the Actions menu for the requester', async () => {
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    // Radix's DropdownMenuTrigger needs a pointerdown to open in jsdom, the same
    // interaction OrderInquiryDetail.test.tsx already uses on this exact trigger - a
    // plain `fireEvent.click` never flips `aria-expanded`.
    fireEvent.pointerDown(screen.getByRole('button', { name: /order inquiry options/i }), {
      button: 0,
    });
    expect(await screen.findByText(/cancel request/i)).toBeInTheDocument();
  });

  it('the requester WITHOUT the reserve permission is offered Cancel request; a colleague without it is not', async () => {
    reservePermissionOverride = false;
    sessionUserId = 'user-1';
    const { unmount } = renderDetail();
    await screen.findByText('ZZT-REQUESTED');
    fireEvent.pointerDown(screen.getByRole('button', { name: /order inquiry options/i }), {
      button: 0,
    });
    expect(await screen.findByText(/cancel request/i)).toBeInTheDocument();
    unmount();

    sessionUserId = 'someone-else';
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');
    fireEvent.pointerDown(screen.getByRole('button', { name: /order inquiry options/i }), {
      button: 0,
    });
    await screen.findByText(/export excel/i);
    expect(screen.queryByText(/cancel request/i)).not.toBeInTheDocument();
  });
});

describe('6e.4 review round: declined lines, the action column gate, amend prefill, staged defaults, filter URL', () => {
  it('AC-RS-83b: a declined line reads Not reserved and offers Amend reserve + History', async () => {
    renderDetail();
    await screen.findByText('ZZT-DECLINED');

    const declinedRow = gridRowFor('ZZT-DECLINED');
    expect(within(declinedRow).getByText('Not reserved')).toBeInTheDocument();
    expect(within(declinedRow).getByLabelText('Amend reserve')).toBeInTheDocument();
    expect(within(declinedRow).getByLabelText('History')).toBeInTheDocument();
  });

  it('AC-RS-83b: no open request and no reserved / declined line - the action column is absent', async () => {
    getReserveRequestsMock.mockResolvedValue([]);
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValue([PLAIN_ROW]);
    renderDetail();
    await screen.findByText('ZZT-PLAIN');

    expect(screen.queryAllByText('Reserve actions')).toHaveLength(0);
    expect(screen.queryByLabelText('Edit reserve')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Amend reserve')).not.toBeInTheDocument();
    expect(screen.queryByTestId('reserve-cta')).not.toBeInTheDocument();
  });

  it('AC-RS-85b: the form opens only once the pools have loaded, prefilled min(requested, available)', async () => {
    let release: () => void = () => {};
    getStockDetailMock.mockImplementationOnce(
      (productId: string) =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              locations: [
                { warehouse_id: 'wh-brw', location: 'BRW', available_qty: '107' },
                { warehouse_id: 'wh-dc1', location: 'DC1', available_qty: '20' },
              ],
            });
          void productId;
        }),
    );
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    fireEvent.click(within(gridRowFor('ZZT-REQUESTED')).getByLabelText('Edit reserve'));
    await waitFor(() => expect(getStockDetailMock).toHaveBeenCalled());
    expect(screen.queryByRole('dialog', { name: /ZZT-REQUESTED/i })).not.toBeInTheDocument();

    release();
    await screen.findByRole('dialog', { name: /ZZT-REQUESTED/i });
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('107');
  });

  it('AC-RS-85c: amend prefills the line net (the pill figure), not one request row', async () => {
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValue([
      PLAIN_ROW,
      REQUESTED_ROW,
      row({ ...RESERVED_ROW, reserved_qty: '45' }),
    ]);
    renderDetail();
    await screen.findByText('ZZT-RESERVED');

    fireEvent.click(within(gridRowFor('ZZT-RESERVED')).getByLabelText('Amend reserve'));
    await screen.findByRole('dialog', { name: /ZZT-RESERVED/i });
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('45');
    expect(screen.getByText('DC1')).toBeInTheDocument();
  });

  it('AC-RS-85c: a line answered by two requests reads Requested 50 across 2 requests; reason below 50', async () => {
    const SECOND_ANSWER: OrderInquiryReserveRequest = {
      ...ANSWERED_REQUEST,
      id: 'rr-9',
      ordinal: 3,
      rows: [
        {
          ...ANSWERED_REQUEST.rows[0],
          id: 'reqrow-9',
          qty_requested: '20',
          qty_reserved: '0',
          reason: 'none left',
        },
      ],
    };
    getReserveRequestsMock.mockResolvedValue([
      OPEN_REQUEST,
      {
        ...ANSWERED_REQUEST,
        rows: [{ ...ANSWERED_REQUEST.rows[0], qty_requested: '30', qty_reserved: '30' }],
      },
      SECOND_ANSWER,
    ]);
    renderDetail();
    await screen.findByText('ZZT-RESERVED');

    fireEvent.click(within(gridRowFor('ZZT-RESERVED')).getByLabelText('Amend reserve'));
    const dialog = await screen.findByRole('dialog', { name: /ZZT-RESERVED/i });
    expect(within(dialog).getByText('Requested 50 across 2 requests')).toBeInTheDocument();
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('30');
    // 40 is below the 50 requested in total: a reason is needed.
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '40' } });
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
  });

  it('AC-RS-85c: a later balance request counts once - 36 asked / 10 got, then 26 asked reads Requested 36', async () => {
    getReserveRequestsMock.mockResolvedValue([
      OPEN_REQUEST,
      {
        ...ANSWERED_REQUEST,
        rows: [{ ...ANSWERED_REQUEST.rows[0], qty_requested: '36', qty_reserved: '10' }],
      },
      {
        ...ANSWERED_REQUEST,
        id: 'rr-9',
        ordinal: 3,
        rows: [{ ...ANSWERED_REQUEST.rows[0], id: 'reqrow-9', qty_requested: '26', qty_reserved: '26' }],
      },
    ]);
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValue([
      PLAIN_ROW,
      REQUESTED_ROW,
      row({ ...RESERVED_ROW, qty: '36', reserved_qty: '36' }),
    ]);
    renderDetail();
    await screen.findByText('ZZT-RESERVED');

    fireEvent.click(within(gridRowFor('ZZT-RESERVED')).getByLabelText('Amend reserve'));
    const dialog = await screen.findByRole('dialog', { name: /ZZT-RESERVED/i });
    expect(within(dialog).getByText('Requested 36 across 2 requests')).toBeInTheDocument();
    // Net 36 = requested 36: nothing to explain.
    expect(within(dialog).queryByLabelText(/reason/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^save$/i })).toBeEnabled();
  });

  it('a commit whose response touched no request toasts Nothing to change', async () => {
    commitReserveSpy.mockResolvedValueOnce([]);
    renderDetail();
    await screen.findByText('ZZT-RESERVED');

    fireEvent.click(within(gridRowFor('ZZT-RESERVED')).getByLabelText('Amend reserve'));
    await screen.findByRole('dialog', { name: /ZZT-RESERVED/i });
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: 'same' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^reserve \(1\)$/i }));
    await confirmReserve();

    await waitFor(() => expect(toastSuccessSpy).toHaveBeenCalledWith('Nothing to change'));
    expect(toastSuccessSpy).not.toHaveBeenCalledWith(expect.stringMatching(/notified/i));
  });

  it('AC-RS-85b: a staged reserve with no location is sent without warehouse_id', async () => {
    getReserveRequestsMock.mockResolvedValue([
      {
        ...OPEN_REQUEST,
        rows: [{ ...OPEN_REQUEST.rows[0], warehouse_id: null, location: null }],
      },
    ]);
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    fireEvent.click(within(gridRowFor('ZZT-REQUESTED')).getByLabelText('Reserve'));
    expect(await screen.findByText('Reserve 107')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^reserve \(1\)$/i }));
    await confirmReserve();

    await waitFor(() => expect(commitReserveSpy).toHaveBeenCalledTimes(1));
    const [, payload] = commitReserveSpy.mock.calls[0];
    expect(payload.reserves).toHaveLength(1);
    expect(payload.reserves[0]).toEqual({ row_id: 'row-requested', qty_reserved: 107, reason: null });
    expect(payload.reserves[0]).not.toHaveProperty('warehouse_id');
  });

  it('AC-RS-88b: changing the filter while ?reserve= is on the URL replaces the URL without it; an empty result reads No line matches the filter.', async () => {
    searchParamsValue = 'reserve=rr-1';
    renderDetail();
    await screen.findByText('ZZT-REQUESTED');

    const trigger = document.querySelector('[data-slot="searchable-multi-select-trigger"]');
    fireEvent.click(trigger as Element);
    const listbox = await screen.findByRole('listbox');
    fireEvent.click(within(listbox).getByRole('option', { name: 'Request to reserve' }));
    fireEvent.click(within(listbox).getByRole('option', { name: 'Done' }));

    await waitFor(() =>
      expect(replaceSpy).toHaveBeenCalledWith('/project-sales/order-inquiries/oi-1', { scroll: false }),
    );
    for (const [url] of replaceSpy.mock.calls) {
      expect(String(url)).not.toContain('reserve=');
    }
    expect(await screen.findByText('No line matches the filter.')).toBeInTheDocument();
  });
});
