/**
 * `PLAN-oi-header-list-detail.md`, AC-DP-05/06, security fix round S1 + UL, reviewer
 * fix round S5 (`oi-header-list-detail-acceptance-criteria.md`).
 *
 * Pinned here:
 *
 * 1. **S1 (never widen).** With nothing ticked, Confirm / Auto link send exactly
 *    `{ filter: { inquiry_id: <the page id> } }` - never a payload that could match
 *    outside this one OI. Real service functions are spied on through a PARTIAL mock
 *    of `orderInquiryService` (kept mostly real via `importOriginal`) rather than
 *    mocking the hooks, so the actual payload the hook builds is what gets asserted.
 * 2. **UL (deferred, not a dialog).** Gear > Unlink selected starts a server-deferred
 *    pending action (countdown + Cancel, AC-DP-06) instead of the retired `AlertDialog`
 *    confirm pattern - `@/services/pendingActionService` is mocked so the countdown
 *    `useDeferredBulkAction` builds is real, and Cancel is asserted to leave the ticked
 *    line ticked (AC-DP-06's own "Cancel leaves the ticked lines ticked").
 * 3. **AC-DP-05.** The Confirm label counts the ticked lines, and Confirm is disabled
 *    once nothing in scope still waits (a Completed header, nothing ticked).
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

// OI-RESEND-PURCHASING: the single-OI entry point.
const previewHandoverResendSpy = vi.fn(async () => ({
  emails: [],
  inquiry_total: 0,
  unresolved: [],
  skipped: [],
  automation_enabled: true,
}));
vi.mock('../../../_shared/services/orderInquiryHandoverResendService', () => ({
  previewHandoverResend: (ids: string[]) => previewHandoverResendSpy(ids),
  sendHandoverResend: vi.fn(),
}));

// S1 (reviewer round): `OrderInquiryDetail` reads `useSession` directly now, to gate
// the reserve-request Cancel control on the actual signed-in user, not only a
// permission flag.
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { id: 'test-current-user' } },
    status: 'authenticated',
  }),
}));

// Under jsdom nothing answers the column-preferences fetch `DataGrid` starts, so the
// Lines tab grid renders skeletons forever and no row is assertable
// (project_datagrid_jsdom_rows_mockable.md).
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));

const acknowledgeFilterSpy = vi.fn(async () => ({
  acknowledged: 0,
  results: [],
}));
const acknowledgeRowsSpy = vi.fn(async () => ({
  acknowledged: 0,
  results: [],
}));
const autoPlaceSpy = vi.fn(async () => ({ linked: 0, results: [] }));
const unacknowledgeSpy = vi.fn<
  (rowIds: unknown) => Promise<{ unacknowledged: number }>
>(async () => ({ unacknowledged: 0 }));
// Lane B (`PLAN-order-sheet-oi-reports-22sep.md`, AC-B1/AC-B5): Export Excel goes
// async (My Downloads), never a blob save - and the gear gets a "Download history" item.
const exportOrderInquiryXlsxSpy = vi.hoisted(() =>
  vi.fn(async () => ({
    id: 'dl-1',
    kind: 'order_inquiry_xlsx',
    status: 'pending',
    filename: 'OI-2609-0001.xlsx',
  })),
);

const toastSpy = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({ toast: toastSpy }));

const saveBlobAsSpy = vi.hoisted(() => vi.fn());
vi.mock('../../../_shared/services/fileDownload', () => ({
  saveBlobAs: (...args: unknown[]) => saveBlobAsSpy(...args),
  filenameFromContentDisposition: vi.fn(),
}));

const entityDownloadsButtonSpy = vi.hoisted(() => vi.fn());
vi.mock('@/components/my-downloads/EntityDownloadsButton', () => ({
  EntityDownloadsButton: (props: {
    entityType: string;
    entityId: string;
    open?: boolean;
    onOpenChange?: (open: boolean) => void;
  }) => {
    entityDownloadsButtonSpy(props);
    return props.open ? (
      <div data-testid="entity-downloads-dialog">
        Downloads for {props.entityType}:{props.entityId}
      </div>
    ) : null;
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
  lines_to_confirm: 3,
  qty_total: '30',
  status: 'outstanding',
  order_type: null,
  raise_history: [],
};

const LINKED_LINE: OrderInquiryWorklistRow = {
  id: 'row-linked',
  order_inquiry_id: 'oi-1',
  item_code: `ZZT-${'LINK'}`,
  qty: '10',
  delivery_date: null,
  supplier: null,
  po_number: null,
  location: null,
  verb: 'order',
  note: null,
  state: 'placed',
  ack_state: 'awaiting',
  links: [{ kind: 'po', document: 'PO-1', po_id: 'po-1', qty: '10' }],
} as unknown as OrderInquiryWorklistRow;

vi.mock(
  '../../../_shared/services/orderInquiryService',
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import('../../../_shared/services/orderInquiryService')
      >();
    return {
      ...actual,
      getOrderInquiryHeader: vi.fn(async () => HEADER),
      getOrderInquiryHeaderLines: vi.fn(async () => [LINKED_LINE]),
      getOrderInquiryHeaderRelatedDocuments: vi.fn(async () => ({
        purchase_orders: [],
        spos: [],
      })),
      listOrderInquiryHeaders: vi.fn(async () => ({
        data: [],
        total: 0,
        page: 1,
        limit: 25,
      })),
      exportOrderInquiryXlsx: (...args: unknown[]) =>
        exportOrderInquiryXlsxSpy(...(args as [string])),
      acknowledgeOrderInquiryRowsByFilter: (...args: unknown[]) =>
        acknowledgeFilterSpy(...(args as [unknown])),
      acknowledgeOrderInquiryRows: (...args: unknown[]) =>
        acknowledgeRowsSpy(...(args as [unknown])),
      autoPlaceOrderInquiryRows: (...args: unknown[]) =>
        autoPlaceSpy(...(args as [unknown])),
      unacknowledgeOrderInquiryRows: (...args: unknown[]) =>
        unacknowledgeSpy(...(args as [unknown])),
    };
  },
);

// UL: the deferred Unlink parks a real pending action through this service - mocked so
// the countdown `useDeferredBulkAction` builds is exercised for real, with a window long
// enough (10s) that a synchronous test never sees it lapse.
const createPendingActionSpy = vi.fn(
  async ({ entityId }: { entityId: string }) => ({
    id: `pa-${entityId}`,
    action_key: 'order_inquiry_row.unlink',
    entity_type: 'order_inquiry_row',
    entity_id: entityId,
    commit_at: new Date(Date.now() + 10_000).toISOString(),
    window_seconds: 10,
  }),
);
const cancelPendingActionSpy = vi.fn(async () => undefined);
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) =>
    createPendingActionSpy(...(args as [{ entityId: string }])),
  cancelPendingAction: (...args: unknown[]) =>
    cancelPendingActionSpy(...(args as [string])),
  getCurrentPendingAction: vi.fn(async () => ({
    pending: null,
    last_outcome: null,
  })),
}));

import { pendingEntityStore } from '@/lib/pending-entity-store';
import {
  getOrderInquiryHeader,
  getOrderInquiryHeaderLines,
} from '../../../_shared/services/orderInquiryService';
import { OrderInquiryDetail } from './OrderInquiryDetail';

const mockGetOrderInquiryHeader =
  getOrderInquiryHeader as unknown as ReturnType<typeof vi.fn>;

function renderDetail(id: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OrderInquiryDetail id={id} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  acknowledgeFilterSpy.mockClear();
  acknowledgeRowsSpy.mockClear();
  autoPlaceSpy.mockClear();
  unacknowledgeSpy.mockClear();
  createPendingActionSpy.mockClear();
  cancelPendingActionSpy.mockClear();
  exportOrderInquiryXlsxSpy.mockClear();
  saveBlobAsSpy.mockClear();
  entityDownloadsButtonSpy.mockClear();
  toastSpy.success.mockClear();
  toastSpy.error.mockClear();
  mockGetOrderInquiryHeader.mockReset();
  mockGetOrderInquiryHeader.mockResolvedValue(HEADER);
});

afterEach(() => {
  pendingEntityStore.reset();
});

describe('Confirm / Auto link never widen without a real page id (S1)', () => {
  it('with nothing ticked, Confirm sends exactly { filter: { inquiry_id: id } }', async () => {
    renderDetail('oi-1');
    const confirmButton = await screen.findByRole('button', {
      name: 'Confirm',
    });

    fireEvent.click(confirmButton);

    await waitFor(() => expect(acknowledgeFilterSpy).toHaveBeenCalledTimes(1));
    expect(acknowledgeFilterSpy.mock.calls[0][0]).toEqual({
      inquiry_id: 'oi-1',
    });
    expect(acknowledgeRowsSpy).not.toHaveBeenCalled();
  });

  it('with an empty page id, Confirm never fires an acknowledge call at all', async () => {
    renderDetail('');

    // The empty-id detail query is `enabled: Boolean(id)` = false, so the header never
    // loads and the "Could not load" state renders instead of the primary button - but
    // that is itself the guard this pins: nothing downstream may EVER reach a mutate
    // call with a widened/empty scope, whether by the button not rendering or by a
    // future change guarding the click. Both are asserted.
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /confirm/i }),
      ).not.toBeInTheDocument(),
    );
    expect(acknowledgeFilterSpy).not.toHaveBeenCalled();
    expect(acknowledgeRowsSpy).not.toHaveBeenCalled();
  });

  it('Auto link with nothing ticked sends exactly { filter: { inquiry_id: id } }', async () => {
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    fireEvent.pointerDown(
      screen.getByRole('button', { name: 'Order inquiry options' }),
      {
        button: 0,
      },
    );
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /auto link/i }),
    );

    await waitFor(() => expect(autoPlaceSpy).toHaveBeenCalledTimes(1));
    expect(autoPlaceSpy.mock.calls[0][0]).toEqual({
      filter: { inquiry_id: 'oi-1' },
    });
  });
});

describe('Confirm label / disabled follow the ticked scope (AC-DP-05, S5)', () => {
  it('ticking a line changes the label to Confirm (1)', async () => {
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    fireEvent.click(
      await screen.findByLabelText(`Select ${LINKED_LINE.item_code}`),
    );

    expect(
      await screen.findByRole('button', { name: 'Confirm (1)' }),
    ).toBeInTheDocument();
  });

  // `PLAN-oi-no-double-count-25sep.md` AC-ND-12 + G6 (owner ruling 26 Sep 2026): the
  // grid ticks sales order LINES and Confirm (N) counts lines. S2 (AC-ND-24, AC-ND-27):
  // Confirm sends the ticked line's LIVE row ids only; the server takes on the line's
  // used rows in the same call, and a cancelled row from the history fetch is never sent.
  it('ticks a line, counts it once, and Confirm sends its live rows only', async () => {
    const lineRow = (over: Partial<OrderInquiryWorklistRow>) =>
      ({
        ...LINKED_LINE,
        item_code: 'ZZT-SPLIT',
        core_line_id: 'cl-1',
        line_no: 1,
        verb: 'ORDER',
        links: [],
        ...over,
      }) as OrderInquiryWorklistRow;
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValueOnce([
      lineRow({
        id: 'row-used',
        state: 'placed',
        redirected_to_pool: true,
        ack_state: 'changed',
      }),
      lineRow({ id: 'row-a', state: 'raised', ack_state: 'changed' }),
      lineRow({ id: 'row-b', state: 'raised', ack_state: 'awaiting' }),
      lineRow({ id: 'row-old', state: 'cancelled', ack_state: 'changed' }),
    ]);
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    fireEvent.click(await screen.findByLabelText('Select ZZT-SPLIT'));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm (1)' }));

    await waitFor(() => expect(acknowledgeRowsSpy).toHaveBeenCalledTimes(1));
    const sent = (acknowledgeRowsSpy.mock.calls[0] as unknown[])[0] as string[];
    expect([...sent].sort()).toEqual(['row-a', 'row-b']);
  });

  // PR #1266 fix round W1 (owner, 26 Sep 2026: "i just realized after we click confirm,
  // at the line level can't really see it is confirmed, can we have an icon here to show
  // it is confirmed?"). Confirm already invalidates the header lines query
  // (`useOrderInquiry.ts`'s `invalidate()`), so the refetched row is what should carry
  // the mark - no page reload involved.
  it("W1: the line's check appears right after Confirm, without a reload", async () => {
    const rowAwaiting = {
      ...LINKED_LINE,
      id: 'row-w1',
      item_code: 'ZZT-W1',
      core_line_id: 'cl-w1',
      line_no: 1,
      ack_state: 'awaiting',
    } as OrderInquiryWorklistRow;
    const rowAcknowledged = {
      ...rowAwaiting,
      ack_state: 'acknowledged',
      acknowledged_by_name: 'Aisyah',
      acknowledged_at: '2026-09-26T11:20:00Z',
    } as OrderInquiryWorklistRow;
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValueOnce([rowAwaiting]);
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValueOnce([
      rowAcknowledged,
    ]);

    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });
    expect(screen.queryByTestId('line-confirmed-mark')).not.toBeInTheDocument();

    fireEvent.click(await screen.findByLabelText('Select ZZT-W1'));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm (1)' }));

    const mark = await screen.findByTestId('line-confirmed-mark');
    expect(mark.getAttribute('aria-label')).toContain('Confirmed by Aisyah');
  });

  it('is disabled on a Completed header with nothing ticked', async () => {
    mockGetOrderInquiryHeader.mockResolvedValue({
      ...HEADER,
      status: 'completed',
      lines_to_confirm: 0,
    });
    renderDetail('oi-1');

    const confirmButton = await screen.findByRole('button', {
      name: 'Confirm',
    });
    expect(confirmButton).toBeDisabled();
  });
});

/**
 * Review S2 (PR #1266 at a98e01cf; SF1 at d0d328d7f): the header must never sit
 * Outstanding on a used row that no tick can confirm (G6 rationale). A ticked line whose
 * only waiting row is a used row enables Confirm and sends that used row's id.
 */
