/**
 * PackingListsList - Region filter (REGION-PACKING-LIST, AC-RPL-5, review round 1).
 *
 * Setting the filter reaches the list service as `region`, and the row link carries it so
 * the detail page's prev/next walks the same filtered set.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
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

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  usePathname: () => '/procurement-management/packing-lists',
  useRouter: () => ({ push: routerPush }),
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
type MenuSlotProps = { children?: React.ReactNode };
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: MenuSlotProps) => <>{children}</>,
  DropdownMenuContent: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuItem: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuCheckboxItem: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuLabel: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuGroup: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuPortal: ({ children }: MenuSlotProps) => <>{children}</>,
  DropdownMenuSub: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuSubContent: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuSubTrigger: ({ children }: MenuSlotProps) => <div>{children}</div>,
}));

// The real SearchableSelect is a Radix popover + cmdk list, which is not deterministic under
// jsdom: a native <select> carries the same value/onChange/options contract.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    id,
    value,
    onChange,
    options,
    placeholder,
  }: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select id={id} aria-label={placeholder ?? id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{placeholder ?? ''}</option>
      {(options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
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

describe('PackingListsList - the Region filter (AC-RPL-5, review round 1)', () => {
  function pickRegion(code: string) {
    fireEvent.change(screen.getByLabelText('All regions'), { target: { value: code } });
  }

  it('sends region to the list service once the filter is set', () => {
    mockList([row({})]);
    renderList();
    expect(usePackingLists.mock.calls.at(-1)?.[0].region).toBeUndefined();

    pickRegion('east');
    expect(usePackingLists.mock.calls.at(-1)?.[0].region).toBe('east');
  });

  it('carries region on the row link so the detail pager walks the same filtered set', () => {
    mockList([row({})]);
    renderList();
    pickRegion('east');

    fireEvent.click(screen.getByText('PL-2026-0001'));
    expect(routerPush).toHaveBeenCalled();
    const href = routerPush.mock.calls.at(-1)?.[0] as string;
    expect(href).toContain('/procurement-management/packing-lists/pl-1');
    expect(new URL(href, 'http://x').searchParams.get('region')).toBe('east');
  });

  it('leaves region off the row link when no region is chosen', () => {
    mockList([row({})]);
    renderList();
    fireEvent.click(screen.getByText('PL-2026-0001'));
    expect(routerPush).toHaveBeenCalled();
    expect(routerPush.mock.calls.at(-1)?.[0] as string).not.toContain('region=');
  });
});
