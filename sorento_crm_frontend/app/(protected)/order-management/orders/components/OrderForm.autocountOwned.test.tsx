import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';

import OrderForm from './OrderForm';

/**
 * DO-OWNERSHIP-GUARD AC-OG15: on an AutoCount-owned DO the edit form shows AutoCount's
 * fields read-only with a "From AutoCount" hint, keeps Order Tracking fields editable, and
 * leaves AutoCount's fields out of the update it sends.
 */

const useOrder = vi.fn();
const useOrderStatusSelectQuery = vi.fn();
const updateMutation = { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false };
const createMutation = { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false };

vi.mock('../hooks/useOrders', () => ({
  useOrder: (...a: unknown[]) => useOrder(...a),
  useCreateOrder: () => createMutation,
  useUpdateOrder: () => updateMutation,
}));

vi.mock('../../shared/hooks/use-order-status-select-query', () => ({
  useOrderStatusSelectQuery: (...a: unknown[]) => useOrderStatusSelectQuery(...a),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

const AUTOCOUNT_FIELDS = [
  'agent', 'customer_id', 'debtor_code', 'debtor_name', 'discount_amount', 'is_cancelled',
  'order_date', 'order_number', 'remarks', 'subtotal_amount', 'tax_amount', 'total_amount',
];

function mockOrder(overrides: Record<string, unknown>) {
  useOrder.mockReturnValue({
    data: {
      id: 'o-1',
      order_number: 'DO-AC-0001',
      order_date: '2026-09-27',
      order_status_id: 's-1',
      debtor_code: '300-AC',
      debtor_name: 'AutoCount Debtor',
      agent: 'AC-AGENT',
      driver_name: 'Ali',
      subtotal_amount: 100,
      discount_amount: 0,
      tax_amount: 6,
      total_amount: 106,
      ...overrides,
    },
    isLoading: false,
  });
  useOrderStatusSelectQuery.mockReturnValue({ data: [] });
}

async function renderForm() {
  render(<OrderForm orderId="o-1" onSuccess={() => {}} />);
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  useOrder.mockReset();
  useOrderStatusSelectQuery.mockReset();
  updateMutation.mutateAsync.mockReset();
});

describe('OrderForm on an AutoCount-owned DO', () => {
  it('disables AutoCount fields with the From AutoCount hint', async () => {
    mockOrder({ autocount_owned_fields: AUTOCOUNT_FIELDS });
    await renderForm();
    const debtor = await waitFor(() => screen.getByDisplayValue('AutoCount Debtor'));
    expect(debtor).toBeDisabled();
    expect(screen.getByDisplayValue('AC-AGENT')).toBeDisabled();
    expect(screen.getByDisplayValue('2026-09-27')).toBeDisabled();
    expect(screen.getAllByText('From AutoCount').length).toBeGreaterThan(0);
  });

  it('keeps Order Tracking fields editable and sends only them', async () => {
    mockOrder({ autocount_owned_fields: AUTOCOUNT_FIELDS });
    await renderForm();
    const tab = screen.getByRole('tab', { name: /Delivery & Tracking/i });
    fireEvent.mouseDown(tab, { button: 0, ctrlKey: false });
    const driver = await waitFor(() => screen.getByDisplayValue('Ali'));
    expect(driver).not.toBeDisabled();
    fireEvent.change(driver, { target: { value: 'Bala' } });
    fireEvent.click(screen.getByRole('button', { name: /Update Delivery Order/i }));
    await waitFor(() => expect(updateMutation.mutateAsync).toHaveBeenCalledTimes(1));
    const sent = updateMutation.mutateAsync.mock.calls[0][0].data as Record<string, unknown>;
    expect(sent.driver_name).toBe('Bala');
    for (const key of AUTOCOUNT_FIELDS) expect(sent).not.toHaveProperty(key);
  });

  it('leaves an ordinary DO fully editable', async () => {
    mockOrder({ autocount_owned_fields: [] });
    await renderForm();
    const debtor = await waitFor(() => screen.getByDisplayValue('AutoCount Debtor'));
    expect(debtor).not.toBeDisabled();
    expect(screen.queryByText('From AutoCount')).not.toBeInTheDocument();
  });
});
