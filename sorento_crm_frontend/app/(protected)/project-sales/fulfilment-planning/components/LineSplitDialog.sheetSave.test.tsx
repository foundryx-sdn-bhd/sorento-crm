/**
 * OI-LINE-SPLIT v4: Save and the never-stuck rules on the sheet (UAC B1, B5, copy rule).
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addBtn,
  block,
  ownerCase,
  qty,
  renderDialog,
  saveButton,
  savedCase,
  sheet,
  splitLine,
  toastCalls,
  typeDate,
  typeQty,
} from '@/test-mocks/lineSplitSheetKit';

const { getLineSplits, saveLineSplits, toastFn, toastError, toastSuccess, toastWarning } =
  vi.hoisted(() => ({
    getLineSplits: vi.fn(),
    saveLineSplits: vi.fn(),
    toastFn: vi.fn(),
    toastError: vi.fn(),
    toastSuccess: vi.fn(),
    toastWarning: vi.fn(),
  }));
vi.mock('@/app/(protected)/project-sales/_shared/services/lineSplitService', () => ({
  getLineSplits: (...a: unknown[]) => getLineSplits(...a),
  saveLineSplits: (...a: unknown[]) => saveLineSplits(...a),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(toastFn, {
    error: toastError,
    success: toastSuccess,
    warning: toastWarning,
  }),
}));

const toasted = () =>
  toastCalls(toastFn, toastError, toastWarning)
    .map((c) => c.msg)
    .join('|');

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(structuredClone(ownerCase()));
  saveLineSplits.mockResolvedValue(structuredClone(ownerCase()));
});

describe('B1 Save waits for exact sums and dates', () => {
  it('Save is enabled with nothing changed', async () => {
    await renderDialog();
    await sheet();
    expect(saveButton()).toBeEnabled();
  });

  it('is disabled on a gap, on an over, and on a missing date; enabled again when it adds up', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    expect(saveButton()).toBeDisabled();
    typeQty(480, 1, '500');
    expect(saveButton()).toBeDisabled();
    typeQty(480, 1, '417');
    expect(saveButton()).toBeEnabled();
    typeDate(480, 1, '');
    await waitFor(() => expect(saveButton()).toBeDisabled());
    typeDate(480, 1, '20/10/2026');
    await waitFor(() => expect(saveButton()).toBeEnabled());
  });

  it('Save sends every line in one call: the split line with its splits, the rest as []', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.click(addBtn(480)); // 403 left, on New line at the base date
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveLineSplits).toHaveBeenCalledTimes(1));
    const [psoId, body] = saveLineSplits.mock.calls[0];
    expect(psoId).toBe('pso-417636');
    const ids = body.lines.map((l: { line_id: string }) => l.line_id).sort();
    expect(ids).toEqual(['L480', 'L608', 'L752', 'L768', 'L900']);
    for (const id of ['L608', 'L752', 'L768', 'L900'])
      expect(body.lines.find((l: { line_id: string }) => l.line_id === id).splits).toEqual([]);
    const l480 = body.lines.find((l: { line_id: string }) => l.line_id === 'L480');
    expect(l480.splits).toEqual([
      { qty: 14, delivery_date: '2026-10-20', is_cancel: false, sits_on_line_id: 'L480', remark: '' },
      { qty: 403, delivery_date: '2026-10-20', is_cancel: false, sits_on_line_id: null, remark: '' },
    ]);
  });
});

describe('B5 never stuck', () => {
  it('a failed load shows an error with Retry, and Retry loads again', async () => {
    getLineSplits.mockRejectedValueOnce(new Error('Failed to load the splits'));
    await renderDialog();
    const retry = await screen.findByRole('button', { name: /retry/i });
    expect(screen.queryByRole('table', { name: 'Split delivery sheet' })).toBeNull();
    fireEvent.click(retry);
    await sheet();
    expect(getLineSplits).toHaveBeenCalledTimes(2);
  });

  it('a failed save keeps the view open with every value and toasts the server message', async () => {
    saveLineSplits.mockRejectedValueOnce(new Error('Line 480 does not add up'));
    const onOpenChange = vi.fn();
    await renderDialog({ onOpenChange });
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.click(addBtn(480));
    fireEvent.click(saveButton());
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(String(toastError.mock.calls[0][0])).toContain('Line 480 does not add up');
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(qty(480, 1).value).toBe('14');
    expect(qty(480, 2).value).toBe('403');
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  async function dirty() {
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
  }
  const headerX = () => screen.getAllByRole('button', { name: /^close$/i })[0];

  it('with unsaved changes Escape keeps the page open and says why', async () => {
    const onOpenChange = vi.fn();
    await renderDialog({ onOpenChange });
    await dirty();
    fireEvent.keyDown(screen.getByTestId('line-split-dialog'), { key: 'Escape' });
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    await waitFor(() => expect(toasted()).toMatch(/Unsaved changes: Save or Cancel/));
  });

  it('with unsaved changes the one secondary button reads Cancel (never Close beside it) and discards and closes with no PUT', async () => {
    const onOpenChange = vi.fn();
    await renderDialog({ onOpenChange });
    await dirty();
    await waitFor(() => expect(screen.getByRole('button', { name: /^cancel$/i })).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /^close$/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(saveLineSplits).not.toHaveBeenCalled();
  });

  it('with no changes the button reads Close and there is no Cancel', async () => {
    await renderDialog();
    await sheet();
    expect(screen.getByRole('button', { name: /^close$/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^cancel$/i })).toBeNull();
  });

  it('with no changes Escape and Close close the page', async () => {
    const onOpenChange = vi.fn();
    await renderDialog({ onOpenChange });
    await sheet();
    fireEvent.keyDown(screen.getByTestId('line-split-dialog'), { key: 'Escape' });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    onOpenChange.mockClear();
    fireEvent.click(headerX());
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });
});

describe('copy', () => {
  it('never says tranche, in any state', async () => {
    getLineSplits.mockResolvedValue(savedCase());
    await renderDialog();
    await sheet();
    expect(document.body.textContent ?? '').not.toMatch(/tranche/i);
    act(() => undefined);
    expect(block(480).textContent ?? '').not.toMatch(/tranche/i);
  });
});
