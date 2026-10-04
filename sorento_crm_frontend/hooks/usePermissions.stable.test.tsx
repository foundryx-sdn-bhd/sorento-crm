/**
 * SESSION-NEVER-STUCK: the literal freeze.
 *
 * `usePermissions` returned `data: permissions = []`, a NEW array on every render while
 * the permissions were not there (loading, or failed because the session died). The
 * universal search dialog's effect depends on `permissions` and sets state each run, so
 * it re-ran on every render, forever. After a click React flushes that update in a
 * microtask, the loop never yields, and the page's main thread freezes: the tab
 * highlights and the page never changes (the owner's report, reproduced in the sandbox
 * with the debugger paused inside `collectMenuItems`).
 */
import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

vi.mock('next-auth/react', () => ({
  useSession: () => ({ status: 'authenticated', data: { user: { id: 'u-1' } } }),
}));
vi.mock('@/lib/permissions-service', () => ({
  fetchMyPermissions: vi.fn(() => Promise.reject(new Error('Failed to fetch permissions'))),
}));

import { usePermissions } from './usePermissions';

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('usePermissions while permissions are missing', () => {
  it('returns the SAME empty array and set on every render (no effect loop)', async () => {
    const { result, rerender } = renderHook(() => usePermissions(), { wrapper });
    await vi.waitFor(() => expect(result.current.error).toBeTruthy());

    const first = result.current;
    rerender();
    rerender();

    expect(result.current.permissions).toEqual([]);
    expect(result.current.permissions).toBe(first.permissions);
    expect(result.current.permissionSet).toBe(first.permissionSet);
  });
});
