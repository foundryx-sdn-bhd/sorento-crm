/**
 * AC-F2: the cell drill's query key carries the picked locations, so two different location
 * sets are two fetches and never one cached answer.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const getStockDebtCell = vi.fn();
const getStockDebtList = vi.fn();
vi.mock('../services/stockDebtService', () => ({
  getStockDebtList: (...args: unknown[]) => getStockDebtList(...args),
  getStockDebtCell: (...args: unknown[]) => getStockDebtCell(...args),
  exportStockDebt: vi.fn(),
}));

import { useStockDebtCellQuery, useStockDebtQuery } from './useStockDebtQuery';

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

describe('Location mode is part of the query keys (AC-X2)', () => {
  function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    return wrapper;
  }

  it('the list fetches again when only the mode flips', async () => {
    getStockDebtList.mockResolvedValue({ data: [] });
    const { rerender } = renderHook(
      ({ mode }: { mode: 'include' | 'exclude' }) =>
        useStockDebtQuery({
          pageIndex: 0, pageSize: 25, query: '', onlyDebt: true, book: 'all',
          supplierIds: [], dateFrom: '', dateTo: '', groups: ['ED'], groupsMode: mode,
        }),
      { wrapper: setup(), initialProps: { mode: 'include' as 'include' | 'exclude' } },
    );
    await waitFor(() => expect(getStockDebtList).toHaveBeenCalledTimes(1));

    rerender({ mode: 'exclude' });
    await waitFor(() => expect(getStockDebtList).toHaveBeenCalledTimes(2));
    expect(getStockDebtList.mock.calls[1][0]).toMatchObject({
      groups: ['ED'], groupsMode: 'exclude',
    });
  });

  it('the cell fetches again when only the mode flips', async () => {
    getStockDebtCell.mockClear();
    getStockDebtCell.mockResolvedValue({ demand: [], supply: [] });
    const { rerender } = renderHook(
      ({ mode }: { mode: 'include' | 'exclude' }) =>
        useStockDebtCellQuery('p1', '2026-10', '', '', 'all', false, ['ED'], mode),
      { wrapper: setup(), initialProps: { mode: 'include' as 'include' | 'exclude' } },
    );
    await waitFor(() => expect(getStockDebtCell).toHaveBeenCalledTimes(1));

    rerender({ mode: 'exclude' });
    await waitFor(() => expect(getStockDebtCell).toHaveBeenCalledTimes(2));
    expect(getStockDebtCell.mock.calls[1][6]).toEqual(['ED']);
    expect(getStockDebtCell.mock.calls[1][7]).toBe('exclude');
  });
});
