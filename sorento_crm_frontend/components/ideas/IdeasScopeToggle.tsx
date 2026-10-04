'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

/** `?view=mine` scopes the Ideas list to the viewer's own ideas; ss decides which those are. */
export function useIdeasMine(): boolean {
  return useSearchParams().get('view') === 'mine';
}

/** My ideas | All ideas, for the Ideas list's `toolbarScopeSlot` (AC-K-07). The URL is the state. */
export function IdeasScopeToggle() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const mine = useIdeasMine();
  const change = (next: string) => {
    if (!next) return;
    const params = new URLSearchParams(searchParams.toString());
    if (next === 'mine') params.set('view', 'mine');
    else params.delete('view');
    const qs = params.toString();
    // push, not replace: Back returns to the other view (hand test step 11).
    router.push(qs ? `${pathname}?${qs}` : pathname);
  };
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      value={mine ? 'mine' : 'all'}
      onValueChange={change}
      aria-label="Whose ideas"
    >
      <ToggleGroupItem value="mine" className="px-3">
        My ideas
      </ToggleGroupItem>
      <ToggleGroupItem value="all" className="px-3">
        All ideas
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
