/**
 * ONE vocabulary for where supply comes from, keyed on the ENGINE'S RUNG and on nothing else.
 *
 * These tests were `boardSuggestion.test.ts`, and half of them pinned the bug: the file split
 * own from shared on the warehouse code's SITE PREFIX, so a pool draw from `BRW` on a line
 * fulfilled from `BRW-BB` read as "Use own location". The captain, on that exact cell: "Use own
 * location, 71 from BRW reads wrong. Own location is the line's -BB location. BRW is the SHARED
 * pool." So the cases that asserted the prefix rule are inverted here rather than deleted - they
 * are the regression.
 */
import { describe, expect, it } from 'vitest';
import {
  COLOURS,
  LABELS,
  SHORT_LABELS,
  boardOrderInquiryWord,
  cellSupply,
  contributionSupply,
  describe as describeSupply,
  dominantText,
  rowOf,
  rowText,
  segmentsOf,
  decisionBreakdown,
  movesOf,
  movesText,
  suggestionBreakdown,
  takenByLocation,
} from './supplyVocabulary';
import type {
  BoardCell,
  BoardContribution,
  BoardDecision,
  BoardLineDecision,
  BoardLineOrderInquiry,
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
    item_code: 'CB2805A-DIY',
    bucket_key: '2026-09-01',
    total_qty: '13',
    locations: [],
    contributions,
    unplannable_count: 0,
    contested_count: 0,
  };
}

/** The row for a label, by its label. */
function row(rows: ReturnType<typeof suggestionBreakdown>, label: string) {
  const found = rows.find((entry) => entry.label === label);
  if (!found) throw new Error(`no row labelled ${label}`);
  return found;
}

/** Is this label on the card at all? A row with no quantity is not. */
function has(rows: ReturnType<typeof suggestionBreakdown>, label: string) {
  return rows.some((entry) => entry.label === label);
}

