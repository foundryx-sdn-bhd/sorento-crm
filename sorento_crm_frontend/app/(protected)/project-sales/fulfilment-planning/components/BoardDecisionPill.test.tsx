/**
 * BoardDecisionPill (PLAN R6, UAC C2/C3): status only, five labels, no revision number, and a
 * warning flag the draft OR the frozen decision can carry.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  BoardDecisionPill,
  matchesStatusFilter,
  PURCHASING_REJECTED_FILTER,
} from './BoardDecisionPill';
import type {
  BoardContribution,
  BoardLineDecision,
} from '../../_shared/types/fulfilmentPlanning.types';

const KEY = 'so-a|1|WESERP10B|2026-08-31';

function contributionOf(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: KEY,
    sales_order_id: 'so-a',
    so_number: 'SO403340',
    line_no: 1,
    item_code: 'WESERP10B',
    qty: '100',
    qty_outstanding: '100',
    fulfilment_location: 'BRW-BB',
    fulfilment_warehouse_id: 'wh-BRW-BB',
    unplannable: false,
    sources: [],
    contested: false,
    rank_score: 0,
    rank_factors: [],
    covered: false,
    decision: null,
    ...overrides,
  };
}

describe('BoardDecisionPill: the five labels (C3, R6)', () => {
  it('reads Suggested when nobody has decided anything yet, on the board or in the database', () => {
    render(<BoardDecisionPill contribution={contributionOf()} decision={null} />);
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Suggested');
  });

  it('reads Saved for an approval, not Approved (S4, R-F)', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf()}
        decision={{ verdict: 'approved' }}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Saved');
  });

  it('reads Saved for an amendment, not Amended (S4, R-F)', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf()}
        decision={{ verdict: 'amended' }}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Saved');
  });

  it('reads Rejected', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf()}
        decision={{ verdict: 'rejected', reason: 'Cancelled' }}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Rejected');
  });

  it('reads Confirmed for a covered line the draft has not touched', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          covered: true,
          decision: { revision_no: 3, timely_spo_qty: '0', reserve: [], borrow: [], buy_qty: '10' },
        })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Confirmed');
  });
});

describe('BoardDecisionPill: a sheet-covered line reads Confirmed (AC-R2-19, owner ruling 18 Sep)', () => {
  it('reads Confirmed for a line covered only by a live sheet-migrated inquiry row (no decision) - "With purchasing" was ruled confusing and dropped', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({ covered: true, decision: null })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Confirmed');
  });
});

describe('BoardDecisionPill: no "rev" (R6)', () => {
  it('never prints a revision number beside Confirmed', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          covered: true,
          decision: { revision_no: 3, timely_spo_qty: '0', reserve: [], borrow: [], buy_qty: '10' },
        })}
        decision={null}
      />,
    );
    const pill = screen.getByTestId(`decision-pill-${KEY}`);
    expect(pill.textContent).toBe('Confirmed');
    expect(pill.textContent).not.toContain('rev');
  });
});

describe('BoardDecisionPill: no popover on the Confirmed chip (round 2, PLAN-oi-decision-trail-ui.md, owner ruling after hand-testing round 1)', () => {
  const FROZEN = { revision_no: 1, timely_spo_qty: '0', reserve: [], borrow: [], buy_qty: '10' };
  const CONFIRMED = {
    covered: true,
    decision: FROZEN,
    decided_by_name: 'Nurain',
    decided_at: '2026-09-25T01:20:34',
    decision_revision: 1,
  };
  // What the backend actually sends when a draft exists: the `draft` object AND the
  // flattened pair, together.
  const DRAFT = {
    draft: {
      decision: { verdict: 'approved' as const },
      saved_by: 'Farah',
      saved_at: '2026-09-25T02:00:00',
    },
    draft_saved_by_name: 'Farah',
    draft_saved_at: '2026-09-25T02:00:00',
  };

  it('renders the Confirmed chip as plain text - no popover trigger, even when a draft also sits on the covered line', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({ ...CONFIRMED, ...DRAFT })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Confirmed');
    // Round 1's own trigger is retired - the History icon (`DecisionTrailButton`, wired
    // in the caller's own Verdict/Decision cell, not this component) opens the trail now.
    expect(screen.queryByTestId(`decision-confirmed-trail-${KEY}`)).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Confirmed by/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Saved by/)).not.toBeInTheDocument();
  });

  it('leaves the Saved-by popover on a non-confirmed draft line unchanged', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          ...DRAFT,
          decided_by_name: 'Nurain',
          decided_at: '2026-09-25T01:20:34',
          decision_revision: 1,
        })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Saved');
    const trigger = screen.getByTestId(`decision-saved-by-${KEY}`);
    expect(trigger.tagName).toBe('BUTTON');
    fireEvent.click(trigger);
    expect(screen.getByText(/^Saved by Farah/)).toBeInTheDocument();
    expect(screen.queryByText(/^Confirmed by/)).not.toBeInTheDocument();
  });
});

describe('BoardDecisionPill: a line with no location', () => {
  it('reads "Needs a location" rather than any verdict', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({ unplannable: true, fulfilment_location: null })}
        decision={null}
      />,
    );
    expect(screen.getByText('Needs a location')).toBeInTheDocument();
  });
});

describe('BoardDecisionPill: cancelled outranks unplannable (R3)', () => {
  it('reads Cancelled, not "Needs a location", for a cancelled line that also has no location', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          cancelled: true,
          unplannable: true,
          fulfilment_location: null,
        })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Cancelled');
    expect(screen.queryByText('Needs a location')).not.toBeInTheDocument();
  });
});

describe('BoardDecisionPill: the warning flag (C10)', () => {
  it('shows the flag from the draft decision', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf()}
        decision={{ verdict: 'approved', suspected_system_issue: true }}
      />,
    );
    expect(screen.getByTestId(`decision-flag-${KEY}`)).toBeInTheDocument();
  });

  it('shows the flag from the frozen contribution.decision after a reload, with no draft entry', () => {
    const frozen: BoardLineDecision = {
      revision_no: 1,
      timely_spo_qty: '0',
      reserve: [],
      borrow: [],
      buy_qty: '10',
      suspected_system_issue: true,
    };
    render(
      <BoardDecisionPill
        contribution={contributionOf({ covered: true, decision: frozen })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-flag-${KEY}`)).toBeInTheDocument();
  });

  it('shows no flag at all for an ordinary decision', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf()}
        decision={{ verdict: 'approved' }}
      />,
    );
    expect(screen.queryByTestId(`decision-flag-${KEY}`)).not.toBeInTheDocument();
  });
});

describe('PLAN-board-change-proposed-pill: a pre-mark reads "Change proposed", not "Saved"', () => {
  it('reads "Change proposed" for a session draft the board pre-marked itself (no server-saved draft)', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf()}
        decision={{ verdict: 'approved', preMarked: true }}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent(
      'Change proposed',
    );
  });

  it('reads "Saved" once the line carries a real server-saved draft, pre-mark flag or not', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          draft: {
            decision: { verdict: 'approved' },
            saved_by: 'Eling',
            saved_at: '2026-09-03T01:00:00',
          },
        })}
        // The pre-mark flag survives on the session's own entry until a real write REPLACES
        // it (`decide()` writes a fresh object) - the server draft arriving first, on the
        // SAME key, is exactly the shape `isPreMarkOnly` has to see through: `preMarked: true`
        // present AND a real `contribution.draft` present both at once.
        decision={{ verdict: 'approved', preMarked: true }}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Saved');
  });

  it('shows no warning flag for a pre-mark, even when the frozen decision behind it was flagged (unchanged: the draft wins outright)', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          decision: {
            revision_no: 1,
            timely_spo_qty: '0',
            reserve: [],
            borrow: [],
            buy_qty: '10',
            suspected_system_issue: true,
          },
        })}
        decision={{ verdict: 'approved', preMarked: true }}
      />,
    );
    expect(screen.queryByTestId(`decision-flag-${KEY}`)).not.toBeInTheDocument();
  });
});

describe('BoardDecisionPill: a saved line the engine has re-suggested (S4, AC-4.4)', () => {
  it('reads "Suggestion changed" rather than Saved', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          draft: {
            decision: { verdict: 'amended' },
            saved_by: 'Eling',
            saved_at: '2026-09-03T01:00:00',
            stale: true,
          },
        })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent(
      'Suggestion changed',
    );
  });

  it('reads Saved once the same draft is no longer stale', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          draft: {
            decision: { verdict: 'amended' },
            saved_by: 'Eling',
            saved_at: '2026-09-03T01:00:00',
            stale: false,
          },
        })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Saved');
  });

  it('leaves a CONFIRMED line alone: a stale draft never overrides what was frozen', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          covered: true,
          // A revision actually confirmed this line - this fixture's own intent is the
          // decision-covered case (a decision-less, sheet-covered line also reads
          // Confirmed since the 18 Sep ruling, see the describe block above).
          decision: { revision_no: 3, timely_spo_qty: '0', reserve: [], borrow: [], buy_qty: '10' },
          draft: {
            decision: { verdict: 'amended' },
            saved_by: 'Eling',
            saved_at: '2026-09-03T01:00:00',
            stale: true,
          },
        })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Confirmed');
  });

  /**
   * N6 (code review round 3): resolution order is `confirmed > rejected > stale > saved`,
   * matching `confirmSummaryFor` (`_shared/lib/fulfilmentBoard.ts`). A REJECTED decision on
   * a stale line commits nothing either way, and "Rejected" is what the planner actually did
   * about it - "Suggestion changed" said something happened that the planner had already
   * answered.
   */
  it('reads Rejected before Suggestion changed, when THIS session rejected a stale line', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          draft: {
            decision: { verdict: 'amended' },
            saved_by: 'Eling',
            saved_at: '2026-09-03T01:00:00',
            stale: true,
          },
        })}
        decision={{ verdict: 'rejected', reason: 'The customer cancelled this line.' }}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Rejected');
  });
});