describe('Review S2: a line whose only waiting row is used confirms from the line', () => {
  const lineRow = (over: Partial<OrderInquiryWorklistRow>) =>
    ({
      ...LINKED_LINE,
      item_code: 'ZZT-USED',
      core_line_id: 'cl-u',
      line_no: 1,
      verb: 'ORDER',
      links: [],
      ...over,
    }) as OrderInquiryWorklistRow;

  it('shape a: live rows confirmed, the used row changed - Confirm (1) sends both', async () => {
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValueOnce([
      lineRow({ id: 'row-live', state: 'placed', ack_state: 'acknowledged' }),
      lineRow({
        id: 'row-used',
        state: 'placed',
        redirected_to_pool: true,
        ack_state: 'changed',
      }),
    ]);
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    fireEvent.click(await screen.findByLabelText('Select ZZT-USED'));
    const confirm = await screen.findByRole('button', { name: 'Confirm (1)' });
    expect(confirm).not.toBeDisabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(acknowledgeRowsSpy).toHaveBeenCalledTimes(1));
    const sent = (acknowledgeRowsSpy.mock.calls[0] as unknown[])[0] as string[];
    expect([...sent].sort()).toEqual(['row-live', 'row-used']);
  });

  it('shape b: a line with only a used row - it ticks, and Confirm (1) sends that row', async () => {
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValueOnce([
      lineRow({
        id: 'row-used',
        state: 'placed',
        redirected_to_pool: true,
        ack_state: 'changed',
      }),
    ]);
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    fireEvent.click(await screen.findByLabelText('Select ZZT-USED'));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm (1)' }));

    await waitFor(() => expect(acknowledgeRowsSpy).toHaveBeenCalledTimes(1));
    expect((acknowledgeRowsSpy.mock.calls[0] as unknown[])[0]).toEqual([
      'row-used',
    ]);
    expect(acknowledgeFilterSpy).not.toHaveBeenCalled();
  });

  it('shape b: a ticked used-only line never widens Auto link to the whole OI', async () => {
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValueOnce([
      lineRow({
        id: 'row-used',
        state: 'placed',
        redirected_to_pool: true,
        ack_state: 'changed',
      }),
    ]);
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });
    fireEvent.click(await screen.findByLabelText('Select ZZT-USED'));
    await screen.findByRole('button', { name: 'Confirm (1)' });

    fireEvent.pointerDown(
      screen.getByRole('button', { name: 'Order inquiry options' }),
      {
        button: 0,
      },
    );
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /auto link/i }),
    );
    expect(autoPlaceSpy).not.toHaveBeenCalled();
  });
});

