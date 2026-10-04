/**
 * CustomerDetail - Group field and "Group ledgers" card (CUSTOMER-GROUP S3, AC-19).
 *
 * Siblings come from `getCustomerGroupCustomers(groupId, params)` in the customer-groups
 * service (the same route the group's Ledgers tab reads).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/order-management/customers/c-1',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

const getCustomer = vi.fn();
vi.mock('../services/customerService', () => ({
  getCustomer: (...a: unknown[]) => getCustomer(...a),
  createCustomer: vi.fn(),
  updateCustomer: vi.fn(),
  getCustomers: vi.fn(),
  deleteCustomer: vi.fn(),
  getCustomerSalesAgentsSelect: vi.fn().mockResolvedValue([]),
}));

const getCustomerGroupCustomers = vi.fn();
vi.mock('../../customer-groups/services/customerGroupService', () => ({
  getCustomerGroupCustomers: (...a: unknown[]) => getCustomerGroupCustomers(...a),
  getCustomerGroups: vi.fn(),
  getCustomerGroup: vi.fn(),
  searchCustomerGroupsSelect: vi.fn().mockResolvedValue([]),
}));

import CustomerDetail from './CustomerDetail';

const BASE = {
  id: 'c-1',
  customer_code: '300-H030',
  customer_name: 'HANLIM TRADING SDN BHD [A/C I]',
  email: null,
  phone_number: null,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  account_level: 1,
};

const SIBLINGS = [
  { id: 'c-1', customer_code: '300-H030', customer_name: 'HANLIM TRADING SDN BHD [A/C I]', account_level: 1, is_active: true },
  { id: 'c-2', customer_code: '300-H070', customer_name: 'HANLIM TRADING SDN BHD [A/C II]', account_level: 2, is_active: true },
  { id: 'c-3', customer_code: '300-H118', customer_name: 'HANLIM TRADING SDN BHD [A/C III]', account_level: 3, is_active: true },
];

function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  getCustomerGroupCustomers.mockResolvedValue({
    data: SIBLINGS,
    pagination: { total: 3, page: 1, limit: 50 },
  });
});

describe('CustomerDetail - Group', () => {
  it('AC-19: the Group field shows the group name as a link to the group page', async () => {
    getCustomer.mockResolvedValue({
      ...BASE,
      customer_group_id: 'grp-1',
      customer_group_name: 'HANLIM TRADING SDN BHD',
    });
    render(<CustomerDetail customerId="c-1" />);

    await waitFor(() => expect(screen.getByText('Group')).toBeInTheDocument());
    const link = await screen.findByRole('link', { name: 'HANLIM TRADING SDN BHD' });
    expect(link).toHaveAttribute('href', '/order-management/customer-groups/grp-1');
  });

  it('AC-19: a ledger with no group says "No group"', async () => {
    getCustomer.mockResolvedValue({ ...BASE, customer_group_id: null, customer_group_name: null });
    render(<CustomerDetail customerId="c-1" />);

    expect(await screen.findByText('No group')).toBeInTheDocument();
  });

  it('AC-19: the Group ledgers card lists siblings and marks the current ledger "(this ledger)"', async () => {
    getCustomer.mockResolvedValue({
      ...BASE,
      customer_group_id: 'grp-1',
      customer_group_name: 'HANLIM TRADING SDN BHD',
    });
    render(<CustomerDetail customerId="c-1" />);

    expect(await screen.findByText('Group ledgers')).toBeInTheDocument();
    expect(await screen.findByText('300-H070')).toBeInTheDocument();
    expect(screen.getByText('300-H118')).toBeInTheDocument();
    expect(getCustomerGroupCustomers.mock.calls[0][0]).toBe('grp-1');
    const own = screen.getByText('(this ledger)');
    expect(own.closest('tr')?.textContent).toContain('300-H030');
    expect(screen.getAllByText('(this ledger)')).toHaveLength(1);
  });

  it('AC-19: with no group the card says "Not in a group" and fetches no siblings', async () => {
    getCustomer.mockResolvedValue({ ...BASE, customer_group_id: null, customer_group_name: null });
    render(<CustomerDetail customerId="c-1" />);

    expect(await screen.findByText('Not in a group')).toBeInTheDocument();
    expect(getCustomerGroupCustomers).not.toHaveBeenCalled();
  });
});
