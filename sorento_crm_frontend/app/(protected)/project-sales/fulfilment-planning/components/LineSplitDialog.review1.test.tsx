/**
 * OI-LINE-SPLIT v4 review round 1: the select merges like a drop (S2), New date never merges
 * (S1), and the handles carry the design-language hit target and press classes (S4).
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { COARSE_HIT_TARGET_CLASS, PRESSED_CLASS } from '@/components/ui/primitive-classes';
import {
  addBtn,
  balance,
  dateIn,
  onSel,
  ownerCase,
  qty,
  queryQty,
  renderDialog,
  sheet,
  splitLine,
  typeQty,
  wireSplit,
} from '@/test-mocks/lineSplitSheetKit';

const { getLineSplits, saveLineSplits } = vi.hoisted(() => ({
  getLineSplits: vi.fn(),
  saveLineSplits: vi.fn(),
}));
vi.mock('@/app/(protected)/project-sales/_shared/services/lineSplitService', () => ({
  getLineSplits: (...a: unknown[]) => getLineSplits(...a),
  saveLineSplits: (...a: unknown[]) => saveLineSplits(...a),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
}));

const focused = () => (document.activeElement ?? document.body) as HTMLElement;
const press = (key: string, code: string = key) =>
  fireEvent.keyDown(focused(), { key, code });
async function pickUp(handle: HTMLElement) {
  act(() => handle.focus());
  fireEvent.keyDown(handle, { key: ' ', code: 'Space' });
  await waitFor(() => expect(screen.getByTestId('drop-cancel')).toBeInTheDocument());
}
async function dropIt() {
  press(' ', 'Space');
  await waitFor(() => expect(screen.queryByTestId('drop-cancel')).toBeNull());
}
const hasClasses = (el: HTMLElement, constant: string) =>
  constant.split(/\s+/).filter(Boolean).every((c) => el.classList.contains(c));

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(structuredClone(ownerCase()));
});

describe('S2 the AUTOCOUNT LINE select merges like a drop', () => {
  it('picking line 608 for the 167 row gives ONE 403 row on 608, remarks joined', async () => {
    const p = ownerCase();
    p.lines.find((l) => l.line_id === 'L480')!.splits = [
      wireSplit('a', 14, '2026-09-20', 'L480'),
      wireSplit('b', 236, '2026-11-20', 'L608', { remark: 'first' }),
      wireSplit('c', 167, '2029-11-20', null, { remark: 'second' }),
    ];
    getLineSplits.mockResolvedValue(p);
    await renderDialog();
    await sheet();
    fireEvent.click(onSel(480, 3));
    const option = (await screen.findAllByRole('option')).find((o) =>
      /^Line 608/.test((o.textContent ?? '').trim()),
    )!;
    fireEvent.click(option);
    await waitFor(() => expect(queryQty(480, 3)).toBeNull());
    expect(qty(480, 2).value).toBe('403');
    expect(dateIn(480, 2).value).toBe('20/11/2026');
    expect(screen.getByLabelText('Remark, line 480 split 2')).toHaveValue('first; second');
    expect(balance(480).textContent).not.toMatch(/left|over/);
  });
});

describe('S1 a drop onto New date never merges', () => {
  it('a second chip drop onto New date makes a second split beside the empty-date one', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    const chip = (n: number) => screen.getByRole('button', { name: `Drag the ${n} left of line 480` });
    await pickUp(chip(403));
    for (let i = 0; i < 3; i += 1) press('ArrowDown');
    await dropIt();
    expect(qty(480, 2).value).toBe('403');
    expect(dateIn(480, 2).value).toBe('');
    typeQty(480, 2, '100');
    await waitFor(() => expect(balance(480).textContent).toMatch(/303 left/));
    await pickUp(chip(303));
    for (let i = 0; i < 3; i += 1) press('ArrowDown');
    await dropIt();
    expect(qty(480, 2).value).toBe('100');
    expect(qty(480, 3).value).toBe('303');
    expect(dateIn(480, 3).value).toBe('');
  });
});

describe('S4 hit targets and press cue on the handles and REMAIN buttons', () => {
  it('grip, "N left" chip and both REMAIN buttons carry COARSE_HIT_TARGET_CLASS and PRESSED_CLASS', async () => {
    await renderDialog();
    await sheet();
    const remainQty = screen.getByRole('button', { name: 'Split line 608 quantity' });
    const remainDate = screen.getByRole('button', { name: 'Split line 608 date' });
    const lineGrip = screen.getByRole('button', { name: 'Drag line 608' });
    await splitLine(480);
    typeQty(480, 1, '14');
    const splitGrip = screen.getByRole('button', { name: 'Drag split 1 of line 480' });
    const chip = screen.getByRole('button', { name: 'Drag the 403 left of line 480' });
    for (const el of [remainQty, remainDate, lineGrip, splitGrip, chip]) {
      expect(hasClasses(el, COARSE_HIT_TARGET_CLASS), `${el.getAttribute('aria-label')} hit target`).toBe(true);
      expect(hasClasses(el, PRESSED_CLASS), `${el.getAttribute('aria-label')} press cue`).toBe(true);
    }
    void addBtn;
  });
});