/**
 * Review S3 (PR #1266 at a98e01cf): since S2 the Lines fetch carries cancelled rows
 * (`include_history`), so the whole-OI Unconfirm must still read the LIVE rows only.
 */
describe('Review S3: whole-OI Unconfirm sends only live acknowledged rows', () => {
  it('with nothing ticked, Unconfirm skips the cancelled and the used acknowledged rows', async () => {
    const lineRow = (over: Partial<OrderInquiryWorklistRow>) =>
      ({
        ...LINKED_LINE,
        verb: 'ORDER',
        links: [],
        ...over,
      }) as OrderInquiryWorklistRow;
    vi.mocked(getOrderInquiryHeaderLines).mockResolvedValueOnce([
      lineRow({
        id: 'row-live',
        item_code: 'ZZT-A',
        core_line_id: 'cl-a',
        line_no: 1,
        ack_state: 'acknowledged',
      }),
      lineRow({
        id: 'row-old',
        item_code: 'ZZT-A',
        core_line_id: 'cl-a',
        line_no: 1,
        state: 'cancelled',
        ack_state: 'acknowledged',
      }),
      lineRow({
        id: 'row-used',
        item_code: 'ZZT-B',
        core_line_id: 'cl-b',
        line_no: 2,
        redirected_to_pool: true,
        ack_state: 'acknowledged',
      }),
    ]);
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });
    await screen.findByLabelText('Select ZZT-A');

    fireEvent.pointerDown(
      screen.getByRole('button', { name: 'Order inquiry options' }),
      {
        button: 0,
      },
    );
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /unconfirm/i }),
    );

    await waitFor(() => expect(unacknowledgeSpy).toHaveBeenCalledTimes(1));
    expect((unacknowledgeSpy.mock.calls[0] as unknown[])[0]).toEqual([
      'row-live',
    ]);
  });
});

