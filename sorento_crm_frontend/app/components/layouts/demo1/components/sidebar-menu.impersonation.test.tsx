/**
 * MENU-PERM-GATE (PLAN-menu-perm-gate-06oct.md, AC-1..AC-3): the rendered sidebar while an
 * admin impersonates a user who holds only `ideation.board.view`. The permission set is the
 * target's (the backend answers `/users/me/permissions` for the impersonated user), the
 * NextAuth session is still the admin's. Nothing but Ideas may show, superadmin-only System
 * items included.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const sessionRef: { data: unknown; status: 'loading' | 'authenticated' } = {
  data: null,
  status: 'authenticated',
};
const permissionsRef: { set: Set<string> } = { set: new Set() };

vi.mock('next-auth/react', () => ({ useSession: () => sessionRef }));
vi.mock('next/navigation', () => ({ usePathname: () => '/' }));
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ permissionSet: permissionsRef.set, isLoading: false }),
  useHasAnyPermission: () => false,
}));
vi.mock('@/hooks/useTenantModules', () => ({
  useTenantModules: () => ({ enabledModuleKeys: null, isLoading: false }),
}));
vi.mock('@/hooks/usePrefetchOnce', () => ({ usePrefetchOnce: () => () => {} }));
vi.mock('@/app/(protected)/workflow-forms-management/hooks/useWorkflowForms', () => ({
  usePublishedWorkflowDefinitionsForSubmissionQuery: () => ({ data: undefined }),
}));
vi.mock('./quick-access-block', () => ({ QuickAccessBlock: () => null }));
vi.mock('./menu-item-pin-button', () => ({ MenuItemPinButton: () => null }));

import { impersonationStore } from '@/lib/impersonation-store';
import { MENU_SIDEBAR } from '@/config/menu.config';
import type { MenuConfig } from '@/config/types';
import { SidebarMenu } from './sidebar-menu';

const ADMIN = {
  user: { id: 'admin-1', name: 'Admin', email: 'admin@x.test', roleName: 'Super Admin', status: 'ACTIVE' },
};

function allSlugs(items: MenuConfig): Set<string> {
  const out = new Set<string>();
  for (const item of items) {
    if (item.permission) out.add(item.permission);
    for (const p of item.permissionsAny ?? []) out.add(p);
    if (item.children) for (const p of allSlugs(item.children)) out.add(p);
  }
  return out;
}

function viewAsRetailSalesperson() {
  impersonationStore.setSession({
    sessionId: 'imp-1',
    startedAt: '2026-10-06T00:00:00Z',
    targetUser: { id: 'u-9', name: 'Retail One', email: 'r@x.test', avatar: null, role_name: 'retail_salesperson' },
  });
}

/** Top-level group titles and leaf titles, as rendered (collapsed sub-trees are not mounted). */
const LEAKED_GROUPS = ['Marketing', 'Complaints', 'SLA', 'Forms', 'Resources', 'System', 'Users & Access'];

describe('SidebarMenu while impersonating (MENU-PERM-GATE)', () => {
  beforeEach(() => {
    sessionRef.status = 'authenticated';
    sessionRef.data = ADMIN;
    impersonationStore.setSession(null);
  });
  afterEach(() => {
    impersonationStore.setSession(null);
  });

  it('admin viewing as a user with only ideation.board.view sees Ideas and nothing else', () => {
    viewAsRetailSalesperson();
    permissionsRef.set = new Set(['ideation.board.view']);
    render(<SidebarMenu />);
    expect(screen.getByText('Ideas')).toBeInTheDocument();
    expect(screen.queryByText('Dashboards')).not.toBeInTheDocument();
    for (const title of LEAKED_GROUPS) {
      expect(screen.queryByText(title), title).not.toBeInTheDocument();
    }
    expect(screen.getByText('OVERVIEW')).toBeInTheDocument();
    for (const heading of ['SALES', 'SUPPLY CHAIN', 'CATALOGUE', 'OPERATIONS', 'ADMINISTRATION']) {
      expect(screen.queryByText(heading), heading).not.toBeInTheDocument();
    }
  });

  it('admin viewing as a target who holds every slug still loses the superadmin-only items', () => {
    viewAsRetailSalesperson();
    permissionsRef.set = allSlugs(MENU_SIDEBAR);
    render(<SidebarMenu />);
    // System stays: App Store, Messaging and the rest are permission-gated and held ...
    fireEvent.click(screen.getByText('System'));
    fireEvent.click(screen.getByText('Platform'));
    expect(screen.getByText('App Store')).toBeInTheDocument();
    // ... but Companies (superadminOnly) is gone, judged on the target, not the admin.
    expect(screen.queryByText('Companies')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Operations'));
    expect(screen.getByText('API Call Log')).toBeInTheDocument();
    expect(screen.queryByText('Import Jobs')).not.toBeInTheDocument();
    expect(screen.queryByText('Audit Logs')).not.toBeInTheDocument();
  });

  it('the same admin, not impersonating, sees every group and the superadmin-only items again', () => {
    permissionsRef.set = allSlugs(MENU_SIDEBAR);
    render(<SidebarMenu />);
    expect(screen.getByText('Dashboards')).toBeInTheDocument();
    for (const title of LEAKED_GROUPS) {
      expect(screen.getByText(title), title).toBeInTheDocument();
    }
    fireEvent.click(screen.getByText('System'));
    fireEvent.click(screen.getByText('Platform'));
    expect(screen.getByText('Companies')).toBeInTheDocument();
  });
});
