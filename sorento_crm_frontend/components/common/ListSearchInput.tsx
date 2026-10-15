'use client';

import { LoaderCircle, Search, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

export interface ListSearchInputProps {
  /** Only where a visible `<Label htmlFor>` names the box. */
  id?: string;
  /** Kept for the boxes an existing spec already drives by test id. */
  'data-testid'?: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  /** From `useDebouncedSearch`. The leading icon spins while the box is ahead of the query. */
  isSettling?: boolean;
  /** Called on Enter, for a list that wants to skip the wait. */
  onSubmit?: () => void;
  className?: string;
  /**
   * Extra classes on the `<Input>` itself, additive to the shared
   * `ps-9 pe-9 w-full` (M6-06). For the one caller whose search box is the
   * page's primary control rather than a toolbar filter (the portal landing
   * page) and needs a size the shared 13px default under-serves.
   */
  inputClassName?: string;
  'aria-label'?: string;
  /** `search` gives the box the searchbox role; omitted keeps the plain text input. */
  type?: 'search';
  /**
   * A one-line hint on the box itself, for the single thing about THIS search a
   * reader would otherwise get wrong (the fulfilment board: a product match
   * lists the whole order). A hint, not a paragraph teaching the feature.
   */
  title?: string;
}

/**
 * The search box every list draws, drawn once (S7-02).
 *
 * The block itself (relative wrapper, leading magnifier, trailing clear) was
 * copied into two dozen list toolbars; what none of the copies had was the
 * settling indicator, and that is the point of putting it here. While
 * `isSettling` the magnifier becomes a spinner IN PLACE: it occupies the same
 * 16px, so the field does not reflow on every keystroke, and the reader gets the
 * one thing the old box withheld - that the rows behind it are about to change.
 *
 * Never disabled while a query is in flight. Each keystroke changes the query
 * key, so the list is pending for most of the typing, and disabling the field on
 * that flip makes the browser blur it and drop the rest of the word.
 */
export function ListSearchInput({
  id,
  'data-testid': testId,
  value,
  onChange,
  placeholder = 'Search...',
  isSettling = false,
  onSubmit,
  className,
  inputClassName,
  'aria-label': ariaLabel,
  type,
  title,
}: ListSearchInputProps) {
  return (
    <div className={cn('relative', className ?? 'w-full md:w-64')}>
      {isSettling ? (
        <LoaderCircle
          className="size-4 text-muted-foreground absolute start-3 top-1/2 -translate-y-1/2 animate-spin"
          aria-hidden
        />
      ) : (
        <Search
          className="size-4 text-muted-foreground absolute start-3 top-1/2 -translate-y-1/2"
          aria-hidden
        />
      )}
      <Input
        id={id}
        type={type}
        data-testid={testId}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
        title={title}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && onSubmit) {
            e.preventDefault();
            onSubmit();
          }
        }}
        className={cn('ps-9 pe-9 w-full', inputClassName)}
      />
      {/* Announced politely so a screen reader is told the wait, not shown a spinner. */}
      <span className="sr-only" role="status" aria-live="polite">
        {isSettling ? 'Searching' : ''}
      </span>
      {value && (
        <Button
          mode="icon"
          variant="dim"
          type="button"
          aria-label="Clear search"
          title="Clear search"
          className="absolute end-1.5 top-1/2 -translate-y-1/2 h-6 w-6"
          onClick={() => onChange('')}
        >
          <X />
        </Button>
      )}
    </div>
  );
}
