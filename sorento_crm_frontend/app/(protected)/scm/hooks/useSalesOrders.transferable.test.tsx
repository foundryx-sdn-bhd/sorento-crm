/**
 * SO-TRANSFERABLE: the list hook and the detail pager both carry the Transferable filter
 * down to `getSalesOrders`. The grid test mocks this hook, so without this suite a dropped
 * argument here would show the filter chip while fetching the whole book.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getSalesOrders = vi.fn();
vi.mock('../services/salesOrderService', () => ({
  getSalesOrders: (...a: unknown[]) => getSalesOrders(...a),
  getSalesOrder: vi.fn(),
  getSalesOrderAgents: vi.fn(),
  createSalesOrder: vi.fn(),
  updateSalesOrder: vi.fn(),
  deleteSalesOrder: vi.fn(),
  createDoFromSalesOrder: vi.fn(),
  resetSalesOrderPlanning: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { salesOrdersPagerQuery, useSalesOrders } from './useSalesOrders';

const EMPTY = { data: [], empty: true, pagination: { total: 0, page: 1, limit: 25 } };

beforeEach(() => {
  getSalesOrders.mockReset().mockResolvedValue(EMPTY);
});

describe('useSalesOrders - transferable', () => {
  it('passes the filter to the list fetch', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    renderHook(
      () =>
        useSalesOrders({
          pageIndex: 0,
          pageSize: 25,
          sorting: [],
          searchQuery: '',
          status: null,
          priority: null,
          transferable: 'no',
        }),
      { wrapper },
    );
    await waitFor(() => expect(getSalesOrders).toHaveBeenCalled());
    expect(getSalesOrders.mock.calls[0][0]).toMatchObject({ transferable: 'no' });
  });

  it('the detail pager reads it back off the URL', async () => {
    await salesOrdersPagerQuery.fetchPage({
      pageIndex: 0,
      pageSize: 25,
      sorting: [],
      searchQuery: '',
      filters: { transferable: 'unknown' },
    } as never);
    expect(getSalesOrders.mock.calls[0][0]).toMatchObject({ transferable: 'unknown' });
  });
});
