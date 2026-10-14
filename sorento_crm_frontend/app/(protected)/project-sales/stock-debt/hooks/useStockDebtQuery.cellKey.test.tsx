/**
 * AC-F2: the cell drill's query key carries the picked locations, so two different location
 * sets are two fetches and never one cached answer.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const getStockDebtCell = vi.fn();
vi.mock('../services/stockDebtService', () => ({
  getStockDebtList: vi.fn(),
  getStockDebtCell: (...args: unknown[]) => getStockDebtCell(...args),
  exportStockDebt: vi.fn(),
}));

import { useStockDebtCellQuery } from './useStockDebtQuery';

describe('useStockDebtCellQuery', () => {
  it('fetches again when the picked locations change', async () => {
    getStockDebtCell.mockResolvedValue({ demand: [], supply: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { rerender } = renderHook(
      ({ groups }: { groups: string[] }) =>
        useStockDebtCellQuery('p1', '2026-10', '', '', 'all', false, groups),
      { wrapper, initialProps: { groups: ['BB'] } },
    );
    await waitFor(() => expect(getStockDebtCell).toHaveBeenCalledTimes(1));

    rerender({ groups: ['ED'] });
    await waitFor(() => expect(getStockDebtCell).toHaveBeenCalledTimes(2));
    expect(getStockDebtCell.mock.calls[1][6]).toEqual(['ED']);
  });
});