describe('Unlink selected is a server-deferred pending action, not a confirm dialog (AC-DP-06, UL)', () => {
  it('does not open an AlertDialog - a countdown with Cancel appears instead', async () => {
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    fireEvent.click(
      await screen.findByLabelText(`Select ${LINKED_LINE.item_code}`),
    );
    fireEvent.pointerDown(
      screen.getByRole('button', { name: 'Order inquiry options' }),
      {
        button: 0,
      },
    );
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /unlink selected/i }),
    );

    // Target behaviour: no confirmation dialog at all, a deferred countdown instead.
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(await screen.findByText(/cancel/i)).toBeInTheDocument();
  });

  it('Cancel leaves the ticked line ticked (AC-DP-06)', async () => {
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    const checkbox = await screen.findByLabelText(
      `Select ${LINKED_LINE.item_code}`,
    );
    fireEvent.click(checkbox);
    expect(checkbox).toHaveAttribute('aria-checked', 'true');

    fireEvent.pointerDown(
      screen.getByRole('button', { name: 'Order inquiry options' }),
      {
        button: 0,
      },
    );
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /unlink selected/i }),
    );
    await waitFor(() =>
      expect(createPendingActionSpy).toHaveBeenCalledTimes(1),
    );

    fireEvent.click(await screen.findByRole('button', { name: /cancel/i }));

    await waitFor(() =>
      expect(cancelPendingActionSpy).toHaveBeenCalledTimes(1),
    );
    // The selection is untouched by Cancel - only a commit clears it.
    expect(checkbox).toHaveAttribute('aria-checked', 'true');
  });
});

