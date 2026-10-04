/**
 * S3 - the loading plan's header metadata moves into a General tab
 * (`PLAN-scm-ui-feedback-14sep.md`, J3, ruling R3).
 *
 * TEST-FIRST. At the time this file is written `LoadingPlanView`'s `PageHeader` still carries
 * the `Planning` badge and the `Started X - up to Y - Stock list Z` subtitle, and the tab
 * strip is `Lines | Supplier codes | Sent` with no General tab at all. Every test asserting
 * the new shape is expected to be red until S3 lands.
 *
 * Why the metadata moves (owner screenshots, 14 Sep): the header is the record's NAME, and
 * on a 375px screen the subtitle wrapped to three lines above the tabs, pushing the work off
 * the fold. The plan's status, dates and provenance are a READING, so they read as a card -
 * the same `Field` grid the proforma invoice's General tab already uses.
 *
 * Mock surface copied verbatim from `LoadingPlanView.test.tsx`, so the two files cannot
 * disagree about what the record's dependencies answer.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { LoadingPlanRecord } from '../../services/fulfilmentService';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}
if (!window.ResizeObserver) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

const push = vi.fn();
const replace = vi.fn();
// The URL is what the tab strip reads and writes (AC-B2) - a real `URLSearchParams` so
// `.get('tab')` and `.toString()` both behave, swappable per test to prove a reload lands
// back on the tab named in it.
let currentSearchParams = new URLSearchParams();
// The pager has its own tests (hooks/useListPager.test.ts).
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('next/navigation', () => ({
  usePathname: () => '/scm/loading-plan/plan-1',
  useRouter: () => ({ push, replace }),
  useSearchParams: () => currentSearchParams,
}));

vi.mock('@/lib/toast', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
    custom: vi.fn(),
  },
}));

// The gear menu, flattened: Radix opens on pointerdown through a portal, and what this suite
// asks of it is which items it offers, not how it animates.
/* eslint-disable @typescript-eslint/no-explicit-any */
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: any) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: any) => <>{children}</>,
  DropdownMenuContent: ({ children }: any) => <div data-testid="menu-content">{children}</div>,
  DropdownMenuItem: ({ children, onSelect, disabled, ...rest }: any) => (
    <button type="button" onClick={onSelect} disabled={disabled} {...rest}>
      {children}
    </button>
  ),
  DropdownMenuLabel: ({ children }: any) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuGroup: ({ children }: any) => <div>{children}</div>,
  DropdownMenuPortal: ({ children }: any) => <>{children}</>,
}));
/* eslint-enable @typescript-eslint/no-explicit-any */

// The tab has its own suite (SupplierCodesTab.test.tsx); here it only has to prove it
// mounted, on the codes tab and nowhere else.
vi.mock('./SupplierCodesTab', () => ({
  SupplierCodesTab: () => <div data-testid="supplier-codes-tab" />,
}));

// The grid is a controlled child now (R5): the record owns the typed quantities, so the stub
// exposes exactly the two props that carry them.
vi.mock('./ContainerRequestSection', () => ({
  ContainerRequestSection: ({
    supplierName,
    readOnly,
    onQtyChange,
    remarkFor,
  }: {
    supplierName: string;
    readOnly?: boolean;
    onQtyChange: (rowKey: string, qty: number) => void;
    remarkFor: (row: { row_key: string }) => string;
  }) => (
    <div data-testid="container-request-section" data-readonly={String(!!readOnly)}>
      Request section for {supplierName}
      <button type="button" data-testid="type-qty" onClick={() => onQtyChange('row-a', 4000)}>
        type
      </button>
      <button
        type="button"
        data-testid="type-engine-qty"
        onClick={() => onQtyChange('row-a', 4242)}
      >
        type the engine figure
      </button>
      {/* S2, review round 1: the SAME `remarkFor` the preview's own input writes through -
          reading it here off the row this suite's fixture always keys `row-a` is what proves
          a remark typed in the preview shows in the plan table without a save. */}
      <span data-testid="plan-table-remark">{remarkFor({ row_key: 'row-a' } as never)}</span>
    </div>
  ),
}));

