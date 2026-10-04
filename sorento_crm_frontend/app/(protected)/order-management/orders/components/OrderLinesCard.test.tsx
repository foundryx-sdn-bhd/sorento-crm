/**
 * M5-06 - the delivery order lines table renders on DataGrid instead of a
 * raw `<Table>`.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/order-management/orders/o-1',
}));

vi.mock('../hooks/useOrders', () => ({
  useBulkDeleteOrderLines: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateOrderLine: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('../services/orderService', () => ({
  importDeliveryOrderDetail: vi.fn(),
}));

vi.mock('./OrderLineDeleteDialog', () => ({
  __esModule: true,
  default: () => null,
}));

const LINES = [
  {
    id: 'l-1',
    order_id: 'o-1',
    product_id: 'p-1',
    warehouse_id: 'w-1',
    quantity: 5,
    unit_price: 10,
    total: 50,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    product: { id: 'p-1', product_code: 'SKU-1', product_name: 'Widget A' },
    warehouse: { id: 'w-1', warehouse_code: 'WH-KL', warehouse_name: 'KL' },
  },
  {
    id: 'l-2',
    order_id: 'o-1',
    product_id: 'p-2',
    warehouse_id: 'w-2',
    quantity: 2,
    unit_price: 20,
    total: 40,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    product: { id: 'p-2', product_code: 'SKU-2', product_name: 'Widget B' },
    warehouse: { id: 'w-2', warehouse_code: 'WH-PG', warehouse_name: 'Penang' },
  },
];

import OrderLinesCard from './OrderLinesCard';

function renderCard(readOnly = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OrderLinesCard orderId="o-1" lines={LINES} readOnly={readOnly} />
    </QueryClientProvider>,
  );
}

describe('OrderLinesCard - DataGrid', () => {
  it('renders the column headers and a real cell value for each line', () => {
    renderCard();

    expect(screen.getByText('Product')).toBeInTheDocument();
    expect(screen.getByText('Warehouse')).toBeInTheDocument();
    expect(screen.getByText('Qty')).toBeInTheDocument();

    expect(screen.getByText('SKU-1 - Widget A')).toBeInTheDocument();
    expect(screen.getByText('SKU-2 - Widget B')).toBeInTheDocument();
    expect(screen.getByText('WH-KL - KL')).toBeInTheDocument();
  });
});

// DO-OWNERSHIP-GUARD AC-OG15: an AutoCount-owned DO's lines come only from AutoCount.
describe('OrderLinesCard - AutoCount-owned DO', () => {
  it('offers add, select and delete on an ordinary DO', () => {
    renderCard();
    expect(screen.getByRole('button', { name: /Add line/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Delete line/i }).length).toBe(2);
    expect(screen.queryByText('From AutoCount')).not.toBeInTheDocument();
  });

  it('renders the lines read-only with the From AutoCount hint', () => {
    renderCard(true);
    expect(screen.getByText('SKU-1 - Widget A')).toBeInTheDocument();
    expect(screen.getByText('From AutoCount')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add line/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Delete/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });
});
