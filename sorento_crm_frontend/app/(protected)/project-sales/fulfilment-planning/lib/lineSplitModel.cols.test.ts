/**
 * OI-LINE-SPLIT: the date columns of a group include every line's AutoCount (now) date, so
 * an ADDED line (base 0, no base date) gets its own column (SO424910 shape).
 */
import { describe, expect, it } from 'vitest';
import * as m from './lineSplitModel';

type Line = Parameters<typeof m.splitsOf>[0];

const line = (lineId: string, no: number, base: number, now: number, date: string): Line =>
  ({
    lineId,
    lineNo: no,
    itemCode: 'SRTSP124',
    location: 'BRW-BB',
    baseQty: base,
    baseDate: base ? date : null,
    nowQty: now,
    nowDate: date,
    pendingChange: true,
    splits: [],
  }) as unknown as Line;

describe('datesOf', () => {
  it('includes the AutoCount date of an added line, ascending', () => {
    const lines = [
      line('l16', 16, 76, 2, '2026-10-07'),
      line('l32', 32, 0, 4, '2026-10-16'),
      line('l48', 48, 0, 10, '2026-11-15'),
    ];
    const dates = m.datesOf(m.groupsOf(lines)[0]);
    expect(dates).toEqual(['2026-10-07', '2026-10-16', '2026-11-15']);
  });
});