describe('rowOf reads the rung, never the warehouse code', () => {
  it('names every rung the engine can produce', () => {
    // The rung strings are `front_planning_engine.py`'s own constants. A rung this table does
    // not name is a rung the board would colour as nothing at all.
    expect(rowOf({ kind: 'reserve', rung: 'pool', qty: '1' })).toBe('shared');
    expect(rowOf({ kind: 'reserve', rung: 'group_take', qty: '1' })).toBe(
      'own',
    );
    // A group borrow names its donor SO only with "Borrow from another order" on
    // (BORROW-LOCATION-ONLY D3); with none named it is a location borrow.
    expect(
      rowOf({ kind: 'borrow', rung: 'group_borrow', qty: '1', donor_so_number: 'SO1' }),
    ).toBe('borrow_order');
    expect(rowOf({ kind: 'borrow', rung: 'group_borrow', qty: '1' })).toBe(
      'borrow_other',
    );
    expect(
      rowOf({ kind: 'borrow', rung: 'cross_group_borrow', qty: '1' }),
    ).toBe('borrow_other');
    expect(rowOf({ kind: 'buy', rung: 'buy', qty: '1' })).toBe('buy');
    expect(rowOf({ kind: 'timely_spo', rung: 'incoming', qty: '1' })).toBe(
      'incoming',
    );
  });

  it('names ladder v7.1 own two borrow rungs, and keeps them apart', () => {
    // Step 2 borrows ON HAND held by a later order, step 3 the DOCUMENT it is waiting on.
    // Neither was in the switch, so both resolved to null and every step-2 borrow vanished
    // from the strip, the bar, the card and the popover behind 'Cannot be sourced'.
    //
    // TWO WORDS, NOT ONE (the captain, 30 Aug 2026): they were both read as
    // `borrow_order`, so step 3 had no card of its own and the walk showed four steps where
    // the engine asks five. What is taken differs - stock a picker can walk to against a
    // document still at sea - and so does when the asker gets it.
    expect(
      rowOf({
        kind: 'borrow',
        rung: 'order_borrow',
        qty: '30',
        location: 'MWH-IB',
        donor_so_number: 'SO414285',
      }),
    ).toBe('borrow_order');
    expect(
      rowOf({
        kind: 'borrow',
        rung: 'supply_borrow',
        qty: '50',
        location: 'BRW-IB',
        donor_so_number: 'SO414285',
      }),
    ).toBe('borrow_incoming');
  });

  it('splits step 1 into the floor and the water, on the component kind (30 Aug 2026)', () => {
    // ONE RUNG, TWO PROMISES. `_draw_group` composes `kind=TIMELY_SPO if water else RESERVE`
    // and stamps `rung=group_take` on both, so reading the rung alone printed a container at
    // sea under "Use own location" beside stock on a floor. Neither is a borrow: free stock
    // owes nobody a thing, wherever it happens to be.
    expect(
      rowOf({
        kind: 'reserve',
        rung: 'group_take',
        qty: '40',
        location: 'BRW-BB',
      }),
    ).toBe('own');
    expect(
      rowOf({
        kind: 'timely_spo',
        rung: 'group_take',
        qty: '25',
        location: 'MWH-BB',
      }),
    ).toBe('incoming');
  });

  it('tells the pool free draw from the pool BORROW, which owes a debt back (R34)', () => {
    // Step 4a takes the free pile and nobody is owed it; step 4b takes a LATER POOL ORDER's
    // on hand and raises an ORDER_BACK against it. Both carry `rung: 'pool'`, so the donor
    // is what distinguishes them - without it the borrow read as the free 'Use BRW' and the
    // debt was invisible.
    expect(
      rowOf({ kind: 'reserve', rung: 'pool', qty: '30', location: 'BRW' }),
    ).toBe('shared');
    expect(
      rowOf({
        kind: 'borrow',
        rung: 'pool',
        qty: '30',
        location: 'BRW',
        donor_so_number: 'SO414285',
      }),
    ).toBe('borrow_order');
  });

  it('reads a FREE step-3 document as a borrow all the same (S4)', () => {
    // A document nobody was waiting on names no donor, and the unrunged fallback would
    // read that as 'borrow_other' - "Borrow other location", which is a location this
    // line never borrows from. The RUNG decides, as it does everywhere else here.
    expect(
      rowOf({
        kind: 'borrow',
        rung: 'supply_borrow',
        qty: '32',
        location: 'BRW-BB',
      }),
    ).toBe('borrow_incoming');
  });

  it('gives every kind a label and a colour, both a swatch and a text token', () => {
    for (const kind of [
      'buy',
      'shared',
      'own',
      'borrow_order',
      'borrow_incoming',
      'borrow_other',
      'incoming',
    ] as const) {
      expect(LABELS[kind]).toBeTruthy();
      expect(COLOURS[kind].bar).toMatch(/^bg-/);
      expect(COLOURS[kind].text).toMatch(/^text-/);
    }
    expect(LABELS.shared).toBe('Use BRW');
    expect(LABELS.own).toBe('Use own location');
    expect(LABELS.borrow_order).toBe('Borrow from another order');
    // Owner hand test, 9 Oct 2026: "just called it borrow cause it is not just other
    // location, own location also can borrow". The order borrow keeps its own words.
    expect(LABELS.borrow_other).toBe('Borrow');
    expect(SHORT_LABELS.borrow_other).toBe('Borrow');
    expect(SHORT_LABELS.borrow_order).toBe('Borrow (order)');
    // The two words of 30 Aug 2026. "Use", because the group's own incoming document is
    // taken free and owes nobody anything; "Borrow", because a later order's document is
    // taken from somebody and raises an order-back.
    expect(LABELS.incoming).toBe('Use incoming');
    expect(LABELS.borrow_incoming).toBe('Borrow incoming');
    // All three borrows share the amber, and are told apart by their words alone.
    expect(COLOURS.borrow_incoming.bar).toBe(COLOURS.borrow_order.bar);
  });

  it('reads the pool as SHARED whatever site it is at, and however the line is coded', () => {
    // AC-A1, and the regression. `BRW` and `BRW-BB` share a prefix; they are not the same pile.
    expect(
      rowOf({ kind: 'reserve', rung: 'pool', qty: '71', location: 'BRW' }),
    ).toBe('shared');
    expect(
      rowOf({ kind: 'reserve', rung: 'pool', qty: '4', location: 'MWH' }),
    ).toBe('shared');
  });

  it('reads a group take as the agent own location, whatever site the sibling is at', () => {
    expect(
      rowOf({
        kind: 'reserve',
        rung: 'group_take',
        qty: '454',
        location: 'DC1-BB',
      }),
    ).toBe('own');
  });

  it('proposes nothing at all for a line the ladder was never walked for', () => {
    expect(rowOf({ kind: 'unplannable', qty: '13' })).toBeNull();
  });

  /**
   * A component with NO rung, which is every source and every decision row of a COVERED line:
   * the board rebuilds a frozen composition without carrying the rung the engine froze. Read
   * live off SO324132 rev 1 on 25 Aug - three reserve sources at DC1-BB / MWH-BB / WH3-BB, all
   * `rung: null`, on a BRW-BB line - which is what made AC-A2 read "Use BRW".
   *
   * The reading is on the OWNERSHIP GROUP: the suffix after the first hyphen, the backend's own
   * rule (`sales_agent_service.group_of_warehouse_code`). Not the site (which called the shared
   * pool BRW the line's own stock) and not the exact code (which called the agent's own DC1-BB
   * somebody else's).
   */
  describe('a component with no rung of its own', () => {
    it('reads a sibling of the line own group as its own location (AC-A2)', () => {
      expect(
        rowOf({ kind: 'reserve', qty: '454', location: 'DC1-BB' }, 'BRW-BB'),
      ).toBe('own');
      expect(
        rowOf({ kind: 'reserve', qty: '267', location: 'MWH-BB' }, 'BRW-BB'),
      ).toBe('own');
      expect(
        rowOf({ kind: 'reserve', qty: '211', location: 'WH3-BB' }, 'BRW-BB'),
      ).toBe('own');
      // The line's own warehouse itself, which is the same group by definition.
      expect(
        rowOf({ kind: 'reserve', qty: '10', location: 'BRW-BB' }, 'BRW-BB'),
      ).toBe('own');
    });

    it('reads a bare site code as the shared pool (AC-A1)', () => {
      for (const pool of ['BRW', 'MWH', 'DC1', 'WH3', 'RSW']) {
        expect(
          rowOf({ kind: 'reserve', qty: '71', location: pool }, 'BRW-BB'),
        ).toBe('shared');
      }
    });

    it('reads another group warehouse as borrowing from another location', () => {
      expect(
        rowOf({ kind: 'reserve', qty: '9', location: 'BRW-HP' }, 'BRW-BB'),
      ).toBe('borrow_other');
      expect(
        rowOf({ kind: 'borrow', qty: '9', location: 'DC1-IB' }, 'BRW-BB'),
      ).toBe('borrow_other');
    });

    it('reads a borrow that names a donor order as a borrow from another order', () => {
      // The donor SO is named: with none, the same rung is a location borrow
      // (BORROW-LOCATION-ONLY D3).
      expect(
        rowOf(
          {
            kind: 'borrow',
            rung: 'group_borrow',
            qty: '3',
            location: 'BRW-BB',
            donor_so_number: 'SO415472',
          },
          'BRW-BB',
        ),
      ).toBe('borrow_order');
    });

    /**
     * The Amend dialog's BorrowAddDialog drafts a borrow with NO rung: the engine never ran
     * for it, CS picked the donor line by hand. Read on the ownership group it would come back
     * "Use own location" for a donor sitting at the line's OWN `-BB` warehouse - which is the
     * one thing a borrow can never be. The quantity belongs to another order and it raises an
     * order-back; nothing about it is this line's own stock.
     */
    it('never reads an unrunged borrow as the line own location, however close the donor is', () => {
      expect(
        rowOf(
          {
            kind: 'borrow',
            qty: '71',
            location: 'BRW-BB',
            donor_so_number: 'SO415472',
          },
          'BRW-BB',
        ),
      ).toBe('borrow_order');
      // A sibling of the same group at another site - the group reading called this "own" too.
      expect(
        rowOf(
          {
            kind: 'borrow',
            qty: '30',
            location: 'DC1-BB',
            donor_so_number: 'SO404352',
          },
          'BRW-BB',
        ),
      ).toBe('borrow_order');
    });

    it('reads an unrunged borrow that names no donor order as borrowing another location', () => {
      expect(
        rowOf({ kind: 'borrow', qty: '9', location: 'BRW-BB' }, 'BRW-BB'),
      ).toBe('borrow_other');
      expect(
        rowOf({ kind: 'borrow', qty: '9', location: 'BRW' }, 'BRW-BB'),
      ).toBe('borrow_other');
    });

    it('reads a pool when the line own location is unknown, never as its own stock', () => {
      // Nothing to compare against is not a licence to claim the agent's group holds it.
      expect(rowOf({ kind: 'reserve', qty: '5', location: 'DC1-BB' })).toBe(
        'borrow_other',
      );
      expect(rowOf({ kind: 'reserve', qty: '5', location: 'BRW' })).toBe(
        'shared',
      );
    });

    it('still reads Buy and incoming off their kind, which never needed a location', () => {
      expect(rowOf({ kind: 'buy', qty: '5' })).toBe('buy');
      expect(rowOf({ kind: 'timely_spo', qty: '5' })).toBe('incoming');
    });
  });
});

