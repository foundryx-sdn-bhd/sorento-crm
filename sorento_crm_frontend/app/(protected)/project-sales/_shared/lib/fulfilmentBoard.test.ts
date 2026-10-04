/**
 * The board's arithmetic (PLAN section 13).
 *
 * Three things here are acceptance criteria rather than implementation detail, and each is
 * asserted directly: which column a line lands in (13.3), the order competing lines are served
 * in (13.5), and when an order becomes confirmable (13.4). None of them needs a grid mounted.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  BoardCell,
  BoardContribution,
} from '../types/fulfilmentPlanning.types';
import {
  aheadFactorLabel,
  amendNeedsReason,
  boardAxis,
  bucketLabelText,
  commitPreviewFor,
  confirmDialogRowsFor,
  confirmLinesFor,
  confirmSummaryFor,
  DAY_WINDOW_COLUMNS as BOARD_DAY_WINDOW_COLUMNS,
  factorLabel,
  matchesSuggestion,
  plannedLineCount,
  pressPostsContribution,
  rankingNote,
  rejectedCoveredLineIdsFor,
  rowMatchesSearch,
  savedAgoText,
  shiftedDayWindow,
  unpostableDecidedFor,
  standingsFor,
} from './fulfilmentBoard';
import { amendDraftFrom, suggestionDraftFrom } from './boardAmend';
import { verdictOf } from '../../fulfilment-planning/components/BoardDecisionPill';
import {
  bucketKeyFor,
  buildBoard,
  compareContributions,
  DAY_WINDOW_COLUMNS,
  LIVE_POLICY,
  monthStart,
  PREVIEW_POLICY,
  rankScore,
  weekStart,
  type BoardDemandLine,
} from './__testsupport__/boardFixture';

const TODAY = '2026-08-18';

function line(overrides: Partial<BoardDemandLine> = {}): BoardDemandLine {
  return {
    sales_order_id: 'so-1',
    so_number: 'SO000001',
    customer_name: 'A CUSTOMER SDN BHD',
    project_label: 'A PROJECT',
    line_no: 1,
    item_code: 'WESERP10B',
    qty: '100',
    required_date: '2026-09-04',
    fulfilment_location: 'BRW-BB',
    priority: null,
    order_date: '2026-01-05',
    payment_terms_days: 30,
    demand_class: 'project',
    ...overrides,
  };
}

describe('weekStart / monthStart', () => {
  it('snaps to the Monday of the ISO week', () => {
    // 2026-09-04 is a Friday; its week begins Monday 2026-08-31.
    expect(weekStart('2026-09-04')).toBe('2026-08-31');
    expect(weekStart('2026-08-31')).toBe('2026-08-31');
    // A Sunday belongs to the week that started six days earlier, not the next one.
    expect(weekStart('2026-09-06')).toBe('2026-08-31');
  });

  it('snaps to the first of the month', () => {
    expect(monthStart('2026-09-04')).toBe('2026-09-01');
  });
});

describe('bucketKeyFor (13.3, captain: do not put overdue together)', () => {
  it('buckets a past date by its OWN date, never into one aggregate column', () => {
    // The captain, verbatim: "don't put overdue together, still split by the date, don't put
    // under overdue". A four-year-old required date is still a date, and collapsing it loses
    // the only thing that says how late it is.
    expect(bucketKeyFor('2022-07-03', TODAY, 'week')).toBe(weekStart('2022-07-03'));
    expect(bucketKeyFor('2026-08-17', TODAY, 'week')).toBe(weekStart('2026-08-17'));
    expect(bucketKeyFor('2022-07-03', TODAY, 'day')).toBe('2022-07-03');
    expect(bucketKeyFor('2022-07-03', TODAY, 'month')).toBe('2022-07-01');
  });

  it('keeps a line with no date in its own column rather than guessing one', () => {
    expect(bucketKeyFor(null, TODAY, 'week')).toBe('no_date');
    expect(bucketKeyFor(undefined, TODAY, 'week')).toBe('no_date');
  });

  it('buckets a future date by week or by month', () => {
    expect(bucketKeyFor('2026-09-04', TODAY, 'week')).toBe('2026-08-31');
    expect(bucketKeyFor('2026-09-04', TODAY, 'month')).toBe('2026-09-01');
  });

  it('buckets today itself like any other date', () => {
    expect(bucketKeyFor(TODAY, TODAY, 'week')).toBe(weekStart(TODAY));
  });
});

describe('rankScore (13.5, lifted from the reorder engine)', () => {
  it('is the weighted mean over the PRESENT factors', () => {
    expect(
      rankScore([
        { key: 'a', weight: 3, value: 1, present: true },
        { key: 'b', weight: 1, value: 0, present: true },
      ]),
    ).toBeCloseTo(0.75);
  });

  it('drops an absent factor from BOTH sums rather than scoring it zero', () => {
    // With the absent factor merely zeroed this would be 0.75; dropped, the present factor
    // stands alone. An unknown is not a bad score.
    expect(
      rankScore([
        { key: 'a', weight: 3, value: 1, present: true },
        { key: 'b', weight: 1, value: null, present: false },
      ]),
    ).toBeCloseTo(1);
  });

  it('is 0 when nothing is present, which is what the live policy produces here', () => {
    expect(rankScore([{ key: 'a', weight: 1, value: null, present: false }])).toBe(0);
  });
});

describe('the live policy cannot rank a board row (13.5, the blocker)', () => {
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO000003', line_no: 1, required_date: '2026-09-04' }),
      line({ sales_order_id: 'so-b', so_number: 'SO000001', line_no: 2, required_date: '2026-09-02' }),
    ],
    { today: TODAY, policy: LIVE_POLICY },
  );

  it('scores every contributor 0, because it weights only a factor no sales-order line has', () => {
    expect(board.cells[0].contributions.map((entry) => entry.rank_score)).toEqual([0, 0]);
  });

  it('marks po_document_sequence absent rather than pretending it is zero', () => {
    const sequence = board.cells[0].contributions[0].rank_factors.find(
      (factor) => factor.key === 'po_document_sequence',
    );
    expect(sequence?.present).toBe(false);
    expect(sequence?.value).toBeNull();
  });

  it('degrades to sales-order order rather than to an arbitrary one', () => {
    expect(board.cells[0].contributions.map((entry) => entry.so_number)).toEqual([
      'SO000001',
      'SO000003',
    ]);
  });

  it('names the policy it ranked by, so a flat ranking is explainable', () => {
    expect(board.policy.name).toBe("Today's rule (PO document sequence)");
    expect(board.policy.is_preview).toBe(false);
  });
});

describe('the preview policy ranks on the captain\'s three factors (13.5)', () => {
  it('puts the sooner delivery date first, need_by_date being the heaviest', () => {
    const board = buildBoard(
      [
        // Same week, so they land in one cell and genuinely compete.
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, required_date: '2026-09-04' }),
        line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 2, required_date: '2026-09-02' }),
      ],
      { today: TODAY, policy: PREVIEW_POLICY },
    );
    expect(board.cells[0].contributions[0].so_number).toBe('SO000002');
    expect(board.cells[0].contributions[0].rank_score).toBeGreaterThan(
      board.cells[0].contributions[1].rank_score,
    );
  });

  it('breaks a dead heat on document date, the older document winning', () => {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, order_date: '2026-07-01' }),
        line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 2, order_date: '2024-01-01' }),
      ],
      { today: TODAY, policy: PREVIEW_POLICY },
    );
    expect(board.cells[0].contributions[0].so_number).toBe('SO000002');
  });

  it('prefers the customer on shorter payment terms, all else equal', () => {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, payment_terms_days: 90 }),
        line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 2, payment_terms_days: 14 }),
      ],
      { today: TODAY, policy: PREVIEW_POLICY },
    );
    expect(board.cells[0].contributions[0].so_number).toBe('SO000002');
  });

  it('never ranks a customer with no terms as best or as worst: the factor is absent', () => {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, payment_terms_days: null }),
        line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 2, payment_terms_days: 30 }),
      ],
      { today: TODAY, policy: PREVIEW_POLICY },
    );
    const unknown = board.cells[0].contributions.find((entry) => entry.so_number === 'SO000001');
    const credit = unknown?.rank_factors.find((factor) => factor.key === 'customer_credit');
    expect(credit?.present).toBe(false);
    expect(credit?.value).toBeNull();
  });

  it('carries the factors that produced the score, so the planner can see WHY', () => {
    const board = buildBoard([line()], { today: TODAY, policy: PREVIEW_POLICY });
    const keys = board.cells[0].contributions[0].rank_factors.map((factor) => factor.key);
    expect(keys).toContain('need_by_date');
    expect(keys).toContain('document_age');
    expect(keys).toContain('customer_credit');
  });

  it('serves scarce stock down the ranking, not down the input order', () => {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100', required_date: '2026-12-01' }),
        line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 2, qty: '100', required_date: '2026-09-02' }),
      ],
      { today: TODAY, policy: PREVIEW_POLICY, freeStock: { 'WESERP10B|BRW-BB': '100' } },
    );
    const cell = board.cells.find((entry) => entry.bucket_key === '2026-08-31');
    const winner = cell?.contributions.find((entry) => entry.so_number === 'SO000002');
    expect(winner?.sources.map((source) => source.kind)).toEqual(['reserve']);
  });
});

describe('compareContributions', () => {
  it('puts the higher rank first', () => {
    expect(
      compareContributions(
        { rank_score: 0.9, so_number: 'SO000002', line_no: 1 },
        { rank_score: 0.4, so_number: 'SO000001', line_no: 1 },
      ),
    ).toBeLessThan(0);
  });

  it('is total, so the board cannot reshuffle between two renders', () => {
    const left = { rank_score: 0.5, so_number: 'SO000001', line_no: 1 };
    const right = { rank_score: 0.5, so_number: 'SO000002', line_no: 1 };
    expect(compareContributions(left, right)).toBeLessThan(0);
    expect(compareContributions(right, left)).toBeGreaterThan(0);
    expect(compareContributions(left, { ...left, line_no: 2 })).toBeLessThan(0);
  });
});

describe('buildBoard: the axes', () => {
  it('orders every dated bucket chronologically, past included, with No date pinned last', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, required_date: '2026-09-04' }),
        line({ line_no: 2, required_date: '2022-07-03' }),
        line({ line_no: 3, required_date: null, fulfilment_location: null }),
        line({ line_no: 4, required_date: '2026-12-01' }),
      ],
      { today: TODAY },
    );
    expect(board.dateBuckets.map((bucket) => bucket.key)).toEqual([
      '2022-06-27',
      '2026-08-31',
      '2026-11-30',
      'no_date',
    ]);
    expect(board.dateBuckets[0].label).toBe('27 Jun 2022');
    expect(board.dateBuckets[3].label).toBe('No date');
    expect(board.dateBuckets.map((bucket) => bucket.kind)).toEqual([
      'dated',
      'dated',
      'dated',
      'no_date',
    ]);
  });

  /**
   * The past is TINTED, not merged. `is_past` is the server's verdict about a bucket that
   * lies entirely before `as_of`, and it is the only thing the grid may colour on: deriving
   * it here would make the tint disagree with the bucketing the server actually did.
   */
  it('marks a bucket that lies entirely before the as-of date as past, and leaves the rest alone', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, required_date: '2022-07-03' }),
        line({ line_no: 2, required_date: '2026-09-04' }),
        line({ line_no: 3, required_date: null, fulfilment_location: null }),
      ],
      { today: TODAY },
    );
    expect(board.dateBuckets.map((bucket) => `${bucket.key} ${bucket.is_past}`)).toEqual([
      '2022-06-27 true',
      '2026-08-31 false',
      'no_date false',
    ]);
  });

  it('does not call the bucket holding the as-of date itself past', () => {
    const board = buildBoard([line({ required_date: TODAY })], { today: TODAY });
    expect(board.dateBuckets[0].is_past).toBe(false);
  });

  /**
   * A line's own lateness is a different question from its bucket's, and only the line-level
   * one can answer "how many lines are late": a line due two days ago sits in the week that
   * contains as_of, whose period has NOT ended.
   */
  it('marks a line past by its OWN required date, even when its bucket is not', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, required_date: '2026-08-17' }),
        line({ line_no: 2, required_date: '2026-08-20' }),
      ],
      { today: TODAY },
    );
    expect(board.dateBuckets[0].is_past).toBe(false);
    expect(board.cells[0].past_count).toBe(1);
    expect(
      board.cells[0].contributions.map((entry) => `${entry.required_date} ${entry.is_past}`).sort(),
    ).toEqual(['2026-08-17 true', '2026-08-20 false']);
  });

  it('never calls an undated line past, because nobody said when it was due', () => {
    const board = buildBoard([line({ required_date: null, fulfilment_location: null })], {
      today: TODAY,
    });
    expect(board.cells[0].past_count).toBe(0);
    expect(board.cells[0].contributions[0].is_past).toBe(false);
  });

  it('produces NO cell for a product and date nobody owes, so the grid can render blank', () => {
    const board = buildBoard(
      [
        line({ item_code: 'AAA', required_date: '2026-09-04' }),
        line({ item_code: 'BBB', required_date: '2026-12-01', line_no: 2 }),
      ],
      { today: TODAY },
    );
    expect(board.productRows.map((row) => row.item_code)).toEqual(['AAA', 'BBB']);
    expect(board.cells).toHaveLength(2);
    expect(board.cells.find((cell) => cell.item_code === 'AAA' && cell.bucket_key === '2026-11-30')).toBeUndefined();
  });

  it('echoes the date it was built against, so the past tint is reproducible', () => {
    expect(buildBoard([line()], { today: TODAY }).as_of).toBe(TODAY);
  });
});

describe('buildBoard: aggregation across orders', () => {
  const shared: BoardDemandLine[] = [
    line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100', required_date: '2026-09-04' }),
    line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 2, qty: '50', required_date: '2026-09-02' }),
    line({ sales_order_id: 'so-c', so_number: 'SO000003', line_no: 3, qty: '25', required_date: '2026-09-01', fulfilment_location: 'BRW-IB' }),
  ];

  it('sums one cell across every contributing sales order', () => {
    const board = buildBoard(shared, { today: TODAY });
    const cell = board.cells.find((entry) => entry.bucket_key === '2026-08-31');
    expect(cell?.total_qty).toBe('175');
    expect(cell?.contributions).toHaveLength(3);
  });

  it('orders the contributions by the ranking, not by input order', () => {
    const board = buildBoard(shared, { today: TODAY, policy: PREVIEW_POLICY });
    const cell = board.cells.find((entry) => entry.bucket_key === '2026-08-31');
    expect(cell?.contributions.map((entry) => entry.so_number)).toEqual([
      'SO000003',
      'SO000002',
      'SO000001',
    ]);
  });

  it('reports one source-strip entry per distinct location, biggest first (13.7)', () => {
    const board = buildBoard(shared, { today: TODAY });
    const cell = board.cells.find((entry) => entry.bucket_key === '2026-08-31');
    expect(cell?.locations).toEqual([
      { location: 'BRW-BB', qty: '150' },
      { location: 'BRW-IB', qty: '25' },
    ]);
  });
});

