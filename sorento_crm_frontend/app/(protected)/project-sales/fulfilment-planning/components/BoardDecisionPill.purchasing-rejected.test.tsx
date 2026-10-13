/**
 * PR 1622 owner hand test: a line purchasing refused read "Suggested" with nothing saying why
 * it came back. It now reads "Rejected by purchasing", and who, when and why open from it.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  BoardDecisionPill,
  purchasingRefusalLine,
  purchasingRefusalTitle,
  verdictOf,
} from './BoardDecisionPill';
import type { BoardContribution } from '../../_shared/types/fulfilmentPlanning.types';

const KEY = 'so-a|1|B2155-NL-BLUE|2026-10-25';

function contributionOf(
  overrides: Partial<BoardContribution> = {},
): BoardContribution {
  return {
    key: KEY,
    sales_order_id: 'so-a',
    so_number: 'SO416732',
    line_no: 1,
    item_code: 'B2155-NL-BLUE',
    qty: '10',
    qty_outstanding: '0',
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

const refused = {
  inquiry_no: 'OI-2610-0007',
  state: 'raised',
  ack_state: 'rejected',
  rejected_reason: 'Supplier discontinued this finish',
  rejected_by_name: 'Joey',
  rejected_at: '2026-10-13T06:05:00',
} as BoardContribution['order_inquiry'];

describe('Rejected by purchasing', () => {
  it('reads "Rejected by purchasing" on an undecided line purchasing refused', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({ order_inquiry: refused })}
        decision={null}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent(
      'Rejected by purchasing',
    );
  });

  it('opens who, when (Malaysia time) and why from the pill', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({ order_inquiry: refused })}
        decision={null}
      />,
    );
    fireEvent.click(screen.getByTestId(`decision-purchasing-rejected-${KEY}`));
    expect(
      screen.getAllByText(
        /Rejected by Joey on 13\/10\/2026, 2:05\s?pm: Supplier discontinued this finish/i,
      ).length,
    ).toBeGreaterThan(0);
  });

  it('keeps the reason and who/when on the SAME line as the pill, truncated, full text as tooltip', () => {
    render(
      <BoardDecisionPill
        contribution={contributionOf({ order_inquiry: refused })}
        decision={null}
      />,
    );
    const line = screen.getByTestId(`decision-purchasing-rejected-line-${KEY}`);
    expect(line).toHaveClass('truncate');
    expect(line.textContent).toMatch(
      /^Supplier discontinued this finish \(Joey, 13\/10\/2026, 2:05\s?pm\)$/i,
    );
    const trigger = screen.getByTestId(`decision-purchasing-rejected-${KEY}`);
    expect(trigger.getAttribute('title')).toMatch(
      /^Rejected by Joey on 13\/10\/2026/,
    );
    expect(trigger).toContainElement(
      screen.getByTestId(`decision-pill-${KEY}`),
    );
  });

  it('one-line text without a name or time is just the reason', () => {
    expect(
      purchasingRefusalLine({
        rejected_by_name: null,
        rejected_at: null,
        rejected_reason: 'Out',
      }),
    ).toBe('Out');
  });

  it('gives way to CS once they decide the line again in this session', () => {
    expect(
      verdictOf(contributionOf({ order_inquiry: refused }), {
        verdict: 'approved',
      }),
    ).toBe('saved');
  });

  it('is not shown for a refusal the server already cleared (CS answered it)', () => {
    const answered = {
      ...refused,
      ack_state: null,
    } as BoardContribution['order_inquiry'];
    expect(verdictOf(contributionOf({ order_inquiry: answered }), null)).toBe(
      'suggested',
    );
  });

  it('says "no reason given" rather than a blank', () => {
    expect(
      purchasingRefusalTitle({
        rejected_by_name: null,
        rejected_at: null,
        rejected_reason: null,
      }),
    ).toBe('Rejected: no reason given');
  });
});
