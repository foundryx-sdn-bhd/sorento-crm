/**
 * S6 - the planner: one search, List default (R-J, AC-P1/AC-P3).
 *
 * `boardViewFrom` (this file's pure default-view rule) and `contributionMatchesSearch`
 * (the ONE matcher both the grid's row filter and the list view's row filter read, S6)
 * are exercised as pure functions - the whole point of AC-P3 is that one function drives
 * both views, so pinning the function is pinning both at once.
 */
import { describe, expect, it } from 'vitest';
import { boardViewFrom } from './FulfilmentBoardPanel';
import { contributionMatchesSearch } from '../../_shared/lib/fulfilmentBoard';
import type { BoardContribution } from '../../_shared/types/fulfilmentPlanning.types';

function contribution(over: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'c1',
    sales_order_id: 'so-1',
    so_number: 'SO386461',
    customer_name: 'Optad Sdn Bhd',
    agent_code: 'SEAN I',
    agent_label: 'Sean',
    project_label: null,
    line_no: 1,
    item_code: 'SRTWC8605-SC-RL',
    qty: '10',
    ...over,
  } as BoardContribution;
}

describe('boardViewFrom (AC-P1)', () => {
  it('defaults to list when no ?view is in the URL', () => {
    expect(boardViewFrom(null)).toBe('list');
  });

  it('opens grid only for an explicit ?view=grid', () => {
    expect(boardViewFrom('grid')).toBe('grid');
  });

  it('falls back to list for any other value, never erroring on an unknown one', () => {
    expect(boardViewFrom('list')).toBe('list');
    expect(boardViewFrom('whatever')).toBe('list');
    expect(boardViewFrom('')).toBe('list');
  });
});

describe('contributionMatchesSearch (AC-P3): SO number, customer, agent code, item code', () => {
  it('matches the sales order number', () => {
    expect(contributionMatchesSearch(contribution(), 'SO386461')).toBe(true);
    expect(contributionMatchesSearch(contribution(), 'SO999999')).toBe(false);
  });

  it('matches the customer name, case-insensitively', () => {
    expect(contributionMatchesSearch(contribution(), 'optad')).toBe(true);
  });

  it('matches the sales agent code', () => {
    expect(contributionMatchesSearch(contribution({ agent_code: 'CONTACT N' }), 'CONTACT N')).toBe(
      true,
    );
  });

  it('matches the item code', () => {
    expect(contributionMatchesSearch(contribution(), 'SRTWC8605')).toBe(true);
  });

  it('answers true for a blank or whitespace-only needle - no filter at all', () => {
    expect(contributionMatchesSearch(contribution(), '')).toBe(true);
    expect(contributionMatchesSearch(contribution(), '   ')).toBe(true);
  });

  it('answers false when nothing on the contribution matches', () => {
    expect(contributionMatchesSearch(contribution(), 'zzt-nothing-matches')).toBe(false);
  });
});