describe('buildBoard: allocation and contest (13.5)', () => {
  it('serves the earliest line in full and buys the rest, naming the loser as contested', () => {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100', required_date: '2026-09-04' }),
        line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 2, qty: '100', required_date: '2026-09-02' }),
      ],
      { today: TODAY, policy: PREVIEW_POLICY, freeStock: { 'WESERP10B|BRW-BB': '100' } },
    );
    const cell = board.cells[0];
    const [first, second] = cell.contributions;
    expect(first.so_number).toBe('SO000002');
    expect(first.sources.map((source) => `${source.kind} ${source.qty}`)).toEqual(['reserve 100']);
    expect(first.contested).toBe(false);
    expect(second.sources.map((source) => `${source.kind} ${source.qty}`)).toEqual(['buy 100']);
    expect(second.contested).toBe(true);
    expect(cell.contested_count).toBe(1);
  });

  it('splits one line into reserve plus buy when the pool runs out mid-line', () => {
    const board = buildBoard([line({ qty: '100' })], {
      today: TODAY,
      freeStock: { 'WESERP10B|BRW-BB': '40' },
    });
    expect(board.cells[0].contributions[0].sources.map((source) => `${source.kind} ${source.qty}`)).toEqual([
      'reserve 40',
      'buy 60',
    ]);
  });

  it('does NOT call a line contested when the location simply never held any stock', () => {
    const board = buildBoard([line({ qty: '100' })], { today: TODAY, freeStock: {} });
    const contribution = board.cells[0].contributions[0];
    expect(contribution.sources.map((source) => source.kind)).toEqual(['buy']);
    expect(contribution.contested).toBe(false);
  });

  it('never promises the same free stock on two different dates', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, qty: '80', required_date: '2026-09-04' }),
        line({ line_no: 2, qty: '80', required_date: '2026-12-01' }),
      ],
      { today: TODAY, freeStock: { 'WESERP10B|BRW-BB': '100' } },
    );
    const reserved = board.cells
      .flatMap((cell) => cell.contributions)
      .flatMap((entry) => entry.sources)
      .filter((source) => source.kind === 'reserve')
      .reduce((total, source) => total + Number.parseFloat(source.qty), 0);
    expect(reserved).toBe(100);
  });

  it('allocates per location: stock at one cannot cover a line owed from another', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, qty: '50', fulfilment_location: 'BRW-IB' }),
        line({ line_no: 2, qty: '50', fulfilment_location: 'BRW-BB' }),
      ],
      { today: TODAY, freeStock: { 'WESERP10B|BRW-IB': '50' } },
    );
    const byLocation = Object.fromEntries(
      board.cells[0].contributions.map((entry) => [
        entry.fulfilment_location,
        entry.sources.map((source) => source.kind).join('+'),
      ]),
    );
    expect(byLocation['BRW-IB']).toBe('reserve');
    expect(byLocation['BRW-BB']).toBe('buy');
  });
});

describe('buildBoard: a line with no location (AC-FP16)', () => {
  const board = buildBoard(
    [
      line({ line_no: 1, qty: '24', fulfilment_location: null }),
      line({ line_no: 2, qty: '10' }),
    ],
    { today: TODAY, freeStock: { 'WESERP10B|BRW-BB': '999' } },
  );
  const cell = board.cells[0];

  it('still contributes its quantity, so the demand is not hidden', () => {
    expect(cell.total_qty).toBe('34');
    expect(cell.unplannable_count).toBe(1);
  });

  it('is proposed nothing at all, rather than a Reserve of zero dressed up as a plan', () => {
    const blocked = cell.contributions.find((entry) => entry.unplannable);
    expect(blocked?.sources.map((source) => source.kind)).toEqual(['unplannable']);
    expect(blocked?.sources[0].reason).toContain('No fulfilment location');
  });

  it('gathers the location-less quantity under its own strip entry', () => {
    expect(cell.locations).toContainEqual({ location: null, qty: '24' });
  });
});

/**
 * The standings are SELECTION-scoped, and that is the whole point of them.
 *
 * `line_count` and `unplannable_count` come off the server's `orders[]`, which is built from
 * every row of the selection. Counting them from the cells ON SCREEN was a live defect: at day
 * granularity the cells are a 30-day window, so a forty-line order read "3 of 3 lines decided"
 * and `commitPreviewFor` then promised to leave nothing behind. Only `decided_count` is the
 * client's, because verdicts live in the client draft.
 */
describe('standingsFor and commitPreviewFor (13.4)', () => {
  // Built through buildBoard so the contributions carry REAL keys, which is the only kind the
  // counter reads now.
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1 }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, item_code: 'TPE-9204' }),
      line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 3 }),
    ],
    { today: TODAY },
  );
  const contributions = board.cells.flatMap((cell) => cell.contributions);
  const owners = new Map(contributions.map((entry) => [entry.key, entry.sales_order_id]));

  it('counts the lines of each order inside the selection', () => {
    expect(
      standingsFor(board.orders, owners, {}).map(
        (entry) => `${entry.so_number} ${entry.decided_count}/${entry.line_count}`,
      ),
    ).toEqual(['SO000001 0/2', 'SO000002 0/1']);
  });

  it('counts a verdict against the order whose contribution it was', () => {
    const key = contributions.find((entry) => entry.so_number === 'SO000001')!.key;
    const standings = standingsFor(board.orders, owners, { [key]: { verdict: 'approved' } });
    expect(standings[0].decided_count).toBe(1);
    expect(standings[1].decided_count).toBe(0);
  });

  it('counts nothing for a draft key no contribution ever carried', () => {
    // The rebuild bug's shape (deviation 5): a plausible key nobody owns is not guessed at.
    const standings = standingsFor(board.orders, owners, {
      'so-a|1|WESERP10B|2026-09-27': { verdict: 'approved' },
    });
    expect(standings.every((entry) => entry.decided_count === 0)).toBe(true);
  });

  it('ignores the server decided_count, which is always 0 (deviation 4)', () => {
    const orders = board.orders.map((entry) => ({ ...entry, decided_count: 99 }));
    expect(standingsFor(orders, owners, {})[0].decided_count).toBe(0);
  });

  it('states what a partial confirmation would commit and what it would leave', () => {
    const key = contributions.find((entry) => entry.so_number === 'SO000001')!.key;
    const standings = standingsFor(board.orders, owners, { [key]: { verdict: 'approved' } });
    // Confirm is NOT gated on completeness (13.4): the planner commits what they decided and
    // the rest keeps flowing to reorder planning, so the screen owes the consequence.
    expect(commitPreviewFor(standings[0])).toEqual({
      committing: 1,
      leaving_undecided: 1,
      blocked: 0,
    });
  });

  it('has nothing to commit before anything is decided', () => {
    expect(commitPreviewFor(standingsFor(board.orders, owners, {})[0]).committing).toBe(0);
  });

  it('counts a line that can never be decided here as left behind, and names it as blocked', () => {
    const withBlocked = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1 }),
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 9, fulfilment_location: null }),
      ],
      { today: TODAY },
    );
    const preview = commitPreviewFor(standingsFor(withBlocked.orders, new Map(), {})[0]);
    expect(preview.leaving_undecided).toBe(2);
    expect(preview.blocked).toBe(1);
  });

  it('does NOT shrink to the day window: the order is counted over the whole selection', () => {
    const lines = [
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, required_date: '2026-09-04' }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, required_date: '2028-01-04' }),
    ];
    const week = buildBoard(lines, { today: TODAY });
    const day = buildBoard(lines, { today: TODAY, granularity: 'day' });

    // The window genuinely hides one of the two lines...
    expect(day.cells.flatMap((cell) => cell.contributions)).toHaveLength(1);
    // ...and the standings are unmoved by that, exactly as the server's own day == month test.
    expect(day.orders).toEqual(week.orders);
    expect(standingsFor(day.orders, new Map(), {})[0].line_count).toBe(2);
  });
});

describe('day granularity (13.3)', () => {
  it('keys on the exact date', () => {
    expect(bucketKeyFor('2026-09-04', TODAY, 'day')).toBe('2026-09-04');
  });

  it('renders a fixed window rather than a column per distinct date', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, required_date: '2026-09-04' }),
        line({ line_no: 2, required_date: '2027-06-01' }),
      ],
      { today: TODAY, granularity: 'day' },
    );
    const dated = board.dateBuckets.filter((bucket) => bucket.kind === 'dated');
    expect(dated).toHaveLength(DAY_WINDOW_COLUMNS);
  });

  it('renders an empty day inside the window, because the gap is the information', () => {
    const board = buildBoard([line({ required_date: '2026-09-04' })], {
      today: TODAY,
      granularity: 'day',
      dayWindowStart: '2026-09-01',
    });
    const keys = board.dateBuckets.map((bucket) => bucket.key);
    expect(keys).toContain('2026-09-02');
    expect(board.cells.some((cell) => cell.bucket_key === '2026-09-02')).toBe(false);
  });

  it('still pins No date last, and starts the window at the earliest date owed', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, required_date: '2026-09-04' }),
        line({ line_no: 2, required_date: '2022-07-03' }),
        line({ line_no: 3, required_date: null, fulfilment_location: null }),
      ],
      { today: TODAY, granularity: 'day' },
    );
    expect(board.dateBuckets[0].key).toBe('2022-07-03');
    expect(board.dateBuckets[0].is_past).toBe(true);
    expect(board.dateBuckets[board.dateBuckets.length - 1].key).toBe('no_date');
  });
});

/**
 * S1 (`PLAN-board-oi-mechanical-22sep.md`, AC-B1-6/AC-B1-7/AC-B1-9): the board's new default
 * granularity - a column per distinct required date actually present, never a calendar
 * window, labelled `DD/MM/YYYY`. The backend half (Phase 2) is not this lane's job to pin;
 * this is the test-support fixture (`__testsupport__/boardFixture.ts`) the FE component tests
 * build boards through, kept in step with the documented contract so a regression here shows
 * up as a fixture-level red rather than only as a mysterious FulfilmentBoardPanel failure.
 */
describe('date granularity (S1, `PLAN-board-oi-mechanical-22sep.md`, AC-B1-6/AC-B1-9)', () => {
  it('keys on the exact date - the same key day uses', () => {
    expect(bucketKeyFor('2026-09-04', TODAY, 'date')).toBe(
      bucketKeyFor('2026-09-04', TODAY, 'day'),
    );
    expect(bucketKeyFor(null, TODAY, 'date')).toBe('no_date');
  });

  it('renders one column per distinct date PRESENT, DD/MM/YYYY-labelled, no calendar fill - unlike day’s 30-day window', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, required_date: '2026-09-04' }),
        line({ line_no: 2, required_date: '2027-06-01' }),
      ],
      { today: TODAY, granularity: 'date' },
    );
    const dated = board.dateBuckets.filter((bucket) => bucket.kind === 'dated');
    expect(dated.map((bucket) => bucket.key)).toEqual(['2026-09-04', '2027-06-01']);
    expect(dated.map((bucket) => bucket.label)).toEqual(['04/09/2026', '01/06/2027']);
  });

  it('still pins No date last, sorted ascending, each with its own is_past', () => {
    const board = buildBoard(
      [
        line({ line_no: 1, required_date: '2026-09-04' }),
        line({ line_no: 2, required_date: '2022-07-03' }),
        line({ line_no: 3, required_date: null, fulfilment_location: null }),
      ],
      { today: TODAY, granularity: 'date' },
    );
    expect(board.dateBuckets.map((bucket) => bucket.key)).toEqual([
      '2022-07-03',
      '2026-09-04',
      'no_date',
    ]);
    expect(board.dateBuckets[0].is_past).toBe(true);
    expect(board.dateBuckets[1].is_past).toBe(false);
  });
});

/**
 * Whether an amendment has to say why, read over the WHOLE composition.
 *
 * It used to look at the Reserve alone, which was all a board amendment could change. Now that
 * the editor composes all four kinds, a planner could move 40 out of Reserve and 40 into
 * Borrow, displace the rule completely, and be asked for nothing.
 */
describe('amendNeedsReason', () => {
  const board = buildBoard([line({ qty: '100' })], {
    today: TODAY,
    freeStock: { 'WESERP10B|BRW-BB': '40' },
  });
  const contribution = board.cells[0].contributions[0];
  /** The proposal as it stands: reserve 40 at the line's own location, buy the other 60. */
  const proposed = {
    timely_spo_qty: '0',
    reserve: [{ qty: '40' }],
    borrow: [] as { qty: string }[],
    buy_qty: '60',
  };

  it('does not demand a reason for accepting the proposal unchanged', () => {
    expect(amendNeedsReason(contribution, proposed)).toBe(false);
  });

  it('demands one for any Reserve that displaces the rule', () => {
    expect(
      amendNeedsReason(contribution, { ...proposed, reserve: [{ qty: '10' }], buy_qty: '90' }),
    ).toBe(true);
  });

  it('demands one for a Borrow, which the engine never proposes', () => {
    expect(
      amendNeedsReason(contribution, {
        ...proposed,
        borrow: [{ qty: '10' }],
        buy_qty: '50',
      }),
    ).toBe(true);
  });

  it('demands one when the Buy changes, however the Reserve was left', () => {
    expect(amendNeedsReason(contribution, { ...proposed, buy_qty: '20' })).toBe(true);
  });

  it('demands one when the same Reserve total is moved to another warehouse', () => {
    // 40 at the pool instead of 40 at the own location is a displacement of the rule too.
    expect(
      amendNeedsReason(contribution, {
        ...proposed,
        reserve: [{ qty: '40', warehouse_id: 'wh-elsewhere' }],
      }),
    ).toBe(true);
  });
});

/**
 * Review finding F9: a covered line's baseline is what was DECIDED, not the engine's proposal.
 *
 * The frozen composition already carries a Borrow (decided on the sheet, with no amend reason),
 * so "any borrow is an override" demanded a reason to re-save an unchanged composition. The
 * reason is mandatory only when the composition differs from what was frozen.
 */
describe('amendNeedsReason on a covered line', () => {
  const frozen = {
    revision_no: 1,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '5' }],
    borrow: [
      {
        source: 'other_location' as const,
        warehouse_id: 'wh-mwh-ib',
        location: 'MWH-IB',
        donor_project_id: null,
        qty: '10',
        reason: 'The other site can wait a week.',
      },
    ],
    buy_qty: '28',
    amend_reason: null,
  };
  const board = buildBoard([line({ qty: '43', decision: frozen })], { today: TODAY });
  const covered = board.cells[0].contributions[0];
  const unchanged = {
    timely_spo_qty: '0',
    reserve: [{ qty: '5', warehouse_id: 'wh-BRW-BB' }],
    borrow: [{ qty: '10', warehouse_id: 'wh-mwh-ib', donor_project_id: null }],
    buy_qty: '28',
  };

  it('is a covered contribution to begin with', () => {
    expect(covered.covered).toBe(true);
  });

  it('does not demand a reason for re-saving the frozen composition, borrow included', () => {
    expect(amendNeedsReason(covered, unchanged)).toBe(false);
  });

  it('demands one when the frozen borrow quantity changes', () => {
    expect(
      amendNeedsReason(covered, {
        ...unchanged,
        borrow: [{ qty: '15', warehouse_id: 'wh-mwh-ib', donor_project_id: null }],
        buy_qty: '23',
      }),
    ).toBe(true);
  });

  it('demands one when the borrow moves to another donor at the same quantity', () => {
    expect(
      amendNeedsReason(covered, {
        ...unchanged,
        borrow: [{ qty: '10', warehouse_id: 'wh-other', donor_project_id: null }],
      }),
    ).toBe(true);
  });

  it('demands one when the frozen reserve or buy changes', () => {
    expect(
      amendNeedsReason(covered, {
        ...unchanged,
        reserve: [{ qty: '8', warehouse_id: 'wh-BRW-BB' }],
        buy_qty: '25',
      }),
    ).toBe(true);
  });

  it('demands one when the frozen borrow is dropped', () => {
    expect(
      amendNeedsReason(covered, { ...unchanged, borrow: [], buy_qty: '38' }),
    ).toBe(true);
  });
});

/**
 * WHICH VERDICT THE ONE SAVE TAKES (the captain, 28 August: "if the suggestion is same as
 * decision then it is approved, if suggestion different from decision then it is amended, so I
 * just click on 1 button").
 *
 * Asserted against the drafts the editor actually opens on - `amendDraftFrom` and
 * `suggestionDraftFrom` - because the panel compares one of those, and a baseline that agreed
 * with a hand-written composition but not with the seeded one would call an untouched form an
 * amendment.
 */
