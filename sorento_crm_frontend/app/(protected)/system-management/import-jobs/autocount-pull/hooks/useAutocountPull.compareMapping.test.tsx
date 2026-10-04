/** useCompareMappings / useSaveCompareMapping - DO-COMPARE-SIM red tests (AC-CMM-13). */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getCompareMappings = vi.fn();
const saveCompareMapping = vi.fn();
vi.mock('../services/autocountPullService', () => ({
  getCompareMappings: (...a: unknown[]) => getCompareMappings(...a),
  saveCompareMapping: (...a: unknown[]) => saveCompareMapping(...a),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('@/lib/toast', () => ({ toast }));

import { useCompareMappings, useSaveCompareMapping } from './useAutocountPull';

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  return { client, wrapper };
}

beforeEach(() => {
  getCompareMappings.mockReset();
  saveCompareMapping.mockReset();
  Object.values(toast).forEach((f) => f.mockReset());
});

describe('compare mapping hooks', () => {
  it('useCompareMappings returns the service payload', async () => {
    getCompareMappings.mockResolvedValue({ items: [{ kind: 'order_listing' }] });
    const { wrapper } = setup();
    const { result } = renderHook(() => useCompareMappings(), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual({ items: [{ kind: 'order_listing' }] }));
  });

  it('useSaveCompareMapping saves, toasts success and refetches the mappings', async () => {
    getCompareMappings.mockResolvedValue({ items: [] });
    saveCompareMapping.mockResolvedValue({ kind: 'order_listing' });
    const { wrapper } = setup();
    const { result } = renderHook(
      () => ({ q: useCompareMappings(), m: useSaveCompareMapping() }),
      { wrapper },
    );
    await waitFor(() => expect(getCompareMappings).toHaveBeenCalledTimes(1));
    const body = { sheet_name: 'Master', columns: [] };
    await act(async () => {
      await result.current.m.mutateAsync({ kind: 'order_listing', body });
    });
    expect(saveCompareMapping).toHaveBeenCalledWith('order_listing', body);
    expect(toast.success).toHaveBeenCalled();
    await waitFor(() => expect(getCompareMappings).toHaveBeenCalledTimes(2));
  });

  it('a failed save toasts the extracted message', async () => {
    getCompareMappings.mockResolvedValue({ items: [] });
    saveCompareMapping.mockRejectedValue(new Error('Map doc_no.'));
    const { wrapper } = setup();
    const { result } = renderHook(() => useSaveCompareMapping(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ kind: 'order_listing', body: { sheet_name: 'M', columns: [] } }).catch(() => {});
    });
    expect(toast.error).toHaveBeenCalledWith('Map doc_no.');
  });
});
