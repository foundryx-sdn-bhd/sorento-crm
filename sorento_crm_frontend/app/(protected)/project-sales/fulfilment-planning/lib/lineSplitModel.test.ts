/**
 * OI-LINE-SPLIT: the pure split model behind the whole-SO split view (UAC A5, A9, B1, B4).
 * Numbers are SO417636 (mock case A): line 20 417 -> 14 + 236 + 167, line 40 504 -> 37 + 467,
 * so line 30's 192 is explained up to 632 and line 40 is left +182.
 *
 * The module is imported inside each test so a missing module fails that test, not the file.
 */
import { describe, expect, it } from 'vitest';

type Model = typeof import('./lineSplitModel');

async function model(): Promise<Model> {
  const path = './lineSplitModel';
  return (await import(/* @vite-ignore */ path)) as Model;
}

type Split = {
  id: string;
  qty: number;
  deliveryDate: string | null;
  isCancel: boolean;
  sitsOnLineId: string | null;
  remark: string;
};
type Line = {
  lineId: string;
  lineNo: number | null;
  itemCode: string;
  location: string;
  baseQty: number;
  baseDate: string | null;
  nowQty: number;
  nowDate: string | null;
  pendingChange: boolean;
  splits: Split[];
};

function line(
  lineId: string,
  no: number,
  baseQty: number,
  baseDate: string,
  nowQty: number,
  nowDate: string,
  item = 'CSK14B',
): Line {
  return {
    lineId,
    lineNo: no,
    itemCode: item,
    location: 'BRW-BB',
    baseQty,
    baseDate,
    nowQty,
    nowDate,
    pendingChange: nowQty !== baseQty || nowDate !== baseDate,
    splits: [],
  };
}

function so417636(): Line[] {
  return [
    line('a1', 10, 301, '2026-08-24', 301, '2026-08-24'),
    line('a2', 20, 417, '2026-10-20', 14, '2026-09-20'),
    line('a3', 30, 192, '2026-11-20', 632, '2026-11-20'),
    line('a4', 40, 504, '2026-12-20', 649, '2027-01-02'),
    line('a5', 50, 335, '2027-01-02', 335, '2027-01-02', 'CSK11A'),
  ];
}

const by = (lines: Line[], id: string) => lines.find((l) => l.lineId === id)!;
const withQty = (lines: Line[], id: string, qty: number) =>
  by(lines, id).splits.find((s) => s.qty === qty)!;

