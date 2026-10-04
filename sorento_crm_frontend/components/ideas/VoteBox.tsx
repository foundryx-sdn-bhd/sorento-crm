'use client';

import type { MouseEvent } from 'react';
import { ChevronUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface VoteBoxProps {
  count: number;
  /** The viewer has upvoted: the box is filled. */
  voted: boolean;
  /** A merged child's votes are frozen. */
  disabled?: boolean;
  /** `md` leads the idea page header; `sm` leads list rows and board cards. */
  size?: 'sm' | 'md';
  onVote: () => void;
  className?: string;
}

/**
 * The upvote box: chevron over the count. Upvote only, so a second click takes the vote back.
 * It sits inside rows and cards that open the idea, so its click never reaches them.
 */
export function VoteBox({ count, voted, disabled, size = 'sm', onVote, className }: VoteBoxProps) {
  const handle = (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (!disabled) onVote();
  };
  return (
    <Button
      type="button"
      variant={voted ? 'primary' : 'outline'}
      aria-pressed={voted}
      aria-label={`${voted ? 'Remove your upvote' : 'Upvote'}, ${count} ${count === 1 ? 'vote' : 'votes'}${voted ? ', you have voted' : ''}`}
      title={voted ? 'Remove upvote' : 'Upvote'}
      disabled={disabled}
      onClick={handle}
      onPointerDown={(event) => event.stopPropagation()}
      className={cn(
        'h-auto shrink-0 flex-col gap-0 px-0 tabular-nums',
        size === 'md' ? 'w-14 py-2 text-base font-semibold' : 'w-10 py-1 text-xs font-medium',
        className,
      )}
    >
      <ChevronUp className={size === 'md' ? 'size-5' : 'size-4'} />
      {count}
    </Button>
  );
}
