/**
 * OI-LINE-SPLIT: the whole-SO split view (UAC A1, A3, A7, B1, B3, B5).
 *
 * Data comes from `useLineSplits(psoId)` over the service module, mocked here; the fixture
 * is the SO417636 payload in `_shared/__mocks__/lineSplits.ts` (contract shape). The
 * component is imported inside each test so a missing file fails that test, not the file.
 *
 * Service contract this test pins: `getLineSplits(psoId)` and `saveLineSplits(psoId, body)`
 * where `body` is `{ lines: [{ line_id, splits: [...] }] }`.
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

const { getLineSplits, saveLineSplits, toastError, toastSuccess } = vi.hoisted(() => ({
  getLineSplits: vi.fn(),
  saveLineSplits: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock('../../_shared/services/lineSplitService', () => ({
  getLineSplits: (...args: unknown[]) => getLineSplits(...args),
  saveLineSplits: (...args: unknown[]) => saveLineSplits(...args),
}));

vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { error: toastError, success: toastSuccess }),
}));

type Props = { open: boolean; psoId: string; focusLineId?: string; onOpenChange: (o: boolean) => void };

async function renderDialog(props: Partial<Props> = {}) {
  const path = './LineSplitDialog';
  const mod = (await import(/* @vite-ignore */ path)) as {
    LineSplitDialog: React.ComponentType<Props>;
  };
  const onOpenChange = props.onOpenChange ?? vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <mod.LineSplitDialog
        open
        psoId={PSO_ID}
        focusLineId="line-20"
        {...props}
        onOpenChange={onOpenChange}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onOpenChange };
}

const saveButton = () => screen.getByRole('button', { name: /^save$/i });

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(structuredClone(LINE_SPLITS_SO417636));
  saveLineSplits.mockResolvedValue(structuredClone(LINE_SPLITS_SO417636));
});

describe('LineSplitDialog', () => {
  it('lists every open line as a row, grouped by item code and location', async () => {
    await renderDialog();
    const dialog = await screen.findByTestId('line-split-dialog');
    await screen.findByTestId('line-split-row-line-20');

    const rows = within(dialog)
      .getAllByTestId(/^line-split-row-/)
      .map((el) => el.getAttribute('data-testid'));
    expect(rows).toEqual([
      'line-split-row-line-10',
      'line-split-row-line-20',
      'line-split-row-line-30',
      'line-split-row-line-40',
      'line-split-row-line-50',
    ]);
    expect(within(dialog).getAllByText(/CSK14B/).length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText(/CSK11A/).length).toBeGreaterThan(0);
    expect(dialog.textContent ?? '').not.toMatch(/tranche/i);
  });

  it('typing a qty that breaks the sum shows Gap on the row and in the footer and blocks Save', async () => {
    await renderDialog();
    await screen.findByTestId('line-split-row-line-20');
    const zoom = await screen.findByTestId('line-split-zoom');

    const qtys = within(zoom).getAllByLabelText('Qty');
    expect(qtys).toHaveLength(3); // 14, 236, 167
    fireEvent.change(qtys[0], { target: { value: '8' } });
    fireEvent.blur(qtys[0]);

    await waitFor(() => {
      expect(screen.getByTestId('line-split-row-line-20').textContent).toMatch(/Gap 6/);
    });
    expect(screen.getByTestId('line-split-footer').textContent).toMatch(/Gap 6/);
    expect(saveButton()).toBeDisabled();

    // and over the other way
    fireEvent.change(within(screen.getByTestId('line-split-zoom')).getAllByLabelText('Qty')[0], {
      target: { value: '20' },
    });
    await waitFor(() => {
      expect(screen.getByTestId('line-split-footer').textContent).toMatch(/Over 6/);
    });
    expect(saveButton()).toBeDisabled();
  });

  it('ArrowDown on a focused card moves it to the next line of the group; Save sends the new sits-on', async () => {
    await renderDialog();
    const card = await screen.findByTestId('line-split-card-sp-20-167');
    expect(screen.getByTestId('line-split-row-line-40').textContent).toMatch(/Plan 467/);
    expect(screen.getByTestId('line-split-row-line-30').textContent).toMatch(/Plan 632/);

    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowDown' });

    await waitFor(() => {
      expect(screen.getByTestId('line-split-row-line-40').textContent).toMatch(/Plan 634/);
    });
    expect(screen.getByTestId('line-split-row-line-30').textContent).toMatch(/Plan 465/);

    fireEvent.click(saveButton());
    await waitFor(() => expect(saveLineSplits).toHaveBeenCalledTimes(1));
    const [psoId, body] = saveLineSplits.mock.calls[0];
    expect(psoId).toBe(PSO_ID);
    const line20 = body.lines.find((l: { line_id: string }) => l.line_id === 'line-20');
    const moved = line20.splits.find((s: { qty: number }) => s.qty === 167);
    expect(moved).toMatchObject({
      sits_on_line_id: 'line-40',
      delivery_date: '2029-11-20',
      is_cancel: false,
    });
    const stay = line20.splits.find((s: { qty: number }) => s.qty === 236);
    expect(stay.sits_on_line_id).toBe('line-30');
  });

  it('Save sends every line of the order in one call, unsplit lines as an empty list', async () => {
    await renderDialog();
    const card = await screen.findByTestId('line-split-card-sp-20-167');
    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowDown' });
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveLineSplits).toHaveBeenCalledTimes(1));
    const body = saveLineSplits.mock.calls[0][1];
    expect(body.lines.map((l: { line_id: string }) => l.line_id)).toEqual([
      'line-10', 'line-20', 'line-30', 'line-40', 'line-50',
    ]);
    expect(body.lines.find((l: { line_id: string }) => l.line_id === 'line-10').splits).toEqual([]);
    expect(body.lines.find((l: { line_id: string }) => l.line_id === 'line-50').splits).toEqual([]);
  });

  it('a rejected save keeps the dialog open with the edited value and shows the server message', async () => {
    saveLineSplits.mockRejectedValueOnce(new Error('Line 20 does not add up'));
    const { onOpenChange } = await renderDialog();
    const card = await screen.findByTestId('line-split-card-sp-20-167');
    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowDown' });
    await waitFor(() => {
      expect(screen.getByTestId('line-split-row-line-40').textContent).toMatch(/Plan 634/);
    });

    fireEvent.click(saveButton());

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(String(toastError.mock.calls[0][0])).toContain('Line 20 does not add up');
    expect(screen.getByTestId('line-split-dialog')).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByTestId('line-split-row-line-40').textContent).toMatch(/Plan 634/);
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('a failed load shows an error with Retry, and Retry loads again', async () => {
    getLineSplits.mockRejectedValueOnce(new Error('Failed to load the splits'));
    await renderDialog();
    const retry = await screen.findByRole('button', { name: /retry/i });
    expect(screen.queryByTestId(/^line-split-row-/)).not.toBeInTheDocument();

    fireEvent.click(retry);
    await screen.findByTestId('line-split-row-line-20');
    expect(getLineSplits).toHaveBeenCalledTimes(2);
  });

  it('never says tranche, in any state', async () => {
    await renderDialog();
    await screen.findByTestId('line-split-row-line-20');
    expect(document.body.textContent ?? '').not.toMatch(/tranche/i);
  });
});
