/**
 * STOCK-DEBT-SNAPSHOT AC-F3, AC-F4, AC-F6c, AC-F8: the snapshot page `SnapshotClient`
 * ({ snapshotId }), its Decision column and Decision popover, and the shared "Actions" menu.
 *
 * The columns ARE the stored payload: axis, tba month, totals and show_aux come from the
 * snapshot header, rows from `listSnapshotRows`. Nothing is recomputed and there are no live
 * filters (no Filters button, no Projected switch). The drill is covered in
 * `SnapshotClient.drill.test.tsx`.
 *
 * Contract assumptions (kept in one place so the coder can match them):
 *   - `getSnapshot(id)` -> the AC-8 header; `listSnapshotRows(id, { pageIndex, pageSize, query,
 *     decision })` -> `{ data: SnapshotRow[], pagination: { total, page, limit } }`;
 *   - `setSnapshotDecision(id, rowId, { decision, warehouse_id, note })`, a blank note is null;
 *   - `SearchableSelect` is stubbed with a native <select> (same props) so the tests do not
 *     drive Radix; each select needs an accessible name (aria-label or <label htmlFor>).
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
const NEXT_SNAP = 'a1b2c3d4-0000-4000-8000-000000000002';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
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
import { toast } from '@/lib/toast';

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
    clearable?: boolean;
    placeholder?: string;
    disabled?: boolean;
    'aria-label'?: string;
  }) => (
    <select
      id={props.id}
      aria-label={props['aria-label']}
      value={props.value ?? ''}
      data-clearable={props.clearable ? 'true' : 'false'}
      disabled={props.disabled}
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
            { value: 'wh-brw', label: 'BRW  available 972' },
            { value: 'wh-mwh', label: 'MWH  available 0' },
          ],
          availableQtyByWarehouseId: { 'wh-brw': 972, 'wh-mwh': 0 },
        },
      ]),
    ),
}));

const parkedAction = vi.fn();
vi.mock('../../_shared/hooks/useParkedSendActions', () => ({
  useParkedSendActions: () => ({
    actions: [
      {
        key: 'send-parked-requests',
        label: 'Send all parked requests to CS (2)',
        disabled: false,
        onClick: parkedAction,
      },
      {
        key: 'send-parked-notices',
        label: 'Send all parked reserves to purchasing (0)',
        disabled: true,
        disabledReason: 'Nothing of yours is parked',
        onClick: vi.fn(),
      },
    ],
    dialog: <div data-testid="parked-dialog-node" />,
  }),
}));

const getSnapshot = vi.fn();
const listSnapshotRows = vi.fn();
const setSnapshotDecision = vi.fn();
const exportSnapshot = vi.fn();
vi.mock('../services/stockDebtSnapshotService', () => ({
  getSnapshot: (...args: unknown[]) => getSnapshot(...args),
  listSnapshotRows: (...args: unknown[]) => listSnapshotRows(...args),
  setSnapshotDecision: (...args: unknown[]) => setSnapshotDecision(...args),
  exportSnapshot: (...args: unknown[]) => exportSnapshot(...args),
  getSnapshotRowLines: vi.fn().mockResolvedValue({ lines: [] }),
}));

import { SnapshotClient } from './SnapshotClient';

function header(over: Record<string, unknown> = {}) {
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
      show_aux: true,
    },
    filters_label:
      'Book Project · All locations · Delivery 01/01/2023 to 31/05/2027 · Only in debt · Stock and PO columns',
    months: ['2026-10', '2027-01'],
    tba_month: '2030-01',
    totals: {
      months: { '2026-10': -3692, '2027-01': -109893 },
      tba: 0,
      undated: 0,
      unlocated: 0,
      total: -507942,
      pool_on_hand: 162636,
      pool_po_qty: 0,
      pool_incoming_qty: 21121,
      dealer_outstanding: -7335,
    },
    row_count: 550,
    decided_count: 5,
    created_by_name: 'Jayson Foundryx',
    created_at: '2026-10-15T03:53:00Z',
    can_delete: true,
    prev_id: null,
    next_id: NEXT_SNAP,
    ...over,
  };
}

function snapRow(over: Record<string, unknown> = {}) {
  return {
    row_id: 'f0000000-0000-4000-8000-000000000001',
    product_id: 'f0000000-0000-4000-8000-0000000000a1',
    product_code: 'ACC6001',
    product_name: 'Acc 6001 coupling',
    months: [
      { key: '2026-10', balance: -93, tone: 'red' },
      { key: '2027-01', balance: -156, tone: 'red' },
    ],
    tba: 0,
    undated: 0,
    unlocated: 0,
    total: -369,
    supplier_id: null,
    supplier_name: null,
    category_code: null,
    pool_on_hand: 0,
    pool_po_qty: 0,
    pool_incoming_qty: 0,
    dealer_outstanding: 0,
    decision: null,
    decision_warehouse_id: null,
    decision_warehouse_code: null,
    decision_note: null,
    decided_by_name: null,
    decided_at: null,
    ...over,
  };
}

const ROWS = [
  snapRow(),
  snapRow({
    row_id: 'f0000000-0000-4000-8000-000000000002',
    product_code: 'SRTWT904-NL',
    product_name: null,
    total: -223,
    months: [
      { key: '2026-10', balance: 97, tone: 'green' },
      { key: '2027-01', balance: -30, tone: 'red' },
    ],
    decision: 'reserve',
    decision_warehouse_id: 'wh-brw',
    decision_warehouse_code: 'BRW',
    decision_note: 'Reserve all 223 from BRW',
    decided_by_name: 'Jayson Foundryx',
    decided_at: '2026-10-15T06:10:00Z',
  }),
  snapRow({
    row_id: 'f0000000-0000-4000-8000-000000000003',
    product_code: 'C-FH14',
    product_name: null,
    total: -23638,
    decision: 'buy',
  }),
  snapRow({
    row_id: 'f0000000-0000-4000-8000-000000000004',
    product_code: 'SRT23-CR',
    product_name: null,
    total: -633,
    decision: 'ignore',
    decision_note: 'Already reserved on OI-2609-0733',
  }),
];

function rowsEnvelope(rows = ROWS, total = 550) {
  return { data: rows, pagination: { total, page: 1, limit: 25 } };
}

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

function rowOf(code: string): HTMLElement {
  return screen.getByText(code).closest('tr') as HTMLElement;
}

function pick(scope: HTMLElement, name: string | RegExp) {
  return within(scope).queryByRole('radio', { name }) ?? within(scope).getByRole('button', { name });
}

beforeEach(() => {
  vi.clearAllMocks();
  permissions = new Set(['projects.stock_debt.view']);
  getSnapshot.mockResolvedValue(header());
  listSnapshotRows.mockResolvedValue(rowsEnvelope());
  setSnapshotDecision.mockResolvedValue(snapRow());
  exportSnapshot.mockResolvedValue({ id: 'd-1', status: 'pending' });
});

describe('AC-F3 header and meta', () => {
  it('shows the name with a Snapshot badge and a back link to the list', async () => {
    renderPage();
    const title = await screen.findByRole('heading', { name: /Stock Debt 15\/10\/2026/ });
    expect(within(title).getByText('Snapshot')).toBeInTheDocument();
    const back = screen.getByRole('link', { name: /back/i });
    expect(back.getAttribute('href')).toContain('/project-sales/stock-debt/snapshots');
    expect(back.getAttribute('href')).not.toContain(SNAP);
  });

  it('reads the snapshot and its rows by id', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    expect(getSnapshot).toHaveBeenCalledWith(SNAP);
    expect(listSnapshotRows).toHaveBeenCalledWith(
      SNAP,
      expect.objectContaining({ pageIndex: 0, query: '' }),
    );
  });

  it('prints the read-only facts under the header from the stored header', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    const page = document.body;
    expect(page).toHaveTextContent('Taken');
    expect(page).toHaveTextContent('15/10/2026');
    expect(page).toHaveTextContent('11:53');
    expect(page).toHaveTextContent('Jayson Foundryx');
    expect(page).toHaveTextContent('Project');
    expect(page).toHaveTextContent('01/01/2023 to 31/05/2027');
    expect(page).toHaveTextContent(/Only in debt/i);
    expect(page).toHaveTextContent('550 products');
    expect(page).toHaveTextContent('5 decided');
  });

  it('walks prev/next snapshots by id; prev is disabled when there is none', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    expect(screen.getByRole('button', { name: /^previous/i })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /^next/i }));
    expect(push).toHaveBeenCalled();
    expect(String(push.mock.calls[0][0])).toContain(`/project-sales/stock-debt/snapshots/${NEXT_SNAP}`);
  });

  it('prints no UUID anywhere (AC-F8)', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    expect(document.body.textContent).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    );
  });
});

describe('AC-F3 grid', () => {
  it('has Decision as the second column, right after Product', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    const headers = screen.getAllByRole('columnheader');
    expect(headers[0]).toHaveTextContent('Product');
    expect(headers[1]).toHaveTextContent('Decision');
  });

  it('draws the stored axis, TBA and Total, signed like the board', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent);
    expect(headers.some((h) => h?.includes('Oct 26'))).toBe(true);
    expect(headers.some((h) => h?.includes('Jan 27'))).toBe(true);
    expect(headers.some((h) => h?.includes('TBA'))).toBe(true);
    expect(headers.some((h) => h?.includes('Total'))).toBe(true);
    const row = rowOf('SRTWT904-NL');
    expect(row).toHaveTextContent('+97');
    expect(row).toHaveTextContent('-30');
    expect(row).toHaveTextContent('-223');
  });

  it('shows the four stock and PO columns when the snapshot stored show_aux', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    for (const name of [
      'BRW Retail On Hand Qty',
      'BRW Retail PO Qty',
      'BRW Retail Incoming Qty',
      'Dealer O/standing Qty',
    ]) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument();
    }
  });

  it('hides them when it did not', async () => {
    getSnapshot.mockResolvedValue(
      header({ filters: { ...header().filters, show_aux: false } }),
    );
    renderPage();
    await screen.findByText('SRTWT904-NL');
    expect(screen.queryByRole('columnheader', { name: 'BRW Retail On Hand Qty' })).not.toBeInTheDocument();
  });

  it('the footer is the STORED whole-set totals, not the sum of the page rows', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    const footer = document.querySelector('tfoot') as HTMLElement;
    expect(footer).toBeInTheDocument();
    expect(footer).toHaveTextContent('-507,942');
    expect(footer).toHaveTextContent('-3,692');
    expect(footer).toHaveTextContent('-109,893');
    expect(footer).toHaveTextContent('162,636');
  });

  it('has no Filters button and no Projected balance switch', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    expect(screen.queryByRole('button', { name: /^filters/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/projected/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/projected balance/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/only products in debt/i)).not.toBeInTheDocument();
  });

  it('the Decision cell reads Decide when empty, else a badge with the pool for Reserve', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    expect(within(rowOf('ACC6001')).getByRole('button', { name: /^decide$/i })).toBeInTheDocument();
    const reserve = rowOf('SRTWT904-NL');
    expect(reserve).toHaveTextContent('Reserve');
    expect(reserve).toHaveTextContent('BRW');
    expect(rowOf('C-FH14')).toHaveTextContent('Buy');
    expect(rowOf('SRT23-CR')).toHaveTextContent('Ignore');
  });
});

describe('AC-F3 search and Decision filter', () => {
  it('typing in the product search sends the query to the stored rows', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'SRTWT' } });
    await waitFor(() =>
      expect(listSnapshotRows).toHaveBeenLastCalledWith(
        SNAP,
        expect.objectContaining({ query: 'SRTWT', pageIndex: 0 }),
      ),
    );
  });

  it('the Decision select is clearable and offers Decided, Undecided, Reserve, Buy, Ignore', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    const select = screen.getByRole('combobox', { name: /decision/i });
    expect(select).toHaveAttribute('data-clearable', 'true');
    for (const label of ['Decided', 'Undecided', 'Reserve', 'Buy', 'Ignore']) {
      expect(within(select).getByRole('option', { name: label })).toBeInTheDocument();
    }
  });

  it('choosing Decided sends decision=decided; clearing drops it', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    const select = screen.getByRole('combobox', { name: /decision/i });

    fireEvent.change(select, { target: { value: 'decided' } });
    await waitFor(() =>
      expect(listSnapshotRows).toHaveBeenLastCalledWith(
        SNAP,
        expect.objectContaining({ decision: 'decided' }),
      ),
    );

    fireEvent.change(screen.getByRole('combobox', { name: /decision/i }), { target: { value: '' } });
    await waitFor(() => {
      const last = listSnapshotRows.mock.calls.at(-1)![1] as Record<string, unknown>;
      expect(last.decision || '').toBe('');
    });
  });
});

describe('AC-F3 states', () => {
  it('shows skeletons while loading', () => {
    getSnapshot.mockReturnValue(new Promise(() => {}));
    listSnapshotRows.mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it('states a failed read in the server words', async () => {
    getSnapshot.mockRejectedValue(new Error('Snapshot not found'));
    renderPage();
    expect(await screen.findByText('Snapshot not found')).toBeInTheDocument();
  });

  it('an empty filtered set says so, with no row', async () => {
    listSnapshotRows.mockResolvedValue(rowsEnvelope([], 0));
    renderPage();
    expect(await screen.findByText(/no product/i)).toBeInTheDocument();
  });
});

describe('AC-16 Export from the snapshot page', () => {
  it('Export popover starts the stored-snapshot export with split and show_aux', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    fireEvent.click(screen.getByRole('button', { name: /^export$/i }));
    const popover = await screen.findByRole('dialog');
    for (const label of ['None', 'Supplier', 'Category', 'Supplier x Category']) {
      expect(within(popover).getByText(label)).toBeInTheDocument();
    }
    fireEvent.click(within(popover).getByLabelText('Supplier'));
    fireEvent.click(within(popover).getByRole('button', { name: /^export$/i }));
    await waitFor(() => expect(exportSnapshot).toHaveBeenCalledTimes(1));
    expect(exportSnapshot).toHaveBeenCalledWith(SNAP, { split: 'supplier', show_aux: true });
  });
});

describe('AC-F6c Actions menu', () => {
  it('lists both send items from the shared hook, with the hook labels', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    const trigger = screen.getByRole('button', { name: /^actions$/i });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    expect(await screen.findByRole('menuitem', { name: 'Send all parked requests to CS (2)' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Send all parked reserves to purchasing (0)' })).toBeInTheDocument();
  });

  it('a greyed item carries the reason, an active one runs the hook onClick', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    const trigger = screen.getByRole('button', { name: /^actions$/i });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    const grey = await screen.findByRole('menuitem', { name: 'Send all parked reserves to purchasing (0)' });
    expect(grey).toHaveAttribute('data-disabled');
    expect(grey).toHaveAttribute('title', 'Nothing of yours is parked');

    fireEvent.click(screen.getByRole('menuitem', { name: 'Send all parked requests to CS (2)' }));
    expect(parkedAction).toHaveBeenCalledTimes(1);
  });

  it('mounts the hook dialog node on the page', async () => {
    renderPage();
    await screen.findByText('SRTWT904-NL');
    expect(screen.getByTestId('parked-dialog-node')).toBeInTheDocument();
  });
});

describe('AC-F4 Decision popover', () => {
  async function openPopover(code: string) {
    await screen.findByText(code);
    const row = rowOf(code);
    const trigger = within(row).queryByRole('button', { name: /^decide$/i }) ?? within(row).getByRole('button', { name: /reserve|buy|ignore/i });
    fireEvent.click(trigger);
    return (await screen.findByRole('dialog')) as HTMLElement;
  }

  it('offers Reserve, Buy and Ignore, a Note, and hides From until Reserve is picked', async () => {
    renderPage();
    const popover = await openPopover('ACC6001');
    for (const name of ['Reserve', 'Buy', 'Ignore']) {
      expect(pick(popover, name)).toBeInTheDocument();
    }
    expect(within(popover).getByLabelText(/note/i)).toBeInTheDocument();
    expect(within(popover).queryByRole('combobox', { name: /from/i })).not.toBeInTheDocument();
  });

  it('the Note textarea is w-full with no fixed width, so it cannot overflow the popover', async () => {
    renderPage();
    const popover = await openPopover('ACC6001');
    const note = within(popover).getByLabelText(/note/i) as HTMLTextAreaElement;
    expect(note.className).toMatch(/(^|\s)w-full(\s|$)/);
    expect(note.style.width).toBe('');
    expect(note.className).not.toMatch(/(^|\s)w-\[|(^|\s)w-\d/);
  });

  it('From appears for Reserve only, as a clearable select of the pools with their stock', async () => {
    renderPage();
    const popover = await openPopover('ACC6001');

    fireEvent.click(pick(popover, 'Reserve'));
    const from = within(popover).getByRole('combobox', { name: /from/i });
    expect(from).toHaveAttribute('data-clearable', 'true');
    expect(within(from).getByRole('option', { name: /BRW\s+available 972/ })).toBeInTheDocument();
    expect(within(from).getByRole('option', { name: /MWH\s+available 0/ })).toBeInTheDocument();

    fireEvent.click(pick(popover, 'Buy'));
    expect(within(popover).queryByRole('combobox', { name: /from/i })).not.toBeInTheDocument();
    fireEvent.click(pick(popover, 'Ignore'));
    expect(within(popover).queryByRole('combobox', { name: /from/i })).not.toBeInTheDocument();
  });

  it('Save sends Reserve with the pool and the note', async () => {
    renderPage();
    const popover = await openPopover('ACC6001');

    fireEvent.click(pick(popover, 'Reserve'));
    fireEvent.change(within(popover).getByRole('combobox', { name: /from/i }), {
      target: { value: 'wh-brw' },
    });
    fireEvent.change(within(popover).getByLabelText(/note/i), { target: { value: 'Reserve the 24 in BRW' } });
    fireEvent.click(within(popover).getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(setSnapshotDecision).toHaveBeenCalledTimes(1));
    expect(setSnapshotDecision).toHaveBeenCalledWith(
      SNAP,
      'f0000000-0000-4000-8000-000000000001',
      { decision: 'reserve', warehouse_id: 'wh-brw', note: 'Reserve the 24 in BRW' },
    );
  });

  it('Buy sends no pool, and a blank note goes as null', async () => {
    renderPage();
    const popover = await openPopover('ACC6001');
    fireEvent.click(pick(popover, 'Buy'));
    fireEvent.click(within(popover).getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(setSnapshotDecision).toHaveBeenCalledTimes(1));
    expect(setSnapshotDecision).toHaveBeenCalledWith(
      SNAP,
      'f0000000-0000-4000-8000-000000000001',
      { decision: 'buy', warehouse_id: null, note: null },
    );
  });

  it('switching from Reserve to Ignore drops the pool that was picked', async () => {
    renderPage();
    const popover = await openPopover('ACC6001');
    fireEvent.click(pick(popover, 'Reserve'));
    fireEvent.change(within(popover).getByRole('combobox', { name: /from/i }), {
      target: { value: 'wh-brw' },
    });
    fireEvent.click(pick(popover, 'Ignore'));
    fireEvent.click(within(popover).getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(setSnapshotDecision).toHaveBeenCalledTimes(1));
    expect((setSnapshotDecision.mock.calls[0][2] as Record<string, unknown>).warehouse_id).toBeNull();
    expect((setSnapshotDecision.mock.calls[0][2] as Record<string, unknown>).decision).toBe('ignore');
  });

  it('opens on a decided row with its choice, pool, note, who and when', async () => {
    renderPage();
    const popover = await openPopover('SRTWT904-NL');
    expect((within(popover).getByRole('combobox', { name: /from/i }) as HTMLSelectElement).value).toBe('wh-brw');
    expect((within(popover).getByLabelText(/note/i) as HTMLTextAreaElement).value).toBe(
      'Reserve all 223 from BRW',
    );
    expect(popover).toHaveTextContent('Jayson Foundryx');
    expect(popover).toHaveTextContent('15/10/2026');
    expect(within(popover).getByRole('button', { name: /clear decision/i })).toBeInTheDocument();
  });

  it('an undecided row has no Clear decision', async () => {
    renderPage();
    const popover = await openPopover('ACC6001');
    expect(within(popover).queryByRole('button', { name: /clear decision/i })).not.toBeInTheDocument();
  });

  it('Clear decision sends nulls for all three fields', async () => {
    renderPage();
    const popover = await openPopover('SRTWT904-NL');
    fireEvent.click(within(popover).getByRole('button', { name: /clear decision/i }));
    await waitFor(() => expect(setSnapshotDecision).toHaveBeenCalledTimes(1));
    expect(setSnapshotDecision).toHaveBeenCalledWith(
      SNAP,
      'f0000000-0000-4000-8000-000000000002',
      { decision: null, warehouse_id: null, note: null },
    );
  });

  it('refreshes the stored rows after a save', async () => {
    renderPage();
    const popover = await openPopover('ACC6001');
    fireEvent.click(pick(popover, 'Buy'));
    const before = listSnapshotRows.mock.calls.length;
    fireEvent.click(within(popover).getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(listSnapshotRows.mock.calls.length).toBeGreaterThan(before));
  });

  it('a failed save toasts the extracted message', async () => {
    setSnapshotDecision.mockRejectedValue(new Error('Pick a site pool'));
    renderPage();
    const popover = await openPopover('ACC6001');
    fireEvent.click(pick(popover, 'Buy'));
    fireEvent.click(within(popover).getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Pick a site pool'));
  });
});
