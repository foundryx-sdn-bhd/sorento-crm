import { describe, expect, it } from 'vitest';
import { formatUsd } from './formatUsd';

const DASH = '-';

describe('formatUsd (UC-3, UC-4, UC-5)', () => {
  it('rounds to 4 decimals with a dollar sign', () => {
    expect(formatUsd(0.00482715)).toBe('$0.0048');
    expect(formatUsd(1.23456)).toBe('$1.2346');
    expect(formatUsd(2.5)).toBe('$2.5000');
  });

  it('keeps a real zero as $0.0000', () => {
    expect(formatUsd(0)).toBe('$0.0000');
  });

  it('shows a dash for a missing cost, never zero', () => {
    expect(formatUsd(null)).toBe(DASH);
    expect(formatUsd(undefined)).toBe(DASH);
  });
});
