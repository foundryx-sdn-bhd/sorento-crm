/**
 * The board's paperclip column (#1312, PLAN-oi-line-attachments-27sep.md, AC-U1/AC-U2):
 * every row carrying a core line id gets its own attachments button in the Verdict cell,
 * after `BoardVerdictActions`.
 *
 * `SoLineAttachmentsButton` itself is mocked (its own contract is
 * `SoLineAttachmentsButton.test.tsx`'s job) - what this file pins is that the BOARD wires
 * a core `line_id` into it, one per contributing line, reading off the `attachmentsByLine`
 * PROP this view now takes (fix round 1 should-fix 3): the ONE lookup call itself lives in
 * `FulfilmentBoardPanel` (`FulfilmentBoardPanel.test.tsx` pins "one call, not one per row"
 * there now), since this view is unit-tested standalone with no `QueryClientProvider` in
 * scope and the lookup used to carry its own nested one just to survive that.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
// The stock status chip reads through React Query; this suite pins the list's own wiring
// and renders without a client, so the chip is stubbed (its own suite covers it).
vi.mock('@/components/stock-status/StockStatusChip', () => ({ StockStatusChip: () => null }));
import type { BoardContribution, BoardDraft } from '../../_shared/types/fulfilmentPlanning.types';
import type { SoLineAttachmentsByLine } from '../../_shared/services/soLineAttachmentService';

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
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('@/app/(protected)/project-sales/_shared/components/SoLineAttachmentsButton', () => ({
  SoLineAttachmentsButton: ({ lineId, label }: { lineId: string; label: string }) => (
    <span data-testid={`attachments-button-${lineId}`}>{label}</span>
  ),
}));

import { FulfilmentBoardListView } from './FulfilmentBoardListView';

function contribution(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'so-1:line-10',
    sales_order_id: 'so-1',
    line_id: 'core-line-10',
    product_id: 'prod-1',
    so_number: 'SO397450',
    customer_name: 'Tuju Residences Sdn Bhd',
    agent_code: 'JEREMY',
    agent_label: 'Jeremy Lee',
    project_label: 'Tuju Residences',
    line_no: 10,
    item_code: 'B2155-NL-BLUE',
    qty: '43',
    qty_outstanding: '43',
    required_date: '2026-09-04',
    unplannable: false,
    rank_score: 0.82,
    rank_factors: [],
    sources: [{ kind: 'buy', qty: '43', reason: 'Nothing free at any location.' }],
    trail: [],
    item_flags: null,
    contested: false,
    covered: false,
    decision: null,
    ...overrides,
  };
}

function renderView(
  contributions: BoardContribution[],
  draft: BoardDraft = {},
  attachmentsByLine: SoLineAttachmentsByLine = {},
) {
  return render(
    <FulfilmentBoardListView
      contributions={contributions}
      draft={draft}
      onDecide={vi.fn()}
      onDecideMany={vi.fn()}
      onDecideBatch={vi.fn()}
      attachmentsByLine={attachmentsByLine}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('FulfilmentBoardListView: attachments paperclip (AC-U1/AC-U2)', () => {
  it('renders a paperclip per row carrying a core line id', async () => {
    renderView([
      contribution({ key: 'so-1:line-10', line_id: 'core-line-10' }),
      contribution({
        key: 'so-2:line-20',
        sales_order_id: 'so-2',
        line_id: 'core-line-20',
        so_number: 'SO397451',
        line_no: 20,
      }),
    ]);

    expect(await screen.findByTestId('attachments-button-core-line-10')).toBeInTheDocument();
    expect(screen.getByTestId('attachments-button-core-line-20')).toBeInTheDocument();
  });

  it('renders no paperclip for a row with no core line id', async () => {
    renderView([contribution({ line_id: null })]);

    await screen.findByText('SO397450');
    expect(screen.queryByTestId(/^attachments-button-/)).not.toBeInTheDocument();
  });
});
