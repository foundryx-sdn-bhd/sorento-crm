/**
 * `PLAN-oi-request-cs-reserve.md` section 3.9 / 4 (Slice 1), `oi-request-cs-reserve-
 * acceptance-criteria.md` AC-RS-42.
 *
 * TEST-FIRST (Phase 2): `FulfilmentBoardListView.tsx` (measured 22 Sep, plan section 2)
 * imports nothing named `BoardCellBreakdownDialog` today and has no Stock button on any
 * row - so a red here is "no such button", not an import typo or a fixture bug.
 *
 * AC-RS-42: "each line row carries a `Stock` icon-button (labelled) ... either opens the
 * grid view's `BoardCellBreakdownDialog` for that line, unchanged". `BoardCellBreakdownDialog`
 * is mocked wholesale - this pins that the LIST view actually wires a button to it, for
 * the right line, never the dialog's own internal rendering (covered by its own suite).
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
// The stock status chip reads through React Query; this suite pins the list's own wiring
// and renders without a client, so the chip is stubbed (its own suite covers it).
vi.mock('@/components/stock-status/StockStatusChip', () => ({ StockStatusChip: () => null }));
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
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const breakdownDialogSpy = vi.fn();
vi.mock('./BoardCellBreakdownDialog', () => ({
  BoardCellBreakdownDialog: (props: Record<string, unknown>) => {
    breakdownDialogSpy(props);
    return <div data-testid="mock-breakdown-dialog">stock-and-contributing-lines</div>;
  },
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
  } as BoardContribution;
}

function renderView(contributions: BoardContribution[] = [contribution()]) {
  return render(
    <FulfilmentBoardListView
      contributions={contributions}
      draft={{}}
      onDecide={vi.fn()}
      onDecideMany={vi.fn(async () => ({ saved: 0, failed: 0 }))}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AC-RS-42: the board list view carries a Stock button per line', () => {
  it('renders a labelled Stock icon-button on the row', async () => {
    renderView();

    expect(
      await screen.findByRole('button', { name: /stock/i }),
    ).toBeInTheDocument();
  });

  it('clicking it opens BoardCellBreakdownDialog for that line, unchanged', async () => {
    renderView([contribution({ item_code: 'B2155-NL-BLUE', product_id: 'prod-b2155' })]);

    fireEvent.click(await screen.findByRole('button', { name: /stock/i }));

    const dialog = await screen.findByTestId('mock-breakdown-dialog');
    expect(dialog).toBeInTheDocument();
    expect(breakdownDialogSpy).toHaveBeenCalled();
    const props = breakdownDialogSpy.mock.calls[breakdownDialogSpy.mock.calls.length - 1][0];
    // Whatever shape the coder builds the cell in, it must be addressed to THIS
    // contribution's own item/product - never a blank or a different line's.
    expect(JSON.stringify(props)).toMatch(/B2155-NL-BLUE|prod-b2155/);
  });

  it('two different rows open the dialog scoped to their own line, not a shared one', async () => {
    renderView([
      contribution({ key: 'so-1:line-10', item_code: 'B2155-NL-BLUE', line_no: 10 }),
      contribution({ key: 'so-2:line-20', item_code: 'CKS1050', line_no: 20 }),
    ]);

    const buttons = await screen.findAllByRole('button', { name: /stock/i });
    expect(buttons).toHaveLength(2);

    fireEvent.click(buttons[1]);
    await screen.findByTestId('mock-breakdown-dialog');
    const props = breakdownDialogSpy.mock.calls[breakdownDialogSpy.mock.calls.length - 1][0];
    expect(JSON.stringify(props)).toMatch(/CKS1050/);
  });

  it('AC-DT-11 (round 2, PLAN-oi-decision-trail-ui.md): icon only, ghost variant - matches the verdict-row pencil, no visible "Stock" text', async () => {
    renderView();

    const button = await screen.findByRole('button', { name: /stock/i });
    // The accessible name still comes off `aria-label`, never off rendered text - the
    // `<span className="hidden xl:inline">Stock</span>` label is gone.
    expect(button).not.toHaveTextContent('Stock');
    // `outline` (what this button used to be) always carries `border-input`; `ghost`
    // never does - the class list is the only reliable way to tell the two variants
    // apart without reaching into the component's own props.
    expect(button.className).not.toMatch(/border-input/);
  });
});
