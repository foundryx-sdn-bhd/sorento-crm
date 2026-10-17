import { describe, it, expect } from 'vitest';

import { wholeNumberRangeError } from './whole-number-range';
import { numberFieldError } from './number-field';

describe('wholeNumberRangeError', () => {
  it('accepts a whole number inside the bounds', () => {
    expect(wholeNumberRangeError(' 50 ', 0, 100)).toBeUndefined();
    expect(wholeNumberRangeError('0', 0, 100)).toBeUndefined();
    expect(wholeNumberRangeError('100', 0, 100)).toBeUndefined();
  });

  it('refuses out-of-range, fractional, negative and non-numeric input', () => {
    const message = 'Enter a whole number between 1 and 10.';
    expect(wholeNumberRangeError('0', 1, 10)).toBe(message);
    expect(wholeNumberRangeError('11', 1, 10)).toBe(message);
    expect(wholeNumberRangeError('1.5', 1, 10)).toBe(message);
    expect(wholeNumberRangeError('-3', 1, 10)).toBe(message);
    expect(wholeNumberRangeError('abc', 1, 10)).toBe(message);
    expect(wholeNumberRangeError('', 1, 10)).toBe(message);
  });

  it('allows blank only when asked, and says so in the message', () => {
    expect(wholeNumberRangeError('', 1, 10, { allowBlank: true })).toBeUndefined();
    expect(wholeNumberRangeError('   ', 1, 10, { allowBlank: true })).toBeUndefined();
    expect(wholeNumberRangeError('x', 1, 10, { allowBlank: true })).toBe(
      'Enter a whole number between 1 and 10, or leave it blank.',
    );
  });
});

describe('wholeNumberRangeError shares the numberFieldError sentence', () => {
  it('S11: a valid-format value out of range returns exactly numberFieldError output', () => {
    expect(wholeNumberRangeError('11', 1, 10)).toBe(numberFieldError('11', { min: 1, max: 10, integer: true }));
    expect(wholeNumberRangeError('11', 1, 10, { allowBlank: true })).toBe(
      numberFieldError('11', { min: 1, max: 10, integer: true, optional: true }),
    );
  });
});

