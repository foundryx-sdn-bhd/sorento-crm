/**
 * PurchaseRequestDetail - gear-menu Reassign item (UAC-response-attachments.md
 * group H). PR/SF has no response-attachment popup this slice (explicitly out
 * of scope, C6), so this file covers Reassign only, mirroring
 * ComplaintDetail.test.tsx / StockInquiryDetail.test.tsx. Every SLA / permission
 * / attachment / conversation dependency is mocked at the module boundary
 * PurchaseRequestDetail pulls in more machinery (useAction, system-settings
 * query, approver-select query) than the other two detail pages, so those are
 * neutralised too, rather than exercised here.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  // Back, and the delete that lands where Back lands, read the list state the
  // row click wrote into this URL.
  useSearchParams: () => new URLSearchParams(''),
}));

// Every network call PurchaseRequestDetail makes directly (system-settings,
// view-link, lookup-binding) resolves to an inert empty payload - none of it
// is relevant to the Reassign wiring under test.
vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({ settings: {} }) }),
}));

const getFormSLATrackersMock = vi.fn();
const escalateFormTrackingMock = vi.fn();
vi.mock('@/app/(protected)/sla-management/_shared/formSLAService', () => ({
  getFormSLATrackers: (...a: unknown[]) => getFormSLATrackersMock(...a),
  escalateFormTracking: (...a: unknown[]) => escalateFormTrackingMock(...a),
}));

vi.mock('@/app/(protected)/sla-management/_shared/SlaActiveTrackerControls', () => ({
  SlaActiveTrackerControls: () => null,
}));
vi.mock('@/app/(protected)/sla-management/_shared/SlaExtendAction', () => ({
  SlaExtendMenuItem: () => null,
  SlaExtendDialog: () => null,
}));
vi.mock('@/app/(protected)/sla-management/_shared/HandlingLockBanner', () => ({
  HandlingLockBanner: () => null,
}));
vi.mock('@/app/(protected)/sla-management/_shared/HandlingLockActions', () => ({
  HandlingLockReleaseMenuItem: () => null,
}));

const handlingLockRefresh = vi.fn();
let handlingLockState: {
  state: string;
  businessCtasEnabled: boolean;
  tracker: unknown;
  claim: () => void;
  takeOver: () => void;
  release: () => void;
  refresh: () => void;
  isMutating: boolean;
} = {
  state: 'not_escalated',
  businessCtasEnabled: true,
  tracker: null,
  claim: vi.fn(),
  takeOver: vi.fn(),
  release: vi.fn(),
  refresh: handlingLockRefresh,
  isMutating: false,
};

// Form-action deferral: idle by default - these suites exercise other wiring. The
// hook's own behaviour is covered by formAction.test.ts + the pytest/e2e layers.
vi.mock('@/app/(protected)/sla-management/_shared/useFormAction', () => ({
  useFormAction: () => ({
    view: { kind: 'idle' },
    ctasDisabled: false,
    cancel: vi.fn(),
    undo: vi.fn(),
    refresh: vi.fn(),
    isMutating: false,
  }),
}));

vi.mock('@/app/(protected)/sla-management/_shared/useHandlingLock', () => ({
  useHandlingLock: () => handlingLockState,
}));

vi.mock(
  '@/app/(protected)/sla-management/conversation-sla-tracking/components/ReassignDialog',
  () => ({
    __esModule: true,
    default: ({
      open,
      onConfirm,
    }: {
      open: boolean;
      onConfirm: (userId: string) => void;
    }) =>
      open ? (
        <button type="button" onClick={() => onConfirm('user-42')}>
          Confirm reassign
        </button>
      ) : null,
  }),
);

const reassignMutateMock = vi.fn();
vi.mock(
  '@/app/(protected)/sla-management/conversation-sla-tracking/hooks/useTeamPendingSLA',
  () => ({
    useReassignSLATracking: () => ({ mutate: reassignMutateMock, isPending: false }),
  }),
);

let permissionMap: Record<string, boolean> = {};
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => permissionMap[slug] ?? false,
}));

vi.mock('@/hooks/useFormVoid', () => ({
  useFormVoid: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/hooks/usePublicViewLinksEnabled', () => ({
  usePublicViewLinksEnabled: () => false,
}));
vi.mock('@/hooks/useLookupOptionsByBinding', () => ({
  useLookupOptionsByBinding: () => ({ data: undefined, isLoading: false }),
}));

vi.mock('./PurchaseRequestNavigation', () => ({ __esModule: true, default: () => null }));
vi.mock('./PurchaseRequestAttachmentsSection', () => ({ __esModule: true, default: () => null }));
vi.mock('./PurchaseRequestConversationPanel', () => ({ __esModule: true, default: () => null }));
vi.mock('./PurchaseRequestSignoffFooter', () => ({
  PurchaseRequestSignoffFooter: () => null,
}));
vi.mock('@/components/audit/AuditTrail', () => ({ __esModule: true, default: () => null }));

// Everything PurchaseRequestDetail does through the service module - none of
// it is exercised by the Reassign scenarios.
vi.mock('../services/purchaseRequestService', () => ({
  sendApprovalLink: vi.fn(),
  setPendingApproval: vi.fn(),
  getUsersForApproverSelect: vi.fn().mockResolvedValue([]),
  getOrCreateViewLink: vi.fn(),
  rejectSubmittedPurchaseRequest: vi.fn(),
  processPurchaseRequestByCs: vi.fn(),
  closePurchaseRequestByCs: vi.fn(),
  submitApprovalDecision: vi.fn(),
  isDeferredDecision: (r: unknown) =>
    !!r && typeof r === 'object' && (r as { deferred?: boolean }).deferred === true,
}));

const usePurchaseRequestMock = vi.fn();
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('../hooks/usePurchaseRequests', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  purchaseRequestsPagerQuery: {
    listQueryKey: () => ['purchase-requests'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  usePurchaseRequest: (...a: unknown[]) => usePurchaseRequestMock(...a),
  usePurchaseRequestNeighbours: () => ({ prevId: null, nextId: null, index: null, total: 0, isLoading: false }),
  useDeletePurchaseRequestAttachment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeletePurchaseRequest: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePurchaseRequestConversation: () => ({ data: undefined, isLoading: false }),
  useExportPurchaseRequestPdf: () => ({ mutate: vi.fn(), isPending: false }),
  usePurchaseRequestRevisions: () => ({ data: [], isLoading: false, isError: false }),
}));

import PurchaseRequestDetail from './PurchaseRequestDetail';

function purchaseRequest(over: Record<string, unknown> = {}) {
  return {
    id: 'pr-1',
    request_type: 'purchase_request',
    request_number: 'PR-2026-0001',
    status: 'draft',
    approval_status: null,
    respond_inbox_url: null,
    updated_at: '2026-07-01T00:00:00Z',
    ...over,
  };
}

function renderPage(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(
    <QueryClientProvider client={client}>
      <PurchaseRequestDetail requestId="pr-1" />
    </QueryClientProvider>,
  );
  return client;
}

function openTracker(over: Record<string, unknown> = {}) {
  return { id: 'tracker-1', is_resolved: false, event_logs: [], ...over };
}

async function openGearMenu() {
  const trigger = await screen.findByRole('button', { name: 'Request actions' });
  fireEvent.pointerDown(trigger, { button: 0, pointerId: 1 });
  fireEvent.pointerUp(trigger, { button: 0, pointerId: 1 });
  fireEvent.click(trigger);
  await waitFor(() => expect(trigger.getAttribute('aria-expanded')).toBe('true'));
}

beforeEach(() => {
  vi.clearAllMocks();
  permissionMap = { 'sla_management.conversation_sla_tracking.reassign': true };
  handlingLockState = {
    state: 'not_escalated',
    businessCtasEnabled: true,
    tracker: null,
    claim: vi.fn(),
    takeOver: vi.fn(),
    release: vi.fn(),
    refresh: handlingLockRefresh,
    isMutating: false,
  };
  usePurchaseRequestMock.mockReturnValue({ data: purchaseRequest(), isLoading: false });
  getFormSLATrackersMock.mockResolvedValue([]);
});

describe('PurchaseRequestDetail - Reassign gear-menu item', () => {
  it('is present when an open tracker exists', async () => {
    getFormSLATrackersMock.mockResolvedValue([openTracker()]);
    renderPage();

    await openGearMenu();
    expect(await screen.findByText('Reassign')).toBeInTheDocument();
  });

  it('is absent when there is no open tracker', async () => {
    getFormSLATrackersMock.mockResolvedValue([]);
    renderPage();

    await openGearMenu();
    await waitFor(() => expect(getFormSLATrackersMock).toHaveBeenCalled());
    expect(screen.queryByText('Reassign')).toBeNull();
  });

  it('is absent when there is an open tracker but the request is voided', async () => {
    getFormSLATrackersMock.mockResolvedValue([openTracker()]);
    usePurchaseRequestMock.mockReturnValue({
      data: purchaseRequest({ status: 'voided' }),
      isLoading: false,
    });
    renderPage();

    await openGearMenu();
    await waitFor(() => expect(getFormSLATrackersMock).toHaveBeenCalled());
    expect(screen.queryByText('Reassign')).toBeNull();
  });

  it('on success invalidates form-sla-trackers AND calls handlingLock.refresh()', async () => {
    getFormSLATrackersMock.mockResolvedValue([openTracker()]);
    reassignMutateMock.mockImplementation((_vars, opts) => {
      opts.onSuccess();
    });
    const client = renderPage();
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');

    await openGearMenu();
    fireEvent.click(await screen.findByText('Reassign'));
    fireEvent.click(await screen.findByText('Confirm reassign'));

    expect(reassignMutateMock).toHaveBeenCalledWith(
      { id: 'tracker-1', userId: 'user-42' },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['form-sla-trackers', 'purchase_request', 'pr-1'],
      }),
    );
    expect(handlingLockRefresh).toHaveBeenCalled();
  });
});

describe('PurchaseRequestDetail - printed / on-screen layout', () => {
  it('lets a long unbroken value wrap instead of widening the page', async () => {
    // A grid item defaults to `min-width: auto`, so an address with no spaces
    // pushes its track wider than the card and the whole page scrolls
    // sideways. `min-w-0` lets the item shrink; `break-words` then splits the
    // token. Both are required - either one alone still overflows.
    usePurchaseRequestMock.mockReturnValue({
      data: purchaseRequest({
        delivery_address:
          's.dajkfndalkfkjfnkaewejirhofeuiiojpweksldmfjasdasdhfolsadkjdskjajlsajdfslsdslllls',
      }),
      isLoading: false,
    });
    renderPage();

    const label = await screen.findByText('Customer Name');
    const grid = label.closest('.grid');
    expect(grid, 'the detail fields are not in a grid any more').toBeTruthy();
    expect(grid?.className).toContain('[&>div]:min-w-0');
    expect(grid?.className).toContain('[&_p]:break-words');
  });

  it('puts Print / Download PDF directly below Export to Excel', async () => {
    // Two ways to get the same document out. Separated in the menu they read as
    // unrelated actions, and a user hunting for "the export" finds only one.
    renderPage();
    await openGearMenu();

    const items = await screen.findAllByRole('menuitem');
    const labels = items.map((el) => el.textContent?.trim() ?? '');
    const excel = labels.findIndex((t) => t.startsWith('Export to Excel'));
    const pdf = labels.findIndex((t) => t.startsWith('Print / Download PDF'));
    expect(excel, 'no Export to Excel item').toBeGreaterThanOrEqual(0);
    expect(pdf).toBe(excel + 1);
  });

  it('offers the PDF from the gear menu, matching stock inquiries', async () => {
    // It used to be a labelled header button. Complaints and stock inquiries
    // put it in the actions menu with the printer icon and keep only the
    // downloads chip in the header; PR/SF now agree.
    renderPage();
    expect(screen.queryByRole('button', { name: /^Download PDF$/ })).toBeNull();

    await openGearMenu();
    expect(await screen.findByText('Print / Download PDF')).toBeTruthy();
  });
});

describe('PurchaseRequestDetail - sponsorship form Sales Type (SF-SALES-TYPE)', () => {
  it('shows Sales Type right after Sponsor Subject', async () => {
    usePurchaseRequestMock.mockReturnValue({
      data: purchaseRequest({
        request_type: 'sponsorship_form',
        request_number: 'PSSF26-0001',
        sponsor_subject: 'others',
        sales_type: 'cash_sales',
        lines: [],
      }),
      isLoading: false,
    });
    renderPage();

    const subject = await screen.findByText('Sponsor Subject');
    const salesType = screen.getByText('Sales Type');
    const labels = Array.from(document.querySelectorAll('p.text-muted-foreground'));
    expect(labels.indexOf(salesType as Element)).toBe(labels.indexOf(subject as Element) + 1);
  });
});