const ENGINE_ROW = {
  row_key: 'row-a',
  product_id: 'prod-a',
  row_kind: 'product' as const,
  product_set_id: null,
  suggested_qty: 4242,
  engine_qty: 4242,
  cbm_per_unit: 0.5,
};

const PLAN: LoadingPlanRecord = {
  id: 'plan-1',
  supplier_id: 'sup-1',
  supplier_name: 'CHAOZHOU JINBAICHUAN SANITARY WARE CO., LTD',
  supplier_email: 'person36@example.com',
  started_at: '2026-08-27T14:02:00',
  plan_horizon_start: null,
  plan_horizon_date: '2026-09-30',
  document_kind: 'stock_list',
  document_label: 'Stock list 27/07/2026',
  statement_as_of: '2026-07-27',
  source_attachment_id: 'att-1',
  source_attachment_filename: 'plan-own-list.xlsx',
  status: 'planning',
  sent_channel: null,
  sent_at: null,
  opened_at: null,
  last_opened_at: null,
  open_count: 0,
  cancelled_at: null,
  cancelled_by: null,
  line_edits: {},
  to_request_qty: null,
  to_request_cbm: null,
};

const state = {
  plan: PLAN,
  rows: [ENGINE_ROW],
  /** Make the pre-send Save refuse, which used to leave an unhandled rejection and send anyway. */
  saveFails: false,
  /** The Supplier codes tab's badge count (S2) - empty by default in this suite, which is
   *  about the toolbar and the Lines tab, not the queue itself (`SupplierCodesTab` and
   *  `SentRequestsPanel` own their own contents; `LoadingPlanView.test.tsx` (tabs) covers
   *  the strip and the badges). */
  unmatchedCodes: [] as unknown[],
  /** The Sent tab's badge count and body (S2). */
  notices: [] as unknown[],
  /** Which supplier the legacy stock-list lookup was asked about, or null when the record
   *  read the plan's own stamped file instead (BL-3). */
  stockListFileAskedAbout: null as string | null,
  /** R9 preview page state: the sheet `useContainerRequestPreview` answers with, null when a
   *  test does not open the preview. */
  previewSheet: null as unknown,
};

const saveEdits = vi.fn();
const sendRequest = vi.fn();
const changeCutOff = vi.fn();
const refetchBuild = vi.fn();

vi.mock('../../hooks/useFulfilment', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  loadingPlanPagerQuery: {
    listQueryKey: () => ['scm-loading-plans'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useContainerRequestBuild: () => ({
    data: { plan: state.plan, rows: state.rows, supplier_id: 'sup-1' },
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: refetchBuild,
  }),
  // R9: `state.previewSheet` stays null (the steady "nothing yet" answer) except in the one
  // test that opens the preview and sets it - both keep the hook count and the component
  // happy without every other test's build needing a sheet fixture of its own.
  useContainerRequestPreview: () => ({ data: state.previewSheet, isError: false, error: null }),
  useSaveLoadingPlanEdits: () => ({
    mutate: (v: unknown, o?: { onSuccess?: () => void }) => {
      saveEdits(v);
      o?.onSuccess?.();
    },
    mutateAsync: async (v: unknown) => {
      saveEdits(v);
      if (state.saveFails) throw new Error('The quantities could not be saved.');
      return state.plan;
    },
    isPending: false,
  }),
  useUpdateLoadingPlanCutOff: () => ({ mutate: changeCutOff, isPending: false }),
  useSendContainerRequest: () => ({
    mutate: (v: unknown, o?: { onSuccess?: () => void }) => {
      sendRequest(v);
      o?.onSuccess?.();
    },
    isPending: false,
    error: null,
    reset: vi.fn(),
  }),
  useSupplierChatContacts: () => ({
    data: {
      data: [],
      total: 0,
      wechat_connected: false,
      wechat_channel_name: null,
      unavailable_reason: 'No WeChat channel is connected in the Respond.io workspace.',
    },
    isLoading: false,
  }),
  useDownloadContainerRequestDocument: () => ({ mutate: vi.fn(), isPending: false }),
  useLoadingPlanList: () => ({ data: { data: [{ id: 'plan-1' }], total: 1 } }),
  useSupplierNotices: () => ({ data: state.notices }),
  useSupplierStockListFile: (supplierId: string | null) => {
    state.stockListFileAskedAbout = supplierId;
    return {
      data: supplierId ? { attachment_id: 'att-supplier', filename: 'supplier.xlsx' } : null,
      isLoading: false,
    };
  },
}));

