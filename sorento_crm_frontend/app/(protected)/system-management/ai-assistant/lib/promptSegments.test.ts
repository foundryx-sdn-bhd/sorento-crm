import { describe, expect, it } from 'vitest';
import { joinSegments, splitTemplate } from './promptSegments';

const NAMES = ['domains', 'statuses'];

describe('splitTemplate (R5a: registry variables are chips, everything else is text)', () => {
  it('splits registry tokens out of the text and keeps other tokens as text', () => {
    expect(splitTemplate('ONE of: {{domains}} | null. {{current_date}} {{ statuses }}', NAMES)).toEqual([
      { kind: 'text', text: 'ONE of: ' },
      { kind: 'var', name: 'domains', raw: '{{domains}}' },
      { kind: 'text', text: ' | null. {{current_date}} ' },
      { kind: 'var', name: 'statuses', raw: '{{ statuses }}' },
    ]);
  });

  it('round-trips byte for byte', () => {
    const t = 'a{{domains}}{{domains}}b\n{{statuses}}';
    expect(joinSegments(splitTemplate(t, NAMES))).toBe(t);
  });

  it('is plain text when the key has no registry variables', () => {
    expect(splitTemplate('x {{domains}}', [])).toEqual([{ kind: 'text', text: 'x {{domains}}' }]);
  });
});
