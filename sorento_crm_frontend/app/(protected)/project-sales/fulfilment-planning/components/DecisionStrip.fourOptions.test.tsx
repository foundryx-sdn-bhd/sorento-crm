/**
 * The strip shows FOUR cards (owner, 12 Oct 2026, `PLAN-plan-four-options-12oct.md`):
 * Use own location, Borrow, Use BRW, Buy - the bulk Decide menu's own options, in its order.
 *
 * The planner no longer suggests Use incoming, Borrow incoming or Borrow from another order,
 * but a decision frozen before that (or a saved draft) still carries them. Display only:
 * an old order or incoming borrow counts under Borrow; old water counts on no card.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DecisionStrip } from './DecisionStrip';
import {
  contributionCarriesKind,
  decisionStripTotals,
} from '../../_shared/lib/decisionStrip';
import type {
  BoardContribution,
  BoardSource,
} from '../../_shared/types/fulfilmentPlanning.types';

function source(over: Partial<BoardSource> = {}): BoardSource {
  return { kind: 'reserve', qty: '10', reason: 'because', ...over };
}

function line(over: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'so-1:10',
    sales_order_id: 'so-1',
    so_number: 'SO416191',
    line_no: 10,
    item_code: 'CB2805A-DIY',
    qty: '13',
    qty_outstanding: '13',
    fulfilment_location: 'BRW-BB',
    unplannable: false,
    rank_score: 0,
    rank_factors: [],
    sources: [],
    trail: [],
    item_flags: null,
    contested: false,
    covered: false,
    decision: null,
    ...over,
  } as BoardContribution;
}

/** A line suggested under the old ladder: every removed kind, plus the four. */
const old = line({
  key: 'old',
  proposed: {
    components: [
      source({ rung: 'group_take', qty: '5', location: 'BRW-BB' }),
      source({ kind: 'timely_spo', rung: 'group_take', qty: '7', location: 'BRW-BB' }),
      source({
        kind: 'borrow',
        rung: 'order_borrow',
        qty: '20',
        location: 'BRW-BB',
        donor_so_number: 'SO383872',
      }),
      source({
        kind: 'borrow',
        rung: 'supply_borrow',
        qty: '3',
        location: 'BRW-BB',
        donor_so_number: 'SO400333',
      }),
      source({ kind: 'borrow', rung: 'order_borrow', qty: '4', location: 'BRW-IB' }),
      source({ rung: 'pool', qty: '6', location: 'BRW' }),
      source({ kind: 'buy', rung: 'buy', qty: '9', location: null }),
    ],
  },
});

function renderStrip(contributions: BoardContribution[]) {
  render(
    <DecisionStrip
      contributions={contributions}
      draft={{}}
      active={null}
      onToggle={vi.fn()}
    />,
  );
}

function cards() {
  return screen
    .getAllByTestId(/^decision-strip-(?!changed-)/)
    .map((node) => node.getAttribute('data-testid'));
}

describe('DecisionStrip: four options', () => {
  it('shows exactly Use own location, Borrow, Use BRW, Buy, in that order', () => {
    renderStrip([old]);

    expect(cards()).toEqual([
      'decision-strip-own',
      'decision-strip-borrow_other',
      'decision-strip-shared',
      'decision-strip-buy',
    ]);
    expect(screen.getByTestId('decision-strip-borrow_other')).toHaveTextContent(
      'Borrow',
    );
  });

  it('keeps the four cards on a board with nothing on it, Borrow included', () => {
    renderStrip([line()]);

    expect(cards()).toEqual([
      'decision-strip-own',
      'decision-strip-borrow_other',
      'decision-strip-shared',
      'decision-strip-buy',
    ]);
  });

  it('never shows a Use incoming, Borrow incoming or Borrow from another order card', () => {
    renderStrip([old]);

    expect(screen.queryByTestId('decision-strip-incoming')).toBeNull();
    expect(screen.queryByTestId('decision-strip-borrow_incoming')).toBeNull();
    expect(screen.queryByTestId('decision-strip-borrow_order')).toBeNull();
    expect(screen.queryByText('Use incoming')).toBeNull();
    expect(screen.queryByText('Borrow from another order')).toBeNull();
  });

  it('counts an old order or incoming borrow under Borrow, and old water on no card', () => {
    const totals = decisionStripTotals([old], {});

    expect(totals.map((t) => [t.kind, t.suggested])).toEqual([
      ['own', '5'],
      ['borrow_other', '27'],
      ['shared', '6'],
      ['buy', '9'],
    ]);
  });

  it('pressing Borrow keeps a line whose only borrow names an old donor order', () => {
    // The filter must match the count: the card counts this line, so pressing it keeps it.
    const donorOnly = line({
      key: 'donor-only',
      proposed: {
        components: [
          source({
            kind: 'borrow',
            rung: 'order_borrow',
            qty: '20',
            location: 'BRW-BB',
            donor_so_number: 'SO383872',
          }),
        ],
      },
    });
    const waterOnly = line({
      key: 'water-only',
      proposed: {
        components: [
          source({ kind: 'timely_spo', rung: 'group_take', qty: '7', location: 'BRW-BB' }),
        ],
      },
    });

    expect(contributionCarriesKind(donorOnly, null, 'borrow_other')).toBe(true);
    expect(contributionCarriesKind(waterOnly, null, 'own')).toBe(false);
  });
});
