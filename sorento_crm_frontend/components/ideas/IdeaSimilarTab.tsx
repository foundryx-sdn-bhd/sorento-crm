'use client';

import Link from 'next/link';
import { ChevronUp, Merge } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useSimilarIdeasQuery } from '@/hooks/useIdeas';
import type { Idea } from '@/types/ideas';
import { IdeaStatusBadge } from './IdeaStatusBadge';

/** The Similar ideas tab: ss's nearest ideas, with Merge (managers) preloading the merge modal. */
export function IdeaSimilarTab({
  ideaId,
  canManage,
  onMerge,
}: {
  ideaId: string;
  canManage: boolean;
  onMerge: (similar: Idea) => void;
}) {
  const { data, isLoading, isError, refetch, isFetching } = useSimilarIdeasQuery(ideaId);

  if (isLoading) {
    return <Skeleton className="h-24 w-full rounded-xl" />;
  }

  if (isError || !data || !data.available) {
    return (
      <Card>
        <section aria-label="Similar ideas" className="flex flex-col items-center gap-3 p-8 text-center">
          <span className="text-sm text-muted-foreground">Similar ideas are unavailable right now</span>
          <Button variant="outline" size="sm" disabled={isFetching} onClick={() => void refetch()}>
            Retry
          </Button>
        </section>
      </Card>
    );
  }

  if (data.ideas.length === 0) {
    return (
      <Card>
        <section aria-label="Similar ideas" className="flex flex-col items-center gap-1 p-8 text-center">
          <h3 className="text-sm font-semibold">No similar ideas</h3>
          <p className="text-sm text-muted-foreground">Ideas close to this one appear here.</p>
        </section>
      </Card>
    );
  }

  return (
    <Card>
      <section aria-label="Similar ideas" className="p-5">
        <ul className="flex flex-col divide-y rounded-lg border">
          {data.ideas.map((similar) => (
            <li key={similar.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2">
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{similar.ideaNumber}</span>
              <Link
                href={`/ideas/${similar.id}`}
                className="min-w-0 flex-1 truncate text-sm text-primary hover:underline"
                title={similar.title ?? similar.problem}
              >
                {similar.title ?? similar.problem}
              </Link>
              <IdeaStatusBadge label={similar.statusLabel} color={similar.statusColor} />
              <span className="flex shrink-0 items-center gap-0.5 text-xs tabular-nums text-muted-foreground">
                <ChevronUp className="size-4" aria-hidden />
                {similar.upvotes}
              </span>
              {canManage ? (
                <Button variant="outline" size="sm" className="gap-1.5" onClick={() => onMerge(similar)}>
                  <Merge className="size-4" />
                  Merge
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </Card>
  );
}
