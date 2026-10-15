/**
 * STOCK-DEBT-SNAPSHOT AC-F5, AC-F6, AC-F8: the frozen drill opened from the snapshot page and
 * the shared "Request CS to reserve" it opens.
 *
 * Reached through `SnapshotClient` ({ snapshotId }) because the drill's own props are not part
 * of the contract: pressing a month cell opens that bucket, pressing the product opens every
 * bucket. The real `RequestReserveDialog` (shared component) renders here; the stock-grid read
 * (`useReserveRowOptions`) and the services are mocked at their boundaries.
 *
 * Contract assumption: `getSnapshotRowLines(id, rowId, month?)` resolves `{ lines: [...] }`,
 * each line = the live drill's demand fields + `bucket`, `sales_order_line_id`, `sales_order_id`,
 * `oi_rows[]` (AC-5/AC-12 shape) + `now: { so_status, open_qty }`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

const SNAP = 'a1b2c3d4-0000-4000-8000-000000000001';
const ROW_ID = 'f0000000-0000-4000-8000-000000000002';
const OI17 = 'b0000000-0000-4000-8000-000000000017';
const OI71 = 'b0000000-0000-4000-8000-000000000071';
const OI33 = 'b0000000-0000-4000-8000-000000000033';
const OI51 = 'b0000000-0000-4000-8000-000000000051';
const OI37 = 'b0000000-0000-4000-8000-000000000037';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => `/project-sales/stock-debt/snapshots/${SNAP}`,
  useSearchParams: () => new URLSearchParams(''),
  useParams: () => ({ id: SNAP }),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn().mockResolvedValue({ listing_key: 'x', config: null }),
  upsertUserListColumnConfig: vi.fn(),
  resetUserListColumnConfig: vi.fn(),
}));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { id: 'u-1', name: 'Jayson Foundryx' } }, status: 'authenticated' }),
}));
vi.mock('@/components/common/PageHeader', () => ({
  PageHeader: ({ title, actions }: { title: React.ReactNode; actions?: React.ReactNode }) => (
    <header>
      <h1>{title}</h1>
      {actions}
    </header>
  ),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn() }),
}));

let permissions = new Set<string>();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => permissions.has(slug),
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value?: string;
    onChange: (value: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    'aria-label'?: string;
  }) => (
    <select
      id={props.id}
      aria-label={props['aria-label']}
      value={props.value ?? ''}
      onChange={(event) => props.onChange(event.target.value)}
    >
      <option value="">{props.placeholder ?? ''}</option>
      {(props.options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

vi.mock('../../_shared/hooks/useReserveRowOptions', () => ({
  useReserveRowOptions: (entries: Array<{ key: string }>) =>
    Object.fromEntries(
      entries.map((entry) => [
        entry.key,
        {
          isLoading: false,
          defaultWarehouseId: 'wh-brw',
          options: [
            { value: 'wh-brw', label: 'BRW' },
            { value: 'wh-mwh', label: 'MWH' },
          ],
          availableQtyByWarehouseId: {},
        },
      ]),
    ),
}));

vi.mock('../../_shared/hooks/useParkedSendActions', () => ({
  useParkedSendActions: () => ({ actions: [], dialog: null }),
}));

const getSnapshot = vi.fn();
const listSnapshotRows = vi.fn();
const getSnapshotRowLines = vi.fn();
vi.mock('../services/stockDebtSnapshotService', () => ({
  getSnapshot: (...args: unknown[]) => getSnapshot(...args),
  listSnapshotRows: (...args: unknown[]) => listSnapshotRows(...args),
  getSnapshotRowLines: (...args: unknown[]) => getSnapshotRowLines(...args),
  setSnapshotDecision: vi.fn(),
  exportSnapshot: vi.fn(),
}));

const getOrderInquiryHeaderLines = vi.fn();
vi.mock('../../_shared/services/orderInquiryService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../_shared/services/orderInquiryService')>();
  return {
    ...actual,
    getOrderInquiryHeaderLines: (...args: unknown[]) => getOrderInquiryHeaderLines(...args),
  };
});

const createReserveRequest = vi.fn();
vi.mock('../../_shared/services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../_shared/services/orderInquiryReserveService')>();
  return {
    ...actual,
    createOrderInquiryReserveRequest: (...args: unknown[]) => createReserveRequest(...args),
  };
});

import { SnapshotClient } from './SnapshotClient';

const ACK = 'projects.order_inquiries.acknowledge';

function header() {
  return {
    id: SNAP,
    name: 'Stock Debt 15/10/2026',
    filters: {
      query: '',
      book: 'project',
      location: [],
      exclude_location: [],
      only_debt: true,
      date_from: '2023-01-01',
      date_to: '2027-05-31',
      supplier_ids: [],
      projected: false,
      show_aux: false,
    },
    filters_label: 'Book Project',
    months: ['2026-10', '2027-01'],
    tba_month: '2030-01',
    totals: {
      months: { '2026-10': 0, '2027-01': -30 },
      tba: 0,
      undated: 0,
      unlocated: 0,
      total: -223,
      pool_on_hand: 972,
      pool_po_qty: 0,
      pool_incoming_qty: 0,
      dealer_outstanding: 0,
    },
    row_count: 1,
    decided_count: 1,
    created_by_name: 'Jayson Foundryx',
    created_at: '2026-10-15T03:53:00Z',
    can_delete: true,
    prev_id: null,
    next_id: null,
  };
}

const PRODUCT_ROW = {
  row_id: ROW_ID,
  product_id: 'f0000000-0000-4000-8000-0000000000a1',
  product_code: 'SRTWT904-NL',
  product_name: null,
  months: [
    { key: '2026-10', balance: 0, tone: 'green' },
    { key: '2027-01', balance: -30, tone: 'red' },
  ],
  tba: 0,
  undated: 0,
  unlocated: 0,
  total: -223,
  supplier_id: null,
  supplier_name: null,
  category_code: null,
  pool_on_hand: 972,
  pool_po_qty: 0,
  pool_incoming_qty: 0,
  dealer_outstanding: 0,
  decision: 'reserve',
  decision_warehouse_id: 'wh-mwh',
  decision_warehouse_code: 'MWH',
  decision_note: 'Reserve all 223 from MWH',
  decided_by_name: 'Jayson Foundryx',
  decided_at: '2026-10-15T06:10:00Z',
};

function oiRow(over: Record<string, unknown>) {
  return {
    oi_id: OI17,
    inquiry_no: 'OI-2609-0417',
    row_id: 'row-17a',
    qty: 30,
    delivery_date: '2027-01-07',
    stock_location: 'BRW-BB',
    verb: 'ORDER',
    state: 'raised',
    now_state: 'raised',
    reserve_state: null,
    remaining: 30,
    requestable: true,
    ...over,
  };
}

function line(over: Record<string, unknown>) {
  return {
    so_number: 'SO383872',
    sales_order_id: 'c0000000-0000-4000-8000-0000000000e1',
    sales_order_line_id: 'c0000000-0000-4000-8000-0000000000f1',
    agent_code: 'LEENA',
    warehouse_code: 'BRW-BB',
    required_date: '2027-01-07',
    bucket: '2027-01',
    qty_ordered: 30,
    qty_delivered: 0,
    open_qty: 30,
    assigned_qty: 0,
    assigned_source: null,
    assigned_from: [],
    status: 'short',
    short_qty: 30,
    oi_rows: [oiRow({})],
    now: { so_status: 'open', open_qty: 30 },
    ...over,
  };
}

const LINES = [
  line({}),
  line({
    required_date: '2027-01-20',
    short_qty: 28,
    qty_ordered: 28,
    open_qty: 28,
    oi_rows: [oiRow({ row_id: 'row-17b', qty: 28, remaining: 28, delivery_date: '2027-01-20' })],
    now: { so_status: 'open', open_qty: 28 },
  }),
  // On two OIs: one parked (not requestable) and one requestable. Shows in BOTH groups.
  line({
    so_number: 'SO391092',
    agent_code: 'SHAHRUL',
    required_date: '2027-01-01',
    short_qty: 10,
    qty_ordered: 10,
    open_qty: 10,
    oi_rows: [
      oiRow({
        oi_id: OI71,
        inquiry_no: 'OI-2609-0271',
        row_id: 'row-71',
        qty: 10,
        remaining: 10,
        reserve_state: 'parked',
        requestable: false,
        delivery_date: '2027-01-01',
      }),
      oiRow({ row_id: 'row-17c', qty: 10, remaining: 10, delivery_date: '2027-01-01' }),
    ],
    now: { so_status: 'open', open_qty: 10 },
  }),
  line({
    so_number: 'SO422078',
    agent_code: 'TERA',
    required_date: '2027-01-03',
    short_qty: 633,
    qty_ordered: 633,
    open_qty: 633,
    oi_rows: [
      oiRow({
        oi_id: OI33,
        inquiry_no: 'OI-2609-0733',
        row_id: 'row-33',
        qty: 633,
        remaining: 0,
        reserve_state: 'reserved',
        requestable: false,
      }),
    ],
    now: { so_status: 'cancelled', open_qty: 0 },
  }),
  line({
    so_number: 'SO400001',
    agent_code: 'JUSTIN',
    required_date: '2027-01-05',
    short_qty: 2,
    qty_ordered: 2,
    open_qty: 2,
    oi_rows: [
      oiRow({
        oi_id: OI51,
        inquiry_no: 'OI-2609-0501',
        row_id: 'row-51',
        qty: 2,
        remaining: 2,
        reserve_state: 'requested',
        requestable: false,
      }),
    ],
    now: { so_status: 'open', open_qty: 2 },
  }),
  line({
    so_number: 'SO411132',
    agent_code: 'JUSTIN',
    required_date: '2027-01-09',
    short_qty: 3,
    qty_ordered: 3,
    open_qty: 3,
    oi_rows: [
      oiRow({
        oi_id: OI37,
        inquiry_no: 'OI-2609-0637',
        row_id: 'row-37',
        qty: 3,
        remaining: 3,
        state: 'placed',
        now_state: 'placed',
        requestable: false,
        after_snapshot: true,
      }),
    ],
    now: { so_status: 'open', open_qty: 3 },
  }),
  line({
    so_number: 'SO381945',
    agent_code: 'ERIC NG',
    required_date: '2027-01-11',
    short_qty: 4,
    qty_ordered: 4,
    open_qty: 4,
    oi_rows: [],
    now: { so_status: 'open', open_qty: 4 },
  }),
];

const OI17_LIVE_ROWS = [
  { id: 'row-17a', item_code: 'SRTWT904-NL', product_id: 'p-904', location: 'BRW-BB', delivery_date: '2027-01-07', qty: '30', linked_qty: '0', reserved_qty: '0', bundled_qty: '0', verb: 'ORDER', state: 'raised', reserve_state: null },
  { id: 'row-17b', item_code: 'SRTWT904-NL', product_id: 'p-904', location: 'BRW-BB', delivery_date: '2027-01-20', qty: '28', linked_qty: '0', reserved_qty: '0', bundled_qty: '0', verb: 'ORDER', state: 'raised', reserve_state: null },
  { id: 'row-17c', item_code: 'SRTWT904-NL', product_id: 'p-904', location: 'BRW-BB', delivery_date: '2027-01-01', qty: '10', linked_qty: '0', reserved_qty: '0', bundled_qty: '0', verb: 'ORDER', state: 'raised', reserve_state: null },
  // A different product on the same OI: never part of this ask.
  { id: 'row-17x', item_code: 'CKS1050', product_id: 'p-cks', location: 'BRW-BB', delivery_date: '2027-02-01', qty: '99', linked_qty: '0', reserved_qty: '0', bundled_qty: '0', verb: 'ORDER', state: 'raised', reserve_state: null },
  // Same product, but not one of the snapshot's frozen rows: not part of this ask.
  { id: 'row-17y', item_code: 'SRTWT904-NL', product_id: 'p-904', location: 'BRW-BB', delivery_date: '2027-03-01', qty: '55', linked_qty: '0', reserved_qty: '0', bundled_qty: '0', verb: 'ORDER', state: 'raised', reserve_state: null },
  // Frozen, but no longer eligible today (placed): dropped by the shared eligibility rule.
  { id: 'row-17z', item_code: 'SRTWT904-NL', product_id: 'p-904', location: 'BRW-BB', delivery_date: '2027-04-01', qty: '40', linked_qty: '40', reserved_qty: '0', bundled_qty: '0', verb: 'ORDER', state: 'placed', reserve_state: null },
];

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SnapshotClient snapshotId={SNAP} />
    </QueryClientProvider>,
  );
}

function lastLinesCall() {
  const call = getSnapshotRowLines.mock.calls.at(-1) as unknown[];
  return { id: call[0], rowId: call[1], month: call[2] ?? undefined };
}

async function openAllMonths() {
  fireEvent.click(await screen.findByText('SRTWT904-NL'));
  return (await screen.findByRole('dialog')) as HTMLElement;
}

async function openMonth(label: string) {
  await screen.findByText('SRTWT904-NL');
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`SRTWT904-NL.*${label}`) }));
  return (await screen.findByRole('dialog')) as HTMLElement;
}

/** The group card of one OI: the smallest ancestor of its header link that holds a table. */
function groupOf(dialog: HTMLElement, inquiryNo: string): HTMLElement {
  let el: HTMLElement | null = within(dialog).getByRole('link', { name: new RegExp(inquiryNo) });
  while (el && !el.querySelector('table')) el = el.parentElement;
  if (!el) throw new Error(`no group for ${inquiryNo}`);
  return el;
}

