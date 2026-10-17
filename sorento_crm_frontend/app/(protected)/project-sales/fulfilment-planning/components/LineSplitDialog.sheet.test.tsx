/**
 * OI-LINE-SPLIT v4: the one split sheet (UAC A2 to A7, A10 to A12), through the dialog.
 * DOM contract: PLAN-oi-line-split-16oct.md "Build v4". Drag is in LineSplitDialog.drag.test.tsx.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addBtn,
  addedCase,
  balance,
  block,
  blockIds,
  dateIn,
  leaveSheet,
  onSel,
  ownerCase,
  qty,
  queryQty,
  remainDate,
  remainQty,
  remarkIn,
  removeBtn,
  renderDialog,
  savedCase,
  sheet,
  splitLine,
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

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(structuredClone(ownerCase()));
  saveLineSplits.mockResolvedValue(structuredClone(ownerCase()));
});

describe('AC-A2 one table, columns and line sort', () => {
  it('renders one table with a tbody per line, sorted by item, location, base date, line no', async () => {
    await renderDialog();
    await sheet();
    expect(blockIds()).toEqual([
      'split-line-768',
      'split-line-480',
      'split-line-608',
      'split-line-752',
      'split-line-900',
    ]);
  });

  it('lists the columns in order: LINE ... BALANCE, with CHANGE QUANTITY after DELIVERY DATE', async () => {
    await renderDialog();
    const table = await sheet();
    const heads = within(table)
      .getAllByRole('columnheader')
      .map((h) => (h.textContent ?? '').trim().toLowerCase())
      .filter(Boolean);
    expect(heads).toEqual([
      'line',
      'so date',
      's/o no',
      'item code',
      'location',
      'qty',
      'delivery date',
      'change quantity',
      'change date',
      'autocount line',
      'remark',
      'balance',
    ]);
  });

  it('shows the base cells of an unsplit line: line no, SO date, S/O no, item, location, qty, date', async () => {
    await renderDialog();
    await sheet();
    const text = block(480).textContent ?? '';
    expect(text).toContain('480');
    expect(text).toContain('03/09/2026');
    expect(text).toContain('SO417636');
    expect(text).toContain('CSK14B');
    expect(text).toContain('BRW-BB');
    expect(text).toContain('417');
    expect(text).toContain('20/10/2026');
  });
});

describe('AC-A3 merged cells and the extra notes', () => {
  it('a split line stacks one row per split and merges its base cells with rowSpan', async () => {
    getLineSplits.mockResolvedValue(savedCase());
    await renderDialog();
    await sheet();
    const b = block(480);
    expect(b.querySelectorAll('tr')).toHaveLength(3);
    const itemCell = within(b).getByText('CSK14B').closest('td')!;
    expect(itemCell.getAttribute('rowspan')).toBe('3');
    expect(balance(480).closest('td')!.getAttribute('rowspan')).toBe('3');
    // the merged cells appear once, in the first row
    expect(within(b).getAllByText('CSK14B')).toHaveLength(1);
    // each split row has its own qty input
    expect([1, 2, 3].map((n) => qty(480, n).value)).toEqual(['14', '236', '167']);
    expect(dateIn(480, 2).value).toBe('20/11/2026');
  });

  it('an unsplit line has single-row cells (no rowSpan above 1)', async () => {
    await renderDialog();
    await sheet();
    const cells = Array.from(block(608).querySelectorAll('td[rowspan]'));
    for (const c of cells) expect(Number(c.getAttribute('rowspan'))).toBeLessThanOrEqual(1);
  });

  it('inside Confirm a line with a pending change shows "AutoCount now <qty> · <date>"', async () => {
    await renderDialog();
    await sheet();
    expect(block(752).textContent).toMatch(/AutoCount now\s*649 · 02\/01\/2027/);
    expect(block(608).textContent).not.toMatch(/AutoCount now/);
  });

  it('an AutoCount-added line shows QTY 0 and "Added in AutoCount: <qty> · <date>" and cannot be split or dragged', async () => {
    getLineSplits.mockResolvedValue(addedCase());
    await renderDialog({ focusLineId: 'L16' }, 'pso-424910');
    await sheet();
    const b = block(32);
    expect(b.textContent).toMatch(/Added in AutoCount: 4 · 16\/10\/2026/);
    expect(within(b).getAllByText('0').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Split line 32 quantity' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add a split to line 32' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Drag line 32' })).toBeNull();
  });

  it('a line other lines splits sit on shows "+N from line M" for each', async () => {
    getLineSplits.mockResolvedValue(savedCase());
    await renderDialog();
    await sheet();
    expect(block(608).textContent).toMatch(/\+236 from line 480/);
    expect(block(480).textContent).not.toMatch(/from line 480/);
    // the split with no sits-on line (a new line) is shown on no other line
    expect(block(752).textContent).not.toMatch(/from line/);
  });

  it('"+N from line M" also shows on an added line', async () => {
    getLineSplits.mockResolvedValue(addedCase());
    await renderDialog({ focusLineId: 'L16' }, 'pso-424910');
    await sheet();
    expect(block(32).textContent).toMatch(/\+4 from line 16/);
  });

  it('a line whose saved splits are stale shows the "AutoCount changed" pill; a line that acts shows none', async () => {
    const p = savedCase();
    p.lines.find((l) => l.line_id === 'L480')!.split_state = 'stale';
    p.lines.find((l) => l.line_id === 'L480')!.acting = false;
    getLineSplits.mockResolvedValue(p);
    await renderDialog();
    await sheet();
    expect(within(block(480)).getByText(/AutoCount changed/i)).toBeInTheDocument();
    expect(within(block(608)).queryByText(/AutoCount changed/i)).toBeNull();
  });

  it('an applied line shows no AutoCount changed pill', async () => {
    const q = savedCase();
    q.lines.find((l) => l.line_id === 'L480')!.split_state = 'applied';
    q.lines.find((l) => l.line_id === 'L480')!.acting = false;
    getLineSplits.mockResolvedValue(q);
    await renderDialog();
    await sheet();
    expect(screen.queryByText(/AutoCount changed/i)).toBeNull();
  });
});

describe('AC-A4 REMAIN', () => {
  it('an unsplit line reads REMAIN in CHANGE QUANTITY and CHANGE DATE', async () => {
    await renderDialog();
    await sheet();
    expect(remainQty(608)).toHaveTextContent('REMAIN');
    expect(remainDate(608)).toHaveTextContent('REMAIN');
    expect(queryQty(608, 1)).toBeNull();
  });

  it('clicking the REMAIN qty cell makes one split row (base qty, base date, this line) focused in qty', async () => {
    await renderDialog();
    await sheet();
    const q = await splitLine(480);
    expect((q as HTMLInputElement).value).toBe('417');
    expect(dateIn(480, 1).value).toBe('20/10/2026');
    expect(onSel(480, 1)).toHaveTextContent(/Line 480 \(this line\)/);
    expect(document.activeElement).toBe(q);
    expect(screen.queryByRole('button', { name: 'Split line 480 quantity' })).toBeNull();
    // the other lines still read REMAIN
    expect(remainQty(608)).toBeInTheDocument();
  });

  it('clicking the REMAIN date cell puts focus in the date input', async () => {
    await renderDialog();
    await sheet();
    fireEvent.click(remainDate(480));
    const d = await screen.findByLabelText('Date, line 480 split 1');
    expect(document.activeElement).toBe(d);
    expect(qty(480, 1).value).toBe('417');
  });

  it('typing a digit on the REMAIN qty cell splits the line with the digit in qty', async () => {
    await renderDialog();
    await sheet();
    fireEvent.keyDown(remainQty(480), { key: '5' });
    const q = (await screen.findByLabelText('Quantity, line 480 split 1')) as HTMLInputElement;
    expect(document.activeElement).toBe(q);
    expect(q.value).toBe('5');
  });

  it('typing a digit or "/" on the REMAIN date cell splits the line with the key in the date input', async () => {
    await renderDialog();
    await sheet();
    fireEvent.keyDown(remainDate(480), { key: '2' });
    const d = (await screen.findByLabelText('Date, line 480 split 1')) as HTMLInputElement;
    expect(document.activeElement).toBe(d);
    expect(d.value).toContain('2');

    fireEvent.keyDown(remainDate(608), { key: '/' });
    const d2 = (await screen.findByLabelText('Date, line 608 split 1')) as HTMLInputElement;
    expect(document.activeElement).toBe(d2);
    expect(d2.value).toContain('/');
  });

  it('when focus leaves a line whose only split equals its base it reads REMAIN again, remark kept', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    const remark = screen.getByLabelText(/^Remark,? line 480( split 1)?$/);
    act(() => (remark as HTMLElement).focus());
    fireEvent.change(remark, { target: { value: 'hold for site' } });
    leaveSheet();
    await waitFor(() => expect(remainQty(480)).toBeInTheDocument());
    expect(queryQty(480, 1)).toBeNull();
    expect(screen.getByLabelText(/^Remark,? line 480( split 1)?$/)).toHaveValue('hold for site');
  });

  it('a split that differs from its base stays split when focus leaves', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    leaveSheet();
    await new Promise((r) => setTimeout(r, 30));
    expect(qty(480, 1).value).toBe('14');
    expect(screen.queryByRole('button', { name: 'Split line 480 quantity' })).toBeNull();
  });

  it('a zero-qty split is dropped when focus leaves its line', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    fireEvent.click(addBtn(480)); // nothing is left, so the new split is 0
    expect(qty(480, 2).value).toBe('0');
    leaveSheet();
    await waitFor(() => expect(remainQty(480)).toBeInTheDocument());
    expect(queryQty(480, 2)).toBeNull();
  });
});

describe('AC-A5 + Split, Ctrl/Cmd+Enter, remove', () => {
  it('+ Split fills what is left, on the base date, AutoCount line New line, qty focused and selected', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.click(addBtn(480));
    const q2 = qty(480, 2);
    expect(q2.value).toBe('403');
    expect(dateIn(480, 2).value).toBe('20/10/2026');
    expect(onSel(480, 2)).toHaveTextContent(/New line/);
    expect(document.activeElement).toBe(q2);
    expect((q2.selectionEnd ?? 0) - (q2.selectionStart ?? 0)).toBe(3);
    expect(balance(480).textContent).toMatch(/^\s*0/);
  });

  it('+ Split on an unsplit line makes its own split plus a new one of 0', async () => {
    await renderDialog();
    await sheet();
    fireEvent.click(addBtn(608));
    expect(qty(608, 1).value).toBe('192');
    expect(qty(608, 2).value).toBe('0');
    expect(onSel(608, 2)).toHaveTextContent(/New line/);
  });

  it('Ctrl+Enter and Cmd+Enter in a split row add a split under the line', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.keyDown(qty(480, 1), { key: 'Enter', ctrlKey: true });
    expect(qty(480, 2).value).toBe('403');
    fireEvent.keyDown(qty(480, 2), { key: 'Enter', metaKey: true });
    expect(qty(480, 3).value).toBe('0');
  });

  it('the x button removes a split row at once, with no confirm', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.click(addBtn(480));
    fireEvent.click(removeBtn(480, 2));
    expect(queryQty(480, 2)).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(balance(480).textContent).toMatch(/403 left/);
  });

  it('Ctrl+Backspace in a split row removes it', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.click(addBtn(480));
    fireEvent.keyDown(qty(480, 2), { key: 'Backspace', ctrlKey: true });
    expect(queryQty(480, 2)).toBeNull();
  });
});

describe('AC-A6 balance', () => {
  it('shows "N left" as a draggable chip when short, "N over" when over, "0" when it adds up', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    expect(balance(480).textContent).toMatch(/403 left/);
    expect(
      screen.getByRole('button', { name: 'Drag the 403 left of line 480' }),
    ).toBeInTheDocument();

    typeQty(480, 1, '500');
    expect(balance(480).textContent).toMatch(/83 over/);
    expect(screen.queryByRole('button', { name: /^Drag the .* left of line 480$/ })).toBeNull();

    typeQty(480, 1, '417');
    expect(balance(480).textContent).not.toMatch(/left|over/);
    expect(balance(480).textContent).toMatch(/^\s*0/);
  });

  it('a line that is not split shows only "+ Split"', async () => {
    await renderDialog();
    await sheet();
    expect(balance(608).textContent).not.toMatch(/left|over|^\s*0/);
    expect(addBtn(608)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Drag the .* left of line 608/ })).toBeNull();
  });
});

describe('AC-A7 AUTOCOUNT LINE select', () => {
  async function openSelect() {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.click(addBtn(480));
    fireEvent.click(onSel(480, 2));
    await screen.findAllByRole('option');
  }
  const labels = () =>
    screen.getAllByRole('option').map((o) => (o.textContent ?? '').trim());
  const pick = (name: string | RegExp) => {
    const o = screen.getAllByRole('option').find((x) =>
      typeof name === 'string' ? (x.textContent ?? '').trim() === name : name.test(x.textContent ?? ''),
    );
    expect(o, `option ${String(name)}`).toBeDefined();
    fireEvent.click(o!);
  };

  it('offers this line, every other line of the same item and location with its date, New line and Cancel', async () => {
    await openSelect();
    expect(labels()).toEqual([
      'Line 480 (this line)',
      'Line 608 · 20/11/2026',
      // line 752 shows AutoCount's current date
      'Line 752 · 02/01/2027',
      'New line',
      'Cancel',
    ]);
  });

  it('picking another line sets the split date to that line date', async () => {
    await openSelect();
    pick('Line 608 · 20/11/2026');
    await waitFor(() => expect(dateIn(480, 2).value).toBe('20/11/2026'));
    fireEvent.click(onSel(480, 2));
    await screen.findAllByRole('option');
    pick('Line 752 · 02/01/2027');
    await waitFor(() => expect(dateIn(480, 2).value).toBe('02/01/2027'));
  });

  it('Cancel makes the date cell read CANCEL; leaving Cancel takes the base date', async () => {
    await openSelect();
    pick('Cancel');
    await waitFor(() => expect(screen.queryByLabelText('Date, line 480 split 2')).toBeNull());
    const row = qty(480, 2).closest('tr')!;
    expect(within(row).getAllByText(/^CANCEL/).length).toBeGreaterThan(0);
    fireEvent.click(onSel(480, 2));
    await screen.findAllByRole('option');
    pick('Line 480 (this line)');
    await waitFor(() => expect(dateIn(480, 2).value).toBe('20/10/2026'));
  });
});

describe('AC-A10 keyboard in the cells', () => {
  async function twoRows() {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    fireEvent.click(addBtn(480));
  }

  it('Enter and ArrowDown go to the same column one row down; Shift+Enter and ArrowUp one row up', async () => {
    await twoRows();
    act(() => qty(480, 1).focus());
    fireEvent.keyDown(qty(480, 1), { key: 'Enter' });
    expect(document.activeElement).toBe(qty(480, 2));
    fireEvent.keyDown(qty(480, 2), { key: 'Enter', shiftKey: true });
    expect(document.activeElement).toBe(qty(480, 1));
    fireEvent.keyDown(qty(480, 1), { key: 'ArrowDown' });
    expect(document.activeElement).toBe(qty(480, 2));
    fireEvent.keyDown(qty(480, 2), { key: 'ArrowUp' });
    expect(document.activeElement).toBe(qty(480, 1));
  });

  it('Enter in the remark and date columns stays in that column', async () => {
    await twoRows();
    act(() => dateIn(480, 1).focus());
    fireEvent.keyDown(dateIn(480, 1), { key: 'Enter' });
    expect(document.activeElement).toBe(dateIn(480, 2));
    act(() => remarkIn(480, 1).focus());
    fireEvent.keyDown(remarkIn(480, 1), { key: 'ArrowDown' });
    expect(document.activeElement).toBe(remarkIn(480, 2));
  });

  it('from the last split row, Enter goes down to the next line REMAIN qty cell', async () => {
    await twoRows();
    act(() => qty(480, 2).focus());
    fireEvent.keyDown(qty(480, 2), { key: 'Enter' });
    expect(document.activeElement).toBe(remainQty(608));
  });

  it('Ctrl+Z undoes the last change', async () => {
    await twoRows();
    expect(qty(480, 2)).toBeInTheDocument();
    fireEvent.keyDown(qty(480, 2), { key: 'z', ctrlKey: true });
    await waitFor(() => expect(queryQty(480, 2)).toBeNull());
  });
});

describe('AC-A11 change pill per split against its own line base date', () => {
  it('later DELAY, earlier ADVANCE, same REMAIN, cancel CANCEL', async () => {
    const p = ownerCase();
    p.lines.find((l) => l.line_id === 'L480')!.splits = [
      { id: 'a', qty: 14, delivery_date: '2026-09-20', is_cancel: false, sits_on_line_id: 'L480', remark: '' },
      { id: 'b', qty: 100, delivery_date: '2026-10-20', is_cancel: false, sits_on_line_id: 'L480', remark: '' },
      { id: 'c', qty: 200, delivery_date: '2026-11-20', is_cancel: false, sits_on_line_id: 'L608', remark: '' },
      { id: 'd', qty: 103, delivery_date: null, is_cancel: true, sits_on_line_id: 'L480', remark: '' },
    ];
    getLineSplits.mockResolvedValue(p);
    await renderDialog();
    await sheet();
    const row = (n: number) => qty(480, n).closest('tr') as HTMLElement;
    expect(within(row(1)).getByText('ADVANCE')).toBeInTheDocument();
    expect(within(row(2)).getByText('REMAIN')).toBeInTheDocument();
    expect(within(row(3)).getByText('DELAY')).toBeInTheDocument();
    expect(within(row(4)).getAllByText(/^CANCEL/).length).toBeGreaterThan(0);
  });
});

describe('AC-A12 header, search, split-only switch and the status line', () => {
  it('shows "Split delivery" and "<SO no> · <SO date> · <customer>"', async () => {
    await renderDialog();
    await sheet();
    const dialog = await screen.findByTestId('line-split-dialog');
    expect(dialog.textContent).toContain('Split delivery');
    expect(dialog.textContent).toContain(
      'SO417636 · 03/09/2026 · GLOBAL INGRESS SDN BHD (PROJECT)',
    );
    expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^close$/i }).length).toBeGreaterThan(0);
  });

  it('search by item code narrows the lines', async () => {
    await renderDialog();
    await sheet();
    fireEvent.change(screen.getByPlaceholderText(/item code/i), { target: { value: 'CSK11A' } });
    await waitFor(() => expect(blockIds()).toEqual(['split-line-768']));
  });

  it('the "Split lines only" switch shows only split lines', async () => {
    getLineSplits.mockResolvedValue(savedCase());
    await renderDialog();
    await sheet();
    expect(blockIds()).toHaveLength(5);
    fireEvent.click(screen.getByRole('switch', { name: /split lines only/i }));
    await waitFor(() => expect(blockIds()).toEqual(['split-line-480']));
  });

  it('the status line counts lines, products and split lines', async () => {
    getLineSplits.mockResolvedValue(savedCase());
    await renderDialog();
    await sheet();
    expect(screen.getByRole('status').textContent).toMatch(/5 lines · 2 products · 1 split/);
  });

  it('names each line that does not add up, with Show that clears the filters and focuses the line', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '14');
    leaveSheet();
    const status = screen.getByRole('status');
    expect(status.textContent).toMatch(/CSK14B line 480: 403 left/);

    // hide the line, then Show brings it back
    fireEvent.change(screen.getByPlaceholderText(/item code/i), { target: { value: 'CSK11A' } });
    await waitFor(() => expect(blockIds()).toEqual(['split-line-768']));
    fireEvent.click(within(screen.getByRole('status')).getByText(/^Show$/));
    await waitFor(() => expect(blockIds()).toContain('split-line-480'));
    expect(screen.getByPlaceholderText(/item code/i)).toHaveValue('');
    await waitFor(() => expect(block(480).contains(document.activeElement)).toBe(true));
  });

  it('says "N over" and "date to fill" in the status line', async () => {
    await renderDialog();
    await sheet();
    await splitLine(480);
    typeQty(480, 1, '500');
    expect(screen.getByRole('status').textContent).toMatch(/CSK14B line 480: 83 over/);
    typeQty(480, 1, '417');
    typeDate(480, 1, '');
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toMatch(/CSK14B line 480: date to fill/),
    );
  });
});
