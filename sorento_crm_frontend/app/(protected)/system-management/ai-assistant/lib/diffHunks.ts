/**
 * Change navigation over a `lineDiff` result (PLAN-prompt-dynamic-30sep R5b): the hunks to
 * step through, and the "changes only" view that folds unchanged runs.
 */
import type { DiffRow } from './lineDiff';

export interface DiffHunk {
  /** Inclusive row indices. */
  start: number;
  end: number;
}

export function diffHunks(rows: DiffRow[]): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  rows.forEach((row, i) => {
    if (row.op === 'equal') return;
    const last = hunks[hunks.length - 1];
    if (last && last.end === i - 1) last.end = i;
    else hunks.push({ start: i, end: i });
  });
  return hunks;
}

export type CollapsedItem =
  | { kind: 'row'; index: number }
  | { kind: 'gap'; from: number; to: number; count: number };

/** Every changed row plus `context` equal rows on each side; the rest folds into gaps. */
export function collapseUnchanged(rows: DiffRow[], context = 2): CollapsedItem[] {
  const keep = new Array(rows.length).fill(false);
  rows.forEach((row, i) => {
    if (row.op === 'equal') return;
    for (let k = Math.max(0, i - context); k <= Math.min(rows.length - 1, i + context); k++) keep[k] = true;
  });
  const out: CollapsedItem[] = [];
  let i = 0;
  while (i < rows.length) {
    if (keep[i]) {
      out.push({ kind: 'row', index: i });
      i++;
      continue;
    }
    let j = i;
    while (j < rows.length && !keep[j]) j++;
    out.push({ kind: 'gap', from: i, to: j - 1, count: j - i });
    i = j;
  }
  return out;
}
