import { describe, it, expect } from 'vitest';
import { numberFieldError, parseNumberDraft } from './number-field';

describe('parseNumberDraft', () => {
  it.each([
    ['', null],
    ['   ', null],
    ['-', null],
    ['.', null],
    [null, null],
    [undefined, null],
    [NaN, null],
    [Infinity, null],
    ['abc', null],
    ['2', 2],
    [' 7 ', 7],
    ['0', 0],
    ['2.5', 2.5],
    ['-3', -3],
    [4, 4],
    [0, 0],
  ])('parses %j to %j', (raw, expected) => {
    expect(parseNumberDraft(raw as string | number | null | undefined)).toBe(expected);
  });
});

describe('numberFieldError', () => {
  it('names min and max', () => {
    expect(numberFieldError('', { min: 1, max: 212, integer: true })).toBe(
      'Enter a whole number between 1 and 212.',
    );
    expect(numberFieldError('', { min: 0, max: 100 })).toBe('Enter a number between 0 and 100.');
  });
  it('names min only', () => {
    expect(numberFieldError('', { min: 1, integer: true })).toBe(
      'Enter a whole number of at least 1.',
    );
    expect(numberFieldError('', { min: 0 })).toBe('Enter a number of at least 0.');
  });
  it('names max only', () => {
    expect(numberFieldError('', { max: 212, integer: true })).toBe(
      'Enter a whole number up to 212.',
    );
    expect(numberFieldError('', { max: 100 })).toBe('Enter a number up to 100.');
  });
  it('names nothing when there are no limits', () => {
    expect(numberFieldError('', { integer: true })).toBe('Enter a whole number.');
    expect(numberFieldError('')).toBe('Enter a number.');
    expect(numberFieldError(null, {})).toBe('Enter a number.');
  });
  it('returns the same sentence for every kind of failure', () => {
    const rules = { min: 1, max: 212, integer: true };
    const sentence = 'Enter a whole number between 1 and 212.';
    expect(numberFieldError('-', rules)).toBe(sentence);
    expect(numberFieldError('abc', rules)).toBe(sentence);
    expect(numberFieldError('0', rules)).toBe(sentence);
    expect(numberFieldError('300', rules)).toBe(sentence);
    expect(numberFieldError('2.5', rules)).toBe(sentence);
  });
  it('accepts valid values, bounds included', () => {
    const rules = { min: 1, max: 212, integer: true };
    expect(numberFieldError('1', rules)).toBeUndefined();
    expect(numberFieldError('212', rules)).toBeUndefined();
    expect(numberFieldError(4, rules)).toBeUndefined();
    expect(numberFieldError('2.5', { min: 0, max: 10 })).toBeUndefined();
    expect(numberFieldError('0', { min: 0 })).toBeUndefined();
  });
  it('rejects a fraction only when integer is set', () => {
    expect(numberFieldError('2.5', { integer: true })).toBe('Enter a whole number.');
    expect(numberFieldError('2.5', {})).toBeUndefined();
  });
  it('rejects below min and above max', () => {
    expect(numberFieldError('-1', { min: 0 })).toBe('Enter a number of at least 0.');
    expect(numberFieldError('101', { max: 100 })).toBe('Enter a number up to 100.');
  });
  it('optional: empty passes, other failures end with the blank clause', () => {
    const rules = { min: 0, max: 100000, integer: true, optional: true };
    expect(numberFieldError('', rules)).toBeUndefined();
    expect(numberFieldError('  ', rules)).toBeUndefined();
    expect(numberFieldError(null, rules)).toBeUndefined();
    expect(numberFieldError('-5', rules)).toBe(
      'Enter a whole number between 0 and 100000, or leave it blank.',
    );
    expect(numberFieldError('7', rules)).toBeUndefined();
    expect(numberFieldError('x', { optional: true })).toBe('Enter a number, or leave it blank.');
  });
});

describe('numberFieldError round 1b', () => {
  it('S3: NaN (browser bad input) is invalid, never "no value", even when optional', () => {
    expect(numberFieldError(NaN, { optional: true })).toBe('Enter a number, or leave it blank.');
    expect(numberFieldError(NaN, {})).toBe('Enter a number.');
  });
  it('S4: minExclusive says "above"', () => {
    expect(numberFieldError(0, { min: 0, minExclusive: true })).toBe('Enter a number above 0.');
    expect(numberFieldError('0', { min: 0, minExclusive: true, integer: true })).toBe(
      'Enter a whole number above 0.',
    );
    expect(numberFieldError('150', { min: 0, max: 100, minExclusive: true })).toBe(
      'Enter a number above 0 and up to 100.',
    );
    expect(numberFieldError('0.5', { min: 0, minExclusive: true })).toBeUndefined();
  });
});


describe('numberFieldError round 2 (S-B)', () => {
  it("the string 'NaN' (a screen storing bad input) is invalid even when optional", () => {
    expect(numberFieldError('NaN', { optional: true })).toBe('Enter a number, or leave it blank.');
    expect(numberFieldError('NaN', { min: 0 })).toBe('Enter a number of at least 0.');
  });
});
