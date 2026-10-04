/**
 * "A/C 1 to 4" for a run of three or more, commas otherwise ("A/C 1, 3, 4"); a run inside a
 * gap folds the same way ("A/C 1 to 3, 5"). Empty when no member ledger is numbered.
 */
export function formatAccountLevels(levels: number[] | null | undefined): string {
  const sorted = [...new Set(levels ?? [])].sort((a, b) => a - b);
  if (sorted.length === 0) return '';
  const parts: string[] = [];
  let start = 0;
  for (let i = 1; i <= sorted.length; i++) {
    if (i < sorted.length && sorted[i] === sorted[i - 1] + 1) continue;
    const run = sorted.slice(start, i);
    parts.push(run.length >= 3 ? `${run[0]} to ${run[run.length - 1]}` : run.join(', '));
    start = i;
  }
  return `A/C ${parts.join(', ')}`;
}

export function ledgerCountLabel(count: number): string {
  return `${count} ${count === 1 ? 'ledger' : 'ledgers'}`;
}
