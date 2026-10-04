/**
 * `useContainerSizes({ enabled: false })` must not fetch: the packing list's Details tab
 * passes it for a user without SCM read, for whom `/scm/container-sizes` 403s and the
 * app-wide error toast would land on a tab that otherwise works (PL-TABS-ACCESS).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// A plain stub rather than `vi.fn()`, matching usePackingListSourceInvoices.test.tsx.
const service = { calls: 0 };
vi.mock('../services/fulfilmentService', () => ({
  getContainerSizes: async () => {
    service.calls += 1;
    return [
      {
        id: 'size-40hq',
        code: '40HQ',
        label: '40ft high cube',
        cbm: 65,
        is_default: true,
      },
    ];
  },
}));

import { useContainerSizes } from './useFulfilment';

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

beforeEach(() => {
  service.calls = 0;
});

describe('useContainerSizes', () => {
  it('reads the sizes by default', async () => {
    const { result } = renderHook(() => useContainerSizes(), {
      wrapper: wrapper(new QueryClient()),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(service.calls).toBe(1);
  });

  it('fetches nothing when told it may not', async () => {
    const { result } = renderHook(() => useContainerSizes({ enabled: false }), {
      wrapper: wrapper(new QueryClient()),
    });

    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.fetchStatus).toBe('idle');
    expect(service.calls).toBe(0);
  });
});
