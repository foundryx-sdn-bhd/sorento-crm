/**
 * OI-LINE-SPLIT v4 model deltas (UAC A2, A4, A5, A7, A9): addSplit fills what is left,
 * placeRest, targetDateOf, setSitsOn taking the target date, sortLines, readsRemain.
 * Numbers are SO417636 CSK14B: line 480 (417 @ 2026-10-20), line 608 (192 @ 2026-11-20).
 */
import { describe, expect, it } from 'vitest';
import * as m from './lineSplitModel';

type Line = Parameters<typeof m.splitsOf>[0];
type Split = Line['splits'][number];

const mk = (
  lineId: string,
  no: number | null,
  baseQty: number,
  baseDate: string | null,
  opts: Partial<Line> = {},
): Line =>
  ({
    lineId,
    lineNo: no,
    itemCode: 'CSK14B',
    location: 'BRW-BB',
    baseQty,
    baseDate,
    nowQty: baseQty,
    nowDate: baseDate,
    pendingChange: false,
    splits: [],
    ...opts,
  }) as Line;

const sp = (
  id: string,
  qty: number,
  date: string | null,
  on: string | null,
  extra: Partial<Split> = {},
): Split => ({
  id,
  qty,
  deliveryDate: date,
  isCancel: false,
  sitsOnLineId: on,
  remark: '',
  ...extra,
});

const by = (lines: Line[], id: string) => lines.find((l) => l.lineId === id)!;

function owner(): Line[] {
  return [
    mk('l480', 480, 417, '2026-10-20'),
    mk('l608', 608, 192, '2026-11-20'),
    mk('l752', 752, 504, '2026-12-20', { nowQty: 649, nowDate: '2027-01-02' }),
  ];
}

describe('addSplit (v4: fills what is left, sits on New line)', () => {
  it('an unsplit line has nothing left: the new split is 0, on New line, at the base date', () => {
    const r = m.addSplit(owner(), 'l480');
    const line = by(r.lines, 'l480');
    const added = line.splits.find((s) => s.id === r.id)!;
    expect(added).toMatchObject({
      qty: 0,
      deliveryDate: '2026-10-20',
      isCancel: false,
      sitsOnLineId: null,
      remark: '',
    });
  });

  it('after the line is cut to 14 the new split takes the 403 left', () => {
    let lines = owner();
    const own = m.splitsOf(by(lines, 'l480'))[0];
    lines = m.setQty(lines, 'l480', own.id, 14);
    const r = m.addSplit(lines, 'l480');
    const added = by(r.lines, 'l480').splits.find((s) => s.id === r.id)!;
    expect(added.qty).toBe(403);
    expect(added.sitsOnLineId).toBeNull();
    expect(added.deliveryDate).toBe('2026-10-20');
    expect(m.gapOf(by(r.lines, 'l480'))).toBe(0);
  });

  it('when the line is already over, the new split is 0 (never negative)', () => {
    let lines = owner();
    const own = m.splitsOf(by(lines, 'l480'))[0];
    lines = m.setQty(lines, 'l480', own.id, 500);
    const r = m.addSplit(lines, 'l480');
    expect(by(r.lines, 'l480').splits.find((s) => s.id === r.id)!.qty).toBe(0);
  });
});

describe('targetDateOf', () => {
  it('is AutoCount now date when there is one, else the base date', () => {
    expect(m.targetDateOf(by(owner(), 'l752'))).toBe('2027-01-02');
    expect(m.targetDateOf(by(owner(), 'l608'))).toBe('2026-11-20');
    expect(m.targetDateOf(mk('x', 1, 5, '2026-01-01', { nowDate: null }))).toBe('2026-01-01');
  });
});

