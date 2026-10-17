/**
 * StockInquiryDetail - the NEW-PRODUCT-APP entry points: the gear item that starts an
 * application from a responded inquiry, and the "New product applications" row listing what
 * was raised from it.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const push = vi.hoisted(() => vi.fn());
const applications = vi.hoisted(() => ({ data: [] as unknown[] }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  // Back, and the delete that lands where Back lands, read the list state the
  // row click wrote into this URL.
  useSearchParams: () => new URLSearchParams(''),
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

vi.mock('./StockInquiryNavigation', () => ({ __esModule: true, default: () => null }));
vi.mock('./StockInquiryAttachmentsSection', () => ({ __esModule: true, default: () => null }));
vi.mock('@/components/audit/AuditTrail', () => ({ __esModule: true, default: () => null }));

const useStockInquiryMock = vi.fn();
const updateInquiryMutateAsync = vi.fn().mockResolvedValue({});
const updateAndReplyMutateAsync = vi.fn().mockResolvedValue({});
const uploadResponseAttachmentsMutateAsync = vi.fn();
const exportPdfMutate = vi.fn();

vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('../hooks/useStockInquiries', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  stockInquiriesPagerQuery: {
    listQueryKey: () => ['stock-inquiries'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useStockInquiry: (...a: unknown[]) => useStockInquiryMock(...a),
  useUpdateStockInquiry: () => ({ mutateAsync: updateInquiryMutateAsync, isPending: false }),
  useUpdateStockInquiryAndReply: () => ({ mutateAsync: updateAndReplyMutateAsync, isPending: false }),
  useSubmitStockInquiryForProjectSales: () => ({ mutate: vi.fn(), isPending: false }),
  useProjectSalesApproveStockInquiry: () => ({ mutate: vi.fn(), isPending: false }),
  useProjectSalesRejectStockInquiry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePurchasingRejectStockInquiry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReopenStockInquiry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadStockInquiryResponseAttachments: () => ({
    mutateAsync: uploadResponseAttachmentsMutateAsync,
    isPending: false,
  }),
  useDeleteStockInquiry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useExportStockInquiryPdf: () => ({ mutate: exportPdfMutate, isPending: false }),
  useStockInquiryRevisions: () => ({ data: [], isLoading: false, isError: false }),
}));

vi.mock('../../new-product-applications/hooks/useNewProductApplications', () => ({
  useStockInquiryApplicationsQuery: (_id: string | null, enabled: boolean) => ({
    data: enabled ? applications.data : undefined,
    isLoading: false,
  }),
}));

import StockInquiryDetail from './StockInquiryDetail';

function inquiry(over: Record<string, unknown> = {}) {
  return {
    id: 'si-1',
    inquiry_number: 'SI26-0412',
    status: 'responded',
    created_at: '2026-07-01T00:00:00Z',
    purchasing_response: '',
    respond_inbox_url: null,
    attachments: [],
    ...over,
  };
}

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <StockInquiryDetail inquiryId="si-1" />
    </QueryClientProvider>,
  );
}

async function openGearMenu() {
  const trigger = await screen.findByRole('button', { name: 'Stock inquiry actions' });
  fireEvent.pointerDown(trigger, { button: 0, pointerId: 1 });
  fireEvent.pointerUp(trigger, { button: 0, pointerId: 1 });
  fireEvent.click(trigger);
  await waitFor(() => expect(trigger.getAttribute('aria-expanded')).toBe('true'));
}

const CREATE = 'procurement.new_product_applications.create';
const VIEW = 'procurement.new_product_applications.view';

beforeEach(() => {
  vi.clearAllMocks();
  permissionMap = {};
  applications.data = [];
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
  useStockInquiryMock.mockReturnValue({ data: inquiry(), isLoading: false });
  getFormSLATrackersMock.mockResolvedValue([]);
});

describe('StockInquiryDetail - Create new product application gear item', () => {
  it('shows on a responded inquiry for a create holder and opens the New form on that inquiry', async () => {
    permissionMap = { [CREATE]: true };
    renderPage();
    await openGearMenu();
    fireEvent.click(await screen.findByText('Create new product application'));
    expect(push).toHaveBeenCalledWith(
      '/procurement-management/new-product-applications/new?stock_inquiry_id=si-1',
    );
  });

  it('is absent without the create permission', async () => {
    permissionMap = { [VIEW]: true };
    renderPage();
    await openGearMenu();
    await screen.findByText('Download history');
    expect(screen.queryByText('Create new product application')).toBeNull();
  });

  it('is absent on an inquiry that is not responded', async () => {
    permissionMap = { [CREATE]: true };
    useStockInquiryMock.mockReturnValue({
      data: inquiry({ status: 'pending_purchasing' }),
      isLoading: false,
    });
    renderPage();
    await openGearMenu();
    await screen.findByText('Download history');
    expect(screen.queryByText('Create new product application')).toBeNull();
  });
});

describe('StockInquiryDetail - New product applications row', () => {
  it('says "None yet" when nothing was raised from the inquiry', async () => {
    permissionMap = { [VIEW]: true };
    renderPage();
    expect(await screen.findByText('New product applications')).toBeInTheDocument();
    expect(screen.getByText('None yet')).toBeInTheDocument();
  });

  it('lists each application number with its status and opened codes', async () => {
    permissionMap = { [VIEW]: true };
    applications.data = [
      {
        id: 'a-1',
        application_no: 'NPA-000001',
        status: 'completed',
        status_label: 'Completed',
        opened_codes: ['XY-1234-MB', 'XY-1234-BG'],
      },
    ];
    renderPage();
    const link = await screen.findByRole('link', { name: 'NPA-000001' });
    expect(link).toHaveAttribute('href', '/procurement-management/new-product-applications/a-1');
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText(/XY-1234-MB, XY-1234-BG/)).toBeInTheDocument();
  });

  it('is hidden for a user with no new product application permission', async () => {
    renderPage();
    await screen.findByText('Stock inquiry number');
    expect(screen.queryByText('New product applications')).toBeNull();
  });
});
