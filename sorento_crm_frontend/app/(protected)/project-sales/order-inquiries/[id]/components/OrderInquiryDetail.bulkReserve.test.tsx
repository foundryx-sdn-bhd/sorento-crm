/**
 * OI-BULK-RESERVE (`PLAN-oi-bulk-reserve-15oct.md`, UAC AC-BR-01..09): the Lines tab's
 * "Decide (N)" (Reserve as requested / Reject...) and the line-level x Reject. Harness
 * follows `OrderInquiryDetail.reserveConfirm.test.tsx`.
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
const deniedSlugs = new Set<string>();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => !deniedSlugs.has(slug),
}));
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
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), dismiss: vi.fn() },
}));

const HEADER = {
  id: 'oi-1',
  inquiry_no: 'OI-2609-0731',
  legacy_inquiry_no: null,
  raised_at: '2026-09-05T08:00:00',
  raised_by_name: 'Agent J',
  sales_order_id: 'so-1',
  project_sales_order_id: 'pso-1',
  so_number: 'SO419603',
  so_date: '2026-09-01',
  customer_name: 'APEX CONNECTION SDN BHD (PROJECT)',
  customer_code: 'CUST-1',
  project_id: null,
  project_title: null,
  agent_name: null,
  lines_total: 3,
  lines_to_confirm: 0,
  qty_total: '197',
  status: 'outstanding',
  order_type: null,
  raise_history: [],
} as unknown as OrderInquiryHeaderDetail;

function row(id: string, itemCode: string, qty: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    order_inquiry_id: 'oi-1',
    item_code: itemCode,
    qty,
    delivery_date: null,
    supplier: null,
    po_number: null,
    location: 'BRW-IB',
    product_id: `prod-${id}`,
    verb: 'ORDER',
    note: null,
    state: 'raised',
    ack_state: 'acknowledged',
    links: [],
    linked_qty: '0',
    bundled_qty: '0',
    reserved_qty: '0',
    reserve_state: 'requested',
    requested_qty: qty,
    so_line_id: `sol-${id}`,
    so_line_no: Number(id.replace(/\D/g, '') || '1'),
    ...extra,
  } as unknown as OrderInquiryWorklistRow;
}

const ROW_A = row('row-1', 'CB313', '88');
const ROW_B = row('row-2', 'CKS1050', '44');
const ROW_C = row('row-3', 'C-FH14', '40', { reserve_state: 'reserved', reserved_qty: '40' });

const OPEN_REQUEST: OrderInquiryReserveRequest = {
  id: 'rr-2',
  order_inquiry_id: 'oi-1',
  ordinal: 2,
  state: 'requested',
  requested_by: 'user-1',
  requested_by_name: 'Agent J',
  requested_at: '2026-10-14T09:00:00',
  note: null,
  reserved_by_name: null,
  reserved_at: null,
  cancelled_at: null,
  first_to_name: null,
  rows: [
    { id: 'rq-1', row_id: 'row-1', item_code: 'CB313', qty_requested: '88', warehouse_id: 'wh-brw', location: 'BRW', qty_reserved: null, reason: null },
    { id: 'rq-2', row_id: 'row-2', item_code: 'CKS1050', qty_requested: '44', warehouse_id: 'wh-brw', location: 'BRW', qty_reserved: null, reason: null },
  ],
};
const ANSWERED_REQUEST: OrderInquiryReserveRequest = {
  ...OPEN_REQUEST,
  id: 'rr-1',
  ordinal: 1,
  state: 'reserved',
  reserved_by_name: 'CS head',
  rows: [
    { id: 'rq-0', row_id: 'row-3', item_code: 'C-FH14', qty_requested: '40', warehouse_id: 'wh-brw', location: 'BRW', qty_reserved: '40', reason: null },
  ],
};

vi.mock('../../../_shared/services/orderInquiryService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../_shared/services/orderInquiryService')>();
  return {
    ...actual,
    getOrderInquiryHeader: vi.fn(async () => HEADER),
    getOrderInquiryHeaderLines: vi.fn(async () => [ROW_A, ROW_B, ROW_C]),
    getOrderInquiryHeaderRelatedDocuments: vi.fn(async () => ({ purchase_orders: [], spos: [] })),
    listOrderInquiryHeaders: vi.fn(async () => ({ data: [], total: 0, page: 1, limit: 25 })),
  };
});

const commitSpy = vi.fn<
  (id: string, payload: CommitReservePayload) => Promise<OrderInquiryReserveRequest[]>
>(async () => [{ ...OPEN_REQUEST, state: 'reserved' }]);
vi.mock('../../../_shared/services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../_shared/services/orderInquiryReserveService')
  >();
  return {
    ...actual,
    getOrderInquiryReserveRequests: vi.fn(async () => [OPEN_REQUEST, ANSWERED_REQUEST]),
    commitOrderInquiryReserve: (id: string, payload: CommitReservePayload) => commitSpy(id, payload),
    getReserveNoticeRecipients: vi.fn(async () => ({ to: { name: 'Agent J' }, cc: [] })),
  };
});

vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(async () => undefined),
  getCurrentPendingAction: vi.fn(async () => ({ pending: null, last_outcome: null })),
}));
vi.mock('../../../_shared/services/fulfilmentPlanningService', () => ({
  getStockDetail: vi.fn(async () => ({
    locations: [{ warehouse_id: 'wh-brw', location: 'BRW', available_qty: '200' }],
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

function gridRowOf(itemCode: string): HTMLElement {
  return screen
    .getAllByText(itemCode)
    .map((e) => e.closest('tr'))
    .find(Boolean) as HTMLElement;
}

function tick(itemCode: string) {
  fireEvent.click(within(gridRowOf(itemCode)).getByRole('checkbox', { name: `Select ${itemCode}` }));
}

function decideButton() {
  return screen.getByTestId('reserve-decide-button');
}

function openDecideMenu() {
  fireEvent.keyDown(decideButton(), { key: 'Enter' });
}

async function ready() {
  // The reserve icons need the reserve requests loaded, not just the lines.
  await waitFor(() => expect(within(gridRowOf('CB313')).getByLabelText('Reserve')).toBeInTheDocument());
}

beforeEach(() => {
  vi.clearAllMocks();
  deniedSlugs.clear();
});

describe('AC-BR-01 Decide (N) on the Lines tab selection strip', () => {
  it('is disabled with nothing ticked and counts only ticked lines with an open request', async () => {
    renderDetail();
    await ready();
    expect(decideButton()).toBeDisabled();

    tick('CB313');
    tick('C-FH14');
    await waitFor(() => expect(decideButton()).toHaveTextContent('Decide (1)'));
    expect(decideButton()).toBeEnabled();

    tick('CKS1050');
    await waitFor(() => expect(decideButton()).toHaveTextContent('Decide (2)'));
  });

  it('is disabled when only answered lines are ticked (AC-BR-05)', async () => {
    renderDetail();
    await ready();
    tick('C-FH14');
    await waitFor(() => expect(decideButton()).toHaveTextContent('Decide (0)'));
    expect(decideButton()).toBeDisabled();
  });

  it('is not shown without the reserve grant', async () => {
    deniedSlugs.add('projects.order_inquiries.reserve');
    renderDetail();
    await screen.findByText('CB313');
    expect(screen.queryByTestId('reserve-decide-button')).not.toBeInTheDocument();
  });
});

describe('AC-BR-02/03 Reserve as requested', () => {
  it('offers exactly Reserve as requested and Reject...', async () => {
    renderDetail();
    await ready();
    tick('CB313');
    openDecideMenu();
    const items = (await screen.findAllByRole('menuitem')).map((item) => item.textContent?.trim());
    expect(items).toEqual(['Reserve as requested', 'Reject...']);
  });

  it('stages every counted line like its tick, clears the ticks, opens no dialog', async () => {
    renderDetail();
    await ready();
    tick('CB313');
    tick('CKS1050');
    tick('C-FH14');
    openDecideMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reserve as requested' }));

    expect(await screen.findByText('Reserve 88 @ BRW')).toBeInTheDocument();
    expect(screen.getByText('Reserve 44 @ BRW')).toBeInTheDocument();
    // The answered line is left alone: still its Amend pen, no chip.
    expect(within(gridRowOf('C-FH14')).getByLabelText('Amend reserve')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^reserve \(2\)$/i })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(decideButton()).toBeDisabled());
    expect(commitSpy).not.toHaveBeenCalled();
  });

  it('replaces a line already staged', async () => {
    renderDetail();
    await ready();
    fireEvent.click(within(gridRowOf('CB313')).getByLabelText('Reject'));
    const dialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    fireEvent.change(within(dialog).getByLabelText(/reason/i), { target: { value: 'No stock' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stage 1 line' }));
    expect(await screen.findByText('Reserve 0')).toBeInTheDocument();

    tick('CB313');
    openDecideMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reserve as requested' }));
    expect(await screen.findByText('Reserve 88 @ BRW')).toBeInTheDocument();
    expect(screen.queryByText('Reserve 0')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^reserve \(1\)$/i })).toBeInTheDocument();
  });
});

describe('AC-BR-04 Reject...', () => {
  it('needs a reason, then stages Reserve 0 on every counted line and clears the ticks', async () => {
    renderDetail();
    await ready();
    tick('CB313');
    tick('CKS1050');
    openDecideMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reject...' }));

    const dialog = await screen.findByRole('dialog', { name: 'Reject 2 lines' });
    const stage = within(dialog).getByRole('button', { name: 'Stage 2 lines' });
    expect(stage).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/reason/i), { target: { value: '   ' } });
    expect(stage).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/reason/i), { target: { value: 'No stock' } });
    expect(stage).toBeEnabled();
    fireEvent.click(stage);

    await waitFor(() => expect(screen.getAllByText('Reserve 0')).toHaveLength(2));
    expect(screen.getByRole('button', { name: /^reserve \(2\)$/i })).toBeInTheDocument();
    await waitFor(() => expect(decideButton()).toBeDisabled());
  });

  it('Cancel stages nothing and keeps the ticks', async () => {
    renderDetail();
    await ready();
    tick('CB313');
    openDecideMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reject...' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Reject 1 line' })).not.toBeInTheDocument());
    expect(screen.queryByText('Reserve 0')).not.toBeInTheDocument();
    expect(decideButton()).toHaveTextContent('Decide (1)');
  });
});

describe('AC-BR-06 commit carries the bulk stages', () => {
  it('a rejected line commits Reserve 0 + reason, a reserved one its requested qty and pool', async () => {
    renderDetail();
    await ready();
    tick('CKS1050');
    openDecideMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reject...' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    fireEvent.change(within(dialog).getByLabelText(/reason/i), { target: { value: 'No stock' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stage 1 line' }));
    await screen.findByText('Reserve 0');

    tick('CB313');
    openDecideMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reserve as requested' }));
    await screen.findByText('Reserve 88 @ BRW');

    fireEvent.click(screen.getByRole('button', { name: /^reserve \(2\)$/i }));
    const confirm = await screen.findByRole('dialog', { name: /reserve 2 lines on OI-2609-0731/i });
    expect(within(confirm).getByText('No stock')).toBeInTheDocument();
    fireEvent.click(await within(confirm).findByRole('button', { name: 'Reserve and send to Agent J' }));

    await waitFor(() => expect(commitSpy).toHaveBeenCalledTimes(1));
    const [inquiryId, payload] = commitSpy.mock.calls[0];
    expect(inquiryId).toBe('oi-1');
    expect(payload.reserves).toEqual(
      expect.arrayContaining([
        { row_id: 'row-2', warehouse_id: 'wh-brw', qty_reserved: 0, reason: 'No stock' },
        { row_id: 'row-1', warehouse_id: 'wh-brw', qty_reserved: 88, reason: null },
      ]),
    );
    expect(payload.reserves).toHaveLength(2);
    expect(payload.amendments).toEqual([]);
  });
});

describe('AC-BR-09 line-level x Reject', () => {
  it('sits between the tick and the pen on a requested line only', async () => {
    renderDetail();
    await ready();
    const labels = within(gridRowOf('CB313'))
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label'))
      .filter((label) => label === 'Reserve' || label === 'Reject' || label === 'Edit reserve');
    expect(labels).toEqual(['Reserve', 'Reject', 'Edit reserve']);
    expect(within(gridRowOf('C-FH14')).queryByLabelText('Reject')).not.toBeInTheDocument();
  });

  it('opens Reject 1 line; Stage stages Reserve 0 on that line, Cancel stages nothing', async () => {
    renderDetail();
    await ready();
    fireEvent.click(within(gridRowOf('CKS1050')).getByLabelText('Reject'));
    let dialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Reject 1 line' })).not.toBeInTheDocument());
    expect(screen.queryByText('Reserve 0')).not.toBeInTheDocument();

    fireEvent.click(within(gridRowOf('CKS1050')).getByLabelText('Reject'));
    dialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    expect(within(dialog).getByRole('button', { name: 'Stage 1 line' })).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/reason/i), { target: { value: 'Damaged stock' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stage 1 line' }));

    expect(await within(gridRowOf('CKS1050')).findByText('Reserve 0')).toBeInTheDocument();
    expect(within(gridRowOf('CB313')).queryByText('Reserve 0')).not.toBeInTheDocument();
  });

  it('is not shown without the reserve grant', async () => {
    deniedSlugs.add('projects.order_inquiries.reserve');
    renderDetail();
    await screen.findByText('CB313');
    expect(within(gridRowOf('CB313')).queryByLabelText('Reject')).not.toBeInTheDocument();
  });
});
