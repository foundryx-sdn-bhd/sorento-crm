/**
 * AC-D2: the board's decision strip, as numbers.
 *
 * The captain, 25 August 2026: "one page that shows, per line, what was SUGGESTED (buy / own
 * / shared / borrow) and what was DECIDED, in the same words" - and the page is the
 * fulfilment-planning board, with cards.
 *
 * The two figures per card are NOT one figure and a delta: a kind the engine never suggested
 * and the planner decided anyway has a Suggested of 0, and that is the case the strip exists
 * to make visible.
 */
import { describe, expect, it } from 'vitest';
import {
  cellCarriesKind,
  contributionCarriesKind,
  decisionStripTotals,
} from './decisionStrip';
import type {
  BoardCell,
  BoardContribution,
  BoardSource,
} from '../types/fulfilmentPlanning.types';

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
  };
}

function cell(contributions: BoardContribution[]): BoardCell {
  return {
    row_key: 'CB2805A-DIY',
    item_code: 'CB2805A-DIY',
    bucket_key: '2026-09-07',
    total_qty: '13',
    contributions,
    locations: [],
    contested_count: 0,
  } as unknown as BoardCell;
}

/** The line the whole plan is about: the engine offered the pool, the planner bought. */
const amended = line({
  key: 'amended',
  covered: true,
  proposed: {
    components: [
      source({ kind: 'reserve', rung: 'pool', qty: '71', location: 'BRW' }),
    ],
  },
  sources: [source({ kind: 'buy', rung: 'buy', qty: '71', location: null })],
  decision: {
    revision_no: 1,
    timely_spo_qty: '0',
    reserve: [],
    borrow: [],
    buy_qty: '71',
  },
});