/** The FIRST row carrying `text`: a group can hold several lines of one SO (OI-2609-0417
 * holds two SO383872 lines), and the first is the one these tests read. */
function rowWith(scope: HTMLElement, text: string): HTMLElement {
  return within(scope).getAllByText(text)[0].closest('tr') as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  permissions = new Set(['projects.stock_debt.view', ACK]);
  getSnapshot.mockResolvedValue(header());
  listSnapshotRows.mockResolvedValue({
    data: [PRODUCT_ROW],
    pagination: { total: 1, page: 1, limit: 25 },
  });
  getSnapshotRowLines.mockResolvedValue({ lines: LINES });
  getOrderInquiryHeaderLines.mockResolvedValue(OI17_LIVE_ROWS);
  createReserveRequest.mockResolvedValue({ id: 'rr-1', ordinal: 1, first_to_name: 'Eling' });
});

describe('AC-F5 opening the drill', () => {
  it('pressing the product asks for every bucket (no month)', async () => {
    renderPage();
    await openAllMonths();
    await waitFor(() => expect(getSnapshotRowLines).toHaveBeenCalled());
    expect(lastLinesCall()).toEqual({ id: SNAP, rowId: ROW_ID, month: undefined });
  });

  it('pressing a month cell asks for that bucket only', async () => {
    renderPage();
    const dialog = await openMonth('Jan 27');
    await waitFor(() => expect(getSnapshotRowLines).toHaveBeenCalled());
    expect(lastLinesCall()).toEqual({ id: SNAP, rowId: ROW_ID, month: '2027-01' });
    expect(dialog).toHaveTextContent(/SRTWT904-NL\s*·\s*Jan 27/);
  });

  it('titles the all-months drill and says "As frozen <taken at>"', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findByText('SO383872', { selector: 'a, span' }).catch(() => null);
    expect(dialog).toHaveTextContent(/SRTWT904-NL\s*·\s*all months/);
    expect(dialog).toHaveTextContent(/As frozen\s+15\/10\/2026/);
    expect(dialog).toHaveTextContent('11:53');
  });

  it('has a summary strip: total, BRW on hand, lines, short and the decision', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(dialog).toHaveTextContent(/Total\s*-223/);
    expect(dialog).toHaveTextContent(/BRW on hand\s*972/);
    expect(dialog).toHaveTextContent(/Lines\s*7/);
    expect(dialog).toHaveTextContent(/Short\s*710/);
    expect(dialog).toHaveTextContent(/Decision\s*Reserve/);
    expect(dialog).toHaveTextContent('MWH');
  });

  it('shows a loading state, then the server message on failure', async () => {
    getSnapshotRowLines.mockRejectedValue(new Error('Snapshot lines are unavailable'));
    renderPage();
    const dialog = await openAllMonths();
    expect(await within(dialog).findByText('Snapshot lines are unavailable')).toBeInTheDocument();
  });

  it('prints no UUID in the drill (AC-F8)', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(dialog.textContent).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    );
  });
});