describe('segmentsOf', () => {
  it('sums by kind and keeps only the kinds with a quantity, in the fixed order', () => {
    const segments = segmentsOf([
      { kind: 'reserve', rung: 'group_take', qty: '454', location: 'DC1-BB' },
      { kind: 'buy', rung: 'buy', qty: '3' },
      { kind: 'reserve', rung: 'group_take', qty: '267', location: 'MWH-BB' },
      { kind: 'reserve', rung: 'pool', qty: '0', location: 'BRW' },
    ]);

    expect(segments).toEqual([
      { kind: 'own', qty: '721' },
      { kind: 'buy', qty: '3' },
    ]);
  });

  it('names the largest kind in the fewest words a cell has room for', () => {
    expect(
      dominantText(
        segmentsOf([
          { kind: 'reserve', rung: 'pool', qty: '71', location: 'BRW' },
          { kind: 'buy', rung: 'buy', qty: '4' },
        ]),
      ),
    ).toBe('BRW 71');
    expect(dominantText([])).toBe('');
  });
});

describe('describe', () => {
  it('renders a composition as one compact line, naming where each kind came from', () => {
    expect(
      describeSupply([
        { kind: 'reserve', rung: 'pool', qty: '71', location: 'BRW' },
        { kind: 'buy', rung: 'buy', qty: '12' },
      ]),
    ).toBe('BRW 71 (BRW) · Buy 12');
  });

  it('reads a SupplyComponent, which spells its warehouse source_location', () => {
    expect(
      describeSupply([
        {
          kind: 'reserve',
          rung: 'group_take',
          qty: '454',
          source_location: 'DC1-BB',
        },
        {
          kind: 'reserve',
          rung: 'group_take',
          qty: '267',
          source_location: 'MWH-BB',
        },
      ]),
    ).toBe('Own 721 (DC1-BB, MWH-BB)');
  });

  it('says nothing for a composition with nothing in it', () => {
    expect(describeSupply([])).toBe('');
    expect(describeSupply([{ kind: 'unplannable', qty: '13' }])).toBe('');
  });
});

