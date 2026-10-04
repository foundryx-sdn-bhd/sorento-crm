/**
 * SalesOrderDetail - the demand-side twin of `PurchaseOrderDetail`.
 *
 * Two things this file is here to hold. The first is the CRUD standard: every section is
 * rendered, with an explicit empty message, so a panel that is missing means something rather
 * than reading as "not loaded yet". The record is TABBED now (General / Lines / Delivery, the
 * same shape as the user detail page), so "rendered" means "reachable on its tab" - which is
 * why almost every assertion below opens the tab it is about first.
 *
 * The second is specific to this screen. 11,006 of the sales orders in the system were
 * absorbed from a six-year AutoCount export and are closed history; the rest are live
 * commitments the plan is computed from. What the header used to carry - a "Committed demand"
 * / "Not committed (...)" line beside the status - is gone at the captain's request: the
 * status, the Source field and the Delivery tab already say all three of those things, and it
 * was a fourth restatement in the one place with least room for it.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within, waitFor } from '@testing-library/react';
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
Element.prototype.scrollIntoView = vi.fn();

// A `let`, not a literal return, so the `?edit=1` auto-open test can point it at a URL
// carrying the param without a second mock module.
let searchParams = new URLSearchParams();
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

// The tab lives in the URL, not component state (see `handleTabChange`), so `openTab`
// below has to reach a REAL `router.replace` - one that feeds back into `searchParams`
// and wakes up every subscriber - or the controlled `<Tabs>` never sees the new value
// and stays on whichever tab it opened on.
const searchParamsListeners = new Set<() => void>();
function replaceSearchParams(next: URLSearchParams) {
  searchParams = next;
  searchParamsListeners.forEach((listener) => listener());
}
vi.mock('next/navigation', () => ({
  usePathname: () => '/scm/sales-orders/so-1',
  useRouter: () => ({
    push: vi.fn(),
    replace: (url: string) => {
      const qIndex = url.indexOf('?');
      replaceSearchParams(new URLSearchParams(qIndex >= 0 ? url.slice(qIndex + 1) : ''));
    },
  }),
  useSearchParams: () =>
    React.useSyncExternalStore(
      (listener) => {
        searchParamsListeners.add(listener);
        return () => searchParamsListeners.delete(listener);
      },
      () => searchParams,
    ),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

// The header's Plan primary (S2, R3) reaches for the NextAuth session through this hook,
// which is not mounted under jsdom - stubbed off, the same way the sales-orders LIST suite
// stubs it, since this file's own tests are about Edit under the gear, not about the
// permission gate (that is a browser-pass check, AC-S2-6/10).
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

// The Transfers tab renders the SAME grid the Transfers page does; that component has its
// own tests, so here only the wiring (which order it is pinned to) has to be proven.
vi.mock(
  '@/app/(protected)/inventory-management/stock-transfers/components/StockTransfersPanel',
  () => ({
    StockTransfersPanel: (props: { salesOrderId?: string; showFilters?: boolean }) => (
      <div data-testid="stock-transfers-panel" data-sales-order={props.salesOrderId}>
        {String(props.showFilters)}
      </div>
    ),
  }),
);

const useSalesOrder = vi.fn();
const updateSalesOrderMutateAsync = vi.fn();
vi.mock('../../../hooks/useSalesOrders', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  salesOrdersPagerQuery: {
    listQueryKey: () => ['scm-sales-orders'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useSalesOrder: (...a: unknown[]) => useSalesOrder(...a),
  // The record's gear renders the shared action set, whose Delete needs this.
  useDeleteSalesOrder: () => ({ isPending: false, mutateAsync: vi.fn() }),
  // The header's prev/next pager reads the same list the user came from. An empty page
  // holds no record, so the pager renders nothing and these tests stay about the record.
  useSalesOrders: () => ({ data: { data: [], pagination: { total: 0, page: 1, limit: 25 } } }),
  useUpdateSalesOrder: () => ({ mutateAsync: updateSalesOrderMutateAsync, isPending: false }),
}));

vi.mock('../../../hooks/useScmOptions', () => ({
  useWarehouseOptions: () => ({
    data: [
      { value: 'BRW-BB', label: 'Brickworks Batu Berendam' },
      { value: 'BRW-IB', label: 'Brickworks Iskandar' },
      { value: 'KL-01', label: 'Kuala Lumpur 01' },
    ],
    isLoading: false,
  }),
}));

// The Customer and Product selects are SERVER-SEARCHED now - `fetchOptions`, not a static
// array - because the two masters behind them hold 6,397 and ~22,000 rows. Mocked as the
// component calls them: `(query, pageIndex) => Promise<Option[]>`.
vi.mock('../../../services/scmOptionsService', () => ({
  SELECT_PAGE_SIZE: 50,
  searchCustomerOptions: vi.fn(async () => [
    { value: '300-R009', label: 'Rowenda Kitchen Sdn Bhd' },
  ]),
  searchProductOptions: vi.fn(async () => [
    { value: 'CW-BASIN-450', label: 'CW-BASIN-450 · Ceramic Wash Basin 450mm' },
  ]),
}));

vi.mock('../../hooks/useSalesAgentOptions', () => ({
  useSalesAgentOptions: () => ({
    options: [
      { value: 'agent-jeremy', label: 'JR001 · JEREMY' },
      { value: 'agent-cindy', label: 'CL002 · CONTACT W' },
    ],
  }),
}));

// `getSalesOrderUoms` is the only export the component reads off this service module - the
// UoM select's own options. Everything else the component uses from it (`updateSalesOrder`)
// is reached through the fully-mocked `useUpdateSalesOrder` above, never this module.
vi.mock('../../../services/salesOrderService', () => ({
  getSalesOrderUoms: () =>
    Promise.resolve([
      { id: 'uom-pcs', uom_code: 'PCS', uom_name: 'Pieces' },
      { id: 'uom-box', uom_code: 'BOX', uom_name: 'Box' },
    ]),
}));

import { SalesOrderDetail } from './SalesOrderDetail';
import type { SalesOrder, SalesOrderLine } from '../../../types/scm.types';

function so(over: Partial<SalesOrder> = {}): SalesOrder {
  return {
    id: 'so-1',
    so_number: 'SO-2026/07-0042',
    order_type: 'project',
    order_type_label: 'Project',
    // The PLANNING CLASS, which is what the screen both renders and edits. `order_type` is
    // the ERP document type and is NULL on 96% of this book.
    demand_class: 'project',
    customer_code: '300-R009',
    customer_name: 'Rowenda Kitchen Sdn Bhd',
    market_segment: 'Project',
    priority: 'normal',
    status: 'open',
    order_date: '2026-07-16',
    requested_delivery_date: '2026-08-30',
    total_qty: 320,
    committed_qty: 320,
    total_amount: '31985.00',
    line_count: 1,
    open_line_count: 1,
    stock_locations: ['BRW-BB'],
    source: 'manual',
    internal_note: null,
    lines: [
      {
        id: 'l-1',
        sku: 'CW-BASIN-450',
        product_name: 'Ceramic Wash Basin 450mm',
        qty_ordered: 320,
        qty_delivered: 0,
        uom: 'PCS',
        unit_price: '100.00',
        discount: '15.00',
        line_total: '31985.00',
        warehouse_code: 'BRW-BB',
        line_status: 'open',
        required_date: '2026-08-30',
      },
    ],
    created_at: '2026-07-16T02:00:00',
    ...over,
  } as SalesOrder;
}

function renderDetail() {
  // A real `QueryClient`, not mocked: the UoM select's own `useQuery` (`getSalesOrderUoms`,
  // mocked above) needs a provider to run at all. `retry: false` so an unmet expectation
  // fails fast instead of retrying into the test's own timeout.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SalesOrderDetail id="so-1" />
    </QueryClientProvider>,
  );
}

/**
 * Open one of the record's tabs. Every section still exists; this is where it lives.
 *
 * `mouseDown`, not `click`: Radix's tab trigger selects on mouse-down (a plain `click` event
 * in jsdom leaves the tab strip exactly where it was, which reads as a section that vanished).
 */
function openTab(name: 'General' | 'Lines' | 'Delivery' | 'Transfers') {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false });
}

/**
 * Edit lives behind the gear now (S2, R3): the header's primary slot is Plan. Radix opens
 * its menu on POINTER-down (see the Columns button elsewhere in this file), so open it the
 * same way, then press the Edit item.
 */
function clickEdit() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Sales order options' }), {
    button: 0,
    ctrlKey: false,
    pointerType: 'mouse',
  });
  fireEvent.click(screen.getByRole('menuitem', { name: /^Edit$/ }));
}

beforeEach(() => {
  cleanup();
  useSalesOrder.mockReset();
  // `planning_change_batch: null` - the shape every save's response carries, whether or not
  // it raised one. `handleSave` reads this key unconditionally.
  updateSalesOrderMutateAsync.mockReset().mockResolvedValue({ planning_change_batch: null });
  searchParams = new URLSearchParams();
});

