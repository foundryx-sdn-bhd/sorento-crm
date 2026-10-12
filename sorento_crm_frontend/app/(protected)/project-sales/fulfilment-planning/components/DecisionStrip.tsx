'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { COLOURS, LABELS } from '../../_shared/lib/supplyVocabulary';
import type { SupplyKind } from '../../_shared/lib/supplyVocabulary';
import { SupplyKindCard } from '../../_shared/components/SupplyKindCard';
import {
  decisionStripTotals,
  rejectedLineCount,
} from '../../_shared/lib/decisionStrip';
import { toMinor } from '../../_shared/lib/supplyComposition';
import type {
  BoardContribution,
  BoardDraft,
} from '../../_shared/types/fulfilmentPlanning.types';

/**
 * Suggested vs decided, per kind of supply, across the whole selection (AC-D2).
 *
 * FOUR CARDS, the bulk Decide menu's own options in its order (owner, 12 Oct 2026,
 * `PLAN-plan-four-options-12oct.md`): Use own location, Borrow, Use BRW, Buy. Off
 * `STRIP_ORDER`, which also folds what an older decision still carries (every borrow under
 * Borrow; old water on no card). Each card carries two figures. Numbers only: what a card
 * means is its label and its colour, and a sentence under a card would be a feature
 * explanation in the UI.
 *
 * A card whose two figures disagree carries the amber dot the board already uses for "this
 * moved". Pressing a card filters BOTH views to the lines carrying that kind on either side,
 * so "who bought what the pool could have covered" is two clicks rather than a scan.
 *
 * A card reading 0 and 0 is DISABLED rather than hidden: nothing on this board is that kind
 * of supply, so there is nothing to filter to, and a press that produced an empty board would
 * read as a broken filter. It keeps its place, because a card that came and went would move
 * every card beside it and the strip is read by glancing at a position.
 */
export function DecisionStrip({
  contributions,
  draft,
  active,
  onToggle,
}: {
  contributions: BoardContribution[];
  draft: BoardDraft;
  /** The kind currently filtering the grid, or null. */
  active: SupplyKind | null;
  onToggle: (kind: SupplyKind) => void;
}) {
  const totals = React.useMemo(
    () => decisionStripTotals(contributions, draft),
    [contributions, draft],
  );
  // Purchasing refused these lines and they are back with CS (AC-RB2). Beside the cards
  // rather than on one, because a refusal is not a kind of supply - it is a line with no
  // supply decided at all.
  const rejected = React.useMemo(
    () => rejectedLineCount(contributions),
    [contributions],
  );

  return (
    <div data-testid="decision-strip" className="space-y-2">
      <div
        className={cn(
          // Four cards: two rows of two on a phone, one row from `sm` up.
          'grid grid-cols-2 gap-2 sm:grid-cols-4',
        )}
      >
        {totals.map((total) => {
          const selected = active === total.kind;
          // Nothing on this board is that kind of supply, so there is nothing to filter to.
          const empty =
            toMinor(total.suggested) === 0 && toMinor(total.decided) === 0;
          return (
            <SupplyKindCard
              key={total.kind}
              kind={total.kind}
              label={LABELS[total.kind]}
              swatchClass={COLOURS[total.kind].bar}
              selected={selected}
              disabled={empty}
              onClick={() => onToggle(total.kind)}
              testId={`decision-strip-${total.kind}`}
              // The two figures moved apart. The same amber the grid marks a change with,
              // and no word beside it: the pair of numbers under it is the explanation.
              mark={
                total.changed ? (
                  <span
                    data-testid={`decision-strip-changed-${total.kind}`}
                    aria-label="Suggested and decided differ"
                    className="size-1.5 shrink-0 rounded-full bg-amber-500"
                  />
                ) : null
              }
            >
              <span className="mt-1.5 flex items-baseline justify-between gap-2">
                <span className="text-[11px] text-muted-foreground">
                  Suggested
                </span>
                <span className="text-sm font-semibold tabular-nums">
                  {total.suggested}
                </span>
              </span>
              <span className="flex items-baseline justify-between gap-2">
                <span className="text-[11px] text-muted-foreground">
                  Decided
                </span>
                <span
                  className={cn(
                    'text-sm font-semibold tabular-nums',
                    COLOURS[total.kind].text,
                  )}
                >
                  {total.decided}
                </span>
              </span>
            </SupplyKindCard>
          );
        })}
      </div>
      {rejected > 0 ? (
        <span
          data-testid="decision-strip-rejected"
          className="inline-flex rounded bg-destructive/10 px-1.5 py-0.5 text-[11px] font-medium text-destructive"
        >
          {`${rejected} rejected`}
        </span>
      ) : null}
    </div>
  );
}