/** Taken as it stood: one kind, both figures the same. */
const untouched = line({
  key: 'untouched',
  covered: true,
  proposed: {
    components: [
      source({
        kind: 'reserve',
        rung: 'group_take',
        qty: '40',
        location: 'DC1-BB',
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
  ],
  decision: {
    revision_no: 1,
    timely_spo_qty: '0',
    reserve: [
      {
        warehouse_id: 'wh-dc1',
        location: 'DC1-BB',
        qty: '40',
        rung: 'group_take',
      },
    ],
    borrow: [],
    buy_qty: '0',
  },
});

/** Nobody has decided it yet: it counts as suggested and as nothing decided. */
const undecided = line({
  key: 'undecided',
  proposed: {
    components: [
      source({ kind: 'reserve', rung: 'pool', qty: '12', location: 'BRW' }),
    ],
  },
  sources: [
    source({ kind: 'reserve', rung: 'pool', qty: '12', location: 'BRW' }),
  ],
});

function card(totals: ReturnType<typeof decisionStripTotals>, kind: string) {
  return totals.find((entry) => entry.kind === kind);
}

describe('decisionStripTotals', () => {
  it('sums both sides per kind over the whole selection', () => {
    const totals = decisionStripTotals([amended, untouched, undecided], {});

    expect(card(totals, 'shared')).toEqual({
      kind: 'shared',
      suggested: '83',
      decided: '0',
      changed: true,
    });
    expect(card(totals, 'buy')).toEqual({
      kind: 'buy',
      suggested: '0',
      decided: '71',
      changed: true,
    });
    expect(card(totals, 'own')).toEqual({
      kind: 'own',
      suggested: '40',
      decided: '40',
      changed: false,
    });
  });

  it('carries the four cards in the Decide menu order (owner, 12 Oct 2026)', () => {
    // Fixed, and always four: what makes two boards comparable is that each kind is in the
    // same place whether it is 300 or absent. The kinds the planner no longer suggests are
    // folded in (`STRIP_ORDER`), never given a card of their own.
    expect(decisionStripTotals([], {}).map((entry) => entry.kind)).toEqual([
      'own',
      'borrow_other',
      'shared',
      'buy',
    ]);
  });

  it('an undecided line counts as suggested and as nothing decided', () => {
    const totals = decisionStripTotals([undecided], {});

    expect(card(totals, 'shared')).toEqual({
      kind: 'shared',
      suggested: '12',
      decided: '0',
      changed: true,
    });
  });

  it('a draft moves the decided figure before anything is confirmed', () => {
    const totals = decisionStripTotals([undecided], {
      undecided: { verdict: 'approved' },
    });

    // An approval takes the proposal as it stands, so the two agree and the card is unmarked.
    expect(card(totals, 'shared')).toEqual({
      kind: 'shared',
      suggested: '12',
      decided: '12',
      changed: false,
    });
  });

  it('a rejection decides no supply and leaves the suggestion standing', () => {
    const totals = decisionStripTotals([undecided], {
      undecided: { verdict: 'rejected', reason: 'no' },
    });

    expect(card(totals, 'shared')?.decided).toBe('0');
    expect(card(totals, 'shared')?.suggested).toBe('12');
  });

  it('a covered line whose revision recorded no proposal contributes to Decided only', () => {
    const old = line({
      key: 'old',
      covered: true,
      sources: [source({ kind: 'reserve', qty: '9', location: 'DC1-BB' })],
      decision: {
        revision_no: 1,
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-dc1', location: 'DC1-BB', qty: '9' }],
        borrow: [],
        buy_qty: '0',
      },
    });

    const totals = decisionStripTotals([old], {});

    expect(card(totals, 'own')).toEqual({
      kind: 'own',
      suggested: '0',
      decided: '9',
      changed: true,
    });
  });
});

describe('cellCarriesKind', () => {
  it('matches on the suggested side', () => {
    expect(cellCarriesKind(cell([amended]), {}, 'shared')).toBe(true);
  });

  it('matches on the decided side', () => {
    expect(cellCarriesKind(cell([amended]), {}, 'buy')).toBe(true);
  });

  it('says no for a kind neither side carries', () => {
    expect(cellCarriesKind(cell([amended]), {}, 'incoming')).toBe(false);
  });

  it('follows the draft, so filtering by Buy releases a cell amended off it', () => {
    const bought = line({
      key: 'bought',
      sources: [
        source({ kind: 'buy', rung: 'buy', qty: '13', location: null }),
      ],
      proposed: {
        components: [
          source({ kind: 'buy', rung: 'buy', qty: '13', location: null }),
        ],
      },
    });

    expect(cellCarriesKind(cell([bought]), {}, 'own')).toBe(false);
    expect(
      cellCarriesKind(
        cell([bought]),
        { bought: { verdict: 'rejected', reason: 'no' } },
        'buy',
      ),
    ).toBe(true);
  });
});

/**
 * The filter and the figures are asked of the SAME population, or a card reads a number the
 * view cannot produce and empties itself when pressed.
 */
describe('contributionCarriesKind', () => {
  it('is what cellCarriesKind is built from, so the two cannot disagree', () => {
    expect(contributionCarriesKind(amended, null, 'shared')).toBe(true);
    expect(contributionCarriesKind(amended, null, 'buy')).toBe(true);
    expect(contributionCarriesKind(amended, null, 'incoming')).toBe(false);
    expect(cellCarriesKind(cell([amended]), {}, 'shared')).toBe(
      contributionCarriesKind(amended, null, 'shared'),
    );
  });

  it('every kind the totals count for a line is a kind that line survives the filter on', () => {
    // The invariant that keeps a press from emptying the view: if the strip added this line
    // to a card, pressing that card has to keep the line.
    const population = [amended, untouched, undecided];
    for (const total of decisionStripTotals(population, {})) {
      const counted = total.suggested !== '0' || total.decided !== '0';
      const survivors = population.filter((entry) =>
        contributionCarriesKind(entry, null, total.kind),
      );
      expect(survivors.length > 0).toBe(counted);
    }
  });

  it('follows the draft on both sides, so an amendment moves the filter with it', () => {
    expect(
      contributionCarriesKind(
        undecided,
        { verdict: 'rejected', reason: 'no' },
        'shared',
      ),
    ).toBe(true);
    expect(
      contributionCarriesKind(
        undecided,
        {
          verdict: 'amended',
          reserve: [],
          borrow: [],
          buy_qty: '12',
          reason: 'buy it',
        },
        'buy',
      ),
    ).toBe(true);
  });
});
