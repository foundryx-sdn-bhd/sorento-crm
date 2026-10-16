/**
 * CONTACT-TAGS S4a: the contacts list's Filters popover summary. Pure helpers over the list's URL
 * filter map (backend param names). Assumed module: ./contactFilterSummary exporting
 *   popoverFilterCount(filters: Record<string,string>): number
 *   popoverFilterChips(filters, labels: Partial<Record<string,string>>, onChange: (next) => void)
 *     -> { label: string; onClear: () => void }[]
 * `labels` maps a param to its human value for ids (e.g. { customer_id: 'ACME - Acme' }); fixed
 * vocabularies (region, yes/no, tier) are humanised by the helper. Tag (`tag`) and `tag_settings`
 * are PRIMARY toolbar filters: never counted, never a chip.
 */
import { describe, it, expect, vi } from 'vitest';
import { popoverFilterCount, popoverFilterChips } from './contactFilterSummary';

const ALL_POPOVER = {
  access_type: 'dealer',
  tier: 'dealer',
  cost: 'yes',
  ai: 'no',
  escalation: 'yes',
  packing_list: 'no',
  stock: 'yes',
  region: 'west',
  customer_id: 'cust-1',
  access_differs_from: 'contact-9',
  customers: 'none',
  tags: 'none',
};

describe('popoverFilterCount', () => {
  it('is 0 with no filters', () => {
    expect(popoverFilterCount({})).toBe(0);
  });

  it('counts each popover filter once (12 of them)', () => {
    expect(popoverFilterCount(ALL_POPOVER)).toBe(12);
    expect(popoverFilterCount({ region: 'west', tags: 'none' })).toBe(2);
  });

  it('never counts the primary Tag and Tag settings filters', () => {
    expect(popoverFilterCount({ tag: 't1,t2', tag_settings: 'differs' })).toBe(0);
    expect(popoverFilterCount({ tag: 't1', tag_settings: 'matches', region: 'east' })).toBe(1);
  });
});

describe('popoverFilterChips', () => {
  it('names each active popover filter in human words', () => {
    const chips = popoverFilterChips(
      { region: 'west', tags: 'none', customers: 'none', customer_id: 'cust-1' },
      { customer_id: 'ACME - Acme' },
      vi.fn(),
    );
    const labels = chips.map((c) => c.label);
    expect(labels).toContain('Region: West');
    expect(labels).toContain('No tags');
    expect(labels).toContain('No customers linked');
    expect(labels).toContain('Customer: ACME - Acme');
    expect(chips).toHaveLength(4);
    expect(labels.join(' ')).not.toMatch(/cust-1/);
  });

  it('primary filters produce no chip', () => {
    expect(popoverFilterChips({ tag: 't1', tag_settings: 'differs' }, {}, vi.fn())).toEqual([]);
  });

  it('clearing a chip removes only that filter', () => {
    const onChange = vi.fn();
    const filters = { region: 'west', tags: 'none', tag: 't1', tag_settings: 'differs' };
    const chips = popoverFilterChips(filters, {}, onChange);
    chips.find((c) => c.label === 'Region: West')!.onClear();
    expect(onChange).toHaveBeenCalledWith({ tags: 'none', tag: 't1', tag_settings: 'differs' });
    expect(filters.region).toBe('west'); // input not mutated
  });
});
