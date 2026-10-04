/**
 * L5 (NEVER-STUCK-UI S3, "Pickers / selects"): a shared select hook never turns a failed
 * read into data.
 *
 * Before: the four master-data select hooks toasted and returned `[]` (so `isError` was
 * never true and every picker read as "nothing to choose"), and the role select hook
 * toasted and returned the error BODY as data (cached for an hour, then `.map` on an
 * object threw on the Users page, audit row C1 / T13). Now each throws the backend's own
 * message, which the picker renders as "no access" or "could not load, retry".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { apiFetchMock, toastError } = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }));
vi.mock('@/lib/toast', () => ({ toast: { error: toastError, custom: vi.fn() } }));

import { useUOMSelectQuery } from './use-uom-select-query';
import { useBrandSelectQuery } from './use-brand-select-query';
import { useProductCategorySelectQuery } from './use-product-category-select-query';
import { useCountrySelectQuery } from './use-country-select-query';
import { useRoleSelectQuery } from '@/app/(protected)/user-management/roles/hooks/use-role-select-query';

function refusal(): Response {
  return new Response(JSON.stringify({ detail: 'Permission required: master_data.brands.view' }), {
    status: 403,
    headers: { 'Content-Type': 'application/json' },
  });
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  apiFetchMock.mockReset();
  toastError.mockReset();
  apiFetchMock.mockImplementation(async () => refusal());
});

describe.each([
  ['useUOMSelectQuery', useUOMSelectQuery],
  ['useBrandSelectQuery', useBrandSelectQuery],
  ['useProductCategorySelectQuery', useProductCategorySelectQuery],
  ['useCountrySelectQuery', useCountrySelectQuery],
  ['useRoleSelectQuery', useRoleSelectQuery],
])('%s', (_name, useHook) => {
  it('a refused read is an error carrying the backend message, never [] or the body', async () => {
    const { result } = renderHook(() => (useHook as () => ReturnType<typeof useUOMSelectQuery>)(), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
    expect((result.current.error as Error).message).toBe(
      'Permission required: master_data.brands.view',
    );
    // The hook no longer raises its own generic toast; the shared query toast speaks once.
    expect(toastError).not.toHaveBeenCalled();
  });
});
