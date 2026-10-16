/** SERIES-BRAND-SCOPE A1/A4/A6: the category picker can be narrowed to one brand. */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { apiFetchMock } = vi.hoisted(() => ({ apiFetchMock: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }));
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), custom: vi.fn() } }));

import { useProductCategorySelectQuery } from './use-product-category-select-query';

const ok = () =>
  new Response(JSON.stringify([{ id: 'c1', category_name: 'Basins' }]), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

describe('useProductCategorySelectQuery', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    apiFetchMock.mockImplementation(async () => ok());
  });

  it('requests the brand-filtered URL when a brand is given', async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useProductCategorySelectQuery('b-1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/v1/master-data/product-categories/select?brand_id=b-1',
    );
  });

  it('keeps the old URL without a brand', async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useProductCategorySelectQuery(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetchMock).toHaveBeenCalledWith('/api/v1/master-data/product-categories/select');
  });

  it('refetches when the brand changes (own cache entry per brand)', async () => {
    const { client, wrapper } = setup();
    const { result, rerender } = renderHook(
      ({ brand }: { brand?: string }) => useProductCategorySelectQuery(brand),
      { wrapper, initialProps: { brand: 'b-1' } as { brand?: string } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    rerender({ brand: 'b-2' });
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        '/api/v1/master-data/product-categories/select?brand_id=b-2',
      ),
    );
    const keys = client
      .getQueryCache()
      .getAll()
      .map((q) => JSON.stringify(q.queryKey));
    expect(keys.some((k) => k.includes('b-1'))).toBe(true);
    expect(keys.some((k) => k.includes('b-2'))).toBe(true);
  });
});
