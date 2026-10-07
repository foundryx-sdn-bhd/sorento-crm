/** SO-CANCEL-LINE (AC-F4, AC-F6): the list row after Confirm - Decided "Cancel SO" and the sentence. */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { BoardContribution } from '../../_shared/types/fulfilmentPlanning.types';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

import { FulfilmentBoardListView } from './FulfilmentBoardListView';

function cancelledRow(): BoardContribution {
  return {
    key: 'so-1:line-10',
    sales_order_id: 'so-1',
    line_id: 'core-line-10',
    product_id: 'prod-1',
    so_number: 'SO900001',
    customer_name: 'ZZT Customer Sdn Bhd',
    line_no: 10,
    so_line_no: 10,
    item_code: 'CB110',
    qty: '0',
    qty_outstanding: '0',
    required_date: '2026-09-04',
    unplannable: false,
    rank_score: 0,
    rank_factors: [],
    sources: [],
    trail: [],
    item_flags: null,
    contested: false,
    covered: false,
    decision: null,
    cancelled: true,
    cancelled_at: '2026-10-07T01:00:00',
    cancelled_by: 'Siti Test',
    cancel_reason: 'ZZT customer cancelled by phone',
  } as unknown as BoardContribution;
}

function renderRow() {
  return render(
    <FulfilmentBoardListView
      contributions={[cancelledRow()]}
      draft={{}}
      onDecide={vi.fn()}
      onDecideMany={vi.fn(async () => ({ saved: 0, failed: 0 }))}
      onDecideBatch={vi.fn(async () => ({ savedKeys: [], failed: [] }))}
    />,
  );
}

describe('FulfilmentBoardListView: a CRM-cancelled line (SO-CANCEL-LINE)', () => {
  it('shows the Confirmed pill and "Cancel SO" in the row', async () => {
    renderRow();
    const row = (await screen.findByText('SO900001')).closest('tr') as HTMLElement;
    expect(within(row).getByTestId('decision-pill-so-1:line-10')).toHaveTextContent('Confirmed');
    expect(row).toHaveTextContent('Cancel SO');
  });

  it('expands to "Transferred to Cancel SO by <name>, <time> · <reason>"', async () => {
    renderRow();
    await screen.findByText('SO900001');
    fireEvent.click(screen.getByTestId('board-list-expand-all'));
    const text = document.body.textContent ?? '';
    expect(text).toMatch(/Transferred to Cancel SO by Siti Test, .+ · ZZT customer cancelled by phone/);
    expect(text).not.toContain('This line was removed from the sales order');
  });
});