describe('matchesSuggestion', () => {
  const board = buildBoard([line({ qty: '100' })], {
    today: TODAY,
    freeStock: { 'WESERP10B|BRW-BB': '40' },
  });
  const contribution = board.cells[0].contributions[0];

  it('reads the draft the editor opens on as the suggestion, untouched', () => {
    expect(matchesSuggestion(contribution, amendDraftFrom(contribution))).toBe(true);
  });

  it('stops reading it as the suggestion once a Reserve quantity is changed', () => {
    const draft = amendDraftFrom(contribution);
    expect(
      matchesSuggestion(contribution, {
        ...draft,
        reserve: draft.reserve.map((row) => ({ ...row, qty: '10' })),
        buy_qty: '90',
      }),
    ).toBe(false);
  });

  it('stops reading it as the suggestion once the same total moves to another warehouse', () => {
    const draft = amendDraftFrom(contribution);
    expect(
      matchesSuggestion(contribution, {
        ...draft,
        reserve: draft.reserve.map((row) => ({ ...row, warehouse_id: 'wh-elsewhere' })),
      }),
    ).toBe(false);
  });
});

/**
 * A COVERED LINE IS THE CASE `amendNeedsReason` CANNOT ANSWER, and the reason this helper is
 * its own function rather than a negation of that one.
 *
 * The canonical line: confirmed at 8 from its own location plus a Buy of 35 while the engine
 * suggested the whole 43 from stock. `amendNeedsReason` compares against what was DECIDED, so
 * it reports the frozen composition as needing no reason - correct, it overrides nothing - and
 * would have called a planner typing the engine's numbers back an amendment.
 */
describe('matchesSuggestion on a covered line', () => {
  const suggestion = [
    {
      kind: 'reserve' as const,
      qty: '43',
      location: 'BRW-BB',
      warehouse_id: 'wh-BRW-BB',
      reason: 'Free unclaimed stock at BRW-BB covers the whole line by the delivery date.',
    },
  ];
  const board = buildBoard(
    [
      line({
        qty: '43',
        decision: {
          revision_no: 4,
          confirmed_at: '2026-08-18T02:00:00',
          timely_spo_qty: '0',
          reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '8' }],
          borrow: [],
          buy_qty: '35',
        },
        proposed_components: suggestion,
      }),
    ],
    { today: TODAY },
  );
  const covered = board.cells[0].contributions[0];

  it('is a covered contribution whose decision left the suggestion', () => {
    expect(covered.covered).toBe(true);
    expect(covered.proposed?.components).toEqual(suggestion);
  });

  it('reads the frozen composition Amend opens on as an amendment, not as the suggestion', () => {
    const frozenDraft = amendDraftFrom(covered);
    expect(matchesSuggestion(covered, frozenDraft)).toBe(false);
    // The two questions, side by side: re-saving what was decided overrides nothing, so it is
    // not the reason that separates them - it is what the composition IS.
    expect(amendNeedsReason(covered, frozenDraft)).toBe(false);
  });

  it('reads the engine’s own composition as the suggestion, whatever was frozen', () => {
    expect(matchesSuggestion(covered, suggestionDraftFrom(covered))).toBe(true);
  });
});

/**
 * Phase 2, deviation 5: the contribution key is the SERVER's, and the counter must use it.
 *
 * The server derives `line_no` (core `sales_order_lines` carries none) and pins the key format
 * `${sales_order_id}|${line_no}|${item_code}|${bucket_key}` with a test of its own. The client
 * used to REBUILD that key from a line plus a re-derived bucket, which means any disagreement
 * about bucketing - a different `as_of`, a granularity the client resolved differently, a
 * timezone - makes every rebuilt key miss. The failure is silent: the draft still records
 * verdicts, and the per-order counter simply never moves off zero.
 *
 * So the counter reads the key the server sent, and these tests exist to stop anyone
 * reintroducing the rebuild.
 */
describe('standingsFor reads the server key (deviation 5)', () => {
  const orders = [
    {
      sales_order_id: '41125fbc-4176-4044-b819-c196c9f6467f',
      so_number: 'SO396488',
      customer_name: 'PP CHIN HIN SDN BHD (PROJECT)',
      line_count: 2,
      decided_count: 0,
      unplannable_count: 0,
    },
    {
      sales_order_id: 'aaaaaaaa-0000-0000-0000-000000000001',
      so_number: 'SO345418',
      customer_name: 'PEMBINAAN YUEN SENG SDN BHD (PROJECT)',
      line_count: 1,
      decided_count: 0,
      unplannable_count: 1,
    },
  ];
  const owners = new Map([
    ['41125fbc-4176-4044-b819-c196c9f6467f|1|CSK11A|2026-09-28', orders[0].sales_order_id],
    ['41125fbc-4176-4044-b819-c196c9f6467f|2|CSK11A|2026-10-05', orders[0].sales_order_id],
    ['aaaaaaaa-0000-0000-0000-000000000001|1|WESERP10B|2022-06-27', orders[1].sales_order_id],
  ]);

  it('counts a verdict against the order whose contribution it was', () => {
    const standings = standingsFor(orders, owners, {
      '41125fbc-4176-4044-b819-c196c9f6467f|1|CSK11A|2026-09-28': { verdict: 'approved' },
    });
    const chin = standings.find((entry) => entry.so_number === 'SO396488');
    expect(chin?.decided_count).toBe(1);
    expect(chin?.line_count).toBe(2);
  });

  it('counts nothing when the draft key is not one the server sent', () => {
    // This is the exact shape of the rebuild bug: a plausible key that no contribution has.
    const standings = standingsFor(orders, owners, {
      '41125fbc-4176-4044-b819-c196c9f6467f|1|CSK11A|2026-09-27': { verdict: 'approved' },
    });
    expect(standings.every((entry) => entry.decided_count === 0)).toBe(true);
  });

  it('still counts a line the sales order gave no location as unplannable', () => {
    const standings = standingsFor(orders, owners, {});
    const yuen = standings.find((entry) => entry.so_number === 'SO345418');
    expect(yuen?.unplannable_count).toBe(1);
  });

  it('counts a verdict on a contribution the current window no longer shows', () => {
    // The owner map outlives the board: a cell decided at week granularity is still that
    // order's line after the planner switches to day and it scrolls out of the window.
    const standings = standingsFor(orders, owners, {
      'aaaaaaaa-0000-0000-0000-000000000001|1|WESERP10B|2022-06-27': { verdict: 'rejected' },
    });
    expect(standings.find((entry) => entry.so_number === 'SO345418')?.decided_count).toBe(1);
  });
});

/**
 * Words a planner uses, not identifiers we chose.
 *
 * The rank chips printed raw database column names - `po_document_sequence absent`,
 * `need_by_date`, `document_age`, `customer_credit`. The captain asking what "w/c" meant is the
 * same fault in the column headers, and the same answer applies: nothing on this screen should
 * need decoding.
 */
describe('factorLabel', () => {
  it('names the four policy factors in English', () => {
    expect(factorLabel('need_by_date')).toBe('Delivery date');
    expect(factorLabel('document_age')).toBe('Order date');
    expect(factorLabel('customer_credit')).toBe('Payment terms');
    expect(factorLabel('demand_class')).toBe('Demand type');
    expect(factorLabel('po_document_sequence')).toBe('Purchase order sequence');
  });

  it('turns a factor nobody has named yet into words rather than printing the key', () => {
    expect(factorLabel('supplier_reliability')).toBe('Supplier reliability');
  });
});

/**
 * Why a line is ahead, when the policy separated nothing: `_pile_book` breaks the tie on the
 * required date first, then line order within an order, then the sales-order number - three
 * keys, each with words of its own, and none of them read as a policy factor.
 */
describe('aheadFactorLabel', () => {
  it('names the three tie-break keys apart from the policy factors', () => {
    expect(aheadFactorLabel('earlier_date')).toBe('Earlier delivery date (tie)');
    expect(aheadFactorLabel('line_order')).toBe('same order');
    expect(aheadFactorLabel('tie_break')).toBe('tie-break');
  });

  it('still names a policy factor as the factor', () => {
    expect(aheadFactorLabel('need_by_date')).toBe('Delivery date');
    expect(aheadFactorLabel('customer_credit')).toBe('Payment terms');
  });
});

describe('bucketLabelText', () => {
  it('drops the week-commencing abbreviation the captain had to ask about', () => {
    expect(bucketLabelText('w/c 2 Nov 2026')).toBe('2 Nov 2026');
  });

  it('leaves every other label alone, and is safe to run twice', () => {
    expect(bucketLabelText('2 Nov 2026')).toBe('2 Nov 2026');
    expect(bucketLabelText('Nov 2026')).toBe('Nov 2026');
    expect(bucketLabelText('No date')).toBe('No date');
  });
});

/**
 * Turning the board's draft into the body the per-order confirm endpoint takes.
 *
 * The captain asked it as a question - "so now when i click the confirm 8 lines, it won't work
 * and won't flow to order inquiries isit?" - and the answer was yes, the button was inert. This
 * is the mapping that makes it not be: one `ConfirmLine` per line the planner actually decided,
 * and NOTHING for a line they did not, because a line the body does not name is left undecided
 * and keeps flowing to reorder planning (13.4).
 */
describe('confirmLinesFor', () => {
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, qty: '50', item_code: 'TPE-9204' }),
      line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 3, qty: '10' }),
    ],
    { today: TODAY, freeStock: { 'WESERP10B|BRW-BB': '100', 'TPE-9204|BRW-BB': '20' } },
  );
  const contributions = board.cells.flatMap((cell) => cell.contributions);
  const keyOf = (soNumber: string, lineNo: number) =>
    contributions.find((entry) => entry.so_number === soNumber && entry.line_no === lineNo)!.key;

  /**
   * D4: only the OTHER order's line is left out - never a line of THIS order, saved or not.
   */
  it('names only the lines of the order being confirmed, not the other order on the draft', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf('SO000001', 1)]: { verdict: 'approved' },
      [keyOf('SO000002', 3)]: { verdict: 'approved' },
      [keyOf('SO000001', 2)]: { verdict: 'approved' },
    });
    expect(lines.map((entry) => entry.project_line_id).sort()).toEqual([
      'pl-so-a-1',
      'pl-so-a-2',
    ]);
  });

  it('leaves an untouched, uncovered line out (8 Sep 2026 ruling, reverses R11)', () => {
    // Line 2 carries no verdict at all: it is undecided, not agreed, so it stays off the body.
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf('SO000001', 1)]: { verdict: 'approved' },
    });
    expect(lines.map((entry) => entry.project_line_id)).toEqual(['pl-so-a-1']);
  });

  it('posts a SAVED approval on an uncovered line as the engine’s own suggestion', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf('SO000001', 1)]: { verdict: 'approved' },
      [keyOf('SO000001', 2)]: { verdict: 'approved' },
    });
    const saved = lines.find((entry) => entry.project_line_id === 'pl-so-a-2')!;
    expect(saved).toEqual({
      project_line_id: 'pl-so-a-2',
      timely_spo_qty: '0',
      suspected_system_issue: false,
      reserve: [{ warehouse_id: 'wh-BRW-BB', qty: '20' }],
      borrow: [],
      buy_qty: '30',
    });
  });

  it('carries the proposal as the engine wrote it on an approved line', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf('SO000001', 1)]: { verdict: 'approved' },
    });
    const approved = lines.find((entry) => entry.project_line_id === 'pl-so-a-1')!;
    expect(approved).toEqual({
      project_line_id: 'pl-so-a-1',
      timely_spo_qty: '0',
      suspected_system_issue: false,
      reserve: [{ warehouse_id: 'wh-BRW-BB', qty: '100' }],
      borrow: [],
      buy_qty: '0',
    });
  });

  /**
   * D11: `BoardLineDecisionPanel.save()` now writes the suggested COMPOSITION onto an
   * approved draft too (reserve, borrow, buy_qty, timely_spo_qty), not only the verdict, so
   * the sales order page's own "Decided" column has something to read. Confirm must not
   * double-count it: an uncovered approval's own branch (`lineFor`) never reads
   * `decision.reserve`/`decision.borrow`/`decision.buy_qty` at all, deriving fresh off the
   * contribution's own `qty_proposed_*` and source strip instead - so a draft that now
   * carries the composition confirms identically to one that carried only the verdict.
   */
  it('confirms identically whether or not the approved draft also carries the suggested composition', () => {
    const bare = confirmLinesFor(contributions, 'so-a', {
      [keyOf('SO000001', 1)]: { verdict: 'approved' },
    });
    const composed = confirmLinesFor(contributions, 'so-a', {
      [keyOf('SO000001', 1)]: {
        verdict: 'approved',
        reserve_qty: '100',
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '100' }],
        borrow: [],
        buy_qty: '0',
      },
    });
    expect(composed).toEqual(bare);
  });

  it('moves what an amendment took off the Reserve into the Buy', () => {
    // 50 owed, 20 free: the rule proposed reserve 20 + buy 30. Amended down to 5, the other
    // 15 has to be bought - the difference cannot simply vanish.
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf('SO000001', 2)]: { verdict: 'amended', reserve_qty: '5', reason: 'Held back.' },
    });
    expect(lines[0].reserve).toEqual([{ warehouse_id: 'wh-BRW-BB', qty: '5' }]);
    expect(lines[0].buy_qty).toBe('45');
  });

  it('leaves a REJECTED line out entirely, so it stays undecided (the other, SAVED line still posts)', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf('SO000001', 1)]: { verdict: 'rejected', reason: 'No.' },
      [keyOf('SO000001', 2)]: { verdict: 'approved' },
    });
    expect(lines.map((entry) => entry.project_line_id)).toEqual(['pl-so-a-2']);
  });

  it('leaves out a line the server gave no mirror id for, rather than posting a null (the other, SAVED line still posts)', () => {
    const orphan = contributions.map((entry) =>
      entry.line_no === 1 ? { ...entry, project_line_id: null } : entry,
    );
    const lines = confirmLinesFor(orphan, 'so-a', {
      [keyOf('SO000001', 1)]: { verdict: 'approved' },
      [keyOf('SO000001', 2)]: { verdict: 'approved' },
    });
    expect(lines.map((entry) => entry.project_line_id)).toEqual(['pl-so-a-2']);
  });

  it('counts only what would actually commit, so the button cannot promise a rejected line', () => {
    const owners = new Map(contributions.map((entry) => [entry.key, entry.sales_order_id]));
    const standings = standingsFor(board.orders, owners, {
      [keyOf('SO000001', 1)]: { verdict: 'approved' },
      [keyOf('SO000001', 2)]: { verdict: 'rejected', reason: 'No.' },
    });
    const soA = standings.find((entry) => entry.so_number === 'SO000001')!;
    expect(soA.decided_count).toBe(2);
    expect(commitPreviewFor(soA)).toEqual({
      committing: 1,
      leaving_undecided: 1,
      blocked: 0,
    });
  });
});

/**
 * The server's own proposal numbers, once it started sending them.
 *
 * The board now proposes what the SHEET proposes - pool and borrow are considered, not just
 * own-location reserve then buy - so summing the source strip on this side would be a second,
 * worse allocator quietly disagreeing with the real one.
 */
