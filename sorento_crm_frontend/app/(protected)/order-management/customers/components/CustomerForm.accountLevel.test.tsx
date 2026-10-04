/**
 * CustomerForm - the "Account level" field (ACCOUNT-LEDGER, AC-1).
 *
 * A clearable SearchableSelect in Basic Information, options "Account 1" .. "Account 9" with
 * the values 1..9. Saving sends `account_level` as a NUMBER, or an explicit `null` when none
 * is set: `CustomerUpdate` is `exclude_unset`, so an omitted key would silently keep the old level.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
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

import CustomerForm from './CustomerForm';

const CUSTOMER = {
  id: 'cust-1',
  customer_code: 'C-001',
  customer_name: 'Alpha Trading',
  email: null,
  phone_number: null,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  sales_agent_id: null,
  account_level: 2,
};

function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  updateCustomer.mockResolvedValue({ ...CUSTOMER });
  createCustomer.mockResolvedValue({ ...CUSTOMER, id: 'cust-2' });
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('CustomerForm - Account level', () => {
  it('preselects the current level as "Account 2"', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER });
    render(<CustomerForm customerId="cust-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Account level' });
    await waitFor(() => expect(combo).toHaveTextContent('Account 2'));
  });

  it('offers Account 1 to Account 9, nothing else', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER });
    render(<CustomerForm customerId="cust-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Account level' });
    fireEvent.click(combo);

    for (let n = 1; n <= 9; n += 1) {
      expect(await screen.findByRole('option', { name: `Account ${n}` })).toBeInTheDocument();
    }
    expect(screen.queryByRole('option', { name: 'Account 10' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Account 0' })).not.toBeInTheDocument();
  });

  it('sends the picked level as a number', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER, account_level: null });
    render(<CustomerForm customerId="cust-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Account level' });
    fireEvent.click(combo);
    fireEvent.click(await screen.findByRole('option', { name: 'Account 3' }));
    fireEvent.click(screen.getByRole('button', { name: /update customer/i }));

    await waitFor(() => expect(updateCustomer).toHaveBeenCalled());
    expect(updateCustomer).toHaveBeenCalledWith(
      'cust-1',
      expect.objectContaining({ account_level: 3 }),
    );
    const sent = updateCustomer.mock.calls[0][1] as { account_level: unknown };
    expect(typeof sent.account_level).toBe('number');
  });

  it('sends an explicit null when the level is cleared', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER });
    render(<CustomerForm customerId="cust-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Account level' });
    await waitFor(() => expect(combo).toHaveTextContent('Account 2'));

    fireEvent.pointerDown(within(combo).getByRole('button', { name: 'Clear selection' }));
    fireEvent.click(screen.getByRole('button', { name: /update customer/i }));

    await waitFor(() => expect(updateCustomer).toHaveBeenCalled());
    expect(updateCustomer).toHaveBeenCalledWith(
      'cust-1',
      expect.objectContaining({ account_level: null }),
    );
  });

  it('a new customer with no level picked submits null, not an unset field', async () => {
    render(<CustomerForm />);

    fireEvent.change(screen.getByPlaceholderText('CUST-001'), { target: { value: 'C-999' } });
    fireEvent.change(screen.getByPlaceholderText('Enter customer name'), {
      target: { value: 'Beta Supplies' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create customer/i }));

    await waitFor(() => expect(createCustomer).toHaveBeenCalled());
    expect(createCustomer).toHaveBeenCalledWith(expect.objectContaining({ account_level: null }));
  });
});
