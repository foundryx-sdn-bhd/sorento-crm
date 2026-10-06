/**
 * MENU-PERM-GATE (PLAN-menu-perm-gate-06oct.md): the superadmin flag follows the EFFECTIVE
 * user. Impersonation swaps the backend principal but never the NextAuth session, so the
 * admin's own `roleName` must not decide while a view-as is active.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

const sessionRef: {
  data: unknown;
  status: 'loading' | 'authenticated' | 'unauthenticated';
} = {
  data: null,
  status: 'authenticated',
};

vi.mock('next-auth/react', () => ({
  useSession: () => sessionRef,
}));

import { impersonationStore } from '@/lib/impersonation-store';
import { useEffectiveIsSuperadmin } from './useEffectiveIsSuperadmin';

const ADMIN = {
  user: {
    id: 'admin-1',
    name: 'Admin',
    email: 'admin@x.test',
    roleName: 'Super Admin',
    status: 'ACTIVE',
  },
};
const VIEWER = {
  user: {
    id: 'u-2',
    name: 'Viewer',
    email: 'v@x.test',
    roleName: 'Retail Salesperson',
    status: 'ACTIVE',
  },
};

function viewAs(roleName: string | null) {
  impersonationStore.setSession({
    sessionId: 'imp-1',
    startedAt: '2026-10-06T00:00:00Z',
    targetUser: {
      id: 'u-9',
      name: 'Target',
      email: 't@x.test',
      avatar: null,
      role_name: roleName,
    },
  });
}

describe('useEffectiveIsSuperadmin', () => {
  beforeEach(() => {
    impersonationStore.setSession(null);
    sessionRef.status = 'authenticated';
    sessionRef.data = ADMIN;
  });
  afterEach(() => {
    impersonationStore.setSession(null);
  });

  it('is null while the session is loading', () => {
    sessionRef.status = 'loading';
    sessionRef.data = null;
    const { result } = renderHook(() => useEffectiveIsSuperadmin());
    expect(result.current).toBeNull();
  });

  it('is true for an admin who is not impersonating (unchanged behaviour)', () => {
    const { result } = renderHook(() => useEffectiveIsSuperadmin());
    expect(result.current).toBe(true);
  });

  it('is false for a non-admin who is not impersonating', () => {
    sessionRef.data = VIEWER;
    const { result } = renderHook(() => useEffectiveIsSuperadmin());
    expect(result.current).toBe(false);
  });

  it('is false for an admin impersonating a retail salesperson', () => {
    viewAs('retail_salesperson');
    const { result } = renderHook(() => useEffectiveIsSuperadmin());
    expect(result.current).toBe(false);
  });

  it('is false for an admin impersonating a target with no role at all', () => {
    viewAs(null);
    const { result } = renderHook(() => useEffectiveIsSuperadmin());
    expect(result.current).toBe(false);
  });

  it('never widens: a non-admin with a forged superadmin target in the store stays false', () => {
    sessionRef.data = VIEWER;
    viewAs('superadmin');
    const { result } = renderHook(() => useEffectiveIsSuperadmin());
    expect(result.current).toBe(false);
  });

  it('follows the store: true again once the view-as ends', () => {
    viewAs('retail_salesperson');
    const { result } = renderHook(() => useEffectiveIsSuperadmin());
    expect(result.current).toBe(false);
    impersonationStore.setSession(null);
    const { result: after } = renderHook(() => useEffectiveIsSuperadmin());
    expect(after.current).toBe(true);
  });
});
