import { describe, expect, it } from 'vitest';

import { promotionOptionName } from './line-pricing-types';

describe('promotionOptionName (PRICE-TAG-A3-EXPIRED)', () => {
  it('names a live promotion by its description alone', () => {
    expect(
      promotionOptionName({
        id: 'p1',
        description: 'Sorento A3 Dealer',
        sell_price: 69,
      }),
    ).toBe('Sorento A3 Dealer');
    expect(
      promotionOptionName({
        id: 'p1',
        description: 'Sorento A3 Dealer',
        sell_price: 69,
        expired_on: null,
      }),
    ).toBe('Sorento A3 Dealer');
  });

  it('labels an ended promotion with the day it ended', () => {
    expect(
      promotionOptionName({
        id: 'p1',
        description: 'Sorento A3 Dealer',
        sell_price: 69,
        expired_on: '2026-09-30',
      }),
    ).toBe('Sorento A3 Dealer (expired 30 Sep 2026)');
  });

  it('does not shift the day in a timezone west of UTC', () => {
    expect(
      promotionOptionName({
        id: 'p1',
        description: 'X',
        sell_price: 1,
        expired_on: '2026-01-01',
      }),
    ).toBe('X (expired 1 Jan 2026)');
  });
});
