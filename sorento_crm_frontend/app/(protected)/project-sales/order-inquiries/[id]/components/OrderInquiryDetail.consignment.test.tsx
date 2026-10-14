/**
 * AC-CONSIGN-CRM UAC-06: the Order Inquiry detail shows "Consignment" in the always-visible
 * header (beside the title and status Badge, OrderInquiryDetail.tsx) when the header's
 * `so_doc_type` is consignment. Mocking copied from `OrderInquiryDetail.test.tsx`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OrderInquiryHeaderDetail, OrderInquiryWorklistRow } from '../../../_shared/types/orderInquiry.types';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/project-sales/order-inquiries/oi-1',
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));

// S1 (reviewer round): `OrderInquiryDetail` reads `useSession` directly now, to gate
// the reserve-request Cancel control on the actual signed-in user, not only a
// permission flag.
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { id: 'test-current-user' } }, status: 'authenticated' }),
}));

// Under jsdom nothing answers the column-preferences fetch `DataGrid` starts, so the
// Lines tab grid renders skeletons forever and no row is assertable
// (project_datagrid_jsdom_rows_mockable.md).
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const acknowledgeFilterSpy = vi.fn(async () => ({ acknowledged: 0, results: [] }));
const acknowledgeRowsSpy = vi.fn(async () => ({ acknowledged: 0, results: [] }));
const autoPlaceSpy = vi.fn(async () => ({ linked: 0, results: [] }));
const unacknowledgeSpy = vi.fn<(rowIds: unknown) => Promise<{ unacknowledged: number }>>(
  async () => ({ unacknowledged: 0 }),
);
// Lane B (`PLAN-order-sheet-oi-reports-22sep.md`, AC-B1/AC-B5): Export Excel goes
// async (My Downloads), never a blob save - and the gear gets a "Download history" item.
const exportOrderInquiryXlsxSpy = vi.hoisted(() => vi.fn(async () => ({
  id: 'dl-1', kind: 'order_inquiry_xlsx', status: 'pending', filename: 'OI-2609-0001.xlsx',
})));

const toastSpy = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
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

vi.mock('../../../_shared/services/orderInquiryService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../_shared/services/orderInquiryService')>();
  return {
    ...actual,
    getOrderInquiryHeader: vi.fn(async () => HEADER),
    getOrderInquiryHeaderLines: vi.fn(async () => [LINKED_LINE]),
    getOrderInquiryHeaderRelatedDocuments: vi.fn(async () => ({
      purchase_orders: [],
      spos: [],
    })),
    listOrderInquiryHeaders: vi.fn(async () => ({ data: [], total: 0, page: 1, limit: 25 })),
    exportOrderInquiryXlsx: (...args: unknown[]) =>
      exportOrderInquiryXlsxSpy(...(args as [string])),
    acknowledgeOrderInquiryRowsByFilter: (...args: unknown[]) =>
      acknowledgeFilterSpy(...(args as [unknown])),
    acknowledgeOrderInquiryRows: (...args: unknown[]) =>
      acknowledgeRowsSpy(...(args as [unknown])),
    autoPlaceOrderInquiryRows: (...args: unknown[]) => autoPlaceSpy(...(args as [unknown])),
    unacknowledgeOrderInquiryRows: (...args: unknown[]) =>
      unacknowledgeSpy(...(args as [unknown])),
  };
});

// UL: the deferred Unlink parks a real pending action through this service - mocked so
// the countdown `useDeferredBulkAction` builds is exercised for real, with a window long
// enough (10s) that a synchronous test never sees it lapse.
const createPendingActionSpy = vi.fn(async ({ entityId }: { entityId: string }) => ({
  id: `pa-${entityId}`,
  action_key: 'order_inquiry_row.unlink',
  entity_type: 'order_inquiry_row',
  entity_id: entityId,
  commit_at: new Date(Date.now() + 10_000).toISOString(),
  window_seconds: 10,
}));
const cancelPendingActionSpy = vi.fn(async () => undefined);
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) =>
    createPendingActionSpy(...(args as [{ entityId: string }])),
  cancelPendingAction: (...args: unknown[]) => cancelPendingActionSpy(...(args as [string])),
  getCurrentPendingAction: vi.fn(async () => ({ pending: null, last_outcome: null })),
}));

import { pendingEntityStore } from '@/lib/pending-entity-store';
import {
  getOrderInquiryHeader,
  getOrderInquiryHeaderLines,
} from '../../../_shared/services/orderInquiryService';
import { OrderInquiryDetail } from './OrderInquiryDetail';

const mockGetOrderInquiryHeader = getOrderInquiryHeader as unknown as ReturnType<typeof vi.fn>;

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

describe('UAC-06: OI detail header carries the Consignment label', () => {
  it('a consignment header shows Consignment', async () => {
    mockGetOrderInquiryHeader.mockResolvedValue({ ...HEADER, so_doc_type: 'consignment' });
    renderDetail('oi-1');
    await screen.findByText(HEADER.inquiry_no);
    expect(await screen.findByText('Consignment')).toBeInTheDocument();
  });

  it('a sales_order header shows no Consignment label', async () => {
    mockGetOrderInquiryHeader.mockResolvedValue({ ...HEADER, so_doc_type: 'sales_order' });
    renderDetail('oi-1');
    await screen.findByText(HEADER.inquiry_no);
    expect(screen.queryByText('Consignment')).toBeNull();
  });
});
