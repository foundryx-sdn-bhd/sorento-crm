'use client';

import { JSX, useCallback, useMemo } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffectiveIsSuperadmin } from '@/hooks/useEffectiveIsSuperadmin';
import { MENU_SIDEBAR } from '@/config/menu.config';
import { injectPublishedWorkflowForms } from '@/config/workflow-forms-dynamic-menu';
import { MenuConfig, MenuItem } from '@/config/types';
import { applyMenuAccess } from '@/lib/menu-access-filter';
import { usePublishedWorkflowDefinitionsForSubmissionQuery } from '@/app/(protected)/workflow-forms-management/hooks/useWorkflowForms';
import { cn } from '@/lib/utils';
import { collectMenuPaths, matchesMenuPath } from '@/lib/menu-path-match';
import { usePermissions, useHasAnyPermission } from '@/hooks/usePermissions';
import { WORKFLOW_PUBLISHED_FOR_SUBMISSION_PERMISSIONS } from '@/config/workflow-forms-dynamic-menu';
import { useTenantModules } from '@/hooks/useTenantModules';
import { usePrefetchOnce } from '@/hooks/usePrefetchOnce';
import {
  AccordionMenu,
  AccordionMenuClassNames,
  AccordionMenuGroup,
  AccordionMenuItem,
  AccordionMenuLabel,
  AccordionMenuSub,
  AccordionMenuSubContent,
  AccordionMenuSubTrigger,
} from '@/components/ui/accordion-menu';
import { Badge } from '@/components/ui/badge';
import { PRESSED_CLASS } from '@/components/ui/primitive-classes';
import { QuickAccessBlock } from './quick-access-block';
import { MenuItemPinButton } from './menu-item-pin-button';

