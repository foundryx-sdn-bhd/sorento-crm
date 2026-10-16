/**
 * RED tests - FORM-ROUTING-CONDITIONS DT-5 (CustomerForm).
 *
 * A clearable "Debtor type" SearchableSelect (Project / Project cash / Cash sales), same place
 * and pattern as Account level. Saving sends `debtor_type` as the value, or an explicit `null`
 * when cleared (`CustomerUpdate` is exclude_unset, so an omitted key would keep the old value).
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
  debtor_type: 'project_cash',
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

describe('CustomerForm - Debtor type', () => {
  it('preselects the current type as "Project cash"', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER });
    render(<CustomerForm customerId="cust-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Debtor type' });
    await waitFor(() => expect(combo).toHaveTextContent('Project cash'));
  });

  it('offers Project, Project cash and Cash sales, nothing else', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER });
    render(<CustomerForm customerId="cust-1" />);

    fireEvent.click(await screen.findByRole('combobox', { name: 'Debtor type' }));

    const labels = (await screen.findAllByRole('option')).map((o) => o.textContent?.trim());
    expect(labels).toEqual(['Project', 'Project cash', 'Cash sales']);
  });

  it('sends the picked value', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER, debtor_type: null });
    render(<CustomerForm customerId="cust-1" />);

    fireEvent.click(await screen.findByRole('combobox', { name: 'Debtor type' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Cash sales' }));
    fireEvent.click(screen.getByRole('button', { name: /update customer/i }));

    await waitFor(() => expect(updateCustomer).toHaveBeenCalled());
    expect(updateCustomer).toHaveBeenCalledWith(
      'cust-1',
      expect.objectContaining({ debtor_type: 'cash_sales' }),
    );
  });

  it('sends an explicit null when the type is cleared', async () => {
    getCustomer.mockResolvedValue({ ...CUSTOMER });
    render(<CustomerForm customerId="cust-1" />);

    const combo = await screen.findByRole('combobox', { name: 'Debtor type' });
    await waitFor(() => expect(combo).toHaveTextContent('Project cash'));

    fireEvent.pointerDown(within(combo).getByRole('button', { name: 'Clear selection' }));
    fireEvent.click(screen.getByRole('button', { name: /update customer/i }));

    await waitFor(() => expect(updateCustomer).toHaveBeenCalled());
    expect(updateCustomer).toHaveBeenCalledWith(
      'cust-1',
      expect.objectContaining({ debtor_type: null }),
    );
  });

  it('a new customer with no type picked submits null, not an unset field', async () => {
    render(<CustomerForm />);

    fireEvent.change(screen.getByPlaceholderText('CUST-001'), { target: { value: 'C-999' } });
    fireEvent.change(screen.getByPlaceholderText('Enter customer name'), {
      target: { value: 'Beta Supplies' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create customer/i }));

    await waitFor(() => expect(createCustomer).toHaveBeenCalled());
    expect(createCustomer).toHaveBeenCalledWith(expect.objectContaining({ debtor_type: null }));
  });
});
