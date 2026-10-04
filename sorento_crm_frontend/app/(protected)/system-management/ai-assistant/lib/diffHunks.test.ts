import { describe, expect, it } from 'vitest';
import { lineDiff } from './lineDiff';
import { collapseUnchanged, diffHunks } from './diffHunks';

const A = ['h1', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'x', 'y', 'z'].join('\n');
const B = ['h1', 'a2', 'b', 'c', 'd', 'e', 'f', 'g', 'x', 'y2', 'z'].join('\n');

describe('diffHunks (R5b: step change by change)', () => {
  it('groups consecutive changed rows into one hunk each', () => {
    const rows = lineDiff(A, B);
    const hunks = diffHunks(rows);
    expect(hunks).toHaveLength(2);
    expect(rows.slice(hunks[0].start, hunks[0].end + 1).map((r) => r.op).sort()).toEqual(['added', 'removed']);
  });

  it('has no hunk for identical texts', () => {
    expect(diffHunks(lineDiff('a\nb', 'a\nb'))).toEqual([]);
  });
});

describe('collapseUnchanged (R5b: changes only, with context)', () => {
  it('keeps 2 context lines around each change and folds the rest into a gap', () => {
    const rows = lineDiff(A, B);
    const items = collapseUnchanged(rows, 2);
    const gaps = items.filter((i) => i.kind === 'gap');
    expect(gaps).toHaveLength(1);
    expect(gaps[0]).toMatchObject({ kind: 'gap', count: 3 }); // d, e, f folded; b, c and g, x stay as context
    const shown = items.filter((i) => i.kind === 'row').length;
    expect(shown + (gaps[0].kind === 'gap' ? gaps[0].count : 0)).toBe(rows.length);
  });

  it('folds nothing when the texts are short', () => {
    const rows = lineDiff('a\nb', 'a\nc');
    expect(collapseUnchanged(rows, 2).every((i) => i.kind === 'row')).toBe(true);
  });
});