describe('placeRest (the "N left" chip)', () => {
  it('adds a split of what is left at the target: 403 @ 20/11/2026 on line 608', () => {
    let lines = owner();
    const own = m.splitsOf(by(lines, 'l480'))[0];
    lines = m.setQty(lines, 'l480', own.id, 14);
    lines = m.setDate(lines, 'l480', own.id, '2026-09-20');
    const next = m.placeRest(lines, 'l480', { date: '2026-11-20', sitsOnLineId: 'l608' });
    const l480 = by(next, 'l480');
    expect(l480.splits).toHaveLength(2);
    expect(l480.splits.find((s) => s.qty === 403)).toMatchObject({
      deliveryDate: '2026-11-20',
      sitsOnLineId: 'l608',
      isCancel: false,
    });
    expect(m.gapOf(l480)).toBe(0);
    // pure
    expect(by(lines, 'l480').splits).toHaveLength(1);
  });

  it('merges into a split of that line already on the same date and sits-on', () => {
    const l480 = mk('l480', 480, 417, '2026-10-20', {
      splits: [
        sp('a', 14, '2026-09-20', 'l480'),
        sp('b', 236, '2026-11-20', 'l608', { remark: 'x' }),
      ],
    });
    const next = m.placeRest([l480, mk('l608', 608, 192, '2026-11-20')], 'l480', {
      date: '2026-11-20',
      sitsOnLineId: 'l608',
    });
    const splits = by(next, 'l480').splits;
    expect(splits).toHaveLength(2);
    expect(splits.find((s) => s.id === 'b')).toMatchObject({ qty: 403, remark: 'x' });
  });

  it('a cancel target makes a cancel split on its own line with no date', () => {
    const l480 = mk('l480', 480, 417, '2026-10-20', { splits: [sp('a', 17, '2026-10-20', 'l480')] });
    const next = m.placeRest([l480], 'l480', { date: 'cancel', sitsOnLineId: null });
    expect(by(next, 'l480').splits.find((s) => s.qty === 400)).toMatchObject({
      isCancel: true,
      deliveryDate: null,
      sitsOnLineId: 'l480',
    });
  });

  it('a New date target sits on New line with the date given', () => {
    const l480 = mk('l480', 480, 417, '2026-10-20', { splits: [sp('a', 17, '2026-10-20', 'l480')] });
    const next = m.placeRest([l480], 'l480', { date: '2029-11-20', sitsOnLineId: null });
    expect(by(next, 'l480').splits.find((s) => s.qty === 400)).toMatchObject({
      deliveryDate: '2029-11-20',
      sitsOnLineId: null,
    });
  });

  it('changes nothing when nothing is left', () => {
    const lines = owner();
    const next = m.placeRest(lines, 'l480', { date: '2026-11-20', sitsOnLineId: 'l608' });
    expect(by(next, 'l480').splits.length).toBe(by(lines, 'l480').splits.length);
    expect(m.gapOf(by(next, 'l480'))).toBe(0);
  });
});

describe('setSitsOn takes the target line date (AC-A7)', () => {
  const lines = (): Line[] => {
    const l480 = mk('l480', 480, 417, '2026-10-20', {
      splits: [sp('a', 14, '2026-09-20', 'l480'), sp('b', 403, '2026-10-20', 'l480')],
    });
    return [l480, mk('l608', 608, 192, '2026-11-20'), by(owner(), 'l752')];
  };

  it('onto another line takes that line date', () => {
    const next = m.setSitsOn(lines(), 'l480', 'b', 'l608');
    expect(by(next, 'l480').splits.find((s) => s.id === 'b')).toMatchObject({
      sitsOnLineId: 'l608',
      deliveryDate: '2026-11-20',
    });
  });

  it('onto a line AutoCount moved takes AutoCount now date', () => {
    const next = m.setSitsOn(lines(), 'l480', 'b', 'l752');
    expect(by(next, 'l480').splits.find((s) => s.id === 'b')!.deliveryDate).toBe('2027-01-02');
  });

  it('onto its own line or New line keeps the date', () => {
    const own = m.setSitsOn(lines(), 'l480', 'a', 'l480');
    expect(by(own, 'l480').splits.find((s) => s.id === 'a')!.deliveryDate).toBe('2026-09-20');
    const fresh = m.setSitsOn(lines(), 'l480', 'a', null);
    expect(by(fresh, 'l480').splits.find((s) => s.id === 'a')).toMatchObject({
      sitsOnLineId: null,
      deliveryDate: '2026-09-20',
    });
  });

  it('leaving cancel to New line takes the base date', () => {
    const l = mk('l480', 480, 417, '2026-10-20', {
      splits: [
        sp('a', 17, '2026-10-20', 'l480'),
        sp('c', 400, null, 'l480', { isCancel: true }),
      ],
    });
    const next = m.setSitsOn([l], 'l480', 'c', null);
    expect(by(next, 'l480').splits.find((s) => s.id === 'c')).toMatchObject({
      isCancel: false,
      deliveryDate: '2026-10-20',
      sitsOnLineId: null,
    });
  });
});

