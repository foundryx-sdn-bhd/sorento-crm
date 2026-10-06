/**
 * MENU-PERM-GATE (PLAN-menu-perm-gate-06oct.md): the real MENU_SIDEBAR through the access
 * filter the sidebar applies. These run against the shipped config on purpose, so a future
 * entry added without a `permission` fails the first test instead of leaking into every
 * user's sidebar.
 */
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR } from '@/config/menu.config';
import type { MenuConfig } from '@/config/types';
import { applyMenuAccess } from './menu-access-filter';

/** Every leaf path in a menu tree, in order. */
function collectPaths(items: MenuConfig): string[] {
  const out: string[] = [];
  for (const item of items) {
    if (item.path) out.push(item.path);
    if (item.children) out.push(...collectPaths(item.children));
  }
  return out;
}

/** Every slug any entry of the tree names, so a holder of all of them is "all permissions". */
function collectSlugs(items: MenuConfig): Set<string> {
  const out = new Set<string>();
  for (const item of items) {
    if (item.permission) out.add(item.permission);
    for (const p of item.permissionsAny ?? []) out.add(p);
    if (item.children) for (const p of collectSlugs(item.children)) out.add(p);
  }
  return out;
}

/** Every leaf path under a `superadminOnly` entry. */
function collectSuperadminOnlyPaths(items: MenuConfig): string[] {
  const out: string[] = [];
  for (const item of items) {
    if (item.superadminOnly) out.push(...collectPaths([item]));
    else if (item.children) out.push(...collectSuperadminOnlyPaths(item.children));
  }
  return out;
}

const LOADED = { isLoading: false, enabledModuleKeys: null, modulesLoading: false } as const;

describe('applyMenuAccess on the real MENU_SIDEBAR', () => {
  it('a user holding only ideation.board.view sees exactly Ideas, under OVERVIEW alone', () => {
    const menu = applyMenuAccess(MENU_SIDEBAR, {
      ...LOADED,
      isSuperadmin: false,
      permissionSet: new Set(['ideation.board.view']),
    });
    expect(collectPaths(menu)).toEqual(['/ideas']);
    expect(menu.filter((i) => i.heading).map((i) => i.heading)).toEqual(['OVERVIEW']);
  });

  it('a non-superadmin holding every slug never sees a superadminOnly entry', () => {
    const menu = applyMenuAccess(MENU_SIDEBAR, {
      ...LOADED,
      isSuperadmin: false,
      permissionSet: collectSlugs(MENU_SIDEBAR),
    });
    const visible = collectPaths(menu);
    const superadminOnly = collectSuperadminOnlyPaths(MENU_SIDEBAR);
    expect(superadminOnly).toContain('/system-management/companies');
    expect(superadminOnly).toContain('/system-management/import-jobs');
    for (const p of superadminOnly) expect(visible).not.toContain(p);
    // The permission-gated neighbours in the same groups stay.
    expect(visible).toContain('/system-management/app-store');
    expect(visible).toContain('/integration-management/integrations');
  });

  it('a superadmin holding every slug sees every path', () => {
    const menu = applyMenuAccess(MENU_SIDEBAR, {
      ...LOADED,
      isSuperadmin: true,
      permissionSet: collectSlugs(MENU_SIDEBAR),
    });
    expect(collectPaths(menu)).toEqual(collectPaths(MENU_SIDEBAR));
  });

  it('shows everything that is not superadmin-only while permissions are still loading', () => {
    const menu = applyMenuAccess(MENU_SIDEBAR, {
      isSuperadmin: false,
      permissionSet: new Set(),
      isLoading: true,
      enabledModuleKeys: null,
      modulesLoading: true,
    });
    const visible = collectPaths(menu);
    expect(visible).toContain('/marketing-management/promotions');
    expect(visible).not.toContain('/system-management/companies');
  });
});