describe('Export Excel goes async, tied to this OI (Lane B, AC-B1/AC-B5)', () => {
  function openGear() {
    fireEvent.pointerDown(
      screen.getByRole('button', { name: 'Order inquiry options' }),
      {
        button: 0,
      },
    );
  }

  it('Export Excel calls exportOrderInquiryXlsx(id) and toasts My Downloads, never saveBlobAs', async () => {
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    openGear();
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /export excel/i }),
    );

    await waitFor(() =>
      expect(exportOrderInquiryXlsxSpy).toHaveBeenCalledWith('oi-1'),
    );
    await waitFor(() => expect(toastSpy.success).toHaveBeenCalled());
    expect(toastSpy.success.mock.calls[0][0]).toMatch(/my downloads/i);
    expect(saveBlobAsSpy).not.toHaveBeenCalled();
  });

  it('disables Export Excel while the export is pending', async () => {
    let resolveExport: (v: unknown) => void = () => {};
    exportOrderInquiryXlsxSpy.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveExport = resolve;
        }),
    );
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    openGear();
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /export excel/i }),
    );

    await waitFor(() => expect(exportOrderInquiryXlsxSpy).toHaveBeenCalled());
    openGear();
    const item = await screen.findByRole('menuitem', { name: /export excel/i });
    expect(item).toHaveAttribute('aria-disabled', 'true');

    resolveExport({
      id: 'dl-1',
      kind: 'order_inquiry_xlsx',
      status: 'pending',
      filename: 'x.xlsx',
    });
    await waitFor(() => expect(toastSpy.success).toHaveBeenCalled());
  });

  it('shows an error toast when the export fails to start', async () => {
    exportOrderInquiryXlsxSpy.mockRejectedValueOnce(
      new Error('An export is already queued'),
    );
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    openGear();
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /export excel/i }),
    );

    await waitFor(() => expect(toastSpy.error).toHaveBeenCalled());
    expect(toastSpy.error.mock.calls[0][0]).toMatch(/already queued/i);
  });

  it('the gear offers "Download history", opening EntityDownloadsButton for this OI', async () => {
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });

    openGear();
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /download history/i }),
    );

    await waitFor(() =>
      expect(entityDownloadsButtonSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          entityType: 'order_inquiry',
          entityId: 'oi-1',
          open: true,
        }),
      ),
    );
    expect(
      await screen.findByTestId('entity-downloads-dialog'),
    ).toBeInTheDocument();
  });
});

describe('Send to purchasing from the detail page (OI-RESEND-PURCHASING)', () => {
  it('the options menu opens the preview for this one order inquiry', async () => {
    renderDetail('oi-1');
    await screen.findByRole('button', { name: 'Confirm' });
    fireEvent.pointerDown(
      screen.getByRole('button', { name: 'Order inquiry options' }),
      {
        button: 0,
      },
    );
    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'Send to purchasing' }),
    );
    await waitFor(() =>
      expect(previewHandoverResendSpy).toHaveBeenCalledWith(['oi-1']),
    );
    expect(
      await screen.findByRole('dialog', { name: 'Send to purchasing' }),
    ).toBeInTheDocument();
  });
});