describe('sortLines (AC-A2)', () => {
  it('orders by item code, location, base date, then line no, without touching the input', () => {
    const input = [
      mk('z', 5, 1, '2026-01-01', { itemCode: 'B' }),
      mk('c', 9, 1, '2026-02-01', { itemCode: 'A', location: 'L2' }),
      mk('b', 8, 1, '2026-03-01', { itemCode: 'A', location: 'L1' }),
      mk('a2', 7, 1, '2026-01-05', { itemCode: 'A', location: 'L1' }),
      mk('a1', 6, 1, '2026-01-05', { itemCode: 'A', location: 'L1' }),
    ];
    const ids = input.map((l) => l.lineId);
    const sorted = m.sortLines(input);
    expect(sorted.map((l) => l.lineId)).toEqual(['a1', 'a2', 'b', 'c', 'z']);
    expect(input.map((l) => l.lineId)).toEqual(ids);
  });

  it('puts a line with no base date (AutoCount added) after dated lines of its item', () => {
    const sorted = m.sortLines([
      mk('add', 32, 0, null, { nowQty: 4, nowDate: '2026-10-16' }),
      mk('old', 16, 76, '2026-10-07'),
    ]);
    expect(sorted.map((l) => l.lineId)).toEqual(['old', 'add']);
  });
});

describe('readsRemain (AC-A4)', () => {
  it('is true when there are no splits', () => {
    expect(m.readsRemain(mk('l', 1, 10, '2026-01-01'))).toBe(true);
  });

  it('is true for one split equal to base on own line and date, with or without a remark', () => {
    const base = mk('l', 1, 10, '2026-01-01');
    expect(m.readsRemain({ ...base, splits: [sp('s', 10, '2026-01-01', 'l')] })).toBe(true);
    expect(
      m.readsRemain({ ...base, splits: [sp('s', 10, '2026-01-01', 'l', { remark: 'hold' })] }),
    ).toBe(true);
  });

  it('is false for a different qty, date, sits-on, a cancel, or a second split', () => {
    const base = mk('l', 1, 10, '2026-01-01');
    expect(m.readsRemain({ ...base, splits: [sp('s', 9, '2026-01-01', 'l')] })).toBe(false);
    expect(m.readsRemain({ ...base, splits: [sp('s', 10, '2026-01-02', 'l')] })).toBe(false);
    expect(m.readsRemain({ ...base, splits: [sp('s', 10, '2026-01-01', null)] })).toBe(false);
    expect(m.readsRemain({ ...base, splits: [sp('s', 10, '2026-01-01', 'other')] })).toBe(false);
    expect(
      m.readsRemain({ ...base, splits: [sp('s', 10, null, 'l', { isCancel: true })] }),
    ).toBe(false);
    expect(
      m.readsRemain({
        ...base,
        splits: [sp('s', 9, '2026-01-01', 'l'), sp('t', 1, '2026-01-01', 'l')],
      }),
    ).toBe(false);
  });
});
