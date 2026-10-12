/**
 * The board's decision strip, as CARDS (`PLAN-scm-cs-planning-uat.md` section 1e, AC-V7,
 * AC-D2). `_shared/lib/decisionStrip.test.ts` already pins the arithmetic; this file pins
 * the RENDER: the fixed reading order the captain's ruling of 30 Aug 2026 asked for - ladder
 * v7.1's own walk, left = first consideration, right = last option ("own location -> use
 * incoming -> borrow from another order -> borrow incoming -> [borrow other location] ->
 * use BRW -> Buy") - and what a card with nothing on it does.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DecisionStrip } from './DecisionStrip';
import type {
  BoardContribution,
  BoardLineDecision,
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

/** Enough kinds carry a quantity that the strip has something to compare. */
const rich = line({
  key: 'rich',
  proposed: {
    components: [
      source({
        kind: 'reserve',
        rung: 'group_take',
        qty: '40',
        location: 'DC1-BB',
      }),
      source({ kind: 'reserve', rung: 'pool', qty: '20', location: 'BRW' }),
      source({
        kind: 'borrow',
        rung: 'cross_group_borrow',
        qty: '15',
        location: 'DC1-NT',
      }),
    ],
  },
  sources: [
    source({
      kind: 'reserve',
      rung: 'group_take',
      qty: '40',
      location: 'DC1-BB',
    }),
    source({ kind: 'reserve', rung: 'pool', qty: '20', location: 'BRW' }),
    source({ kind: 'buy', rung: 'buy', qty: '15', location: null }),
  ],
});

function renderStrip(contributions: BoardContribution[]) {
  const onToggle = vi.fn();
  render(
    <DecisionStrip
      contributions={contributions}
      draft={{}}
      active={null}
      onToggle={onToggle}
    />,
  );
  return { onToggle };
}

describe('DecisionStrip render order', () => {
  it('reads the four options: own, Borrow, BRW, Buy', () => {
    renderStrip([rich]);

    const cards = screen
      .getAllByTestId(/^decision-strip-(?!changed-)/)
      .map((node) => node.getAttribute('data-testid'));

    // The bulk Decide menu's own order (owner, 12 Oct 2026). This fixture's frozen
    // `cross_group_borrow` counts under Borrow rather than earning a card of its own.
    expect(cards).toEqual([
      'decision-strip-own',
      'decision-strip-borrow_other',
      'decision-strip-shared',
      'decision-strip-buy',
    ]);
    expect(screen.getByTestId('decision-strip-borrow_other')).toHaveTextContent(
      'Suggested15',
    );
  });

  it('presses a card and reports which kind was pressed', () => {
    const { onToggle } = renderStrip([rich]);

    screen.getByTestId('decision-strip-shared').click();

    expect(onToggle).toHaveBeenCalledWith('shared');
  });

  it('keeps the Borrow card in place, disabled, when it reads 0 and 0', () => {
    // A board where nothing borrows: the card still holds its column, or the four stop
    // reading in one order.
    renderStrip([
      line({
        key: 'no-borrow',
        sources: [
          source({
            kind: 'reserve',
            rung: 'group_take',
            qty: '40',
            location: 'DC1-BB',
          }),
          source({ kind: 'buy', rung: 'buy', qty: '15', location: null }),
        ],
      }),
    ]);

    expect(screen.getAllByTestId(/^decision-strip-(?!changed-)/)).toHaveLength(
      4,
    );
    expect(screen.getByTestId('decision-strip-borrow_other')).toBeDisabled();
  });

  it('counts a CONFIRMED water line on no card, and never under Use own location', () => {
    // Use incoming is no longer an option (owner, 12 Oct 2026, Q1): a decision frozen with
    // a `timely_spo` keeps it in its data, but the strip has no card for it and the owner
    // ruled it must not be shown as own stock.
    const confirmed = line({
      key: 'confirmed-water',
      covered: true,
      qty: '9',
      sources: [
        source({
          kind: 'timely_spo',
          rung: 'group_take',
          qty: '9',
          location: 'BRW-SMC',
        }),
      ],
      decision: {
        revision_no: 2,
        timely_spo_qty: '9',
        incoming: [{ location: 'BRW-SMC', qty: '9', rung: 'group_take' }],
        reserve: [],
        borrow: [],
        buy_qty: '0',
      } as BoardLineDecision,
      proposed: {
        components: [
          source({
            kind: 'timely_spo',
            rung: 'group_take',
            qty: '9',
            location: 'BRW-SMC',
          }),
        ],
      },
    } as Partial<BoardContribution>);

    renderStrip([confirmed]);

    expect(screen.queryByTestId('decision-strip-incoming')).toBeNull();
    const own = screen.getByTestId('decision-strip-own');
    expect(own).toHaveTextContent('Suggested0');
    expect(own).toHaveTextContent('Decided0');
  });
});

/**
 * AC-RB2 (`PLAN-scm-oi-handshake.md` section 11): the strip counts the lines purchasing
 * refused, so CS sees the bounce without visiting Order Inquiries.
 *
 * Beside the cards rather than on one: a refusal is not a kind of supply, it is a line with
 * no supply decided at all. Two figures the count is NOT allowed to be: the number of cells
 * (a cell holds several lines) and the number of rows (one line, several inquiry rows).
 */
describe('DecisionStrip: refused lines', () => {
  const refused = (key: string) =>
    line({
      key,
      order_inquiry: {
        inquiry_no: 'OI-000101',
        state: 'raised',
        ack_state: 'rejected',
        rejected_by_name: 'Joey',
        rejected_reason: 'No supplier until November',
      },
    });

  it('counts the refused lines', () => {
    renderStrip([rich, refused('line-a'), refused('line-b')]);

    expect(screen.getByTestId('decision-strip-rejected')).toHaveTextContent(
      '2 rejected',
    );
  });

  it('says nothing when nobody has refused anything', () => {
    renderStrip([rich]);

    expect(screen.queryByTestId('decision-strip-rejected')).toBeNull();
  });

  it('drops back to nothing once CS has answered the refusal', () => {
    // The board clears `ack_state` on a line CS has decided again, so the count follows it
    // down with no second rule of its own.
    renderStrip([
      rich,
      line({
        key: 'answered',
        order_inquiry: {
          inquiry_no: 'OI-000101',
          state: 'raised',
          ack_state: null,
          rejected_by_name: null,
          rejected_reason: null,
        },
      }),
    ]);

    expect(screen.queryByTestId('decision-strip-rejected')).toBeNull();
  });
});
