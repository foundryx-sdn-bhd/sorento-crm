'use client';

import Link from 'next/link';
import { ChevronUp, MessageSquare } from 'lucide-react';
import { IdeaStatusBadge } from '@/components/ideas/IdeaStatusBadge';
import { cn } from '@/lib/utils';
import type { PortalCard } from '@/types/ideasPortal';

/** One idea as a link to its portal card page. The roadmap omits the status (its column says it). */
export function PortalCardLink({
  card,
  showStatus,
  className,
}: {
  card: PortalCard;
  showStatus?: boolean;
  className?: string;
}) {
  const title = card.title ?? card.ideaNumber ?? 'Idea';
  return (
    <Link
      href={`/ideas/portal/${card.id}`}
      className={cn(
        'flex min-w-0 flex-col gap-2 rounded-lg border bg-card p-3 transition-colors hover:bg-accent/50',
        className,
      )}
    >
      <span className="text-xs tabular-nums text-muted-foreground">{card.ideaNumber}</span>
      <span className="break-words text-sm font-medium" title={title}>
        {title}
      </span>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {showStatus ? <IdeaStatusBadge label={card.statusLabel} color={card.statusColor} /> : null}
        <span className="flex items-center gap-0.5 tabular-nums">
          <ChevronUp className="size-3.5" aria-hidden />
          {card.upvotes}
        </span>
        <span className="flex items-center gap-1 tabular-nums">
          <MessageSquare className="size-3.5" aria-hidden />
          {card.commentCount}
        </span>
      </span>
    </Link>
  );
}

/** An empty state: heading and hint, no button. */
export function PortalEmpty({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed py-10 text-center">
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="text-sm text-muted-foreground">{hint}</p>
    </div>
  );
}
