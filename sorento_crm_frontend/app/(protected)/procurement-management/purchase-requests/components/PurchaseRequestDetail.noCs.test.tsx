/**
 * RED tests - FORM-ROUTING-CONDITIONS NC-7 (owner ruling 14 Oct 22:02: a No CS form stays
 * Approved, there is no Completed status).
 *
 * PR/SF detail offers the CS actions (Processed by CS / Close) only on an approved form that
 * HAS a customer-service stage tracker in its SLA trackers; an approved form with no such
 * tracker (No CS) shows neither, and nothing renders them while trackers are still loading.
 * Void stays as for any approved form. Harness copied from
 * PurchaseRequestDetail.exportRevisions.test.tsx.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  // Back, and the delete that lands where Back lands, read the list state the
  // row click wrote into this URL.
  useSearchParams: () => new URLSearchParams(''),
}));
const getTrackers = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({ settings: {} }) }),
}));
vi.mock('@/app/(protected)/sla-management/_shared/formSLAService', () => ({
  getFormSLATrackers: (...a: unknown[]) => getTrackers(...a),
  escalateFormTracking: vi.fn(),
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
  useHandlingLock: () => ({
    state: 'not_escalated',
    businessCtasEnabled: true,
    tracker: null,
    claim: vi.fn(),
    takeOver: vi.fn(),
    release: vi.fn(),
    refresh: vi.fn(),
    isMutating: false,
  }),
}));
vi.mock(
  '@/app/(protected)/sla-management/conversation-sla-tracking/components/ReassignDialog',
  () => ({ __esModule: true, default: () => null }),
);
vi.mock(
  '@/app/(protected)/sla-management/conversation-sla-tracking/hooks/useTeamPendingSLA',
  () => ({ useReassignSLATracking: () => ({ mutate: vi.fn(), isPending: false }) }),
);
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
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

const getRevisions = vi.fn().mockResolvedValue([]);
vi.mock('../services/purchaseRequestService', () => ({
  sendApprovalLink: vi.fn(),
  setPendingApproval: vi.fn(),
  getUsersForApproverSelect: vi.fn().mockResolvedValue([]),
  getOrCreateViewLink: vi.fn(),
  rejectSubmittedPurchaseRequest: vi.fn(),
  processPurchaseRequestByCs: vi.fn(),
  closePurchaseRequestByCs: vi.fn(),
  submitApprovalDecision: vi.fn(),
  isDeferredDecision: () => false,
  getPurchaseRequestRevisions: (...a: unknown[]) => getRevisions(...a),
}));

let revisionEnabledMap: Record<string, boolean> = { purchase_request: true };
vi.mock('@/app/(protected)/sla-management/_shared/useRevisionEnabledMap', () => ({
  useRevisionEnabledMap: () => ({ data: revisionEnabledMap }),
}));

const exportToExcel = vi.fn().mockResolvedValue(undefined);
const exportWithRevisions = vi.fn().mockResolvedValue(undefined);
vi.mock('../lib/purchase-request-excel-export', () => ({
  exportPurchaseRequestOrSponsorshipToExcel: (...a: unknown[]) => exportToExcel(...a),
  exportPurchaseRequestOrSponsorshipWithRevisionsToExcel: (...a: unknown[]) =>
    exportWithRevisions(...a),
  createSalesTypeLabelResolver: () => () => null,
}));

const usePurchaseRequestMock = vi.fn();
const exportPdfMutate = vi.fn();
const revisionEntries = [
  { id: 'rev-0', version_no: 0, revision_no: 0, kind: 'original', label: 'Original' },
  { id: 'rev-1', version_no: 1, revision_no: 1, kind: 'revision', label: 'Revision 1' },
];

vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('../hooks/usePurchaseRequests', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  purchaseRequestsPagerQuery: {
    listQueryKey: () => ['purchase-requests'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  usePurchaseRequest: (...a: unknown[]) => usePurchaseRequestMock(...a),
  usePurchaseRequestNeighbours: () => ({
    prevId: null,
    nextId: null,
    index: null,
    total: 0,
    isLoading: false,
  }),
  useDeletePurchaseRequestAttachment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeletePurchaseRequest: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePurchaseRequestConversation: () => ({ data: undefined, isLoading: false }),
  useExportPurchaseRequestPdf: () => ({ mutate: exportPdfMutate, isPending: false }),
  usePurchaseRequestRevisions: () => ({
    data: revisionEntries,
    isLoading: false,
    isError: false,
  }),
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
    revision_no: 1,
    updated_at: '2026-07-01T00:00:00Z',
    ...over,
  };
}

function renderPage() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <PurchaseRequestDetail requestId="pr-1" />
    </QueryClientProvider>,
  );
}


const CS_TRACKER = { id: 't-cs', team_set_code: 'customer_service', is_resolved: false };
const APPROVAL_TRACKER = { id: 't-ap', team_set_code: 'approval', is_resolved: true };

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  getTrackers.mockResolvedValue([APPROVAL_TRACKER, CS_TRACKER]);
});

function approved(requestType: string) {
  usePurchaseRequestMock.mockReturnValue({
    data: purchaseRequest({ request_type: requestType, status: 'approved', approval_status: 'approved' }),
    isLoading: false,
  });
}

// Processed by CS is a header button; Close ("Mark as closed") is an item of the actions menu.
const processedButton = () => screen.queryByRole('button', { name: /processed by cs/i });
const closeItem = () => screen.queryByText('Mark as closed');

async function openActionsMenu() {
  const trigger = await screen.findByRole('button', { name: 'Request actions' });
  fireEvent.pointerDown(trigger, { button: 0, pointerId: 1 });
  fireEvent.pointerUp(trigger, { button: 0, pointerId: 1 });
  fireEvent.click(trigger);
  await waitFor(() => expect(trigger.getAttribute('aria-expanded')).toBe('true'));
}

describe('PurchaseRequestDetail - CS actions follow the customer-service tracker', () => {
  it.each(['purchase_request', 'sponsorship_form'])(
    'an approved %s WITH a customer-service tracker shows Processed by CS and Close',
    async (requestType) => {
      approved(requestType);
      renderPage();

      expect(await screen.findByRole('button', { name: /processed by cs/i })).toBeInTheDocument();
      await openActionsMenu();
      expect(await screen.findByText('Mark as closed')).toBeInTheDocument();
    },
  );

  it.each(['purchase_request', 'sponsorship_form'])(
    'an approved %s with only the approval-stage tracker (No CS) shows neither',
    async (requestType) => {
      getTrackers.mockResolvedValue([APPROVAL_TRACKER]);
      approved(requestType);
      renderPage();
      await waitFor(() => expect(getTrackers).toHaveBeenCalled());
      await screen.findAllByText(/PR-2026-0001/);
      await new Promise((r) => setTimeout(r, 50));
      await openActionsMenu();
      await screen.findByText('Export to Excel');

      expect(processedButton()).toBeNull();
      expect(closeItem()).toBeNull();
    },
  );

  it('shows neither while the trackers are still loading', async () => {
    getTrackers.mockReturnValue(new Promise(() => {}));
    approved('purchase_request');
    renderPage();
    await screen.findAllByText(/PR-2026-0001/);
    await openActionsMenu();
    await screen.findByText('Export to Excel');

    expect(processedButton()).toBeNull();
    expect(closeItem()).toBeNull();
  });

  it.each(['purchase_request', 'sponsorship_form'])(
    'Void is offered on a No CS approved %s',
    async (requestType) => {
      getTrackers.mockResolvedValue([APPROVAL_TRACKER]);
      approved(requestType);
      renderPage();
      await openActionsMenu();

      expect(await screen.findByText('Void')).toBeInTheDocument();
    },
  );
});
