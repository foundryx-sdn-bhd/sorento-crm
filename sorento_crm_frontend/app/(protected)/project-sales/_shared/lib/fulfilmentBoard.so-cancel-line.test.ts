/**
 * SO-CANCEL-LINE (AC-F3 amend wording, AC-F4, AC-F5): a `cancelled` draft verdict is carried by
 * the press on its OWN body channel (`cancelled_lines`), never in `lines`.
 *
 * `cancelledLinesFor(contributions, salesOrderId, draft, options)` is the contract name the
 * coder implements (twin of `rejectedCoveredLineIdsFor`); it returns the body entries
 * `[{ project_line_id, reason }]`.
 */
import { describe, expect, it } from 'vitest';
import type { BoardContribution, BoardDraft } from '../types/fulfilmentPlanning.types';
import * as board from './fulfilmentBoard';
import { amendSummary } from './boardAmend';

const { confirmLinesFor, pressPostsContribution, plannedLineCount, confirmDialogRowsFor } = board;

function row(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'so-1|10|CB110|2026-10-01',
    sales_order_id: 'so-1',
    project_line_id: 'pl-10',
    so_number: 'SO900001',
    line_no: 10,
    item_code: 'CB110',
    qty: '36',
    unplannable: false,
    cancelled: false,
    rank_score: 0,
    rank_factors: [],
    sources: [],
    contested: false,
    covered: false,
    decision: null,
    locations: [],
    ...overrides,
  } as BoardContribution;
}

const CANCEL = {
  verdict: 'cancelled',
  reason: 'Customer cancelled on the phone',
  reserve: [],
  borrow: [],
  timely_spo_qty: '0',
  buy_qty: '0',
} as unknown as BoardDraft[string];

describe('cancelled verdict on the press (AC-F5)', () => {
  const contributions = [
    row(),
    row({ key: 'so-1|20|CB110-R|2026-10-01', project_line_id: 'pl-20', line_no: 20 }),
  ];
  const draft: BoardDraft = { [contributions[0].key]: CANCEL };

  it('keeps a cancelled draft out of `lines`', () => {
    expect(confirmLinesFor(contributions, 'so-1', draft)).toEqual([]);
  });

  it('exposes it as cancelled_lines [{project_line_id, reason}]', () => {
    const fn = (board as Record<string, unknown>).cancelledLinesFor as
      | ((c: BoardContribution[], so: string, d: BoardDraft, o?: unknown) => unknown)
      | undefined;
    expect(typeof fn).toBe('function');
    expect(fn!(contributions, 'so-1', draft)).toEqual([
      { project_line_id: 'pl-10', reason: 'Customer cancelled on the phone' },
    ]);
  });

  it('the press counts the saved cancel as something it sends', () => {
    expect(pressPostsContribution(contributions[0], CANCEL)).toBe(true);
  });

  it('leaves out a row the planner unticked in the pre-confirm dialog', () => {
    const fn = (board as Record<string, unknown>).cancelledLinesFor as (
      c: BoardContribution[],
      so: string,
      d: BoardDraft,
      o?: unknown,
    ) => unknown;
    expect(typeof fn).toBe('function');
    expect(fn(contributions, 'so-1', draft, { excludeKeys: new Set([contributions[0].key]) })).toEqual(
      [],
    );
  });
});

describe('amendSummary reads Cancel SO (AC-F4)', () => {
  it('names a cancelled verdict "Cancel SO", not "Amended to nothing"', () => {
    expect(amendSummary(CANCEL)).toBe('Cancel SO');
  });
});

describe('a cancel draft on a covered line is no press at all (AC-F10)', () => {
  const covered = row({ covered: true, decision: { id: 'd-1' } as never });
  const draft: BoardDraft = { [covered.key]: CANCEL };

  it('cancelledLinesFor sends nothing for it, and `lines` does not carry it either', () => {
    expect(board.cancelledLinesFor([covered], 'so-1', draft)).toEqual([]);
    expect(confirmLinesFor([covered], 'so-1', draft)).toEqual([]);
    expect(pressPostsContribution(covered, CANCEL)).toBe(false);
  });
});

describe('a cancel draft under a pending planning change is held, not counted (AC-F11)', () => {
  const contribution = row();
  const draft: BoardDraft = { [contribution.key]: CANCEL };

  it('is not a press and is not counted', () => {
    expect(pressPostsContribution(contribution, CANCEL, { batchBlocked: true })).toBe(false);
    expect(plannedLineCount([contribution], 'so-1', draft, new Set(['so-1']))).toBe(0);
    expect(plannedLineCount([contribution], 'so-1', draft, new Set())).toBe(1);
  });

  it('the dialog names it as not posted rather than listing it', () => {
    const dialog = confirmDialogRowsFor([contribution], draft, {
      currentUserName: 'Cyndi',
      openedAt: new Date('2026-10-01T00:00:00Z'),
      batchBlockedSalesOrderIds: new Set(['so-1']),
    });
    expect(dialog.rows).toHaveLength(0);
    expect(dialog.notPosted).toHaveLength(1);
  });
});

describe('committed Cancel SO line (AC-F16)', () => {
  const committed = row({ cancelled: true, cancelled_at: '2026-10-01T09:00:00' } as never);
  const bookCancelled = row({ cancelled: true });
  const ctx = { currentUserName: 'Cyndi', openedAt: new Date('2026-10-01T00:00:00Z') };

  it('is not counted as planned (a)', () => {
    expect(plannedLineCount([committed], 'so-1', {})).toBe(0);
  });

  it('is not a press (b)', () => {
    expect(pressPostsContribution(committed, undefined)).toBe(false);
  });

  it('is in neither lines nor cancelled_lines (c)', () => {
    expect(confirmLinesFor([committed], 'so-1', {})).toEqual([]);
    expect(board.cancelledLinesFor([committed], 'so-1', {})).toEqual([]);
  });

  it('is in no bucket of the confirm dialog (d)', () => {
    const dialog = confirmDialogRowsFor([committed], {}, ctx);
    expect(dialog.rows).toHaveLength(0);
    expect(dialog.cancelled).toHaveLength(0);
    expect(dialog.notPosted).toHaveLength(0);
  });

  it('does not make its order a confirmable order in the summary', () => {
    const summary = board.confirmSummaryFor([committed], {});
    expect(summary.orderCount).toBe(0);
    expect(summary.toConfirm).toBe(0);
    expect(board.confirmSummaryFor([bookCancelled], {}).orderCount).toBe(1);
  });

  it('control: a book-cancelled line is still retired by the press', () => {
    expect(plannedLineCount([bookCancelled], 'so-1', {})).toBe(1);
    expect(confirmDialogRowsFor([bookCancelled], {}, ctx).cancelled).toHaveLength(1);
  });
});
