/**
 * (item 13) a NULL-quantity line from external intake reads as an error. Mocks mirror the lineItems test. M5-06 - the read-only "Line items" table on the printable-style detail
 * card renders on DataGrid instead of a raw `<Table>`. Mocking mirrors
 * PurchaseRequestDetail.test.tsx: every SLA / permission / attachment /
 * conversation dependency is neutralised, none of it relevant here.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
  usePathname: () => '/procurement-management/purchase-requests/pr-1',
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

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
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
}));

const usePurchaseRequestMock = vi.fn();
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));
vi.mock('../hooks/usePurchaseRequests', () => ({
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
    lines: [
      { id: 'line-1', purchase_request_id: 'pr-1', item_code: 'ITEM-A', quantity: 4, remark: 'Urgent' },
      { id: 'line-2', purchase_request_id: 'pr-1', item_code: 'ITEM-B', quantity: 2, remark: null },
    ],
    ...over,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PurchaseRequestDetail requestId="pr-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  usePurchaseRequestMock.mockReturnValue({ data: purchaseRequest(), isLoading: false });
});


describe('PurchaseRequestDetail - a line with no quantity (item 13a)', () => {
  it('shows the Quantity sentence as an error in that cell instead of a dash', async () => {
    usePurchaseRequestMock.mockReturnValue({
      data: purchaseRequest({
        lines: [
          { id: 'line-1', purchase_request_id: 'pr-1', item_code: 'ITEM-A', quantity: 4, remark: null },
          { id: 'line-2', purchase_request_id: 'pr-1', item_code: 'ITEM-NOQ', quantity: null, remark: null },
        ],
      }),
      isLoading: false,
    });
    renderPage();
    const row = (await screen.findByText('ITEM-NOQ')).closest('tr') as HTMLElement;
    const msg = within(row).getByText(/^Enter a (whole )?number/);
    expect(msg.className).toContain('text-destructive');
    const okRow = screen.getByText('ITEM-A').closest('tr') as HTMLElement;
    expect(within(okRow).queryByText(/^Enter a (whole )?number/)).toBeNull();
  });
});
