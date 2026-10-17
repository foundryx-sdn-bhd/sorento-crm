/** NUMBER-FIELDS round 3 (S5): Trips with bad input shows the shared sentence, never zod's raw text. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { typeBadInput } from '@/test-utils/badInput';

import OrderForm from './OrderForm';

const useOrder = vi.fn();
const useOrderStatusSelectQuery = vi.fn();
const updateAsync = vi.fn();
const noopMutation = { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false };

vi.mock('../hooks/useOrders', () => ({
  useOrder: (...a: unknown[]) => useOrder(...a),
  useCreateOrder: () => noopMutation,
  useUpdateOrder: () => ({ mutate: vi.fn(), mutateAsync: updateAsync, isPending: false }),
}));
vi.mock('../../shared/hooks/use-order-status-select-query', () => ({
  useOrderStatusSelectQuery: (...a: unknown[]) => useOrderStatusSelectQuery(...a),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  useOrder.mockReturnValue({ data: { id: 'o-1', order_number: 'REPPS2605-0012', trips: 2 }, isLoading: false });
  useOrderStatusSelectQuery.mockReturnValue({ data: [] });
});

describe('OrderForm Trips bad number input (S5)', () => {
  it('shows the shared sentence, not zod raw text, and saves nothing', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<OrderForm orderId="o-1" />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    const tab = screen.getByRole('tab', { name: /Delivery & Tracking/i });
    fireEvent.mouseDown(tab, { button: 0, ctrlKey: false });
    fireEvent.click(tab);
    const trips = await screen.findByLabelText('Trips');
    typeBadInput(trips);
    fireEvent.blur(trips);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Update|Save/i }));
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByText(/Expected number, received nan/i)).toBeNull();
    expect(screen.getByText(/Enter a whole number of at least 0/)).toBeInTheDocument();
    expect(updateAsync).not.toHaveBeenCalled();
    const nanWarning = errors.mock.calls.some((c) => /Received NaN for the `value`/.test(String(c[0]) + String(c[1] ?? '')));
    expect(nanWarning).toBe(false);
    errors.mockRestore();
  });
});
