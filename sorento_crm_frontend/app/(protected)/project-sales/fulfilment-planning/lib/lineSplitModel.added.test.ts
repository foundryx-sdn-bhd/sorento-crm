/**
 * OI-LINE-SPLIT D1: a line AutoCount ADDED in the pending change has base 0 and no implicit
 * own split, so what moves in is all it plans (SO424910: line 16 76 -> 2, lines 32 and 48
 * added with 4 and 10).
 */
import { describe, expect, it } from 'vitest';
import * as m from './lineSplitModel';

type Line = Parameters<typeof m.splitsOf>[0];

const mk = (lineId: string, no: number, baseQty: number, nowQty: number, date: string): Line =>
  ({
    lineId,
    lineNo: no,
    itemCode: 'SRTSP124',
    location: 'BRW-BB',
    baseQty,
    baseDate: baseQty ? date : null,
    nowQty,
    nowDate: date,
    pendingChange: true,
    splits: [],
  }) as unknown as Line;

const sp = (id: string, qty: number, date: string | null, on: string, cancel = false) => ({
  id,
  qty,
  deliveryDate: date,
  isCancel: cancel,
  sitsOnLineId: on,
  remark: '',
});

function so424910(): Line[] {
  const l16 = mk('l16', 16, 76, 2, '2026-10-07');
  l16.splits = [
    sp('a', 2, '2026-10-07', 'l16'),
    sp('b', 4, '2026-10-16', 'l32'),
    sp('c', 10, '2026-11-15', 'l48'),
    sp('d', 60, null, 'l16', true),
  ] as never;
  return [l16, mk('l32', 32, 0, 4, '2026-10-16'), mk('l48', 48, 0, 10, '2026-11-15')];
}

describe('added lines (base 0)', () => {
  it('have no implicit own split and are not split', () => {
    const [, l32] = so424910();
    expect(m.splitsOf(l32)).toEqual([]);
    expect(m.isSplit(l32)).toBe(false);
    expect(m.gapOf(l32)).toBe(0);
  });

  it('plan only what sits on them, and are explained when it equals AutoCount', () => {
    const lines = so424910();
    expect(m.planOf(lines, 'l32')).toBe(4);
    expect(m.leftOf(lines, 'l32')).toBe(0);
    expect(m.planOf(lines, 'l48')).toBe(10);
    expect(m.leftOf(lines, 'l48')).toBe(0);
    expect(m.planOf(lines, 'l16')).toBe(2);
    expect(m.leftOf(lines, 'l16')).toBe(0);
    expect(m.gapOf(lines[0])).toBe(0);
  });

  it('before anything moves in, an added line is left its whole AutoCount qty', () => {
    const lines = [mk('l16', 16, 76, 2, '2026-10-07'), mk('l32', 32, 0, 4, '2026-10-16')];
    expect(m.planOf(lines, 'l32')).toBe(0);
    expect(m.leftOf(lines, 'l32')).toBe(4);
  });

  it('sends [] for an added line and never invents an own split for it', () => {
    const body = m.toPayload(so424910());
    expect(body.lines.find((l: { line_id: string }) => l.line_id === 'l32')!.splits).toEqual([]);
  });
});
