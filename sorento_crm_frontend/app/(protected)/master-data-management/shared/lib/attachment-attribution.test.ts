import { describe, it, expect } from 'vitest';
import { attachmentUploaderLabel } from './attachment-attribution';

describe('attachmentUploaderLabel', () => {
  const cases: Array<{
    name: string;
    input: Parameters<typeof attachmentUploaderLabel>;
    expected: string;
  }> = [
    { name: 'contact upload', input: ['CONTACT Y', 'contact'], expected: 'by CONTACT Y (contact)' },
    { name: 'staff upload', input: ['CONTACT X Lee', 'staff'], expected: 'by CONTACT X Lee (staff)' },
    // Unresolved role but a name is present (e.g. legacy row) - render the bare
    // name rather than guessing a role.
    { name: 'name with no role', input: ['Cindy', null], expected: 'Cindy' },
    { name: 'name with undefined role', input: ['Cindy', undefined], expected: 'Cindy' },
    // Missing / blank name always renders the explicit "Unknown" - never a
    // guessed name, never a raw UUID (see UAC group B5).
    { name: 'missing name, contact role', input: [null, 'contact'], expected: 'Unknown' },
    { name: 'missing name, staff role', input: [undefined, 'staff'], expected: 'Unknown' },
    { name: 'empty-string name', input: ['', 'contact'], expected: 'Unknown' },
    { name: 'whitespace-only name', input: ['   ', 'staff'], expected: 'Unknown' },
    { name: 'both missing', input: [null, null], expected: 'Unknown' },
    // Name is trimmed before use.
    { name: 'name with surrounding whitespace', input: ['  CONTACT Y  ', 'contact'], expected: 'by CONTACT Y (contact)' },
  ];

  it.each(cases)('$name -> "$expected"', ({ input, expected }) => {
    expect(attachmentUploaderLabel(...input)).toBe(expected);
  });
});
