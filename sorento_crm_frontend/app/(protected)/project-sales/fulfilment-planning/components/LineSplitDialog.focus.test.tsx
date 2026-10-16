/**
 * OI-LINE-SPLIT (hand-test findings): focus stays on a card after a keyboard move, and the
 * phone line chip of an AutoCount-added line shows its AutoCount qty, never "<item> 0".
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LINE_SPLITS_SO417636, PSO_ID } from '../../_shared/__mocks__/lineSplits';

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

const { getLineSplits, saveLineSplits, toastFn, toastError, toastWarning } = vi.hoisted(() => ({
  getLineSplits: vi.fn(),
  saveLineSplits: vi.fn(),
  toastFn: vi.fn(),
  toastError: vi.fn(),
  toastWarning: vi.fn(),
}));
vi.mock('../../_shared/services/lineSplitService', () => ({
  getLineSplits: (...args: unknown[]) => getLineSplits(...args),
  saveLineSplits: (...args: unknown[]) => saveLineSplits(...args),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(toastFn, { error: toastError, success: vi.fn(), warning: toastWarning }),
}));

function payload() {
  return structuredClone(LINE_SPLITS_SO417636);
}

async function open(focusLineId = 'line-20') {
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
      <mod.LineSplitDialog open psoId={PSO_ID} focusLineId={focusLineId} onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(payload());
  saveLineSplits.mockResolvedValue(payload());
});

describe('focus after a keyboard move', () => {
  it('stays on the moved card so a second arrow works without re-tabbing', async () => {
    await open();
    const card = await screen.findByTestId('line-split-card-sp-20-167');
    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowDown' });
    await waitFor(() => {
      expect(screen.getByTestId('line-split-row-line-40').textContent).toMatch(/Plan 634/);
    });
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId('line-split-card-sp-20-167')),
    );
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowUp' });
    await waitFor(() => {
      expect(screen.getByTestId('line-split-row-line-40').textContent).toMatch(/Plan 467/);
    });
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId('line-split-card-sp-20-167')),
    );
  });

  it('after a Shift+arrow part move, focus is on a card (the moved part)', async () => {
    await open();
    const card = await screen.findByTestId('line-split-card-sp-20-167');
    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowDown', shiftKey: true });
    await waitFor(() => {
      expect(screen.getByTestId('line-split-row-line-40').textContent).toMatch(/Plan 477/);
    });
    const active = document.activeElement as HTMLElement;
    expect(active.getAttribute('data-testid') ?? '', `focus is on ${active.tagName}`).toMatch(/^line-split-card-/);
    expect(active.textContent?.trim().startsWith('10')).toBe(true);
  });

  it('when the move merges into another split, focus goes to the merged card', async () => {
    await open();
    const card = await screen.findByTestId('line-split-card-sp-20-167');
    card.focus();
    // 167 on 20/11/2029 steps left onto the 236 on 20/11/2026 (same origin, same line): merge
    fireEvent.keyDown(card, { key: 'ArrowLeft' });
    await waitFor(() => {
      const cards = screen.getAllByTestId(/^line-split-card-/);
      expect(cards.some((c) => c.textContent?.trim().startsWith('403'))).toBe(true);
    });
    const active = document.activeElement as HTMLElement;
    expect(active.getAttribute('data-testid') ?? '', `focus is on ${active.tagName}`).toMatch(/^line-split-card-/);
    expect(active.textContent?.trim().startsWith('403')).toBe(true);
  });
});

describe('phone line chips', () => {
  it('an AutoCount-added line (base 0) shows its AutoCount qty, normal lines their base qty', async () => {
    (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
      matches: true,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    });
    const p = payload() as { lines: Array<Record<string, unknown>> };
    p.lines.push({
      line_id: 'line-60', line_no: 60, item_code: 'SRTSP124', location: 'BRW-BB',
      base_qty: 0, base_date: null, now_qty: 4, now_date: '2026-10-16',
      pending_change: true, delivered_qty: 0, split_state: 'acting', acting: true, splits: [],
    });
    p.lines.push({
      line_id: 'line-70', line_no: 70, item_code: 'SRTSP124', location: 'BRW-BB',
      base_qty: 76, base_date: '2026-10-07', now_qty: 2, now_date: '2026-10-07',
      pending_change: true, delivered_qty: 0, split_state: 'acting', acting: true, splits: [],
    });
    getLineSplits.mockResolvedValue(p);
    await open('line-70');
    await screen.findByText(/^SRTSP124 76$/);
    expect(screen.getByText(/^SRTSP124 4$/)).toBeInTheDocument();
    expect(screen.queryByText(/^SRTSP124 0$/)).not.toBeInTheDocument();
  });
});
