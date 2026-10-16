/**
 * OI-BULK-RESERVE v4 (`PLAN-oi-bulk-reserve-15oct.md`, UAC AC-BR-21..28): the cross-OI
 * Lines page decides reserve requests the way the OI Lines tab does. Test-FIRST: written
 * before any of it exists. Harness follows `OrderInquiriesClient.confirm.test.tsx` (real
 * `useListingViewPreferences` over a mocked service, services mocked rather than hooks) and
 * the reserve mocks of `OrderInquiryDetail.bulkReserve.test.tsx`.
 *
 * Rows span three order inquiries so the per-OI commit is observable:
 *   oi-1 (OI-2609-0001): CB313 (88 @ BRW, requested), CKS1050 (44, requested; only 20 in stock)
 *   oi-2 (OI-2609-0002): C-FH14 (12 @ BRW, requested)
 *   oi-3 (OI-2609-0003): RSV-ITEM (reserved, 40), NOWH (requested, request names no pool)
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MOCK_WORKLIST_ROWS,
  MOCK_WORKLIST_SUMMARY,
} from '../../_shared/__mocks__/orderInquiryWorklist';
import type { OrderInquiryWorklistRow } from '../../_shared/types/orderInquiry.types';
import type { CommitReservePayload } from '../../_shared/services/orderInquiryReserveService';

const RESERVE = 'projects.order_inquiries.reserve';
const ACK = 'projects.order_inquiries.acknowledge';
const ACTION = 'projects.order_inquiry.action';

let granted = new Set([ACTION, ACK, RESERVE]);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => granted.has(slug),
  useHasAnyPermission: (slugs: string[]) => slugs.some((slug) => granted.has(slug)),
  usePermissions: () => ({ permissions: [...granted], permissionSet: granted, isLoading: false }),
}));

const routerReplace = vi.fn();
let currentSearchParams = new URLSearchParams('');
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: (...args: unknown[]) => routerReplace(...args) }),
  usePathname: () => '/project-sales/order-inquiries',
  useSearchParams: () => currentSearchParams,
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
const prefs = vi.hoisted(() => ({
  getUserListColumnConfig: vi.fn(),
  upsertUserListColumnConfig: vi.fn(),
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: (...args: unknown[]) => prefs.getUserListColumnConfig(...args),
  upsertUserListColumnConfig: (...args: unknown[]) => prefs.upsertUserListColumnConfig(...args),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));

const listOrderInquiryWorklist = vi.fn();
const getOrderInquiryWorklistSummary = vi.fn();
vi.mock('../../_shared/services/orderInquiryService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../_shared/services/orderInquiryService')>();
  return {
    ...actual,
    listOrderInquiryWorklist: (...args: unknown[]) => listOrderInquiryWorklist(...args),
    getOrderInquiryWorklistSummary: (...args: unknown[]) => getOrderInquiryWorklistSummary(...args),
    getUnplaceAllPreview: vi.fn(async () => ({ count: 0, product_code: null, product_name: null })),
  };
});
vi.mock('../../_shared/services/orderInquiryMatrixService', () => ({
  getOrderInquiryMatrix: vi.fn(async () => ({ data: [] })),
}));
vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  // CKS1050 has only 20 at BRW in the CRM: the pen must still offer its full 44.
  getStockDetail: vi.fn(async (productId: string) => ({
    locations: [
      {
        warehouse_id: 'wh-brw',
        location: 'BRW',
        available_qty: productId === 'prod-cks' ? '20' : '200',
      },
    ],
  })),
}));

const commitSpy = vi.fn<(id: string, payload: CommitReservePayload) => Promise<unknown[]>>();
const getSummarySpy = vi.fn();
vi.mock('../../_shared/services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../_shared/services/orderInquiryReserveService')
  >();
  return {
    ...actual,
    commitOrderInquiryReserve: (id: string, payload: CommitReservePayload) => commitSpy(id, payload),
    getReserveStatusSummary: () => getSummarySpy(),
    previewParkedReserveNotices: vi.fn(async () => ({ emails: [], unresolved: [] })),
    previewParkedReserveRequests: vi.fn(async () => ({ emails: [], unresolved: [] })),
  };
});

vi.mock('../../../scm/reorder/components/OutstandingUploadDialog', () => ({
  OutstandingUploadDialog: () => null,
}));
vi.mock('@/components/upload-activity/useUploadActivity', () => ({
  useUploadActivity: () => ({
    sessions: [],
    badgeCount: 0,
    hasInFlight: false,
    refetch: vi.fn(),
    isLoading: false,
    dismissed: new Set<string>(),
  }),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), dismiss: vi.fn() },
}));
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(async () => undefined),
  getCurrentPendingAction: vi.fn(async () => ({ pending: null, last_outcome: null })),
}));

// A plain <select>, so a test can reach the Reserve filter by its options whatever its
// placeholder turns out to be.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
    id,
  }: {
    value: string;
    onChange: (next: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    id?: string;
  }) => (
    <select
      aria-label={id ?? placeholder ?? 'select'}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">{placeholder ?? ''}</option>
      {(options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

import { toast } from '@/lib/toast';
import { OrderInquiriesClient } from './OrderInquiriesClient';

function line(
  id: string,
  itemCode: string,
  so: string,
  qty: string,
  inquiryId: string,
  inquiryNo: string,
  extra: Record<string, unknown> = {},
): OrderInquiryWorklistRow {
  return {
    ...MOCK_WORKLIST_ROWS[1],
    id,
    order_inquiry_id: inquiryId,
    inquiry_no: inquiryNo,
    item_code: itemCode,
    so_number: so,
    qty,
    location: 'BRW-IB',
    product_id: `prod-${itemCode.toLowerCase().replace(/[^a-z]/g, '')}`,
    state: 'raised',
    ack_state: 'acknowledged',
    reserve_state: 'requested',
    requested_qty: qty,
    reserved_qty: '0',
    reserve_warehouse_id: 'wh-brw',
    reserve_location: 'BRW',
    ...extra,
  } as unknown as OrderInquiryWorklistRow;
}

const CB = line('row-a1', 'CB313', 'SO-A1', '88', 'oi-1', 'OI-2609-0001', { product_id: 'prod-cb' });
const CKS = line('row-a2', 'CKS1050', 'SO-A2', '44', 'oi-1', 'OI-2609-0001', { product_id: 'prod-cks' });
const CFH = line('row-b1', 'C-FH14', 'SO-B1', '12', 'oi-2', 'OI-2609-0002', { product_id: 'prod-cfh' });
const RSV = line('row-c1', 'RSV-ITEM', 'SO-C1', '40', 'oi-3', 'OI-2609-0003', {
  reserve_state: 'reserved',
  reserved_qty: '40',
  requested_qty: '0',
});
const NOWH = line('row-c2', 'NOWH', 'SO-C2', '30', 'oi-3', 'OI-2609-0003', {
  reserve_warehouse_id: null,
  reserve_location: null,
});
const ROWS = [CB, CKS, CFH, RSV, NOWH];

function envelope(rows: OrderInquiryWorklistRow[]) {
  return { data: rows, total: rows.length, page: 1, limit: 25 };
}

function renderClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OrderInquiriesClient />
    </QueryClientProvider>,
  );
}

async function ready(rows: OrderInquiryWorklistRow[] = ROWS) {
  renderClient();
  await screen.findByText(rows[0].so_number as string);
}

function rowOf(so: string): HTMLElement {
  return screen.getByText(so).closest('tr') as HTMLElement;
}

function tick(itemCode: string, so: string) {
  fireEvent.click(screen.getByLabelText(`Select ${itemCode} on ${so}`));
}

const decideButton = () => screen.getByTestId('reserve-decide-button');
const openDecideMenu = () => fireEvent.keyDown(decideButton(), { key: 'Enter' });

async function reserveAsRequested() {
  openDecideMenu();
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Reserve as requested' }));
}

function openFilters() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /filters/i }), {
    button: 0,
    ctrlKey: false,
  });
}

function openActionsMenu() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /^actions$/i }), {
    button: 0,
    ctrlKey: false,
  });
}

function reserveButton(n: number) {
  return screen.getByRole('button', { name: `Reserve (${n})` });
}

function anyToastCalledWith(message: string): boolean {
  return [toast.success, toast.warning, toast.error, toast.info].some((fn) =>
    vi.mocked(fn).mock.calls.some(([text]) => text === message),
  );
}

function cellsOfRow(el: HTMLElement): string[] {
  return within(el)
    .getAllByRole('cell')
    .map((cell) => cell.textContent?.trim() ?? '');
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  granted = new Set([ACTION, ACK, RESERVE]);
  currentSearchParams = new URLSearchParams('');
  prefs.getUserListColumnConfig.mockResolvedValue({ listing_key: 'k', config: null });
  prefs.upsertUserListColumnConfig.mockImplementation(async (key: string, payload: unknown) => ({
    listing_key: key,
    config: payload,
  }));
  listOrderInquiryWorklist.mockResolvedValue(envelope(ROWS));
  getOrderInquiryWorklistSummary.mockResolvedValue(MOCK_WORKLIST_SUMMARY);
  getSummarySpy.mockResolvedValue({
    parked: 0,
    needs_reserve: 3,
    notice_parked: 0,
    reserved: 1,
    my_parked_requests: 0,
    my_parked_notices: 4,
  });
  commitSpy.mockImplementation(async () => [{ id: 'rr-1' }]);
});

describe('AC-BR-21 / F1 the Reserve filter', () => {
  it('offers the four Documents-view labels, passes `reserve` to the list and summary, and carries it in the URL', async () => {
    await ready();
    openFilters();
    const select = (await screen.findAllByRole('combobox')).find((candidate) =>
      within(candidate).queryByRole('option', { name: /^Needs reserve/ }),
    ) as HTMLElement;
    expect(select).toBeTruthy();
    for (const label of [
      /^Parked request to reserve/,
      /^Needs reserve/,
      /^Parked reserve/,
      /^Reserved/,
    ]) {
      expect(within(select).getByRole('option', { name: label })).toBeInTheDocument();
    }

    fireEvent.change(select, { target: { value: 'needs_reserve' } });

    await waitFor(() =>
      expect(listOrderInquiryWorklist).toHaveBeenCalledWith(
        expect.objectContaining({ reserve: 'needs_reserve' }),
      ),
    );
    await waitFor(() =>
      expect(getOrderInquiryWorklistSummary).toHaveBeenCalledWith(
        expect.objectContaining({ reserve: 'needs_reserve' }),
      ),
    );
    await waitFor(() =>
      expect(
        routerReplace.mock.calls.some(([url]) => String(url).includes('reserve=needs_reserve')),
      ).toBe(true),
    );
  });

  it('reads `?reserve=` on arrival', async () => {
    currentSearchParams = new URLSearchParams('reserve=reserved');
    await ready();
    await waitFor(() =>
      expect(listOrderInquiryWorklist).toHaveBeenCalledWith(
        expect.objectContaining({ reserve: 'reserved' }),
      ),
    );
  });
});

describe('AC-BR-22 / F2 / F3 / F4 ticking and Decide (N)', () => {
  it('a reserve-grant holder without the acknowledge grant can tick lines', async () => {
    granted = new Set([ACTION, RESERVE]);
    await ready();
    const box = screen.getByLabelText('Select CB313 on SO-A1');
    expect(box).toBeEnabled();
    fireEvent.click(box);
    await waitFor(() => expect(decideButton()).toHaveTextContent('Decide (1)'));
  });

  it('Decide is absent with nothing ticked, counts only requested lines, and is disabled at 0', async () => {
    await ready();
    expect(screen.queryByTestId('reserve-decide-button')).not.toBeInTheDocument();

    tick('RSV-ITEM', 'SO-C1');
    await waitFor(() => expect(decideButton()).toHaveTextContent('Decide (0)'));
    expect(decideButton()).toBeDisabled();

    tick('CB313', 'SO-A1');
    tick('C-FH14', 'SO-B1');
    await waitFor(() => expect(decideButton()).toHaveTextContent('Decide (2)'));
    expect(decideButton()).toBeEnabled();
  });

  it('its menu is exactly Reserve as requested and Reject...', async () => {
    await ready();
    tick('CB313', 'SO-A1');
    openDecideMenu();
    const items = (await screen.findAllByRole('menuitem')).map((item) => item.textContent?.trim());
    expect(items).toEqual(['Reserve as requested', 'Reject...']);
  });

  it('without the reserve grant: no Decide, no per-line reserve controls', async () => {
    granted = new Set([ACTION, ACK]);
    await ready();
    tick('CB313', 'SO-A1');
    expect(screen.queryByTestId('reserve-decide-button')).not.toBeInTheDocument();
    const row = rowOf('SO-A1');
    expect(within(row).queryByRole('button', { name: 'Reserve' })).not.toBeInTheDocument();
    expect(within(row).queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
    expect(within(row).queryByRole('button', { name: 'Edit reserve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reserve \(\d+\)$/ })).not.toBeInTheDocument();
  });
});

describe('AC-BR-23 / F5 Reserve as requested and Reject...', () => {
  it('drafts each counted line at its request row location, clears the ticks, opens no dialog', async () => {
    await ready();
    tick('CB313', 'SO-A1');
    tick('C-FH14', 'SO-B1');
    tick('RSV-ITEM', 'SO-C1');
    await reserveAsRequested();

    expect(await screen.findByText('Reserve 88 @ BRW')).toBeInTheDocument();
    expect(screen.getByText('Reserve 12 @ BRW')).toBeInTheDocument();
    expect(within(rowOf('SO-C1')).queryByText(/^Reserve \d/)).not.toBeInTheDocument();
    expect(within(rowOf('SO-A1')).getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(reserveButton(2)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(commitSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByTestId('reserve-decide-button')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Select CB313 on SO-A1')).not.toBeChecked();
  });

  it('Undo removes a draft', async () => {
    await ready();
    tick('CB313', 'SO-A1');
    await reserveAsRequested();
    await screen.findByText('Reserve 88 @ BRW');
    fireEvent.click(within(rowOf('SO-A1')).getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(screen.queryByText('Reserve 88 @ BRW')).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /^Reserve \(\d+\)$/ })).not.toBeInTheDocument();
  });

  it('Reject... needs a reason, then drafts Reserve 0 on every counted line and clears the ticks', async () => {
    await ready();
    tick('CB313', 'SO-A1');
    tick('CKS1050', 'SO-A2');
    openDecideMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reject...' }));

    const dialog = await screen.findByRole('dialog', { name: 'Reject 2 lines' });
    expect(within(dialog).queryByText(/\bstage\b/i)).not.toBeInTheDocument();
    const save = within(dialog).getByRole('button', { name: 'Save 2 lines' });
    expect(save).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/reason/i), { target: { value: 'No stock' } });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => expect(screen.getAllByText('Reserve 0')).toHaveLength(2));
    expect(reserveButton(2)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('reserve-decide-button')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Select CB313 on SO-A1')).not.toBeChecked();
  });

  it('Cancel on Reject... drafts nothing and keeps the ticks', async () => {
    await ready();
    tick('CB313', 'SO-A1');
    openDecideMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reject...' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByText('Reserve 0')).not.toBeInTheDocument();
    expect(decideButton()).toHaveTextContent('Decide (1)');
  });

  it('a new Decide replaces an earlier draft on the same line', async () => {
    await ready();
    fireEvent.click(within(rowOf('SO-A1')).getByRole('button', { name: 'Reject' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    fireEvent.change(within(dialog).getByLabelText(/reason/i), { target: { value: 'No stock' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save 1 line' }));
    expect(await within(rowOf('SO-A1')).findByText('Reserve 0')).toBeInTheDocument();

    tick('CB313', 'SO-A1');
    await reserveAsRequested();
    expect(await screen.findByText('Reserve 88 @ BRW')).toBeInTheDocument();
    expect(screen.queryByText('Reserve 0')).not.toBeInTheDocument();
    expect(reserveButton(1)).toBeInTheDocument();
  });

  it('never prints the word Stage', async () => {
    await ready();
    tick('CB313', 'SO-A1');
    await reserveAsRequested();
    await screen.findByText('Reserve 88 @ BRW');
    expect(screen.queryByText(/\bstage/i)).not.toBeInTheDocument();
  });
});

describe('AC-BR-22 / AC-BR-24 / F6 / F7 the line controls and the pen', () => {
  it('a requested line shows tick, x and pen in that order; a reserved line shows no Amend pen', async () => {
    await ready();
    const labels = within(rowOf('SO-A1'))
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label'))
      .filter((label) => label === 'Reserve' || label === 'Reject' || label === 'Edit reserve');
    expect(labels).toEqual(['Reserve', 'Reject', 'Edit reserve']);

    const reserved = within(rowOf('SO-C1'));
    expect(reserved.queryByRole('button', { name: 'Amend reserve' })).not.toBeInTheDocument();
    expect(reserved.queryByRole('button', { name: 'Edit reserve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Amend reserve' })).not.toBeInTheDocument();
  });

  it('the line tick drafts the requested qty at its location', async () => {
    await ready();
    fireEvent.click(within(rowOf('SO-B1')).getByRole('button', { name: 'Reserve' }));
    expect(await within(rowOf('SO-B1')).findByText('Reserve 12 @ BRW')).toBeInTheDocument();
    expect(reserveButton(1)).toBeInTheDocument();
  });

  it('the line x opens Reject 1 line and drafts Reserve 0 on that line only', async () => {
    await ready();
    fireEvent.click(within(rowOf('SO-A2')).getByRole('button', { name: 'Reject' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    expect(within(dialog).getByRole('button', { name: 'Save 1 line' })).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/reason/i), { target: { value: 'Damaged' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save 1 line' }));
    expect(await within(rowOf('SO-A2')).findByText('Reserve 0')).toBeInTheDocument();
    expect(within(rowOf('SO-A1')).queryByText('Reserve 0')).not.toBeInTheDocument();
  });

  it('the pen prefills the full request even above CRM stock, with no Reason, and Save drafts it', async () => {
    await ready();
    fireEvent.click(within(rowOf('SO-A2')).getByRole('button', { name: 'Edit reserve' }));
    const form = await screen.findByRole('dialog', { name: 'CKS1050' });
    expect(within(form).getByLabelText('Reserved')).toHaveValue(44);
    expect(within(form).queryByLabelText(/reason/i)).not.toBeInTheDocument();
    expect(within(form).queryByText(/\bstage/i)).not.toBeInTheDocument();
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));

    expect(await within(rowOf('SO-A2')).findByText('Reserve 44 @ BRW')).toBeInTheDocument();
    expect(reserveButton(1)).toBeInTheDocument();
  });
});

describe('AC-BR-25 / F8 the Reserve (N) confirmation across order inquiries', () => {
  async function draftThree() {
    await ready();
    tick('CB313', 'SO-A1');
    tick('CKS1050', 'SO-A2');
    tick('C-FH14', 'SO-B1');
    await reserveAsRequested();
    await screen.findByText('Reserve 88 @ BRW');
  }

  it('opens "Reserve 3 lines on 2 order inquiries" listing each OI with its Reserve and Reject counts', async () => {
    await draftThree();
    // One of the three becomes a reject, so the second OI reads 0 / 1.
    fireEvent.click(within(rowOf('SO-B1')).getByRole('button', { name: 'Undo' }));
    fireEvent.click(within(rowOf('SO-B1')).getByRole('button', { name: 'Reject' }));
    const rejectDialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    fireEvent.change(within(rejectDialog).getByLabelText(/reason/i), { target: { value: 'No stock' } });
    fireEvent.click(within(rejectDialog).getByRole('button', { name: 'Save 1 line' }));
    await within(rowOf('SO-B1')).findByText('Reserve 0');

    fireEvent.click(reserveButton(3));
    const dialog = await screen.findByRole('dialog', {
      name: 'Reserve 3 lines on 2 order inquiries',
    });
    expect(cellsOfRow(within(dialog).getByText('OI-2609-0001').closest('tr') as HTMLElement)).toEqual([
      'OI-2609-0001',
      '2',
      '0',
      '',
    ]);
    expect(cellsOfRow(within(dialog).getByText('OI-2609-0002').closest('tr') as HTMLElement)).toEqual([
      'OI-2609-0002',
      '0',
      '1',
      '',
    ]);
    expect(within(dialog).getByLabelText(/park notice first/i)).not.toBeChecked();
    expect(within(dialog).queryByText(/notice to purchasing/i)).not.toBeInTheDocument();
  });
});

describe('AC-BR-26 / F9 confirming commits once per order inquiry', () => {
  async function openConfirm() {
    await ready();
    tick('CB313', 'SO-A1');
    tick('CKS1050', 'SO-A2');
    tick('C-FH14', 'SO-B1');
    tick('NOWH', 'SO-C2');
    await reserveAsRequested();
    await screen.findByText('Reserve 88 @ BRW');
    // C-FH14 becomes a reject with a reason.
    fireEvent.click(within(rowOf('SO-B1')).getByRole('button', { name: 'Undo' }));
    fireEvent.click(within(rowOf('SO-B1')).getByRole('button', { name: 'Reject' }));
    const rejectDialog = await screen.findByRole('dialog', { name: 'Reject 1 line' });
    fireEvent.change(within(rejectDialog).getByLabelText(/reason/i), { target: { value: 'No stock' } });
    fireEvent.click(within(rejectDialog).getByRole('button', { name: 'Save 1 line' }));
    await within(rowOf('SO-B1')).findByText('Reserve 0');
    fireEvent.click(reserveButton(4));
    return screen.findByRole('dialog', { name: 'Reserve 4 lines on 3 order inquiries' });
  }

  it('sends one reserve-commit per OI with qty, warehouse (omitted when none), reason and park_notice false', async () => {
    const dialog = await openConfirm();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reserve and send' }));

    await waitFor(() => expect(commitSpy).toHaveBeenCalledTimes(3));
    const byInquiry = Object.fromEntries(commitSpy.mock.calls.map(([id, payload]) => [id, payload]));
    expect(Object.keys(byInquiry).sort()).toEqual(['oi-1', 'oi-2', 'oi-3']);

    expect(byInquiry['oi-1'].reserves).toHaveLength(2);
    expect(byInquiry['oi-1'].reserves).toEqual(
      expect.arrayContaining([
        { row_id: 'row-a1', warehouse_id: 'wh-brw', qty_reserved: 88, reason: null },
        { row_id: 'row-a2', warehouse_id: 'wh-brw', qty_reserved: 44, reason: null },
      ]),
    );
    expect(byInquiry['oi-1'].amendments).toEqual([]);
    expect(byInquiry['oi-1'].park_notice).toBe(false);

    expect(byInquiry['oi-2'].reserves).toEqual([
      { row_id: 'row-b1', warehouse_id: 'wh-brw', qty_reserved: 0, reason: 'No stock' },
    ]);

    expect(byInquiry['oi-3'].reserves).toHaveLength(1);
    expect(byInquiry['oi-3'].reserves[0]).toMatchObject({ row_id: 'row-c2', qty_reserved: 30, reason: null });
    expect('warehouse_id' in byInquiry['oi-3'].reserves[0]).toBe(false);
  });

  it('"Park notice first" sends park_notice true on every commit', async () => {
    const dialog = await openConfirm();
    fireEvent.click(within(dialog).getByLabelText(/park notice first/i));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reserve and park notice' }));
    await waitFor(() => expect(commitSpy).toHaveBeenCalledTimes(3));
    for (const [, payload] of commitSpy.mock.calls) expect(payload.park_notice).toBe(true);
  });

  it('all saved: each OI reads Saved, the footer is only Close, drafts clear, lines refetch, the toast sums up', async () => {
    const dialog = await openConfirm();
    const listCallsBefore = listOrderInquiryWorklist.mock.calls.length;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reserve and send' }));

    await waitFor(() =>
      expect(within(dialog).getAllByText('Saved')).toHaveLength(3),
    );
    expect(within(dialog).queryByRole('button', { name: /^reserve and/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    expect(screen.queryByText('Reserve 88 @ BRW')).not.toBeInTheDocument();
    expect(screen.queryByText('Reserve 0')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reserve \(\d+\)$/ })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(listOrderInquiryWorklist.mock.calls.length).toBeGreaterThan(listCallsBefore),
    );
    expect(anyToastCalledWith('Reserved on 3 order inquiries')).toBe(true);
  });

  it('one OI failing: it shows the server message, the others read Saved, only Close remains, failed chips stay and saved ones clear', async () => {
    commitSpy.mockImplementation(async (id) => {
      if (id === 'oi-2') throw new Error('Row already answered');
      return [{ id: 'rr-1' }];
    });
    const dialog = await openConfirm();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reserve and send' }));

    await waitFor(() => expect(within(dialog).getByText('Row already answered')).toBeInTheDocument());
    const cell = (no: string) =>
      cellsOfRow(within(dialog).getByText(no).closest('tr') as HTMLElement)[3];
    expect(cell('OI-2609-0001')).toBe('Saved');
    expect(cell('OI-2609-0002')).toBe('Row already answered');
    expect(cell('OI-2609-0003')).toBe('Saved');
    expect(within(dialog).queryByRole('button', { name: /^reserve and/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // The failed OI's draft stays; the saved ones clear.
    expect(within(rowOf('SO-B1')).getByText('Reserve 0')).toBeInTheDocument();
    expect(screen.queryByText('Reserve 88 @ BRW')).not.toBeInTheDocument();
    expect(reserveButton(1)).toBeInTheDocument();
    expect(anyToastCalledWith('Reserved on 2 of 3 order inquiries; 1 failed')).toBe(true);
  });

  it('keeps at most 3 commits in flight', async () => {
    const four = [
      line('r1', 'P1', 'SO-1', '10', 'oi-1', 'OI-2609-0001'),
      line('r2', 'P2', 'SO-2', '10', 'oi-2', 'OI-2609-0002'),
      line('r3', 'P3', 'SO-3', '10', 'oi-3', 'OI-2609-0003'),
      line('r4', 'P4', 'SO-4', '10', 'oi-4', 'OI-2609-0004'),
    ];
    listOrderInquiryWorklist.mockResolvedValue(envelope(four));
    let inFlight = 0;
    let peak = 0;
    commitSpy.mockImplementation(async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 30));
      inFlight -= 1;
      return [{ id: 'rr-1' }];
    });
    await ready(four);
    for (let i = 1; i <= 4; i += 1) tick(`P${i}`, `SO-${i}`);
    await reserveAsRequested();
    await screen.findAllByText('Reserve 10 @ BRW');
    fireEvent.click(await screen.findByRole('button', { name: 'Reserve (4)' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reserve 4 lines on 4 order inquiries' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reserve and send' }));

    await waitFor(() => expect(commitSpy).toHaveBeenCalledTimes(4), { timeout: 3000 });
    await waitFor(() => expect(within(dialog).getAllByText('Saved')).toHaveLength(4), { timeout: 3000 });
    expect(peak).toBeLessThanOrEqual(3);
  });
});

describe('AC-BR-27 / F10 the Actions menu', () => {
  it('carries "Send all parked reserves to purchasing (N)" for a reserve-grant holder', async () => {
    await ready();
    await waitFor(() => expect(getSummarySpy).toHaveBeenCalled());
    openActionsMenu();
    expect(
      await screen.findByRole('menuitem', { name: 'Send all parked reserves to purchasing (4)' }),
    ).toBeInTheDocument();
  });

  it('is absent without the reserve grant', async () => {
    granted = new Set([ACTION, ACK]);
    await ready();
    openActionsMenu();
    await screen.findByRole('menuitem', { name: /Export Excel/ });
    expect(
      screen.queryByRole('menuitem', { name: /Send all parked reserves to purchasing/ }),
    ).not.toBeInTheDocument();
  });
});