describe('AC-F5 grouping by OI', () => {
  it('has the live drill demand columns plus OI row and Now', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    const group = groupOf(dialog, 'OI-2609-0417');
    for (const name of [
      'Sales order', 'Agent', 'Bin', 'Due', 'Ordered', 'Delivered', 'Outstanding',
      'Assigned', 'Covered by', 'Status', 'Short', 'OI row', 'Now',
    ]) {
      expect(within(group).getByRole('columnheader', { name })).toBeInTheDocument();
    }
  });

  it('groups lines by OI: two lines under OI-2609-0417 plus the shared one', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    const group = groupOf(dialog, 'OI-2609-0417');
    expect(within(group).getAllByText('SO383872')).toHaveLength(2);
    expect(within(group).getByText('SO391092')).toBeInTheDocument();
    expect(within(group).queryByText('SO381945')).not.toBeInTheDocument();
  });

  it('a line on two OIs shows in both groups', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(within(groupOf(dialog, 'OI-2609-0271')).getByText('SO391092')).toBeInTheDocument();
    expect(within(groupOf(dialog, 'OI-2609-0417')).getByText('SO391092')).toBeInTheDocument();
  });

  it('lines with no OI sit in a "No OI" group, last', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    const text = dialog.textContent ?? '';
    const noOi = text.indexOf('No OI');
    expect(noOi).toBeGreaterThan(-1);
    for (const no of ['OI-2609-0417', 'OI-2609-0271', 'OI-2609-0733', 'OI-2609-0501', 'OI-2609-0637']) {
      expect(text.indexOf(no)).toBeGreaterThan(-1);
      expect(text.indexOf(no)).toBeLessThan(noOi);
    }
    expect(within(dialog).getAllByText('SO381945')).toHaveLength(1);
    // The No OI group has no OI link and no Request button of its own.
    expect(dialog.textContent!.slice(noOi)).not.toMatch(/OI-2609-/);
  });

  it('the OI number links to the OI page; the OI row figure links to that row', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    const group = groupOf(dialog, 'OI-2609-0417');

    const headerLink = within(group).getByRole('link', { name: /OI-2609-0417/ });
    expect(headerLink.getAttribute('href')).toContain(`/project-sales/order-inquiries/${OI17}`);

    const first = rowWith(group, 'SO383872');
    const rowLink = within(first)
      .getAllByRole('link')
      .find((a) => (a.getAttribute('href') ?? '').includes('?row=row-17a'));
    expect(rowLink).toBeDefined();
    expect(rowLink!.getAttribute('href')).toContain(
      `/project-sales/order-inquiries/${OI17}?row=row-17a`,
    );
    expect(rowLink).toHaveTextContent('30');
  });

  it('the Sales order links to the SO page, as the live drill does', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    const link = within(groupOf(dialog, 'OI-2609-0417')).getAllByRole('link', { name: 'SO383872' })[0];
    expect(link.getAttribute('href')).toContain('/scm/sales-orders/');
  });
});

