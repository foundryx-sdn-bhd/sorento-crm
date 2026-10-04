'use client';

import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { useIdeaMergedQuery } from '@/hooks/useIdeas';

/** The ideas merged into this one, each opening its own page (where Unmerge lives). */
export function IdeaMergedList({ ideaId, count }: { ideaId: string; count: number }) {
  const { data: children = [] } = useIdeaMergedQuery(ideaId);
  return (
    <section aria-label="Merged ideas" className="flex flex-col gap-3 border-t pt-5">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        Merged ideas
        <Badge variant="secondary" size="sm" shape="circle">
          {count}
        </Badge>
      </h3>
      {children.length === 0 ? (
        <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed py-6 text-center">
          <span className="text-sm font-medium">No merged ideas</span>
          <span className="text-sm text-muted-foreground">Ideas merged into this one appear here.</span>
        </div>
      ) : (
        <ul className="flex flex-col divide-y rounded-lg border">
          {children.map((child) => (
            <li key={child.id} className="flex min-w-0 items-center gap-3 px-3 py-2">
              <span className="w-24 shrink-0 truncate text-xs tabular-nums text-muted-foreground">
                {child.ideaNumber ?? '-'}
              </span>
              <Link
                href={`/ideas/${child.id}`}
                className="min-w-0 flex-1 truncate text-sm text-primary hover:underline"
                title={child.title ?? child.problem}
              >
                {child.title ?? child.problem}
              </Link>
              <span className="hidden shrink-0 truncate text-xs text-muted-foreground sm:block">
                {child.submitterName}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
