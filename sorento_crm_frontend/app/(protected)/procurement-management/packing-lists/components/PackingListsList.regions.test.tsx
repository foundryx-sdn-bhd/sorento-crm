/**
 * PackingListsList - Regions column (REGION-PACKING-LIST, AC-RPL-5).
 *
 * One Badge per region on each row: 'west' reads "West Malaysia", 'east' reads
 * "East Malaysia". Same module mocks as PackingListsList.test.tsx.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}
Element.prototype.scrollIntoView = vi.fn();

vi.mock('next/navigation', () => ({
  usePathname: () => '/procurement-management/packing-lists',
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/components/upload-activity', () => ({
  useUploadManager: () => ({ startSession: vi.fn() }),
}));

const usePackingLists = vi.fn();
vi.mock('../hooks/usePackingLists', () => ({
  usePackingLists: (...a: unknown[]) => usePackingLists(...a),
  useBulkDeletePackingLists: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeletePackingList: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('../services/packingListService', () => ({
  getLatestContainerStatusDocument: vi.fn(),
}));
vi.mock('@/app/(protected)/scm/services/fulfilmentService', () => ({
  previewSupplierDocuments: vi.fn(),
  applySupplierDocuments: vi.fn(),
  getFulfilmentSuppliers: vi.fn(async () => []),
}));
vi.mock('@/app/(protected)/resource-management/attachments/hooks/useAttachments', () => ({
  useAttachmentTypesList: () => ({ data: [], isLoading: false }),
}));
vi.mock('@/app/(protected)/resource-management/attachments/components/AttachmentUploadDialog', () => ({
  default: () => null,
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasAnyPermission: () => true }));
vi.mock('@/hooks/useTenantModules', () => ({
  useTenantModules: () => ({ enabledModuleKeys: new Set(['procurement', 'scm']), isLoading: false }),
}));

import PackingListsList from './PackingListsList';
import type { PackingList } from '../types/packingList.types';

function row(over: Record<string, unknown>): PackingList {
  return {
    id: 'pl-1',
    shipment_number: 'PL-2026-0001',
    shipping_container_number: 'TEMU1234567',
    supplier: { id: 'sup-1', supplier_code: 'SUP1', supplier_name: 'Acme Sanitary' },
    shipment_date: '2026-08-01',
    estimated_arrival_date: null,
    shipment_status: 'in_transit',
    total_items_shipped: 100,
    created_at: '2026-08-01T00:00:00Z',
    regions: ['west'],
    ...over,
  } as unknown as PackingList;
}

function mockList(rows: PackingList[]) {
  usePackingLists.mockReturnValue({
    data: { data: rows, pagination: { page: 1, total: rows.length } },
    isLoading: false,
    isFetching: false,
    isPlaceholderData: false,
    refetch: vi.fn(),
  });
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PackingListsList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PackingListsList - Regions column (AC-RPL-5)', () => {
  it('has a Regions column header', () => {
    mockList([row({})]);
    renderList();
    expect(screen.getByText('Regions')).toBeInTheDocument();
  });

  it('renders a West Malaysia badge for a West-only packing list', () => {
    mockList([row({ regions: ['west'] })]);
    renderList();
    expect(screen.getByText('West Malaysia')).toBeInTheDocument();
    expect(screen.queryByText('East Malaysia')).not.toBeInTheDocument();
  });

  it('renders an East Malaysia badge for an East-only packing list', () => {
    mockList([row({ regions: ['east'] })]);
    renderList();
    expect(screen.getByText('East Malaysia')).toBeInTheDocument();
    expect(screen.queryByText('West Malaysia')).not.toBeInTheDocument();
  });

  it('renders both badges for a West + East packing list', () => {
    mockList([row({ regions: ['west', 'east'] })]);
    renderList();
    expect(screen.getByText('West Malaysia')).toBeInTheDocument();
    expect(screen.getByText('East Malaysia')).toBeInTheDocument();
  });

  it('renders each row\'s own regions', () => {
    mockList([
      row({ id: 'pl-1', shipment_number: 'PL-WEST', regions: ['west'] }),
      row({ id: 'pl-2', shipment_number: 'PL-EAST', regions: ['east'] }),
    ]);
    renderList();
    const westRow = screen.getByText('PL-WEST').closest('tr') as HTMLElement;
    const eastRow = screen.getByText('PL-EAST').closest('tr') as HTMLElement;
    expect(within(westRow).getByText('West Malaysia')).toBeInTheDocument();
    expect(within(westRow).queryByText('East Malaysia')).not.toBeInTheDocument();
    expect(within(eastRow).getByText('East Malaysia')).toBeInTheDocument();
  });
});
