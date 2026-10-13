/**
 * `PLAN-oi-decision-trail-ui.md` (round 2, AC-DT-5/AC-DT-10) - pure presentation, the same
 * shape as `ReserveLineHistoryDialog.test.tsx` beside it.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DecisionTrailDialog } from './DecisionTrailDialog';
import type { DecisionTrailEntry } from '../services/orderInquiryReserveService';

describe('DecisionTrailDialog', () => {
  it('renders every kind of entry, newest first, with the actor and the date', () => {
    const entries: DecisionTrailEntry[] = [
      {
        kind: 'confirmed',
        actor_name: 'Nurain',
        at: '2026-09-25T01:20:34Z',
        detail: 'Revision 1 · Buy 10',
      },
      {
        kind: 'saved',
        actor_name: 'Farah',
        at: '2026-09-25T02:00:00Z',
        detail: 'Amended · Buy 3',
      },
      {
        kind: 'reconfirmed',
        actor_name: 'Dara',
        at: '2026-09-25T01:20:33Z',
        detail: 'OI-2609-0731 · qty 20',
      },
      {
        kind: 'sheet',
        actor_name: null,
        at: '2026-09-01T00:00:00Z',
        detail: 'OI-2005-0012 · qty 5',
      },
      {
        kind: 'planning_change',
        actor_name: null,
        at: '2026-08-01T00:00:00Z',
        detail: 'Was 2026-07-01',
      },
    ];

    render(
      <DecisionTrailDialog open onOpenChange={() => {}} itemCode="CB6622-PP" entries={entries} />,
    );

    expect(screen.getByText('Decision trail - CB6622-PP')).toBeInTheDocument();
    expect(screen.getByText(/Confirmed.*Revision 1/)).toBeInTheDocument();
    expect(screen.getByText(/Nurain on/)).toBeInTheDocument();
    expect(screen.getByText(/Saved.*Amended/)).toBeInTheDocument();
    expect(screen.getByText(/Reconfirmed.*OI-2609-0731/)).toBeInTheDocument();
    expect(screen.getByText(/Planning change.*Was 2026-07-01/)).toBeInTheDocument();
  });

  /**
   * PR 1622 (owner, mock v3 note 4): purchasing's refusal stays in the line's history after
   * the badge clears, so CS can trace it back: which inquiry, why, who, when.
   */
  it('names a purchasing refusal with its inquiry, reason and who', () => {
    render(
      <DecisionTrailDialog
        open
        onOpenChange={() => {}}
        itemCode="CB6622-PP"
        entries={[
          {
            kind: 'rejected',
            actor_name: 'Joey',
            at: '2026-10-13T08:32:00Z',
            detail: 'OI-2610-0007: DISCON',
          },
        ]}
      />,
    );

    expect(screen.getByText(/Rejected by purchasing.*OI-2610-0007: DISCON/)).toBeInTheDocument();
    expect(screen.getByText(/Joey on/)).toBeInTheDocument();
  });

  /**
   * N3 (review round 3): a `sheet` or `planning_change` entry names no actor at all -
   * "Unknown" claimed somebody was simply left unnamed, which is a different (and
   * false) fact. The rule is generic on `actor_name` being null, not on the entry's
   * `kind` - a future kind that also carries no actor (an anonymous backfill event's
   * `raised`/`reconfirmed`, for one - `raised_by` is nullable) reads the same way.
   */
  it('omits the actor line entirely and prints only the date when actor_name is null, whatever the kind', () => {
    render(
      <DecisionTrailDialog
        open
        onOpenChange={() => {}}
        itemCode="CB6622-PP"
        entries={[
          { kind: 'sheet', actor_name: null, at: '2026-09-01T00:00:00Z', detail: 'OI-2005-0012 · qty 5' },
          { kind: 'planning_change', actor_name: null, at: '2026-08-01T00:00:00Z', detail: 'Was 2026-07-01' },
          // An anonymous backfill event: no `raised_by`, so no actor - same rule applies.
          { kind: 'raised', actor_name: null, at: '2026-01-01T00:00:00Z', detail: 'OI-0001 · qty 1' },
        ]}
      />,
    );

    expect(screen.queryByText(/Unknown/)).not.toBeInTheDocument();
    const sheetCard = screen.getByText(/Sheet.*OI-2005-0012/).closest('div')?.parentElement;
    expect(sheetCard?.textContent).toMatch(/2026/);
    const planningCard = screen.getByText(/Planning change.*Was 2026-07-01/).closest('div')?.parentElement;
    expect(planningCard?.textContent).toMatch(/2026/);
    const raisedCard = screen.getByText(/Raised.*OI-0001/).closest('div')?.parentElement;
    expect(raisedCard?.textContent).toMatch(/2026/);
  });

  it('renders nothing on the actor line when both actor_name and at are null', () => {
    render(
      <DecisionTrailDialog
        open
        onOpenChange={() => {}}
        itemCode="CB6622-PP"
        entries={[{ kind: 'sheet', actor_name: null, at: null, detail: 'OI-2005-0012 · qty 5' }]}
      />,
    );

    const card = screen.getByText(/Sheet.*OI-2005-0012/).closest('div')?.parentElement;
    expect(card?.querySelector('.text-muted-foreground')?.textContent).toBe('');
  });

  /**
   * S2 (review round 3): two rows raised in the same call can share the exact same
   * `at` - the key used to be `${kind}-${at ?? index}`, which collided whenever that
   * happened (React would silently drop or misrender the second row).
   */
  it('renders two entries with the same kind and the same timestamp as two distinct rows', () => {
    render(
      <DecisionTrailDialog
        open
        onOpenChange={() => {}}
        itemCode="CB6622-PP"
        entries={[
          { kind: 'raised', actor_name: 'Nurain', at: '2026-09-25T01:20:34Z', detail: 'OI-0001 · qty 1' },
          { kind: 'raised', actor_name: 'Nurain', at: '2026-09-25T01:20:34Z', detail: 'OI-0002 · qty 2' },
        ]}
      />,
    );

    expect(screen.getByText(/OI-0001/)).toBeInTheDocument();
    expect(screen.getByText(/OI-0002/)).toBeInTheDocument();
  });

  it('shows a skeleton while loading, never the empty state', () => {
    render(
      <DecisionTrailDialog
        open
        onOpenChange={() => {}}
        itemCode="CB6622-PP"
        entries={[]}
        isLoading
      />,
    );

    expect(screen.getByTestId('decision-trail-loading')).toBeInTheDocument();
    expect(screen.queryByText('No trail recorded yet.')).not.toBeInTheDocument();
  });

  it('shows the error message instead of the empty state when the read failed', () => {
    render(
      <DecisionTrailDialog
        open
        onOpenChange={() => {}}
        itemCode="CB6622-PP"
        entries={[]}
        error="Failed to load that decision trail"
      />,
    );

    expect(screen.getByText('Failed to load that decision trail')).toBeInTheDocument();
    expect(screen.queryByText('No trail recorded yet.')).not.toBeInTheDocument();
  });

  it('shows the empty state when nothing has been recorded yet', () => {
    render(
      <DecisionTrailDialog open onOpenChange={() => {}} itemCode="CB6622-PP" entries={[]} />,
    );

    expect(screen.getByText('No trail recorded yet.')).toBeInTheDocument();
  });

  it('titles the dialog without an item code when none is given', () => {
    render(<DecisionTrailDialog open onOpenChange={() => {}} itemCode={null} entries={[]} />);

    expect(screen.getByText('Decision trail')).toBeInTheDocument();
  });
});
