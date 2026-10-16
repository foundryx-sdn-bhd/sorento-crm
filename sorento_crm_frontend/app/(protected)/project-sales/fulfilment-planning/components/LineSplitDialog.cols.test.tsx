/**
 * OI-LINE-SPLIT: the date columns include every line's AutoCount (now) date, so an ADDED
 * line (base 0, no base date, now 16/10/2026) has a 16/10/2026 column and its cell there is
 * a drop target `line-split-cell-<lineId>-<yyyy-mm-dd>`; a keyboard move onto it carries the
 * date, no typing. D1 fixture shape.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}
if (!(globalThis as { ResizeObserver?: unknown }).ResizeObserver) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

const { getLineSplits, saveLineSplits } = vi.hoisted(() => ({
  getLineSplits: vi.fn(),
  saveLineSplits: vi.fn(),
}));
vi.mock('../../_shared/services/lineSplitService', () => ({
  getLineSplits: (...a: unknown[]) => getLineSplits(...a),
  saveLineSplits: (...a: unknown[]) => saveLineSplits(...a),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

const line = (id: string, no: number, base: number, now: number, date: string, splits: unknown[] = []) => ({
  line_id: id,
  line_no: no,
  item_code: 'SRTSP124',
  location: 'BRW-BB',
  base_qty: base,
  base_date: base ? date : null,
  now_qty: now,
  now_date: date,
  pending_change: true,
  delivered_qty: 0,
  split_state: 'acting',
  acting: true,
  splits,
});
const sp = (id: string, qty: number, date: string | null, on: string, cancel = false) => ({
  id,
  qty,
  delivery_date: date,
  is_cancel: cancel,
  sits_on_line_id: on,
  remark: '',
});

const PAYLOAD = {
  pso_id: 'pso-424910',
  so_number: 'SO424910',
  customer: 'C',
  project: 'P',
  lines: [
    line('l16', 16, 76, 2, '2026-10-07'),
    line('l32', 32, 0, 4, '2026-10-16'),
    line('l48', 48, 0, 10, '2026-11-15'),
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(structuredClone(PAYLOAD));
});

describe('date columns from AutoCount dates', () => {
  async function open() {
    const path = './LineSplitDialog';
    const mod = (await import(/* @vite-ignore */ path)) as {
      LineSplitDialog: React.ComponentType<{
        open: boolean;
        psoId: string;
        focusLineId?: string;
        onOpenChange: (o: boolean) => void;
      }>;
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <mod.LineSplitDialog open psoId="pso-424910" focusLineId="l16" onOpenChange={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByTestId('line-split-row-l32');
  }

  it('an added line has a column for its AutoCount date and a drop-target cell there', async () => {
    await open();
    expect(screen.getByTestId('line-split-cell-l32-2026-10-16')).toBeInTheDocument();
    expect(screen.getByTestId('line-split-cell-l48-2026-11-15')).toBeInTheDocument();
    expect(screen.getAllByText('16/10/2026').length).toBeGreaterThan(0);
  });

  it('a keyboard move onto the added line carries its date with no typing', async () => {
    saveLineSplits.mockResolvedValue(structuredClone(PAYLOAD));
    await open();
    const card = await screen.findByTestId('line-split-card-own:l16');
    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowDown' });
    const moved = await screen.findAllByTestId(/^line-split-card-/);
    const again = moved.find((c) => c.textContent?.trim().startsWith('76')) ?? moved[0];
    again.focus();
    fireEvent.keyDown(again, { key: 'ArrowRight' });
    await waitFor(() =>
      expect(
        within(screen.getByTestId('line-split-cell-l32-2026-10-16')).getByText(/^76/),
      ).toBeInTheDocument(),
    );
  });
});