export function SidebarMenu() {
  const pathname = usePathname();
  // The EFFECTIVE user's flag: the impersonation target's while viewing as, else the
  // session's (MENU-PERM-GATE). The permission set below is already the target's, the
  // backend answers /users/me/permissions for the impersonated user.
  const isSuperadmin = useEffectiveIsSuperadmin();
  const { permissionSet, isLoading } = usePermissions();
  const { enabledModuleKeys, isLoading: modulesLoading } = useTenantModules();
  // A viewport prefetch of ~100 sidebar links is why every menu item carried
  // `prefetch={false}`; hover is the middle ground (M4 list latency).
  const prefetchOnce = usePrefetchOnce();
  const wfModuleEnabled = enabledModuleKeys?.has('workflow_forms') ?? false;
  /** Avoid calling published-for-submission without RBAC - global QueryCache onError would toast 403 on every page. */
  const canFetchPublishedWorkflowForms = useHasAnyPermission(
    [...WORKFLOW_PUBLISHED_FOR_SUBMISSION_PERMISSIONS],
  );
  const { data: publishedFormsRes } = usePublishedWorkflowDefinitionsForSubmissionQuery({
    enabled: wfModuleEnabled && !modulesLoading && canFetchPublishedWorkflowForms,
  });

  const menuWithPublishedForms = useMemo(
    () => injectPublishedWorkflowForms(MENU_SIDEBAR, publishedFormsRes?.data ?? []),
    [publishedFormsRes?.data],
  );

  const effectiveMenu = useMemo(() => {
    return applyMenuAccess(menuWithPublishedForms, {
      isSuperadmin,
      permissionSet,
      isLoading,
      enabledModuleKeys,
      modulesLoading,
    });
  }, [
    permissionSet,
    isLoading,
    enabledModuleKeys,
    modulesLoading,
    menuWithPublishedForms,
    isSuperadmin,
  ]);

  // Every path the visible menu can highlight, so a match can be tested against its rivals.
  const menuPaths = useMemo(() => collectMenuPaths(effectiveMenu), [effectiveMenu]);

  // Memoize matchPath to prevent unnecessary re-renders
  const matchPath = useCallback(
    (path: string): boolean => matchesMenuPath(path, pathname, menuPaths),
    [pathname, menuPaths],
  );

  // Global classNames for consistent styling
  const classNames: AccordionMenuClassNames = {
    root: 'lg:ps-1 space-y-3',
    group: 'gap-px',
    label:
      'uppercase text-xs font-medium text-muted-foreground/70 pt-2.25 pb-px',
    separator: '',
    item: cn(
      'h-8 text-accent-foreground hover:bg-accent hover:text-primary data-[selected=true]:text-primary data-[selected=true]:bg-muted data-[selected=true]:font-medium',
      PRESSED_CLASS,
    ),
    sub: '',
    subTrigger: cn(
      'h-8 text-accent-foreground hover:bg-accent hover:text-primary data-[selected=true]:text-primary data-[selected=true]:bg-muted data-[selected=true]:font-medium',
      PRESSED_CLASS,
    ),
    subContent: 'py-0',
    indicator: '',
  };

  const buildMenu = (items: MenuConfig): JSX.Element[] => {
    return items.map((item: MenuItem, index: number) => {
      if (item.heading) {
        return buildMenuHeading(item, index);
      } else if (item.disabled) {
        return buildMenuItemRootDisabled(item, index);
      } else {
        return buildMenuItemRoot(item, index);
      }
    });
  };

  const buildMenuItemRoot = (item: MenuItem, index: number): JSX.Element => {
    if (item.children) {
      return (
        <AccordionMenuSub key={index} value={item.path || `root-${index}`}>
          <AccordionMenuSubTrigger className="text-sm font-medium">
            {item.icon && <item.icon data-slot="accordion-menu-icon" />}
            <span data-slot="accordion-menu-title">{item.title}</span>
          </AccordionMenuSubTrigger>
          <AccordionMenuSubContent
            type="single"
            collapsible
            parentValue={item.path || `root-${index}`}
            className="ps-6"
          >
            <AccordionMenuGroup>
              {buildMenuItemChildren(item.children, 1)}
            </AccordionMenuGroup>
          </AccordionMenuSubContent>
        </AccordionMenuSub>
      );
    } else {
      return (
        <AccordionMenuItem
          key={index}
          value={item.path || ''}
          className="text-sm font-medium group"
        >
          <Link
            href={item.path || '#'}
            prefetch={false}
            onPointerEnter={() => item.path && prefetchOnce(item.path)}
            className="flex items-center grow gap-2 min-w-0"
          >
            {item.icon && <item.icon data-slot="accordion-menu-icon" className="shrink-0" />}
            <span data-slot="accordion-menu-title" className="truncate">{item.title}</span>
            {item.path && <MenuItemPinButton path={item.path} title={item.title} className="ms-auto" />}
          </Link>
        </AccordionMenuItem>
      );
    }
  };

  const buildMenuItemRootDisabled = (
    item: MenuItem,
    index: number,
  ): JSX.Element => {
    return (
      <AccordionMenuItem
        key={index}
        value={`disabled-${index}`}
        className="text-sm font-medium"
      >
        {item.icon && <item.icon data-slot="accordion-menu-icon" />}
        <span data-slot="accordion-menu-title">{item.title}</span>
        {item.disabled && (
          <Badge variant="secondary" size="sm" className="ms-auto me-[-10px]">
            Soon
          </Badge>
        )}
      </AccordionMenuItem>
    );
  };

  const buildMenuItemChildren = (
    items: MenuConfig,
    level: number = 0,
  ): JSX.Element[] => {
    return items.map((item: MenuItem, index: number) => {
      if (item.disabled) {
        return buildMenuItemChildDisabled(item, index, level);
      } else {
        return buildMenuItemChild(item, index, level);
      }
    });
  };

  const buildMenuItemChild = (
    item: MenuItem,
    index: number,
    level: number = 0,
  ): JSX.Element => {
    if (item.children) {
      return (
        <AccordionMenuSub
          key={index}
          value={item.path || `child-${level}-${index}`}
        >
          <AccordionMenuSubTrigger className="text-[13px]">
            {item.collapse ? (
              <span className="text-muted-foreground">
                <span className="hidden [[data-state=open]>span>&]:inline">
                  {item.collapseTitle}
                </span>
                <span className="inline [[data-state=open]>span>&]:hidden">
                  {item.expandTitle}
                </span>
              </span>
            ) : (
              item.title
            )}
          </AccordionMenuSubTrigger>
          <AccordionMenuSubContent
            type="single"
            collapsible
            parentValue={item.path || `child-${level}-${index}`}
            className={cn(
              'ps-4',
              !item.collapse && 'relative',
              !item.collapse && (level > 0 ? '' : ''),
            )}
          >
            <AccordionMenuGroup>
              {buildMenuItemChildren(
                item.children,
                item.collapse ? level : level + 1,
              )}
            </AccordionMenuGroup>
          </AccordionMenuSubContent>
        </AccordionMenuSub>
      );
    } else {
      return (
        <AccordionMenuItem
          key={index}
          value={item.path || ''}
          className="text-[13px] group"
        >
          <div className="flex items-center gap-1 min-w-0 w-full">
            <Link
              href={item.path || '#'}
              prefetch={false}
              onPointerEnter={() => item.path && prefetchOnce(item.path)}
              className="flex-1 min-w-0 truncate"
            >
              {item.title}
            </Link>
            {item.path && (
              <MenuItemPinButton path={item.path} title={item.title} size="sm" />
            )}
          </div>
        </AccordionMenuItem>
      );
    }
  };

  const buildMenuItemChildDisabled = (
    item: MenuItem,
    index: number,
    level: number = 0,
  ): JSX.Element => {
    return (
      <AccordionMenuItem
        key={index}
        value={`disabled-child-${level}-${index}`}
        className="text-[13px]"
      >
        <span data-slot="accordion-menu-title">{item.title}</span>
        {item.disabled && (
          <Badge variant="secondary" size="sm" className="ms-auto me-[-10px]">
            Soon
          </Badge>
        )}
      </AccordionMenuItem>
    );
  };

  const buildMenuHeading = (item: MenuItem, index: number): JSX.Element => {
    return <AccordionMenuLabel key={index}>{item.heading}</AccordionMenuLabel>;
  };

  const salesHeadingIndex = useMemo(
    () => effectiveMenu.findIndex((item) => item.heading === 'SALES'),
    [effectiveMenu]
  );
  const indexToSplit = salesHeadingIndex >= 0 ? salesHeadingIndex : effectiveMenu.length;
  const menuBefore = useMemo(() => effectiveMenu.slice(0, indexToSplit), [effectiveMenu, indexToSplit]);
  const menuAfter = useMemo(() => effectiveMenu.slice(indexToSplit), [effectiveMenu, indexToSplit]);

  return (
    // M6-02: dvh - this IS the shipped shell (demo1), so its sidebar scroll cap
    // must not sit under mobile Safari's dynamic toolbar the way vh does.
    <div className="kt-scrollable-y-hover flex grow shrink-0 py-5 px-5 lg:max-h-[calc(100dvh-5.5rem)]">
      <AccordionMenu
        selectedValue={pathname}
        matchPath={matchPath}
        type="single"
        collapsible
        classNames={classNames}
        defaultExpandedValue="quick-access"
      >
        {buildMenu(menuBefore)}
        <QuickAccessBlock />
        {buildMenu(menuAfter)}
      </AccordionMenu>
    </div>
  );
}
