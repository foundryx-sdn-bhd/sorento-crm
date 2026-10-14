/** SO-CANCEL-LINE (AC-F4, AC-F6): the pill after a CRM cancel reads Confirmed, never Cancelled. */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BoardDecisionPill } from './BoardDecisionPill';
import type { BoardContribution } from '../../_shared/types/fulfilmentPlanning.types';

const KEY = 'so-a|1|CB110|2026-10-01';

function contributionOf(overrides: Record<string, unknown> = {}): BoardContribution {
  return {
    key: KEY,
    sales_order_id: 'so-a',
    so_number: 'SO900001',
    line_no: 1,
    item_code: 'CB110',
    qty: '0',
    unplannable: false,
    sources: [],
    contested: false,
    rank_score: 0,
    rank_factors: [],
    covered: false,
    decision: null,
    ...overrides,
  } as BoardContribution;
}

describe('BoardDecisionPill with a CRM cancel', () => {
  it('reads Confirmed when cancelled_at is set (no new label, not Cancelled)', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          cancelled: true,
          cancelled_at: '2026-10-07T01:00:00',
          cancelled_by: 'Siti Test',
          cancel_reason: 'ZZT reason',
        })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Confirmed');
  });

  it('reads Cancelled for an AutoCount cancel that left OI rows for CS (CANCEL-STOP-AT-SO)', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({
          cancelled: true,
          cancelled_at: '2026-10-14T11:55:00',
          cancelled_by: 'AutoCount',
          cancel_reason: 'XS001 · 2 open order inquiry rows',
          cancel_open_oi_rows: 2,
        })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`).textContent).toBe('Cancelled');
  });

  it('keeps Cancelled for a book-cancelled line with no cancelled_at', () => {
    render(<BoardDecisionPill contribution={contributionOf({ cancelled: true })} decision={null} />);
    expect(screen.getByTestId(`decision-pill-${KEY}`).textContent).toBe('Cancelled');
  });

  it('reads Saved for a saved cancel verdict on an uncovered line', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf()}
        decision={{ verdict: 'cancelled', reason: 'ZZT reason' } as never}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent('Saved');
  });
});
