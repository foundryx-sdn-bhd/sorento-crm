/**
 * OI-LINE-SPLIT D1: in the dialog an ADDED line (base 0) whose AutoCount qty is fully moved
 * in reads "Explained", never an over-plan.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
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
    line('l16', 16, 76, 2, '2026-10-07', [
      sp('a', 2, '2026-10-07', 'l16'),
      sp('b', 4, '2026-10-16', 'l32'),
      sp('c', 10, '2026-11-15', 'l48'),
      sp('d', 60, null, 'l16', true),
    ]),
    line('l32', 32, 0, 4, '2026-10-16'),
    line('l48', 48, 0, 10, '2026-11-15'),
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(structuredClone(PAYLOAD));
});

describe('LineSplitDialog added lines', () => {
  it('an added line fully moved in reads Explained', async () => {
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
    const row32 = await screen.findByTestId('line-split-row-l32');
    expect(within(row32).getByText(/Explained/i)).toBeInTheDocument();
    expect(row32.textContent).toMatch(/Plan 4 · AutoCount 4/);
    const row48 = screen.getByTestId('line-split-row-l48');
    expect(within(row48).getByText(/Explained/i)).toBeInTheDocument();
    expect(row48.textContent).toMatch(/Plan 10 · AutoCount 10/);
    expect(row32.textContent).not.toMatch(/CANCEL BALANCE/);
  });
});
