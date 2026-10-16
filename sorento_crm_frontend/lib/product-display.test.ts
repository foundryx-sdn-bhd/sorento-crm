import { describe, it, expect } from 'vitest';

import { codeWithName, distinctName, sameDisplayText } from '@/lib/product-display';

// AC-1: the rule table from name-equals-code-acceptance-criteria.md.
const TABLE: Array<[string, string | null | undefined, string | null, string]> = [
  ['11X11', '11X11', null, '11X11'],
  ['11X11', '11x11', null, '11X11'],
  ['1861', ' 1861 ', null, '1861'],
  ['CWC  1009', 'cwc 1009', null, 'CWC  1009'],
  ['2102AW', '', null, '2102AW'],
  ['2102AW', null, null, '2102AW'],
  ['2102AW', undefined, null, '2102AW'],
  ['2102AW', '   ', null, '2102AW'],
  ['SRT6641', 'SRT6641 FLOOR TRAP', 'SRT6641 FLOOR TRAP', 'SRT6641 - SRT6641 FLOOR TRAP'],
];

describe('distinctName / codeWithName (AC-1)', () => {
  it.each(TABLE)('code %j name %j', (code, name, distinct, joined) => {
    expect(distinctName(code, name as string)).toBe(distinct);
    expect(codeWithName(code, name as string, ' - ')).toBe(joined);
  });

  it('defaults the separator to " - "', () => {
    expect(codeWithName('SRT6641', 'FLOOR TRAP')).toBe('SRT6641 - FLOOR TRAP');
  });

  it('honours a custom separator', () => {
    expect(codeWithName('SRT6641', 'FLOOR TRAP', ' · ')).toBe('SRT6641 · FLOOR TRAP');
    expect(codeWithName('1861', '1861', ' · ')).toBe('1861');
  });
});

describe('sameDisplayText', () => {
  it('ignores case, outer and inner whitespace', () => {
    expect(sameDisplayText('11X11', '11x11')).toBe(true);
    expect(sameDisplayText('1861', ' 1861 ')).toBe(true);
    expect(sameDisplayText('CWC  1009', 'cwc 1009')).toBe(true);
  });

  it('is false for different text', () => {
    expect(sameDisplayText('SRT6641', 'SRT6641 FLOOR TRAP')).toBe(false);
  });
});