describe('suggestionBreakdown', () => {
  it('states only the kinds with a quantity, keeping their fixed order', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          sources: [
            source({ kind: 'buy', rung: 'buy', qty: '3', location: null }),
            source({
              kind: 'reserve',
              rung: 'pool',
              qty: '13',
              location: 'BRW',
            }),
          ],
        }),
      ]),
    );

    expect(rows.map((entry) => entry.label)).toEqual(['Use BRW', 'Buy']);
  });

  it('says nothing at all for a cell the ladder proposes nothing for', () => {
    expect(suggestionBreakdown(cell([line()]))).toEqual([]);
  });

  it('reads a pool draw as shared stock, and names the pool it came from (AC-A1)', () => {
    // SRT382-6-DIY on SO415472: "Use BRW 71 from BRW", never "Use own location".
    const rows = suggestionBreakdown(
      cell([
        line({
          sources: [
            source({
              kind: 'reserve',
              rung: 'pool',
              qty: '71',
              location: 'BRW',
            }),
          ],
        }),
      ]),
    );

    expect(row(rows, 'Use BRW').qty).toBe('71');
    expect(rowText(row(rows, 'Use BRW'))).toBe('71 from BRW');
    expect(has(rows, 'Use own location')).toBe(false);
  });

  it('names the quantity PER LOCATION on a group take (AC-A2)', () => {
    // CWCY605 on SO324132 rev 1. The split is the instruction: three movements of stock,
    // and somebody has to key each one.
    const rows = suggestionBreakdown(
      cell([
        line({
          sources: [
            source({
              kind: 'reserve',
              rung: 'group_take',
              qty: '454',
              location: 'DC1-BB',
            }),
            source({
              kind: 'reserve',
              rung: 'group_take',
              qty: '267',
              location: 'MWH-BB',
            }),
            source({
              kind: 'reserve',
              rung: 'group_take',
              qty: '211',
              location: 'WH3-BB',
            }),
          ],
        }),
      ]),
    );

    expect(row(rows, 'Use own location').qty).toBe('932');
    expect(rowText(row(rows, 'Use own location'))).toBe(
      '454 from DC1-BB, 267 from MWH-BB, 211 from WH3-BB',
    );
  });

  it('tells the two borrows apart by rung, not by where they happen to be (AC-A3)', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          sources: [
            // Names its donor SO: a donor-less one reads as a location borrow
            // (BORROW-LOCATION-ONLY D3).
            source({
              kind: 'borrow',
              rung: 'group_borrow',
              qty: '3',
              location: 'BRW-BB',
              donor_so_number: 'SO415472',
            }),
            source({
              kind: 'borrow',
              rung: 'cross_group_borrow',
              qty: '9',
              location: 'BRW-HP',
            }),
          ],
        }),
      ]),
    );

    expect(row(rows, 'Borrow from another order').qty).toBe('3');
    expect(row(rows, 'Borrow other location').qty).toBe('9');
    expect(has(rows, 'Use own location')).toBe(false);
  });

  it('reads a buy as a buy, and names no location because it is held nowhere yet', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          sources: [
            source({ kind: 'buy', rung: 'buy', qty: '13', location: null }),
          ],
        }),
      ]),
    );

    expect(row(rows, 'Buy').places).toEqual([]);
    expect(rowText(row(rows, 'Buy'))).toBe('13');
  });

  it('sums across every line of the cell and totals each location once', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          key: 'a',
          sources: [
            source({
              kind: 'reserve',
              rung: 'pool',
              qty: '5',
              location: 'BRW',
            }),
          ],
        }),
        line({
          key: 'b',
          sources: [
            source({
              kind: 'reserve',
              rung: 'pool',
              qty: '8',
              location: 'BRW',
            }),
            source({
              kind: 'reserve',
              rung: 'group_take',
              qty: '2',
              location: 'MWH-BB',
            }),
          ],
        }),
      ]),
    );

    expect(rowText(row(rows, 'Use BRW'))).toBe('13 from BRW');
    expect(row(rows, 'Use own location').qty).toBe('2');
  });

  it('carries incoming as its own row, ahead of Buy, and only when there is some', () => {
    // Never folded into Buy: the incoming quantity is already bought and on its way, and
    // adding it to Buy would propose buying it twice.
    //
    // "Use incoming", and AHEAD OF BUY rather than trailing the list (the captain, 30 Aug
    // 2026). It reads as a step of the walk now, because under v7.1 it is one - step 1 off
    // the group's water - and Buy is what the walk ends on.
    expect(has(suggestionBreakdown(cell([line()])), 'Use incoming')).toBe(
      false,
    );

    const rows = suggestionBreakdown(
      cell([
        line({
          sources: [
            source({ kind: 'buy', rung: 'buy', qty: '2' }),
            source({
              kind: 'timely_spo',
              rung: 'incoming',
              qty: '7',
              location: 'BRW-BB',
            }),
          ],
        }),
      ]),
    );

    expect(row(rows, 'Use incoming').qty).toBe('7');
    expect(rows.map((entry) => entry.label)).toEqual(['Use incoming', 'Buy']);
  });

  it('carries the engine own sentence when every source on the row gives the same one', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          sources: [
            source({
              kind: 'buy',
              rung: 'buy',
              qty: '13',
              location: null,
              reason:
                'Delivery date beyond the lead time window; stock kept for nearer orders',
            }),
          ],
        }),
      ]),
    );

    expect(row(rows, 'Buy').note).toBe(
      'Delivery date beyond the lead time window; stock kept for nearer orders',
    );
  });

  it('says nothing when the lines on a row disagree about why', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          key: 'a',
          sources: [
            source({
              kind: 'buy',
              rung: 'buy',
              qty: '5',
              reason: 'one reason',
            }),
          ],
        }),
        line({
          key: 'b',
          sources: [
            source({ kind: 'buy', rung: 'buy', qty: '5', reason: 'another' }),
          ],
        }),
      ]),
    );

    expect(row(rows, 'Buy').qty).toBe('10');
    expect(row(rows, 'Buy').note).toBeUndefined();
  });

  it('says nothing can be sourced for a line whose order states no location', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          unplannable: true,
          sources: [source({ kind: 'unplannable', qty: '13' })],
        }),
      ]),
    );

    expect(rows).toEqual([]);
  });
});

/**
 * SO324132 rev 1, EXACTLY as the board sends it (read live off :8080 on 25 Aug).
 *
 * CWCY605 is a COVERED line, and `_apply_frozen` rebuilds its composition without the rung the
 * engine froze: three reserve sources at DC1-BB / MWH-BB / WH3-BB, `rung: null` on every one,
 * and a decision whose three reserve rows are the same. Both surfaces therefore read the
 * fallback, and both read "Use BRW" until the fallback learned about ownership groups.
 */
