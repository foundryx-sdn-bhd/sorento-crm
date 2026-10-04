/**
 * CustomerDetail - the read-only "Account level" field (ACCOUNT-LEDGER, AC-1).
 *
 * Same section as the form's field (Contact Information, View = Edit layout): "Account 2" when
 * set, an explicit empty state when not. The empty text carries no dash and no UUID.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  usePathname: () => '/order-management/customers/cust-1',
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

import CustomerDetail from './CustomerDetail';

const BASE = {
  id: 'cust-1',
  customer_code: 'C-001',
  customer_name: 'Alpha Trading',
  email: null,
  phone_number: '03-1111111',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  sales_agent_id: null,
  sales_agent_code: null,
  sales_agent_name: null,
};

function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('CustomerDetail - Account level', () => {
  it('shows "Account 2" under the Account level label, in the same card as Phone', async () => {
    getCustomer.mockResolvedValue({ ...BASE, account_level: 2 });
    render(<CustomerDetail customerId="cust-1" />);

    await waitFor(() => expect(screen.getByText('Account 2')).toBeInTheDocument());
    const label = screen.getByText('Account level');
    expect(label.closest('[data-slot="card"]')).toBe(
      screen.getByText('Phone').closest('[data-slot="card"]'),
    );
  });

  it('shows an explicit empty state when no level is set', async () => {
    getCustomer.mockResolvedValue({ ...BASE, account_level: null });
    render(<CustomerDetail customerId="cust-1" />);

    await waitFor(() => expect(screen.getByText('Account level')).toBeInTheDocument());
    expect(screen.getByText('No account level set')).toBeInTheDocument();
    expect(screen.queryByText(/^Account \d+$/)).not.toBeInTheDocument();
  });
});
