import type { MenuConfig, MenuItem } from '@/config/types';
import { filterMenuBySuperadmin } from '@/lib/menu-access-filter';

export type UniversalSearchSource = 'menu' | 'spo';

export interface UniversalSearchResult {
  id: string;
  sourceType: UniversalSearchSource;
  group: string;
  title: string;
  breadcrumb: string;
  path?: string;
  keywords: string[];
}

export interface UniversalSearchContext {
  permissionSet: Set<string>;
  /** When set, menu items with moduleKey outside this set are excluded. null = skip module filter. */
  enabledModuleKeys?: Set<string> | null;
  /**
   * The EFFECTIVE user's superadmin flag (the impersonation target's while viewing as).
   * `false` drops `superadminOnly` entries, the same way the sidebar does; null / undefined =
   * still loading, keep them (MENU-PERM-GATE).
   */
  isSuperadmin?: boolean | null;
}

export interface UniversalSearchProvider {
  id: UniversalSearchSource;
  label: string;
  search: (
    query: string,
    context: UniversalSearchContext,
  ) => Promise<UniversalSearchResult[]> | UniversalSearchResult[];
}

function normalize(value: string): string {
  return value.trim().toLowerCase();
}

/** User can see this menu item only if it has no permission or their permissionSet includes it. */
function isAllowed(
  item: MenuItem,
  permissionSet: Set<string>,
  enabledModuleKeys: Set<string> | null | undefined,
): boolean {
  if (enabledModuleKeys && item.moduleKey && !enabledModuleKeys.has(item.moduleKey)) {
    return false;
  }
  if (item.permissionsAny?.length) {
    return item.permissionsAny.some((p) => permissionSet.has(p));
  }
  if (!item.permission) return true;
  return permissionSet.has(item.permission);
}

function collectMenuItems(
  items: MenuConfig,
  permissionSet: Set<string>,
  enabledModuleKeys: Set<string> | null | undefined,
  parents: string[] = [],
): UniversalSearchResult[] {
  const results: UniversalSearchResult[] = [];

  for (const item of items) {
    if (item.heading || item.disabled || !isAllowed(item, permissionSet, enabledModuleKeys)) {
      continue;
    }

    const nextParents = item.title ? [...parents, item.title] : parents;

    if (item.path && item.title) {
      const breadcrumb = nextParents.join(' > ');
      results.push({
        id: `menu:${item.path}`,
        sourceType: 'menu',
        group: 'Menus',
        title: item.title,
        breadcrumb,
        path: item.path,
        keywords: [item.title, breadcrumb, item.path],
      });
    }

    if (item.children?.length) {
      results.push(...collectMenuItems(item.children, permissionSet, enabledModuleKeys, nextParents));
    }
  }

  return results;
}

function searchFromKeywords(
  query: string,
  records: UniversalSearchResult[],
): UniversalSearchResult[] {
  const q = normalize(query);
  if (!q) return records.slice(0, 80);

  return records
    .filter((item) => item.keywords.some((key) => normalize(key).includes(q)))
    .slice(0, 80);
}

/** Menu search provider: only includes items the user is allowed to see (context.permissionSet). */
export function createMenuSearchProvider(menu: MenuConfig): UniversalSearchProvider {
  return {
    id: 'menu',
    label: 'Menus',
    search: (query, context) => {
      const records = collectMenuItems(
        filterMenuBySuperadmin(menu, context.isSuperadmin ?? null),
        context.permissionSet,
        context.enabledModuleKeys,
      );
      return searchFromKeywords(query, records);
    },
  };
}

export const spoSearchProvider: UniversalSearchProvider = {
  id: 'spo',
  label: 'SPO Allocations',
  search: async () => {
    // Placeholder provider for future SPO-number lookup integration.
    return [];
  },
};

export async function runUniversalSearch(
  query: string,
  providers: UniversalSearchProvider[],
  context: UniversalSearchContext,
): Promise<Record<string, UniversalSearchResult[]>> {
  const groups: Record<string, UniversalSearchResult[]> = {};

  const providerResults = await Promise.all(
    providers.map(async (provider) => ({
      label: provider.label,
      items: await provider.search(query, context),
    })),
  );

  for (const { label, items } of providerResults) {
    if (items.length > 0) {
      groups[label] = items;
    }
  }

  return groups;
}
