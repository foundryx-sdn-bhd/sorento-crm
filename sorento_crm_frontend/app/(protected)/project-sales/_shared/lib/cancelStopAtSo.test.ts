/**
 * CANCEL-STOP-AT-SO (owner, 14 Oct 23:20): "when SO is cancelled, we trigger the transfer to
 * cancel SO action in fulfilment planning and let the CS confirm themselves". A line an
 * AutoCount Cancel SO cancelled, with order inquiry rows still on it, carries the sync's saved
 * Transfer to Cancel SO; Confirm posts it in `cancelled_lines` like any board transfer, and it
 * is the only thing such a line can be decided as.
 */
import { describe, expect, it } from 'vitest';
import { awaitsCancelSo, canDecide, decideComposition } from './boardAmend';
import { cancelledLinesFor, confirmDialogRowsFor, confirmSummaryFor } from './fulfilmentBoard';
import { verdictOf } from '../../fulfilment-planning/components/BoardDecisionPill';
import type { BoardContribution, BoardDraft } from '../types/fulfilmentPlanning.types';

const KEY = 'so-a|2|CB1500SS|no_date';
const REASON = 'AutoCount Cancel SO XS001';

function line(overrides: Record<string, unknown> = {}): BoardContribution {
  return {
    key: KEY,
    sales_order_id: 'so-a',
    so_number: 'SO422005',
    line_no: 2,
    item_code: 'CB1500SS',
    qty: '0',
    qty_delivered: '0',
    project_line_id: 'pl-2',
    unplannable: false,
    sources: [],
    contested: false,
    rank_score: 0,
    rank_factors: [],
    covered: false,
    decision: null,
    cancelled: true,
    cancelled_at: '2026-10-14T11:55:00',
    cancelled_by: 'AutoCount',
    cancel_reason: 'XS001 · 1 open order inquiry row',
    cancel_open_oi_rows: 1,
    ...overrides,
  } as BoardContribution;
}

const pending = { verdict: 'cancelled', reason: REASON } as never;

describe('a line AutoCount cancelled with order inquiry rows on it', () => {
  it('awaits Transfer to Cancel SO, and the Decide strip may tick it', () => {
    expect(awaitsCancelSo(line())).toBe(true);
    expect(canDecide(line())).toBe(true);
  });

  it('is decided as Transfer to Cancel SO only', () => {
    expect(decideComposition(line(), 'cancel_so')).toMatchObject({ buy_qty: '0' });
    expect(decideComposition(line(), 'buy')).toEqual({
      skip: 'cancelled in AutoCount, transfer it to Cancel SO',
    });
  });

  it('with the pending transfer saved reads Saved; dismissed it reads Cancelled', () => {
    expect(verdictOf(line(), pending)).toBe('saved');
    expect(verdictOf(line({ draft: { decision: pending } }), null)).toBe('saved');
    expect(verdictOf(line(), null)).toBe('cancelled');
  });

  it('Confirm posts the pending transfer in cancelled_lines and counts it', () => {
    const draft: BoardDraft = { [KEY]: pending };
    expect(cancelledLinesFor([line()], 'so-a', draft)).toEqual([
      { project_line_id: 'pl-2', reason: REASON },
    ]);
    expect(confirmSummaryFor([line()], draft).toConfirm).toBe(1);
    const dialog = confirmDialogRowsFor([line()], draft, {
      currentUserName: 'Siti',
      openedAt: new Date('2026-10-14T12:00:00Z'),
    });
    expect(dialog.rows.map((r) => r.composition)).toEqual(['Cancel SO']);
    expect(dialog.cancelled).toEqual([]);
  });

  it('dismissed, Confirm leaves it alone', () => {
    expect(cancelledLinesFor([line()], 'so-a', {})).toEqual([]);
    expect(confirmSummaryFor([line()], {}).toConfirm).toBe(0);
  });
});

describe('lines this does not touch', () => {
  it('a line CS already transferred stays committed and untickable', () => {
    const transferred = line({ cancelled_by: 'Siti', cancel_open_oi_rows: 0 });
    expect(awaitsCancelSo(transferred)).toBe(false);
    expect(canDecide(transferred)).toBe(false);
    expect(verdictOf(transferred, null)).toBe('confirmed');
    expect(cancelledLinesFor([transferred], 'so-a', { [KEY]: pending })).toEqual([]);
  });

  it('a book-cancelled line with a pending change is still not decidable', () => {
    const book = line({ cancelled_at: null, cancel_open_oi_rows: 0 });
    expect(canDecide(book)).toBe(false);
    expect(decideComposition(book, 'cancel_so')).toEqual({ skip: 'already cancelled' });
  });
});
