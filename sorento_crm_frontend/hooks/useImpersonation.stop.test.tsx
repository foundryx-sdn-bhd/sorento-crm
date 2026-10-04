/**
 * SESSION-NEVER-STUCK: a deliberate Stop clears view-as BEFORE the request, so a query
 * answered while the server ends the row cannot raise "View-as ended" for a stop the
 * user just asked for. A failed stop puts the view-as back.
 */
import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { impersonationStore } from '@/lib/impersonation-store';

const stopImpersonation = vi.hoisted(() => vi.fn());
vi.mock('@/services/impersonationService', () => ({
  stopImpersonation,
  startImpersonation: vi.fn(),
  fetchCurrentImpersonation: vi.fn(async () => null),
}));

import { useImpersonation } from './useImpersonation';

const VIEW_AS = {
  sessionId: 's-1',
  startedAt: '2026-10-01T00:00:00',
  targetUser: { id: 'u-kx', name: 'Kah Xin', email: null, avatar: null, role_name: null },
};

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useImpersonation().stop', () => {
  beforeEach(() => {
    impersonationStore.setSession(VIEW_AS);
    stopImpersonation.mockReset();
  });

  it('clears view-as before the stop request is sent', async () => {
    let seenDuringCall: unknown = 'not called';
    stopImpersonation.mockImplementation(async () => {
      seenDuringCall = impersonationStore.getState();
    });
    const { result } = renderHook(() => useImpersonation(), { wrapper });

    await act(async () => {
      await result.current.stop();
    });

    expect(seenDuringCall).toBeNull();
    expect(impersonationStore.getState()).toBeNull();
  });

  it('a failed stop restores the view-as it cleared', async () => {
    stopImpersonation.mockRejectedValue(new Error('Failed to stop impersonation'));
    const { result } = renderHook(() => useImpersonation(), { wrapper });

    await act(async () => {
      await result.current.stop().catch(() => undefined);
    });

    expect(impersonationStore.getState()?.targetUser.id).toBe('u-kx');
  });
});