describe('AC-A2: a covered line whose composition arrives with no rungs', () => {
  const unrunged = [
    {
      kind: 'reserve' as const,
      qty: '454',
      location: 'DC1-BB',
      reason: 'Reserved at DC1-BB.',
    },
    {
      kind: 'reserve' as const,
      qty: '267',
      location: 'MWH-BB',
      reason: 'Reserved at MWH-BB.',
    },
    {
      kind: 'reserve' as const,
      qty: '211',
      location: 'WH3-BB',
      reason: 'Reserved at WH3-BB.',
    },
  ];

  const covered = line({
    item_code: 'CWCY605',
    qty: '932',
    qty_outstanding: '932',
    fulfilment_location: 'BRW-BB',
    covered: true,
    sources: unrunged,
    decision: {
      revision_no: 1,
      timely_spo_qty: '0',
      reserve: [
        { warehouse_id: 'wh-dc1', location: 'DC1-BB', qty: '454' },
        { warehouse_id: 'wh-mwh', location: 'MWH-BB', qty: '267' },
        { warehouse_id: 'wh-wh3', location: 'WH3-BB', qty: '211' },
      ],
      borrow: [],
      buy_qty: '0',
    },
  });

  it('the Decision card reads Use own location, named per location', () => {
    const rows = decisionBreakdown(cell([covered]), {});

    expect(rows.map((entry) => entry.label)).toEqual(['Use own location']);
    expect(rowText(rows[0])).toBe(
      '454 from DC1-BB, 267 from MWH-BB, 211 from WH3-BB',
    );
  });

  it('and the Suggestion card is silent, because this revision recorded none', () => {
    // AC-D1: a decision frozen before `proposed_components` existed. The suggestion is NOT
    // the decision rebuilt - reading it that way printed every amended line as having
    // suggested exactly what somebody changed it to.
    expect(suggestionBreakdown(cell([covered]))).toEqual([]);
  });

  it('the decided bar reads own, solid, off the same composition', () => {
    const supply = contributionSupply(covered, null);

    expect(supply.segments).toEqual([{ kind: 'own', qty: '932' }]);
    expect(supply.decided).toBe(true);
  });

  it('a BRW pool draw on the same line still reads shared (AC-A1 is not undone)', () => {
    const pooled = line({
      fulfilment_location: 'BRW-BB',
      covered: true,
      sources: [
        {
          kind: 'reserve',
          qty: '71',
          location: 'BRW',
          reason: 'Reserved at BRW.',
        },
      ],
      decision: {
        revision_no: 1,
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-brw', location: 'BRW', qty: '71' }],
        borrow: [],
        buy_qty: '0',
      },
    });

    expect(decisionBreakdown(cell([pooled]), {})[0].label).toBe('Use BRW');
    expect(contributionSupply(pooled, null).segments).toEqual([
      { kind: 'shared', qty: '71' },
    ]);
  });

  it('resolves per contributing line, because a cell spans lines at different groups', () => {
    // DC1-BB is the agent's own group on a BRW-BB line and somebody else's on a BRW-IB one.
    const ib = line({
      key: 'ib',
      fulfilment_location: 'BRW-IB',
      sources: [
        { kind: 'reserve', qty: '100', location: 'DC1-BB', reason: 'r' },
      ],
    });

    // The covered line answers on the DECISION side, the undecided one on the suggestion
    // side, and both are resolved against their OWN line's location.
    const decided = decisionBreakdown(cell([covered, ib]), {});
    const suggested = suggestionBreakdown(cell([covered, ib]));

    expect(decided.map((entry) => entry.label)).toEqual(['Use own location']);
    expect(decided[0].qty).toBe('932');
    expect(suggested.map((entry) => entry.label)).toEqual([
      'Borrow other location',
    ]);
    expect(suggested[0].qty).toBe('100');
  });
});

describe('what the bar is drawn from', () => {
  const buyProposal = line({
    sources: [
      source({ kind: 'buy', rung: 'buy', qty: '71', location: null }),
      source({
        kind: 'reserve',
        rung: 'pool',
        qty: '0',
        location: 'BRW',
        warehouse_id: 'wh-brw',
      }),
    ],
  });

  it('draws the engine proposal, faded, while nothing has been decided', () => {
    const supply = contributionSupply(buyProposal, null);

    expect(supply.segments).toEqual([{ kind: 'buy', qty: '71' }]);
    expect(supply.decided).toBe(false);
  });

  it('flips Buy to Shared the moment an amend is ticked, and back when it is cleared (AC-C2)', () => {
    const amended: BoardDecision = {
      verdict: 'amended',
      reserve: [{ warehouse_id: 'wh-brw', location: 'BRW', qty: '71' }],
      borrow: [],
      buy_qty: '0',
      reason: 'The pool can cover it',
    };

    const ticked = contributionSupply(buyProposal, amended);
    expect(ticked.segments).toEqual([{ kind: 'shared', qty: '71' }]);
    expect(ticked.decided).toBe(true);

    const cleared = contributionSupply(buyProposal, null);
    expect(cleared.segments).toEqual([{ kind: 'buy', qty: '71' }]);
    expect(cleared.decided).toBe(false);
  });

  it('keeps the proposal on an approval, because an approval is the proposal', () => {
    const supply = contributionSupply(buyProposal, { verdict: 'approved' });

    expect(supply.segments).toEqual([{ kind: 'buy', qty: '71' }]);
    expect(supply.decided).toBe(true);
  });

  it('leaves a rejection faded: a rejection settles no supply', () => {
    const supply = contributionSupply(buyProposal, {
      verdict: 'rejected',
      reason: 'Rejected on the planning board.',
    });

    expect(supply.decided).toBe(false);
  });

  it('draws a confirmed revision solid, in the words its own rungs give it', () => {
    const frozen: BoardLineDecision = {
      revision_no: 1,
      timely_spo_qty: '0',
      reserve: [
        { warehouse_id: 'wh-dc1', location: 'DC1-BB', qty: '454' },
        { warehouse_id: 'wh-mwh', location: 'MWH-BB', qty: '267' },
      ],
      borrow: [],
      buy_qty: '0',
    };
    const covered = line({
      covered: true,
      decision: frozen,
      sources: [
        source({
          kind: 'reserve',
          rung: 'group_take',
          qty: '454',
          location: 'DC1-BB',
          warehouse_id: 'wh-dc1',
        }),
        source({
          kind: 'reserve',
          rung: 'group_take',
          qty: '267',
          location: 'MWH-BB',
          warehouse_id: 'wh-mwh',
        }),
      ],
    });

    const supply = contributionSupply(covered, null);

    expect(supply.segments).toEqual([{ kind: 'own', qty: '721' }]);
    expect(supply.decided).toBe(true);
  });

  it('names a drafted borrow that cites a donor order as a borrow from another order', () => {
    const supply = contributionSupply(buyProposal, {
      verdict: 'amended',
      reserve: [],
      borrow: [
        {
          source: 'other_location',
          warehouse_id: 'wh-x',
          warehouse_code: 'BRW-BB',
          qty: '71',
          reason: 'Authorised by agent',
          donor_so_number: 'SO394803',
        },
      ],
      buy_qty: '0',
    });

    expect(supply.segments).toEqual([{ kind: 'borrow_order', qty: '71' }]);
  });

  it('is solid for a cell only once EVERY contributing line is settled', () => {
    const a = line({
      key: 'a',
      sources: [source({ kind: 'buy', rung: 'buy', qty: '10' })],
    });
    const b = line({
      key: 'b',
      sources: [
        source({ kind: 'reserve', rung: 'pool', qty: '30', location: 'BRW' }),
      ],
    });

    const half = cellSupply(cell([a, b]), { a: { verdict: 'approved' } });
    expect(half.decided).toBe(false);
    expect(half.segments).toEqual([
      { kind: 'shared', qty: '30' },
      { kind: 'buy', qty: '10' },
    ]);

    const whole = cellSupply(cell([a, b]), {
      a: { verdict: 'approved' },
      b: { verdict: 'approved' },
    });
    expect(whole.decided).toBe(true);
  });
});

