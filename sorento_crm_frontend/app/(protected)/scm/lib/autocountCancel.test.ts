import { describe, expect, it } from 'vitest';

import { cancelSourcesTitle, isPartlyCancelled } from './autocountCancel';

describe('cancelSourcesTitle', () => {
  it('names each document with its qty and how it was matched, in plain words', () => {
    expect(
      cancelSourcesTitle([
        { doc_no: 'XS-1', qty: 36, match_reason: 'line_key' },
        { doc_no: 'XS-2', qty: 4, match_reason: 'item' },
        { doc_no: 'XS-3', qty: null, match_reason: 'group_whole' },
      ]),
    ).toBe('XS-1: 36 (line key)\nXS-2: 4 (item)\nXS-3 (every line of the item)');
  });

  it('never prints an internal code it has no words for', () => {
    // Crew audit: a reason the screen does not know (an old row, a new backend value) shows
    // the document and qty only, never the code.
    expect(cancelSourcesTitle([{ doc_no: 'XS-9', qty: 2, match_reason: 'tie_break' }])).toBe('XS-9: 2');
    expect(cancelSourcesTitle([{ doc_no: 'XS-9', qty: null, match_reason: 'some_new_code' }])).toBe('XS-9');
  });

  it('reads nothing when no document cancelled the line', () => {
    expect(cancelSourcesTitle([])).toBeUndefined();
    expect(cancelSourcesTitle(null)).toBeUndefined();
  });
});

describe('isPartlyCancelled', () => {
  it('is true only while the line is open', () => {
    expect(isPartlyCancelled({ qty_cancelled: 4, line_status: 'open' })).toBe(true);
    expect(isPartlyCancelled({ qty_cancelled: 4, line_status: 'closed' })).toBe(false);
    expect(isPartlyCancelled({ qty_cancelled: 36, line_status: 'cancelled' })).toBe(false);
    expect(isPartlyCancelled({ qty_cancelled: 0, line_status: 'open' })).toBe(false);
  });
});
