/**
 * OI-LINE-SPLIT review round 2: the "AutoCount changed" warning pill for a line whose
 * stored split no longer acts (`acting: false`), and Escape / the header X with and without
 * unsaved changes.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

function payload(stale: string[] = [], applied: string[] = []) {
  const p = structuredClone(LINE_SPLITS_SO417636) as { lines: Array<Record<string, unknown>> };
  p.lines.forEach((l) => {
    const state = stale.includes(l.line_id as string)
      ? 'stale'
      : applied.includes(l.line_id as string)
        ? 'applied'
        : 'acting';
    l.split_state = state;
    l.acting = state === 'acting';
  });
  return p;
}

async function renderDialog(onOpenChange = vi.fn()) {
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
      <mod.LineSplitDialog open psoId={PSO_ID} focusLineId="line-20" onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  );
  return onOpenChange;
}

const toasted = () =>
  [toastFn, toastError, toastWarning].flatMap((f) => f.mock.calls.map((c) => String(c[0])));

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(payload());
  saveLineSplits.mockResolvedValue(payload());
});

describe('split_state: stale warning', () => {
  it('shows "AutoCount changed" in the row header and the zoom of a line that no longer acts', async () => {
    getLineSplits.mockResolvedValue(payload(['line-20']));
    await renderDialog();
    const row = await screen.findByTestId('line-split-row-line-20');
    expect(within(row).getByText(/AutoCount changed/i)).toBeInTheDocument();
    const zoom = await screen.findByTestId('line-split-zoom');
    expect(within(zoom).getByText(/AutoCount changed/i)).toBeInTheDocument();
    expect(
      within(screen.getByTestId('line-split-row-line-30')).queryByText(/AutoCount changed/i),
    ).not.toBeInTheDocument();
  });

  it('an applied line shows no pill', async () => {
    getLineSplits.mockResolvedValue(payload([], ['line-20']));
    await renderDialog();
    const row = await screen.findByTestId('line-split-row-line-20');
    expect(within(row).queryByText(/AutoCount changed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/AutoCount changed/i)).not.toBeInTheDocument();
  });

  it('shows no pill when every line acts', async () => {
    await renderDialog();
    await screen.findByTestId('line-split-row-line-20');
    expect(screen.queryByText(/AutoCount changed/i)).not.toBeInTheDocument();
  });
});

describe('Escape and the header X', () => {
  async function dirty() {
    const card = await screen.findByTestId('line-split-card-sp-20-167');
    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowDown' });
    await waitFor(() => {
      expect(screen.getByTestId('line-split-row-line-40').textContent).toMatch(/Plan 634/);
    });
  }
  const headerX = () => screen.getAllByRole('button', { name: /^close$/i })[0];

  it('with unsaved changes Escape keeps the dialog open and says why', async () => {
    const onOpenChange = await renderDialog();
    await dirty();
    fireEvent.keyDown(screen.getByTestId('line-split-dialog'), { key: 'Escape' });
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByTestId('line-split-dialog')).toBeInTheDocument();
    await waitFor(() => expect(toasted().join('|')).toMatch(/Unsaved changes: Save or Cancel/));
  });

  it('with unsaved changes the header X keeps the dialog open and says why', async () => {
    const onOpenChange = await renderDialog();
    await dirty();
    fireEvent.click(headerX());
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    await waitFor(() => expect(toasted().join('|')).toMatch(/Unsaved changes: Save or Cancel/));
  });

  it('with no changes Escape closes', async () => {
    const onOpenChange = await renderDialog();
    await screen.findByTestId('line-split-row-line-20');
    fireEvent.keyDown(screen.getByTestId('line-split-dialog'), { key: 'Escape' });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('with no changes the header X closes', async () => {
    const onOpenChange = await renderDialog();
    await screen.findByTestId('line-split-row-line-20');
    fireEvent.click(headerX());
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });
});