describe('SalesOrderDetail - states', () => {
  it('shows a skeleton and the way back while loading', () => {
    useSalesOrder.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    renderDetail();
    // The way back is present even before the record is: a slow read must not trap anybody.
    expect(screen.getByText('Back to sales orders')).toBeInTheDocument();
  });

  it('names what is missing rather than rendering an empty shell', () => {
    useSalesOrder.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    renderDetail();
    expect(screen.getByText('Sales order not found')).toBeInTheDocument();
    expect(screen.getByText('Back to sales orders')).toBeInTheDocument();
  });

  it('renders the document number, the customer and every summary field', () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.getByText('SO-2026/07-0042')).toBeInTheDocument();
    expect(screen.getByText('Rowenda Kitchen Sdn Bhd')).toBeInTheDocument();
    expect(screen.getByText('300-R009')).toBeInTheDocument();
    // Per card, because "Customer" is now both a card TITLE and a field label inside it.
    const fields: Record<string, string[]> = {
      Order: ['Order type', 'Priority', 'Order date', 'Delivery date', 'Agent', 'Source'],
      Customer: ['Customer', 'Customer code'],
      Totals: ['Total amount', 'Total qty', 'Lines'],
    };
    for (const [card, labels] of Object.entries(fields)) {
      const region = screen.getByRole('region', { name: card });
      for (const label of labels) {
        expect(within(region).getByText(label)).toBeInTheDocument();
      }
    }
  });

  it('groups the summary into three cards of at most two columns each', () => {
    // One eleven-field grid four across reads as a wall. These are the three things a person
    // asks about an order separately - what it is, who it is for, what it comes to.
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    for (const name of ['Order', 'Customer', 'Totals']) {
      const region = screen.getByRole('region', { name });
      expect(region).toBeInTheDocument();
      // Never a third column: `lg:grid-cols-4` is what made it a wall.
      expect(region.className).toContain('sm:grid-cols-2');
      expect(region.className).not.toMatch(/grid-cols-[34]/);
    }
    expect(screen.queryByRole('region', { name: 'Order summary' })).not.toBeInTheDocument();
  });

  it('drops Market segment - the customer record is where a segment is read', () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.queryByText('Market segment')).not.toBeInTheDocument();
  });

  it('shows the project label under the SO number, and nothing when there is none (AC-F2)', () => {
    useSalesOrder.mockReturnValue({
      data: so({ project_label: 'BAMBOO RESIDENCE / KUALA LUMPUR' }),
      isLoading: false,
      isError: false,
    });
    const { unmount } = renderDetail();
    // Switching off the General tab proves this is the HEADER's own subtitle, not the
    // Order card's "Project" field (which only exists on that tab) - the header is read-
    // only metadata and must survive whichever tab is open.
    openTab('Lines');
    expect(screen.getByText('BAMBOO RESIDENCE / KUALA LUMPUR')).toBeInTheDocument();
    unmount();

    useSalesOrder.mockReturnValue({
      data: so({ project_label: null }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    expect(screen.queryByText(/RESIDENCE|KUALA LUMPUR/)).not.toBeInTheDocument();
  });

  it('the Order card names the project with its muted source word, or a dash (AC-F3)', () => {
    useSalesOrder.mockReturnValue({
      data: so({ project_label: 'BAMBOO RESIDENCE / KUALA LUMPUR', project_label_source: 'inquiry' }),
      isLoading: false,
      isError: false,
    });
    const { unmount } = renderDetail();
    // Order type's own value (`demandClassBadge`) also reads "Project" for a project-class
    // order, so the FIELD's own label span is picked out by its exact class rather than by
    // text alone - `screen.getByText('Project')` would otherwise match both.
    const field = () =>
      (screen.getByText('Project', { selector: 'span.text-xs.text-muted-foreground' }).closest(
        'div',
      ) as HTMLElement);
    expect(within(field()).getByText('BAMBOO RESIDENCE / KUALA LUMPUR')).toBeInTheDocument();
    expect(within(field()).getByText('· Inquiry sheet')).toBeInTheDocument();
    unmount();

    useSalesOrder.mockReturnValue({
      data: so({ project_label: 'TAIGA RESIDENCE', project_label_source: 'note' }),
      isLoading: false,
      isError: false,
    });
    const second = renderDetail();
    expect(within(field()).getByText('· Note')).toBeInTheDocument();
    second.unmount();

    useSalesOrder.mockReturnValue({
      data: so({ project_label: null, project_label_source: null }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    expect(within(field()).getByText('-')).toBeInTheDocument();
  });

  it('states what the order is worth, in ringgit, and a dash when nobody priced it', () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    const { unmount } = renderDetail();
    const totals = () => screen.getByRole('region', { name: 'Totals' });
    expect(within(totals()).getByText('RM 31,985.00')).toBeInTheDocument();
    unmount();

    // Not RM 0.00: an order nobody priced is not an order worth nothing, and 15,000 of the
    // absorbed rows are exactly that.
    useSalesOrder.mockReturnValue({
      data: so({ total_amount: null }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    const amount = screen.getByText('Total amount').closest('div');
    expect(within(amount as HTMLElement).getByText('-')).toBeInTheDocument();
  });

  it('shows the order\'s stock transfers on their own tab, pinned to it (AC-E6)', () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    openTab('Transfers');

    const panel = screen.getByTestId('stock-transfers-panel');
    expect(panel).toHaveAttribute('data-sales-order', 'so-1');
    // Pinned, so the page-level filter bar and bulk approve are off.
    expect(panel).toHaveTextContent('false');
  });

  it('carries one tab per concern of the order, General first', () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'General',
      'Lines',
      'Delivery',
      'Transfers',
    ]);
    expect(screen.getByRole('tab', { name: 'General' })).toHaveAttribute(
      'data-state',
      'active',
    );
  });

  it('opens on the tab named by `?tab=`, so the pager can carry it across a Next/Prev step', () => {
    // `SalesOrderNavigation` pushes `${pathname}?${searchParams}` on Next/Prev - the tab has
    // to live in the URL, not component state, or stepping to a different order always lands
    // on General regardless of which tab the reader was on.
    searchParams = new URLSearchParams('tab=lines');
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.getByRole('tab', { name: 'Lines' })).toHaveAttribute('data-state', 'active');
    expect(screen.getByText('Order lines')).toBeInTheDocument();
  });

  it('does not restate the order LOCATIONS on the header - a location belongs to a line', () => {
    // One order routinely ships from two warehouses, so "BRW-BB, KL-01" on the header said
    // less than the Lines tab, which says which line goes where.
    useSalesOrder.mockReturnValue({
      data: so({ stock_locations: ['BRW-BB', 'KL-01'] }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.queryByText('Locations')).not.toBeInTheDocument();
    expect(screen.queryByText('BRW-BB, KL-01')).not.toBeInTheDocument();
  });

  it('shows each line\'s Location, falling back to "-" only for a line the API sent none for', () => {
    // The gap this guards: a line carrying `warehouse_code` must render the code, and a line
    // without one (closed history with no warehouse assigned) must read "-", not a blank cell
    // that is indistinguishable from a column that failed to render at all.
    useSalesOrder.mockReturnValue({
      data: so({
        lines: [
          {
            id: 'l-open', sku: 'SKU-OPEN', product_name: 'Open line', qty_ordered: 150,
            qty_delivered: 0, uom: 'L', warehouse_code: 'BRW-IB', line_status: 'open',
            required_date: null,
          },
          {
            id: 'l-closed', sku: 'SKU-CLOSED', product_name: 'Closed line', qty_ordered: 500,
            qty_delivered: 0, uom: 'L', warehouse_code: '', line_status: 'closed',
            required_date: '2026-01-10',
          },
        ],
        line_count: 2,
        open_line_count: 1,
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const openRow = screen.getByText('SKU-OPEN').closest('tr');
    const closedRow = screen.getByText('SKU-CLOSED').closest('tr');
    expect(openRow).not.toBeNull();
    expect(closedRow).not.toBeNull();
    expect(within(openRow as HTMLElement).getByText('BRW-IB')).toBeInTheDocument();
    // The closed row has no location - it prints the same "-" every other empty cell on this
    // grid uses, never a silently blank <td>.
    expect(within(closedRow as HTMLElement).getAllByText('-').length).toBeGreaterThan(0);
  });

  it('renders every section even when the record is bare', () => {
    // The CRUD standard, asserted on the emptiest record the API can return: a section that
    // disappears on missing data reads as a page that failed to load. Tabbed now, so each one
    // is opened rather than expected to be on screen at once.
    useSalesOrder.mockReturnValue({
      data: so({ lines: [], total_qty: 0, committed_qty: 0, line_count: 0, open_line_count: 0 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByText('Note')).toBeInTheDocument();
    expect(screen.getByText(/No note\./)).toBeInTheDocument();

    openTab('Lines');
    expect(screen.getByText('Order lines')).toBeInTheDocument();
    expect(screen.getByText('This sales order has no lines.')).toBeInTheDocument();

    openTab('Delivery');
    expect(screen.getByText(/Nothing delivered yet/)).toBeInTheDocument();
  });
});

describe('SalesOrderDetail - the header says what the order IS, once', () => {
  it('states the status as a standard pill and nothing about "committed demand"', () => {
    // The indicator beside the status is gone: the status, the Source field and the Delivery
    // tab each already answer "is this still owed", and a fourth phrasing of it was the
    // captain's complaint about this header.
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    // Worded the way AutoCount words it - `open` is Outstanding - and shaped like every
    // other pill on the page: a light chip, no dot and no ghost.
    const badge = screen.getByText('Outstanding').closest('[data-slot="badge"]');
    expect(badge).not.toBeNull();
    expect(badge?.className).not.toContain('bg-transparent');
    expect(badge?.className).toContain('--color-success-soft');
    expect(badge?.querySelector('[data-slot="badge-dot"]')).toBeNull();
    expect(screen.queryByText('Open')).not.toBeInTheDocument();
    expect(screen.queryByText('Committed demand')).not.toBeInTheDocument();
    expect(screen.queryByText(/Not committed/)).not.toBeInTheDocument();
  });

  it('words a closed order "Completed", on the header and on its lines', () => {
    useSalesOrder.mockReturnValue({
      data: so({
        status: 'closed',
        committed_qty: 0,
        open_line_count: 0,
        lines: [
          {
            id: 'l-1', sku: 'CW-BASIN-450', product_name: 'Ceramic Wash Basin 450mm',
            qty_ordered: 320, qty_delivered: 320, uom: 'PCS',
            warehouse_code: 'BRW-BB', line_status: 'closed', required_date: '2020-03-15',
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByText('Completed')).toBeInTheDocument();
    openTab('Lines');
    // Header and line both, from the one helper - the two cannot drift into two words.
    expect(screen.getAllByText('Completed').length).toBe(2);
    expect(screen.queryByText('Closed')).not.toBeInTheDocument();
  });

  it('still says an absorbed order is absorbed - on the Source field, where it belongs', () => {
    // The distinction that matters on this database: 11,006 of these orders were read off a
    // 2020-2026 spreadsheet. Calling one "delivered" claims a delivery this system recorded,
    // when it only ever read one.
    useSalesOrder.mockReturnValue({
      data: so({
        status: 'closed',
        source: 'history',
        committed_qty: 0,
        open_line_count: 0,
        lines: [
          {
            id: 'l-1', sku: 'CW-BASIN-450', product_name: 'Ceramic Wash Basin 450mm',
            qty_ordered: 320, qty_delivered: 320, uom: 'PCS',
            warehouse_code: 'BRW-BB', line_status: 'closed', required_date: '2020-03-15',
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByText('Absorbed history')).toBeInTheDocument();
    expect(screen.queryByText(/Not committed/)).not.toBeInTheDocument();
  });

  it('keeps the system-wide colour for a status the sales book does not rename', () => {
    // Only `open` and `closed` are reworded; `cancelled` keeps its own word AND its own
    // colour, read off the system table rather than invented here.
    useSalesOrder.mockReturnValue({
      data: so({ status: 'cancelled', committed_qty: 0, open_line_count: 0 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    const badge = screen.getByText('Cancelled').closest('[data-slot="badge"]');
    expect(badge).not.toBeNull();
    // The light family's destructive, since every pill on this page is now a light chip.
    expect(badge?.className).toContain('--color-destructive-soft');
  });

  it('shows "Outstanding qty" ALWAYS, even when it repeats the total', () => {
    // It used to appear only when it differed from the total, on the grounds that a repeated
    // figure is noise. A field that comes and goes is worse: on a wholly open order the
    // reader has to work out from its ABSENCE that nothing has shipped, and a section that
    // hides on some records teaches nobody where anything lives.
    // Scoped to the Totals card, because the lines grid carries a column of the same name -
    // deliberately, since it is the same quantity once per order and once per line.
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    const { unmount } = renderDetail();
    const summary = () => screen.getByRole('region', { name: 'Totals' });
    expect(within(summary()).getByText('Outstanding qty')).toBeInTheDocument();
    unmount();

    useSalesOrder.mockReturnValue({
      data: so({ committed_qty: 120 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    expect(within(summary()).getByText('Outstanding qty')).toBeInTheDocument();
    expect(within(summary()).getByText('120')).toBeInTheDocument();
  });
});

describe('SalesOrderDetail - delivery panel', () => {
  it('reports a part delivery with what is left and across how many lines', () => {
    useSalesOrder.mockReturnValue({
      data: so({ total_qty: 320, committed_qty: 120, open_line_count: 2 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Delivery');
    expect(screen.getByText(/200 of 320 delivered/)).toBeInTheDocument();
    expect(screen.getByText(/120 outstanding across 2 lines/)).toBeInTheDocument();
  });

  it('reports a full delivery once nothing is owed', () => {
    useSalesOrder.mockReturnValue({
      data: so({ committed_qty: 0, open_line_count: 0 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Delivery');
    expect(screen.getByText('delivered in full')).toBeInTheDocument();
  });
});

describe('SalesOrderDetail - sorting the lines', () => {
  /**
   * A header that carries a sort arrow and does nothing is worse than a header that offers
   * no sort at all: the user reads the reordered arrow as a reordered table and trusts the
   * top row. These lines arrive in an order that is ascending in NO column, so a grid that
   * quietly ignores the click cannot pass by accident.
   */
  const SORT_LINES: SalesOrderLine[] = [
    {
      id: 'l-b', sku: 'SKU-B', product_name: 'Beta basin', qty_ordered: 320,
      qty_delivered: 300, uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open',
      required_date: '2026-09-01',
    },
    {
      id: 'l-c', sku: 'SKU-C', product_name: 'Gamma tap', qty_ordered: 45,
      qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open',
      required_date: '2026-07-04',
    },
    {
      id: 'l-a', sku: 'SKU-A', product_name: 'Alpha pan', qty_ordered: 1200,
      qty_delivered: 1100, uom: 'PCS', warehouse_code: 'KL-01', line_status: 'open',
      required_date: '2026-08-15',
    },
  ];

  const skuOrder = () =>
    screen
      .getAllByRole('row')
      .map((row) => row.textContent ?? '')
      .filter((text) => text.includes('SKU-'))
      .map((text) => text.match(/SKU-[A-Z]/)?.[0] ?? '');

  beforeEach(() => {
    useSalesOrder.mockReturnValue({
      data: so({ lines: SORT_LINES, line_count: 3, open_line_count: 3 }),
      isLoading: false,
      isError: false,
    });
  });

  it('orders by quantity, ascending then descending, when the header is clicked', () => {
    renderDetail();
    openTab('Lines');
    expect(skuOrder()).toEqual(['SKU-B', 'SKU-C', 'SKU-A']);

    // 45 < 320 < 1200 - a numeric order, not the alphabetical one a formatted string gives.
    fireEvent.click(screen.getByRole('button', { name: 'Qty ordered' }));
    expect(skuOrder()).toEqual(['SKU-C', 'SKU-B', 'SKU-A']);

    fireEvent.click(screen.getByRole('button', { name: 'Qty ordered' }));
    expect(skuOrder()).toEqual(['SKU-A', 'SKU-B', 'SKU-C']);
  });

  it('orders by what has been delivered', () => {
    renderDetail();
    openTab('Lines');
    fireEvent.click(screen.getByRole('button', { name: 'Qty delivered' }));
    expect(skuOrder()).toEqual(['SKU-C', 'SKU-B', 'SKU-A']);
  });

  it('orders "Outstanding qty" by the figure it shows, not by the order the lines arrived in', () => {
    // The computed column: 20, 45, 100 owed. Its order differs from every other column's,
    // so this fails if the header sorts on anything but the number in the cell.
    renderDetail();
    openTab('Lines');
    fireEvent.click(screen.getByRole('button', { name: 'Outstanding qty' }));
    expect(skuOrder()).toEqual(['SKU-B', 'SKU-C', 'SKU-A']);

    fireEvent.click(screen.getByRole('button', { name: 'Outstanding qty' }));
    expect(skuOrder()).toEqual(['SKU-A', 'SKU-C', 'SKU-B']);
  });

  it('orders the required date chronologically, not by how it is printed', () => {
    // Printed as 04 Jul / 15 Aug / 01 Sep, so a sort on the displayed text would read
    // 01, 04, 15 and put September first.
    renderDetail();
    openTab('Lines');
    fireEvent.click(screen.getByRole('button', { name: 'Delivery date' }));
    expect(skuOrder()).toEqual(['SKU-C', 'SKU-A', 'SKU-B']);
  });
});

describe('SalesOrderDetail - finding one product on a long order', () => {
  /**
   * A contract order runs to a couple of hundred lines and the question asked of it is almost
   * always "is this item on here, and how many": scrolling for it is the whole cost. The
   * search is over the lines already loaded - the order is the unit, so there is no request
   * and no paging to work across.
   */
  const LINES: SalesOrderLine[] = [
    {
      id: 'l-basin', sku: 'CW-BASIN-450', product_name: 'Ceramic Wash Basin 450mm',
      qty_ordered: 320, qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB',
      line_status: 'open', required_date: '2026-08-30',
    },
    {
      id: 'l-tap', sku: 'TAP-CHR-12', product_name: 'Chrome pillar tap',
      qty_ordered: 45, qty_delivered: 0, uom: 'PCS', warehouse_code: 'KL-01',
      line_status: 'open', required_date: '2026-09-04',
    },
  ];

  beforeEach(() => {
    useSalesOrder.mockReturnValue({
      data: so({ lines: LINES, line_count: 2, open_line_count: 2 }),
      isLoading: false,
      isError: false,
    });
  });

  it('filters the lines by product code', async () => {
    renderDetail();
    openTab('Lines');

    fireEvent.change(screen.getByLabelText('Search lines'), { target: { value: 'tap-chr' } });

    await waitFor(() => expect(screen.queryByText('CW-BASIN-450')).not.toBeInTheDocument());
    expect(screen.getByText('TAP-CHR-12')).toBeInTheDocument();
  });

  it('filters by the description too - the code is not always what is remembered', async () => {
    renderDetail();
    openTab('Lines');

    fireEvent.change(screen.getByLabelText('Search lines'), { target: { value: 'basin' } });

    await waitFor(() => expect(screen.queryByText('TAP-CHR-12')).not.toBeInTheDocument());
    expect(screen.getByText('CW-BASIN-450')).toBeInTheDocument();
  });

  it('says the search found nothing, rather than claiming the order has no lines', async () => {
    renderDetail();
    openTab('Lines');

    fireEvent.change(screen.getByLabelText('Search lines'), { target: { value: 'zzz' } });

    await waitFor(() =>
      expect(screen.getByText(/No line on this order matches that product/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText('This sales order has no lines.')).not.toBeInTheDocument();
  });

  it('restores every line when the search is cleared', async () => {
    renderDetail();
    openTab('Lines');

    fireEvent.change(screen.getByLabelText('Search lines'), { target: { value: 'basin' } });
    await waitFor(() => expect(screen.getByText('CW-BASIN-450')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));

    await waitFor(() => expect(screen.getByText('TAP-CHR-12')).toBeInTheDocument());
    expect(screen.getByText('CW-BASIN-450')).toBeInTheDocument();
  });

  it('offers the standard Columns control, so the chosen columns survive the visit', () => {
    renderDetail();
    openTab('Lines');

    expect(screen.getByRole('button', { name: /Columns/ })).toBeInTheDocument();
  });
});

describe('SalesOrderDetail - the lines say what the customer was charged', () => {
  /**
   * The grid printed quantities and nothing else, on a book whose own export states a unit
   * price, a discount and a line total for every row. "Who ordered it and at what price" is
   * the question a buyer opens this screen with, and it could not be answered here.
   */
  const TWO_LINES: SalesOrderLine[] = [
    {
      id: 'l-a', sku: 'SKU-A', product_name: 'Alpha pan', qty_ordered: 10,
      qty_delivered: 4, uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open',
      required_date: '2026-08-15', unit_price: '100.00', discount: '15.00',
      line_total: '985.00',
    },
    {
      id: 'l-b', sku: 'SKU-B', product_name: 'Beta basin', qty_ordered: 2,
      qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open',
      required_date: '2026-09-01', unit_price: '10.00', discount: null,
      line_total: null,
    },
  ];

  function renderLines(over: Partial<SalesOrder> = {}) {
    useSalesOrder.mockReturnValue({
      data: so({ lines: TWO_LINES, line_count: 2, open_line_count: 2, ...over }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');
  }

  it('names the first column Product and the computed one Outstanding qty', () => {
    // "SKU" is the code; the column shows the code AND the description. "Still owed" was the
    // same figure under a phrase nobody in the warehouse uses.
    renderLines();

    expect(screen.getByRole('button', { name: 'Product' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Outstanding qty' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'SKU' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Still owed' })).not.toBeInTheDocument();
  });

  it('prints the price, the discount and the total, and a dash where nobody priced it', () => {
    renderLines();

    const rowA = screen.getByText('SKU-A').closest('tr') as HTMLElement;
    expect(within(rowA).getByText('RM 100.00')).toBeInTheDocument();
    expect(within(rowA).getByText('RM 15.00')).toBeInTheDocument();
    expect(within(rowA).getByText('RM 985.00')).toBeInTheDocument();

    // No stated total on line B, so the total is its parts: 2 x 10.00, no discount.
    const rowB = screen.getByText('SKU-B').closest('tr') as HTMLElement;
    expect(within(rowB).getByText('RM 20.00')).toBeInTheDocument();
    // And no discount at all reads "-", never RM 0.00, which would claim one was given.
    expect(within(rowB).getAllByText('-').length).toBeGreaterThan(0);
  });

  it('carries a totals row INSIDE the table, under the columns it sums', () => {
    renderLines();

    const foot = document.querySelector('tfoot') as HTMLElement;
    expect(foot).not.toBeNull();
    expect(within(foot).getByText('12')).toBeInTheDocument(); // 10 + 2 ordered
    expect(within(foot).getByText('4')).toBeInTheDocument(); // delivered
    expect(within(foot).getByText('8')).toBeInTheDocument(); // outstanding: 6 + 2
    expect(within(foot).getByText('RM 1,005.00')).toBeInTheDocument(); // 985 + 20
  });

  it('carries the standard pagination footer, so the line count is on the screen', () => {
    renderLines();

    expect(screen.getByText('1 - 2 of 2')).toBeInTheDocument();
  });

  /**
   * SO397450: a book re-upload closed 306 lines by absence ("no longer on the uploaded
   * book"). Each read `1,500 ordered / 0 delivered / 1,500 outstanding / Completed`, and the
   * footer summed 39,008 outstanding on an order that is done. A closed line has nothing
   * outstanding by definition, and `qty_delivered` is NOT back-filled to make the
   * subtraction come out - what shipped before the book dropped the line is unknown.
   */
  it('reads 0 outstanding on a CLOSED line, however little was delivered', () => {
    renderLines({
      lines: [
        {
          id: 'l-closed', sku: 'CB6633', product_name: 'Closed board', qty_ordered: 1500,
          qty_delivered: 0, outstanding_qty: 0, uom: 'PCS', warehouse_code: 'BRW-BB',
          line_status: 'closed', required_date: '2026-08-15',
        },
        ...TWO_LINES,
      ],
      line_count: 3,
      open_line_count: 2,
    });

    const closed = screen.getByText('CB6633').closest('tr') as HTMLElement;
    expect(within(closed).getByText('1,500')).toBeInTheDocument();
    // Delivered stays 0: inventing a delivery to balance the row would be worse than the
    // figure being unknown.
    expect(within(closed).getAllByText('0').length).toBeGreaterThan(0);

    // ... and the footer sums the two OPEN lines only, not 1,508.
    const foot = document.querySelector('tfoot') as HTMLElement;
    expect(within(foot).getByText('8')).toBeInTheDocument();
    expect(within(foot).queryByText('1,508')).not.toBeInTheDocument();
  });

  it('recomputes Outstanding qty live while a quantity is being typed', () => {
    // A row that states 10 ordered, 4 delivered and 6 outstanding must not keep saying 6
    // while the quantity box reads 20. Two figures contradicting each other on one row is
    // read as a broken screen, not as an unsaved edit.
    renderLines();
    clickEdit();
    openTab('Lines');

    // In an edit session the Product cell is a select, so the row is found by one of its
    // own inputs rather than by the SKU text, which is no longer a plain span.
    const rowA = () =>
      screen.getByLabelText('Unit price on SKU-A').closest('tr') as HTMLElement;
    expect(within(rowA()).getByText('6')).toBeInTheDocument();

    fireEvent.change(within(rowA()).getByDisplayValue('10'), { target: { value: '20' } });

    expect(within(rowA()).getByText('16')).toBeInTheDocument();
    // ... and the footer moves with it: 16 + 2.
    const foot = document.querySelector('tfoot') as HTMLElement;
    expect(within(foot).getByText('18')).toBeInTheDocument();
  });

  it('sends an edited price and discount with the line', async () => {
    renderLines();
    clickEdit();
    openTab('Lines');

    fireEvent.change(screen.getByLabelText('Unit price on SKU-A'), {
      target: { value: '88.50' },
    });
    fireEvent.change(screen.getByLabelText('Discount on SKU-A'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    expect(body.lines[0]).toMatchObject({
      id: 'l-a',
      unit_price: '88.50',
      // Cleared, which is an explicit null - not "leave the stored figure alone".
      discount: null,
    });
    // `line_total` is what the source document charged, so it is never written back.
    expect(body.lines[0]).not.toHaveProperty('line_total');
  });
});

describe('SalesOrderDetail - the order type round trip', () => {
  /**
   * The view rendered `demand_class` while the edit form seeded from `order_type`, which is
   * NULL on 96% of this book - and `handleSave` then refused an empty order type, so most
   * orders could not be header-edited at all.
   */
  it('seeds the select from the same value the pill shows, and saves it as the class', async () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    // Scoped to the Order type FIELD's own wrapper, not a bare `getByText('Project')` -
    // the Order card also carries its own "Project" field (PLAN-so-project-label.md) whose
    // label reads the same word as this pill's project-class VALUE, and an unscoped query
    // cannot tell a field's name from a value.
    const orderTypeField = screen
      .getByText('Order type', { selector: 'span.text-xs.text-muted-foreground' })
      .closest('div') as HTMLElement;
    expect(within(orderTypeField).getByText('Project')).toBeInTheDocument();
    clickEdit();
    expect(screen.getByRole('combobox', { name: 'Order type' })).toHaveTextContent('Project');

    fireEvent.click(screen.getByRole('combobox', { name: 'Order type' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Retail' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    expect(body).toMatchObject({ demand_class: 'retail' });
    // `order_type` is a different column and this screen no longer writes it.
    expect(body).not.toHaveProperty('order_type');
  });

  it('saves an unclassified order rather than refusing it', async () => {
    // 96% of the book. Refusing the save on an empty class made those orders un-editable,
    // and an empty class means "leave the stored classification alone", not "clear it".
    useSalesOrder.mockReturnValue({
      data: so({ demand_class: null }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    clickEdit();
    expect(screen.getByRole('combobox', { name: 'Order type' })).toHaveTextContent(
      'Unclassified',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    expect(screen.queryByText('Select an order type.')).not.toBeInTheDocument();
    expect(updateSalesOrderMutateAsync.mock.calls[0][0].data).toMatchObject({
      demand_class: null,
    });
  });

  it('sends a corrected order date', async () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    fireEvent.change(screen.getByLabelText('Order date'), { target: { value: '2026-05-04' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    expect(updateSalesOrderMutateAsync.mock.calls[0][0].data).toMatchObject({
      order_date: '2026-05-04',
    });
  });
});

describe('SalesOrderDetail - the note', () => {
  it('shows the customer an absorbed order could not be matched to', () => {
    // 470 of the client's debtor codes are not in the CRM. The order is still absorbed, and
    // this is the only place the name survives - without it the order is anonymous.
    useSalesOrder.mockReturnValue({
      data: so({
        source: 'history',
        customer_name: '',
        internal_note: 'A COMPANY NOT IN THE CRM / 300-NOSUCH',
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    expect(screen.getByText('A COMPANY NOT IN THE CRM / 300-NOSUCH')).toBeInTheDocument();
  });

  it('renders a paragraph break as an actual line break, not a run-on sentence', () => {
    // The backend now sends plain text with `\n` line breaks (AutoCount's RTF push is
    // stripped at ingest) - `whitespace-pre-wrap` is what turns that into visible lines
    // instead of collapsing them into one, the way a bare `<p>` would; `break-words`
    // is the repo convention alongside it, so a long unbroken token (a URL, a run-on
    // reference number) cannot overflow the card at 375px.
    useSalesOrder.mockReturnValue({
      data: so({ internal_note: 'LINE ONE\nLINE TWO' }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    const note = screen.getByText(
      (_, element) => element?.tagName === 'P' && element.textContent === 'LINE ONE\nLINE TWO'
    );
    expect(note).toBeInTheDocument();
    expect(note).toHaveClass('whitespace-pre-wrap');
  });
});

describe('SalesOrderDetail - the agent', () => {
  it("shows the agent's code and the person it is annotated to", () => {
    useSalesOrder.mockReturnValue({
      data: so({ sales_agent_id: 'agent-jeremy', sales_agent_code: 'JR001', sales_agent_label: 'JEREMY' }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    expect(screen.getByText('JR001')).toBeInTheDocument();
    expect(screen.getByText('· JEREMY')).toBeInTheDocument();
  });

  it('reads as "-" when no order line-up names an agent', () => {
    useSalesOrder.mockReturnValue({
      data: so({ sales_agent_id: null, sales_agent_code: null, sales_agent_label: null }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    const orderCard = screen.getByRole('region', { name: 'Order' });
    const agentValue = screen.getByText('Agent').closest('div');
    expect(within(agentValue as HTMLElement).getByText('-')).toBeInTheDocument();
    expect(orderCard).toContainElement(agentValue);
  });

  it('names the order inquiries raised against it, and links to them', () => {
    // The business sees sales orders and order inquiries and nothing between them, so this
    // is where the order says what purchasing has been told to do about it.
    useSalesOrder.mockReturnValue({
      data: so({
        order_inquiries: [
          {
            inquiry_no: 'OI-000007',
            state: 'raised',
            raised_at: '2026-07-18T09:00:00',
            raised_by_name: 'CONTACT AH',
            rows_total: 3,
            rows_placed: 2,
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    const orderCard = screen.getByRole('region', { name: 'Order' });
    const link = within(orderCard).getByRole('link', { name: 'OI-000007' });
    expect(link).toHaveAttribute(
      'href',
      '/project-sales/order-inquiries?query=SO-2026%2F07-0042',
    );
    expect(link.getAttribute('title')).toContain('2/3 placed');
    // WHO raised it and WHEN, ON the header rather than in a tooltip (AC-H2). 09:00 UTC
    // is 5:00 pm in Malaysia: rendering the naive stamp as local time is the defect.
    expect(within(orderCard).getByText(/CONTACT AH/)).toBeInTheDocument();
    expect(within(orderCard).getByText(/18\/07\/2026, 5:00 pm/)).toBeInTheDocument();
  });

  it('says the inquiry names nobody rather than leaving the header half-written', () => {
    useSalesOrder.mockReturnValue({
      data: so({
        order_inquiries: [
          {
            inquiry_no: 'OI-000008',
            state: 'raised',
            raised_at: null,
            raised_by_name: null,
            rows_total: 1,
            rows_placed: 0,
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    const orderCard = screen.getByRole('region', { name: 'Order' });
    expect(within(orderCard).getByText(/Not recorded/)).toBeInTheDocument();
  });

  it('shows the Order inquiries field even when there are none - never hidden', () => {
    useSalesOrder.mockReturnValue({
      data: so({ order_inquiries: [] }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    const value = screen.getByText('Order inquiries').closest('div');
    expect(within(value as HTMLElement).getByText('-')).toBeInTheDocument();
  });
});

describe('SalesOrderDetail - view and edit are the same layout', () => {
  function record() {
    return so({
      sales_agent_id: 'agent-jeremy',
      sales_agent_code: 'JR001',
      sales_agent_label: 'JEREMY',
    });
  }

  it('has no Edit entry point while the record is still loading or missing', () => {
    useSalesOrder.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    renderDetail();
    expect(screen.queryByRole('button', { name: 'Sales order options' })).not.toBeInTheDocument();
  });

  it('swaps the header values for inputs in place, in the same field order, on Edit', () => {
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    // Field labels only, across all three cards in order. A pill is a VALUE and happens to
    // be a `span.text-xs` too, so it is excluded by its slot rather than by being kept a
    // different size than every other pill.
    const labelOrder = () =>
      ['Order', 'Customer', 'Totals'].flatMap((name) =>
        Array.from(
          screen
            .getByRole('region', { name })
            .querySelectorAll('label, span.text-xs:not([data-slot="badge"])'),
        ).map((el) => el.textContent),
      );

    const before = labelOrder();
    clickEdit();
    const after = labelOrder();

    // Same labels, in the same order - editing swaps a value for an input, nothing moves.
    expect(after).toEqual(before);

    // The six editable fields are now real inputs, preloaded with the stored values.
    expect(screen.getByRole('combobox', { name: 'Customer' })).toHaveTextContent(
      'Rowenda Kitchen Sdn Bhd',
    );
    expect(screen.getByRole('combobox', { name: 'Order type' })).toHaveTextContent('Project');
    expect(screen.getByRole('combobox', { name: 'Priority' })).toHaveTextContent('Normal');
    expect(screen.getByRole('combobox', { name: 'Agent' })).toHaveTextContent('JR001 · JEREMY');
    expect(screen.getByLabelText('Order date')).toHaveValue('2026-07-16');
    expect(screen.getByLabelText('Delivery date')).toHaveValue('2026-08-30');

    // Save / Cancel replace the pager and the way out; nothing else changed shape.
    expect(screen.getByRole('button', { name: 'Save sales order' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sales order options' })).not.toBeInTheDocument();
  });

  /**
   * AC-S4-7. The Planned chip is read-only metadata about the whole record, so it lives in the
   * header beside Status - not in a tab body - and there is nothing to change about it in an
   * edit session. View and edit therefore show the SAME chip, which is the layout rule this
   * describe block exists for.
   */
  it('shows the same Planned chip beside Status in view and in edit', () => {
    useSalesOrder.mockReturnValue({
      data: so({ planned_lines: 2, plannable_lines: 3 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByText('Partly 2/3')).toBeInTheDocument();

    clickEdit();

    expect(screen.getByText('Partly 2/3')).toBeInTheDocument();
    // Read-only: editing swaps a value for an input, and this one has no input to swap to.
    expect(screen.queryByRole('combobox', { name: 'Planned' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Planned')).not.toBeInTheDocument();
  });

  it('reads Planned when every plannable line is decided, and a dash when there is none', () => {
    useSalesOrder.mockReturnValue({
      data: so({ planned_lines: 3, plannable_lines: 3 }),
      isLoading: false,
      isError: false,
    });
    const planned = renderDetail();
    expect(screen.getByText('Planned')).toBeInTheDocument();
    planned.unmount();

    useSalesOrder.mockReturnValue({
      data: so({ planned_lines: 0, plannable_lines: 0 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    expect(screen.queryByText('Planned')).not.toBeInTheDocument();
    expect(screen.queryByText('Not planned')).not.toBeInTheDocument();
  });

  it('Cancel discards the session and returns to the read values, unsaved', () => {
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    expect(screen.getByRole('button', { name: 'Save sales order' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('button', { name: 'Save sales order' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sales order options' })).toBeInTheDocument();
    expect(updateSalesOrderMutateAsync).not.toHaveBeenCalled();
  });

  it('`?edit=1` opens the edit session on arrival, the same entry the list Pencil uses', () => {
    searchParams = new URLSearchParams('edit=1');
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.getByRole('button', { name: 'Save sales order' })).toBeInTheDocument();
  });

  it('a header-only save (no line touched) omits `lines` from the write, so the BE leaves them alone', async () => {
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    fireEvent.change(screen.getByLabelText('Delivery date'), {
      target: { value: '2026-09-15' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    expect(updateSalesOrderMutateAsync).toHaveBeenCalledWith({
      id: 'so-1',
      data: expect.objectContaining({
        requested_delivery_date: '2026-09-15',
        sales_agent_id: 'agent-jeremy',
      }),
    });
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    expect(body).not.toHaveProperty('lines');
  });

  it('an explicit Agent clear sends `sales_agent_id: null`', async () => {
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    // `clearable` renders an explicit × once a value is selected - the agent select's own
    // stated requirement, since not every order names an agent. Scoped to the Agent combobox:
    // the Location select on the line grid is clearable too and has its own × once a line
    // carries a warehouse.
    const agentCombo = screen.getByRole('combobox', { name: 'Agent' });
    fireEvent.pointerDown(within(agentCombo).getByRole('button', { name: 'Clear selection' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    expect(updateSalesOrderMutateAsync).toHaveBeenCalledWith({
      id: 'so-1',
      data: expect.objectContaining({ sales_agent_id: null }),
    });
  });

  it('editing a line quantity sends `lines`, replacing the whole set', async () => {
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    openTab('Lines');
    const qtyInputs = screen.getAllByDisplayValue('320');
    fireEvent.change(qtyInputs[0], { target: { value: '400' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    // Every field the line already carried rides along unchanged - `id` so the BE matches
    // by id rather than SKU, and the location/date/UoM the order loaded with.
    expect(body.lines).toEqual([{
      id: 'l-1',
      sku: 'CW-BASIN-450',
      qty_ordered: 400,
      warehouse_code: 'BRW-BB',
      required_date: '2026-08-30',
      uom: 'PCS',
      unit_price: '100.00',
      discount: '15.00',
    }]);
  });

  it('the Location select is clearable', () => {
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    openTab('Lines');
    const locationCombo = screen.getByRole('combobox', { name: 'Location on CW-BASIN-450' });
    // Code-first, same as the read view's Location cell (`BRW-BB` above) - a code review
    // nit found this select's option label was the bare warehouse NAME, so the same value
    // read as a code in view and a name in edit.
    expect(locationCombo).toHaveTextContent('BRW-BB - Brickworks Batu Berendam');
    // Same clear affordance the Agent select already carries - a location, once picked,
    // must be unsettable, not just re-pickable.
    expect(within(locationCombo).getByRole('button', { name: 'Clear selection' }))
      .toBeInTheDocument();
  });

  it('editing a line\'s location, delivery date and UoM sends them with the line id', async () => {
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('combobox', { name: 'Location on CW-BASIN-450' }));
    fireEvent.click(await screen.findByRole('option', { name: 'BRW-IB - Brickworks Iskandar' }));

    fireEvent.change(screen.getByLabelText('Delivery date on CW-BASIN-450'), {
      target: { value: '2026-10-05' },
    });

    fireEvent.click(screen.getByRole('combobox', { name: 'UoM on CW-BASIN-450' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Box' }));

    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    expect(body.lines).toEqual([{
      id: 'l-1',
      sku: 'CW-BASIN-450',
      qty_ordered: 320,
      warehouse_code: 'BRW-IB',
      required_date: '2026-10-05',
      uom: 'BOX',
      unit_price: '100.00',
      discount: '15.00',
    }]);
  });

  it('the UoM select is clearable, off the units-of-measure master', async () => {
    // The line UoM: was a free-text `Input`, is now a `SearchableSelect` sourced from
    // `getSalesOrderUoms` (mocked above) - clearable, since a line's own UoM override is
    // optional and falls back to the product's default when unset.
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    openTab('Lines');
    // The options load async (`getSalesOrderUoms`) - the trigger shows the placeholder
    // until they resolve and the value's label can be matched. Re-queried each poll
    // (not a captured reference) since the resolved options swap this node out.
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'UoM on CW-BASIN-450' })).toHaveTextContent(
        'Pieces',
      ),
    );
    const uomCombo = screen.getByRole('combobox', { name: 'UoM on CW-BASIN-450' });
    expect(within(uomCombo).getByRole('button', { name: 'Clear selection' })).toBeInTheDocument();
  });

  it('clearing the UoM select sends an explicit empty override - the product default', async () => {
    // `uom: ''` is the SAME wire value the free-text `Input` this select replaced already
    // sent on clear - the BE reads an empty override as "use the product's own default"
    // (`_upsert_lines`'s `ln.uom or None`), unchanged by this swap.
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    openTab('Lines');
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'UoM on CW-BASIN-450' })).toHaveTextContent(
        'Pieces',
      ),
    );
    const uomCombo = screen.getByRole('combobox', { name: 'UoM on CW-BASIN-450' });
    fireEvent.pointerDown(within(uomCombo).getByRole('button', { name: 'Clear selection' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    expect(body.lines[0].uom).toBe('');
  });

  it('shows the planning-change banner when a save raises one, opening the board on it, and it clears on the next edit', async () => {
    // Same envelope key the SO-book upload's own preview surfaces
    // (`OutstandingUploadDialog`'s `PlanningChangeBatchCard`) - PLAN-so-book-diff
    // -replanning.md section 2.
    updateSalesOrderMutateAsync.mockResolvedValue({
      planning_change_batch: { id: 'batch-77', order_count: 1, line_count: 2 },
    });
    useSalesOrder.mockReturnValue({ data: record(), isLoading: false, isError: false });
    renderDetail();

    clickEdit();
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    expect(await screen.findByText('Planning changes raised on 2 lines')).toBeInTheDocument();
    // AC-P3-1: straight to the BOARD, on this order and this batch. There is no batch
    // page any more - the plan has one screen and one vocabulary.
    expect(screen.getByRole('link', { name: 'Plan' })).toHaveAttribute(
      'href',
      '/project-sales/fulfilment-planning?orders=SO-2026%2F07-0042&batch=batch-77',
    );

    // A fresh edit session clears the stale notice - it describes the LAST save, not this one.
    clickEdit();
    expect(screen.queryByText('Planning changes raised on 2 lines')).not.toBeInTheDocument();
  });
});

describe('SalesOrderDetail - what has already been planned about a line', () => {
  /**
   * The header names the inquiries raised on the ORDER. That cannot answer the question a
   * planner asks at the line: a confirmation covers the SUBSET of lines somebody chose
   * (PLAN-fulfilment-planning-from-autocount-so.md 13.4), so an order carrying an inquiry
   * and an active revision still holds lines neither touches.
   *
   * Both columns therefore state their own absence rather than inheriting the header's
   * answer, and "-" is what that absence looks like on this grid.
   */
  function planned(over: Partial<SalesOrderLine> = {}): SalesOrder {
    return so({
      lines: [
        {
          id: 'l-planned',
          sku: 'SKU-PLANNED',
          product_name: 'Planned line',
          qty_ordered: 10,
          qty_delivered: 0,
          uom: 'PCS',
          warehouse_code: 'BRW-BB',
          line_status: 'open',
          required_date: '2026-08-30',
          order_inquiry: { inquiry_no: 'OI-000123', state: 'placed' },
          decision_revision: 2,
          ...over,
        } as SalesOrderLine,
      ],
      line_count: 1,
    });
  }

  it('names the inquiry, its state and the revision that decided the line', () => {
    useSalesOrder.mockReturnValue({ data: planned(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).getByText('OI-000123')).toBeInTheDocument();
    // The same pill wording the order-inquiry worklist uses, so the state word cannot mean
    // two things on two screens. S5 (`PLAN-board-oi-mechanical-22sep.md`, AC-B5-1, owner's
    // pick, 22 Sep 2026): `placed` now reads "On PO/SPO", not "Linked".
    expect(within(row).getByText('On PO/SPO')).toBeInTheDocument();
    expect(within(row).getByText('Rev 2')).toBeInTheDocument();
  });

  /**
   * S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-2): the Order inquiry cell becomes a
   * link to that exact row once the backend sends `inquiry_id`/`row_id` beside `inquiry_no`.
   */
  it('AC-B6-2: links the Order inquiry cell to the exact row once inquiry_id/row_id are on the line', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        order_inquiry: {
          inquiry_no: 'OI-000123',
          state: 'placed',
          inquiry_id: 'oi-header-1',
          row_id: 'oi-row-9',
        },
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    const link = within(row).getByText('OI-000123').closest('a');
    expect(link).not.toBeNull();
    expect(link).toHaveAttribute(
      'href',
      '/project-sales/order-inquiries/oi-header-1?row=oi-row-9',
    );
  });

  it('AC-B6-2: stays plain text - no link - while inquiry_id/row_id are not yet on the line', () => {
    useSalesOrder.mockReturnValue({ data: planned(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    const reference = within(row).getByText('OI-000123');
    expect(reference.closest('a')).toBeNull();
  });

  it('sits Linked to immediately after Outstanding qty in the default column order (R4, AC-K1)', () => {
    useSalesOrder.mockReturnValue({ data: planned(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    const outstandingIndex = headers.indexOf('Outstanding qty');
    expect(outstandingIndex).toBeGreaterThan(-1);
    expect(headers[outstandingIndex + 1]).toBe('Linked to');
  });

  // R5 (AC-L4): the "Linked to" cell reads a figure BUTTON now, but the two absence states
  // it carried before this stay exactly as worded.
  it('reads a dash when no inquiry row exists at all, and offers no button', () => {
    useSalesOrder.mockReturnValue({
      data: planned({ linked_to: null }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).getAllByText('-').length).toBeGreaterThanOrEqual(1);
    expect(
      screen.queryByRole('button', { name: 'Linked to SKU-PLANNED' }),
    ).not.toBeInTheDocument();
  });

  it('reads Not linked, not a dash, on a line whose inquiry row holds no link', () => {
    useSalesOrder.mockReturnValue({
      data: planned({ linked_to: [] }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).getByText('Not linked')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Linked to SKU-PLANNED' }),
    ).not.toBeInTheDocument();
  });

  it('opens Linked to · <code> when a line with links is pressed', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        linked_to: [
          { kind: 'spo', document: 'SPO-2026/08-0061', qty: '10', location: 'BRW' },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');
    fireEvent.click(screen.getByRole('button', { name: 'Linked to SKU-PLANNED' }));

    expect(screen.getByText('Linked to · SKU-PLANNED')).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText('SPO-2026/08-0061')).toBeInTheDocument();
  });

  it('prints a dash on a line nothing has been raised or decided on', () => {
    useSalesOrder.mockReturnValue({
      data: planned({ order_inquiry: null, decision_revision: null }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).queryByText('OI-000123')).not.toBeInTheDocument();
    expect(within(row).queryByText(/^Rev /)).not.toBeInTheDocument();
    // One dash for each of the two columns, beside whatever other empty cells the row has.
    expect(within(row).getAllByText('-').length).toBeGreaterThanOrEqual(2);
  });

  it('offers both columns to the Columns menu, so a planner who never plans can drop them', async () => {
    useSalesOrder.mockReturnValue({ data: planned(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    // Radix opens its menu on POINTER-down; a plain click leaves the trigger closed, which
    // reads in a failure message as a menu that has no items rather than one never opened.
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Columns' }), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse',
    });
    expect(
      await screen.findByRole('menuitemcheckbox', { name: 'Order inquiry' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('menuitemcheckbox', { name: 'Decision' })).toBeInTheDocument();
  });

  /**
   * AC-D4: the SECONDARY surface for the board's two compositions. The board is where the
   * decision is taken; this is where somebody looking at the order alone can read it, in the
   * SAME words - `supplyVocabulary.describe`, imported, never restated.
   */
  it('states what was suggested and what was decided, in the board\'s words', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        supply_proposed: [
          { kind: 'reserve', qty: '10', source_location: 'BRW', rung: 'pool' },
        ],
        supply_decided: [{ kind: 'buy', qty: '10', source_location: null, rung: 'buy' }],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).getByText('BRW 10 (BRW)')).toBeInTheDocument();
    expect(within(row).getByText('Buy 10')).toBeInTheDocument();
  });

  it('says Not recorded for a decided line whose revision froze no proposal', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        supply_proposed: null,
        supply_decided: [{ kind: 'buy', qty: '10', source_location: null, rung: 'buy' }],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    // Not "-": the revision predates the field, which is a different statement from "the
    // engine suggested nothing".
    expect(within(row).getByText('Not recorded')).toBeInTheDocument();
  });

  it('offers the two new columns to the Columns menu as well', async () => {
    useSalesOrder.mockReturnValue({ data: planned(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Columns' }), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse',
    });
    expect(
      await screen.findByRole('menuitemcheckbox', { name: 'Suggested' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('menuitemcheckbox', { name: 'Decided' })).toBeInTheDocument();
  });

  /**
   * D10 (captain, 3 Sep): a decision SAVED on the planning board but not yet confirmed
   * reads here too, until Confirm replaces it with the frozen `supply_decided`/
   * `decision_revision`. Before this the Lines tab answered "-"/"-"/"-" for a line the
   * captain had just saved, which read as the save having done nothing.
   */
  it('shows a saved decision as its composition, a Saved badge and who saved it', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        decision_revision: null,
        supply_saved: [{ kind: 'buy', qty: '3', source_location: null, rung: null }],
        saved_by: 'Leena',
        saved_at: '2026-09-03T02:30:00Z',
        saved_stale: false,
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).getByText('Buy 3')).toBeInTheDocument();
    // Two "Saved"s on the row by design: the Decided column's badge, and the Decision
    // column's own word - the same reading a saved-but-unconfirmed line gets on the board.
    expect(within(row).getAllByText('Saved')).toHaveLength(2);
    expect(within(row).getByTestId('saved-decision-badge-l-planned').textContent).toBe(
      'Saved',
    );
    const decision = within(row).getByText('Saved', { selector: 'span.text-sm' });
    expect(decision.title).toContain('Saved by Leena');
  });

  it('reads "Suggestion changed" on a saved decision the engine has since re-proposed', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        decision_revision: null,
        supply_saved: [{ kind: 'buy', qty: '3', source_location: null, rung: null }],
        saved_by: 'Leena',
        saved_at: '2026-09-03T02:30:00Z',
        saved_stale: true,
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    // The Decided column's own badge reads "Suggestion changed", never "Saved" - the
    // Decision column beside it still names the saver, unrelated to this warning.
    expect(within(row).getByTestId('saved-decision-badge-l-planned').textContent).toBe(
      'Suggestion changed',
    );
  });

  it('a confirmed line still reads Rev N, with no Saved badge', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        supply_decided: [{ kind: 'buy', qty: '10', source_location: null, rung: 'buy' }],
        supply_saved: null,
        saved_by: null,
        saved_stale: false,
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).getByText('Rev 2')).toBeInTheDocument();
    expect(within(row).queryByText('Saved')).not.toBeInTheDocument();
  });

  /**
   * D12 (#573, captain 3 Sep): a saved draft keeps the engine's suggestion at save time,
   * so the Suggested column shows it here the same way the board's list view does ("BRW 3
   * (BRW)") until Confirm freezes a revision. Before this the column read `supply_proposed`
   * off the confirmed revision alone, so a line saved but not yet confirmed read "-" beside
   * a live composition.
   */
  it('shows a saved draft\'s suggested composition too, before any revision is confirmed', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        decision_revision: null,
        supply_proposed: [{ kind: 'reserve', qty: '3', source_location: 'BRW', rung: 'pool' }],
        supply_decided: null,
        supply_saved: [{ kind: 'reserve', qty: '3', source_location: 'BRW', rung: 'pool' }],
        saved_by: 'Leena',
        saved_at: '2026-09-03T02:30:00Z',
        saved_stale: false,
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).getAllByText('BRW 3 (BRW)')).toHaveLength(2);
  });

  it('reads "-", not "Not recorded", for a saved line with no suggestion at all', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        decision_revision: null,
        supply_proposed: null,
        supply_decided: null,
        supply_saved: [{ kind: 'buy', qty: '3', source_location: null, rung: null }],
        saved_by: 'Leena',
        saved_at: '2026-09-03T02:30:00Z',
        saved_stale: false,
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    // The Decided column already reads the saved "Buy 3"; the Suggested column, with
    // nothing recorded for it, reads a plain absence rather than "Not recorded" - that
    // word is reserved for a CONFIRMED revision frozen before the proposal was recorded.
    expect(within(row).getAllByText('-').length).toBeGreaterThanOrEqual(1);
    expect(within(row).queryByText('Not recorded')).not.toBeInTheDocument();
  });

  /**
   * Slice D: an applied cancellation leaves the fulfilment board (a closed line has no
   * cell), so `BoardChangeTable`'s own "Where it went" list is unreachable there. The line
   * is still ON this order, so this screen reads the same fact off `planning_change`
   * instead of the plain, static `saved_stale` badge - which otherwise reads "Suggestion
   * changed" for a line the book has actually CLOSED, not merely re-suggested.
   */
  it('opens the What-changed dialog with where a cancelled, applied line went, instead of the plain "Suggestion changed" badge', () => {
    useSalesOrder.mockReturnValue({
      data: planned({
        line_status: 'cancelled',
        supply_saved: [{ kind: 'buy', qty: '3', source_location: null, rung: null }],
        saved_stale: true,
        planning_change: {
          id: 'pcr-so1-l-planned',
          kind: 'cancelled',
          applied_state: 'applied',
          result: {
            executed_reallocations: ['Reallocate 202607-S0080 3 to pool'],
            released_documents: [],
          },
        },
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('SKU-PLANNED').closest('tr') as HTMLElement;
    expect(within(row).queryByText('Suggestion changed')).not.toBeInTheDocument();

    const icon = within(row).getByTestId('board-change-icon-pcr-so1-l-planned');
    fireEvent.click(icon);

    const dialog = screen.getByTestId('board-change-dialog');
    expect(dialog).toHaveTextContent('Cancelled');
    expect(dialog).toHaveTextContent('Reallocate 202607-S0080 3 to pool');
  });
});

/**
 * Browser pass 4, findings 4 and 5 - two links to the same SPO line, and the date on them.
 *
 * A line CAN be linked twice to one SPO line: the SPO covers it in two goes, and each link
 * carries its own quantity. The key was kind + document + line label, so the second row
 * collided with the first and React warned in the console.
 */
describe('SalesOrderDetail - two links to the same SPO line', () => {
  /** One planned line carrying two links to the SAME SPO line - `planned` lives in another
   *  block, so the line is stated here rather than reached for across it. */
  const twoLinks = () =>
    so({
      lines: [
        {
          id: 'l-planned',
          sku: 'SKU-PLANNED',
          product_name: 'Planned line',
          qty_ordered: 10,
          qty_delivered: 0,
          uom: 'PCS',
          warehouse_code: 'BRW-BB',
          line_status: 'open',
          required_date: '2026-08-30',
          linked_to: [
            {
              kind: 'spo',
              document: 'SPO-2026/08-0061',
              line_label: 'L4',
              qty: '10',
              location: 'BRW',
              expected_date: '2026-09-14',
            },
            {
              kind: 'spo',
              document: 'SPO-2026/08-0061',
              line_label: 'L4',
              qty: '5',
              location: 'BRW',
              expected_date: '2026-09-14',
            },
          ],
        } as unknown as SalesOrderLine,
      ],
      line_count: 1,
    });

  // R5: the inline row is gone (AC-L4) - both links now render as rows in the "Linked to"
  // lightbox the summed figure opens, still each a distinct row (the duplicate-key risk
  // this describe block exists for is `SoLineLinksBody`'s `getRowId`, which carries the
  // INDEX for exactly this reason).
  it('opens both links in the lightbox without a duplicate-key warning', () => {
    const warn = vi.spyOn(console, 'error').mockImplementation(() => {});
    useSalesOrder.mockReturnValue({ data: twoLinks(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');
    fireEvent.click(screen.getByRole('button', { name: 'Linked to SKU-PLANNED' }));

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('Linked to · SKU-PLANNED')).toBeInTheDocument();
    // Each link keeps its OWN quantity - the SPO covered this line in two goes.
    expect(dialog.getByText('10')).toBeInTheDocument();
    expect(dialog.getByText('5')).toBeInTheDocument();
    expect(dialog.getAllByText('SPO-2026/08-0061 L4')).toHaveLength(2);
    expect(
      warn.mock.calls.some((call) => String(call[0]).includes('same key')),
    ).toBe(false);
    warn.mockRestore();
  });

  it('states when the goods are due (AC-G7)', () => {
    useSalesOrder.mockReturnValue({ data: twoLinks(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');
    fireEvent.click(screen.getByRole('button', { name: 'Linked to SKU-PLANNED' }));

    expect(within(screen.getByRole('dialog')).getAllByText('14/09/2026')).toHaveLength(2);
  });
});

/**
 * Prod 500, 17 Sep: S5's synthetic `derived`-kind spo entry (a row's own PO link's open SPO
 * allocation for the same product) now carries the same shape as a real link. The FE marks
 * it "via PO" - never a stored link - the same wording the order-inquiry worklist uses.
 */
describe('SalesOrderDetail - a derived SPO link on a PO-linked line', () => {
  const derivedLine = () =>
    so({
      lines: [
        {
          id: 'l-planned',
          sku: 'SKU-PLANNED',
          product_name: 'Planned line',
          qty_ordered: 10,
          qty_delivered: 0,
          uom: 'PCS',
          warehouse_code: 'BRW-BB',
          line_status: 'open',
          required_date: '2026-08-30',
          linked_to: [
            { kind: 'po', document: '202607-S0105', qty: '8', location: 'BRW' },
            {
              kind: 'spo',
              document: 'ZZT-SPO-abc12345',
              qty: '5',
              location: 'BRW',
              derived: true,
            },
          ],
        } as unknown as SalesOrderLine,
      ],
      line_count: 1,
    });

  it('marks the derived SPO document "via PO", and leaves the real PO link alone', () => {
    useSalesOrder.mockReturnValue({ data: derivedLine(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');
    fireEvent.click(screen.getByRole('button', { name: 'Linked to SKU-PLANNED' }));

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('ZZT-SPO-abc12345')).toBeInTheDocument();
    expect(dialog.getByText('via PO')).toBeInTheDocument();
    expect(dialog.getByText('202607-S0105').closest('tr')).not.toHaveTextContent('via PO');
  });
});

describe('SalesOrderDetail - removing a line', () => {
  /**
   * The backend already refuses a removal that would orphan a project sales order or a
   * purchase order claim (409 `SO_LINE_LINKED_TO_PROJECT` / `SO_LINE_LINKED_TO_CLAIM`) - the
   * mutation's own error toast covers that, so nothing here re-asserts a 409 path; these
   * cover the FE half: the control removes immediately with no confirmation, the omission on
   * Save, and the two ways out (a guard, and Cancel) that must never lose or silently drop a
   * line.
   */
  const TWO_LINES: SalesOrderLine[] = [
    {
      id: 'l-1', sku: 'CW-BASIN-450', product_name: 'Ceramic Wash Basin 450mm',
      qty_ordered: 320, qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB',
      line_status: 'open', required_date: '2026-08-30', unit_price: '100.00',
      discount: '15.00', line_total: '31985.00',
    },
    {
      id: 'l-2', sku: 'TAP-CHR-12', product_name: 'Chrome pillar tap',
      qty_ordered: 45, qty_delivered: 0, uom: 'PCS', warehouse_code: 'KL-01',
      line_status: 'open', required_date: '2026-09-04', unit_price: '20.00',
      discount: null, line_total: null,
    },
  ];

  function renderTwoLines() {
    useSalesOrder.mockReturnValue({
      data: so({ lines: TWO_LINES, line_count: 2, open_line_count: 2 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
  }

  // The row is found the same way the price/discount tests above find it: by one of its own
  // inputs, since the Product cell is a select in an edit session and its text is no longer
  // the bare SKU.
  const rowFor = (sku: string) =>
    screen.getByLabelText(`Unit price on ${sku}`).closest('tr') as HTMLElement;

  it('shows no remove control outside an edit session', () => {
    renderTwoLines();
    openTab('Lines');

    expect(screen.queryByRole('button', { name: 'Remove line' })).not.toBeInTheDocument();
  });

  it('removes the row immediately, with no dialog, and the totals follow', () => {
    renderTwoLines();
    clickEdit();
    openTab('Lines');

    fireEvent.click(within(rowFor('TAP-CHR-12')).getByRole('button', { name: 'Remove line' }));

    // No confirmation - nothing is written until Save, and Cancel restores the row.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Unit price on TAP-CHR-12')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Unit price on CW-BASIN-450')).toBeInTheDocument();
    // 320 + 45 = 365 before; 320 after the removed line drops out of the footer sum (both
    // Qty ordered and Outstanding qty read 320 here, since nothing has been delivered).
    const foot = document.querySelector('tfoot') as HTMLElement;
    expect(within(foot).getAllByText('320').length).toBeGreaterThan(0);
    expect(within(foot).queryByText('365')).not.toBeInTheDocument();
  });

  it('saves the remaining lines only, so the BE deletes the omitted one', async () => {
    renderTwoLines();
    clickEdit();
    openTab('Lines');

    fireEvent.click(within(rowFor('TAP-CHR-12')).getByRole('button', { name: 'Remove line' }));
    expect(screen.queryByLabelText('Unit price on TAP-CHR-12')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await screen.findByRole('button', { name: 'Sales order options' });
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    // A removal changes the LINE COUNT, so `lines` is always sent - never omitted the way a
    // header-only save omits it.
    expect(body.lines).toHaveLength(1);
    expect(body.lines.some((l: { id: string }) => l.id === 'l-2')).toBe(false);
    expect(body.lines[0]).toMatchObject({ id: 'l-1', sku: 'CW-BASIN-450' });
  });

  it('refuses to remove the last line', () => {
    // The default fixture carries exactly one line.
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();
    clickEdit();
    openTab('Lines');

    fireEvent.click(
      within(rowFor('CW-BASIN-450')).getByRole('button', { name: 'Remove line' }),
    );

    expect(screen.getByText('An order needs at least one line.')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // Nothing removed - the only line is still on the grid.
    expect(screen.getByLabelText('Unit price on CW-BASIN-450')).toBeInTheDocument();
  });

  it('keeps the session open and the row visible again after Cancel', () => {
    renderTwoLines();
    clickEdit();
    openTab('Lines');

    fireEvent.click(within(rowFor('TAP-CHR-12')).getByRole('button', { name: 'Remove line' }));
    expect(screen.queryByLabelText('Unit price on TAP-CHR-12')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    // Cancel discarded the whole session, including the removal - the read view shows both
    // lines exactly as loaded.
    expect(screen.getByRole('button', { name: 'Sales order options' })).toBeInTheDocument();
    openTab('Lines');
    expect(screen.getByText('TAP-CHR-12')).toBeInTheDocument();
    expect(screen.getByText('CW-BASIN-450')).toBeInTheDocument();
    expect(updateSalesOrderMutateAsync).not.toHaveBeenCalled();
  });

  // R2-S5c (`PLAN-scm-change-management-one-engine.md`, Slice A review round 2): a
  // removal or a qty-to-zero edit leaves the CORE line `line_status: 'cancelled'` rather
  // than deleting it (R-S5/R-B1), so it can still arrive on this same order's next load,
  // inside an edit session, sitting beside lines that are still open. It must render
  // read-only - not another line the planner can edit or remove again.
  it('renders a cancelled line read-only, with no remove control, during an edit session', () => {
    const THREE_LINES: SalesOrderLine[] = [
      ...TWO_LINES,
      {
        id: 'l-3', sku: 'BASIN-OLD-99', product_name: 'Retired basin', qty_ordered: 72,
        qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'cancelled',
        required_date: '2026-08-01', unit_price: '50.00', discount: null, line_total: null,
      },
    ];
    useSalesOrder.mockReturnValue({
      data: so({ lines: THREE_LINES, line_count: 3, open_line_count: 2 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    clickEdit();
    openTab('Lines');

    expect(screen.queryByLabelText('Product on BASIN-OLD-99')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Unit price on BASIN-OLD-99')).not.toBeInTheDocument();
    // Only the two OPEN lines get a remove control - a cancelled line cannot be removed
    // again.
    expect(screen.getAllByRole('button', { name: 'Remove line' })).toHaveLength(2);
  });
});

// --------------------------------------------------------------------------------------- //
// Red tests for the SO400884 walk defects (captain's R4 ruling, 13 Sep browser round):
// qty 0 is accepted on an EXISTING line (Cancelled after Save), still refused on a NEW
// line, header/footer totals exclude a cancelled line, and Add line exists on the edit
// screen.
// --------------------------------------------------------------------------------------- //

describe('SalesOrderDetail - R4: qty 0 on an existing line, never on a new one', () => {
  const TWO_LINES: SalesOrderLine[] = [
    {
      id: 'l-a', sku: 'SKU-A', product_name: 'Alpha pan', qty_ordered: 10,
      qty_delivered: 4, uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open',
      required_date: '2026-08-15', unit_price: '100.00', discount: '15.00',
      line_total: '985.00',
    },
    {
      id: 'l-b', sku: 'SKU-B', product_name: 'Beta basin', qty_ordered: 2,
      qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open',
      required_date: '2026-09-01', unit_price: '10.00', discount: null,
      line_total: null,
    },
  ];

  function renderTwoLines() {
    useSalesOrder.mockReturnValue({
      data: so({ lines: TWO_LINES, line_count: 2, open_line_count: 2 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
  }

  const rowFor = (sku: string) =>
    screen.getByLabelText(`Unit price on ${sku}`).closest('tr') as HTMLElement;

  // R4: "the client validation 'quantity above zero' applies to create and to a new line
  // only" - today `handleSave` (SalesOrderDetail.tsx ~1241-1242) refuses EVERY line whose
  // qty_ordered is not > 0, existing lines included, so this is the genuine red: typing 0
  // on an EXISTING line must save, not raise the banner below.
  it('accepts qty 0 on an existing line and saves', async () => {
    renderTwoLines();
    clickEdit();
    openTab('Lines');

    fireEvent.change(within(rowFor('SKU-A')).getByDisplayValue('10'), {
      target: { value: '0' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await waitFor(() => expect(updateSalesOrderMutateAsync).toHaveBeenCalledTimes(1));
    expect(
      screen.queryByText('Every line needs a product and a quantity above zero.'),
    ).not.toBeInTheDocument();
    const body = updateSalesOrderMutateAsync.mock.calls[0][0].data;
    expect(body.lines.find((l: { id: string }) => l.id === 'l-a')).toMatchObject({
      id: 'l-a',
      qty_ordered: 0,
    });
  });

  // Add line's shape (a new row with product / qty / date, the create modal's row control
  // reused) is confirmed - owner said keep.
  it('still refuses qty 0 on a new line', async () => {
    renderTwoLines();
    clickEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: /Add line/i }));
    const newRowQty = screen.getAllByRole('spinbutton').at(-1) as HTMLElement;
    fireEvent.change(newRowQty, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save sales order' }));

    await waitFor(() =>
      expect(
        screen.getByText('Every line needs a product and a quantity above zero.'),
      ).toBeInTheDocument(),
    );
    expect(updateSalesOrderMutateAsync).not.toHaveBeenCalled();
  });
});

describe('SalesOrderDetail - R4: footer totals exclude a cancelled line', () => {
  // R4: "Header totals (Total, Qty ordered, Outstanding) exclude cancelled lines." The
  // Lines-tab footer already floors `outstandingTotal` for any non-'open' status
  // (SalesOrderDetail.tsx `outstandingOf`, ~line 529) - that half is measured GREEN below,
  // kept as a guard rather than dropped, so a future regression on it is caught here too.
  // `qtyOrderedTotal` (~592-601) sums every line's qty_ordered with no status check at all,
  // which is the genuine red: the cancelled line's 72 must not land in the "Qty ordered"
  // footer.
  it('excludes a cancelled line from the Qty ordered and Outstanding footer totals', () => {
    const LINES: SalesOrderLine[] = [
      {
        // 20 delivered, so Qty ordered (320) and Outstanding (300) print DIFFERENT
        // figures - otherwise both totals read 320 and `getByText('320')` matches twice.
        id: 'l-1', sku: 'CW-BASIN-450', product_name: 'Ceramic Wash Basin 450mm',
        qty_ordered: 320, qty_delivered: 20, uom: 'PCS', warehouse_code: 'BRW-BB',
        line_status: 'open', required_date: '2026-08-30',
      },
      {
        id: 'l-cancelled', sku: 'BASIN-OLD-99', product_name: 'Retired basin',
        qty_ordered: 72, qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB',
        line_status: 'cancelled', required_date: '2026-08-01', unit_price: '50.00',
        discount: null, line_total: '3600.00',
      },
    ];
    useSalesOrder.mockReturnValue({
      data: so({ lines: LINES, line_count: 2, open_line_count: 1 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const foot = document.querySelector('tfoot') as HTMLElement;
    // Qty ordered: 320 only - not 392 (320 + the cancelled line's 72).
    expect(within(foot).getByText('320')).toBeInTheDocument();
    expect(within(foot).queryByText('392')).not.toBeInTheDocument();
    // Outstanding: 300 (320 - 20 delivered) only - not 372 (300 + the cancelled line's 72).
    expect(within(foot).getByText('300')).toBeInTheDocument();
    expect(within(foot).queryByText('372')).not.toBeInTheDocument();
  });

  it('excludes a cancelled line from the Total (amount) footer', () => {
    const LINES: SalesOrderLine[] = [
      {
        id: 'l-1', sku: 'SKU-A', product_name: 'Alpha pan', qty_ordered: 10,
        qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB', line_status: 'open',
        required_date: '2026-08-15', unit_price: '100.00', discount: null,
        line_total: '1000.00',
      },
      {
        id: 'l-cancelled', sku: 'BASIN-OLD-99', product_name: 'Retired basin',
        qty_ordered: 72, qty_delivered: 0, uom: 'PCS', warehouse_code: 'BRW-BB',
        line_status: 'cancelled', required_date: '2026-08-01', unit_price: '50.00',
        discount: null, line_total: '3600.00',
      },
    ];
    useSalesOrder.mockReturnValue({
      data: so({ lines: LINES, line_count: 2, open_line_count: 1 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const foot = document.querySelector('tfoot') as HTMLElement;
    // RM 1,000.00 only - not RM 4,600.00 (1,000 + the cancelled line's stale 3,600).
    expect(within(foot).getByText('RM 1,000.00')).toBeInTheDocument();
    expect(within(foot).queryByText('RM 4,600.00')).not.toBeInTheDocument();
  });
});

describe('SalesOrderDetail - R4: Add line on the edit screen', () => {
  // Add line reuses the create modal's own row control (product / qty / date) - confirmed,
  // owner said keep.
  it('offers Add line in edit mode, appending an editable row', () => {
    useSalesOrder.mockReturnValue({ data: so(), isLoading: false, isError: false });
    renderDetail();
    clickEdit();
    openTab('Lines');

    const before = screen.getAllByRole('spinbutton').length;
    fireEvent.click(screen.getByRole('button', { name: /Add line/i }));
    const after = screen.getAllByRole('spinbutton').length;

    expect(after).toBeGreaterThan(before);
  });
});
