/**
 * AC-CONSIGN-CRM N1 (reviewer): the React Query key the Consignment list really builds equals
 * `consignmentsPagerQuery.listQueryKey` for the same URL state, so the detail pager reads the
 * page the list fetched. Real hooks; only the service is mocked. Companion to
 * hooks/useListPager.keyParity.test.ts (one case per wired entity).
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider, hashKey } from '@tanstack/react-query';

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

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const push = vi.fn();
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push }),
  usePathname: () => '/scm/consignments',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(async () => ({ listing_key: '/scm/consignments', config: null })),
  upsertUserListColumnConfig: vi.fn(async (listingKey: string, payload: unknown) => ({
    listing_key: listingKey,
    config: payload,
  })),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));

let hasPermission = true;
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => hasPermission,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

const EMPTY = { data: [], isLoading: false };

vi.mock('../../hooks/useScmOptions', () => ({
  useCustomerOptions: () => EMPTY,
  useOrderTypeOptions: () => EMPTY,
  useProductOptions: () => EMPTY,
  useSupplierOptions: () => EMPTY,
  useCategoryOptions: () => EMPTY,
  useWarehouseOptions: () => EMPTY,
}));


const getSalesOrders = vi.fn(async () => ({
  data: [{ id: 'cg-1', so_number: 'CG-1', doc_type: 'consignment', status: 'open', lines: [] }],
  pagination: { total: 1, page: 1, limit: 25 },
  empty: false,
}));
vi.mock('../../services/salesOrderService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/salesOrderService')>()),
  getSalesOrders: (...a: unknown[]) => (getSalesOrders as (...x: unknown[]) => unknown)(...a),
}));

import { ConsignmentsList } from './ConsignmentsList';
import { consignmentsPagerQuery } from '../../hooks/useSalesOrders';
import { buildDetailSearch, parseDetailSearch } from '@/lib/listNavQuery';

describe('N1: Consignment list key parity with its pager', () => {
  it('the list query key equals consignmentsPagerQuery.listQueryKey for the same state', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ConsignmentsList />
      </QueryClientProvider>,
    );
    await screen.findByText('CG-1');
    const listKey = qc
      .getQueryCache()
      .getAll()
      .map((q) => q.queryKey)
      .find((k) => k[0] === 'scm' && k[1] === 'sales-orders');
    expect(listKey).toBeDefined();
    const url = buildDetailSearch(
      { pageIndex: 0, pageSize: 25, sorting: [{ id: 'order_date', desc: true }], searchQuery: '' },
      {},
    );
    const pagerKey = consignmentsPagerQuery.listQueryKey(parseDetailSearch(new URLSearchParams(url)));
    expect(hashKey(listKey!)).toBe(hashKey(pagerKey));
  });
});
