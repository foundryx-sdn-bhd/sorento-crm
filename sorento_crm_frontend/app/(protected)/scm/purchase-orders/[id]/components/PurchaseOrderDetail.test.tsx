/**
 * PurchaseOrderDetail - the supply-side twin of `SalesOrderDetail`, and held to the same
 * shape on purpose: the captain's instruction was that the two books must look very alike,
 * one buying and one selling.
 *
 * Two things this file holds. The first is the CRUD standard: every section is rendered, with
 * an explicit empty message, so a panel that is missing means something rather than reading
 * as "not loaded yet". The record is TABBED (General / Lines / Goods receipt), so "rendered"
 * means "reachable on its tab" - which is why almost every assertion below opens the tab it
 * is about first.
 *
 * The second is the wording. There is no `on_order` status in the database: being on order is
 * DERIVED, and the screen used to say it three times over (a status chip, an "On order" line
 * beside it, and an "On order: Yes/No" field). It now says it once, in the two words the
 * client reads all day - Outstanding and Completed.
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
  usePathname: () => '/scm/purchase-orders/po-1',
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

const usePurchaseOrder = vi.fn();
const updatePurchaseOrderMutateAsync = vi.fn();
vi.mock('../../../hooks/usePurchaseOrders', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  purchaseOrdersPagerQuery: {
    listQueryKey: () => ['scm-purchase-orders'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  usePurchaseOrder: (...a: unknown[]) => usePurchaseOrder(...a),
  // The header's prev/next pager reads the same list the user came from. An empty page
  // holds no record, so the pager renders nothing and these tests stay about the record.
  usePurchaseOrders: () => ({ data: { data: [], pagination: { total: 0, page: 1, limit: 25 } } }),
  useUpdatePurchaseOrder: () => ({
    mutateAsync: updatePurchaseOrderMutateAsync,
    isPending: false,
  }),
}));

vi.mock('../../../hooks/useScmOptions', () => ({
  useWarehouseOptions: () => ({
    data: [
      { value: 'BRW-BB', label: 'Brickworks Batu Berendam' },
      { value: 'WH-KL', label: 'Kuala Lumpur DC' },
    ],
    isLoading: false,
  }),
}));

// The Supplier and Product selects are SERVER-SEARCHED - `fetchOptions`, not a static array.
// Mocked as the component calls them: `(query, pageIndex) => Promise<Option[]>`.
vi.mock('../../../services/scmOptionsService', () => ({
  SELECT_PAGE_SIZE: 50,
  searchSupplierOptions: vi.fn(async () => [
    { value: 'SUP-ACME', label: 'SUP-ACME · Acme Sanitary' },
  ]),
  searchProductOptions: vi.fn(async () => [
    { value: 'CW-BASIN-450', label: 'CW-BASIN-450 · Ceramic Wash Basin 450mm' },
  ]),
}));

// `getSalesOrderUoms` is the only export this component reads off that service module: the
// UoM select's own options, which are the same `units_of_measure` rows either book needs.
vi.mock('../../../services/salesOrderService', () => ({
  getSalesOrderUoms: () =>
    Promise.resolve([
      { id: 'uom-pcs', uom_code: 'PCS', uom_name: 'Pieces' },
      { id: 'uom-box', uom_code: 'BOX', uom_name: 'Box' },
    ]),
}));

import { PurchaseOrderDetail } from './PurchaseOrderDetail';
import type { PurchaseOrder, PurchaseOrderLine } from '../../../types/scm.types';

function po(over: Partial<PurchaseOrder> = {}): PurchaseOrder {
  return {
    id: 'po-1',
    po_number: 'PO-2026/07-0009',
    supplier_code: 'SUP-ACME',
    supplier_name: 'Acme Sanitary',
    warehouse_code: 'WH-KL',
    warehouse_name: 'Kuala Lumpur DC',
    status: 'active',
    order_date: '2026-07-16',
    expected_date: '2026-07-30',
    total_qty: 320,
    line_count: 1,
    open_qty: 320,
    open_line_count: 1,
    total_amount: '31985.00',
    currency: 'MYR',
    lines: [
      {
        id: 'l-1',
        sku: 'CW-BASIN-450',
        product_name: 'Ceramic Wash Basin 450mm',
        qty_ordered: 320,
        qty_received: 0,
        uom: 'PCS',
        warehouse_code: 'WH-KL',
        line_status: 'open',
        expected_date: '2026-07-30',
        unit_price: '100.00',
        discount: '15.00',
        line_total: '31985.00',
        currency: 'MYR',
      },
    ],
    created_at: '2026-07-16T00:00:00',
    is_on_order: true,
    source: 'recommendation',
    gr_reference: null,
    ...over,
  } as PurchaseOrder;
}

function renderDetail() {
  // A real `QueryClient`, not mocked: the UoM select's own `useQuery` needs a provider to run
  // at all. `retry: false` so an unmet expectation fails fast.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PurchaseOrderDetail id="po-1" />
    </QueryClientProvider>,
  );
}

/**
 * Open one of the record's tabs.
 *
 * `mouseDown`, not `click`: Radix's tab trigger selects on mouse-down (a plain `click` event
 * in jsdom leaves the tab strip exactly where it was, which reads as a section that vanished).
 */
