/**
 * RED tests - FORM-ROUTING-CONDITIONS NC-7 / NC-5 (PurchaseRequestDetail).
 *
 * A `completed` form (approved, finished at approval because the requestor's rule was No CS)
 * reads "Completed" in the status pill, and offers no Processed by CS / Close buttons: it is
 * terminal. Harness copied from PurchaseRequestDetail.exportRevisions.test.tsx.
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
vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({ settings: {} }) }),
}));
vi.mock('@/app/(protected)/sla-management/_shared/formSLAService', () => ({
  getFormSLATrackers: vi.fn().mockResolvedValue([]),
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


beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PurchaseRequestDetail - completed status', () => {
  it.each(['purchase_request', 'sponsorship_form'])(
    'shows the status pill as "Completed" for a completed %s',
    async (requestType) => {
      usePurchaseRequestMock.mockReturnValue({
        data: purchaseRequest({
          request_type: requestType,
          status: 'completed',
          approval_status: 'approved',
        }),
        isLoading: false,
      });
      renderPage();

      expect(await screen.findAllByText('Completed')).not.toHaveLength(0);
      expect(screen.queryByText('Approved')).not.toBeInTheDocument();
    },
  );

  it('offers no Processed by CS or Close action on a completed form', async () => {
    usePurchaseRequestMock.mockReturnValue({
      data: purchaseRequest({ status: 'completed', approval_status: 'approved' }),
      isLoading: false,
    });
    renderPage();
    await screen.findAllByText(/PR-2026-0001/);

    expect(screen.queryByRole('button', { name: /processed by cs/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^close/i })).toBeNull();
  });

  it('guard: an approved (not completed) form still offers Processed by CS', async () => {
    usePurchaseRequestMock.mockReturnValue({
      data: purchaseRequest({ status: 'approved', approval_status: 'approved' }),
      isLoading: false,
    });
    renderPage();

    expect(await screen.findByRole('button', { name: /processed by cs/i })).toBeInTheDocument();
  });
});