describe('confirmLinesFor reads the engine’s numbers', () => {
  const board = buildBoard([line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' })], {
    today: TODAY,
  });
  const base = board.cells[0].contributions[0];

  it('posts the proposal the server sent, not one re-derived from the sources', () => {
    const withProposal = [
      {
        ...base,
        // Deliberately disagreeing with `sources`, which says buy 100: the engine found a
        // pool Reserve the client's own arithmetic could never have known about.
        qty_proposed_reserve: '60',
        qty_proposed_incoming: '10',
        qty_proposed_buy: '30',
      },
    ];
    const lines = confirmLinesFor(withProposal, 'so-a', {
      [base.key]: { verdict: 'approved' },
    });
    expect(lines[0].timely_spo_qty).toBe('10');
    expect(lines[0].reserve).toEqual([{ warehouse_id: 'wh-BRW-BB', qty: '60' }]);
    expect(lines[0].buy_qty).toBe('30');
  });

  it('posts the DOCUMENT a step-3 borrow names, on an approved line', () => {
    // The approval path never goes through the editor: it maps the source strip straight
    // into the confirm body. Dropping the address here turned a document borrow into a
    // free-stock borrow at a bin the container has not reached, which the server refuses.
    const withBorrow = [
      {
        ...base,
        qty_proposed_reserve: '0',
        qty_proposed_incoming: '0',
        qty_proposed_buy: '0',
        sources: [
          {
            kind: 'borrow' as const,
            qty: '100',
            location: 'DC1-IR',
            warehouse_id: 'wh-ir',
            reason: 'Take 100 arriving 15 Sep 2026 (SPO 202607-S0105).',
            rung: 'supply_borrow',
            supply_key: 'spo:alloc-1',
            supply_document: 'SPO 202607-S0105',
            arrival_date: '2026-09-15',
          },
        ],
      },
    ];
    const lines = confirmLinesFor(withBorrow, 'so-a', {
      [base.key]: { verdict: 'approved' },
    });
    expect(lines[0].borrow[0]).toMatchObject({
      warehouse_id: 'wh-ir',
      qty: '100',
      supply_key: 'spo:alloc-1',
      supply_document: 'SPO 202607-S0105',
      arrival_date: '2026-09-15',
    });
  });

  /**
   * S1 (fix round 2, reviewer): this is the path SO420745 itself takes - an uncovered,
   * approved line whose Save carried a reason the planner typed on a suggested borrow row
   * (`BoardLineDecisionPanel`'s approving `save()`, board-confirm-left-out AC-3). Rebuilding
   * `borrow` straight off `contribution.sources` (the engine's own auto-proposed borrow, with
   * the engine's own sentence) posted that sentence back regardless of what the SAVED decision
   * actually carried.
   */
  it('carries the reason the approving decision typed for a suggested borrow row', () => {
    const withBorrow = [
      {
        ...base,
        qty_proposed_reserve: '0',
        qty_proposed_incoming: '0',
        qty_proposed_buy: '0',
        sources: [
          {
            kind: 'borrow' as const,
            qty: '100',
            location: 'MWH-IB',
            warehouse_id: 'wh-mwh-ib',
            reason: 'Cross-group cap allows this.',
          },
        ],
      },
    ];
    const lines = confirmLinesFor(withBorrow, 'so-a', {
      [base.key]: {
        verdict: 'approved',
        borrow: [
          {
            source: 'other_location',
            warehouse_id: 'wh-mwh-ib',
            qty: '100',
            reason: 'Confirmed with the other site on WhatsApp.',
          },
        ],
      },
    });
    expect(lines[0].borrow[0]).toMatchObject({
      warehouse_id: 'wh-mwh-ib',
      qty: '100',
      reason: 'Confirmed with the other site on WhatsApp.',
    });
  });

  it('still moves an amendment’s difference into the Buy', () => {
    const withProposal = [
      { ...base, qty_proposed_reserve: '60', qty_proposed_incoming: '10', qty_proposed_buy: '30' },
    ];
    const lines = confirmLinesFor(withProposal, 'so-a', {
      [base.key]: { verdict: 'amended', reserve_qty: '20', reason: 'Held back.' },
    });
    expect(lines[0].reserve).toEqual([{ warehouse_id: 'wh-BRW-BB', qty: '20' }]);
    // 100 owed, 10 incoming, 20 reserved: the other 70 is bought, not lost.
    expect(lines[0].buy_qty).toBe('70');
  });
});

/**
 * The three ids the confirm body is built from, now that they are live.
 *
 * The one that could not have been guessed from the screen: a line can carry TWO reserve
 * components at two different warehouses (its own location and the pool), and each has to be
 * addressed by ITS OWN id. A display string like "BRW-BB" cannot carry that, so the warehouse
 * is read off the SOURCE and never off the location label.
 */
describe('confirmLinesFor addresses each reserve to its own warehouse', () => {
  const board = buildBoard(
    [line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' })],
    { today: TODAY },
  );
  const base = board.cells[0].contributions[0];

  /** What the engine sends when it covers a line from the location AND the pool. */
  function twoReserves() {
    return [
      {
        ...base,
        qty_proposed_reserve: '70',
        qty_proposed_incoming: '0',
        qty_proposed_buy: '30',
        sources: [
          {
            kind: 'reserve' as const,
            qty: '40',
            location: 'BRW-BB',
            warehouse_id: 'wh-own',
            reason: 'Free stock at BRW-BB covers this much.',
          },
          {
            kind: 'reserve' as const,
            qty: '30',
            location: 'BRW',
            warehouse_id: 'wh-pool',
            reason: 'The dealer pool covers the rest.',
          },
          { kind: 'buy' as const, qty: '30', location: null, reason: 'The residual is bought.' },
        ],
      },
    ];
  }

  it('posts one component per reserve, each with its own warehouse id', () => {
    const lines = confirmLinesFor(twoReserves(), 'so-a', { [base.key]: { verdict: 'approved' } });
    expect(lines[0].reserve).toEqual([
      { warehouse_id: 'wh-own', qty: '40' },
      { warehouse_id: 'wh-pool', qty: '30' },
    ]);
    expect(lines[0].buy_qty).toBe('30');
  });

  it('takes an amendment off the reserves in order, and moves the difference to Buy', () => {
    const lines = confirmLinesFor(twoReserves(), 'so-a', {
      [base.key]: { verdict: 'amended', reserve_qty: '50', reason: 'Holding the pool back.' },
    });
    // 50 of the 70 proposed: the own location keeps its 40, the pool gives up 20 of its 30.
    expect(lines[0].reserve).toEqual([
      { warehouse_id: 'wh-own', qty: '40' },
      { warehouse_id: 'wh-pool', qty: '10' },
    ]);
    expect(lines[0].buy_qty).toBe('50');
  });

  it('drops a reserve component the server gave no warehouse for, rather than inventing one', () => {
    const noWarehouse = [
      {
        ...base,
        qty_proposed_reserve: '40',
        sources: [
          { kind: 'reserve' as const, qty: '40', location: 'BRW-BB', reason: 'Covered.' },
        ],
      },
    ];
    expect(
      confirmLinesFor(noWarehouse, 'so-a', { [base.key]: { verdict: 'approved' } }),
    ).toEqual([]);
  });
});

/**
 * An amendment made in the editor posts THE COMPOSITION THE PLANNER TYPED.
 *
 * The captain: "I should be able to amend the decision and quantity, like I can decide to
 * reserve, or buy, or borrow". A body that took one number and derived the rest could carry
 * one of those three; this carries what was actually decided, per warehouse and per donor, the
 * same way the per-order sheet's body does.
 */
describe('confirmLinesFor and a composed amendment', () => {
  const board = buildBoard(
    [line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' })],
    { today: TODAY },
  );
  const base = board.cells[0].contributions[0];

  it('posts the reserve, the borrow and the buy exactly as composed', () => {
    const lines = confirmLinesFor([base], 'so-a', {
      [base.key]: {
        verdict: 'amended',
        reserve_qty: '20',
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '20' }],
        borrow: [
          {
            source: 'other_location',
            warehouse_id: 'wh-ib',
            warehouse_code: 'BRW-IB',
            donor_project_id: null,
            qty: '10',
            reason: 'The site next door can wait a week.',
          },
        ],
        buy_qty: '70',
        reason: 'Holding the rest for the late site.',
      },
    });

    expect(lines[0]).toEqual({
      project_line_id: 'pl-so-a-1',
      timely_spo_qty: '0',
      suspected_system_issue: false,
      reserve: [{ warehouse_id: 'wh-BRW-BB', qty: '20' }],
      borrow: [
        {
          source: 'other_location',
          warehouse_id: 'wh-ib',
          donor_project_id: null,
          qty: '10',
          reason: 'The site next door can wait a week.',
          donor_core_line_id: null,
          donor_so_number: null,
          donor_line_no: null,
          donor_agent_code: null,
          same_agent: false,
          donor_required_date: null,
          // Ladder v7.1 step 3 (S4): this borrow names no document, and the keys are still
          // stated - the mapper is one spread now (`borrowPassThrough`), and a partial one
          // is how `supply_key` reached the seeders and not Save.
          supply_key: null,
          supply_document: null,
          arrival_date: null,
        },
      ],
      buy_qty: '70',
      amend_reason: 'Holding the rest for the late site.',
    });
  });

  it('no longer drops the amendment that turns a Buy into a Reserve', () => {
    // The line the engine met entirely from Buy is the one a planner most wants to amend:
    // there is stock at their own warehouse. The old body read the warehouse off the Reserve
    // SOURCES, of which there were none, and dropped the line - so the amendment was accepted
    // on screen and posted nothing at all.
    const lines = confirmLinesFor([base], 'so-a', {
      [base.key]: {
        verdict: 'amended',
        reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '100' }],
        borrow: [],
        buy_qty: '0',
        timely_spo_qty: '0',
        reason: 'The stock is at my own warehouse.',
      },
    });

    expect(lines).toHaveLength(1);
    expect(lines[0].reserve).toEqual([{ warehouse_id: 'wh-BRW-BB', qty: '100' }]);
    expect(lines[0].buy_qty).toBe('0');
  });

  it('leaves the reason off a body nobody gave one for', () => {
    const lines = confirmLinesFor([base], 'so-a', {
      [base.key]: {
        verdict: 'amended',
        reserve: [],
        borrow: [],
        buy_qty: '100',
        timely_spo_qty: '0',
      },
    });
    expect(lines[0].amend_reason).toBeUndefined();
    expect(lines[0].buy_reason).toBeUndefined();
  });

  it('carries the buy reason the editor took for a discontinued product', () => {
    const lines = confirmLinesFor([base], 'so-a', {
      [base.key]: {
        verdict: 'amended',
        reserve: [],
        borrow: [],
        buy_qty: '100',
        timely_spo_qty: '0',
        buy_reason: 'Last batch for the site, agreed with purchasing.',
      },
    });
    expect(lines[0].buy_reason).toBe('Last batch for the site, agreed with purchasing.');
  });

  it('still derives the Buy for an amendment carrying only the old single number', () => {
    // A draft entry made before the editor existed. It cannot name a warehouse for a quantity
    // the engine did not reserve, so the derived path stays exactly as it was.
    const lines = confirmLinesFor(
      [{ ...base, qty_proposed_reserve: '40', qty_proposed_buy: '60',
         sources: [
           { kind: 'reserve' as const, qty: '40', location: 'BRW-BB', warehouse_id: 'wh-BRW-BB', reason: 'Covered.' },
           { kind: 'buy' as const, qty: '60', location: null, reason: 'The residual is bought.' },
         ] }],
      'so-a',
      { [base.key]: { verdict: 'amended', reserve_qty: '10', reason: 'Held back.' } },
    );
    expect(lines[0].reserve).toEqual([{ warehouse_id: 'wh-BRW-BB', qty: '10' }]);
    expect(lines[0].buy_qty).toBe('90');
    expect(lines[0].amend_reason).toBe('Held back.');
  });
});

/**
 * A LATER confirmation on the same order covers the UNION (PLAN 13.4), and THE UNION IS THE
 * SERVER'S.
 *
 * "Deciding more lines later is a NEW revision covering the union, which is exactly what
 * revisions already do." The board briefly built that union itself, re-posting every covered
 * line from its frozen decision, and that had two holes: at day granularity the cells are a
 * window, so a covered line outside it was not re-posted and was silently un-decided; and the
 * re-posted line was rebuilt without its buy reason and re-judged against live facts, so a
 * discontinued product's covered line 422'd the confirmation of an unrelated one. The server now
 * carries every covered line the body does not name, verbatim, so the body names ONLY what the
 * planner just decided or amended.
 */