describe('AC-F5 Now badges (AC-12)', () => {
  it('prints the OI row state and the live open quantity', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    const row = rowWith(groupOf(dialog, 'OI-2609-0417'), 'SO383872');
    expect(row).toHaveTextContent('Raised');
    expect(row).toHaveTextContent('Open 30');
  });

  it('Reserve parked', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(rowWith(groupOf(dialog, 'OI-2609-0271'), 'SO391092')).toHaveTextContent('Reserve parked');
  });

  it('Reserve requested', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(rowWith(groupOf(dialog, 'OI-2609-0501'), 'SO400001')).toHaveTextContent('Reserve requested');
  });

  it('Reserved', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(rowWith(groupOf(dialog, 'OI-2609-0733'), 'SO422078')).toHaveTextContent('Reserved');
  });

  it('SO cancelled', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(rowWith(groupOf(dialog, 'OI-2609-0733'), 'SO422078')).toHaveTextContent('SO cancelled');
  });

  it('an OI row added after the snapshot is badged "after the snapshot"', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    const row = rowWith(groupOf(dialog, 'OI-2609-0637'), 'SO411132');
    expect(row).toHaveTextContent(/after the snapshot/i);
    expect(row).toHaveTextContent('Placed');
  });
});

describe('AC-F6 Request CS to reserve button', () => {
  it('shows on the group with a requestable row for an acknowledge holder, and only there', async () => {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(
      within(groupOf(dialog, 'OI-2609-0417')).getByRole('button', { name: /request cs to reserve/i }),
    ).toBeInTheDocument();
    for (const no of ['OI-2609-0271', 'OI-2609-0733', 'OI-2609-0501', 'OI-2609-0637']) {
      expect(
        within(groupOf(dialog, no)).queryByRole('button', { name: /request cs to reserve/i }),
      ).not.toBeInTheDocument();
    }
    expect(screen.getAllByRole('button', { name: /request cs to reserve/i })).toHaveLength(1);
  });

  it('is absent without the acknowledge permission, even with a requestable row', async () => {
    permissions = new Set(['projects.stock_debt.view']);
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    expect(screen.queryByRole('button', { name: /request cs to reserve/i })).not.toBeInTheDocument();
  });
});

