/**
 * MENU-PERM-GATE (PLAN-menu-perm-gate-06oct.md, AC-5): `<RequireAccess superadmin>` judges
 * the EFFECTIVE user. An admin viewing as a non-admin is denied; the same admin, not
 * impersonating, is let through as before.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

const sessionRef: { data: unknown; status: 'loading' | 'authenticated' } = {
  data: null,
  status: 'authenticated',
};
vi.mock('next-auth/react', () => ({ useSession: () => sessionRef }));

vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));
vi.mock('@/components/common/screen-loader', () => ({
  ScreenLoader: () => <div>loading-access</div>,
}));

import { impersonationStore } from '@/lib/impersonation-store';
import RequireAccess from './RequireAccess';

const ADMIN = {
  user: {
    id: 'admin-1',
    name: 'Admin',
    email: 'admin@x.test',
    roleName: 'Super Admin',
    status: 'ACTIVE',
  },
};

function viewAsRetailSalesperson() {
  impersonationStore.setSession({
    sessionId: 'imp-1',
    startedAt: '2026-10-06T00:00:00Z',
    targetUser: {
      id: 'u-9',
      name: 'Retail One',
      email: 'r@x.test',
      avatar: null,
      role_name: 'retail_salesperson',
    },
  });
}

beforeEach(() => {
  sessionRef.status = 'authenticated';
  sessionRef.data = ADMIN;
  impersonationStore.setSession(null);
});
afterEach(() => {
  impersonationStore.setSession(null);
  cleanup();
});

describe('RequireAccess superadmin during impersonation (MENU-PERM-GATE)', () => {
  it('denies an admin who is viewing as a non-admin', () => {
    viewAsRetailSalesperson();
    render(
      <RequireAccess superadmin>
        <div>page body</div>
      </RequireAccess>,
    );
    expect(screen.getByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByText('page body')).toBeNull();
  });

  it('lets the same admin through when not impersonating', () => {
    render(
      <RequireAccess superadmin>
        <div>page body</div>
      </RequireAccess>,
    );
    expect(screen.getByText('page body')).toBeTruthy();
    expect(screen.queryByText("You don't have access to this page")).toBeNull();
  });

  it('shows the loader, not the denied state, while the session is still loading', () => {
    sessionRef.status = 'loading';
    sessionRef.data = null;
    render(
      <RequireAccess superadmin>
        <div>page body</div>
      </RequireAccess>,
    );
    expect(screen.getByText('loading-access')).toBeTruthy();
    expect(screen.queryByText("You don't have access to this page")).toBeNull();
  });
});
