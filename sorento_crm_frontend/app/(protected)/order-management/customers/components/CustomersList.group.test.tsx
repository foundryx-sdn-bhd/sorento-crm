/**
 * CustomersList - the Group column and Group filter (CUSTOMER-GROUP S3, AC-21).
 *
 * The filter is a Group select (accessible name "Group") inside the Filters popover; picking a
 * group puts `customer_group_id` on the list query params. Options come from
 * `searchCustomerGroupsSelect`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
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
Element.prototype.hasPointerCapture = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/order-management/customers',
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('@/components/upload-activity', () => ({
  useImportJobDrawer: () => ({ notifyImportQueued: vi.fn() }),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));
vi.mock('../services/customerImportService', () => ({
  importCustomers: vi.fn(),
  validateCustomerImport: vi.fn(),
}));
vi.mock('../../customer-groups/services/customerGroupService', () => ({
  searchCustomerGroupsSelect: vi.fn().mockResolvedValue([
    { id: 'grp-1', name: 'HANLIM TRADING SDN BHD', ledger_count: 6 },
  ]),
  getCustomerGroups: vi.fn(),
  getCustomerGroup: vi.fn(),
  getCustomerGroupCustomers: vi.fn(),
}));

const ROWS = [
  {
    id: 'c-1',
    customer_code: '300-H030',
    customer_name: 'HANLIM TRADING SDN BHD [A/C I]',
    email: null,
    phone_number: null,
    is_active: true,
    created_at: new Date('2026-01-01T00:00:00Z'),
    customer_group_id: 'grp-1',
    customer_group_name: 'HANLIM TRADING SDN BHD',
  },
  {
    id: 'c-2',
    customer_code: 'C-002',
    customer_name: 'Beta Supplies',
    email: null,
    phone_number: null,
    is_active: true,
    created_at: new Date('2026-01-02T00:00:00Z'),
    customer_group_id: null,
    customer_group_name: null,
  },
];

const useCustomersSpy = vi.hoisted(() => vi.fn());
vi.mock('../hooks/useCustomers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../hooks/useCustomers')>()),
  useCustomers: (params: unknown) => {
    useCustomersSpy(params);
    return {
      data: { data: ROWS, pagination: { page: 1, limit: 50, total: ROWS.length } },
      isLoading: false,
      isPlaceholderData: false,
      isFetching: false,
      refetch: vi.fn(),
    };
  },
}));

import CustomersList from './CustomersList';

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CustomersList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useCustomersSpy.mockClear();
});

describe('CustomersList - Group', () => {
  it('AC-21: has a Group column rendering customer_group_name', () => {
    renderList();
    expect(screen.getAllByText('Group').length).toBeGreaterThan(0);
    const row = screen.getByText('300-H030').closest('tr') as HTMLElement;
    expect(row.textContent).toContain('HANLIM TRADING SDN BHD');
  });

  it('AC-21: picking a Group in the filter passes customer_group_id to the list query', async () => {
    renderList();

    fireEvent.pointerDown(screen.getByRole('button', { name: /filters?/i }), {
      button: 0,
      ctrlKey: false,
    });
    const combo = await screen.findByRole('combobox', { name: 'Group' });
    fireEvent.click(combo);
    fireEvent.click(await screen.findByRole('option', { name: /HANLIM TRADING SDN BHD/ }));

    await waitFor(() =>
      expect(useCustomersSpy).toHaveBeenLastCalledWith(
        expect.objectContaining({ customer_group_id: 'grp-1' }),
      ),
    );
  });
});
