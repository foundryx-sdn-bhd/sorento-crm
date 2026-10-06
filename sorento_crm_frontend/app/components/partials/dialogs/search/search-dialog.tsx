'use client';

import { ReactNode, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { ArrowUpRight, Search } from 'lucide-react';
import { MENU_SIDEBAR } from '@/config/menu.config';
import { usePermissions } from '@/hooks/usePermissions';
import { useTenantModules } from '@/hooks/useTenantModules';
import { useEffectiveIsSuperadmin } from '@/hooks/useEffectiveIsSuperadmin';
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from '@/components/ui/command';
import {
  createMenuSearchProvider,
  runUniversalSearch,
  spoSearchProvider,
  type UniversalSearchProvider,
  type UniversalSearchResult,
} from '@/lib/universal-search';

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tagName = target.tagName.toLowerCase();
  if (tagName === 'input' || tagName === 'textarea' || tagName === 'select') {
    return true;
  }
  if (target.isContentEditable) return true;
  return target.getAttribute('role') === 'textbox';
}

export function SearchDialog({ trigger }: { trigger: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { permissions } = usePermissions();
  const { enabledModuleKeys } = useTenantModules();
  // Same effective flag as the sidebar, so a view-as hides the same entries here.
  const isSuperadmin = useEffectiveIsSuperadmin();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [groupedResults, setGroupedResults] = useState<
    Record<string, UniversalSearchResult[]>
  >({});

  const providers = useMemo<UniversalSearchProvider[]>(
    () => [createMenuSearchProvider(MENU_SIDEBAR), spoSearchProvider],
    [],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const isShortcut =
        (event.metaKey || event.ctrlKey) &&
        event.shiftKey &&
        event.key.toLowerCase() === 'k';
      if (!isShortcut || isTypingTarget(event.target)) return;
      event.preventDefault();
      setOpen((value) => !value);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Results are filtered by user permissions: only menus the user can access are searchable.
  // Depend on `permissions` (stable array from react-query), not `permissionSet`
  // (new Set every render), to avoid infinite effect loop and page freeze.
  useEffect(() => {
    let cancelled = false;
    const permissionSet = new Set(permissions);
    const run = async () => {
      const next = await runUniversalSearch(query, providers, {
        permissionSet,
        enabledModuleKeys,
        isSuperadmin,
      });
      if (!cancelled) setGroupedResults(next);
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [query, providers, permissions, enabledModuleKeys, isSuperadmin]);

  const hasAnyResults = useMemo(
    () => Object.values(groupedResults).some((items) => items.length > 0),
    [groupedResults],
  );

  const handleSelect = (path?: string) => {
    if (!path) return;
    setOpen(false);
    router.push(path);
  };

  return (
    <>
      <span className="contents" onClick={() => setOpen(true)}>
        {trigger}
      </span>
      {/* Keyboard-triggered (Cmd/Ctrl+Shift+K): no scale, no spring
          (DESIGN-LANGUAGE section 3, M2-01). */}
      <CommandDialog open={open} onOpenChange={setOpen} motion={false} className="sm:max-w-[680px]">
        <CommandInput
          value={query}
          onValueChange={setQuery}
          placeholder="Search menu (e.g. SPO Allocations)"
        />
        <CommandList>
          {!hasAnyResults && <CommandEmpty>No results found.</CommandEmpty>}
          {Object.entries(groupedResults).map(([groupLabel, items]) => (
            <CommandGroup key={groupLabel} heading={groupLabel}>
              {items.map((item) => (
                <CommandItem
                  key={item.id}
                  value={`${item.title} ${item.breadcrumb} ${item.path ?? ''}`}
                  onSelect={() => handleSelect(item.path)}
                >
                  <Search className="size-4" />
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate font-medium">{item.title}</span>
                    <span className="truncate text-xs text-muted-foreground">
                      {item.breadcrumb}
                    </span>
                  </div>
                  <ArrowUpRight className="ms-auto size-4 text-muted-foreground" />
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
        </CommandList>
        <div className="flex items-center justify-between border-t px-3 py-2 text-xs text-muted-foreground">
          <span>Tip: Search by menu title or module</span>
          <CommandShortcut>Cmd/Ctrl + Shift + K</CommandShortcut>
        </div>
      </CommandDialog>
    </>
  );
}
