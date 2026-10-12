'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';

/**
 * A HISTORY, shown the way delivery tracking and Sheets version history show one.
 *
 * The client's words: "our audit trails and activities UI should be like those tracking
 * systems like Lazada, Shopee, Lalamove, or even Google Sheet history tracking - like it is
 * a timeline that explains what happens at what time".
 *
 * This is the one place the system does NOT use a DataGrid, and the distinction is worth
 * stating because it decides which to reach for:
 *
 * - A list of RECORDS (quotations, POs, stakeholders) is a grid. You scan a column, sort it,
 *   compare two rows, click one to open it.
 * - A HISTORY of events is a timeline. Nobody sorts their parcel tracking by courier name.
 *   What you want is the sequence and the gaps in it, and a rail with dots down one side is
 *   what makes "then, three days later" visible at a glance. A table of timestamps hides it.
 *
 * Shape: newest first, grouped under a date heading so the day is said once instead of on
 * every row, absolute times (ADR 1d - "3 days ago" cannot be compared between two rows and
 * rots while the page is open), and the rail drawn continuous between dots so a reader sees
 * one thread rather than a stack of cards.
 */
export type TimelineEvent = {
  id: string;
  /** What happened, in the fewest words that are still specific. */
  title: React.ReactNode;
  /** Lets a caller find one entry (a test, or a row that opens a dialog). */
  testId?: string;
  /** Who did it, if a person did. */
  actor?: string | null;
  /** ISO timestamp. Null is allowed and shown honestly rather than dropping the entry. */
  at?: string | null;
  /** Anything worth reading under the title: a reason, a quoted note, a field change. */
  detail?: React.ReactNode;
  /** Small chips beside the title, e.g. "counts as work". */
  tags?: React.ReactNode;
  /**
   * Rendered in place of the dot. For a human post an avatar says who at a glance, which is
   * exactly what Sheets history does; the rail still runs through it.
   */
  marker?: React.ReactNode;
  /**
   * Tone of the dot. `current` marks the newest event (the parcel's live step), `muted`
   * de-emphasises routine noise, `alert` a failure or a refusal.
   */
  tone?: 'default' | 'current' | 'muted' | 'alert';
};

const DOT_CLASS: Record<NonNullable<TimelineEvent['tone']>, string> = {
  default: 'bg-primary',
  // A ring rather than a bigger dot, so the live step reads as "here" without shouting.
  current: 'bg-primary ring-4 ring-primary/20',
  muted: 'bg-muted-foreground/40',
  alert: 'bg-destructive',
};

/** Undated entries sort last: no stamp cannot claim a place among the ones that have one. */
function groupByDay(events: TimelineEvent[]): { day: string; events: TimelineEvent[] }[] {
  const groups = new Map<string, TimelineEvent[]>();
  for (const event of events) {
    const day = event.at ? formatDateInMalaysia(event.at) : 'Date not recorded';
    const bucket = groups.get(day);
    if (bucket) bucket.push(event);
    else groups.set(day, [event]);
  }
  return [...groups.entries()].map(([day, list]) => ({ day, events: list }));
}

/** Just the time, since the day is already the heading above it. */
function timeOnly(iso?: string | null): string {
  if (!iso) return '-';
  const stamped = formatDateTimeInMalaysia(iso);
  const parts = stamped.split(', ');
  return parts.length > 1 ? parts.slice(1).join(', ') : stamped;
}

export function EventTimeline({
  events,
  emptyTitle = 'Nothing has happened yet',
  className,
}: {
  /** Already in the order you want to read them: newest first. */
  events: TimelineEvent[];
  emptyTitle?: string;
  className?: string;
}) {
  const groups = React.useMemo(() => groupByDay(events), [events]);

  if (events.length === 0) {
    return (
      <div className="px-6 py-10 text-center">
        <h3 className="text-sm font-semibold">{emptyTitle}</h3>
      </div>
    );
  }

  return (
    <ol className={cn('space-y-6', className)} data-testid="event-timeline">
      {groups.map((group) => (
        <li key={group.day}>
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {group.day}
          </p>
          <ol className="space-y-0">
            {group.events.map((event, index) => (
              <li
                key={event.id}
                data-testid={event.testId}
                className="relative flex gap-3 pb-5 last:pb-0"
              >
                {/* The rail. Drawn per row and stopped on the last one, so the thread does
                    not dangle below the final event. */}
                {index < group.events.length - 1 && (
                  <span
                    aria-hidden
                    className="absolute left-[5px] top-4 h-full w-px bg-border"
                  />
                )}
                {event.marker ? (
                  <span className="relative -ms-[7px] mt-0.5 shrink-0">{event.marker}</span>
                ) : (
                  <span
                    aria-hidden
                    className={cn(
                      'relative mt-1.5 size-2.5 shrink-0 rounded-full',
                      DOT_CLASS[event.tone ?? 'default'],
                    )}
                  />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-3">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="break-words text-sm font-medium">{event.title}</span>
                      {event.tags}
                    </div>
                    {/* Right-aligned from sm up, the way a tracking step carries its time. */}
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {timeOnly(event.at)}
                    </span>
                  </div>
                  {event.actor && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{event.actor}</p>
                  )}
                  {event.detail && (
                    <div className="mt-1 break-words text-sm text-muted-foreground">
                      {event.detail}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </li>
      ))}
    </ol>
  );
}
