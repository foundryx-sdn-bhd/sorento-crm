import { describe, expect, it } from 'vitest';

import { promotionOptionName } from './line-pricing-types';

describe('promotionOptionName (PRICE-TAG-A3-EXPIRED)', () => {
  it('names a live promotion by its description alone', () => {
    expect(
      promotionOptionName({
        description: 'Sorento A3 Dealer',
      }),
    ).toBe('Sorento A3 Dealer');
    expect(
      promotionOptionName({
        description: 'Sorento A3 Dealer',
        expired_on: null,
      }),
    ).toBe('Sorento A3 Dealer');
  });

  it('labels an ended promotion with the day it ended', () => {
    expect(
      promotionOptionName({
        description: 'Sorento A3 Dealer',
        expired_on: '2026-09-30',
      }),
    ).toBe('Sorento A3 Dealer (expired 30 Sep 2026)');
  });

  it('labels a saved line from its name and end date alone', () => {
    expect(
      promotionOptionName({
        description: 'Sorento A3 Office',
        expired_on: '2026-09-30',
      }),
    ).toBe('Sorento A3 Office (expired 30 Sep 2026)');
  });

  it('does not shift the day in a timezone west of UTC', () => {
    expect(
      promotionOptionName({
        description: 'X',
        expired_on: '2026-01-01',
      }),
    ).toBe('X (expired 1 Jan 2026)');
  });
});