describe('confirmLinesFor and a line an active decision already covers', () => {
  const frozen = {
    revision_no: 1,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [],
    borrow: [
      {
        source: 'other_location' as const,
        warehouse_id: 'wh-mwh-ib',
        location: 'MWH-IB',
        donor_project_id: null,
        qty: '10',
        reason: 'The other site can wait a week.',
      },
    ],
    buy_qty: '33',
    amend_reason: 'Borrowed rather than bought, agreed with the other site.',
  };
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO403765', line_no: 1, qty: '43', decision: frozen }),
      line({ sales_order_id: 'so-a', so_number: 'SO403765', line_no: 2, qty: '21', item_code: 'TPE-9204' }),
    ],
    { today: TODAY, freeStock: { 'TPE-9204|BRW-BB': '5' } },
  );
  const contributions = board.cells.flatMap((cell) => cell.contributions);
  const keyOf = (lineNo: number) =>
    contributions.find((entry) => entry.line_no === lineNo)!.key;

  it('posts ONLY the newly decided line; the covered one is carried by the server', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf(2)]: { verdict: 'approved' },
    });

    // The covered line is NOT re-posted. The server carries every covered line the body does
    // not name, verbatim from its frozen snapshot, so re-posting it here would only re-judge
    // it against live facts - and at day granularity the board could not even see all of them.
    expect(lines.map((entry) => entry.project_line_id)).toEqual(['pl-so-a-2']);
    expect(lines[0].buy_qty).toBe('16');
  });

  it('posts nothing for an untouched order: the covered line stays carried, the uncovered line stays undecided (8 Sep 2026 ruling, reverses R11)', () => {
    // Line 1 is covered and untouched: the server carries it. Line 2 is uncovered and
    // untouched: nobody has saved a decision for it, so it stays on the board too.
    const lines = confirmLinesFor(contributions, 'so-a', {});
    expect(lines).toEqual([]);
  });

  it('sends the amendment when the planner amended the covered line, alongside the other line’s own SAVED suggestion', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf(1)]: {
        verdict: 'amended',
        reserve_qty: '5',
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '5' }],
        borrow: [],
        buy_qty: '38',
        reason: 'The stock arrived at my own warehouse.',
      },
      [keyOf(2)]: { verdict: 'approved' },
    });

    expect(lines.map((entry) => entry.project_line_id).sort()).toEqual([
      'pl-so-a-1',
      'pl-so-a-2',
    ]);
    const amended = lines.find((entry) => entry.project_line_id === 'pl-so-a-1')!;
    expect(amended).toEqual({
      project_line_id: 'pl-so-a-1',
      timely_spo_qty: '0',
      suspected_system_issue: false,
      reserve: [{ warehouse_id: 'wh-BRW-BB', qty: '5' }],
      borrow: [],
      buy_qty: '38',
      amend_reason: 'The stock arrived at my own warehouse.',
    });
  });

  it('counts an already-confirmed line as decided on the order card', () => {
    // "1 of 2 lines decided" beside an order whose first line IS confirmed read "0 of 2",
    // because the count only ever looked at the client draft.
    const owners = new Map(contributions.map((entry) => [entry.key, entry.sales_order_id]));
    const covered = new Set([keyOf(1)]);
    const standings = standingsFor(board.orders, owners, {}, covered);
    expect(standings[0].decided_count).toBe(1);
    expect(standings[0].line_count).toBe(2);
  });

  it('counts a covered line the planner amended once, not twice', () => {
    const owners = new Map(contributions.map((entry) => [entry.key, entry.sales_order_id]));
    const standings = standingsFor(
      board.orders,
      owners,
      { [keyOf(1)]: { verdict: 'amended', reason: 'Changed.' } },
      new Set([keyOf(1)]),
    );
    expect(standings[0].decided_count).toBe(1);
  });

  it('does not count a carried line as left undecided by the press', () => {
    // The body names one line and the server carries the covered one, so after the press
    // BOTH are decided: "Confirms 1, leaves 1 undecided" would describe an un-decide that
    // never happens.
    const owners = new Map(contributions.map((entry) => [entry.key, entry.sales_order_id]));
    const draft = { [keyOf(2)]: { verdict: 'approved' as const } };
    const standing = standingsFor(board.orders, owners, draft, new Set([keyOf(1)]))[0];
    expect(standing.carried_count).toBe(1);
    expect(commitPreviewFor(standing, confirmLinesFor(contributions, 'so-a', draft).length)).toEqual({
      committing: 1,
      leaving_undecided: 0,
      blocked: 0,
    });
  });

  it('counts an amended covered line as committing, not as carried', () => {
    const owners = new Map(contributions.map((entry) => [entry.key, entry.sales_order_id]));
    const draft = {
      [keyOf(1)]: {
        verdict: 'amended' as const,
        reserve_qty: '5',
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '5' }],
        borrow: [],
        buy_qty: '38',
        reason: 'The stock arrived at my own warehouse.',
      },
      [keyOf(2)]: { verdict: 'approved' as const },
    };
    const standing = standingsFor(board.orders, owners, draft, new Set([keyOf(1)]))[0];
    expect(standing.carried_count).toBe(0);
    // The body now carries TWO lines: the amendment, and line 2's own SAVED suggestion - so
    // nothing of this two-line order is left undecided by the press.
    expect(commitPreviewFor(standing, confirmLinesFor(contributions, 'so-a', draft).length)).toEqual({
      committing: 2,
      leaving_undecided: 0,
      blocked: 0,
    });
  });

  it('leaves a covered line out when it has no mirror id, rather than posting a null', () => {
    const orphan = contributions.map((entry) =>
      entry.line_no === 1 ? { ...entry, project_line_id: null } : entry,
    );
    const lines = confirmLinesFor(orphan, 'so-a', { [keyOf(2)]: { verdict: 'approved' } });
    expect(lines.map((entry) => entry.project_line_id)).toEqual(['pl-so-a-2']);
  });

  /**
   * AN EXPLICIT APPROVAL ON A COVERED LINE IS A DECISION, AND IT IS POSTED (C11).
   *
   * Silence on a covered line means "leave it as the database has it", which is why an
   * untouched one is never named. An approval is not silence: the planner amended a
   * confirmed line, changed their mind, and put the engine's composition back. The board swallowed it - the pill read Approved, the Confirm counter stayed where
   * it was and the press wrote nothing, so a reload showed the old revision unchanged. An
   * approved covered line goes down the same derivation branch an approved uncovered one
   * does, and the confirmation writes a new revision at the suggestion.
   */
  it('posts the suggestion for a covered line the planner APPROVED (C11)', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf(1)]: { verdict: 'approved' },
      [keyOf(2)]: { verdict: 'approved' },
    });

    expect(lines.map((entry) => entry.project_line_id).sort()).toEqual([
      'pl-so-a-1',
      'pl-so-a-2',
    ]);
    const approved = lines.find((entry) => entry.project_line_id === 'pl-so-a-1')!;
    // The engine's own composition for the line, exactly as the panel resets the inputs to
    // it: nothing re-derived here, and no `amend_reason` - an approval is not an override.
    expect(approved.buy_qty).toBe('33');
    expect(approved.borrow.map((row) => [row.warehouse_id, row.qty])).toEqual([
      ['wh-mwh-ib', '10'],
    ]);
    expect(approved.reserve).toEqual([]);
    expect(approved.amend_reason).toBeUndefined();
  });

  it('still posts nothing for a covered line nobody touched', () => {
    // Line 2 is saved so the assertion isolates the covered line's own untouched rule.
    expect(
      confirmLinesFor(contributions, 'so-a', { [keyOf(2)]: { verdict: 'approved' } }).map(
        (entry) => entry.project_line_id,
      ),
    ).toEqual(['pl-so-a-2']);
  });

  it('still posts nothing for a covered line the planner REJECTED', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [keyOf(1)]: { verdict: 'rejected', reason: 'The site cancelled it.' },
      [keyOf(2)]: { verdict: 'approved' },
    });
    expect(lines.map((entry) => entry.project_line_id)).toEqual(['pl-so-a-2']);
  });

  it('counts the approved covered line on the Confirm button, and the untouched one not', () => {
    // The counter reads the same rule the body does, so "Confirm (N)" and what the press
    // posts cannot disagree - the defect was visible as a button stuck at 0. Line 2 is saved
    // in both cases so the comparison isolates what approving the COVERED line 1 adds.
    expect(
      plannedLineCount(contributions, 'so-a', { [keyOf(2)]: { verdict: 'approved' } }),
    ).toBe(1);
    expect(
      plannedLineCount(contributions, 'so-a', {
        [keyOf(1)]: { verdict: 'approved' },
        [keyOf(2)]: { verdict: 'approved' },
      }),
    ).toBe(2);
  });

  it('counts an approved covered line as committing, not as carried', () => {
    const owners = new Map(contributions.map((entry) => [entry.key, entry.sales_order_id]));
    const draft = {
      [keyOf(1)]: { verdict: 'approved' as const },
      [keyOf(2)]: { verdict: 'approved' as const },
    };
    const standing = standingsFor(board.orders, owners, draft, new Set([keyOf(1)]))[0];
    expect(standing.carried_count).toBe(0);
    expect(
      commitPreviewFor(standing, confirmLinesFor(contributions, 'so-a', draft).length),
    ).toEqual({ committing: 2, leaving_undecided: 0, blocked: 0 });
  });

  /**
   * REWORKED (owner ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`, hand-test
   * feedback: "we should confirm the rejection"): a reject on a covered line is a STAGED
   * decision like every other board decision now, and Confirm is what actually withdraws it
   * (`rejected_line_ids`) - so it counts as BOTH `rejected` (the planner's own decision) AND
   * `toConfirm` (Confirm has something to DO with this press: carry the withdrawal). This test
   * used to pin the opposite ("carried rather than rejected") from when a covered reject was
   * refused outright at click time; superseded by the rework below.
   */
  it('counts a covered rejected line as BOTH rejected and something this press confirms', () => {
    const summary = confirmSummaryFor(contributions, {
      [keyOf(1)]: { verdict: 'rejected', reason: 'Changed my mind.' },
    });
    expect(summary.rejected).toBe(1);
    // Line 1's withdrawal is the one thing this press commits; line 2 is uncovered and
    // untouched, so it stays undecided (8 Sep 2026 ruling, reverses R11).
    expect(summary.toConfirm).toBe(1);
  });

  /**
   * N1 (fix round): an INQUIRY-ONLY covered line's own staged reject still counts as the
   * planner's `rejected` decision, but adds nothing to `toConfirm` - Confirm has no active
   * decision to withdraw it from, unlike line 1's ACTIVE-decision reject above.
   */
  it('counts an inquiry-only covered rejected line as rejected, but NOT toward toConfirm', () => {
    const inquiryOnly = {
      ...contributions.find((entry) => entry.line_no === 2)!,
      covered: true,
      decision: null,
    };
    const summary = confirmSummaryFor([inquiryOnly], {
      [inquiryOnly.key]: { verdict: 'rejected', reason: 'Not needed.' },
    });
    expect(summary.rejected).toBe(1);
    expect(summary.toConfirm).toBe(0);
  });

  it('rejectedCoveredLineIdsFor names the covered line’s own project_line_id', () => {
    expect(
      rejectedCoveredLineIdsFor(contributions, 'so-a', {
        [keyOf(1)]: { verdict: 'rejected', reason: 'Changed my mind.' },
      }),
    ).toEqual(['pl-so-a-1']);
  });

  it('rejectedCoveredLineIdsFor leaves an UNCOVERED rejected line out - nothing active to withdraw', () => {
    expect(
      rejectedCoveredLineIdsFor(contributions, 'so-a', {
        [keyOf(2)]: { verdict: 'rejected', reason: 'Not needed.' },
      }),
    ).toEqual([]);
  });

  it('rejectedCoveredLineIdsFor is empty when nothing is rejected', () => {
    expect(rejectedCoveredLineIdsFor(contributions, 'so-a', {})).toEqual([]);
  });

  /**
   * N1 (fix round, `PLAN-board-reject-on-confirmed-line.md`): `covered` spans TWO kinds of
   * line - an ACTIVE decision (this describe block's line 1, `decision: frozen`), or a LIVE
   * order-inquiry row naming it with none at all (`inquiry_decided`, migrated sheet lines,
   * #875). Only the first has a `line_snapshots` entry Confirm's `rejected_line_ids` could
   * ever name, so a staged reject on the SECOND kind must contribute no id and no count -
   * built here by overriding line 2 (uncovered by default) to the inquiry-only shape,
   * since the board fixture's own `covered` is `Boolean(line.decision)` and has no
   * `inquiry_decided` knob of its own.
   */
  it('rejectedCoveredLineIdsFor and plannedLineCount exclude an INQUIRY-ONLY covered line - no active decision to withdraw', () => {
    const inquiryOnly = {
      ...contributions.find((entry) => entry.line_no === 2)!,
      covered: true,
      decision: null,
    };
    const draft = { [inquiryOnly.key]: { verdict: 'rejected' as const, reason: 'Not needed.' } };
    expect(rejectedCoveredLineIdsFor([inquiryOnly], 'so-a', draft)).toEqual([]);
    expect(plannedLineCount([inquiryOnly], 'so-a', draft)).toBe(0);
  });

  /**
   * S4 (fix round, review): a pending planning-change batch has no shape for a
   * withdrawal riding beside it (AC-B12, server refuses `rejected_line_ids` alongside
   * `batch_id` outright) - so a covered line's staged reject on a BATCHED order must
   * not count toward `plannedLineCount`/`toConfirm` either, or the "Confirm (N)" button
   * promises a withdrawal the press cannot actually carry out ("Confirm (1) then
   * nothing").
   */
  it('plannedLineCount excludes a covered rejected line when its order is in batchBlockedSalesOrderIds', () => {
    const draft = { [keyOf(1)]: { verdict: 'rejected' as const, reason: 'Wrong site.' } };
    // Unblocked: counts, exactly as `test_confirming_a_new_composition...` above pins.
    expect(plannedLineCount(contributions, 'so-a', draft)).toBe(1);
    // Blocked: the batch on so-a's own order holds it back.
    expect(plannedLineCount(contributions, 'so-a', draft, new Set(['so-a']))).toBe(0);
    // A DIFFERENT order's own batch block never reaches so-a's line.
    expect(plannedLineCount(contributions, 'so-a', draft, new Set(['so-b']))).toBe(1);
  });

  it('confirmSummaryFor still counts the withdrawal as rejected, but not toward toConfirm, once batch-blocked', () => {
    const draft = { [keyOf(1)]: { verdict: 'rejected' as const, reason: 'Wrong site.' } };
    const blocked = confirmSummaryFor(contributions, draft, new Set(['so-a']));
    expect(blocked.rejected).toBe(1);
    expect(blocked.toConfirm).toBe(0);
    const unblocked = confirmSummaryFor(contributions, draft);
    expect(unblocked.toConfirm).toBe(1);
  });
});

/**
 * C4 (code review round 3 batch 2): a stale line is dropped from Confirm with no trace -
 * `lineFor` posts nothing for it, `confirmSummaryFor` skips it, and until this the planner's
 * only signal was the pill itself, which is easy to miss on a board of forty lines. `changed`
 * is the count that says so where Confirm is pressed.
 */
describe('confirmSummaryFor: changed (C4)', () => {
  it('counts a saved-but-stale line under `changed`, and leaves it out of `toConfirm`', () => {
    const board = buildBoard(
      [line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' })],
      { today: TODAY },
    );
    const base = board.cells[0].contributions[0];
    const decision = { verdict: 'amended' as const };
    const stale = {
      ...base,
      draft: {
        decision,
        saved_by: 'Eling',
        saved_at: '2026-09-03T01:00:00',
        stale: true,
      },
    };

    // The Panel's own draft map mirrors every server draft into it before any press (the
    // seeding effect), so a real caller's `draft[key]` is never undefined here - it is seeded
    // the same way in this test.
    const summary = confirmSummaryFor([stale], { [stale.key]: decision });

    expect(summary.changed).toBe(1);
    expect(summary.toConfirm).toBe(0);
  });

  it('reads `changed` 0 and `toConfirm` 1 when the one saved line is not stale', () => {
    const board = buildBoard(
      [line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' })],
      { today: TODAY },
    );
    const base = board.cells[0].contributions[0];
    const decision = { verdict: 'approved' as const };
    const saved = {
      ...base,
      draft: { decision, saved_by: 'Eling', saved_at: '2026-09-03T01:00:00' },
    };

    const summary = confirmSummaryFor([saved], { [saved.key]: decision });

    expect(summary.changed).toBe(0);
    expect(summary.toConfirm).toBe(1);
  });
});

/**
 * R3 (captain's ruling, 13 Sep board-display round, scenario S5): a cancelled changed line
 * (the book removed it; a pending 'cancelled' `PlanningChangeRow` is what will retire it)
 * must count toward `toConfirm` when the board's Confirm(N) is pressed - it applies through
 * the retire path same as any other decided line. `confirmSummaryFor` has no concept of
 * `cancelled` at all today: an uncovered, undraft line - which is exactly what a cancelled
 * contribution looks like without a new field naming it - hits the SAME "untouched, nobody
 * saved it" skip (line ~627, `if (!contribution.covered && !decision) continue;`) an
 * ordinary undecided line does, so it is silently left out of `toConfirm`.
 */
describe('confirmSummaryFor: a cancelled changed line (R3, 13 Sep board-display round)', () => {
  it('counts a cancelled line toward toConfirm, not as an ordinary untouched line', () => {
    const board = buildBoard(
      [line({ sales_order_id: 'so-s5', so_number: 'SO400884', line_no: 1, qty: '72' })],
      { today: TODAY },
    );
    const base = board.cells[0].contributions[0];
    const cancelled = {
      ...base,
      qty: '0',
      qty_outstanding: '0',
      covered: false,
      decision: null,
      // Not yet on `BoardContribution` (grepped `fulfilmentPlanning.types.ts` - absent) -
      // the field the board-side fix is expected to add; rename if the coder picks a
      // different key.
      cancelled: true,
    } as BoardContribution & { cancelled: boolean };

    // No draft at all - a cancelled line's own retire is not something a planner "saves" the
    // way an amend or an approval is; it is inherent to the row itself.
    const summary = confirmSummaryFor([cancelled], {});

    expect(summary.toConfirm).toBe(1);
  });
});

/**
 * CONFIRM POSTS SAVED LINES ONLY (8 Sep 2026 ruling, reverses R11): a board of five lines
 * where only three were saved reads "3 to confirm", not five - the untouched line stays off
 * the count exactly as it stays off the body.
 */
describe('confirmSummaryFor: saved lines only (8 Sep 2026 ruling, reverses R11)', () => {
  it('reads toConfirm 3, rejected 1 over 3 saved, 1 untouched and 1 rejected line', () => {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '10' }),
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, qty: '10', item_code: 'TPE-9204' }),
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 3, qty: '10', item_code: 'TPE-9205' }),
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 4, qty: '10', item_code: 'TPE-9206' }),
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 5, qty: '10', item_code: 'TPE-9207' }),
      ],
      { today: TODAY },
    );
    const contributions = board.cells.flatMap((cell) => cell.contributions);
    const keyOf = (lineNo: number) =>
      contributions.find((entry) => entry.line_no === lineNo)!.key;

    const summary = confirmSummaryFor(contributions, {
      [keyOf(1)]: { verdict: 'approved' },
      [keyOf(2)]: { verdict: 'approved' },
      [keyOf(3)]: { verdict: 'approved' },
      [keyOf(5)]: { verdict: 'rejected', reason: 'No.' },
      // Line 4 is left out of the draft entirely: untouched, uncovered, undecided.
    });

    expect(summary.toConfirm).toBe(3);
    expect(summary.rejected).toBe(1);
  });
});

