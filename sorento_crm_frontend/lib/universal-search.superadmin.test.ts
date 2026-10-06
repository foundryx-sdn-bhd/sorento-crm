/**
 * MENU-PERM-GATE: the Cmd-K menu search is the sidebar's second surface, so it drops
 * `superadminOnly` entries on the same effective flag.
 */
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR } from '@/config/menu.config';
import type { MenuConfig } from '@/config/types';
import { createMenuSearchProvider } from './universal-search';

function allSlugs(items: MenuConfig): Set<string> {
  const out = new Set<string>();
  for (const item of items) {
    if (item.permission) out.add(item.permission);
    for (const p of item.permissionsAny ?? []) out.add(p);
    if (item.children) for (const p of allSlugs(item.children)) out.add(p);
  }
  return out;
}

const provider = createMenuSearchProvider(MENU_SIDEBAR);

async function titles(query: string, isSuperadmin: boolean | null | undefined) {
  const results = await provider.search(query, {
    permissionSet: allSlugs(MENU_SIDEBAR),
    enabledModuleKeys: null,
    isSuperadmin,
  });
  return results.map((r) => r.title);
}

describe('menu search and superadmin-only entries', () => {
  it('hides them for a non-superadmin (an admin viewing as one included)', async () => {
    expect(await titles('Companies', false)).toEqual([]);
    expect(await titles('Import Jobs', false)).toEqual([]);
    // A permission-gated neighbour in the same group stays searchable.
    expect(await titles('App Store', false)).toContain('App Store');
  });

  it('keeps them for a superadmin', async () => {
    expect(await titles('Companies', true)).toContain('Companies');
  });

  it('keeps them while the flag is still loading, as the sidebar does', async () => {
    expect(await titles('Companies', null)).toContain('Companies');
    expect(await titles('Companies', undefined)).toContain('Companies');
  });
});