/**
 * The Moves line under the Decision card (`PLAN-scm-cs-planning-uat.md` section E).
 *
 * What Approve will ask a warehouse to physically carry, said before it is pressed.
 */
describe('movesOf', () => {
  const covered = (over: Partial<BoardLineDecision> = {}) =>
    line({
      covered: true,
      decision: {
        reserve: [
          {
            warehouse_id: 'wh-dc1',
            location: 'DC1-BB',
            qty: '454',
            rung: 'group_take',
          },
          {
            warehouse_id: 'wh-mwh',
            location: 'MWH-BB',
            qty: '267',
            rung: 'group_take',
          },
        ],
        borrow: [],
        buy_qty: '0',
        timely_spo_qty: '0',
        ...over,
      } as BoardLineDecision,
    });

  it('names every movement the decision implies, per (from, to) pair', () => {
    const moves = movesOf(cell([covered()]), {});

    expect(moves).toEqual([
      { from: 'DC1-BB', to: 'BRW-BB', qty: '454' },
      { from: 'MWH-BB', to: 'BRW-BB', qty: '267' },
    ]);
    expect(movesText(moves)).toBe(
      '454 DC1-BB -> BRW-BB · 267 MWH-BB -> BRW-BB',
    );
  });

  it("says nothing about stock already at the line's own location", () => {
    const moves = movesOf(
      cell([
        covered({
          reserve: [
            {
              warehouse_id: 'wh-brw-bb',
              location: 'BRW-BB',
              qty: '40',
              rung: 'group_take',
            },
          ],
        } as Partial<BoardLineDecision>),
      ]),
      {},
    );

    expect(moves).toEqual([]);
    expect(movesText(moves)).toBe('');
  });

  it('says nothing about a Buy or about incoming supply', () => {
    const moves = movesOf(
      cell([
        covered({
          reserve: [],
          buy_qty: '71',
          timely_spo_qty: '30',
        } as Partial<BoardLineDecision>),
      ]),
      {},
    );

    expect(moves).toEqual([]);
  });

  it('sums two lines drawing the same way into ONE movement to key', () => {
    const moves = movesOf(
      cell([covered(), { ...covered(), key: 'so-1:20' }]),
      {},
    );

    expect(moves).toEqual([
      { from: 'DC1-BB', to: 'BRW-BB', qty: '908' },
      { from: 'MWH-BB', to: 'BRW-BB', qty: '534' },
    ]);
  });

  it('follows the DRAFT, so the line changes the moment a tick lands', () => {
    const proposal = line({
      sources: [source({ kind: 'buy', rung: 'buy', qty: '71' })],
    });
    const draft: Record<string, BoardDecision> = {
      [proposal.key]: {
        verdict: 'amended',
        reserve: [
          { warehouse_id: 'wh-brw', location: 'BRW', qty: '71', rung: 'pool' },
        ],
        borrow: [],
        buy_qty: '0',
      } as BoardDecision,
    };

    expect(movesText(movesOf(cell([proposal]), draft))).toBe(
      '71 BRW -> BRW-BB',
    );
    expect(movesText(movesOf(cell([proposal]), {}))).toBe('');
  });
});

/**
 * The Taken column of the cell's location table (AC-B3).
 *
 * The captain's question was "why not MWH", and the answer is a row rather than an absence:
 * MWH was listed, it held stock, and nothing was needed from it. So every location the table
 * shows reads a quantity, and the ones nothing was drawn from read 0.
 */
describe('takenByLocation', () => {
  const covered = (over: Partial<BoardLineDecision> = {}) =>
    line({
      covered: true,
      qty: '932',
      decision: {
        reserve: [
          {
            warehouse_id: 'wh-dc1',
            location: 'DC1-BB',
            qty: '454',
            rung: 'group_take',
          },
          {
            warehouse_id: 'wh-mwh',
            location: 'MWH-BB',
            qty: '267',
            rung: 'group_take',
          },
          {
            warehouse_id: 'wh-wh3',
            location: 'WH3-BB',
            qty: '211',
            rung: 'group_take',
          },
        ],
        borrow: [],
        buy_qty: '0',
        timely_spo_qty: '0',
        ...over,
      } as BoardLineDecision,
    });

  it('sums to the quantity needed when the line is covered from stock', () => {
    const taken = takenByLocation(cell([covered()]), {});

    expect([...taken]).toEqual([
      ['DC1-BB', '454'],
      ['MWH-BB', '267'],
      ['WH3-BB', '211'],
    ]);
    const total = [...taken.values()].reduce(
      (sum, qty) => sum + Number(qty),
      0,
    );
    expect(total).toBe(932);
  });

  it('draws on nothing for a Buy-only cell', () => {
    const buy = line({
      sources: [source({ kind: 'buy', rung: 'buy', qty: '71' })],
    });

    expect([...takenByLocation(cell([buy]), {})]).toEqual([]);
  });

  it('counts what question 1 drew off the water at the row it came to', () => {
    // The water ruling, 27 August 2026: part of the ownership group's offer is an SPO, and
    // it comes off the SPO qty of a row this table already lists. Leaving it out made the
    // table say "Taken 1" under a suggestion reading "Use own location 10 from BRW-SMC".
    const mixed = line({
      qty: '10',
      sources: [
        source({
          kind: 'reserve',
          rung: 'group_take',
          qty: '1',
          location: 'BRW-SMC',
        }),
        source({
          kind: 'timely_spo',
          rung: 'group_take',
          qty: '9',
          location: 'BRW-SMC',
        }),
      ],
    });

    expect([...takenByLocation(cell([mixed]), {})]).toEqual([
      ['BRW-SMC', '10'],
    ]);
  });

  it('leaves incoming supply out: it arrives, it is not taken off a pile', () => {
    const taken = takenByLocation(
      cell([
        covered({
          reserve: [
            {
              warehouse_id: 'wh-dc1',
              location: 'DC1-BB',
              qty: '454',
              rung: 'group_take',
            },
          ],
          timely_spo_qty: '478',
        } as Partial<BoardLineDecision>),
      ]),
      {},
    );

    expect([...taken]).toEqual([['DC1-BB', '454']]);
  });

  it('reads the SUGGESTION while a line is undecided', () => {
    const proposal = line({
      sources: [
        source({ kind: 'reserve', rung: 'pool', qty: '71', location: 'BRW' }),
      ],
    });

    expect([...takenByLocation(cell([proposal]), {})]).toEqual([['BRW', '71']]);
  });

  it('follows the DRAFT, the same switch the cell bar uses', () => {
    const proposal = line({
      sources: [source({ kind: 'buy', rung: 'buy', qty: '71' })],
    });
    const draft: Record<string, BoardDecision> = {
      [proposal.key]: {
        verdict: 'amended',
        reserve: [
          { warehouse_id: 'wh-brw', location: 'BRW', qty: '71', rung: 'pool' },
        ],
        borrow: [],
        buy_qty: '0',
      } as BoardDecision,
    };

    expect([...takenByLocation(cell([proposal]), draft)]).toEqual([
      ['BRW', '71'],
    ]);
    // Tick cleared, and the cell goes back to drawing on nothing.
    expect([...takenByLocation(cell([proposal]), {})]).toEqual([]);
  });

  it('sums two lines drawing on the same location', () => {
    const taken = takenByLocation(
      cell([covered(), { ...covered(), key: 'so-1:20' }]),
      {},
    );

    expect(taken.get('DC1-BB')).toBe('908');
    expect(taken.get('MWH-BB')).toBe('534');
  });

  it('counts a borrow, which is stock carried off a location like any other', () => {
    const borrowed = line({
      sources: [
        source({
          kind: 'borrow',
          rung: 'cross_group_borrow',
          qty: '35',
          location: 'BRW-IB',
        }),
      ],
    });

    expect([...takenByLocation(cell([borrowed]), {})]).toEqual([
      ['BRW-IB', '35'],
    ]);
  });
});

