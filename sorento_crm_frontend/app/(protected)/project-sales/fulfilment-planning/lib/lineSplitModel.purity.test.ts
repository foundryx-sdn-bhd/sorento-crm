/**
 * OI-LINE-SPLIT: the split model is pure (review round 1). React StrictMode runs a state
 * updater twice, so every function must leave its input untouched and must return the SAME
 * answer when called twice on the same input (ids included).
 */
import { describe, expect, it } from 'vitest';
import * as m from './lineSplitModel';

type Line = Parameters<typeof m.splitsOf>[0];

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    Object.values(value as object).forEach((v) => deepFreeze(v));
  }
  return value;
}

function fixture(): Line[] {
  const base = {
    itemCode: 'CSK14B',
    location: 'BRW-BB',
    pendingChange: false,
    nowDate: null,
  };
  const sp = (id: string, qty: number, date: string | null, on: string | null, cancel = false) => ({
    id,
    qty,
    deliveryDate: date,
    isCancel: cancel,
    sitsOnLineId: on,
    remark: '',
  });
  return [
    { ...base, lineId: 'a1', lineNo: 10, baseQty: 301, baseDate: '2026-08-24', nowQty: 301, splits: [] },
    {
      ...base,
      lineId: 'a2',
      lineNo: 20,
      baseQty: 417,
      baseDate: '2026-10-20',
      nowQty: 14,
      splits: [sp('s14', 14, '2026-09-20', 'a2'), sp('s403', 403, '2026-11-20', 'a3')],
    },
    { ...base, lineId: 'a3', lineNo: 30, baseQty: 192, baseDate: '2026-11-20', nowQty: 632, splits: [] },
    {
      ...base,
      lineId: 'a4',
      lineNo: 40,
      baseQty: 504,
      baseDate: '2026-12-20',
      nowQty: 649,
      splits: [sp('s504', 504, '2026-12-20', 'a4'), sp('s0', 0, '2026-12-21', 'a4')],
    },
  ] as unknown as Line[];
}

const CALLS: Array<[string, (lines: Line[]) => unknown]> = [
  ['splitsOf', (l) => m.splitsOf(l[1])],
  ['isSplit', (l) => m.isSplit(l[1])],
  ['gapOf', (l) => m.gapOf(l[1])],
  ['planOf', (l) => m.planOf(l, 'a3')],
  ['leftOf', (l) => m.leftOf(l, 'a3')],
  ['changeOf', (l) => m.changeOf(l[1].splits[1], l[1])],
  ['moveSplit part', (l) => m.moveSplit(l, 'a2', 's403', 100, { date: '2027-01-02', sitsOnLineId: 'a4' })],
  ['moveSplit part of an implicit split', (l) =>
    m.moveSplit(l, 'a1', m.implicitSplitId('a1'), 100, { date: '2026-09-01', sitsOnLineId: 'a3' })],
  ['moveSplit all', (l) => m.moveSplit(l, 'a2', 's403', 403, { date: '2026-09-20', sitsOnLineId: 'a2' })],
  ['moveSplit cancel', (l) => m.moveSplit(l, 'a2', 's403', 3, { date: 'cancel', sitsOnLineId: null })],
  ['splitInTwo', (l) => m.splitInTwo(l, 'a2', 's403')],
  ['backToOwn', (l) => m.backToOwn(l, 'a2', 's403')],
  ['setQty', (l) => m.setQty(l, 'a2', 's403', 5)],
  ['setDate', (l) => m.setDate(l, 'a2', 's403', '2026-12-01')],
  ['setSitsOn', (l) => m.setSitsOn(l, 'a2', 's403', 'a4')],
  ['setRemark', (l) => m.setRemark(l, 'a2', 's403', 'note')],
  ['setPair', (l) => m.setPair(l, 'a2', 's14', 's403', 20)],
  ['addSplit', (l) => m.addSplit(l, 'a2')],
  ['dropEmpty', (l) => m.dropEmpty(l, 'a4')],
  ['toPayload', (l) => m.toPayload(l)],
  ['groupsOf', (l) => m.groupsOf(l)],
];

describe('lineSplitModel purity', () => {
  it.each(CALLS)('%s does not mutate its input', (_name, call) => {
    const lines = deepFreeze(fixture());
    const snapshot = JSON.parse(JSON.stringify(lines));
    expect(() => call(lines)).not.toThrow();
    expect(JSON.parse(JSON.stringify(lines))).toEqual(snapshot);
  });

  it.each(CALLS)('%s answers the same when called twice on the same input', (_name, call) => {
    const lines = deepFreeze(fixture());
    expect(call(lines)).toEqual(call(lines));
  });

  it('datesOf does not mutate its input and is stable', () => {
    const lines = deepFreeze(fixture());
    const group = m.groupsOf(lines)[0];
    expect(m.datesOf(group)).toEqual(m.datesOf(group));
  });
});
