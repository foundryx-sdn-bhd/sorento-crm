import type { MenuConfig, MenuItem } from '@/config/types';
import { filterMenuByModule } from '@/lib/menu-module-filter';

/**
 * The sidebar's access filters, as pure functions over a menu tree so the shipped
 * `MENU_SIDEBAR` can be tested through exactly what the component renders from
 * (MENU-PERM-GATE, PLAN-menu-perm-gate-06oct.md).
 */

/** Hide items that require a permission the user doesn't have; recurse into children. */
export function filterMenuByPermission(
  items: MenuConfig,
  permissionSet: Set<string>,
): MenuConfig {
  return items
    .filter((item: MenuItem) => {
      if (item.heading) return true;
      if (item.permissionsAny?.length) {
        if (!item.permissionsAny.some((p) => permissionSet.has(p)))
          return false;
      } else if (item.permission && !permissionSet.has(item.permission)) {
        return false;
      }
      if (item.children?.length) {
        return filterMenuByPermission(item.children, permissionSet).length > 0;
      }
      return true;
    })
    .map((item: MenuItem) =>
      item.children?.length
        ? {
            ...item,
            children: filterMenuByPermission(item.children, permissionSet),
          }
        : item,
    );
}

/** Hide superadmin-only entries from non-superadmins. `null` = still loading - show all (avoids flicker for real superadmins). */
export function filterMenuBySuperadmin(
  items: MenuConfig,
  isSuperadmin: boolean | null,
): MenuConfig {
  if (isSuperadmin === null || isSuperadmin) return items;
  return items
    .filter((item: MenuItem) => {
      if (item.heading) return true;
      if (item.superadminOnly) return false;
      if (item.children?.length) {
        return filterMenuBySuperadmin(item.children, isSuperadmin).length > 0;
      }
      return true;
    })
    .map((item: MenuItem) =>
      item.children?.length
        ? {
            ...item,
            children: filterMenuBySuperadmin(item.children, isSuperadmin),
          }
        : item,
    );
}

/** Drop heading items that have no non-heading items following them before the next heading or end of list. */
export function filterOrphanHeadings(items: MenuConfig): MenuConfig {
  return items.filter((item, index) => {
    if (!item.heading) return true;
    for (let i = index + 1; i < items.length; i++) {
      if (items[i].heading) return false;
      return true;
    }
    return false;
  });
}

export type MenuAccess = {
  /** The EFFECTIVE user's flag (the impersonation target's while viewing as). `null` while loading. */
  isSuperadmin: boolean | null;
  /** The effective user's permission slugs. */
  permissionSet: Set<string>;
  /** Permissions still loading: show everything that is not superadmin-only (no flicker). */
  isLoading: boolean;
  /** Enabled tenant module keys. `null` = still loading / error - show all. */
  enabledModuleKeys: Set<string> | null;
  modulesLoading: boolean;
};

/** Superadmin, then permission, then module, then orphan headings: the order the sidebar renders in. */
export function applyMenuAccess(
  menu: MenuConfig,
  access: MenuAccess,
): MenuConfig {
  const bySuper = filterMenuBySuperadmin(menu, access.isSuperadmin);
  if (access.isLoading) return filterOrphanHeadings(bySuper);
  const byPerm = filterMenuByPermission(bySuper, access.permissionSet);
  if (access.modulesLoading) return filterOrphanHeadings(byPerm);
  return filterOrphanHeadings(
    filterMenuByModule(byPerm, access.enabledModuleKeys),
  );
}
