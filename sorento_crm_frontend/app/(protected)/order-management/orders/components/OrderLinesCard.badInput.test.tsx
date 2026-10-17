/** NUMBER-FIELDS round 3 (low-harm): bad input in an optional figure is never saved as blank. */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { typeBadInput } from '@/test-utils/badInput';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('next/navigation', () => ({ usePathname: () => '/order-management/orders/o-1' }));

const createLine = vi.hoisted(() => vi.fn());
vi.mock('../hooks/useOrders', () => ({
  useBulkDeleteOrderLines: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateOrderLine: () => ({ mutateAsync: createLine, isPending: false }),
}));
vi.mock('../services/orderService', () => ({ importDeliveryOrderDetail: vi.fn() }));
vi.mock('./OrderLineDeleteDialog', () => ({ __esModule: true, default: () => null }));

import OrderLinesCard from './OrderLinesCard';

// Quantity first, then the six optional figures in screen order.
const FIGURES = ['Unit price', 'Discount', 'Total', 'Tax', 'Total (excl)', 'Total (incl)'];

describe('OrderLinesCard add line, bad number input', () => {
  it.each(FIGURES.map((name, i) => [name, i + 1] as const))(
    '%s: Add is refused with the sentence and nothing is sent',
    (_name, index) => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <OrderLinesCard orderId="o-1" lines={[]} readOnly={false} />
        </QueryClientProvider>,
      );
      fireEvent.click(screen.getByRole('button', { name: /Add line/i }));
      const dialog = screen.getByRole('dialog');
      const [product, warehouse] = within(dialog).getAllByPlaceholderText('UUID');
      fireEvent.change(product, { target: { value: 'p-1' } });
      fireEvent.change(warehouse, { target: { value: 'w-1' } });
      const boxes = within(dialog).getAllByRole('spinbutton');
      fireEvent.change(boxes[0], { target: { value: '3' } });
      typeBadInput(boxes[index]);
      fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
      expect(within(dialog).getByText(/Enter a number/)).toBeInTheDocument();
      expect(createLine).not.toHaveBeenCalled();
    },
  );
});
