/**
 * RED tests - FORM-ROUTING-CONDITIONS DT-5 (CustomerDetail).
 *
 * The read-only "Debtor type" field, same card as Account level / Phone (View = Edit layout):
 * the label ("Project cash") when set, "-" when blank.
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

describe('CustomerDetail - Debtor type', () => {
  it.each([
    ['project', 'Project'],
    ['project_cash', 'Project cash'],
    ['cash_sales', 'Cash sales'],
  ])('shows %s as "%s" under the Debtor type label, in the same card as Phone', async (value, label) => {
    getCustomer.mockResolvedValue({ ...BASE, account_level: null, debtor_type: value });
    render(<CustomerDetail customerId="cust-1" />);

    const heading = await screen.findByText('Debtor type');
    expect(heading.parentElement).toHaveTextContent(label);
    expect(heading.closest('[data-slot="card"]')).toBe(
      screen.getByText('Phone').closest('[data-slot="card"]'),
    );
  });

  it('shows "-" when no debtor type is set', async () => {
    getCustomer.mockResolvedValue({ ...BASE, account_level: null, debtor_type: null });
    render(<CustomerDetail customerId="cust-1" />);

    const heading = await screen.findByText('Debtor type');
    expect(heading.parentElement).toHaveTextContent('-');
    expect(screen.queryByText(/^Project cash$/)).not.toBeInTheDocument();
  });
});