/**
 * AN APPROVAL ON A COVERED LINE POSTS THE ENGINE'S COMPOSITION, NOT THE FROZEN ONE.
 *
 * The plan's canonical line, third browser run: SO404352 line 22 was confirmed at Reserve 8
 * from BRW-AM plus 16 from the BRW pool while the engine suggests 9 plus 15. Amend, then the
 * engine's numbers typed back, flipped the pill to Approved and moved the Confirm counter -
 * and then posted the 8 and the 16 again ("0 transfers proposed", the revision unchanged after a
 * reload), because `qty_proposed_*` and the source strip both state the ACTIVE DECISION on a
 * covered line (the server's `_apply_frozen`). So the approval is composed from the
 * suggestion the way the editor composes it, and posted the way an amendment is.
 */
describe('confirmLinesFor: an approval on a covered line whose decision left the suggestion', () => {
  const frozen = {
    revision_no: 4,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [
      { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '8' },
      { warehouse_id: 'wh-BRW', location: 'BRW', qty: '16' },
    ],
    borrow: [],
    buy_qty: '0',
  };
  const suggestion = [
    {
      kind: 'reserve' as const,
      qty: '9',
      location: 'BRW-AM',
      warehouse_id: 'wh-BRW-AM',
      reason: 'Free unclaimed stock at BRW-AM covers this much by the delivery date.',
    },
    {
      kind: 'reserve' as const,
      qty: '15',
      location: 'BRW',
      warehouse_id: 'wh-BRW',
      reason: 'The shared pool at BRW covers this much within its cap.',
    },
  ];
  const board = buildBoard(
    [
      line({
        sales_order_id: 'so-a',
        so_number: 'SO404352',
        line_no: 22,
        qty: '24',
        item_code: 'SRTWB7518',
        fulfilment_location: 'BRW-AM',
        decision: frozen,
      }),
    ],
    { today: TODAY },
  );
  // As the server sends a covered line: `sources` and `qty_proposed_*` state the DECISION
  // (`_apply_frozen`), and the engine's own composition is the proposal frozen beside it.
  const contributions = board.cells
    .flatMap((cell) => cell.contributions)
    .map((entry) => ({ ...entry, proposed: { components: suggestion } }));
  const key = contributions[0].key;

  it('posts the engine’s 9 and 15, never the 8 and 16 the revision froze', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [key]: { verdict: 'approved' },
    });

    expect(lines).toHaveLength(1);
    expect(lines[0].reserve).toEqual([
      { warehouse_id: 'wh-BRW-AM', qty: '9' },
      { warehouse_id: 'wh-BRW', qty: '15' },
    ]);
    expect(lines[0].buy_qty).toBe('0');
    // An approval is not an override: it carries no reason of its own.
    expect(lines[0].amend_reason).toBeUndefined();
  });

  it('carries the doubt beside the verdict (R10)', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [key]: { verdict: 'approved', suspected_system_issue: true },
    });
    expect(lines[0].suspected_system_issue).toBe(true);
  });

  it('posts what the planner composed when they amended instead', () => {
    const lines = confirmLinesFor(contributions, 'so-a', {
      [key]: {
        verdict: 'amended',
        reserve_qty: '24',
        timely_spo_qty: '0',
        reserve: [
          { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '8' },
          { warehouse_id: 'wh-BRW', location: 'BRW', qty: '16' },
        ],
        borrow: [],
        buy_qty: '0',
        reason: 'The site took the extra from the pool.',
      },
    });

    expect(lines[0].reserve).toEqual([
      { warehouse_id: 'wh-BRW-AM', qty: '8' },
      { warehouse_id: 'wh-BRW', qty: '16' },
    ]);
    expect(lines[0].amend_reason).toBe('The site took the extra from the pool.');
  });

  it('still posts nothing for the covered line while nobody has touched it, or rejected it', () => {
    expect(confirmLinesFor(contributions, 'so-a', {})).toEqual([]);
    expect(
      confirmLinesFor(contributions, 'so-a', {
        [key]: { verdict: 'rejected', reason: 'The site cancelled it.' },
      }),
    ).toEqual([]);
  });

  it('leaves an UNCOVERED approval on its own derivation', () => {
    const uncovered = contributions.map((entry) => ({
      ...entry,
      covered: false,
      decision: null,
      sources: suggestion,
      qty_proposed_reserve: '24',
      qty_proposed_incoming: '0',
      qty_proposed_buy: '0',
    }));
    const lines = confirmLinesFor(uncovered, 'so-a', { [key]: { verdict: 'approved' } });

    expect(lines[0].reserve).toEqual([
      { warehouse_id: 'wh-BRW-AM', qty: '9' },
      { warehouse_id: 'wh-BRW', qty: '15' },
    ]);
    expect(lines[0].buy_qty).toBe('0');
  });
});

/**
 * Adoption mirrored the order's OPEN lines at the time it ran, so a later upload can add a core
 * line that has no mirror. Its order is still confirmable; that one line is not.
 *
 * The planner may well have approved that row, so it is named rather than silently dropped -
 * and the fix (re-sync on the sheet) is somewhere else entirely, which is exactly why saying
 * nothing would be the wrong answer.
 */
describe('confirmLinesFor and a line with no mirror', () => {
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '10' }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, qty: '20', item_code: 'TPE-9204' }),
    ],
    { today: TODAY },
  );
  const contributions = board.cells
    .flatMap((cell) => cell.contributions)
    .map((entry) => (entry.line_no === 2 ? { ...entry, project_line_id: null } : entry));
  const approved = Object.fromEntries(
    contributions.map((entry) => [entry.key, { verdict: 'approved' as const }]),
  );

  it('leaves the unmirrored line out of the body', () => {
    const lines = confirmLinesFor(contributions, 'so-a', approved);
    expect(lines).toHaveLength(1);
    expect(lines[0].project_line_id).toBe('pl-so-a-1');
  });

  it('names the decided lines it had to leave out, so the planner is not told they committed', () => {
    expect(
      unpostableDecidedFor(contributions, 'so-a', approved).map(
        (entry) => `${entry.contribution.item_code} line ${entry.contribution.line_no}: ${entry.reason}`,
      ),
    ).toEqual(['TPE-9204 line 2: no_mirror']);
  });

  it('counts nothing as unpostable when every decided line has its mirror', () => {
    const whole = board.cells.flatMap((cell) => cell.contributions);
    expect(unpostableDecidedFor(whole, 'so-a', approved)).toEqual([]);
  });

  it('does not count a REJECTED unmirrored line as unpostable: it was never going to post', () => {
    const rejected = Object.fromEntries(
      contributions.map((entry) => [entry.key, { verdict: 'rejected' as const, reason: 'No.' }]),
    );
    expect(unpostableDecidedFor(contributions, 'so-a', rejected)).toEqual([]);
  });
});

/**
 * D2 (AC-19, AC-20): a Buy on a DISCONTINUED product no longer needs a reason to post. The
 * board still carries the discontinued flag (for the warning chip/badge elsewhere), but
 * `lineFor` treats a discontinued Buy the same as any other line now - it posts with or
 * without a `buy_reason`, and `unpostableDecidedFor` never names it for that cause.
 */
describe('confirmLinesFor and a discontinued product', () => {
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '10' }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, qty: '20', item_code: 'OLD-1' }),
    ],
    { today: TODAY },
  );
  const discontinued = {
    dealer_hot_selling: false,
    dealer_hot_selling_where: [],
    project_hot_selling: false,
    project_hot_selling_where: [],
    dealer_classified: false,
    project_classified: false,
    discontinued: true,
    retail_classification_available: true,
  };
  const contributions = board.cells
    .flatMap((cell) => cell.contributions)
    .map((entry) => (entry.line_no === 2 ? { ...entry, item_flags: discontinued } : entry));
  const old = contributions.find((entry) => entry.line_no === 2) as BoardContribution;
  const approved = Object.fromEntries(
    contributions.map((entry) => [entry.key, { verdict: 'approved' as const }]),
  );

  it('AC-19/AC-20: posts an approved Buy of a discontinued product with no reason, same as any other line', () => {
    const lines = confirmLinesFor(contributions, 'so-a', approved);
    expect(lines.map((entry) => entry.project_line_id).sort()).toEqual([
      'pl-so-a-1',
      'pl-so-a-2',
    ]);
    expect(
      lines.find((entry) => entry.project_line_id === 'pl-so-a-2')!.buy_reason,
    ).toBeUndefined();
    expect(unpostableDecidedFor(contributions, 'so-a', approved)).toEqual([]);
  });

  it('posts it once the amendment carries the reason', () => {
    const draft = {
      ...approved,
      [old.key]: {
        verdict: 'amended' as const,
        reserve: [],
        borrow: [],
        buy_qty: '20',
        timely_spo_qty: '0',
        buy_reason: 'Last batch for the site.',
      },
    };
    expect(confirmLinesFor(contributions, 'so-a', draft)).toHaveLength(2);
    expect(unpostableDecidedFor(contributions, 'so-a', draft)).toEqual([]);
  });

  /**
   * BOARD-CONFIRM-LEFT-OUT (measured cause 1): an APPROVED (not amended) verdict already
   * reads `decision.buy_reason` on this UNCOVERED derivation - this is the panel's own
   * approving Save, once it carries the reason (`BoardLineDecisionPanel.test.tsx`). Pinned
   * here so the derivation this test exercises cannot regress the same way the COVERED
   * one did (see "confirmLinesFor and an approved COVERED line" below).
   */
  it('posts an approved Buy once the approval itself carries the reason', () => {
    const draft = {
      ...approved,
      [old.key]: { verdict: 'approved' as const, buy_reason: 'Last batch for the site.' },
    };
    const lines = confirmLinesFor(contributions, 'so-a', draft);
    expect(lines.map((entry) => entry.project_line_id).sort()).toEqual([
      'pl-so-a-1',
      'pl-so-a-2',
    ]);
    expect(lines.find((entry) => entry.project_line_id === 'pl-so-a-2')!.buy_reason).toBe(
      'Last batch for the site.',
    );
    expect(unpostableDecidedFor(contributions, 'so-a', draft)).toEqual([]);
  });

  it('AC-19: posts an amended Buy of a discontinued product with no reason (the other, SAVED line also posts)', () => {
    const draft = {
      [contributions.find((entry) => entry.line_no === 1)!.key]: { verdict: 'approved' as const },
      [old.key]: {
        verdict: 'amended' as const,
        reserve: [],
        borrow: [],
        buy_qty: '20',
        timely_spo_qty: '0',
      },
    };
    const lines = confirmLinesFor(contributions, 'so-a', draft);
    expect(lines.map((entry) => entry.project_line_id).sort()).toEqual([
      'pl-so-a-1',
      'pl-so-a-2',
    ]);
    expect(unpostableDecidedFor(contributions, 'so-a', draft)).toEqual([]);
  });

  it('does not ask for a reason when the discontinued line buys nothing', () => {
    const covered = contributions.map((entry) =>
      entry.line_no === 2
        ? {
            ...entry,
            qty_proposed_reserve: '20',
            qty_proposed_buy: '0',
            sources: [
              { kind: 'reserve' as const, qty: '20', location: 'BRW-BB', warehouse_id: 'wh-BRW-BB', reason: 'Covered.' },
            ],
          }
        : entry,
    );
    expect(confirmLinesFor(covered, 'so-a', approved)).toHaveLength(2);
    expect(unpostableDecidedFor(covered, 'so-a', approved)).toEqual([]);
  });

  it('AC-19: an order nobody has adopted yet is unaffected by the discontinued cause - both lines read no_mirror and both count as planned', () => {
    const unadopted = contributions.map((entry) => ({ ...entry, project_line_id: null }));
    expect(
      unpostableDecidedFor(unadopted, 'so-a', approved, false).map((entry) => entry.reason),
    ).toEqual([]);
    expect(plannedLineCount(unadopted, 'so-a', approved)).toBe(2);
  });
});

/**
 * BOARD-CONFIRM-LEFT-OUT (SO420745, 21 Sep 2026, measured cause 1): the COVERED-approved
 * derivation rebuilds the suggestion (`suggestionWithReasons` in `boardAmend.ts`), because a
 * covered line's `qty_proposed_*`/`sources` state the ACTIVE DECISION, not the engine's
 * suggestion - so it cannot read `decision.buy_reason` off the derivation the way the
 * UNCOVERED path above does. It used to drop `buy_reason` (and any borrow reason) on that
 * rebuild, so a discontinued line the planner had already given a reason for, on an approving
 * Save, still read `buy_reason_missing` at Confirm.
 */
describe('confirmLinesFor and an approved COVERED line: the buy reason travels with it', () => {
  const frozen = {
    revision_no: 1,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [],
    borrow: [],
    buy_qty: '10',
  };
  const discontinuedFlags = {
    dealer_hot_selling: false,
    dealer_hot_selling_where: [],
    project_hot_selling: false,
    project_hot_selling_where: [],
    dealer_classified: false,
    project_classified: false,
    discontinued: true,
    retail_classification_available: true,
  };
  const board = buildBoard(
    [
      line({
        sales_order_id: 'so-a',
        so_number: 'SO420745',
        line_no: 32,
        item_code: 'SRTWT9610-GM',
        qty: '10',
        decision: frozen,
      }),
    ],
    { today: TODAY },
  );
  const contributions = board.cells
    .flatMap((cell) => cell.contributions)
    .map((entry) => ({ ...entry, item_flags: discontinuedFlags }));
  const key = contributions[0].key;
  const REASON = 'Owner accepted this discontinued item for the project.';

  it('is not unpostable once the approving decision carries a fresh buy reason', () => {
    const draft = { [key]: { verdict: 'approved' as const, buy_reason: REASON } };
    expect(unpostableDecidedFor(contributions, 'so-a', draft)).toEqual([]);
  });

  it('the confirm line carries the buy reason', () => {
    const draft = { [key]: { verdict: 'approved' as const, buy_reason: REASON } };
    const lines = confirmLinesFor(contributions, 'so-a', draft);
    expect(lines[0].buy_reason).toBe(REASON);
  });

  it('AC-19/AC-20: no longer refuses it without one - the reason is optional now', () => {
    const draft = { [key]: { verdict: 'approved' as const } };
    expect(unpostableDecidedFor(contributions, 'so-a', draft)).toEqual([]);
    const lines = confirmLinesFor(contributions, 'so-a', draft);
    expect(lines).toHaveLength(1);
    expect(lines[0].buy_reason).toBeUndefined();
  });
});

/**
 * A decided line whose Reserve cannot be addressed to any warehouse is left out of the body
 * rather than posted with a guessed id. It used to be left out SILENTLY: the button read
 * "Confirm 7 lines" beside eight verdicts and nothing said which one was missing or why.
 */
describe('confirmLinesFor and a Reserve nobody can address', () => {
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '10' }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, qty: '20', item_code: 'TPE-9204' }),
    ],
    { today: TODAY },
  );
  const contributions = board.cells.flatMap((cell) => cell.contributions).map((entry) =>
    entry.line_no === 2
      ? {
          ...entry,
          qty_proposed_reserve: '20',
          qty_proposed_buy: '0',
          sources: [{ kind: 'reserve' as const, qty: '20', location: 'BRW-BB', reason: 'Covered.' }],
        }
      : entry,
  );
  const approved = Object.fromEntries(
    contributions.map((entry) => [entry.key, { verdict: 'approved' as const }]),
  );

  it('names the line it left out, so the body length and the notice agree', () => {
    const lines = confirmLinesFor(contributions, 'so-a', approved);
    const unpostable = unpostableDecidedFor(contributions, 'so-a', approved);
    expect(lines).toHaveLength(1);
    expect(unpostable.map((entry) => `${entry.contribution.item_code}: ${entry.reason}`)).toEqual([
      'TPE-9204: no_reserve_warehouse',
    ]);
    expect(lines.length + unpostable.length).toBe(Object.keys(approved).length);
  });
});

