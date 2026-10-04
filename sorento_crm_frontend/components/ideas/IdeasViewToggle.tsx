'use client';

import { useRouter } from 'next/navigation';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

/** List and Board are two views of the same ideas: a switch in the toolbar, not a second menu entry. */
export function IdeasViewToggle({ active }: { active: 'list' | 'board' }) {
  const router = useRouter();
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      value={active}
      aria-label="View"
      onValueChange={(view) => {
        if (view === 'list' && active !== 'list') router.push('/ideas');
        if (view === 'board' && active !== 'board') router.push('/ideas/board');
      }}
    >
      <ToggleGroupItem value="list" className="px-3">
        List
      </ToggleGroupItem>
      <ToggleGroupItem value="board" className="px-3">
        Board
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
