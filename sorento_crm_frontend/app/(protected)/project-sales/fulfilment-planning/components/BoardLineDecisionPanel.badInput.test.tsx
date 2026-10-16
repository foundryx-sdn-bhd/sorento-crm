/** NUMBER-FIELDS round 3 (S8): bad input in a board quantity is never read as 0; the sentence shows and nothing is decided. */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { BoardDecisionPill } from './BoardDecisionPill';
import { BoardLineDecisionPanel } from './BoardLineDecisionPanel';
import type {
  BoardBorrowCandidate,
  BoardCellLocation,
  BoardContribution,
  BoardDecision,
  BoardLineDecision,
} from '../../_shared/types/fulfilmentPlanning.types';

const KEY = 'so-a|22|SRTWB7518|2026-06-29';

function contributionOf(
  overrides: Partial<BoardContribution> = {},
): BoardContribution {
  return {
    key: KEY,
    sales_order_id: 'so-a',
    so_number: 'SO404352',
    customer_name: 'ABC SDN BHD',
    project_label: null,
    agent_code: 'AG01',
    line_no: 22,
    item_code: 'SRTWB7518',
    qty: '24',
    qty_ordered: '24',
    qty_delivered: '0',
    qty_outstanding: '24',
    project_line_id: 'pl-so-a-22',
    required_date: '2026-06-29',
    is_past: false,
    fulfilment_location: 'BRW-AM',
    fulfilment_warehouse_id: 'wh-BRW-AM',
    unplannable: false,
    priority: null,
    sources: [
      {
        kind: 'reserve',
        qty: '9',
        location: 'BRW-AM',
        warehouse_id: 'wh-BRW-AM',
        reason:
          'Free unclaimed stock at BRW-AM covers this much by the delivery date.',
      },
      {
        kind: 'reserve',
        qty: '15',
        location: 'BRW',
        warehouse_id: 'wh-BRW',
        reason: 'The shared pool at BRW covers this much within its cap.',
      },
    ],
    qty_proposed_reserve: '24',
    qty_proposed_incoming: '0',
    qty_proposed_buy: '0',
    contested: false,
    rank_score: 0,
    rank_factors: [],
    covered: false,
    decision: null,
    order_inquiry: null,
    item_flags: null,
    borrow_candidates: [],
    ...overrides,
  };
}

const LOCATIONS: BoardCellLocation[] = [
  {
    location: 'BRW-AM',
    warehouse_id: 'wh-BRW-AM',
    qty: '0',
    available_qty: '9',
    qty_free: '9',
    qty_free_remaining: '9',
  },
  {
    location: 'BRW',
    warehouse_id: 'wh-BRW',
    qty: '0',
    available_qty: '16',
    qty_free: '16',
    qty_free_remaining: '16',
  },
];

function renderPanel(
  overrides: Partial<BoardContribution> = {},
  decision: BoardDecision | null = null,
) {
  const onDecide = vi.fn();
  const onDirtyChange = vi.fn();
  render(
    <BoardLineDecisionPanel
      contribution={contributionOf(overrides)}
      decision={decision}
      locations={LOCATIONS}
      onDecide={onDecide}
      onDirtyChange={onDirtyChange}
    />,
  );
  return { onDecide, onDirtyChange };
}

import { typeBadInput } from '@/test-utils/badInput';

describe('BoardLineDecisionPanel bad number input (S8)', () => {
  it.each(['Reserve at BRW', 'Reserve at BRW-AM'])(
    '%s: bad input shows the sentence and Save decision sends nothing',
    (label) => {
      const { onDecide } = renderPanel();
      typeBadInput(screen.getByLabelText(label));
      expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
      const save = screen.queryByRole('button', { name: /Save decision|Amend/ });
      if (save && !(save as HTMLButtonElement).disabled) fireEvent.click(save);
      expect(onDecide).not.toHaveBeenCalled();
    },
  );
});