/**
 * The day window scrolls by a WHOLE window (deviation 7), whether or not the current one has any
 * dated column in it: an empty stretch of calendar is still thirty days wide.
 */
describe('shiftedDayWindow', () => {
  it('moves by the contract\'s window, forwards and back', () => {
    expect(BOARD_DAY_WINDOW_COLUMNS).toBe(30);
    expect(shiftedDayWindow('2026-08-18', 1)).toBe('2026-09-17');
    expect(shiftedDayWindow('2026-08-18', -1)).toBe('2026-07-19');
  });

  it('crosses a year boundary without drifting', () => {
    expect(shiftedDayWindow('2026-12-20', 1)).toBe('2027-01-19');
  });
});

/**
 * Pivoting the row axis (the captain: "i wonder if we can view in the dimension of sales order
 * also ... how about if we want vertical is sales order, is customer, is project").
 *
 * The dates stay across the top and the SAME contributions are grouped differently into cells.
 * No second fetch, and no second idea of what a line is - which is why a decision made under
 * one axis is still that line's decision under another.
 */
describe('boardAxis: pivoting the rows', () => {
  function contributionsOf() {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, item_code: 'AAA', qty: '10' }),
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, item_code: 'BBB', qty: '20' }),
        line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 3, item_code: 'AAA', qty: '5' }),
      ],
      { today: TODAY },
    );
    return board.cells;
  }

  it('keeps each date its own cell: a pivot changes the ROWS, never the columns', () => {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, item_code: 'AAA', qty: '10', required_date: '2026-09-04' }),
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, item_code: 'BBB', qty: '20', required_date: '2026-12-01' }),
      ],
      { today: TODAY },
    );

    const { cells } = boardAxis('sales_order', board.cells);

    // One order, two dates: two cells, not one lump of 30.
    expect(cells).toHaveLength(2);
    expect(cells.map((cell) => `${cell.bucket_key} ${cell.total_qty}`).sort()).toEqual([
      '2026-08-31 10',
      '2026-11-30 20',
    ]);
  });

  it('groups a sales-order row by ORDER, across every product it owes', () => {
    const { rows, cells } = boardAxis('sales_order', contributionsOf());

    expect(rows.map((row) => row.label)).toEqual(['SO000001', 'SO000002']);
    const first = cells.find((cell) => cell.row_key === 'so-a');
    // Both of SO000001's lines, though they are different products.
    expect(first?.contributions).toHaveLength(2);
    expect(first?.total_qty).toBe('30');
  });

  it('keys a row on the ID and labels it with the name, so one name is never two customers merged', () => {
    const shared = contributionsOf().map((cell) => ({
      ...cell,
      contributions: cell.contributions.map((entry) => ({
        ...entry,
        customer_name: 'ABC SDN BHD',
        customer_id: entry.sales_order_id === 'so-a' ? 'cust-1' : 'cust-2',
      })),
    }));

    const { rows } = boardAxis('customer', shared);

    expect(rows.map((row) => row.key)).toEqual(['cust-1', 'cust-2']);
    expect(rows.map((row) => row.label)).toEqual(['ABC SDN BHD', 'ABC SDN BHD']);
  });

  it('groups a project row on its own key', () => {
    const tagged = contributionsOf().map((cell) => ({
      ...cell,
      contributions: cell.contributions.map((entry) => ({
        ...entry,
        project_key: entry.sales_order_id === 'so-a' ? 'proj-1' : 'proj-2',
        project_label: entry.sales_order_id === 'so-a' ? 'TOWER A' : 'TOWER B',
      })),
    }));

    const { rows, cells } = boardAxis('project', tagged);

    expect(rows.map((row) => `${row.key} ${row.label}`)).toEqual([
      'proj-1 TOWER A',
      'proj-2 TOWER B',
    ]);
    expect(cells.find((cell) => cell.row_key === 'proj-1')?.total_qty).toBe('30');
  });

  it('orders sales orders by number, and customers and projects alphabetically', () => {
    const jumbled = contributionsOf().map((cell) => ({
      ...cell,
      contributions: cell.contributions.map((entry) => ({
        ...entry,
        customer_id: entry.sales_order_id === 'so-a' ? 'c-z' : 'c-a',
        customer_name: entry.sales_order_id === 'so-a' ? 'ZULU SDN BHD' : 'ALPHA SDN BHD',
      })),
    }));
    expect(boardAxis('customer', jumbled).rows.map((row) => row.label)).toEqual([
      'ALPHA SDN BHD',
      'ZULU SDN BHD',
    ]);
    expect(boardAxis('sales_order', jumbled).rows.map((row) => row.label)).toEqual([
      'SO000001',
      'SO000002',
    ]);
  });

  it('carries the per-line facts up into the cell, rather than re-deriving them', () => {
    const flagged = contributionsOf().map((cell) => ({
      ...cell,
      contributions: cell.contributions.map((entry) => ({
        ...entry,
        is_past: entry.item_code === 'AAA',
        unplannable: entry.line_no === 2,
        contested: entry.line_no === 3,
      })),
    }));

    const cell = boardAxis('sales_order', flagged).cells.find((c) => c.row_key === 'so-a');

    expect(cell?.past_count).toBe(1);
    expect(cell?.unplannable_count).toBe(1);
    expect(cell?.contested_count).toBe(0);
  });

  it('states no stock position on a pivoted cell, because it spans several products', () => {
    // On-hand and free are facts about ONE product at ONE location. A cell holding three
    // products has no single stock position, so it claims none rather than inventing one.
    const cell = boardAxis('sales_order', contributionsOf()).cells[0];
    expect(cell.locations).toEqual([]);
  });

  it('says how many lines it groups, so the reader knows what a cell holds', () => {
    const cell = boardAxis('sales_order', contributionsOf()).cells.find(
      (entry) => entry.row_key === 'so-a',
    );
    expect(cell?.contributions.length).toBe(2);
  });

  it('falls back to the label when the server sends no id, and says nothing false about it', () => {
    const unkeyed = contributionsOf().map((cell) => ({
      ...cell,
      contributions: cell.contributions.map((entry) => ({
        ...entry,
        customer_id: null,
        customer_name: 'ONLY ONE SDN BHD',
      })),
    }));
    const { rows } = boardAxis('customer', unkeyed);
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe('ONLY ONE SDN BHD');
  });
});

/**
 * Searching the board across the four things a planner knows an order by.
 *
 * A ROW survives if ANY of its lines matches. The cells in that row still hold all their
 * contributions: filtering inside a cell would print a total that is not the cell's.
 */
describe('rowMatchesSearch', () => {
  const contributions = [
    {
      key: 'k1',
      sales_order_id: 'so-a',
      so_number: 'SO391698',
      customer_name: 'OIB CONSTRUCTION SDN BHD',
      project_label: 'MYRA DAHLIA 9307',
      item_code: 'WESERP10B',
    },
  ] as BoardContribution[];

  const row = { key: 'WESERP10B', label: 'WESERP10B', description: 'Wall socket 10A' };

  it('matches on any of the four, case-insensitively', () => {
    for (const needle of ['so3916', 'oib const', 'myra', 'weserp', 'wall socket']) {
      expect(rowMatchesSearch(row, contributions, needle)).toBe(true);
    }
  });

  it('does not match a needle none of them carries', () => {
    expect(rowMatchesSearch(row, contributions, 'zzzz')).toBe(false);
  });

  it('keeps every row when nothing is typed', () => {
    expect(rowMatchesSearch(row, contributions, '   ')).toBe(true);
  });
});

/**
 * The ranking wording, from ONE place.
 *
 * The backend lane is reconsidering what the single-line and single-order cases should say -
 * "the policy separates none of these rows" is true there but reads as a policy failure, when
 * the real cause is that one order's lines share a customer, a date and a class. So the words
 * live in one function keyed on the flags, and changing them later is an edit here rather than
 * a hunt through two components.
 */
describe('rankingNote', () => {
  function cellOf(count: number, extra: Partial<BoardCell> = {}): BoardCell {
    return {
      item_code: 'AAA',
      bucket_key: '2026-08-31',
      total_qty: '10',
      locations: [],
      contributions: Array.from({ length: count }, () => ({}) as BoardContribution),
      unplannable_count: 0,
      contested_count: 0,
      ...extra,
    };
  }

  it('shows the ranking, and says nothing, when the policy separates the rows', () => {
    const note = rankingNote(cellOf(3, { rank_separates: true, distinct_order_count: 3 }));
    expect(note).toBeNull();
  });

  it('still says Not ranked for one line, but carries no sentence about it', () => {
    // Nothing was ranked because there was nothing to rank against, and "Only line in this
    // cell" said that back to a reader who could already see the single row underneath it.
    // Removed 25 August 2026: a sentence that restates the screen is a sentence to skip past.
    // The Rank column keeps "Not ranked" - a flat 0.00 there would claim a ranking nobody ran.
    const note = rankingNote(cellOf(1, { rank_separates: false, distinct_order_count: 1 }));
    expect(note?.cell).toBe('Not ranked');
    expect(note?.note).toBeNull();
  });

  it('says nothing when the tie is one order competing with itself (ladder v4)', () => {
    // It used to read "Same sales order; line order decided which line was served first",
    // which described the rank queue deciding availability. Under ladder v4 availability is
    // the ownership group's and rank decides only the order of service, so the sentence
    // named a cause the engine no longer has.
    const note = rankingNote(cellOf(4, { rank_separates: false, distinct_order_count: 1 }));
    expect(note?.cell).toBe('Not ranked');
    expect(note?.note).toBeNull();
  });

  it('keeps the policy sentence for a real tie between different orders', () => {
    const note = rankingNote(cellOf(4, { rank_separates: false, distinct_order_count: 3 }));
    expect(note?.note).toBe('The active policy separates none of these rows');
  });

  it('says nothing about a cell that states neither flag, so the rank shows as scored', () => {
    // A pivoted cell spans several piles and has no single ranking to describe; the lines
    // keep their own scores and no tie sentence is invented for them.
    expect(rankingNote(cellOf(4))).toBeNull();
  });

  it('is null on every pivoted cell', () => {
    const board = buildBoard(
      [
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, item_code: 'AAA', qty: '10' }),
        line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, item_code: 'BBB', qty: '20' }),
      ],
      { today: TODAY },
    );
    const { cells } = boardAxis('sales_order', cells_with_flags(board.cells));
    expect(cells).toHaveLength(1);
    expect(cells[0].contributions).toHaveLength(2);
    expect(rankingNote(cells[0])).toBeNull();
  });

  function cells_with_flags(cells: BoardCell[]): BoardCell[] {
    return cells.map((cell) => ({ ...cell, rank_separates: false, distinct_order_count: 1 }));
  }
});

/**
 * Review round 1, B1: a composition is balanced against the PLAN quantity.
 *
 * The 14 September 2026 ruling split `qty` from `qty_outstanding` - the board asks for the
 * whole ordered quantity because a delivered unit nobody sourced is a unit to put back - and
 * the server's own balance check validates against that figure (`_LineFacts.open_qty`).
 * Everything on this side that composes had gone on reading `qty_outstanding ?? qty`, so on
 * any line with a delivery the client posted a body summing to less than the line asks for
 * and the confirm refused the whole order.
 */
describe('a delivered line composes against its plan quantity', () => {
  /** 3 ordered, 1 delivered: the plan quantity is 3 and 2 is merely what is still owed. */
  const delivered: BoardContribution = {
    key: 'so-d|1|WESERP10B|2026-08-17',
    sales_order_id: 'so-d',
    so_number: 'SO000009',
    project_line_id: 'pl-so-d-1',
    line_no: 1,
    item_code: 'WESERP10B',
    qty: '3',
    qty_ordered: '3',
    qty_delivered: '1',
    qty_outstanding: '2',
    qty_proposed_reserve: '0',
    qty_proposed_incoming: '0',
    qty_proposed_buy: '3',
    fulfilment_location: 'BRW-BB',
    fulfilment_warehouse_id: 'wh-BRW-BB',
    rank_score: 1,
    rank_factors: [],
    sources: [
      {
        kind: 'buy',
        qty: '3',
        location: null,
        warehouse_id: null,
        reason: 'Nothing free at the location.',
      },
    ],
    unplannable: false,
    contested: false,
  };

  it('posts the whole 3 on an approval, not the 2 still owed', () => {
    const [posted] = confirmLinesFor([delivered], 'so-d', {
      [delivered.key]: { verdict: 'approved' },
    });
    expect(posted.buy_qty).toBe('3');
    expect(
      Number(posted.buy_qty) +
        Number(posted.timely_spo_qty) +
        posted.reserve.reduce((total, row) => total + Number(row.qty), 0),
    ).toBe(3);
  });

  it('seeds the amend editor to balance against 3', () => {
    expect(suggestionDraftFrom(delivered).open_qty).toBe('3');
  });
});

describe('#1362 item 5: soLineLabel / soLineNoText', () => {
  it('names the AutoCount line number, never the planning row index', async () => {
    const { soLineLabel, soLineNoText } = await import('./fulfilmentBoard');
    expect(soLineLabel({ so_line_no: 2912, line_no: 110 })).toBe('Line 2912');
    expect(soLineNoText({ so_line_no: 2912, line_no: 110 })).toBe('2912');
    // AutoCount number 0 is a real number, not "absent".
    expect(soLineLabel({ so_line_no: 0, line_no: 4 })).toBe('Line 0');
  });

  it('falls back to the plain position only when AutoCount gave the line no number', async () => {
    const { soLineLabel, soLineNoText, soLineNoIsSynced } = await import('./fulfilmentBoard');
    expect(soLineLabel({ so_line_no: null, line_no: 110 })).toBe('Line 110');
    expect(soLineNoText({ so_line_no: null, line_no: 3 })).toBe('3');
    expect(soLineNoIsSynced({ so_line_no: null })).toBe(false);
    expect(soLineNoIsSynced({ so_line_no: 2912 })).toBe(true);
    expect(soLineNoIsSynced({})).toBe(false);
    // A payload with no so_line_no key at all predates the field: its number is bare.
    expect(soLineNoText({ line_no: 3 })).toBe('3');
  });
});

describe('#1362 item 5: failingLineText', () => {
  it('names a refused line by its AutoCount number, not the positional one', async () => {
    const { failingLineText } = await import('./fulfilmentBoard');
    expect(
      failingLineText({
        line_no: 110,
        so_line_no: 2912,
        item_code: 'B2154-NL',
        reason: '100 landed for this line on SPO-2026/09-0036; nothing to buy for it',
      }),
    ).toBe('Line 2912, B2154-NL: 100 landed for this line on SPO-2026/09-0036; nothing to buy for it');
    expect(failingLineText({ line_no: null, reason: 'Nothing is mapped yet.' })).toBe(
      'Nothing is mapped yet.',
    );
  });
});

describe('#1362 (owner, 29 Sep 2026): confirmNoticeLines', () => {
  it('names a held-back line and a Buy kept over landed goods, each by its line', async () => {
    const { confirmNoticeLines } = await import('./fulfilmentBoard');
    expect(
      confirmNoticeLines({
        lines_held_back: [
          {
            line_no: 29,
            so_line_no: null,
            item_code: 'B2154-NL',
            reason: 'The components add up to 50 and the line is open for 100.',
          },
        ],
        landed_buy_notices: [
          {
            line_no: 110,
            so_line_no: 2912,
            item_code: 'B2154-NL',
            reason:
              'Buy 100 confirmed as decided; 100 landed for this line on SPO-2026/06-0131 stay linked to it, for purchasing to adjust',
          },
        ],
      }),
    ).toEqual([
      'Line 29, B2154-NL: held back, decision kept: The components add up to 50 and the line is open for 100.',
      'Line 2912, B2154-NL: Buy 100 confirmed as decided; 100 landed for this line on SPO-2026/06-0131 stay linked to it, for purchasing to adjust',
    ]);
    expect(confirmNoticeLines({})).toEqual([]);
  });
});

