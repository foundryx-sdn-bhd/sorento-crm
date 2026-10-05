'use client';

import { useEffect, useRef } from 'react';
import { ChevronDown, ChevronUp, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { FindController } from './useFindController';

/**
 * Floating find bar (n8n / editor style): query input, "N/M" match count,
 * previous/next, close. Enter = next, Shift+Enter = previous, Escape = close.
 * Rendered top-right inside a `relative` container.
 */
export function FindBar({
  controller,
  onDismiss,
}: {
  controller: FindController;
  /** Called after the bar closes, so a host can take focus back (caret stays on the match). */
  onDismiss?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { open, query, matches, activeIndex, setQuery, close: closeFind, next, prev } = controller;
  const close = () => {
    closeFind();
    onDismiss?.();
  };

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const total = matches.length;

  return (
    <div
      className="absolute right-2 top-2 z-20 flex items-center gap-1 rounded-md border bg-background p-1 shadow-md"
      data-testid="find-bar"
      // Keep clicks/keys inside the bar from bubbling to the host container.
      onKeyDown={(e) => e.stopPropagation()}
    >
      <input
        ref={inputRef}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            if (e.shiftKey) prev();
            else next();
          } else if (e.key === 'Escape') {
            e.preventDefault();
            close();
          }
        }}
        placeholder="Find"
        className="h-6 w-36 rounded bg-transparent px-1.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring placeholder:text-muted-foreground"
        data-testid="find-input"
      />
      <span
        className={cn(
          'min-w-12 select-none px-1 text-center text-[11px] tabular-nums',
          query && total === 0 ? 'text-destructive' : 'text-muted-foreground',
        )}
        data-testid="find-count"
      >
        {query ? `${total === 0 ? 0 : activeIndex + 1}/${total}` : '0/0'}
      </span>
      <button
        type="button"
        onClick={prev}
        disabled={total === 0}
        className="rounded p-0.5 text-muted-foreground hover:bg-muted disabled:opacity-40"
        aria-label="Previous match"
        data-testid="find-prev"
      >
        <ChevronUp className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={next}
        disabled={total === 0}
        className="rounded p-0.5 text-muted-foreground hover:bg-muted disabled:opacity-40"
        aria-label="Next match"
        data-testid="find-next"
      >
        <ChevronDown className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={close}
        className="rounded p-0.5 text-muted-foreground hover:bg-muted"
        aria-label="Close find"
        data-testid="find-close"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}

/** True for the Cmd/Ctrl+F chord. */
export function isFindChord(e: {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
}): boolean {
  return (e.metaKey || e.ctrlKey) && (e.key === 'f' || e.key === 'F');
}
