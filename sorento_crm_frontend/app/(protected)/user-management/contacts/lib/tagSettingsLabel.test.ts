/**
 * CONTACT-TAGS review N3: the toolbar export writes human values for the Tag settings column.
 * Assumed: ./tagSettingsLabel exports tagSettingsLabel(ts?) used by the column's accessorFn.
 */
import { describe, it, expect } from 'vitest';
import { tagSettingsLabel } from './tagSettingsLabel';

describe('tagSettingsLabel', () => {
  it('Matches / Differs: n / dash', () => {
    expect(tagSettingsLabel({ status: 'matches', differ_count: 0, differ_labels: [] })).toBe('Matches');
    expect(tagSettingsLabel({ status: 'differs', differ_count: 2, differ_labels: ['A', 'B'] })).toBe('Differs: 2');
    expect(tagSettingsLabel({ status: 'none', differ_count: 0, differ_labels: [] })).toBe('-');
    expect(tagSettingsLabel(undefined)).toBe('-');
  });
});