/**
 * FULFIL-CONFIRM-SCOPE (S2): Confirm posts what the planner ticked in the pre-confirm dialog.
 * `excludeKeys` leaves a contribution out of the body and the count; `confirmDialogRowsFor`
 * is the pure read the dialog lists (AC-D1, D2, D4, D5).
 */
describe('confirm scope: excludeKeys', () => {
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, qty: '50', item_code: 'TPE-9204' }),
      line({ sales_order_id: 'so-b', so_number: 'SO000002', line_no: 3, qty: '10' }),
    ],
    { today: TODAY, freeStock: { 'WESERP10B|BRW-BB': '100', 'TPE-9204|BRW-BB': '20' } },
  );
  const contributions = board.cells.flatMap((cell) => cell.contributions);
  const keyOf = (soNumber: string, lineNo: number) =>
    contributions.find((entry) => entry.so_number === soNumber && entry.line_no === lineNo)!.key;
  const saved = () => ({
    [keyOf('SO000001', 1)]: { verdict: 'approved' as const },
    [keyOf('SO000001', 2)]: { verdict: 'approved' as const },
    [keyOf('SO000002', 3)]: { verdict: 'approved' as const },
  });

  it('confirmLinesFor leaves an excluded contribution out of the returned lines (AC-D3)', () => {
    const lines = confirmLinesFor(contributions, 'so-a', saved(), {
      excludeKeys: new Set([keyOf('SO000001', 2)]),
    });
    expect(lines.map((entry) => entry.project_line_id)).toEqual(['pl-so-a-1']);
  });

  it('confirmSummaryFor lowers toConfirm by the excluded count (AC-D3)', () => {
    const all = confirmSummaryFor(contributions, saved(), new Set<string>());
    expect(all.toConfirm).toBe(3);
    const fewer = confirmSummaryFor(contributions, saved(), new Set<string>(), {
      excludeKeys: new Set([keyOf('SO000001', 1)]),
    });
    expect(fewer.toConfirm).toBe(2);
  });
});

describe('confirmDialogRowsFor', () => {
  const OPENED = new Date('2026-09-30T02:00:00Z');
  const EIGHT_DAYS_AGO = '2026-09-22T03:09:00Z';
  const board = buildBoard(
    [
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100', item_code: 'SRTSH1040' }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 2, qty: '239', item_code: 'SRTWT6808' }),
      line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 3, qty: '10', item_code: 'TPE-9204' }),
    ],
    { today: TODAY },
  );
  const base = board.cells.flatMap((cell) => cell.contributions);
  const at = (lineNo: number) => base.find((entry) => entry.line_no === lineNo)!;

  const borrowSource = (qty: string) => ({
    kind: 'borrow' as const,
    qty,
    location: 'BRW-BB',
    warehouse_id: 'wh-brw-bb',
    reason: 'Group borrow.',
  });
  const withDrafts: BoardContribution[] = [
    {
      ...at(1),
      qty_proposed_reserve: '0',
      qty_proposed_incoming: '0',
      qty_proposed_buy: '0',
      sources: [borrowSource('43'), borrowSource('57')],
      draft: {
        decision: { verdict: 'approved' },
        saved_by: 'Jayson Foundryx',
        saved_at: EIGHT_DAYS_AGO,
      },
    },
    {
      ...at(2),
      draft: {
        decision: { verdict: 'amended', reserve_qty: '0', reserve: [], borrow: [], buy_qty: '239', reason: 'Late.' },
        saved_by: 'Cyndi',
        saved_at: '2026-09-30T02:30:00Z',
      },
    },
    {
      ...at(3),
      draft: {
        decision: { verdict: 'approved' },
        saved_by: 'Cyndi',
        saved_at: '2026-09-29T01:00:00Z',
        stale: true,
      },
    },
  ];
  const draft = {
    [withDrafts[0].key]: withDrafts[0].draft!.decision,
    [withDrafts[1].key]: withDrafts[1].draft!.decision,
    [withDrafts[2].key]: withDrafts[2].draft!.decision,
  };
  const result = () =>
    confirmDialogRowsFor(withDrafts, draft, { currentUserName: 'Cyndi', openedAt: OPENED });

  it('describes an approved line by the live suggestion that WILL be posted (AC-D5)', () => {
    const row = result().rows.find((entry) => entry.line_no === 1)!;
    expect(row).toMatchObject({
      key: withDrafts[0].key,
      sales_order_id: 'so-a',
      so_number: 'SO000001',
      item_code: 'SRTSH1040',
      verdict: 'approved',
      composition: 'Borrow 43 + 57 from BRW-BB',
      saved_by: 'Jayson Foundryx',
      saved_at: EIGHT_DAYS_AGO,
    });
  });

  it('describes an amended buy by its own composition (AC-D1)', () => {
    const row = result().rows.find((entry) => entry.line_no === 2)!;
    expect(row.verdict).toBe('amended');
    expect(row.composition).toBe('Buy 239');
  });

  it('flags a row saved by someone else, before the board was opened (AC-D2)', () => {
    const jayson = result().rows.find((entry) => entry.line_no === 1)!;
    expect(jayson.savedByOther).toBe(true);
    expect(jayson.savedBefore).toBe(true);
    const own = result().rows.find((entry) => entry.line_no === 2)!;
    expect(own.savedByOther).toBe(false);
    expect(own.savedBefore).toBe(false);
  });

  it('lists a stale line under notPosted with its reason, not as a row (AC-D4)', () => {
    const { rows, notPosted } = result();
    expect(rows.map((entry) => entry.line_no)).toEqual([1, 2]);
    expect(notPosted).toHaveLength(1);
    expect(notPosted[0]).toMatchObject({ key: withDrafts[2].key, line_no: 3, item_code: 'TPE-9204' });
    expect(notPosted[0].reason).toContain('Suggestion changed');
  });
});

/**
 * FULFIL-CONFIRM-SCOPE fix round 1: title, dialog rows, button and body are views of ONE
 * population. For every kind of line, the rows the dialog lists equal what the body posts
 * (lines plus withdrawals), and rows plus cancelled rows equal `plannedLineCount`.
 */
describe('confirm scope: one population for dialog, count and body', () => {
  const board = buildBoard(
    [line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' })],
    { today: TODAY, freeStock: { 'WESERP10B|BRW-BB': '100' } },
  );
  const base = board.cells.flatMap((cell) => cell.contributions)[0];
  const OPENED = new Date('2026-09-30T02:00:00Z');
  const covered = { covered: true, decision: {} as never };

  type Case = {
    name: string;
    contribution: Partial<BoardContribution>;
    decision: ({ verdict: 'approved' | 'amended' | 'rejected' } & Record<string, unknown>) | null;
    blocked?: boolean;
    unadopted?: boolean;
    listed: boolean;
    counted: number;
  };
  const amended = {
    verdict: 'amended' as const,
    reserve_qty: '0',
    reserve: [],
    borrow: [],
    buy_qty: '100',
    reason: 'Late.',
  };
  const cases: Case[] = [
    { name: 'covered + approved', contribution: covered, decision: { verdict: 'approved' }, listed: true, counted: 1 },
    { name: 'covered + amended', contribution: covered, decision: amended, listed: true, counted: 1 },
    { name: 'covered + rejected (withdrawal)', contribution: covered, decision: { verdict: 'rejected' }, listed: true, counted: 1 },
    { name: 'batch-blocked rejection', contribution: covered, decision: { verdict: 'rejected' }, blocked: true, listed: false, counted: 0 },
    { name: 'stale draft', contribution: { draft: { decision: { verdict: 'approved' }, saved_by: 'x', saved_at: '2026-09-29T00:00:00Z', stale: true } }, decision: { verdict: 'approved' }, listed: false, counted: 0 },
    { name: 'no_mirror on an adopted order', contribution: { project_line_id: null }, decision: { verdict: 'approved' }, listed: false, counted: 0 },
    { name: 'no_mirror on an unadopted order', contribution: { project_line_id: null }, decision: { verdict: 'approved' }, unadopted: true, listed: true, counted: 1 },
    { name: 'cancelled', contribution: { cancelled: true }, decision: null, listed: false, counted: 1 },
  ];

  it.each(cases)('$name', (entry) => {
    const contribution = { ...base, ...entry.contribution } as BoardContribution;
    const draft = entry.decision ? { [contribution.key]: entry.decision as never } : {};
    const blocked = new Set(entry.blocked ? ['so-a'] : []);
    const unadopted = new Set(entry.unadopted ? ['so-a'] : []);
    const dialog = confirmDialogRowsFor([contribution], draft, {
      currentUserName: 'Cyndi',
      openedAt: OPENED,
      unadoptedSalesOrderIds: unadopted,
      batchBlockedSalesOrderIds: blocked,
    });
    const posted = confirmLinesFor([contribution], 'so-a', draft).map((row) => row.project_line_id);
    // A rejection under a pending change is zeroed out of the body by the caller.
    const withdrawn = entry.blocked
      ? []
      : rejectedCoveredLineIdsFor([contribution], 'so-a', draft);
    const bodyCount = posted.length + withdrawn.length;
    expect(dialog.rows.length).toBe(entry.listed ? 1 : 0);
    if (!entry.unadopted) expect(dialog.rows.length).toBe(bodyCount);
    expect(
      dialog.rows.length + dialog.cancelled.length,
    ).toBe(
      plannedLineCount([contribution], 'so-a', draft, blocked, { unadoptedSalesOrderIds: unadopted }),
    );
    expect(dialog.rows.length + dialog.cancelled.length).toBe(entry.counted);
    // A line the press leaves out is named, never silent.
    if (!entry.listed && !entry.counted) expect(dialog.notPosted.length).toBe(1);
  });

  it('confirmSummaryFor admits an order whose only posted line is a covered approved one', () => {
    const contribution = { ...base, ...covered } as BoardContribution;
    const summary = confirmSummaryFor([contribution], { [contribution.key]: { verdict: 'approved' } });
    expect(summary.toConfirm).toBe(1);
    expect(summary.orderCount).toBe(1);
  });

  it('an unticked covered-rejected line leaves the withdrawal out', () => {
    const contribution = { ...base, ...covered } as BoardContribution;
    const draft = { [contribution.key]: { verdict: 'rejected' as const, reason: 'No.' } };
    expect(
      rejectedCoveredLineIdsFor([contribution], 'so-a', draft, {
        excludeKeys: new Set([contribution.key]),
      }),
    ).toEqual([]);
  });

  it('flags nobody as somebody else when the current user is unknown', () => {
    const contribution = {
      ...base,
      draft: { decision: { verdict: 'approved' }, saved_by: 'Jayson', saved_at: '2026-09-29T00:00:00Z' },
    } as BoardContribution;
    const { rows } = confirmDialogRowsFor(
      [contribution],
      { [contribution.key]: { verdict: 'approved' } },
      { currentUserName: '', openedAt: OPENED },
    );
    expect(rows[0].savedByOther).toBe(false);
  });
});

describe('confirm scope: a naive-UTC saved_at is read as UTC', () => {
  afterEach(() => vi.useRealTimers());

  it('a draft saved a moment ago reads "just now" and not before the board opened', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-30T04:50:40Z'));
    const board = buildBoard(
      [line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1 })],
      { today: TODAY },
    );
    const base = board.cells.flatMap((cell) => cell.contributions)[0];
    const contribution = {
      ...base,
      draft: { decision: { verdict: 'approved' }, saved_by: 'Jayson', saved_at: '2026-09-30T04:50:00' },
    } as BoardContribution;
    const { rows } = confirmDialogRowsFor(
      [contribution],
      { [contribution.key]: { verdict: 'approved' } },
      { currentUserName: 'Cyndi', openedAt: new Date('2026-09-30T04:52:00Z') },
    );
    expect(rows[0].savedBefore).toBe(true);
    expect(savedAgoText('2026-09-30T04:50:00')).toBe('just now');
  });
});

/**
 * Saved | Others is Confirm's own posting set: `pressPostsContribution` is true exactly for the
 * lines `confirmLinesFor` posts and the covered rejections `rejectedCoveredLineIdsFor` withdraws.
 */
describe('pressPostsContribution: Saved is exactly what Confirm posts', () => {
  const board = buildBoard(
    [line({ sales_order_id: 'so-a', so_number: 'SO000001', line_no: 1, qty: '100' })],
    { today: TODAY, freeStock: { 'WESERP10B|BRW-BB': '100' } },
  );
  const base = board.cells.flatMap((cell) => cell.contributions)[0];
  const covered = { covered: true, decision: {} as never };
  const amended = {
    verdict: 'amended' as const,
    reserve_qty: '0',
    reserve: [],
    borrow: [],
    buy_qty: '100',
    reason: 'Late.',
  };

  type Case = {
    name: string;
    contribution: Partial<BoardContribution>;
    decision?: { verdict: 'approved' | 'amended' | 'rejected' } & Record<string, unknown>;
    saved: boolean;
  };
  const cases: Case[] = [
    { name: 'saved, uncovered', contribution: {}, decision: { verdict: 'approved' }, saved: true },
    { name: 'covered + amended', contribution: covered, decision: amended, saved: true },
    { name: 'covered + approved', contribution: covered, decision: { verdict: 'approved' }, saved: true },
    { name: 'staged reject on a covered line', contribution: covered, decision: { verdict: 'rejected' }, saved: true },
    {
      name: 'stale draft',
      contribution: { draft: { decision: { verdict: 'approved' }, saved_by: 'x', saved_at: '2026-09-29T00:00:00Z', stale: true } },
      decision: { verdict: 'approved' },
      saved: false,
    },
    { name: 'no mirror on an adopted order', contribution: { project_line_id: null }, decision: { verdict: 'approved' }, saved: false },
    { name: 'unplannable', contribution: { unplannable: true }, decision: { verdict: 'approved' }, saved: false },
    { name: 'suggested (no decision)', contribution: {}, saved: false },
  ];

  it.each(cases)('$name', (entry) => {
    const contribution = { ...base, ...entry.contribution } as BoardContribution;
    const draft = entry.decision ? { [contribution.key]: entry.decision as never } : {};
    const posted = confirmLinesFor([contribution], 'so-a', draft).map((row) => row.project_line_id);
    const withdrawn = rejectedCoveredLineIdsFor([contribution], 'so-a', draft);
    const inConfirm = [...posted, ...withdrawn].includes(contribution.project_line_id ?? '__none__');

    expect(pressPostsContribution(contribution, draft[contribution.key])).toBe(entry.saved);
    expect(pressPostsContribution(contribution, draft[contribution.key])).toBe(inConfirm);
  });

  it('follows the predicate, not the pill: a staged reject reads Rejected yet is in Saved', () => {
    const contribution = { ...base, ...covered } as BoardContribution;
    const decision = { verdict: 'rejected' as const, reason: 'No.' };
    // The old rule (`verdictOf === "saved"`) would have put this line under Others.
    expect(verdictOf(contribution, decision)).toBe('rejected');
    expect(pressPostsContribution(contribution, decision)).toBe(true);
  });

  it('counts a no-mirror line as saved only on an order about to be adopted', () => {
    const contribution = { ...base, project_line_id: null } as BoardContribution;
    const decision = { verdict: 'approved' as const };
    expect(pressPostsContribution(contribution, decision, { unadopted: true })).toBe(true);
    expect(pressPostsContribution(contribution, decision, { unadopted: false })).toBe(false);
  });

  it('a staged reject held back by a pending planning change is not a write', () => {
    const contribution = { ...base, ...covered } as BoardContribution;
    const decision = { verdict: 'rejected' as const, reason: 'No.' };
    expect(pressPostsContribution(contribution, decision, { batchBlocked: true })).toBe(false);
  });
});