vi.mock('../../hooks/useSupplierCodeAliases', () => ({
  useRematchSupplierCodes: () => ({ mutate: vi.fn(), isPending: false }),
  useUnmatchedSupplierCodes: () => ({ data: state.unmatchedCodes }),
}));

import { LoadingPlanView } from './LoadingPlanView';

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LoadingPlanView planId="plan-1" />
    </QueryClientProvider>,
  );
}

/** Radix tabs open on pointerdown; a plain click is a silent no-op. */
function selectTab(name: string | RegExp) {
  const tab = screen.getByRole('tab', { name });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);
}

/** The `Plan` card on the General tab, so its labels can be read without catching the
 *  breadcrumb or the toolbar. */
function planCard(): HTMLElement {
  const title = screen.getByText('Plan');
  const card = title.closest('[data-slot="card"]');
  if (!card) throw new Error('no card around the Plan heading');
  return card as HTMLElement;
}

/** The value rendered under a label, the way `Field` stacks them. */
function fieldValue(label: string): string {
  const node = within(planCard()).getByText(label);
  const field = node.parentElement;
  if (!field) throw new Error(`label ${label} has no field wrapper`);
  return (field.textContent ?? '').replace(label, '').trim();
}

beforeEach(() => {
  vi.clearAllMocks();
  state.plan = { ...PLAN };
  state.rows = [ENGINE_ROW];
  state.saveFails = false;
  state.unmatchedCodes = [];
  state.notices = [];
  state.stockListFileAskedAbout = null;
  state.previewSheet = null;
  currentSearchParams = new URLSearchParams();
});

// --------------------------------------------------------------------------- AC-3.1