describe("step 1's pill names the group whose stock it is (R-M, 3 Sep 2026)", () => {
  it('reads a draw from another ownership group as that group, not "Use own location"', () => {
    // The captain's production cell: a `BRW-BB` line composed off `BRW-IB`. The board's own
    // options row already read "Use IB group stock" (`front_planning_engine._use_step_label`)
    // while the Suggestion card beside it read "Use own location" for the same composition.
    const rows = suggestionBreakdown(
      cell([
        line({
          fulfilment_location: 'BRW-BB',
          sources: [
            source({
              kind: 'reserve',
              rung: 'group_take',
              qty: '4',
              location: 'BRW-IB',
            }),
          ],
        }),
      ]),
    );

    expect(rows.map((entry) => entry.label)).toEqual(['Use IB group stock']);
  });

  it('keeps "Use own location" where the stock is the line\'s own group', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          fulfilment_location: 'BRW-BB',
          sources: [
            source({
              kind: 'reserve',
              rung: 'group_take',
              qty: '40',
              location: 'DC1-BB',
            }),
          ],
        }),
      ]),
    );

    expect(rows.map((entry) => entry.label)).toEqual([LABELS.own]);
  });

  it('names another group\'s WATER as incoming from that group', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          fulfilment_location: 'BRW-BB',
          sources: [
            source({
              kind: 'timely_spo',
              rung: 'group_take',
              qty: '15',
              location: 'BRW-IB',
            }),
          ],
        }),
      ]),
    );

    expect(rows.map((entry) => entry.label)).toEqual([
      'Use incoming from IB group',
    ]);
  });

  it('names two lending groups together, in the order the label sorts them', () => {
    const rows = suggestionBreakdown(
      cell([
        line({
          fulfilment_location: 'BRW-BB',
          sources: [
            source({
              kind: 'reserve',
              rung: 'group_take',
              qty: '4',
              location: 'BRW-IB',
            }),
            source({
              kind: 'reserve',
              rung: 'group_take',
              qty: '6',
              location: 'DC1-NTC',
            }),
          ],
        }),
      ]),
    );

    expect(rows.map((entry) => entry.label)).toEqual([
      'Use IB group and NTC group stock',
    ]);
  });
});

/**
 * `board-verdict-actions-chips-acceptance-criteria.md` section A: the Product cell's chip
 * ladder, widened from a bare `'received' | 'used' | null` to
 * `{ word: 'received' | 'used' | 'SPO' | 'PO' | 'OI'; title: string } | null` (AC-A7) so a
 * planner can tell an OI that has become a PO from one still sitting open, not only the two
 * states that were already wired (`received` / `used`).
 *
 * RED today: `boardOrderInquiryWord` returns the bare word (or `null`), never an object -
 * every assertion below reading `.word` throws on `undefined` against today's code.
 */
