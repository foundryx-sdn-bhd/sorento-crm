/** NC-7 guard: a `completed` form (the label the pill is handed is "Completed") gets a real
 *  class, not the neutral fallback. Likely green already; kept so it stays that way. */
import { describe, expect, it } from 'vitest';
import { statusPillClass } from './status-pill';

describe('statusPillClass completed', () => {
  it('has a non-neutral class for completed in either case', () => {
    expect(statusPillClass('completed')).toContain('emerald');
    expect(statusPillClass('Completed')).toBe(statusPillClass('completed'));
  });
});
