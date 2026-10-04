'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Tag } from 'lucide-react';
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
import {
  useMarketSegments,
  useMemberMarketSegments,
  useSetMemberMarketSegments,
} from '@/app/(protected)/user-management/market-segments/hooks/useMarketSegments';

// Module-level so an undefined query result yields the SAME array on every
// render. An inline `= []` default is a fresh array each time, which makes every
// downstream memo (and, worse, the draft-reset effect) fire forever.
const EMPTY: string[] = [];
const EMPTY_CATALOG: never[] = [];

/**
 * Per-member market-segment multiselect within a CS team roster. A member's
 * segments (retail / project) restrict which contacts route to them; both =
 * both checked. A member with no segments serves ALL contacts ("Serves all").
 * Keyed by (team_id, user_id) so the same person can serve different segments
 * across teams. See PLAN-cs-team-market-segment-routing.md.
 *
 * Dialog rather than popover, for the reason spelled out in MemberBrandEditor:
 * SearchableMultiSelect's own portalled popover reads as an outside click to a
 * parent popover and would dismiss the editor mid-selection.
 */
export default function MemberMarketSegmentEditor({
  teamId,
  userId,
}: {
  teamId: string;
  userId: string;
}) {
  const { data: assigned = EMPTY, isLoading } = useMemberMarketSegments(teamId, userId);
  const {
    data: catalog = EMPTY_CATALOG,
    error: catalogError,
    refetch: refetchCatalog,
  } = useMarketSegments(true);
  const setSegments = useSetMemberMarketSegments(teamId, userId);

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

  const options: SearchableMultiSelectOption[] = useMemo(() => {
    const byCode = new Map<string, SearchableMultiSelectOption>();
    for (const s of catalog) {
      if (!s.code || byCode.has(s.code)) continue;
      byCode.set(s.code, { value: s.code, label: s.name });
    }
    // A tag whose segment has since left the catalogue stays selectable, or the
    // member is stuck with it forever and can never go back to serving all.
    for (const code of assigned) {
      if (!byCode.has(code)) byCode.set(code, { value: code, label: code });
    }
    return Array.from(byCode.values());
  }, [catalog, assigned]);

  const nameByCode = useMemo(() => {
    const m = new Map<string, string>();
    options.forEach((o) => m.set(o.value, o.label));
    return m;
  }, [options]);

  const label = (code: string) => nameByCode.get(code) ?? code;

  function save() {
    setSegments.mutate(draft, { onSuccess: () => setOpen(false) });
  }

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-6 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
        aria-label="Edit market segments"
        onClick={() => setOpen(true)}
      >
        <Tag className="size-3" aria-hidden />
        {isLoading ? (
          <span>…</span>
        ) : assigned.length > 0 ? (
          <span className="flex flex-wrap items-center gap-1">
            {assigned.map((code) => (
              <Badge key={code} variant="secondary" className="text-[10px] font-normal">
                {label(code)}
              </Badge>
            ))}
          </span>
        ) : (
          <span>Serves all</span>
        )}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Market segments served</DialogTitle>
            <DialogDescription>Leave empty to serve all contacts.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <SearchableMultiSelect
              value={draft}
              onChange={setDraft}
              options={options}
              loadError={catalogError}
              onRetry={() => void refetchCatalog()}
              placeholder="All contacts"
              emptyMessage="No matching segment."
              disabled={setSegments.isPending}
            />
            {catalog.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No market segments configured. Add some under User Management → Market
                Segments.
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOpen(false)}
              disabled={setSegments.isPending}
            >
              Cancel
            </Button>
            <Button size="sm" onClick={save} disabled={setSegments.isPending}>
              Save segments
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