describe('lineSplitModel', () => {
  it('an empty split list reads as one implicit split: the base on its own line and date', async () => {
    const m = await model();
    const a2 = by(so417636(), 'a2');
    expect(m.splitsOf(a2)).toHaveLength(1);
    expect(m.splitsOf(a2)[0]).toMatchObject({
      qty: 417,
      deliveryDate: '2026-10-20',
      isCancel: false,
      sitsOnLineId: 'a2',
      remark: '',
    });
    expect(m.isSplit(a2)).toBe(false);
    expect(m.gapOf(a2)).toBe(0);
  });

  it('one stored split equal to the base is still not split; a remark or a second split is', async () => {
    const m = await model();
    const plain = {
      ...by(so417636(), 'a1'),
      splits: [
        { id: 'x', qty: 301, deliveryDate: '2026-08-24', isCancel: false, sitsOnLineId: 'a1', remark: '' },
      ],
    };
    expect(m.isSplit(plain)).toBe(false);
    expect(m.isSplit({ ...plain, splits: [{ ...plain.splits[0], remark: 'hold' }] })).toBe(true);
    expect(
      m.isSplit({
        ...plain,
        splits: [
          { ...plain.splits[0], qty: 300 },
          { ...plain.splits[0], id: 'y', qty: 1 },
        ],
      }),
    ).toBe(true);
  });

  it('moving part of a split makes a new split for the moved qty and leaves the rest', async () => {
    const m = await model();
    const lines = so417636();
    const own = m.splitsOf(by(lines, 'a2'))[0];
    const next = m.moveSplit(lines, 'a2', own.id, 403, { date: '2026-11-20', sitsOnLineId: 'a3' });
    const a2 = by(next, 'a2');
    expect(a2.splits.map((s) => s.qty).sort((x, y) => x - y)).toEqual([14, 403]);
    expect(withQty(next, 'a2', 403)).toMatchObject({
      deliveryDate: '2026-11-20',
      sitsOnLineId: 'a3',
      isCancel: false,
    });
    expect(withQty(next, 'a2', 14)).toMatchObject({ deliveryDate: '2026-10-20', sitsOnLineId: 'a2' });
    expect(m.gapOf(a2)).toBe(0);
    // pure: the input is untouched
    expect(by(lines, 'a2').splits).toEqual([]);
  });

  it('moving all of a split moves it: no new split, the same qty at the new date', async () => {
    const m = await model();
    const lines = so417636();
    const own = m.splitsOf(by(lines, 'a2'))[0];
    const next = m.moveSplit(lines, 'a2', own.id, 417, { date: '2026-09-20', sitsOnLineId: 'a2' });
    expect(by(next, 'a2').splits).toHaveLength(1);
    expect(by(next, 'a2').splits[0]).toMatchObject({ qty: 417, deliveryDate: '2026-09-20' });
  });

  it('a move onto a cell that already holds a split of the same own line, date and sits-on merges', async () => {
    const m = await model();
    let lines = so417636();
    const own = m.splitsOf(by(lines, 'a2'))[0];
    lines = m.moveSplit(lines, 'a2', own.id, 403, { date: '2026-11-20', sitsOnLineId: 'a3' });
    const big = withQty(lines, 'a2', 403);
    lines = m.moveSplit(lines, 'a2', big.id, 167, { date: '2029-11-20', sitsOnLineId: 'a3' });
    expect(by(lines, 'a2').splits).toHaveLength(3);
    const far = withQty(lines, 'a2', 167);
    // bring the 167 back to 20 Nov on line 30: it joins the 236 already there
    lines = m.moveSplit(lines, 'a2', far.id, 167, { date: '2026-11-20', sitsOnLineId: 'a3' });
    expect(by(lines, 'a2').splits).toHaveLength(2);
    expect(withQty(lines, 'a2', 403)).toMatchObject({ deliveryDate: '2026-11-20', sitsOnLineId: 'a3' });
    expect(m.gapOf(by(lines, 'a2'))).toBe(0);
  });

  it('the same date on a different sits-on line does not merge', async () => {
    const m = await model();
    let lines = so417636();
    const own = m.splitsOf(by(lines, 'a2'))[0];
    lines = m.moveSplit(lines, 'a2', own.id, 100, { date: '2026-11-20', sitsOnLineId: 'a3' });
    const rest = withQty(lines, 'a2', 317);
    lines = m.moveSplit(lines, 'a2', rest.id, 50, { date: '2026-11-20', sitsOnLineId: 'a4' });
    expect(by(lines, 'a2').splits.map((s) => s.qty).sort((x, y) => x - y)).toEqual([50, 100, 267]);
  });

  it('cancel makes a cancel split: no date, sitting on its own line', async () => {
    const m = await model();
    const lines = so417636();
    const own = m.splitsOf(by(lines, 'a1'))[0];
    const next = m.moveSplit(lines, 'a1', own.id, 100, { date: 'cancel', sitsOnLineId: 'a3' });
    const cancel = withQty(next, 'a1', 100);
    expect(cancel).toMatchObject({ isCancel: true, deliveryDate: null, sitsOnLineId: 'a1' });
    expect(m.gapOf(by(next, 'a1'))).toBe(0);
    // a cancel is not planned on any line
    expect(m.planOf(next, 'a1')).toBe(201);
    expect(m.planOf(next, 'a3')).toBe(192);
  });

  it('splitInTwo halves a split, the first half rounded up', async () => {
    const m = await model();
    const lines = so417636();
    const own = m.splitsOf(by(lines, 'a1'))[0];
    const next = m.splitInTwo(lines, 'a1', own.id);
    expect(by(next, 'a1').splits.map((s) => s.qty)).toEqual([151, 150]);
    expect(by(next, 'a1').splits.every((s) => s.deliveryDate === '2026-08-24')).toBe(true);
    expect(m.gapOf(by(next, 'a1'))).toBe(0);
  });

  it('backToOwn returns a split to its own line and original date, merging with what is there', async () => {
    const m = await model();
    let lines = so417636();
    const own = m.splitsOf(by(lines, 'a2'))[0];
    lines = m.moveSplit(lines, 'a2', own.id, 200, { date: '2026-11-20', sitsOnLineId: 'a3' });
    const moved = withQty(lines, 'a2', 200);
    lines = m.backToOwn(lines, 'a2', moved.id);
    expect(by(lines, 'a2').splits).toHaveLength(1);
    expect(by(lines, 'a2').splits[0]).toMatchObject({
      qty: 417,
      deliveryDate: '2026-10-20',
      sitsOnLineId: 'a2',
    });
    expect(m.isSplit(by(lines, 'a2'))).toBe(false);
  });

  it('SO417636: line 30 is explained to 632 and line 40 is left +182', async () => {
    const m = await model();
    let lines = so417636();

    const own2 = m.splitsOf(by(lines, 'a2'))[0];
    lines = m.moveSplit(lines, 'a2', own2.id, 403, { date: '2026-11-20', sitsOnLineId: 'a3' });
    lines = m.moveSplit(lines, 'a2', withQty(lines, 'a2', 14).id, 14, {
      date: '2026-09-20',
      sitsOnLineId: 'a2',
    });
    lines = m.moveSplit(lines, 'a2', withQty(lines, 'a2', 403).id, 167, {
      date: '2029-11-20',
      sitsOnLineId: 'a3',
    });

    const own4 = m.splitsOf(by(lines, 'a4'))[0];
    lines = m.moveSplit(lines, 'a4', own4.id, 37, { date: '2026-11-20', sitsOnLineId: 'a3' });
    lines = m.moveSplit(lines, 'a4', withQty(lines, 'a4', 467).id, 467, {
      date: '2027-01-02',
      sitsOnLineId: 'a4',
    });

    expect(by(lines, 'a2').splits.map((s) => s.qty).sort((x, y) => x - y)).toEqual([14, 167, 236]);
    for (const id of ['a1', 'a2', 'a3', 'a4', 'a5']) expect(m.gapOf(by(lines, id))).toBe(0);

    expect(m.planOf(lines, 'a3')).toBe(632);
    expect(m.leftOf(lines, 'a3')).toBe(0);
    expect(m.planOf(lines, 'a4')).toBe(467);
    expect(m.leftOf(lines, 'a4')).toBe(182);
    expect(m.planOf(lines, 'a2')).toBe(14);
    expect(m.leftOf(lines, 'a2')).toBe(0);
    expect(m.planOf(lines, 'a1')).toBe(301);
    expect(m.isSplit(by(lines, 'a2'))).toBe(true);
    expect(m.isSplit(by(lines, 'a3'))).toBe(false);
  });

  it('gapOf is base minus the sum: positive is a gap, negative is over', async () => {
    const m = await model();
    const a1 = by(so417636(), 'a1');
    const s = (id: string, qty: number): Split => ({
      id,
      qty,
      deliveryDate: '2026-08-24',
      isCancel: false,
      sitsOnLineId: 'a1',
      remark: '',
    });
    expect(m.gapOf({ ...a1, splits: [s('p', 280), s('q', 1)] })).toBe(20);
    expect(m.gapOf({ ...a1, splits: [s('p', 280), s('q', 41)] })).toBe(-20);
  });

  it('toPayload sends every line, [] for an unsplit line and the contract keys for a split one', async () => {
    const m = await model();
    let lines = so417636();
    const own1 = m.splitsOf(by(lines, 'a1'))[0];
    lines = m.moveSplit(lines, 'a1', own1.id, 100, { date: 'cancel', sitsOnLineId: null });
    const own2 = m.splitsOf(by(lines, 'a2'))[0];
    lines = m.moveSplit(lines, 'a2', own2.id, 403, { date: '2026-11-20', sitsOnLineId: 'a3' });

    const payload = m.toPayload(lines);
    expect(payload.lines.map((l: { line_id: string }) => l.line_id)).toEqual([
      'a1', 'a2', 'a3', 'a4', 'a5',
    ]);
    expect(payload.lines.find((l: { line_id: string }) => l.line_id === 'a3')!.splits).toEqual([]);
    expect(payload.lines.find((l: { line_id: string }) => l.line_id === 'a5')!.splits).toEqual([]);
    const a1 = payload.lines.find((l: { line_id: string }) => l.line_id === 'a1')!;
    expect(a1.splits).toContainEqual({
      qty: 100,
      delivery_date: null,
      is_cancel: true,
      sits_on_line_id: 'a1',
      remark: '',
    });
    const a2 = payload.lines.find((l: { line_id: string }) => l.line_id === 'a2')!;
    expect(a2.splits).toContainEqual({
      qty: 403,
      delivery_date: '2026-11-20',
      is_cancel: false,
      sits_on_line_id: 'a3',
      remark: '',
    });
    expect(Object.keys(a2.splits[0]).sort()).toEqual([
      'delivery_date', 'is_cancel', 'qty', 'remark', 'sits_on_line_id',
    ]);
  });

  it('a line put back to one plain split on its own line sends [] (unsplit)', async () => {
    const m = await model();
    let lines = so417636();
    const own = m.splitsOf(by(lines, 'a1'))[0];
    lines = m.splitInTwo(lines, 'a1', own.id);
    lines = m.moveSplit(lines, 'a1', by(lines, 'a1').splits[1].id, 150, {
      date: '2026-08-24',
      sitsOnLineId: 'a1',
    });
    expect(by(lines, 'a1').splits).toHaveLength(1);
    expect(m.toPayload(lines).lines.find((l: { line_id: string }) => l.line_id === 'a1')!.splits).toEqual([]);
  });

  it('toPayload drops a zero-qty split', async () => {
    const m = await model();
    const a1 = by(so417636(), 'a1');
    const mk = (id: string, qty: number): Split => ({
      id,
      qty,
      deliveryDate: '2026-09-01',
      isCancel: false,
      sitsOnLineId: 'a1',
      remark: '',
    });
    const lines = [{ ...a1, splits: [mk('p', 301), mk('q', 0)] }];
    const sent = m.toPayload(lines).lines[0].splits;
    expect(sent).toHaveLength(1);
    expect(sent[0].qty).toBe(301);
  });
});
