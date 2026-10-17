/**
 * STOCK-STATUS, crew audit fix round: on the fulfilment board the FIRST click on an item's
 * stock chip after load did not open its popover; later clicks did.
 *
 * The list's columns are memoised on its inputs (`draft`, `onDecide`, the attachment lookup
 * and more). Any of them changing rebuilds the columns, and TanStack renders a cell function
 * as a component, so every cell remounts: an open popover went with it. This pins the REAL
 * chip on the REAL list surviving that rebuild. The panel inside it is stubbed.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import type { BoardContribution } from '../../_shared/types/fulfilmentPlanning.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));
vi.mock('@/lib/api', () => ({
  apiFetch: async () =>
    new Response(
      JSON.stringify([{ product_id: 'prod-1', on_hand: 499, so_qty: 10390 }]),
      { status: 200 },
    ),
}));
vi.mock(
  '@/components/stock-status/StockStatusPanel',
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import('@/components/stock-status/StockStatusPanel')
      >();
    return {
      ...actual,
      StockStatusPanel: () => <div data-testid="stock-status-panel-stub" />,
    };
  },
);

import { FulfilmentBoardListView } from './FulfilmentBoardListView';

const ROW = {
  key: 'so-1:line-10',
  sales_order_id: 'so-1',
  line_id: 'core-line-10',
  product_id: 'prod-1',
  so_number: 'SO421741',
  customer_name: 'HAANAFLO',
  agent_code: 'JEREMY',
  agent_label: 'Jeremy Lee',
  project_label: 'LUMINAR SUB',
  line_no: 128,
  item_code: 'SRTWB1515',
  qty: '468',
  qty_outstanding: '468',
  required_date: '2026-12-31',
  unplannable: false,
  rank_score: 0.82,
  rank_factors: [],
  sources: [
    { kind: 'buy', qty: '468', reason: 'Nothing free at any location.' },
  ],
  trail: [],
  item_flags: null,
  contested: false,
  covered: false,
  decision: null,
} as unknown as BoardContribution;

describe('fulfilment board List view: the stock chip', () => {
  it('opens on the first click and stays open when the list rebuilds its columns', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = (draft: Record<string, never>) => (
      <QueryClientProvider client={client}>
        <FulfilmentBoardListView
          contributions={[ROW]}
          draft={draft}
          onDecide={vi.fn()}
          onDecideMany={vi.fn(async () => ({ saved: 0, failed: 0 }))}
          onDecideBatch={vi.fn()}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(view({}));

    await userEvent.click(
      screen.getByRole('button', { name: 'Stock status of SRTWB1515' }),
    );
    expect(
      await screen.findByTestId('stock-status-panel-stub'),
    ).toBeInTheDocument();

    // A new draft object, as the board hands down on any change: the columns rebuild.
    rerender(view({}));
    expect(
      await screen.findByTestId('stock-status-panel-stub'),
    ).toBeInTheDocument();
  });

  it('two rows of the same product with no SO line id: a click opens exactly one popover (reviewer B1)', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const rows = [
      { ...ROW, key: 'so-1:line-10', line_id: null, so_number: 'SO421741' },
      { ...ROW, key: 'so-2:line-20', line_id: null, so_number: 'SO420099' },
    ] as unknown as BoardContribution[];
    render(
      <QueryClientProvider client={client}>
        <FulfilmentBoardListView
          contributions={rows}
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn(async () => ({ saved: 0, failed: 0 }))}
          onDecideBatch={vi.fn()}
        />
      </QueryClientProvider>,
    );
    const chips = screen.getAllByRole('button', {
      name: 'Stock status of SRTWB1515',
    });
    expect(chips).toHaveLength(2);
    await userEvent.click(chips[0]);
    await waitFor(() =>
      expect(screen.getAllByTestId('stock-status-panel-stub')).toHaveLength(1),
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getAllByTestId('stock-status-panel-stub')).toHaveLength(1);
  });
});