describe('boardOrderInquiryWord: the Product cell chip ladder (AC-A1 to AC-A7)', () => {
  function inquiry(over: Partial<BoardLineOrderInquiry> = {}): BoardLineOrderInquiry {
    return {
      inquiry_no: 'OI-000418',
      state: 'raised',
      documents: [],
      redirected: false,
      ...over,
    };
  }

  it('AC-A1: reads "received" when every document behind the line is received', () => {
    const word = boardOrderInquiryWord(
      inquiry({
        documents: [
          { document: 'SPO-2026/01-0143', kind: 'spo', received: true },
          { document: '202607-S0105', kind: 'po', received: true },
        ],
      }),
    );

    expect(word?.word).toBe('received');
  });

  it('AC-A2: reads "used" when the winning row was itself redirected, and it wins over received/SPO/PO/OI', () => {
    const word = boardOrderInquiryWord(
      inquiry({
        redirected: true,
        documents: [{ document: 'SPO-2026/01-0143', kind: 'spo', received: false }],
      }),
    );

    expect(word?.word).toBe('used');
  });

  it('AC-A3: reads "SPO" when the documents hold at least one spo, and titles the SPO numbers joined by ", "', () => {
    const word = boardOrderInquiryWord(
      inquiry({
        documents: [
          { document: 'SPO-2026/01-0143', kind: 'spo', received: false },
          { document: 'SPO-2026/01-0200', kind: 'spo', received: false },
          // A PO on the same line must not leak into the SPO title.
          { document: '202607-S0105', kind: 'po', received: false },
        ],
      }),
    );

    expect(word?.word).toBe('SPO');
    expect(word?.title).toBe('SPO-2026/01-0143, SPO-2026/01-0200');
  });

  it('AC-A4: reads "PO" when the documents hold only po (none received, none redirected), titled with the PO number(s)', () => {
    const word = boardOrderInquiryWord(
      inquiry({
        documents: [
          { document: '202607-S0105', kind: 'po', received: false },
          { document: '202607-S0106', kind: 'po', received: false },
        ],
      }),
    );

    expect(word?.word).toBe('PO');
    expect(word?.title).toBe('202607-S0105, 202607-S0106');
  });

  it('AC-A5: reads "OI" when the inquiry carries no documents and is not cancelled, titled with its own number', () => {
    const word = boardOrderInquiryWord(
      inquiry({ inquiry_no: 'OI-000901', documents: [], state: 'raised' }),
    );

    expect(word?.word).toBe('OI');
    expect(word?.title).toBe('OI-000901');
  });

  it('AC-A5: titles an unnumbered inquiry row "Unnumbered inquiry"', () => {
    const word = boardOrderInquiryWord(
      inquiry({ inquiry_no: null, documents: [], state: 'raised' }),
    );

    expect(word?.word).toBe('OI');
    expect(word?.title).toBe('Unnumbered inquiry');
  });

  it('AC-A6: renders no chip when there is no order_inquiry at all', () => {
    expect(boardOrderInquiryWord(null)).toBeNull();
    expect(boardOrderInquiryWord(undefined)).toBeNull();
  });

  it('AC-A6: renders no chip for a cancelled inquiry with no documents behind it', () => {
    const word = boardOrderInquiryWord(
      inquiry({ state: 'cancelled', documents: [], redirected: false }),
    );

    expect(word).toBeNull();
  });

  it('a cancelled inquiry that still carries a document is NOT silenced - only the empty-and-cancelled case is (AC-A6\'s own gate)', () => {
    const word = boardOrderInquiryWord(
      inquiry({
        state: 'cancelled',
        documents: [{ document: '202607-S0105', kind: 'po', received: false }],
      }),
    );

    expect(word?.word).toBe('PO');
  });

  it('AC-A7: the ladder is used > received > SPO > PO > OI - used wins over received when the row was redirected', () => {
    // Corrected 22 Sep (owner ruling, fix round 1): AC-RL-10 already ships `used` over
    // `received`, because `used` says the document that landed was redirected OFF this
    // line - the goods are somebody else's, so "received" would read as stock in hand.
    const word = boardOrderInquiryWord(
      inquiry({
        redirected: true,
        documents: [{ document: 'SPO-2026/01-0143', kind: 'spo', received: true }],
      }),
    );

    expect(word?.word).toBe('used');
  });

  it('AC-A7: used wins over SPO when the row is redirected but not every document is received', () => {
    const word = boardOrderInquiryWord(
      inquiry({
        redirected: true,
        documents: [{ document: 'SPO-2026/01-0143', kind: 'spo', received: false }],
      }),
    );

    expect(word?.word).toBe('used');
  });

  it('AC-A7: SPO wins over PO when both kinds are present', () => {
    const word = boardOrderInquiryWord(
      inquiry({
        documents: [
          { document: '202607-S0105', kind: 'po', received: false },
          { document: 'SPO-2026/01-0143', kind: 'spo', received: false },
        ],
      }),
    );

    expect(word?.word).toBe('SPO');
  });

  it('AC-A7: PO wins over OI - a line with only po documents never reads "OI"', () => {
    const word = boardOrderInquiryWord(
      inquiry({ documents: [{ document: '202607-S0105', kind: 'po', received: false }] }),
    );

    expect(word?.word).toBe('PO');
  });
});

/**
 * BORROW-LOCATION-ONLY (D3): a borrow that names no donor SO is a LOCATION borrow, whatever
 * its rung. Under the switch OFF the engine still walks step 2 (`order_borrow`), step 4b
 * (`pool`) and the frozen `group_borrow`, but blanks the donor fields, so those must read
 * "Borrow other location". With a donor SO named (switch ON) they keep reading "Borrow from
 * another order".
 */
describe('rowOf: a borrow that names no donor is a location borrow (BORROW-LOCATION-ONLY D3)', () => {
  it.each(['order_borrow', 'group_borrow', 'pool'])(
    'reads a donor-less %s borrow as borrow_other',
    (rung) => {
      expect(
        rowOf({
          kind: 'borrow',
          rung,
          qty: '30',
          location: 'MWH-IB',
          donor_so_number: null,
        }),
      ).toBe('borrow_other');
      expect(
        rowOf({ kind: 'borrow', rung, qty: '30', location: 'MWH-IB' }),
      ).toBe('borrow_other');
    },
  );

  it.each(['order_borrow', 'group_borrow', 'pool'])(
    'still reads a %s borrow that names a donor SO as borrow_order (ON pinned)',
    (rung) => {
      expect(
        rowOf({
          kind: 'borrow',
          rung,
          qty: '30',
          location: 'MWH-IB',
          donor_so_number: 'SO397450',
        }),
      ).toBe('borrow_order');
    },
  );
});

describe('the Decided cell reads a location borrow as plain Borrow (owner, 9 Oct 2026)', () => {
  it('writes "Borrow 6 (BRW-BB)", not "Borrow (other) 6 (BRW-BB)"', () => {
    expect(
      describeSupply(
        [{ kind: 'borrow', rung: 'order_borrow', qty: '6', location: 'BRW-BB', donor_so_number: null }],
        'BRW-IR',
      ),
    ).toBe('Borrow 6 (BRW-BB)');
  });
});