function openTab(name: 'General' | 'Lines' | 'Goods receipt') {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false });
}

beforeEach(() => {
  cleanup();
  usePurchaseOrder.mockReset();
  updatePurchaseOrderMutateAsync.mockReset().mockResolvedValue({});
  searchParams = new URLSearchParams();
});

describe('PurchaseOrderDetail - states', () => {
  it('shows a skeleton and the way back while loading', () => {
    usePurchaseOrder.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    const { container } = renderDetail();
    expect(container.querySelector('[data-slot="skeleton"], .animate-pulse')).toBeTruthy();
    // The way back is present even before the record is: a slow read must not trap anybody.
    expect(screen.getByText('Back to purchase orders')).toBeInTheDocument();
  });

  it('names what is missing rather than rendering an empty shell', () => {
    usePurchaseOrder.mockReturnValue({ data: null, isLoading: false, isError: true });
    renderDetail();
    expect(screen.getByText('Purchase order not found')).toBeInTheDocument();
    expect(screen.getByText('Back to purchase orders')).toBeInTheDocument();
  });

  it('carries one tab per concern of the order, General first', () => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'General',
      'Lines',
      'Goods receipt',
    ]);
    expect(screen.getByRole('tab', { name: 'General' })).toHaveAttribute(
      'data-state',
      'active',
    );
  });

  it('opens on the tab named by `?tab=`, so the pager can carry it across a Next/Prev step', () => {
    // `PurchaseOrderNavigation` pushes `${pathname}?${searchParams}` on Next/Prev - the tab
    // has to live in the URL, not component state, or stepping to a different order always
    // lands on General regardless of which tab the reader was on.
    searchParams = new URLSearchParams('tab=lines');
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.getByRole('tab', { name: 'Lines' })).toHaveAttribute('data-state', 'active');
    expect(screen.getByText('Order lines')).toBeInTheDocument();
  });

  it('groups the summary into three cards of at most two columns each', () => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();

    for (const name of ['Order', 'Supplier', 'Totals']) {
      const region = screen.getByRole('region', { name });
      expect(region).toBeInTheDocument();
      // Never a third column: the four-across grid is what made it a wall.
      expect(region.className).toContain('sm:grid-cols-2');
      expect(region.className).not.toMatch(/grid-cols-[34]/);
    }
  });

  it('renders the document number, the supplier and every summary field', () => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.getByText('PO-2026/07-0009')).toBeInTheDocument();
    // Per card, because "Supplier" is now both a card TITLE and a field label inside it.
    const fields: Record<string, string[]> = {
      Order: ['Order date', 'Delivery date', 'Source', 'Currency'],
      Supplier: ['Supplier', 'Supplier code'],
      Totals: ['Total amount', 'Total qty', 'Outstanding qty', 'Lines'],
    };
    for (const [card, labels] of Object.entries(fields)) {
      const region = screen.getByRole('region', { name: card });
      for (const label of labels) {
        expect(within(region).getByText(label)).toBeInTheDocument();
      }
    }
    expect(within(screen.getByRole('region', { name: 'Supplier' })).getByText('Acme Sanitary'))
      .toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Supplier' })).getByText('SUP-ACME'))
      .toBeInTheDocument();
    expect(screen.getByText('Reorder recommendation')).toBeInTheDocument();
  });

  it('states what the order is worth, and a dash when nobody priced it', () => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    const { unmount } = renderDetail();
    expect(
      within(screen.getByRole('region', { name: 'Totals' })).getByText('RM 31,985.00'),
    ).toBeInTheDocument();
    unmount();

    // Not RM 0.00: an order nobody priced is not an order worth nothing.
    usePurchaseOrder.mockReturnValue({
      data: po({
        total_amount: null,
        lines: [
          {
            id: 'l-1', sku: 'CW-BASIN-450', product_name: 'Basin', qty_ordered: 320,
            qty_received: 0, uom: 'PCS', line_status: 'open',
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    const amount = screen.getByText('Total amount').closest('div');
    expect(within(amount as HTMLElement).getByText('-')).toBeInTheDocument();
  });

  it('prices a USD order in USD, never in ringgit', () => {
    // The book is 8,438 lines USD against 4,186 MYR, so "RM 12.50" against a USD purchase
    // order is a wrong number stated as a fact.
    usePurchaseOrder.mockReturnValue({
      data: po({ currency: 'USD', lines: [{ ...po().lines[0], currency: 'USD' }] }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    const totals = screen.getByRole('region', { name: 'Totals' });
    expect(within(totals).getByText('USD 31,985.00')).toBeInTheDocument();
    expect(within(totals).queryByText(/^RM /)).toBeNull();
  });

  it('shows Outstanding qty even when it equals the total', () => {
    // It used to appear only when the two differed. A field that comes and goes is worse
    // than a repeated figure: the reader has to infer from its ABSENCE that nothing has
    // arrived yet.
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();

    const totals = screen.getByRole('region', { name: 'Totals' });
    expect(within(totals).getByText('Outstanding qty')).toBeInTheDocument();
    // Ordered 320, received 0 - the same figure twice, deliberately.
    expect(within(totals).getAllByText('320')).toHaveLength(2);
  });

  it('renders the empty-lines state when the PO has no lines', () => {
    usePurchaseOrder.mockReturnValue({
      data: po({ lines: [], line_count: 0 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');
    expect(screen.getByText('This purchase order has no lines.')).toBeInTheDocument();
  });
});

describe('PurchaseOrderDetail - how the status is worded', () => {
  it('says Outstanding once, and never "On order"', () => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();

    expect(screen.getAllByText('Outstanding')).toHaveLength(1);
    expect(screen.queryByText('On order')).toBeNull();
    expect(screen.queryByText(/Not on order/)).toBeNull();
    expect(screen.queryByText('Active')).toBeNull();
  });

  it('does not call a closed historical order a draft - it calls it Completed', () => {
    // An imported 2020 order is closed and fully received. "Not on order (draft)" said
    // somebody had yet to confirm a purchase that arrived six years ago.
    usePurchaseOrder.mockReturnValue({
      data: po({ status: 'closed', source: 'import', is_on_order: false }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('Imported history')).toBeInTheDocument();
    expect(screen.queryByText('Closed')).toBeNull();
  });

  it('still calls a draft a draft', () => {
    usePurchaseOrder.mockReturnValue({
      data: po({ status: 'draft_recommendation', is_on_order: false }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByText('Draft')).toBeInTheDocument();
  });
});

describe('PurchaseOrderDetail - the goods-receipt tab', () => {
  it('surfaces the goods-receipt reference once a GR exists', () => {
    usePurchaseOrder.mockReturnValue({
      data: po({ status: 'received', gr_reference: 'GR-2026/07-0003' }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Goods receipt');
    expect(screen.getByText('GR-2026/07-0003')).toBeInTheDocument();
  });

  it('does not send the reader to a list button that no longer exists', () => {
    // The empty state used to read "Create one from the purchase orders list" - and that
    // button came off the list. Pointing at a control that is not there is worse than
    // saying nothing.
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();
    openTab('Goods receipt');

    expect(screen.getByText(/Nothing received yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/from the purchase orders list/i)).toBeNull();
  });

  it('reports a part-received order as part-received', () => {
    usePurchaseOrder.mockReturnValue({
      data: po({
        lines: [{ ...po().lines[0], qty_received: 120 }],
      }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Goods receipt');

    expect(screen.getByText(/120 of 320 received/i)).toBeInTheDocument();
    expect(screen.getByText(/200 still to arrive/i)).toBeInTheDocument();
  });
});

describe('PurchaseOrderDetail - the lines grid', () => {
  const SORT_LINES: PurchaseOrderLine[] = [
    {
      id: 'l-b', sku: 'SKU-B', product_name: 'Beta basin', qty_ordered: 320, qty_received: 300,
      uom: 'PCS', line_status: 'open',
    },
    {
      id: 'l-c', sku: 'SKU-C', product_name: 'Gamma tap', qty_ordered: 45, qty_received: 0,
      uom: 'PCS', line_status: 'open',
    },
    {
      id: 'l-a', sku: 'SKU-A', product_name: 'Alpha pan', qty_ordered: 1200, qty_received: 1100,
      uom: 'PCS', line_status: 'closed',
    },
  ];

  const skuOrder = () =>
    screen
      .getAllByRole('row')
      .map((row) => row.textContent ?? '')
      .filter((text) => text.includes('SKU-'))
      .map((text) => text.match(/SKU-[A-Z]/)?.[0] ?? '');

  beforeEach(() => {
    usePurchaseOrder.mockReturnValue({
      data: po({ lines: SORT_LINES, line_count: 3 }),
      isLoading: false,
      isError: false,
    });
  });

  it('carries the money columns the order was written with', () => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('CW-BASIN-450').closest('tr') as HTMLElement;
    expect(within(row).getByText('RM 100.00')).toBeInTheDocument();
    expect(within(row).getByText('RM 15.00')).toBeInTheDocument();
    expect(within(row).getByText('RM 31,985.00')).toBeInTheDocument();
    // Outstanding is computed from the two columns beside it, so it cannot disagree: 320
    // ordered, 0 received, 320 still to come - the same figure printed twice on purpose.
    expect(within(row).getAllByText('320')).toHaveLength(2);
  });

  it('words a line the same two ways the header pill does', () => {
    renderDetail();
    openTab('Lines');

    const openRow = screen.getByText('SKU-C').closest('tr') as HTMLElement;
    const closedRow = screen.getByText('SKU-A').closest('tr') as HTMLElement;
    expect(within(openRow).getByText('Outstanding')).toBeInTheDocument();
    expect(within(closedRow).getByText('Completed')).toBeInTheDocument();
  });

  it('orders by quantity, ascending then descending, when the header is clicked', () => {
    renderDetail();
    openTab('Lines');
    expect(skuOrder()).toEqual(['SKU-B', 'SKU-C', 'SKU-A']);

    // 45 < 320 < 1200 - numeric, not the alphabetical order a formatted string would give.
    fireEvent.click(screen.getByRole('button', { name: 'Qty ordered' }));
    expect(skuOrder()).toEqual(['SKU-C', 'SKU-B', 'SKU-A']);

    fireEvent.click(screen.getByRole('button', { name: 'Qty ordered' }));
    expect(skuOrder()).toEqual(['SKU-A', 'SKU-B', 'SKU-C']);
  });

  it('orders by what has been received', () => {
    renderDetail();
    openTab('Lines');
    fireEvent.click(screen.getByRole('button', { name: 'Qty received' }));
    expect(skuOrder()).toEqual(['SKU-C', 'SKU-B', 'SKU-A']);
  });

  it('searches the lines already loaded, and says so when nothing matches', async () => {
    renderDetail();
    openTab('Lines');

    fireEvent.change(screen.getByLabelText('Search lines'), { target: { value: 'SKU-C' } });
    await waitFor(() => expect(skuOrder()).toEqual(['SKU-C']));

    fireEvent.change(screen.getByLabelText('Search lines'), { target: { value: 'nothing' } });
    await waitFor(() =>
      expect(
        screen.getByText('No line on this order matches that product.'),
      ).toBeInTheDocument(),
    );
  });

  it('totals the quantities and the money under the columns they sum', () => {
    renderDetail();
    openTab('Lines');

    const footer = document.querySelector('tfoot') as HTMLElement;
    expect(footer).toBeTruthy();
    // 320 + 45 + 1200 ordered, 300 + 0 + 1100 received. Outstanding is 20 + 45 + 0 = 65,
    // NOT 165: the 1200/1100 line is CLOSED, and a closed line has nothing still to arrive
    // however the two quantities read. `qty_received` is left at 1100 rather than being
    // back-filled to 1200 - what actually arrived is what it is.
    expect(within(footer).getByText('1,565')).toBeInTheDocument();
    expect(within(footer).getByText('1,400')).toBeInTheDocument();
    expect(within(footer).getByText('65')).toBeInTheDocument();
  });

  /**
   * The sales book's own defect, on this side of the same rule (SO397450): a book re-upload
   * closes a line by absence without knowing what arrived, so `qty_received` stays where it
   * is and `ordered - received` would report the whole quantity as still coming.
   */
  it('reads 0 outstanding on a CLOSED line, however little was received', () => {
    usePurchaseOrder.mockReturnValue({
      data: po({ lines: SORT_LINES, line_count: 3 }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const closed = screen.getByText('SKU-A').closest('tr') as HTMLElement;
    expect(within(closed).getByText('1,200')).toBeInTheDocument();
    expect(within(closed).getByText('1,100')).toBeInTheDocument();
    // Not 100: the line is closed, so nothing on it is still to arrive.
    expect(within(closed).queryByText('100')).not.toBeInTheDocument();
  });
});

describe('PurchaseOrderDetail - the book\'s own S/O linkage (AC-A1/A2/A3/A8)', () => {
  const lineWith = (extra: Record<string, unknown>) =>
    po({ lines: [{ ...po().lines[0], ...extra }] });

  it('prints the sales order the book names on the line', () => {
    usePurchaseOrder.mockReturnValue({
      data: lineWith({ book_so_number: 'SO391853', book_so_unresolved: false }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('CW-BASIN-450').closest('tr') as HTMLElement;
    expect(within(row).getByText('SO391853')).toBeInTheDocument();
  });

  it('reads a muted dash when the book names no sales order, never a guess', () => {
    usePurchaseOrder.mockReturnValue({
      data: lineWith({ book_so_number: null, book_so_unresolved: false }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('CW-BASIN-450').closest('tr') as HTMLElement;
    expect(within(row).getByText('-')).toBeInTheDocument();
    expect(within(row).queryByText('Linked, not held')).not.toBeInTheDocument();
  });

  it('distinguishes a linkage this system does not hold from no linkage at all', () => {
    // The state the owner would otherwise never see: `202607-S0082` carries a ref on all
    // 14 of its lines and resolves none of them. Collapsing this into the dash would
    // report "nothing linked" about a document the book has linked line for line.
    usePurchaseOrder.mockReturnValue({
      data: lineWith({ book_so_number: null, book_so_unresolved: true }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('CW-BASIN-450').closest('tr') as HTMLElement;
    expect(within(row).getByText('Linked, not held')).toBeInTheDocument();
    // And it is NOT the dash, or the two states would be indistinguishable.
    expect(within(row).queryByText('-')).not.toBeInTheDocument();
  });

  it('never renders the raw AutoCount ref, which is a machine key', () => {
    usePurchaseOrder.mockReturnValue({
      data: lineWith({
        book_so_number: null,
        book_so_unresolved: true,
        // Not a field the type declares, and never one the backend sends - asserted here
        // so a future "just print what we have" cannot pass unnoticed.
        from_so_line_ref: 'AED_SORENTO:45322312:45322332',
      }),
      isLoading: false,
      isError: false,
    });
    const { container } = renderDetail();
    openTab('Lines');

    expect(container.textContent).not.toContain('AED_SORENTO');
    expect(container.textContent).not.toContain('45322312');
  });

  it('has no "+N more" overflow, because a line carries ONE sales order', () => {
    usePurchaseOrder.mockReturnValue({
      data: lineWith({ book_so_number: 'SO391853', book_so_unresolved: false }),
      isLoading: false,
      isError: false,
    });
    const { container } = renderDetail();
    openTab('Lines');

    expect(container.textContent).not.toMatch(/\+\d+ more/);
  });

  it('treats an absent field as "nothing linked", never as licence to guess (AC-A4)', () => {
    usePurchaseOrder.mockReturnValue({
      data: lineWith({ book_so_number: undefined, book_so_unresolved: undefined }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    const row = screen.getByText('CW-BASIN-450').closest('tr') as HTMLElement;
    expect(within(row).getByText('-')).toBeInTheDocument();
  });

  it('does not turn the sales order into a link (a deliberate omission)', () => {
    usePurchaseOrder.mockReturnValue({
      data: lineWith({ book_so_number: 'SO391853', book_so_unresolved: false }),
      isLoading: false,
      isError: false,
    });
    renderDetail();
    openTab('Lines');

    expect(screen.queryByRole('link', { name: /SO391853/ })).not.toBeInTheDocument();
  });
});

describe('PurchaseOrderDetail - correcting the order in place', () => {
  beforeEach(() => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
  });

  it('offers Edit, and swaps the values for inputs in the SAME cards', () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    // The same three cards, in the same order, with the same labels.
    for (const name of ['Order', 'Supplier', 'Totals']) {
      expect(screen.getByRole('region', { name })).toBeInTheDocument();
    }
    expect(screen.getByLabelText('Order date')).toBeInTheDocument();
    expect(screen.getByLabelText('Delivery date')).toBeInTheDocument();
    expect(screen.getByText('Nothing is written until you press Save.')).toBeInTheDocument();
  });

  it('opens the session straight away on ?edit=1', () => {
    searchParams = new URLSearchParams('edit=1');
    renderDetail();
    expect(screen.getByRole('button', { name: 'Save purchase order' })).toBeInTheDocument();
  });

  it('writes the header alone when no line moved', async () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Order date'), { target: { value: '2026-05-04' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save purchase order' }));

    await waitFor(() => expect(updatePurchaseOrderMutateAsync).toHaveBeenCalled());
    const payload = updatePurchaseOrderMutateAsync.mock.calls[0][0];
    expect(payload.data.order_date).toBe('2026-05-04');
    expect(payload.data.supplier_code).toBe('SUP-ACME');
    // `lines` is left off entirely: sending it would have the backend re-upsert every line
    // of the order for nothing.
    expect(payload.data.lines).toBeUndefined();
  });

  it('sends every line once one of them moved, price and discount included', async () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    openTab('Lines');
    fireEvent.change(screen.getByLabelText('Unit price on CW-BASIN-450'), {
      target: { value: '88.5' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save purchase order' }));

    await waitFor(() => expect(updatePurchaseOrderMutateAsync).toHaveBeenCalled());
    const [line] = updatePurchaseOrderMutateAsync.mock.calls[0][0].data.lines;
    expect(line).toMatchObject({
      id: 'l-1',
      sku: 'CW-BASIN-450',
      qty_ordered: 320,
      unit_price: '88.5',
      // Untouched keys still ride along, carrying exactly what the order loaded with.
      discount: '15.00',
      uom: 'PCS',
      warehouse_code: 'WH-KL',
      expected_date: '2026-07-30',
    });
  });

  it('recomputes the line total from the parts once a priced line is touched', () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    openTab('Lines');

    // The stated total wins until one of the figures it was charged ON is edited.
    fireEvent.change(screen.getByLabelText('Qty ordered on CW-BASIN-450'), {
      target: { value: '10' },
    });
    const row = screen.getByLabelText('Qty ordered on CW-BASIN-450').closest('tr') as HTMLElement;
    // 10 x 100.00 - 15.00
    expect(within(row).getByText('RM 985.00')).toBeInTheDocument();
    expect(within(row).queryByText('RM 31,985.00')).toBeNull();
  });

  it('refuses a line with no quantity rather than writing it', async () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    openTab('Lines');
    fireEvent.change(screen.getByLabelText('Qty ordered on CW-BASIN-450'), {
      target: { value: '0' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save purchase order' }));

    expect(
      screen.getByText('Every line needs a product and a quantity above zero.'),
    ).toBeInTheDocument();
    expect(updatePurchaseOrderMutateAsync).not.toHaveBeenCalled();
  });

  it('S9: a negative unit price marks the price box itself, with the sentence, and writes nothing', () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    openTab('Lines');
    const box = screen.getByLabelText('Unit price on CW-BASIN-450');
    fireEvent.change(box, { target: { value: '-5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save purchase order' }));

    expect(updatePurchaseOrderMutateAsync).not.toHaveBeenCalled();
    expect(box).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a number of at least 0, or leave it blank.')).toBeInTheDocument();
  });

  it('S9: a negative discount marks the discount box itself', () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    openTab('Lines');
    const box = screen.getByLabelText('Discount on CW-BASIN-450');
    fireEvent.change(box, { target: { value: '-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save purchase order' }));

    expect(updatePurchaseOrderMutateAsync).not.toHaveBeenCalled();
    expect(box).toHaveAttribute('aria-invalid', 'true');
  });

  it('leaves the record alone on Cancel', () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Order date'), { target: { value: '2026-05-04' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(updatePurchaseOrderMutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
  });
});

describe('PurchaseOrderDetail - the Placed lightbox (R5, AC-L1/AC-L3)', () => {
  const withAllocation = () =>
    po({
      allocations: [
        {
          line_id: 'l-1',
          sku: 'CW-BASIN-450',
          warehouse_code: 'WH-KL',
          outstanding: 320,
          allocated: 60,
          free: 260,
          dedicated_to: [],
          placements: [
            {
              kind: 'spo',
              spo_number: 'CRM-SPO-2026/08-0007',
              packing_list: 'FSCU8103365',
              qty: 60,
              warehouses: [{ warehouse_code: 'BRW', qty: 60 }],
              arrival_date: '2026-09-14',
              inquiry_no: null,
              so_number: null,
              customer: null,
              agent: null,
              needed_at: null,
              location_differs: false,
            },
          ],
        },
      ],
    });

  it('has a Placed column right after Outstanding qty', () => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent);
    const outstandingIndex = headers.findIndex((h) => h === 'Outstanding qty');
    expect(headers[outstandingIndex + 1]).toBe('Placed');
  });

  it('opens Placed on · <code> when the figure is pressed', () => {
    usePurchaseOrder.mockReturnValue({ data: withAllocation(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: 'Placed on CW-BASIN-450' }));
    expect(screen.getByText('Placed on · CW-BASIN-450')).toBeInTheDocument();
    expect(screen.getByText('CRM-SPO-2026/08-0007')).toBeInTheDocument();
  });

  it('shows the figure as plain text, not a button, when nothing is placed', () => {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    expect(
      screen.queryByRole('button', { name: 'Placed on CW-BASIN-450' }),
    ).not.toBeInTheDocument();
  });

  it('no longer renders the Allocated to card', () => {
    usePurchaseOrder.mockReturnValue({ data: withAllocation(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    expect(screen.queryByText('Allocated to')).toBeNull();
  });
});

describe('PurchaseOrderDetail - the Plan card (R1, AC-H7)', () => {
  const spoPlan = {
    pulls: [
      {
        purchase_order_id: 'po-source-1',
        po_number: '202605-S0060',
        po_line_label: 'CW-BASIN-450',
        qty: 409,
      },
    ],
    covers: [
      { so_number: 'SO-2201', customer: 'Dealer A', qty: 30, warehouse: 'MWH' },
    ],
  };

  it('renders for a crm_spo order, with its own pulls and covers', () => {
    usePurchaseOrder.mockReturnValue({
      data: po({ source: 'crm', spo_plan: spoPlan }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByRole('heading', { name: 'Plan' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /202605-S0060/ })).toHaveAttribute(
      'href',
      '/scm/purchase-orders/po-source-1',
    );
    expect(screen.getByText('SO-2201')).toBeInTheDocument();
    expect(screen.getByText('Dealer A')).toBeInTheDocument();
  });

  it('does not render for an import (non-crm) order', () => {
    usePurchaseOrder.mockReturnValue({
      data: po({ source: 'import' }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.queryByRole('heading', { name: 'Plan' })).not.toBeInTheDocument();
  });

  it('empty pulls/covers state their own message rather than an empty table', () => {
    usePurchaseOrder.mockReturnValue({
      data: po({ source: 'crm', spo_plan: { pulls: [], covers: [] } }),
      isLoading: false,
      isError: false,
    });
    renderDetail();

    expect(screen.getByText('No PO pull recorded.')).toBeInTheDocument();
    expect(screen.getByText('No sales order covered.')).toBeInTheDocument();
  });
});

// AC-CANCEL-CRM (owner hand test, 13 Oct): AutoCount Cancel PO is in TransferedQty, so the PO
// line shows its own Qty cancelled, which document cancelled it and how it was matched.
describe('PurchaseOrderDetail - AutoCount Cancel PO on the lines', () => {
  function cancelledPo() {
    return po({
      lines: [
        {
          id: 'l-1', sku: 'CW-BASIN-450', product_name: 'Ceramic Wash Basin 450mm',
          qty_ordered: 320, qty_received: 300, qty_cancelled: 20, uom: 'PCS',
          warehouse_code: 'WH-KL', line_status: 'closed', expected_date: '2026-07-30',
          unit_price: '100.00', discount: '15.00', line_total: '31985.00', currency: 'MYR',
          cancels: [{ doc_no: 'XP-000077', qty: 20, match_reason: 'item' }],
        },
      ],
    });
  }

  it('states Qty cancelled right after Qty received, with the document and match on it', () => {
    usePurchaseOrder.mockReturnValue({ data: cancelledPo(), isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent ?? '');
    const received = headers.findIndex((h) => h.includes('Qty received'));
    expect(headers[received + 1]).toContain('Qty cancelled');
    const cell = screen.getByTestId('qty-cancelled-l-1');
    expect(cell).toHaveTextContent('20');
    expect(cell).toHaveAttribute('title', 'XP-000077: 20 (item)');
    // Owner, 13 Oct: the line is done (closed), so it reads Completed, not Partly cancelled.
    expect(screen.queryByText('Partly cancelled')).not.toBeInTheDocument();
  });

  it('reads Partly cancelled while a partly cancelled line is still open', () => {
    const order = cancelledPo();
    order.lines[0] = { ...order.lines[0], qty_received: 100, line_status: 'open' };
    usePurchaseOrder.mockReturnValue({ data: order, isLoading: false, isError: false });
    renderDetail();
    openTab('Lines');

    expect(screen.getByText('Partly cancelled')).toBeInTheDocument();
  });

  // AC-CONSIGN-CRM part C: the received figure on screen is net of the cancel, so an edit
  // session that recomputes outstanding from it must add the cancel back, or the cancelled
  // qty reads as still to arrive (320 ordered, 100 received + 20 cancelled = 200, not 220).
  it('keeps a cancelled qty out of outstanding while the order is being edited', () => {
    const order = cancelledPo();
    order.lines[0] = {
      ...order.lines[0], qty_received: 100, line_status: 'open', outstanding_qty: 200,
    };
    usePurchaseOrder.mockReturnValue({ data: order, isLoading: false, isError: false });
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    openTab('Lines');
    const row = screen.getByLabelText('Qty ordered on CW-BASIN-450').closest('tr') as HTMLElement;
    expect(within(row).getByText('200')).toBeInTheDocument();
    expect(within(row).queryByText('220')).toBeNull();
  });
});

describe('PurchaseOrderDetail - a blank or invalid quantity is pending, never 0 (round 2, S-E)', () => {
  const cells = (tr: HTMLElement) =>
    Array.from(tr.querySelectorAll('td')).map((td) => (td.textContent ?? '').trim());
  const footer = () =>
    Array.from((document.querySelector('tfoot') as HTMLElement).querySelectorAll('td')).map((td) =>
      (td.textContent ?? '').trim(),
    );

  function openEditLines() {
    usePurchaseOrder.mockReturnValue({ data: po(), isLoading: false, isError: false });
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    openTab('Lines');
    return screen.getByLabelText('Qty ordered on CW-BASIN-450') as HTMLInputElement;
  }

  it.each([
    ['blank', ''],
    ['negative', '-5'],
  ])('a %s qty shows - in Outstanding and in the totals that include it', (_name, typed) => {
    const box = openEditLines();
    const tr = box.closest('tr') as HTMLElement;
    const dashesBefore = footer().filter((t) => t === '-').length;
    const rowDashesBefore = cells(tr).filter((t) => t === '-').length;
    fireEvent.change(box, { target: { value: typed } });
    // Outstanding was 320 and must not become 0.
    expect(cells(tr).filter((t) => t === '-').length).toBeGreaterThan(rowDashesBefore);
    const headers = Array.from(document.querySelectorAll('thead th')).map((th) => (th.textContent ?? '').trim());
    const outstandingAt = headers.findIndex((h) => /^Outstanding qty/.test(h));
    expect(outstandingAt).toBeGreaterThan(-1);
    expect(cells(tr)[outstandingAt]).toBe('-');
    expect(footer().filter((t) => t === '-').length).toBeGreaterThanOrEqual(dashesBefore + 2);
  });

  it('Save is refused with the sentence under the qty box', () => {
    const box = openEditLines();
    fireEvent.change(box, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save purchase order' }));
    expect(updatePurchaseOrderMutateAsync).not.toHaveBeenCalled();
    expect(box).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText(/^Enter a number above 0\.$/)).toBeInTheDocument();
  });
});
