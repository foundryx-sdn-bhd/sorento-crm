/**
 * Which move becomes the primary button.
 *
 * This is worth pinning because it has now been got wrong twice, both times in a way that
 * offered UNDOING the last step as the obvious next one:
 *
 * 1. Ranking on the edge's own `sort_order`, which every seeded transition leaves at 0, so the
 *    winner was whichever row Postgres returned first.
 * 2. Preferring any non-terminal move over a forward one, so a rung whose only advance is a
 *    terminal end (Accepted, Won) fell through to a backward correction.
 */
import { describe, expect, it } from 'vitest';
import { splitStatusMoves, type StatusMove } from './ProjectStatusAction';

function move(overrides: Partial<StatusMove> & { transitionId: string }): StatusMove {
  return {
    toStatusId: `to-${overrides.transitionId}`,
    label: 'Move',
    toLabel: 'Somewhere',
    toKey: 'somewhere',
    toIsTerminal: false,
    isForward: true,
    ...overrides,
  };
}

describe('splitStatusMoves', () => {
  it('offers the forward move, not the correction that undoes the last one', () => {
    const { primary, secondary } = splitStatusMoves([
      move({ transitionId: 'back', label: 'Back to identified', isForward: false }),
      move({ transitionId: 'fwd', label: 'Spec in' }),
    ]);

    expect(primary?.label).toBe('Spec in');
    expect(secondary.map((m) => m.label)).toEqual(['Back to identified']);
  });

  it('prefers a forward TERMINAL rung over a backward open one', () => {
    // The issued-quotation case: "Mark accepted" ends the lifecycle and "Back to draft" does
    // not, so ranking on non-terminal alone made undoing the issue the primary button.
    const { primary } = splitStatusMoves([
      move({ transitionId: 'accept', label: 'Mark accepted', toIsTerminal: true }),
      move({ transitionId: 'back', label: 'Back to draft', isForward: false }),
    ]);

    expect(primary?.label).toBe('Mark accepted');
  });

  it('still prefers an open forward rung over a terminal one', () => {
    const { primary } = splitStatusMoves([
      move({ transitionId: 'lost', label: 'Lost', toIsTerminal: true }),
      move({ transitionId: 'next', label: 'PO received' }),
    ]);

    expect(primary?.label).toBe('PO received');
  });

  it('names the exit rather than leaving the header with no action', () => {
    // Nothing forward remains. Hiding the only move reads as "there is a next step but we are
    // not telling you", which is worse than naming the exit.
    const { primary } = splitStatusMoves([
      move({ transitionId: 'dormant', label: 'Mark dormant', toIsTerminal: true, isForward: false }),
    ]);

    expect(primary?.label).toBe('Mark dormant');
  });

  it('has no primary when the rung has no outgoing move', () => {
    expect(splitStatusMoves([])).toEqual({ primary: null, secondary: [] });
  });
});
