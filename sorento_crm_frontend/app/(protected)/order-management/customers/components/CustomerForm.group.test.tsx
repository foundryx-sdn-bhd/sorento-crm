/**
 * CustomerForm - the "Group" select (CUSTOMER-GROUP S3, AC-20).
 *
 * A clearable SearchableSelect (accessible name "Group") fed by
 * `searchCustomerGroupsSelect(query)` -> [{ id, name, ledger_count }]. Saving sends
 * `customer_group_id` (string id, or an explicit null when cleared: the update is exclude_unset).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const getCustomer = vi.fn();
const createCustomer = vi.fn();
const updateCustomer = vi.fn();
vi.mock('../services/customerService', () => ({
  getCustomer: (...a: unknown[]) => getCustomer(...a),
  createCustomer: (...a: unknown[]) => createCustomer(...a),
  updateCustomer: (...a: unknown[]) => updateCustomer(...a),
  getCustomers: vi.fn(),
  deleteCustomer: vi.fn(),
  getCustomerSalesAgentsSelect: vi.fn().mockResolvedValue([]),
}));

const searchCustomerGroupsSelect = vi.fn();
vi.mock('../../customer-groups/services/customerGroupService', () => ({
  searchCustomerGroupsSelect: (...a: unknown[]) => searchCustomerGroupsSelect(...a),
  getCustomerGroups: vi.fn(),
  getCustomerGroup: vi.fn(),
  getCustomerGroupCustomers: vi.fn().mockResolvedValue({ data: [], pagination: { total: 0 } }),
}));

import CustomerForm from './CustomerForm';

const CUSTOMER = {
  id: 'c-1',
  customer_code: '300-H030',
  customer_name: 'HANLIM TRADING SDN BHD [A/C I]',
  email: null,
  phone_number: null,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  sales_agent_id: null,
  account_level: 1,
  customer_group_id: 'grp-1',
  customer_group_name: 'HANLIM TRADING SDN BHD',
};

function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  updateCustomer.mockResolvedValue({ ...CUSTOMER });
  searchCustomerGroupsSelect.mockResolvedValue([
    { id: 'grp-1', name: 'HANLIM TRADING SDN BHD', ledger_count: 6 },
    { id: 'grp-2', name: 'JUBIN BMS SDN BHD', ledger_count: 13 },
  ]);
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('CustomerForm - Group', () => {
  it('preselects the current group by name', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER });
    render(<CustomerForm customerId="c-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Group' });
    await waitFor(() => expect(combo).toHaveTextContent('HANLIM TRADING SDN BHD'));
  });

  it('sends the picked group id', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER, customer_group_id: null, customer_group_name: null });
    render(<CustomerForm customerId="c-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Group' });
    fireEvent.click(combo);
    fireEvent.click(await screen.findByRole('option', { name: /JUBIN BMS SDN BHD/ }));
    fireEvent.click(screen.getByRole('button', { name: /update customer/i }));

    await waitFor(() => expect(updateCustomer).toHaveBeenCalled());
    expect(updateCustomer).toHaveBeenCalledWith(
      'c-1',
      expect.objectContaining({ customer_group_id: 'grp-2' }),
    );
  });

  it('is clearable and sends an explicit null when cleared', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER });
    render(<CustomerForm customerId="c-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Group' });
    await waitFor(() => expect(combo).toHaveTextContent('HANLIM TRADING SDN BHD'));
    fireEvent.pointerDown(within(combo).getByRole('button', { name: 'Clear selection' }));
    fireEvent.click(screen.getByRole('button', { name: /update customer/i }));

    await waitFor(() => expect(updateCustomer).toHaveBeenCalled());
    expect(updateCustomer).toHaveBeenCalledWith(
      'c-1',
      expect.objectContaining({ customer_group_id: null }),
    );
  });
});
