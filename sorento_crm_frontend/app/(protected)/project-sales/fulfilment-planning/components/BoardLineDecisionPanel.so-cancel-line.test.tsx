/**
 * AC-F12 (SO-CANCEL-LINE browser pass step 8): a line whose saved decision is Transfer to Cancel SO
 * reads as that in the row, not as the engine's suggestion.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { BoardLineDecisionPanel } from './BoardLineDecisionPanel';
import type {
  BoardContribution,
  BoardDecision,
} from '../../_shared/types/fulfilmentPlanning.types';

const KEY = 'so-a|22|SRTWB7518|2026-06-29';

function contribution(): BoardContribution {
  return {
    key: KEY,
    sales_order_id: 'so-a',
    so_number: 'SO404352',
    customer_name: 'ABC SDN BHD',
    project_label: null,
    agent_code: 'AG01',
    line_no: 22,
    item_code: 'SRTWB7518',
    qty: '36',
    qty_ordered: '36',
    qty_delivered: '0',
    qty_outstanding: '36',
    project_line_id: 'pl-so-a-22',
    required_date: '2026-06-29',
    is_past: false,
    fulfilment_location: 'BRW-AM',
    fulfilment_warehouse_id: 'wh-BRW-AM',
    unplannable: false,
    priority: null,
    sources: [
      {
        kind: 'buy',
        qty: '36',
        location: null,
        warehouse_id: null,
        reason: 'Nothing on hand or incoming covers this.',
      },
    ],
    qty_proposed_reserve: '0',
    qty_proposed_incoming: '0',
    qty_proposed_buy: '36',
    contested: false,
    rank_score: 0,
    rank_factors: [],
    covered: false,
    decision: null,
    order_inquiry: null,
    item_flags: null,
    borrow_candidates: [],
  } as unknown as BoardContribution;
}

const CANCEL: BoardDecision = {
  verdict: 'cancelled',
  reason: 'CANCEL ORDER',
  reserve: [],
  borrow: [],
  timely_spo_qty: '0',
  buy_qty: '0',
  order_back: false,
} as unknown as BoardDecision;

function renderPanel(decision: BoardDecision | null) {
  render(
    <BoardLineDecisionPanel
      contribution={contribution()}
      decision={decision}
      locations={[]}
      onDecide={vi.fn()}
      onDirtyChange={vi.fn()}
    />,
  );
}

describe('AC-F12: a Transfer to Cancel SO decision on an uncovered line', () => {
  it('summarises as "Transfer to Cancel SO", with no suggestion summary, no short hint, reason kept', () => {
    renderPanel(CANCEL);

    const summary = screen.getByTestId(`line-decision-summary-${KEY}`);
    expect(summary).toHaveTextContent('Transfer to Cancel SO');
    expect(summary).not.toHaveTextContent('Buy 36');
    expect(summary).not.toHaveTextContent('BRW');
    expect(screen.queryByTestId(`line-decision-hint-${KEY}`)).toBeNull();
    expect(screen.queryByText(/\bshort\b/)).toBeNull();
    expect(screen.getByLabelText(/Reason/)).toHaveValue('CANCEL ORDER');
  });

  it('negative control: no decision shows the suggestion summary and no cancel text', () => {
    renderPanel(null);

    const summary = screen.getByTestId(`line-decision-summary-${KEY}`);
    expect(summary).toHaveTextContent('Buy 36');
    expect(summary).not.toHaveTextContent('Transfer to Cancel SO');
  });
});