describe('LoadingPlanView - the header is the name only (AC-3.1)', () => {
  it('still titles the record with the supplier', () => {
    renderView();

    expect(
      screen.getByRole('heading', { name: /CHAOZHOU JINBAICHUAN SANITARY WARE CO\., LTD/ }),
    ).toBeTruthy();
  });

  // Scoped to the HEADER itself, on the tab that does carry these facts: asking the whole
  // screen would pass for the wrong reason the day the default tab changes, or the day
  // somebody puts the pill back on a tab body.
  it('carries no status badge and no Started / up to / Stock list line', () => {
    currentSearchParams = new URLSearchParams('tab=general');
    renderView();

    const header = screen
      .getByRole('heading', { name: /CHAOZHOU JINBAICHUAN SANITARY WARE CO\., LTD/ })
      .closest('[data-slot="toolbar"]') as HTMLElement;
    expect(header).not.toBeNull();

    const inHeader = within(header);
    expect(
      inHeader.queryAllByText('Planning').filter((el) => el.dataset.slot === 'badge'),
    ).toHaveLength(0);
    expect(inHeader.queryByText(/Started 27\/08\/2026/)).toBeNull();
    expect(inHeader.queryByText(/up to 30\/09\/2026/)).toBeNull();
    expect(inHeader.queryByText(/Stock list 27\/07\/2026/)).toBeNull();
    // The facts themselves are on the General tab, which is open: the assertion above is
    // about WHERE they are, so it has to be able to find them somewhere.
    expect(screen.getByTestId('plan-general')).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- AC-3.2

describe('LoadingPlanView - General leads the tab strip (AC-3.2)', () => {
  it('reads General | Lines | Supplier codes (n) | Sent, in that order', () => {
    state.unmatchedCodes = [{ item_code: 'A' }, { item_code: 'B' }];
    state.notices = [{ id: 'n-1', notice_type: 'container_request', channel: 'email' }];
    renderView();

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'General',
      'Lines',
      'Supplier codes (2)',
      'Sent (1)',
    ]);
  });

  it('still opens on Lines when the URL names no tab', () => {
    renderView();

    expect(screen.getByRole('tab', { name: 'Lines' })).toHaveAttribute('data-state', 'active');
    expect(screen.getByTestId('container-request-section')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'General' })).toHaveAttribute(
      'data-state',
      'inactive',
    );
  });

  it('clicking General writes ?tab=general', () => {
    renderView();

    selectTab('General');

    expect(replace).toHaveBeenCalledWith('/scm/loading-plan/plan-1?tab=general', {
      scroll: false,
    });
  });

  it('a reload on ?tab=general lands on the General tab', () => {
    currentSearchParams = new URLSearchParams('tab=general');
    renderView();

    expect(screen.getByRole('tab', { name: 'General' })).toHaveAttribute('data-state', 'active');
    expect(screen.queryByTestId('container-request-section')).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- AC-3.3

describe('LoadingPlanView - the General tab reads the plan (AC-3.3)', () => {
  beforeEach(() => {
    currentSearchParams = new URLSearchParams('tab=general');
  });

  it('states the status, the supplier, when it started, its window and its stock list', () => {
    renderView();

    const card = within(planCard());
    for (const label of ['Status', 'Supplier', 'Started', 'Plan window', 'Stock list']) {
      expect(card.getByText(label)).toBeInTheDocument();
    }
    expect(fieldValue('Supplier')).toBe('CHAOZHOU JINBAICHUAN SANITARY WARE CO., LTD');
    expect(fieldValue('Started')).toContain('27/08/2026');
    expect(fieldValue('Plan window')).toContain('up to 30/09/2026');
    expect(fieldValue('Stock list')).toBe('Stock list 27/07/2026');
  });

  it('renders the status as the same badge the list uses, not as plain text', () => {
    renderView();

    const badge = within(planCard())
      .getAllByText('Planning')
      .find((el) => el.dataset.slot === 'badge');
    expect(badge).toBeTruthy();
  });

  it('says a plan with no file has no stock list rather than rendering nothing', () => {
    state.plan = { ...PLAN, document_kind: 'none', document_label: 'No file' };
    renderView();

    expect(fieldValue('Stock list')).toBeTruthy();
  });
});

// --------------------------------------------------------------------------- AC-3.4

describe('LoadingPlanView - the action cluster is on every tab (AC-3.4)', () => {
  it('keeps the gear, Save and Back on the General tab, and Save still counts', () => {
    currentSearchParams = new URLSearchParams('tab=general');
    renderView();

    expect(screen.getAllByRole('button', { name: 'Plan actions' })).toHaveLength(1);
    expect(screen.getByTestId('save-plan-edits').textContent).toContain('Save (0)');
    expect(screen.getByTestId('back-to-plans')).toBeInTheDocument();
  });

  it('keeps them on the Supplier codes tab too', () => {
    currentSearchParams = new URLSearchParams('tab=codes');
    renderView();

    expect(screen.getAllByRole('button', { name: 'Plan actions' })).toHaveLength(1);
    expect(screen.getByTestId('save-plan-edits')).toBeInTheDocument();
    expect(screen.getByTestId('back-to-plans')).toBeInTheDocument();
  });

  it('a quantity typed on Lines is still counted by Save after a trip to General', () => {
    renderView();

    fireEvent.click(screen.getByTestId('type-qty'));
    expect(screen.getByTestId('save-plan-edits').textContent).toContain('Save (1)');

    currentSearchParams = new URLSearchParams('tab=general');
    selectTab('General');

    expect(screen.getByTestId('save-plan-edits').textContent).toContain('Save (1)');
  });
});