describe('AC-F6 the shared RequestReserveDialog opened from a group', () => {
  async function openRequest() {
    renderPage();
    const dialog = await openAllMonths();
    await within(dialog).findAllByText('SO383872');
    fireEvent.click(
      within(groupOf(dialog, 'OI-2609-0417')).getByRole('button', { name: /request cs to reserve/i }),
    );
    const heading = await screen.findByRole('heading', { name: /request cs to reserve/i });
    return heading.closest('[role="dialog"]') as HTMLElement;
  }

  it('reads that OI live rows, and lists only this product frozen rows that are still requestable', async () => {
    const request = await openRequest();
    expect(getOrderInquiryHeaderLines).toHaveBeenCalledWith(OI17);
    const table = await within(request).findByRole('table');
    const body = within(table).getAllByRole('row').slice(1);
    expect(body).toHaveLength(3);
    for (const tr of body) expect(tr).toHaveTextContent('SRTWT904-NL');
    expect(table).toHaveTextContent('07/01/2027');
    expect(table).toHaveTextContent('20/01/2027');
    expect(table).toHaveTextContent('01/01/2027');
    // Other product, unfrozen row, and ineligible (placed) row are all left out.
    expect(table).not.toHaveTextContent('CKS1050');
    expect(table).not.toHaveTextContent('01/03/2027');
    expect(table).not.toHaveTextContent('01/04/2027');
  });

  it('Park this first is unticked on open', async () => {
    const request = await openRequest();
    expect(await within(request).findByLabelText(/park this first/i)).not.toBeChecked();
  });

  it('Location defaults to the decision pool (MWH) rather than the stock-grid default (BRW)', async () => {
    const request = await openRequest();
    await within(request).findByRole('table');
    fireEvent.click(within(request).getByRole('button', { name: /send request/i }));
    await waitFor(() => expect(createReserveRequest).toHaveBeenCalledTimes(1));
    const [inquiryId, payload] = createReserveRequest.mock.calls[0] as [
      string,
      { rows: Array<{ row_id: string; qty_requested: number; warehouse_id: string }>; park: boolean },
    ];
    expect(inquiryId).toBe(OI17);
    expect(payload.rows.map((r) => r.warehouse_id)).toEqual(['wh-mwh', 'wh-mwh', 'wh-mwh']);
    expect(payload.rows.map((r) => r.row_id).sort()).toEqual(['row-17a', 'row-17b', 'row-17c']);
    expect(payload.park).toBe(false);
  });

  it('parking sends park: true through the same call', async () => {
    const request = await openRequest();
    await within(request).findByRole('table');
    fireEvent.click(within(request).getByLabelText(/park this first/i));
    fireEvent.click(within(request).getByRole('button', { name: /park request/i }));
    await waitFor(() => expect(createReserveRequest).toHaveBeenCalledTimes(1));
    expect(createReserveRequest.mock.calls[0][1]).toMatchObject({ park: true });
  });

  it('on success the drill lines are read again so Now can show the new state', async () => {
    const request = await openRequest();
    await within(request).findByRole('table');
    const before = getSnapshotRowLines.mock.calls.length;
    fireEvent.click(within(request).getByRole('button', { name: /send request/i }));
    await waitFor(() => expect(createReserveRequest).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getSnapshotRowLines.mock.calls.length).toBeGreaterThan(before));
  });
});
