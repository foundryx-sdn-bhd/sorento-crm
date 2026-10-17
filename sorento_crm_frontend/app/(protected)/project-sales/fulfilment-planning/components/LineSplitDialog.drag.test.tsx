/**
 * OI-LINE-SPLIT v4: drag and drop on the sheet (UAC A8, A9, A10 keyboard half).
 *
 * jsdom has no layout, so the drag is driven the way a keyboard user drives it: focus a
 * handle, Space picks up, ArrowDown / ArrowUp step through the targets (the item's other
 * lines in order, then New date, then Cancel), Space or Enter drops, Escape puts it back.
 * That path must not depend on measured rects. The pointer collision and sensor wiring are
 * pinned in LineSplitDialog.dnd.test.tsx.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addBtn,
  balance,
  block,
  dateIn,
  onSel,
  ownerCase,
  qty,
  queryQty,
  remainQty,
  renderDialog,
  saveButton,
  savedCase,
  sheet,
  splitLine,
  toastCalls,
  typeDate,
  typeQty,
  wireSplit,
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

const grip = (no: number, n: number) =>
  screen.getByRole('button', { name: `Drag split ${n} of line ${no}` });
const lineGrip = (no: number) => screen.getByRole('button', { name: `Drag line ${no}` });
const chip = (no: number, left: number) =>
  screen.getByRole('button', { name: `Drag the ${left} left of line ${no}` });

const focused = () => (document.activeElement ?? document.body) as HTMLElement;
const press = (key: string, code: string = key, mods: Record<string, boolean> = {}) =>
  fireEvent.keyDown(focused(), { key, code, ...mods });
const moveTo = (n: number, dir: 'Down' | 'Up' = 'Down') => {
  for (let i = 0; i < n; i += 1) press(`Arrow${dir}`);
};

async function pickUp(handle: HTMLElement) {
  act(() => handle.focus());
  fireEvent.keyDown(handle, { key: ' ', code: 'Space' });
  await waitFor(() => expect(screen.getByTestId('drop-cancel')).toBeInTheDocument());
}
async function dropIt() {
  press(' ', 'Space');
  await waitFor(() => expect(screen.queryByTestId('drop-cancel')).toBeNull());
}
const toasts = () => toastCalls(toastFn, toastSuccess, toastWarning);

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(structuredClone(ownerCase()));
  saveLineSplits.mockResolvedValue(structuredClone(ownerCase()));
});

describe('AC-A8 handles', () => {
  it('the grip is the FIRST cell of every split row and of a REMAIN line', async () => {
    getLineSplits.mockResolvedValue(savedCase());
    await renderDialog();
    await sheet();
    for (const n of [1, 2, 3]) {
      const row = qty(480, n).closest('tr') as HTMLTableRowElement;
      expect(row.cells[0].contains(grip(480, n)), `split ${n} grip in the first cell`).toBe(true);
    }
    const remainRow = remainQty(608).closest('tr') as HTMLTableRowElement;
    expect(remainRow.cells[0].contains(lineGrip(608))).toBe(true);
  });

  it('grips and the "N left" chip are draggable', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    for (const el of [grip(480, 1), lineGrip(608), chip(480, 403)])
      expect(el.getAttribute('aria-roledescription')).toBe('draggable');
  });
});

describe('AC-A8 while dragging', () => {
  it('fades other items and locations, never targets the own block, opens New date and Cancel rows', async () => {
    await renderDialog();
    await sheet();
    expect(screen.queryByTestId('drop-new-date')).toBeNull();
    expect(screen.queryByTestId('drop-cancel')).toBeNull();
    await pickUp(lineGrip(480));
    // other item (CSK11A) and other location (KL-HQ) fade
    expect(block(768).getAttribute('data-dimmed')).toBe('true');
    expect(block(900).getAttribute('data-dimmed')).toBe('true');
    // the item's lines do not
    expect(block(608).getAttribute('data-dimmed')).not.toBe('true');
    expect(block(752).getAttribute('data-dimmed')).not.toBe('true');
    expect(screen.getByTestId('drop-new-date')).toBeInTheDocument();
    expect(screen.getByTestId('drop-cancel')).toBeInTheDocument();
    press('Escape');
    await waitFor(() => expect(screen.queryByTestId('drop-cancel')).toBeNull());
    expect(block(768).getAttribute('data-dimmed')).not.toBe('true');
  });

  it('Down and Up step through the other lines in order, then New date, then Cancel; never the own block', async () => {
    await renderDialog();
    await sheet();
    await pickUp(lineGrip(480));
    const over = () =>
      ['split-line-480', 'split-line-608', 'split-line-752', 'drop-new-date', 'drop-cancel'].filter(
        (id) => screen.getByTestId(id).getAttribute('data-over') === 'true',
      );
    press('ArrowDown');
    expect(over()).toEqual(['split-line-608']);
    press('ArrowDown');
    expect(over()).toEqual(['split-line-752']);
    press('ArrowDown');
    expect(over()).toEqual(['drop-new-date']);
    press('ArrowDown');
    expect(over()).toEqual(['drop-cancel']);
    press('ArrowUp');
    expect(over()).toEqual(['drop-new-date']);
    press('ArrowUp');
    press('ArrowUp');
    press('ArrowUp');
    expect(over()).toEqual(['split-line-608']);
    press('Escape');
  });

  it('the overlay shows the qty, item, "from line N" and the target', async () => {
    await renderDialog();
    await sheet();
    await pickUp(lineGrip(480));
    press('ArrowDown');
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/→\s*20\/11\/2026 · line 608/),
    );
    expect(document.body.textContent).toMatch(/417/);
    expect(document.body.textContent).toMatch(/from line 480/);
    moveTo(2);
    await waitFor(() => expect(document.body.textContent).toMatch(/→\s*New date/));
    press('ArrowDown');
    await waitFor(() => expect(document.body.textContent).toMatch(/→\s*Cancel this qty/));
    press('Escape');
  });

  it('Escape puts it back: nothing moves, no toast, and the page stays open', async () => {
    const onOpenChange = vi.fn();
    await renderDialog({ onOpenChange });
    await sheet();
    await pickUp(lineGrip(480));
    press('ArrowDown');
    press('Escape');
    await waitFor(() => expect(screen.queryByTestId('drop-cancel')).toBeNull());
    expect(remainQty(480)).toBeInTheDocument();
    expect(toasts().filter((t) => /^Moved/.test(t.msg))).toHaveLength(0);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});

describe('AC-A9 drops move ALL of it at once', () => {
  it('a split row dropped on line 608 sits on 608 and takes 20/11/2026, with a "Moved N" toast', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.click(addBtn(480)); // 403, base date, New line
    expect(qty(480, 2).value).toBe('403');
    await pickUp(grip(480, 2));
    press('ArrowDown');
    await dropIt();
    expect(qty(480, 2).value).toBe('403');
    expect(dateIn(480, 2).value).toBe('20/11/2026');
    expect(onSel(480, 2)).toHaveTextContent(/Line 608/);
    expect(balance(608).closest('tbody')!.textContent).toMatch(/\+403 from line 480/);
    const moved = toasts().find((t) => /^Moved 403 to 20\/11\/2026 · line 608/.test(t.msg));
    expect(moved, 'toast Moved 403 to 20/11/2026 · line 608').toBeDefined();
    expect(moved!.opts?.action?.label).toBe('Undo');
  });

  it('the "N left" chip makes a new split of N at the target', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    await pickUp(chip(480, 403));
    press('ArrowDown');
    await dropIt();
    expect(qty(480, 1).value).toBe('14');
    expect(qty(480, 2).value).toBe('403');
    expect(dateIn(480, 2).value).toBe('20/11/2026');
    expect(onSel(480, 2)).toHaveTextContent(/Line 608/);
    expect(balance(480).textContent).not.toMatch(/left|over/);
    expect(toasts().some((t) => /^Moved 403 to /.test(t.msg))).toBe(true);
  });

  it('landing where a split of the same own line already sits on that date merges: qty added, remarks joined', async () => {
    const p = savedCase();
    const l480 = p.lines.find((l) => l.line_id === 'L480')!;
    l480.splits = [
      wireSplit('s14', 14, '2026-09-20', 'L480'),
      wireSplit('s236', 236, '2026-11-20', 'L608', { remark: 'first' }),
      wireSplit('s167', 167, '2029-11-20', null, { remark: 'second' }),
    ];
    getLineSplits.mockResolvedValue(p);
    await renderDialog();
    await sheet();
    await pickUp(grip(480, 3));
    press('ArrowDown'); // line 608: 20/11/2026
    await dropIt();
    expect(queryQty(480, 3)).toBeNull();
    expect(qty(480, 2).value).toBe('403');
    expect(screen.getByLabelText('Remark, line 480 split 2')).toHaveValue('first; second');
    expect(balance(480).textContent).not.toMatch(/left|over/);
  });

  it('the chip merges the same way into an existing split on that line and date', async () => {
    const p = ownerCase();
    p.lines.find((l) => l.line_id === 'L480')!.splits = [
      wireSplit('a', 14, '2026-09-20', 'L480'),
      wireSplit('b', 236, '2026-11-20', 'L608'),
    ];
    getLineSplits.mockResolvedValue(p);
    await renderDialog();
    await sheet();
    await pickUp(chip(480, 167));
    press('ArrowDown');
    await dropIt();
    expect(queryQty(480, 3)).toBeNull();
    expect(qty(480, 2).value).toBe('403');
  });

  it('dropping a REMAIN line moves the whole line', async () => {
    await renderDialog();
    await sheet();
    await pickUp(lineGrip(480));
    press('ArrowDown');
    await dropIt();
    expect(qty(480, 1).value).toBe('417');
    expect(dateIn(480, 1).value).toBe('20/11/2026');
    expect(onSel(480, 1)).toHaveTextContent(/Line 608/);
    expect(screen.queryByRole('button', { name: 'Split line 480 quantity' })).toBeNull();
    expect(toasts().some((t) => /^Moved 417 to 20\/11\/2026 · line 608/.test(t.msg))).toBe(true);
  });

  it('a line AutoCount moved takes AutoCount\'s current date on the drop', async () => {
    await renderDialog();
    await sheet();
    await pickUp(lineGrip(480));
    moveTo(2); // line 752, now 02/01/2027
    await dropIt();
    expect(dateIn(480, 1).value).toBe('02/01/2027');
  });

  it('New date leaves the date empty, focused, and Save waits until a date is typed', async () => {
    await renderDialog();
    await sheet();
    await pickUp(lineGrip(480));
    moveTo(3); // 608, 752, New date
    await dropIt();
    const d = dateIn(480, 1);
    expect(d.value).toBe('');
    expect(onSel(480, 1)).toHaveTextContent(/New line/);
    await waitFor(() => expect(document.activeElement).toBe(d));
    expect(saveButton()).toBeDisabled();
    expect(screen.getByRole('status').textContent).toMatch(/CSK14B line 480: date to fill/);
    typeDate(480, 1, '20/11/2029');
    await waitFor(() => expect(saveButton()).toBeEnabled());
  });

  it('Cancel this qty makes a cancel split', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    await pickUp(chip(480, 403));
    moveTo(4); // 608, 752, New date, Cancel
    await dropIt();
    expect(qty(480, 2).value).toBe('403');
    expect(screen.queryByLabelText('Date, line 480 split 2')).toBeNull();
    expect(within(qty(480, 2).closest('tr')!).getAllByText(/^CANCEL/).length).toBeGreaterThan(0);
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveLineSplits).toHaveBeenCalled());
    const l480 = saveLineSplits.mock.calls[0][1].lines.find(
      (l: { line_id: string }) => l.line_id === 'L480',
    );
    expect(l480.splits).toContainEqual({
      qty: 403,
      delivery_date: null,
      is_cancel: true,
      sits_on_line_id: 'L480',
      remark: '',
    });
  });

  it('the toast Undo restores the line as it was', async () => {
    await renderDialog();
    await sheet();
    await pickUp(lineGrip(480));
    press('ArrowDown');
    await dropIt();
    expect(qty(480, 1).value).toBe('417');
    const moved = toasts().find((t) => /^Moved 417/.test(t.msg))!;
    act(() => moved.opts!.action!.onClick());
    await waitFor(() => expect(remainQty(480)).toBeInTheDocument());
    expect(queryQty(480, 1)).toBeNull();
  });
});

describe('the owner case, SO417636 CSK14B line 480 end to end', () => {
  it('14 @ 20/9/2026, 403 left to line 608, 236 typed over, 167 left to New date 20/11/2029: balance 0, Save sends it', async () => {
    await renderDialog();
    await sheet();

    // 1/5 type 14 in the REMAIN qty cell
    await splitLine(480);
    typeQty(480, 1, '14');
    expect(balance(480).textContent).toMatch(/403 left/);
    // 2/5 its date: 20/9/2026
    typeDate(480, 1, '20/9/2026');
    expect(balance(480).textContent).toMatch(/403 left/);

    // 3/5 drag "403 left" onto line 608
    await pickUp(chip(480, 403));
    press('ArrowDown');
    await dropIt();
    expect(qty(480, 2).value).toBe('403');
    expect(dateIn(480, 2).value).toBe('20/11/2026');
    expect(onSel(480, 2)).toHaveTextContent(/Line 608/);

    // 4/5 type 236 over 403: "167 left" appears
    typeQty(480, 2, '236');
    await waitFor(() => expect(balance(480).textContent).toMatch(/167 left/));

    // 5/5 drag "167 left" to New date and type 20/11/2029
    await pickUp(chip(480, 167));
    moveTo(3);
    await dropIt();
    expect(qty(480, 3).value).toBe('167');
    expect(dateIn(480, 3).value).toBe('');
    expect(saveButton()).toBeDisabled();
    typeDate(480, 3, '20/11/2029');

    await waitFor(() => expect(balance(480).textContent).not.toMatch(/left|over/));
    expect(balance(480).textContent).toMatch(/^\s*0/);
    await waitFor(() => expect(saveButton()).toBeEnabled());

    fireEvent.click(saveButton());
    await waitFor(() => expect(saveLineSplits).toHaveBeenCalledTimes(1));
    const [psoId, body] = saveLineSplits.mock.calls[0];
    expect(psoId).toBe('pso-417636');
    const by = (id: string) => body.lines.find((l: { line_id: string }) => l.line_id === id);
    expect(by('L480').splits).toEqual([
      { qty: 14, delivery_date: '2026-09-20', is_cancel: false, sits_on_line_id: 'L480', remark: '' },
      { qty: 236, delivery_date: '2026-11-20', is_cancel: false, sits_on_line_id: 'L608', remark: '' },
      { qty: 167, delivery_date: '2029-11-20', is_cancel: false, sits_on_line_id: null, remark: '' },
    ]);
    for (const id of ['L608', 'L752', 'L768', 'L900']) expect(by(id).splits).toEqual([]);
  });
});
