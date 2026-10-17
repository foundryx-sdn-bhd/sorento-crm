/** NUMBER-FIELDS round 4 (S3): a quantity box holding bad input ('NaN') is never read as 0 by the Confirm check. */
import { describe, expect, it } from 'vitest';
import { lineBlockers, type DraftLine } from './supplyComposition';

function draft(overrides: Partial<DraftLine> = {}): DraftLine {
  return {
    project_line_id: 'pl-1',
    line_no: 1,
    item_code: 'CB6633',
    open_qty: '10',
    timely_spo_qty: '0',
    reserve: [],
    borrow: [],
    buy_qty: '0',
    buy_reason: '',
    is_discontinued: false,
    order_back: false,
    cited_document: '',
    ...overrides,
  };
}

const reserve = (qty: string, key: string, warehouse = 'w1') => ({
  key,
  location: key,
  warehouse_id: warehouse,
  qty,
  reason: 'Free stock covers it.',
});

describe('lineBlockers with bad input (S3)', () => {
  it('a reserve row holding NaN blocks even when the other rows already add up', () => {
    const blockers = lineBlockers(
      draft({ reserve: [reserve('10', 'BRW'), reserve('NaN', 'BRW-AM', 'w2')] }),
    );
    expect(blockers.length).toBeGreaterThan(0);
    expect(blockers.join(' ')).toMatch(/Line 1/);
  });

  it('a buy box holding NaN blocks even when the reserve already covers the line', () => {
    const blockers = lineBlockers(draft({ reserve: [reserve('10', 'BRW')], buy_qty: 'NaN' }));
    expect(blockers.length).toBeGreaterThan(0);
  });

  it('a borrow row holding NaN blocks even when the reserve already covers the line', () => {
    const blockers = lineBlockers(
      draft({
        reserve: [reserve('10', 'BRW')],
        borrow: [
          {
            key: 'b1',
            source: 'other_location' as const,
            warehouse_code: 'HQ',
            warehouse_id: 'w3',
            qty: 'NaN',
            reason: 'Needed',
            donor_impact: { free_before: '80', free_after_full_borrow: '0', committed_qty: '140' },
          },
        ],
      }),
    );
    expect(blockers.length).toBeGreaterThan(0);
  });

  it('a blank box is still none, not a blocker', () => {
    expect(lineBlockers(draft({ reserve: [reserve('10', 'BRW'), reserve('', 'BRW-AM', 'w2')] }))).toEqual([]);
  });
});
