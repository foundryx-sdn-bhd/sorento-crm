'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Bookmark } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  SearchableMultiSelect,
  type SearchableMultiSelectOption,
} from '@/components/common/SearchableMultiSelect';
import { useBrandSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query';
import { useMemberBrands, useSetMemberBrands } from '../hooks/useMemberBrands';

// Module-level so an undefined query result yields the SAME array on every
// render. An inline `= []` default is a fresh array each time, which makes every
// downstream memo (and, worse, the draft-reset effect) fire forever.
const EMPTY: string[] = [];
const EMPTY_CATALOG: never[] = [];

/**
 * Per-member brand multiselect within a team roster. A member's brands restrict
 * which work routes to them; a member with no brands serves ALL brands ("All
 * brands"). When nobody in the team carries the brand asked for, the whole team
 * round-robins. Keyed by (team_id, user_id) so the same person can serve
 * different brands across teams. Mirrors MemberMarketSegmentEditor.
 *
 * The picker lives in a dialog rather than a popover: SearchableMultiSelect opens
 * its own portalled popover, and Radix reads that as an outside click on a parent
 * popover, which would dismiss the editor mid-selection. DialogContent already
 * guards against exactly that (see components/ui/dialog.tsx).
 */
export default function MemberBrandEditor({
  teamId,
  userId,
}: {
  teamId: string;
  userId: string;
}) {
  const { data: assigned = EMPTY, isLoading } = useMemberBrands(teamId, userId);
  const {
    data: catalog = EMPTY_CATALOG,
    error: catalogError,
    refetch: refetchCatalog,
  } = useBrandSelectQuery();
  const setBrands = useSetMemberBrands(teamId, userId);

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>(EMPTY);

  // The server value is read ONLY at the moment the dialog opens: a background
  // refetch while it is open would otherwise wipe whatever the user has picked.
  const assignedRef = useRef(assigned);
  useEffect(() => {
    assignedRef.current = assigned;
  }, [assigned]);

  useEffect(() => {
    if (open) setDraft(assignedRef.current);
  }, [open]);

  // Deduped by code (the same brand exists per company) and sorted by name, so the
  // list reads A to Z whatever order the API happened to return.
  const options: SearchableMultiSelectOption[] = useMemo(() => {
    const byCode = new Map<string, SearchableMultiSelectOption>();
    for (const b of catalog) {
      const value = String(b.brand_code ?? '').toLowerCase();
      if (!value || byCode.has(value)) continue;
      byCode.set(value, { value, label: b.brand_name });
    }
    // A tag whose brand has since left the catalogue stays selectable, or the
    // member is stuck with it forever and can never go back to serving all.
    for (const code of assigned) {
      if (!byCode.has(code)) byCode.set(code, { value: code, label: code.toUpperCase() });
    }
    return Array.from(byCode.values()).sort((a, b) =>
      a.label.localeCompare(b.label),
    );
  }, [catalog, assigned]);

  const nameByCode = useMemo(() => {
    const m = new Map<string, string>();
    options.forEach((o) => m.set(o.value, o.label));
    return m;
  }, [options]);

  const label = (code: string) => nameByCode.get(code) ?? code.toUpperCase();

  function save() {
    setBrands.mutate(draft, { onSuccess: () => setOpen(false) });
  }

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-6 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
        aria-label="Edit brands"
        onClick={() => setOpen(true)}
      >
        <Bookmark className="size-3" aria-hidden />
        {isLoading ? (
          <span>…</span>
        ) : assigned.length > 0 ? (
          <span className="flex flex-wrap items-center gap-1">
            {assigned.map((code) => (
              <Badge
                key={code}
                variant="secondary"
                className="text-[10px] font-normal"
              >
                {label(code)}
              </Badge>
            ))}
          </span>
        ) : (
          <span>All brands</span>
        )}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Brands served</DialogTitle>
            <DialogDescription>Leave empty to serve all brands.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <SearchableMultiSelect
              value={draft}
              onChange={setDraft}
              options={options}
              loadError={catalogError}
              onRetry={() => void refetchCatalog()}
              placeholder="All brands"
              emptyMessage="No matching brand."
              disabled={setBrands.isPending}
            />
            {catalog.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No brands configured. Add some under Master Data → Brands.
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOpen(false)}
              disabled={setBrands.isPending}
            >
              Cancel
            </Button>
            <Button size="sm" onClick={save} disabled={setBrands.isPending}>
              Save brands
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