/**
 * PR 1622 (mock v3): "Rejected by purchasing" is the order inquiry's status, not a verdict,
 * but it filters from the same Status control the verdicts do.
 */
describe('matchesStatusFilter: Rejected by purchasing (PR 1622)', () => {
  const refused = contributionOf({
    order_inquiry: {
      inquiry_no: 'OI-2610-0007',
      state: 'raised',
      ack_state: 'rejected',
      rejected_reason: 'DISCON',
      rejected_by_name: 'Joey',
    } as BoardContribution['order_inquiry'],
  });
  const answered = contributionOf({
    order_inquiry: {
      inquiry_no: 'OI-2610-0007',
      state: 'raised',
      ack_state: null,
    } as BoardContribution['order_inquiry'],
  });

  it('keeps an open refusal and drops everything else', () => {
    expect(matchesStatusFilter(refused, null, [PURCHASING_REJECTED_FILTER])).toBe(true);
    expect(matchesStatusFilter(answered, null, [PURCHASING_REJECTED_FILTER])).toBe(false);
  });

  it('still matches the line by its own verdict beside it', () => {
    expect(matchesStatusFilter(refused, null, ['suggested'])).toBe(true);
    expect(matchesStatusFilter(answered, null, [PURCHASING_REJECTED_FILTER, 'suggested'])).toBe(
      true,
    );
  });

  it('lets every line through with no filter', () => {
    expect(matchesStatusFilter(answered, null, [])).toBe(true);
  });
});
