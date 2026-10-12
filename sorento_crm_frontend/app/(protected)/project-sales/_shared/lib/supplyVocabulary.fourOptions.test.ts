/**
 * Owner rule Q4(a), 12 Oct 2026 (`PLAN-plan-four-options-12oct.md`): an old frozen order
 * borrow READS "Borrow" everywhere - the line detail included, not only the strip. The
 * stored composition still names its donor; only the word shown changes.
 */
import { describe, expect, it } from 'vitest';
import {
  LABELS,
  SHORT_LABELS,
  describe as describeParts,
  rowOf,
} from './supplyVocabulary';
import type { SupplyPart } from './supplyVocabulary';

const donorNamed: SupplyPart[] = [
  {
    kind: 'borrow',
    rung: 'order_borrow',
    qty: '42',
    location: 'BRW-BB',
    donor_so_number: 'SO383872',
  },
  {
    kind: 'borrow',
    rung: 'group_borrow',
    qty: '84',
    location: 'BRW-BB',
    donor_so_number: 'SO383872',
  },
  {
    kind: 'borrow',
    rung: 'pool',
    qty: '3',
    location: 'BRW',
    donor_so_number: 'SO391687',
  },
  {
    kind: 'borrow',
    rung: 'supply_borrow',
    qty: '3',
    location: 'BRW-BB',
    donor_so_number: 'SO400333',
  },
  {
    kind: 'borrow',
    rung: null,
    qty: '26',
    location: 'WH3',
    donor_so_number: 'SO413742',
  },
];

describe('an old order borrow reads Borrow', () => {
  it('every donor-named borrow is the Borrow kind', () => {
    for (const part of donorNamed) {
      expect(rowOf(part, 'BRW-BB')).toBe('borrow_other');
    }
  });

  it('the line detail words never say another order', () => {
    for (const part of donorNamed) {
      const kind = rowOf(part, 'BRW-BB')!;
      expect(LABELS[kind]).toBe('Borrow');
      expect(SHORT_LABELS[kind]).toBe('Borrow');
    }
    const text = describeParts(donorNamed, 'BRW-BB');
    expect(text).not.toMatch(/another order|incoming|\(order\)/i);
    expect(text).toMatch(/^Borrow/);
  });
});
