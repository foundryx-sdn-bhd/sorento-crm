/** NUMBER-FIELDS fix round 1 (finding C): a blank qty never feeds a figure as 0. */
import { describe, it, expect } from 'vitest';
import { lineAmount } from './OpportunityLineRow';

describe('lineAmount with a blank or invalid qty', () => {
  it('is null (pending), not 0, when qty is blank and a price is set', () => {
    expect(lineAmount({ qty: '', unitPrice: '10' })).toBeNull();
    expect(lineAmount({ qty: '  ', unitPrice: '10' })).toBeNull();
  });
  it('still computes when qty is valid', () => {
    expect(lineAmount({ qty: '3', unitPrice: '10' })).toBe(30);
  });
});
