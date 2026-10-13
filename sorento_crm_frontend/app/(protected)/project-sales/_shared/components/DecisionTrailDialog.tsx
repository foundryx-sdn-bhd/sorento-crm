'use client';

import * as React from 'react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import type { DecisionTrailEntry } from '../services/orderInquiryReserveService';

/**
 * `PLAN-oi-decision-trail-ui.md` (round 2, AC-DT-5/AC-DT-10) - the History icon's own
 * dialog, the same shape as `ReserveLineHistoryDialog` beside it: pure presentation, the
 * entries already resolved by the caller (`useDecisionTrail`) one layer up. Shared by the
 * OI worklist, the OI detail Lines tab and the fulfilment board, so a History icon on any
 * of the three opens the same trail for the same core sales-order line.
 *
 * Round 3 (reviewer N2): lives in `_shared/components/`, not under the `order-inquiries/
 * [id]/` route folder it started in - shared code (the board imports this too) must not
 * import from a route folder.
 */
const DECISION_TRAIL_KIND_LABEL: Record<string, string> = {
  confirmed: 'Confirmed',
  saved: 'Saved',
  raised: 'Raised',
  reconfirmed: 'Reconfirmed',
  sheet: 'Sheet',
  planning_change: 'Planning change',
  // PR 1622: purchasing's refusal, kept in the line's history after the badge clears.
  rejected: 'Rejected by purchasing',
};

/**
 * Round 3 (reviewer N3): a `sheet` or `planning_change` entry names no actor
 * (`actor_name: null`) - nobody in this system raised it - and printing "Unknown"
 * claimed a person was simply left unnamed, which is a different (and false) thing.
 * `null` actor_name omits the actor entirely and prints ONLY the date, whatever the
 * entry's own `kind` is: a plain, actor-agnostic rule rather than a per-kind special
 * case, since the fact that decides it is `actor_name` being null, not which kind an
 * entry happens to be.
 */
function ActorLine({ entry }: { entry: DecisionTrailEntry }) {
  const when = entry.at ? formatDateTimeInMalaysia(entry.at) : null;
  if (!entry.actor_name) return when ? <>{when}</> : null;
  return (
    <>
      {entry.actor_name}
      {when ? ` on ${when}` : ''}
    </>
  );
}

/**
 * The trail itself, without the dialog around it: `DecisionTrailDialog` below renders it,
 * and so does the OI detail's per-line History dialog as its Decisions tab
 * (`PLAN-oi-no-double-count-25sep.md`, owner ruling 26 Sep, G3) - one trail, two frames.
 */
export function DecisionTrailEntries({
  entries,
  isLoading,
  error,
  emptyText = 'No trail recorded yet.',
}: {
  entries: DecisionTrailEntry[];
  isLoading?: boolean;
  error?: string | null;
  emptyText?: string;
}) {
  return (
    <div className="space-y-2">
      {isLoading ? (
        <div className="space-y-2" data-testid="decision-trail-loading">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        entries.map((entry, index) => (
          // The entry's own identity - kind + when it happened - never the array
          // index ALONE, which reorders on refetch; `at` alone still collides when
          // two rows are raised in the same call (S2, round 3: `at` shares a second
          // between them), so the index breaks that tie too.
          <div
            key={`${entry.kind}-${entry.at ?? ''}-${index}`}
            className="rounded-md border border-border p-2 text-xs"
          >
            <div className="font-medium">
              {DECISION_TRAIL_KIND_LABEL[entry.kind] ?? entry.kind}
              {entry.detail ? ` · ${entry.detail}` : ''}
            </div>
            <div className="text-muted-foreground">
              <ActorLine entry={entry} />
            </div>
          </div>
        ))
      )}
    </div>
  );
}

export function DecisionTrailDialog({
  open,
  onOpenChange,
  itemCode,
  entries,
  isLoading,
  error,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  itemCode: string | null;
  entries: DecisionTrailEntry[];
  /** S1 (review round 3): while the read is in flight, a skeleton - never the empty
   * state, which would tell a reader "nothing happened here" before the answer is even
   * back. Defaults to `false` so a caller with no loading state of its own (there is
   * none today) reads exactly as before. */
  isLoading?: boolean;
  /** S1: a failed fetch states the error - `extractApiError`'s own message, thrown by
   * `getDecisionTrail` - rather than silently reading as "No trail recorded yet.",
   * which claims the read succeeded and simply found nothing. */
  error?: string | null;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Decision trail{itemCode ? ` - ${itemCode}` : ''}</DialogTitle>
          <DialogDescription className="sr-only">
            Who saved, confirmed and raised this line
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <DecisionTrailEntries entries={entries} isLoading={isLoading} error={error} />
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

export default DecisionTrailDialog;
